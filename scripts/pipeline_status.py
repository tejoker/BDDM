#!/usr/bin/env python3
"""Claim-level verification status taxonomy and ledger writer.

Status taxonomy (single source of truth):

  FULLY_PROVEN        — formal statement validated, proof steps verified,
                        all assumptions grounded. Eligible for Mathlib promotion.
  INTERMEDIARY_PROVEN — proof steps verified under assumptions, but at least
                        one assumption remains UNGROUNDED.
  FLAWED              — extracted proof steps fail local verification or a
                        contradiction is found.
  UNRESOLVED          — pipeline could not complete deterministically (parse
                        failure, translation failure, timeout, etc.).

Grounding policy for assumptions:

  GROUNDED_MATHLIB        — proved directly from Mathlib.
  GROUNDED_INTERNAL_KG    — proved from already accepted internal theorems.
  GROUNDED_EXTERNAL_PAPER — linked to a cited source and re-verified.
  UNGROUNDED              — no trusted derivation yet.

Ledger files are written to output/verification_ledgers/<paper_id>.json.
Each file is a schema-versioned JSON document with an `entries` array
(one theorem entry per item).
"""

from __future__ import annotations

import json
import subprocess
import re
import time
import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

try:
    from bridge_proofs import suggest_bridge_candidates
except Exception:
    try:
        from scripts.bridge_proofs import suggest_bridge_candidates
    except Exception:
        suggest_bridge_candidates = None

try:
    from step_entailment_checker import assess_step_entailment
except Exception:
    try:
        from scripts.step_entailment_checker import assess_step_entailment
    except Exception:
        assess_step_entailment = None


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class VerificationStatus(str, Enum):
    FULLY_PROVEN = "FULLY_PROVEN"
    INTERMEDIARY_PROVEN = "INTERMEDIARY_PROVEN"
    FLAWED = "FLAWED"
    UNRESOLVED = "UNRESOLVED"


class StepVerdict(str, Enum):
    VERIFIED = "VERIFIED"
    FLAWED = "FLAWED"
    INCOMPLETE = "INCOMPLETE"


class FailureOrigin(str, Enum):
    NOT_FAILED = "NOT_FAILED"
    FORMALIZATION_ERROR = "FORMALIZATION_ERROR"
    PROOF_SEARCH_ERROR = "PROOF_SEARCH_ERROR"
    POSSIBLY_FALSE_STATEMENT = "POSSIBLY_FALSE_STATEMENT"
    UNKNOWN = "UNKNOWN"


# Keep legacy alias.
TheoremStatus = VerificationStatus


class GroundingStatus(str, Enum):
    GROUNDED_MATHLIB = "GROUNDED_MATHLIB"
    GROUNDED_INTERNAL_KG = "GROUNDED_INTERNAL_KG"
    GROUNDED_EXTERNAL_PAPER = "GROUNDED_EXTERNAL_PAPER"
    UNGROUNDED = "UNGROUNDED"
    UNKNOWN = "UNKNOWN"


class TrustClass(str, Enum):
    TRUST_MATHLIB = "TRUST_MATHLIB"
    TRUST_EXTERNAL_FORMAL_LIB = "TRUST_EXTERNAL_FORMAL_LIB"
    TRUST_INTERNAL_PROVED = "TRUST_INTERNAL_PROVED"
    TRUST_PLACEHOLDER = "TRUST_PLACEHOLDER"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class StatusDecision:
    """Legacy simple classifier result — kept for backward compatibility."""
    verification_status: VerificationStatus
    grounding_status: GroundingStatus
    reason: str


@dataclass
class StepObligation:
    """One step in a proof attempt with its verification outcome."""
    step_index: int
    tactic: str
    result: str       # "state-advanced" | "proof-finished" | "lean-error" | ...
    detail: str = ""
    verified: bool = False


@dataclass
class Assumption:
    """A single assumption extracted from a theorem statement or proof context."""
    label: str
    lean_expr: str
    grounding: GroundingStatus = GroundingStatus.UNGROUNDED
    grounding_source: str = ""
    trust_class: TrustClass = TrustClass.TRUST_PLACEHOLDER
    trust_reference: str = ""


@dataclass
class ProvenanceLink:
    paper_id: str
    section: str = ""
    label: str = ""
    cited_refs: list[str] = field(default_factory=list)


@dataclass
class TheoremLedgerEntry:
    """Full verification record for one theorem."""
    theorem_name: str
    lean_file: str
    lean_statement: str
    status: VerificationStatus
    step_verdict: StepVerdict = StepVerdict.INCOMPLETE
    failure_origin: FailureOrigin = FailureOrigin.UNKNOWN
    trust_class: TrustClass = TrustClass.TRUST_PLACEHOLDER
    trust_reference: str = ""
    promotion_gate_passed: bool = False
    step_obligations: list[StepObligation] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    provenance: ProvenanceLink | None = None
    proof_text: str = ""
    first_failing_step: int = -1
    error_message: str = ""
    proof_mode: str = "full-draft"
    rounds_used: int = 0
    time_s: float = 0.0
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["step_verdict"] = self.step_verdict.value
        d["failure_origin"] = self.failure_origin.value
        d["trust_class"] = self.trust_class.value
        for a in d["assumptions"]:
            if not isinstance(a["grounding"], str):
                a["grounding"] = a["grounding"].value
            if not isinstance(a["trust_class"], str):
                a["trust_class"] = a["trust_class"].value
        return d


def _trust_for_grounding(grounding: GroundingStatus) -> tuple[TrustClass, str]:
    if grounding == GroundingStatus.GROUNDED_MATHLIB:
        return TrustClass.TRUST_MATHLIB, "mathlib"
    if grounding == GroundingStatus.GROUNDED_INTERNAL_KG:
        return TrustClass.TRUST_INTERNAL_PROVED, "internal_kg"
    if grounding == GroundingStatus.GROUNDED_EXTERNAL_PAPER:
        return TrustClass.TRUST_EXTERNAL_FORMAL_LIB, "external_formal_or_cited"
    return TrustClass.TRUST_PLACEHOLDER, "untrusted"


def _derive_theorem_trust(
    *,
    assumptions: list[Assumption],
    status: VerificationStatus,
) -> tuple[TrustClass, str, bool]:
    if status in {VerificationStatus.FLAWED, VerificationStatus.UNRESOLVED}:
        return TrustClass.TRUST_PLACEHOLDER, "theorem_not_verified", False

    if assumptions and all(a.trust_class == TrustClass.TRUST_MATHLIB for a in assumptions):
        promotion_ok = status == VerificationStatus.FULLY_PROVEN
        return TrustClass.TRUST_MATHLIB, "all_assumptions_mathlib", promotion_ok

    if assumptions and any(a.trust_class == TrustClass.TRUST_EXTERNAL_FORMAL_LIB for a in assumptions):
        promotion_ok = status == VerificationStatus.FULLY_PROVEN
        return TrustClass.TRUST_EXTERNAL_FORMAL_LIB, "contains_external_sources", promotion_ok

    promotion_ok = status == VerificationStatus.FULLY_PROVEN
    return TrustClass.TRUST_INTERNAL_PROVED, "internal_verified_pipeline", promotion_ok


# ---------------------------------------------------------------------------
# Status inference
# ---------------------------------------------------------------------------

def infer_status(
    *,
    proved: bool,
    step_obligations: list[StepObligation],
    assumptions: list[Assumption],
    step_verdict: StepVerdict,
    error: str = "",
) -> VerificationStatus:
    """Derive status from proof outcome + obligation/assumption state."""
    if proved:
        ungrounded = [
            a for a in assumptions
            if a.grounding in (GroundingStatus.UNGROUNDED, GroundingStatus.UNKNOWN)
        ]
        if not ungrounded:
            return VerificationStatus.FULLY_PROVEN
        return VerificationStatus.INTERMEDIARY_PROVEN

    if step_verdict == StepVerdict.FLAWED:
        return VerificationStatus.FLAWED

    return VerificationStatus.UNRESOLVED


def derive_step_verdict(
    *,
    proved: bool,
    step_obligations: list[StepObligation],
    error_message: str = "",
) -> StepVerdict:
    """Classify whether the observed proof-step trace is valid, flawed, or incomplete."""
    if proved:
        return StepVerdict.VERIFIED

    if not step_obligations:
        err_l = (error_message or "").lower()
        if any(tok in err_l for tok in ("lean-error", "tactic failed", "proof-given-up", "could not", "failed")):
            return StepVerdict.FLAWED
        return StepVerdict.INCOMPLETE

    error_l = (error_message or "").lower()
    if "timeout" in error_l or "interrupted" in error_l:
        return StepVerdict.INCOMPLETE

    failing_results = {"lean-error", "proof-given-up"}
    if any((s.result or "").strip().lower() in failing_results for s in step_obligations):
        return StepVerdict.FLAWED

    if any("failed" in (s.detail or "").lower() for s in step_obligations):
        return StepVerdict.FLAWED

    # Optional scaffold for full entailment checking.
    if os.environ.get("DESOL_ENABLE_STEP_ENTAILMENT", "0") == "1" and assess_step_entailment is not None:
        try:
            assessment = assess_step_entailment(step_obligations)
            if assessment.is_flawed:
                return StepVerdict.FLAWED
        except Exception:
            pass

    if any(s.verified for s in step_obligations):
        return StepVerdict.INCOMPLETE

    # All attempted steps were non-progress but non-failing (e.g. model timeout loops).
    return StepVerdict.INCOMPLETE


def infer_failure_origin(
    *,
    proved: bool,
    lean_statement: str,
    step_obligations: list[StepObligation],
    step_records: list[Any] | None = None,
    error_message: str = "",
    min_false_seeds: int = 3,
) -> FailureOrigin:
    """Explain why a theorem failed: formalization/pipeline vs search vs likely false."""
    if proved:
        return FailureOrigin.NOT_FAILED

    stmt_l = (lean_statement or "").strip().lower()
    err_l = (error_message or "").lower()

    # 1) Formalization-level failures first.
    # Non-proposition declarations cannot be proved as theorem goals.
    if stmt_l.startswith("def ") or "not a proposition" in err_l:
        return FailureOrigin.FORMALIZATION_ERROR

    # Parse/elaboration and naming/syntax issues are formalization-level.
    formalization_markers = (
        "unknown identifier",
        "unknown constant",
        "invalid field",
        "unexpected token",
        "type mismatch",
        "not found in source",
        "translation",
        "elaborate",
    )
    if any(m in err_l for m in formalization_markers):
        return FailureOrigin.FORMALIZATION_ERROR

    # 2) Search/runtime failures next.
    search_markers = (
        "timeout",
        "proof-given-up",
        "failed after repair_rounds",
        "no proof backend available",
        "interrupted",
        "keyboardinterrupt",
        "mcts",
        "parallel draft mcts exhausted",
        "no successful workers",
        "resource exhausted",
    )
    if any(m in err_l for m in search_markers):
        return FailureOrigin.PROOF_SEARCH_ERROR

    # 3) Possibly false requires repeated independent hard Lean rejections.
    # Count independent attempts from explicit step records if present.
    records = step_records or []
    distinct_attempts: set[tuple[int, int]] = set()
    lean_error_records = 0
    contradiction_like_records = 0
    contradiction_markers = (
        "contradiction",
        "false",
        "not provable",
        "cannot be proved",
        "failed to close goal",
        "unsolved goals",
        "no goals to be solved",
        "tactic failed",
    )
    for rec in records:
        if isinstance(rec, dict):
            step_idx = int(rec.get("step", 0) or 0)
            attempt_idx = int(rec.get("attempt", 0) or 0)
            result = str(rec.get("result", "")).strip().lower()
            detail = str(rec.get("detail", "")).lower()
        else:
            step_idx = int(getattr(rec, "step", 0) or 0)
            attempt_idx = int(getattr(rec, "attempt", 0) or 0)
            result = str(getattr(rec, "result", "")).strip().lower()
            detail = str(getattr(rec, "detail", "")).lower()

        if result in {"lean-error", "proof-given-up"}:
            lean_error_records += 1
            distinct_attempts.add((step_idx, attempt_idx))
            if any(tok in detail for tok in contradiction_markers):
                contradiction_like_records += 1

    # Supplement seed count from parallel summary text when available.
    worker_ids = {m.group(1) for m in re.finditer(r"worker\s+(\d+)\s*:", err_l)}
    independent_runs = max(len(distinct_attempts), len(worker_ids))

    if (
        step_obligations
        and all((s.result or "").lower() == "lean-error" for s in step_obligations)
        and independent_runs >= min_false_seeds
        and contradiction_like_records >= min_false_seeds
    ):
        return FailureOrigin.POSSIBLY_FALSE_STATEMENT

    # 4) If there were many failures but not enough independent evidence, keep as search error.
    if lean_error_records > 0 and independent_runs < min_false_seeds:
        return FailureOrigin.PROOF_SEARCH_ERROR

    return FailureOrigin.UNKNOWN


def reconstruct_step_obligations(
    *,
    step_records: list[Any],
    error_message: str = "",
) -> tuple[list[StepObligation], int]:
    """Build a normalized step-obligation trace from raw records and fallback error text.

    Returns (obligations, first_failing_step).
    """
    obligations: list[StepObligation] = []
    first_failing = -1

    for i, rec in enumerate(step_records):
        if isinstance(rec, dict):
            result = rec.get("result", "")
            tactic = rec.get("tactic", "")
            detail = rec.get("detail", "")
            step_idx = rec.get("step", i)
        else:
            result = getattr(rec, "result", "")
            tactic = getattr(rec, "tactic", "")
            detail = getattr(rec, "detail", "")
            step_idx = getattr(rec, "step", i)

        result_str = str(result)
        verified = result_str in ("state-advanced", "proof-finished")
        obligations.append(
            StepObligation(
                step_index=int(step_idx),
                tactic=str(tactic),
                result=result_str,
                detail=str(detail),
                verified=verified,
            )
        )

        if not verified and first_failing == -1:
            first_failing = int(step_idx)

    # If no records were captured but error text clearly indicates a failed step,
    # synthesize an explicit failing obligation for downstream classifiers.
    if not obligations:
        err_l = (error_message or "").lower()
        if any(tok in err_l for tok in ("lean-error", "tactic failed", "proof-given-up", "could not", "failed")):
            obligations.append(
                StepObligation(
                    step_index=0,
                    tactic="",
                    result="lean-error",
                    detail=error_message[:300],
                    verified=False,
                )
            )
            first_failing = 0

    return obligations, first_failing


def classify_theorem_result(
    *, translated: bool, proved: bool, had_exception: bool
) -> StatusDecision:
    """Conservative default classifier for the current pipeline capabilities.

    Until explicit assumption grounding and step-level obligation checking are
    integrated, a proved theorem is INTERMEDIARY_PROVEN, not FULLY_PROVEN.
    """
    if proved and translated:
        return StatusDecision(
            verification_status=VerificationStatus.INTERMEDIARY_PROVEN,
            grounding_status=GroundingStatus.UNKNOWN,
            reason=(
                "Theorem closed by Lean pipeline, but assumption grounding "
                "and step-level obligation verification are not yet integrated."
            ),
        )

    if had_exception:
        return StatusDecision(
            verification_status=VerificationStatus.UNRESOLVED,
            grounding_status=GroundingStatus.UNKNOWN,
            reason="Pipeline exception during proving/validation.",
        )

    if not translated:
        return StatusDecision(
            verification_status=VerificationStatus.UNRESOLVED,
            grounding_status=GroundingStatus.UNKNOWN,
            reason="Statement translation did not validate.",
        )

    return StatusDecision(
        verification_status=VerificationStatus.UNRESOLVED,
        grounding_status=GroundingStatus.UNKNOWN,
        reason="No closed Lean proof for theorem under current search budget.",
    )


# ---------------------------------------------------------------------------
# Assumption extraction
# ---------------------------------------------------------------------------

_MATHLIB_KNOWN: frozenset[str] = frozenset({
    "MetricSpace", "PseudoMetricSpace", "NormedAddCommGroup", "NormedSpace",
    "InnerProductSpace", "CompleteSpace", "TopologicalSpace", "T2Space",
    "CompactSpace", "Module", "Ring", "CommRing", "Field", "Fintype",
    "DecidableEq", "MeasurableSpace", "MeasureSpace", "IsProbabilityMeasure",
    "AddCommGroup", "Group", "CommGroup", "Monoid", "CommMonoid",
    "PolishSpace", "SmoothManifoldWithCorners", "SimpleGraph",
    "LinearOrder", "Lattice", "BoundedOrder", "OrderedField",
    "NormedField", "RCLike", "IsROrC",
})

_PROP_INDICATORS = ("∀", "∃", "≤", "≥", "<", ">", "=", "≠", "∈", "⊆", "→", "↔", "¬")


def extract_assumptions_from_statement(lean_statement: str) -> list[Assumption]:
    """Heuristically extract typeclass and hypothesis assumptions from a Lean 4 statement."""
    assumptions: list[Assumption] = []
    seen_keys: set[str] = set()

    def _push(label: str, lean_expr: str, grounding: GroundingStatus, source: str = "") -> None:
        key = f"{label}|{lean_expr}"
        if not label or key in seen_keys:
            return
        seen_keys.add(key)
        assumptions.append(
            Assumption(
                label=label,
                lean_expr=lean_expr,
                grounding=grounding,
                grounding_source=source,
            )
        )

    for m in re.finditer(r"\[([^\[\]]+)\]", lean_statement):
        expr = m.group(1).strip()
        if not expr:
            continue
        label = expr.split()[0] if expr.split() else expr
        grounding = GroundingStatus.UNGROUNDED
        source = ""
        if label in _MATHLIB_KNOWN:
            grounding = GroundingStatus.GROUNDED_MATHLIB
            source = "Mathlib"
        _push(label, f"[{expr}]", grounding, source)

    for m in re.finditer(r"\((\w+)\s*:\s*([^()]+)\)", lean_statement):
        name, typ = m.group(1), m.group(2).strip()
        # Heuristic: hypothesis binders are usually proposition-like formulas,
        # or conventionally named h/h1/hFoo even when formula is a named axiom.
        is_hyp_name = name.startswith("h")
        looks_named_axiom = bool(re.fullmatch(r"[A-Z][A-Za-z0-9_.'-]*", typ))
        if any(ind in typ for ind in _PROP_INDICATORS) or is_hyp_name or looks_named_axiom:
            _push(name, f"({name} : {typ})", GroundingStatus.UNGROUNDED)

    # Capture qualified identifiers used in statements (e.g. MeasureTheory.X, ProbabilityTheory.Y).
    for m in re.finditer(r"\b([A-Z][A-Za-z0-9_']*(?:\.[A-Za-z][A-Za-z0-9_']*)+)\b", lean_statement):
        ident = m.group(1).strip()
        # Skip obvious type-level atoms.
        if ident in {"Type", "Prop", "Sort"}:
            continue
        _push(ident, ident, GroundingStatus.UNGROUNDED, "qualified_identifier")

    return assumptions


# ---------------------------------------------------------------------------
# Ledger I/O
# ---------------------------------------------------------------------------

_LEDGER_DIR = Path("output/verification_ledgers")
_INTERNAL_THEOREM_CACHE: dict[str, set[str]] = {}


def _mathlib_name_exists(name: str, project_root: Path) -> bool:
    """Check if a Lean constant/typeclass name resolves with project imports."""
    safe = re.sub(r"[^A-Za-z0-9_.'\[\]{}: ]", "", name).strip()
    if not safe:
        return False
    tmp = project_root / "Desol" / f"_tmp_grounding_{int(time.time() * 1000)}.lean"
    src = (
        "import Desol.SDE.Basic\n\n"
        f"#check {safe}\n"
    )
    try:
        tmp.write_text(src, encoding="utf-8")
        proc = subprocess.run(
            ["lake", "env", "lean", str(tmp)],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=20,
        )
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        return proc.returncode == 0 and "error:" not in out.lower()
    except Exception:
        return False
    finally:
        tmp.unlink(missing_ok=True)


def _extract_assumption_type_expr(lean_expr: str) -> str:
    m = re.match(r"\(\w+\s*:\s*(.+)\)$", lean_expr.strip())
    if not m:
        return ""
    return m.group(1).strip()


def _norm_ref_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def load_internal_grounded_theorems(output_root: Path | None = None) -> set[str]:
    """Load theorem names already marked FULLY_PROVEN from local verification ledgers."""
    base = output_root if output_root is not None else _LEDGER_DIR
    key = str(base.resolve())
    if key in _INTERNAL_THEOREM_CACHE:
        return _INTERNAL_THEOREM_CACHE[key]

    grounded: set[str] = set()
    if base.exists():
        for p in base.glob("*.json"):
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(raw, list):
                rows = raw
            elif isinstance(raw, dict) and isinstance(raw.get("entries"), list):
                rows = raw["entries"]
            else:
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if row.get("status") == VerificationStatus.FULLY_PROVEN.value:
                    name = str(row.get("theorem_name", "")).strip()
                    if name:
                        grounded.add(name)

    _INTERNAL_THEOREM_CACHE[key] = grounded
    return grounded


def ground_assumptions(
    assumptions: list[Assumption],
    *,
    project_root: Path | None = None,
    ledger_root: Path | None = None,
    cited_refs: list[str] | None = None,
) -> list[Assumption]:
    """Attempt grounding of assumptions via Mathlib, internal KG, or cited references."""
    internal_grounded = load_internal_grounded_theorems(output_root=ledger_root)
    cited_refs = cited_refs or []
    cited_norm = {_norm_ref_token(r) for r in cited_refs if r}

    grounded_out: list[Assumption] = []
    for a in assumptions:
        if a.grounding in {
            GroundingStatus.GROUNDED_MATHLIB,
            GroundingStatus.GROUNDED_INTERNAL_KG,
            GroundingStatus.GROUNDED_EXTERNAL_PAPER,
        }:
            grounded_out.append(a)
            continue

        expr_type = _extract_assumption_type_expr(a.lean_expr)

        if project_root is not None and expr_type:
            # For simple named assumptions, directly test symbol availability.
            simple = bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.'-]*", expr_type))
            if simple and _mathlib_name_exists(expr_type, project_root):
                grounded_out.append(
                    Assumption(
                        label=a.label,
                        lean_expr=a.lean_expr,
                        grounding=GroundingStatus.GROUNDED_MATHLIB,
                        grounding_source=f"Mathlib:#check {expr_type}",
                        trust_class=TrustClass.TRUST_MATHLIB,
                        trust_reference=f"Mathlib:#check {expr_type}",
                    )
                )
                continue

        if expr_type and expr_type in internal_grounded:
            grounded_out.append(
                Assumption(
                    label=a.label,
                    lean_expr=a.lean_expr,
                    grounding=GroundingStatus.GROUNDED_INTERNAL_KG,
                    grounding_source=f"internal_kg:{expr_type}",
                    trust_class=TrustClass.TRUST_INTERNAL_PROVED,
                    trust_reference=f"internal_kg:{expr_type}",
                )
            )
            continue

        label_norm = _norm_ref_token(a.label)
        expr_norm = _norm_ref_token(expr_type)
        if cited_norm and (
            (expr_norm and any(expr_norm in ref or ref in expr_norm for ref in cited_norm))
            or (label_norm and any(label_norm in ref or ref in label_norm for ref in cited_norm))
        ):
            grounded_out.append(
                Assumption(
                    label=a.label,
                    lean_expr=a.lean_expr,
                    grounding=GroundingStatus.GROUNDED_EXTERNAL_PAPER,
                    grounding_source="paper_reference_match(normalized)",
                    trust_class=TrustClass.TRUST_EXTERNAL_FORMAL_LIB,
                    trust_reference="paper_reference_match(normalized)",
                )
            )
            continue

        placeholder_trust, placeholder_ref = _trust_for_grounding(a.grounding)
        # Optional bridge-proof hinting: keep status UNGROUNDED but include candidate link.
        bridge_hint = a.grounding_source
        if (
            suggest_bridge_candidates is not None
            and project_root is not None
            and ledger_root is not None
        ):
            try:
                candidates = suggest_bridge_candidates(
                    assumption_expr=expr_type or a.label,
                    ledger_root=ledger_root,
                    max_candidates=1,
                )
                if candidates:
                    bridge_hint = f"bridge_candidate:{candidates[0].theorem_name}"
            except Exception:
                bridge_hint = a.grounding_source

        grounded_out.append(
            Assumption(
                label=a.label,
                lean_expr=a.lean_expr,
                grounding=a.grounding,
                grounding_source=bridge_hint,
                trust_class=placeholder_trust,
                trust_reference=placeholder_ref,
            )
        )

    return grounded_out


def _ledger_path(paper_id: str, output_root: Path | None = None) -> Path:
    safe = paper_id.replace("/", "_").replace(":", "_")
    base = output_root if output_root is not None else _LEDGER_DIR
    return base / f"{safe}.json"


def load_ledger(paper_id: str, output_root: Path | None = None) -> list[dict[str, Any]]:
    path = _ledger_path(paper_id, output_root=output_root)
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict) and isinstance(doc.get("entries"), list):
        return [r for r in doc["entries"] if isinstance(r, dict)]
    return []


def _get_pipeline_commit(cwd: Path | None = None) -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0:
            return (proc.stdout or "").strip() or "unknown"
    except Exception:
        pass
    return "unknown"


def _get_lean_version(cwd: Path | None = None) -> str:
    try:
        proc = subprocess.run(
            ["lean", "--version"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        if out:
            return out.splitlines()[0]
    except Exception:
        pass
    return "unknown"


def save_ledger(
    paper_id: str,
    entries: list[dict[str, Any]],
    output_root: Path | None = None,
    metadata: dict[str, Any] | None = None,
) -> Path:
    base = output_root if output_root is not None else _LEDGER_DIR
    base.mkdir(parents=True, exist_ok=True)
    path = _ledger_path(paper_id, output_root=output_root)

    root_for_tools = output_root.parent if output_root is not None else Path(".")
    merged_meta = {
        "schema_version": "2.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pipeline_commit": _get_pipeline_commit(root_for_tools),
        "toolchain_versions": {
            "lean": _get_lean_version(root_for_tools),
            "python": os.environ.get("PYTHON_VERSION", "unknown"),
            "mistral_model": os.environ.get("MISTRAL_MODEL", "unknown"),
        },
    }
    if metadata:
        merged_meta.update(metadata)

    doc = {
        **merged_meta,
        "entries": entries,
    }
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def upsert_ledger_entry(
    paper_id: str,
    entry: TheoremLedgerEntry,
    output_root: Path | None = None,
) -> Path:
    """Insert or replace the ledger entry for one theorem (matched by theorem_name)."""
    entries = load_ledger(paper_id, output_root=output_root)
    entry_dict = entry.to_dict()
    replaced = False
    for i, existing in enumerate(entries):
        if existing.get("theorem_name") == entry.theorem_name:
            entries[i] = entry_dict
            replaced = True
            break
    if not replaced:
        entries.append(entry_dict)
    return save_ledger(paper_id, entries, output_root=output_root)


def aggregate_grounding_status(assumptions: list[Assumption]) -> GroundingStatus:
    """Aggregate per-assumption grounding into a theorem-level grounding status.

    If any assumption is unknown/ungrounded, theorem grounding is UNKNOWN.
    If all assumptions are grounded, return one representative grounded tier.
    """
    if not assumptions:
        return GroundingStatus.UNKNOWN

    statuses = {a.grounding for a in assumptions}
    if GroundingStatus.UNKNOWN in statuses or GroundingStatus.UNGROUNDED in statuses:
        return GroundingStatus.UNKNOWN
    if GroundingStatus.GROUNDED_MATHLIB in statuses:
        return GroundingStatus.GROUNDED_MATHLIB
    if GroundingStatus.GROUNDED_INTERNAL_KG in statuses:
        return GroundingStatus.GROUNDED_INTERNAL_KG
    if GroundingStatus.GROUNDED_EXTERNAL_PAPER in statuses:
        return GroundingStatus.GROUNDED_EXTERNAL_PAPER
    return GroundingStatus.UNKNOWN


# ---------------------------------------------------------------------------
# Convenience builder
# ---------------------------------------------------------------------------

def build_ledger_entry(
    *,
    theorem_name: str,
    lean_file: str,
    lean_statement: str,
    proved: bool,
    step_records: list[Any],
    proof_text: str = "",
    error_message: str = "",
    proof_mode: str = "full-draft",
    rounds_used: int = 0,
    time_s: float = 0.0,
    provenance: ProvenanceLink | None = None,
    project_root: Path | None = None,
    ledger_root: Path | None = None,
) -> TheoremLedgerEntry:
    """Build a TheoremLedgerEntry from raw pipeline step_records."""
    step_obligations, first_failing = reconstruct_step_obligations(
        step_records=step_records,
        error_message=error_message,
    )

    assumptions = extract_assumptions_from_statement(lean_statement)
    assumptions = ground_assumptions(
        assumptions,
        project_root=project_root,
        ledger_root=ledger_root,
        cited_refs=(provenance.cited_refs if provenance else []),
    )

    step_verdict = derive_step_verdict(
        proved=proved,
        step_obligations=step_obligations,
        error_message=error_message,
    )

    failure_origin = infer_failure_origin(
        proved=proved,
        lean_statement=lean_statement,
        step_obligations=step_obligations,
        step_records=step_records,
        error_message=error_message,
    )

    status = infer_status(
        proved=proved,
        step_obligations=step_obligations,
        assumptions=assumptions,
        step_verdict=step_verdict,
        error=error_message,
    )

    theorem_trust_class, theorem_trust_ref, promotion_gate = _derive_theorem_trust(
        assumptions=assumptions,
        status=status,
    )

    return TheoremLedgerEntry(
        theorem_name=theorem_name,
        lean_file=lean_file,
        lean_statement=lean_statement,
        status=status,
        step_verdict=step_verdict,
        failure_origin=failure_origin,
        trust_class=theorem_trust_class,
        trust_reference=theorem_trust_ref,
        promotion_gate_passed=promotion_gate,
        step_obligations=step_obligations,
        assumptions=assumptions,
        provenance=provenance,
        proof_text=proof_text,
        first_failing_step=first_failing,
        error_message=error_message[:500] if error_message else "",
        proof_mode=proof_mode,
        rounds_used=rounds_used,
        time_s=round(time_s, 2),
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
