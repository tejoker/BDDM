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
import os
import re
import subprocess
import time
import hashlib
from pathlib import Path
from typing import Any
from pipeline_status_classification import (
    classify_theorem_result,
    derive_step_verdict,
    infer_failure_origin,
    infer_quality_scores,
    infer_status,
    reconstruct_step_obligations,
)
from pipeline_status_models import (
    Assumption,
    ClaimEquivalenceVerdict,
    FailureOrigin,
    FailureKind,
    GroundingStatus,
    ProvenanceLink,
    ProofMethod,
    StatusDecision,
    SemanticEquivalenceArtifact,
    StepObligation,
    StepVerdict,
    TheoremLedgerEntry,
    TrustClass,
    VerificationStatus,
    all_assumptions_grounded as _all_assumptions_grounded,
    derive_theorem_trust as _derive_theorem_trust,
    trust_for_grounding as _trust_for_grounding,
)

try:
    from bridge_proofs import suggest_bridge_candidates
except ModuleNotFoundError:
    try:
        from scripts.bridge_proofs import suggest_bridge_candidates
    except ModuleNotFoundError:
        suggest_bridge_candidates = None

try:
    from step_entailment_checker import assess_step_entailment
except ModuleNotFoundError:
    try:
        from scripts.step_entailment_checker import assess_step_entailment
    except ModuleNotFoundError:
        assess_step_entailment = None


TheoremStatus = VerificationStatus


def _auto_reproducible_env(project_root: Path | None) -> bool:
    if project_root is None:
        return False
    commit = _get_pipeline_commit(project_root)
    lean_ver = _get_lean_version(project_root)
    return commit != "unknown" and lean_ver != "unknown"


def independent_lean_verify(
    *,
    lean_statement: str,
    proof_text: str,
    project_root: Path,
    timeout: int = 60,
) -> tuple[bool, str]:
    """Independently re-verify a proof by writing it to a temp file and running lake build.

    This is separate from the 'proved' flag produced during proof search — it provides
    an independent check that the proof is reproducible without the search-time cache.

    Returns (success, detail).
    """
    import tempfile
    if not proof_text.strip() or not lean_statement.strip():
        return False, "empty proof or statement"

    # Build a minimal .lean file
    imports = "import Mathlib\nimport Aesop\n\n"
    # Strip any existing :=  by ... from statement, add proof
    stmt = lean_statement.strip()
    if ":= by" in stmt:
        stmt = stmt[:stmt.index(":= by")].rstrip()
    if stmt.endswith(":="):
        stmt = stmt[:-2].rstrip()

    lean_src = imports + stmt + " := by\n"
    for line in proof_text.strip().splitlines():
        lean_src += "  " + line + "\n"

    tmp_dir = project_root / "Desol"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="_tmp_verify_",
        suffix=".lean",
        dir=tmp_dir,
        delete=False,
    ) as _tf:
        verify_lean = Path(_tf.name)
        _tf.write(lean_src.encode())
    try:
        pass  # file written above

        result = subprocess.run(
            ["lake", "env", "lean", str(verify_lean)],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_elan_env(),
        )
        success = result.returncode == 0 and "sorry" not in lean_src
        detail = (result.stdout + result.stderr).strip()[:300]
        return success, detail
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    except Exception as exc:
        return False, str(exc)
    finally:
        verify_lean.unlink(missing_ok=True)


def evaluate_promotion_gates(
    *,
    status: VerificationStatus,
    proved: bool,
    step_verdict: StepVerdict,
    assumptions: list[Assumption],
    provenance: ProvenanceLink | None,
    project_root: Path | None,
    translation_fidelity_score: float | None,
    status_alignment_score: float | None,
    dependency_trust_complete: bool | None,
    reproducible_env: bool | None,
    lean_statement: str = "",
    proof_text: str = "",
    run_independent_verify: bool = False,
    claim_equivalence_verdict: ClaimEquivalenceVerdict = ClaimEquivalenceVerdict.UNCLEAR,
    independent_semantic_evidence: bool | None = None,
    semantic_adversarial_checks_passed: bool | None = None,
    axiom_debt: list[str] | None = None,
) -> tuple[VerificationStatus, dict[str, bool], list[str]]:
    """Evaluate strict promotion gates and optionally downgrade FULLY_PROVEN.

    FULLY_PROVEN requires independent gate evidence beyond raw proof closure.
    When run_independent_verify=True, 'lean_proof_closed' is checked by a
    fresh lake build in a temp dir, not just the search-time 'proved' flag.
    """
    min_fidelity = float(os.environ.get("DESOL_MIN_TRANSLATION_FIDELITY", "0.80"))
    min_alignment = float(os.environ.get("DESOL_MIN_STATUS_ALIGNMENT", "0.80"))
    require_equivalent = os.environ.get("DESOL_REQUIRE_EQUIVALENT_FOR_FULL", "1").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    require_independent_semantic = os.environ.get(
        "DESOL_REQUIRE_INDEPENDENT_SEMANTIC_EVIDENCE_FOR_FULL",
        "1",
    ).strip().lower() in {
        "1",
        "true",
        "yes",
    }

    # Independent verification: re-run lake build in a clean temp dir
    if run_independent_verify and proved and project_root and lean_statement and proof_text:
        lean_closed, _detail = independent_lean_verify(
            lean_statement=lean_statement,
            proof_text=proof_text,
            project_root=project_root,
        )
    else:
        lean_closed = proved

    debt = [str(x).strip() for x in (axiom_debt or []) if str(x).strip()]
    gates = {
        "lean_proof_closed": lean_closed,
        "step_verdict_verified": step_verdict == StepVerdict.VERIFIED,
        "assumptions_grounded": _all_assumptions_grounded(assumptions),
        "provenance_linked": bool(
            provenance
            and provenance.paper_id
            and (provenance.section or provenance.label or provenance.cited_refs)
        ),
        "translation_fidelity_ok": (
            translation_fidelity_score is not None and translation_fidelity_score >= min_fidelity
        ),
        "status_alignment_ok": (
            status_alignment_score is not None and status_alignment_score >= min_alignment
        ),
        "dependency_trust_complete": (
            dependency_trust_complete
            if dependency_trust_complete is not None
            else all(a.trust_class != TrustClass.TRUST_PLACEHOLDER for a in assumptions)
        ),
        "reproducible_env": (
            reproducible_env
            if reproducible_env is not None
            else _auto_reproducible_env(project_root)
        ),
        "claim_equivalent": (
            claim_equivalence_verdict == ClaimEquivalenceVerdict.EQUIVALENT
            if require_equivalent
            else claim_equivalence_verdict
            in {ClaimEquivalenceVerdict.EQUIVALENT, ClaimEquivalenceVerdict.UNCLEAR}
        ),
        "independent_semantic_equivalence_evidence": (
            bool(independent_semantic_evidence)
            if require_independent_semantic
            else True
        ),
        "semantic_adversarial_checks_passed": (
            bool(semantic_adversarial_checks_passed)
            if semantic_adversarial_checks_passed is not None
            else True
        ),
        "no_paper_axiom_debt": not debt,
    }

    failures = [k for k, ok in gates.items() if not ok]
    final_status = status
    if status == VerificationStatus.FULLY_PROVEN and debt:
        final_status = (
            VerificationStatus.AXIOM_BACKED
            if set(failures) <= {"no_paper_axiom_debt"}
            else VerificationStatus.INTERMEDIARY_PROVEN
        )
    elif status == VerificationStatus.FULLY_PROVEN and failures:
        final_status = VerificationStatus.INTERMEDIARY_PROVEN

    return final_status, gates, failures


def infer_claim_equivalence(
    *,
    translation_validated: bool | None,
    translation_fidelity_score: float | None,
    status_alignment_score: float | None,
    uncertainty_flags: list[str] | None,
    adversarial_flags: list[str] | None,
    roundtrip_flags: list[str] | None,
) -> tuple[ClaimEquivalenceVerdict, list[str]]:
    flags = [str(x).strip() for x in (uncertainty_flags or []) if str(x).strip()]
    adv = [str(x).strip() for x in (adversarial_flags or []) if str(x).strip()]
    rt = [str(x).strip() for x in (roundtrip_flags or []) if str(x).strip()]
    all_flags_l = [f.lower() for f in [*flags, *adv, *rt]]
    notes: list[str] = []
    allow_heuristic_equivalence = os.environ.get("DESOL_ALLOW_HEURISTIC_EQUIVALENCE", "0").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    independent_positive_markers = (
        "human_equivalent",
        "claim_equivalent:human",
        "semantic_equivalence:verified",
        "roundtrip_equivalent",
        "adversarial_passed",
    )
    has_independent_positive = any(
        any(marker in f for marker in independent_positive_markers) for f in all_flags_l
    )

    # Hard-block only on *semantic* failures (adversarial verdict, policy violation,
    # or weaker/stronger shape mismatch) — NOT on Lean symbol-resolution failures.
    # A Lean validation error (e.g. unknown identifier, type mismatch) means the
    # statement may need repair, but it does not make the claim semantically wrong.
    _semantic_hard_fail_markers = (
        "semantic_policy_violation",
        "trivialization_hard_violation",
        "adversarial_check_failed",
        "verdict:wrong",
        "verdict:suspicious",
    )
    _has_semantic_hard_fail = any(
        any(mark in f for mark in _semantic_hard_fail_markers) for f in all_flags_l
    )
    if translation_validated is False and _has_semantic_hard_fail:
        notes.append("translation_unvalidated_semantic_fail")
        return ClaimEquivalenceVerdict.UNCLEAR, notes

    # Record validation failure as a note (for traceability) but continue scoring.
    if translation_validated is False:
        notes.append("translation_unvalidated")

    if any("weaker" in f for f in all_flags_l):
        notes.append("detected_weaker_than_paper")
        return ClaimEquivalenceVerdict.WEAKER, notes
    if any(("stronger" in f) or ("dropped hypotheses" in f) or ("dropped hypothesis" in f) for f in all_flags_l):
        notes.append("detected_stronger_than_paper")
        return ClaimEquivalenceVerdict.STRONGER, notes

    risky_markers = (
        "roundtrip_semantic_mismatch",
        "adversarial_mismatch",
        "schema_self_check_failed",
        "schema_coverage_missing",
        "trivially_true",
        "verdict:wrong",
        "verdict:suspicious",
    )
    if any(any(mark in f for mark in risky_markers) for f in all_flags_l):
        notes.append("semantic_mismatch_risk")
        return ClaimEquivalenceVerdict.UNCLEAR, notes

    fidelity = float(translation_fidelity_score or 0.0)
    alignment = float(status_alignment_score or 0.0)

    # Apply a small fidelity penalty when Lean validation failed (not semantic fail):
    # the statement may have unresolved symbols so we discount confidence slightly.
    if translation_validated is False:
        fidelity = max(0.0, fidelity - 0.10)

    if has_independent_positive and fidelity >= 0.80 and alignment >= 0.75:
        notes.append("equivalent_independent_semantic_evidence")
        return ClaimEquivalenceVerdict.EQUIVALENT, notes

    # Heuristic scores are triage, not independent semantic evidence. Keep
    # heuristic equivalence opt-in so release reports cannot certify claim
    # equivalence from the same pipeline signals used to generate a proof.
    if allow_heuristic_equivalence and fidelity >= 0.9 and alignment >= 0.85:
        notes.append("equivalent_high_confidence_heuristic_opt_in")
        return ClaimEquivalenceVerdict.EQUIVALENT, notes

    if (
        allow_heuristic_equivalence
        and fidelity >= 0.65
        and alignment >= 0.55
        and not any(
            any(mark in f for mark in ("semantic_mismatch", "adversarial", "roundtrip", "schema_coverage_missing"))
            for f in all_flags_l
        )
    ):
        notes.append("equivalent_medium_confidence")
        return ClaimEquivalenceVerdict.EQUIVALENT, notes

    notes.append("insufficient_semantic_evidence")
    return ClaimEquivalenceVerdict.UNCLEAR, notes


_INDEPENDENT_SEMANTIC_EVIDENCE_MARKERS = (
    "human_equivalent",
    "claim_equivalent:human",
    "semantic_equivalence:verified",
    "roundtrip_equivalent",
    "adversarial_passed",
    "equivalent_independent_semantic_evidence",
)


def _coerce_str_list(raw: object) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, tuple):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _strip_latex_markup(text: str) -> str:
    out = text or ""
    out = re.sub(r"%.*", "", out)
    out = re.sub(r"\\(?:begin|end)\{[^}]+\}", " ", out)
    out = re.sub(r"\\(?:label|ref|cite|eqref)\{[^}]*\}", " ", out)
    out = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", " ", out)
    out = out.replace("{", " ").replace("}", " ")
    out = out.replace("$", " ")
    out = re.sub(r"\s+", " ", out)
    return out.strip()


def _schema_from_context(
    *,
    context_pack: dict[str, Any] | None,
    existing_artifact: dict[str, Any] | None,
) -> dict[str, Any]:
    for source in (existing_artifact or {}, context_pack or {}):
        for key in (
            "translation_statement_schema",
            "statement_schema",
            "normalized_statement_schema",
            "schema",
        ):
            raw = source.get(key) if isinstance(source, dict) else None
            if isinstance(raw, dict):
                return raw
    return {}


def _normalized_theorem_text(
    *,
    original_latex_theorem: str,
    explicit_normalized: str,
    schema: dict[str, Any],
) -> str:
    if explicit_normalized.strip():
        return explicit_normalized.strip()
    if schema:
        quantifiers = _coerce_str_list(schema.get("quantifiers"))
        assumptions = _coerce_str_list(schema.get("assumptions"))
        claim = str(schema.get("claim", "") or "").strip()
        parts: list[str] = []
        if quantifiers:
            parts.append("Quantifiers: " + "; ".join(quantifiers))
        if assumptions:
            parts.append("Assumptions: " + "; ".join(assumptions))
        if claim:
            parts.append("Conclusion: " + claim)
        if parts:
            return " ".join(parts)
    return _strip_latex_markup(original_latex_theorem)


def _lean_conclusion(lean_statement: str) -> str:
    stmt = re.sub(r":=\s*by\b.*$", "", lean_statement or "", flags=re.DOTALL).strip()
    depth = 0
    target_start = -1
    for i, ch in enumerate(stmt):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0:
            target_start = i + 1
    if target_start >= 0:
        return stmt[target_start:].strip()
    return stmt


def _top_level_binder_count(lean_statement: str) -> int:
    stmt = re.sub(r":=\s*by\b.*$", "", lean_statement or "", flags=re.DOTALL)
    return len(re.findall(r"[\(\{\[][^\)\}\]]+:[^\)\}\]]+[\)\}\]]", stmt))


def _hypothesis_copies_conclusion(lean_statement: str, conclusion: str) -> bool:
    target = re.sub(r"\s+", " ", (conclusion or "")).strip()
    if not target:
        return False
    for m in re.finditer(r"\((\w+)\s*:\s*([^()]*)\)", lean_statement or ""):
        name, typ = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip()
        if name.startswith("h") and typ == target:
            return True
    return False


def _semantic_evidence_strings(
    *,
    context_pack: dict[str, Any] | None,
    existing_artifact: dict[str, Any] | None,
    uncertainty_flags: list[str] | None,
    adversarial_flags: list[str] | None,
    roundtrip_flags: list[str] | None,
    claim_equivalence_notes: list[str] | None,
    reviewer_evaluator_evidence: list[str] | None,
) -> list[str]:
    values: list[str] = []
    values.extend(_coerce_str_list(uncertainty_flags))
    values.extend(_coerce_str_list(adversarial_flags))
    values.extend(_coerce_str_list(roundtrip_flags))
    values.extend(_coerce_str_list(claim_equivalence_notes))
    values.extend(_coerce_str_list(reviewer_evaluator_evidence))

    for source in (context_pack or {}, existing_artifact or {}):
        if not isinstance(source, dict):
            continue
        for key in (
            "semantic_equivalence_evidence",
            "semantic_evidence",
            "independent_semantic_equivalence_evidence",
            "reviewer_evaluator_evidence",
        ):
            raw = source.get(key)
            if isinstance(raw, dict):
                values.extend(f"{k}:{v}" for k, v in raw.items())
            else:
                values.extend(_coerce_str_list(raw))
        equiv = source.get("semantic_equivalence")
        if isinstance(equiv, dict):
            values.extend(f"semantic_equivalence:{k}:{v}" for k, v in equiv.items())

    return [v for v in values if v]


def _has_independent_semantic_evidence(
    *,
    context_pack: dict[str, Any] | None,
    existing_artifact: dict[str, Any] | None,
    evidence_values: list[str],
    equivalence_verdict: ClaimEquivalenceVerdict,
) -> bool:
    lowered = [v.lower() for v in evidence_values]
    if any(any(marker in value for marker in _INDEPENDENT_SEMANTIC_EVIDENCE_MARKERS) for value in lowered):
        return True
    for source in (context_pack or {}, existing_artifact or {}):
        if not isinstance(source, dict):
            continue
        if bool(source.get("semantic_equivalence_verified")) and equivalence_verdict == ClaimEquivalenceVerdict.EQUIVALENT:
            return True
        if bool(source.get("independent_semantic_evidence")) and equivalence_verdict == ClaimEquivalenceVerdict.EQUIVALENT:
            return True
        equiv = source.get("semantic_equivalence")
        if isinstance(equiv, dict):
            verdict = str(equiv.get("verdict", "") or equiv.get("claim_equivalence_verdict", "")).lower()
            if bool(equiv.get("independent")) and verdict == "equivalent":
                return True
    return False


def _semantic_adversarial_checks(
    *,
    original_latex_theorem: str,
    lean_statement: str,
    lean_conclusion: str,
    uncertainty_flags: list[str] | None,
    adversarial_flags: list[str] | None,
    roundtrip_flags: list[str] | None,
    claim_equivalence_notes: list[str] | None,
) -> dict[str, dict[str, Any]]:
    flags = [
        f.lower()
        for f in _coerce_str_list(uncertainty_flags)
        + _coerce_str_list(adversarial_flags)
        + _coerce_str_list(roundtrip_flags)
        + _coerce_str_list(claim_equivalence_notes)
    ]
    latex_l = (original_latex_theorem or "").lower()
    lean_l = (lean_statement or "").lower()
    conclusion_l = (lean_conclusion or "").lower()

    def result(triggered: bool, evidence: list[str]) -> dict[str, Any]:
        return {"passed": not triggered, "evidence": evidence if triggered else []}

    weaker_evidence = [f for f in flags if "weaker" in f or "dropped conclusion" in f]
    trivial_evidence = [
        f
        for f in flags
        if any(mark in f for mark in ("trivially_true", "vacuity", "hypothesis_copies_target"))
    ]
    if _hypothesis_copies_conclusion(lean_statement, lean_conclusion):
        trivial_evidence.append("lean_hypothesis_copies_conclusion")

    prop_evidence = [
        f
        for f in flags
        if any(mark in f for mark in ("schema_placeholder", "placeholder_or_schema", "prop_placeholder"))
    ]
    if re.search(r"\([A-Za-z_][A-Za-z0-9_']*\s*:\s*Prop\)", lean_statement or ""):
        domain_words = (
            "space",
            "function",
            "operator",
            "measure",
            "solution",
            "distribution",
            "sequence",
            "process",
            "kernel",
            "group",
            "field",
            "manifold",
        )
        if any(word in latex_l for word in domain_words):
            prop_evidence.append("lean_prop_binder_for_domain_object")

    analytic_markers = (
        "\\le",
        "\\ge",
        "\\leq",
        "\\geq",
        "≤",
        "≥",
        "estimate",
        "bound",
        "norm",
        "\\|",
        "integral",
        "\\int",
    )
    tautology_evidence: list[str] = []
    if any(mark in latex_l for mark in analytic_markers):
        if conclusion_l in {"true", "0 = 0", "1 = 1"} or re.fullmatch(
            r"([A-Za-z0-9_'.]+)\s*=\s*\1",
            conclusion_l,
        ):
            tautology_evidence.append("analytic_claim_reduced_to_tautology")
        if " : true" in lean_l:
            tautology_evidence.append("lean_statement_concludes_true")

    quantifier_evidence = [f for f in flags if "quantifier" in f and ("erase" in f or "drop" in f)]
    latex_quantified = any(q in latex_l for q in ("\\forall", "\\exists", " for all ", "there exists", "∀", "∃"))
    lean_quantified = any(q in (lean_statement or "") for q in ("∀", "∃")) or _top_level_binder_count(lean_statement) > 0
    if latex_quantified and not lean_quantified:
        quantifier_evidence.append("latex_quantifier_absent_from_lean_statement")

    return {
        "lean_theorem_weaker": result(bool(weaker_evidence), weaker_evidence),
        "trivialized_by_hypothesis": result(bool(trivial_evidence), trivial_evidence),
        "domain_object_replaced_by_prop": result(bool(prop_evidence), prop_evidence),
        "analytic_estimate_tautology": result(bool(tautology_evidence), tautology_evidence),
        "quantifiers_erased": result(bool(quantifier_evidence), quantifier_evidence),
    }


def build_semantic_equivalence_artifact(
    *,
    original_latex_theorem: str,
    normalized_natural_language_theorem: str,
    lean_statement: str,
    extracted_assumptions: list[str] | None,
    extracted_conclusion: str,
    assumptions: list[Assumption],
    equivalence_verdict: ClaimEquivalenceVerdict,
    claim_equivalence_notes: list[str],
    reviewer_evaluator_evidence: list[str] | None,
    uncertainty_flags: list[str] | None,
    adversarial_flags: list[str] | None,
    roundtrip_flags: list[str] | None,
    context_pack: dict[str, Any] | None,
    existing_artifact: dict[str, Any] | None = None,
) -> SemanticEquivalenceArtifact:
    existing = existing_artifact if isinstance(existing_artifact, dict) else {}
    context = context_pack if isinstance(context_pack, dict) else {}
    schema = _schema_from_context(context_pack=context, existing_artifact=existing)
    original = (
        original_latex_theorem.strip()
        or str(existing.get("original_latex_theorem", "") or "").strip()
        or str(context.get("original_latex_theorem", "") or "").strip()
    )
    schema_assumptions = _coerce_str_list(schema.get("assumptions"))
    assumption_texts = (
        _coerce_str_list(extracted_assumptions)
        or _coerce_str_list(existing.get("extracted_assumptions"))
        or schema_assumptions
        or [a.lean_expr for a in assumptions]
    )
    lean_target = _lean_conclusion(lean_statement)
    conclusion = (
        extracted_conclusion.strip()
        or str(existing.get("extracted_conclusion", "") or "").strip()
        or str(schema.get("claim", "") or "").strip()
        or lean_target
    )
    normalized = _normalized_theorem_text(
        original_latex_theorem=original,
        explicit_normalized=(
            normalized_natural_language_theorem.strip()
            or str(existing.get("normalized_natural_language_theorem", "") or "")
        ),
        schema=schema,
    )
    evidence = _semantic_evidence_strings(
        context_pack=context,
        existing_artifact=existing,
        uncertainty_flags=uncertainty_flags,
        adversarial_flags=adversarial_flags,
        roundtrip_flags=roundtrip_flags,
        claim_equivalence_notes=claim_equivalence_notes,
        reviewer_evaluator_evidence=reviewer_evaluator_evidence,
    )
    evidence.extend(f"claim_equivalence_note:{n}" for n in claim_equivalence_notes)
    evidence = list(dict.fromkeys(evidence))
    checks = _semantic_adversarial_checks(
        original_latex_theorem=original,
        lean_statement=lean_statement,
        lean_conclusion=lean_target,
        uncertainty_flags=uncertainty_flags,
        adversarial_flags=adversarial_flags,
        roundtrip_flags=roundtrip_flags,
        claim_equivalence_notes=claim_equivalence_notes,
    )
    independent = _has_independent_semantic_evidence(
        context_pack=context,
        existing_artifact=existing,
        evidence_values=evidence,
        equivalence_verdict=equivalence_verdict,
    )
    if bool(existing.get("independent_semantic_evidence")):
        independent = True

    return SemanticEquivalenceArtifact(
        original_latex_theorem=original,
        normalized_natural_language_theorem=normalized,
        lean_statement=lean_statement,
        extracted_assumptions=assumption_texts,
        extracted_conclusion=conclusion,
        equivalence_verdict=equivalence_verdict,
        reviewer_evaluator_evidence=evidence,
        adversarial_checks=checks,
        independent_semantic_evidence=independent,
    )


def _paper_theory_manifest_symbols(lean_file: str) -> dict[str, str]:
    """Load generated PaperTheory symbol grounding classes when available."""
    roots: list[Path] = []
    imported_modules: set[str] = set()
    if lean_file:
        p = Path(lean_file)
        roots.extend([p.parent, *p.parents])
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                imported_modules = {
                    m.rsplit(".", 1)[-1]
                    for m in re.findall(r"(?m)^\s*import\s+(Desol\.PaperTheory\.Paper_[A-Za-z0-9_]+)\b", text)
                }
            except Exception:
                imported_modules = set()
    roots.append(Path(".").resolve())
    seen: set[Path] = set()
    out: dict[str, str] = {}
    for root in roots:
        candidate_root = root / "Desol" / "PaperTheory"
        if not candidate_root.exists() or candidate_root in seen:
            continue
        seen.add(candidate_root)
        for manifest in candidate_root.glob("Paper_*.manifest.json"):
            if imported_modules and manifest.name.removesuffix(".manifest.json") not in imported_modules:
                continue
            try:
                raw = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception:
                continue
            symbols = raw.get("symbols", []) if isinstance(raw, dict) else []
            if not isinstance(symbols, list):
                continue
            for sym in symbols:
                if not isinstance(sym, dict):
                    continue
                lean = str(sym.get("lean", "") or "").strip()
                grounding = str(sym.get("grounding", "") or "").strip()
                if lean:
                    out[lean] = grounding
    return out


def _debt_label_for_grounding(name: str, grounding: str) -> str:
    if grounding in {"definition_stub", "mathlib_abbrev"}:
        return f"paper_definition_stub:{name}"
    if grounding in {"local_lemma_axiom"}:
        return f"paper_local_lemma:{name}"
    return f"paper_symbol:{name}"


def _detect_axiom_debt(
    *,
    lean_file: str,
    lean_statement: str,
    proof_text: str,
    context_pack: dict[str, Any] | None,
) -> list[str]:
    """Record theorem-local dependence on paper-local axioms/stubs.

    A generated file may import a paper-theory module so isolated checks can
    elaborate, but that import alone is not proof debt. Debt is attached only
    when the theorem statement/proof/context actually references paper-local
    symbols or explicit paper-theory names.
    """
    debt: list[str] = []
    ctx = context_pack if isinstance(context_pack, dict) else {}
    raw_ctx_debt = ctx.get("axiom_debt", [])
    if isinstance(raw_ctx_debt, list):
        debt.extend(str(x).strip() for x in raw_ctx_debt if str(x).strip())
    elif isinstance(raw_ctx_debt, str) and raw_ctx_debt.strip():
        debt.append(raw_ctx_debt.strip())

    combined = "\n".join([lean_statement or "", proof_text or ""])
    if re.search(r"\bPaper_[A-Za-z0-9_]+\.", combined) or "Desol.PaperTheory" in combined:
        debt.append("paper_theory_reference")
    manifest_symbols = _paper_theory_manifest_symbols(lean_file)
    for name, grounding in manifest_symbols.items():
        if re.search(rf"(?<![A-Za-z0-9_']){re.escape(name)}(?![A-Za-z0-9_'])", combined):
            debt.append(_debt_label_for_grounding(name, grounding))

    paper_symbol_pattern = re.compile(
        r"(?<![A-Za-z0-9_'])("
        r"HSobolev|C_T|L2Space|I_i|ξ[0-9]?|Ψ[0-9]?|Γ[0-9]?|Θ|"
        r"cutoff_solution|paracontrolled_solution|cutoff_enhanced_data|"
        r"rho_V|naive_low_high_estimate"
        r")(?![A-Za-z0-9_'])"
    )
    for name in paper_symbol_pattern.findall(combined):
        if name not in manifest_symbols:
            debt.append(f"paper_symbol:{name}")

    return list(dict.fromkeys(debt))


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


def _looks_proposition_type(typ: str, name: str) -> bool:
    t = (typ or "").strip()
    if not t:
        return False
    # Explicit proposition markers.
    if any(ind in t for ind in ("∀", "∃", "≤", "≥", "<", ">", "=", "≠", "∈", "⊆", "↔", "¬")):
        return True
    # Function arrows often denote ordinary function/object binders; only treat as
    # assumptions when binder is hypothesis-like (h, h1, hFoo) or ends in Prop.
    if "→" in t or "->" in t:
        if name.startswith("h") or t.endswith("Prop"):
            return True
        return False
    if t == "Prop":
        return True
    return name.startswith("h")


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
        if _looks_proposition_type(typ, name):
            # Local theorem hypotheses should count as in-scope grounded premises,
            # not unresolved external dependencies.
            _push(
                name,
                f"({name} : {typ})",
                GroundingStatus.GROUNDED_INTERNAL_KG,
                "local_hypothesis",
            )

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
            # Normalize trust metadata when caller pre-classified grounding.
            if a.trust_class == TrustClass.TRUST_PLACEHOLDER:
                trust_cls, trust_ref = _trust_for_grounding(a.grounding)
                grounded_out.append(
                    Assumption(
                        label=a.label,
                        lean_expr=a.lean_expr,
                        grounding=a.grounding,
                        grounding_source=a.grounding_source or trust_ref,
                        trust_class=trust_cls,
                        trust_reference=a.trust_reference or trust_ref,
                    )
                )
            else:
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


def _elan_env() -> dict:
    env = os.environ.copy()
    env["PATH"] = str(Path.home() / ".elan" / "bin") + ":" + env.get("PATH", "")
    return env


def ground_assumption(
    assumption: "Assumption",
    *,
    ledger_root: Path | None = None,
    project_root: Path | None = None,
    lean_timeout: int = 30,
    cited_refs: list[str] | None = None,
) -> "Assumption":
    """Execute the grounding policy for a single assumption.

    Policy order (stops at first success):
      1. Mathlib check via `lake env lean -E "#check <expr>"`.
      2. Internal KG match: scan ledger for a FULLY_PROVEN theorem whose
         lean_statement is token-similar to the assumption lean_expr.
      3. Cited reference mining: scan ledger entries whose paper_id matches
         any entry in cited_refs and check token-similarity.
      4. Falls through to UNGROUNDED.

    Returns a copy of the assumption with updated grounding fields.
    """
    lean_expr = assumption.lean_expr

    # Step 1 — Mathlib check
    if project_root is not None:
        expr_to_check = lean_expr.strip()
        m = re.match(r"\(\w+\s*:\s*(.+)\)$", expr_to_check)
        if m:
            expr_to_check = m.group(1).strip()
        try:
            proc = subprocess.run(
                ["lake", "env", "lean", "-E", f"#check ({expr_to_check})"],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=lean_timeout,
                env=_elan_env(),
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode == 0 and "error" not in out.lower():
                assumption.grounding = GroundingStatus.GROUNDED_MATHLIB
                assumption.grounding_source = "mathlib_check"
                return assumption
        except Exception:
            pass

    if ledger_root is not None and Path(ledger_root).exists():
        assumption_tokens = set(re.findall(r'[A-Za-z0-9_]+', lean_expr))

        def _rows_from_file(p: Path) -> list[dict]:
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return []
            if isinstance(raw, list):
                return raw
            if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
                return raw["entries"]
            return []

        def _token_match(entry: dict) -> bool:
            stmt = entry.get("lean_statement", "")
            entry_tokens = set(re.findall(r'[A-Za-z0-9_]+', stmt))
            overlap = assumption_tokens & entry_tokens
            return len(overlap) / max(1, len(assumption_tokens)) > 0.4

        # Step 2 — Internal KG match: scan ledger JSONs + KG trusted layer (incl. Mathlib seed).
        # KG trusted files are JSONL (one entry per line); ledger files are JSON with "entries" list.
        def _rows_from_jsonl(p: Path) -> list[dict]:
            rows = []
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        try:
                            rows.append(json.loads(line))
                        except Exception:
                            pass
            except Exception:
                pass
            return rows

        # Ledger JSONs
        for p in Path(ledger_root).glob("*.json"):
            for entry in _rows_from_file(p):
                if not isinstance(entry, dict):
                    continue
                if entry.get("status") != VerificationStatus.FULLY_PROVEN.value:
                    continue
                if _token_match(entry):
                    assumption.grounding = GroundingStatus.GROUNDED_INTERNAL_KG
                    assumption.grounding_source = f"internal_kg:{entry.get('theorem_name', '')}"
                    return assumption

        # KG trusted layer: output/kg/trusted/*.jsonl (includes mathlib_seed.jsonl)
        # Search parent of ledger_root for kg/trusted/ — conventional layout.
        kg_trusted_dirs = [
            Path(ledger_root).parent / "kg" / "trusted",
            Path(ledger_root).parent.parent / "kg" / "trusted",
        ]
        for kg_dir in kg_trusted_dirs:
            if not kg_dir.exists():
                continue
            for p in kg_dir.glob("*.jsonl"):
                for entry in _rows_from_jsonl(p):
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("status") not in {
                        VerificationStatus.FULLY_PROVEN.value,
                        "FULLY_PROVEN",
                    }:
                        continue
                    if _token_match(entry):
                        # Distinguish Mathlib seed from internal proofs.
                        source = entry.get("trust_class", "")
                        if source == "TRUST_MATHLIB":
                            assumption.grounding = GroundingStatus.GROUNDED_MATHLIB
                            assumption.grounding_source = f"mathlib_seed:{entry.get('theorem_name', '')}"
                        else:
                            assumption.grounding = GroundingStatus.GROUNDED_INTERNAL_KG
                            assumption.grounding_source = f"kg_trusted:{entry.get('theorem_name', '')}"
                        return assumption

        # Step 3 — Cited reference mining
        if cited_refs:
            cited_set = {str(r).strip().lower() for r in cited_refs if r}
            for p in Path(ledger_root).glob("*.json"):
                # Match ledger file stem against cited paper IDs.
                stem = p.stem.lower().replace("_", "/")
                if not any(stem == c or stem in c or c in stem for c in cited_set):
                    continue
                for entry in _rows_from_file(p):
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("status") not in {
                        VerificationStatus.FULLY_PROVEN.value,
                        VerificationStatus.INTERMEDIARY_PROVEN.value,
                    }:
                        continue
                    if _token_match(entry):
                        assumption.grounding = GroundingStatus.GROUNDED_EXTERNAL_PAPER
                        assumption.grounding_source = (
                            f"cited_ref:{p.stem}:{entry.get('theorem_name', '')}"
                        )
                        return assumption

    # Step 4 — fall through
    return assumption


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
    translation_fidelity_score: float | None = None,
    status_alignment_score: float | None = None,
    dependency_trust_complete: bool | None = None,
    reproducible_env: bool | None = None,
    translation_validated: bool | None = None,
    translation_rounds_used: int | None = None,
    translation_uncertainty_flags: list[str] | None = None,
    translation_adversarial_flags: list[str] | None = None,
    translation_roundtrip_flags: list[str] | None = None,
    translation_confidence: float | None = None,
    context_pack: dict[str, Any] | None = None,
    original_latex_theorem: str = "",
    normalized_natural_language_theorem: str = "",
    extracted_assumptions: list[str] | None = None,
    extracted_conclusion: str = "",
    reviewer_evaluator_evidence: list[str] | None = None,
    semantic_equivalence_artifact: dict[str, Any] | None = None,
    had_exception: bool = False,
    proof_method: ProofMethod | None = None,
    failure_kind: FailureKind | None = None,
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
        assess_step_entailment_fn=assess_step_entailment,
    )

    failure_origin = infer_failure_origin(
        proved=proved,
        lean_statement=lean_statement,
        step_obligations=step_obligations,
        step_records=step_records,
        error_message=error_message,
    )

    # Failure kind: finer-grained taxonomy used for routing.
    if failure_kind is None:
        low_err = (error_message or "").lower()
        if not proved and ("translation not validated" in low_err or "semantic_policy_hard_block" in low_err):
            failure_kind = FailureKind.TRANSLATION_FAILURE
        elif not proved and ("unknown identifier" in low_err or "failed to synthesize" in low_err):
            failure_kind = FailureKind.MISSING_DEFINITION
        elif not proved and ("expected command" in low_err or "unexpected token" in low_err or "function expected at" in low_err):
            failure_kind = FailureKind.ELABORATION_FAILURE
        elif not proved and ("extractdata" in low_err):
            failure_kind = FailureKind.IMPORT_MISMATCH
        elif not proved:
            failure_kind = FailureKind.PROOF_SEARCH_FAILURE
        else:
            failure_kind = FailureKind.UNKNOWN

    status = infer_status(
        proved=proved,
        step_obligations=step_obligations,
        assumptions=assumptions,
        step_verdict=step_verdict,
        error=error_message,
    )

    auto_fidelity, auto_alignment = infer_quality_scores(
        proved=proved,
        step_records=step_records,
        error_message=error_message,
        lean_statement=lean_statement,
        translation_fidelity_score=translation_fidelity_score,
        status_alignment_score=status_alignment_score,
        translation_validated=translation_validated,
        translation_rounds_used=translation_rounds_used,
        translation_uncertainty_flags=translation_uncertainty_flags,
        translation_confidence=translation_confidence,
        had_exception=had_exception,
    )
    equiv_verdict, equiv_notes = infer_claim_equivalence(
        translation_validated=translation_validated,
        translation_fidelity_score=auto_fidelity,
        status_alignment_score=auto_alignment,
        uncertainty_flags=translation_uncertainty_flags,
        adversarial_flags=translation_adversarial_flags,
        roundtrip_flags=translation_roundtrip_flags,
    )
    semantic_artifact = build_semantic_equivalence_artifact(
        original_latex_theorem=original_latex_theorem,
        normalized_natural_language_theorem=normalized_natural_language_theorem,
        lean_statement=lean_statement,
        extracted_assumptions=extracted_assumptions,
        extracted_conclusion=extracted_conclusion,
        assumptions=assumptions,
        equivalence_verdict=equiv_verdict,
        claim_equivalence_notes=equiv_notes,
        reviewer_evaluator_evidence=reviewer_evaluator_evidence,
        uncertainty_flags=translation_uncertainty_flags,
        adversarial_flags=translation_adversarial_flags,
        roundtrip_flags=translation_roundtrip_flags,
        context_pack=context_pack,
        existing_artifact=semantic_equivalence_artifact,
    )
    semantic_checks_passed = all(
        bool(check.get("passed", False))
        for check in (semantic_artifact.adversarial_checks or {}).values()
        if isinstance(check, dict)
    )

    axiom_debt = _detect_axiom_debt(
        lean_file=lean_file,
        lean_statement=lean_statement,
        proof_text=proof_text,
        context_pack=context_pack,
    )
    axiom_debt_hash = (
        hashlib.sha256("\n".join(axiom_debt).encode("utf-8")).hexdigest()[:16]
        if axiom_debt
        else ""
    )

    run_indep = os.environ.get("DESOL_INDEPENDENT_VERIFY", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }
    status, validation_gates, gate_failures = evaluate_promotion_gates(
        status=status,
        proved=proved,
        step_verdict=step_verdict,
        assumptions=assumptions,
        provenance=provenance,
        project_root=project_root,
        translation_fidelity_score=auto_fidelity,
        status_alignment_score=auto_alignment,
        dependency_trust_complete=dependency_trust_complete,
        reproducible_env=reproducible_env,
        lean_statement=lean_statement,
        proof_text=proof_text,
        run_independent_verify=run_indep,
        claim_equivalence_verdict=equiv_verdict,
        independent_semantic_evidence=semantic_artifact.independent_semantic_evidence,
        semantic_adversarial_checks_passed=semantic_checks_passed,
        axiom_debt=axiom_debt,
    )

    theorem_trust_class, theorem_trust_ref, promotion_gate = _derive_theorem_trust(
        assumptions=assumptions,
        status=status,
    )
    if gate_failures:
        theorem_trust_ref = theorem_trust_ref + ";gate_failures=" + ",".join(gate_failures)
    review_required = bool(
        equiv_verdict != ClaimEquivalenceVerdict.EQUIVALENT
        or ("claim_equivalent" in set(gate_failures))
        or ("independent_semantic_equivalence_evidence" in set(gate_failures))
        or ("semantic_adversarial_checks_passed" in set(gate_failures))
    )
    review_queue_id = f"review::{theorem_name}" if review_required else ""

    # Infer proof_method when not supplied by the caller.
    # Only LEAN_VERIFIED when the Lean kernel actually confirmed the proof
    # (step_records contain a "proof-finished" or "state-advanced" result).
    if proof_method is None:
        if proved:
            _step_results = {
                str(r.get("result", "") if isinstance(r, dict) else getattr(r, "result", ""))
                for r in step_records
            }
            if _step_results & {"proof-finished", "state-advanced"}:
                proof_method = ProofMethod.LEAN_VERIFIED
            else:
                proof_method = ProofMethod.UNKNOWN
        else:
            proof_method = ProofMethod.UNKNOWN

    return TheoremLedgerEntry(
        theorem_name=theorem_name,
        lean_file=lean_file,
        lean_statement=lean_statement,
        status=status,
        step_verdict=step_verdict,
        failure_origin=failure_origin,
        failure_kind=failure_kind,
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
        proof_method=proof_method,
        rounds_used=rounds_used,
        time_s=round(time_s, 2),
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        validation_gates=validation_gates,
        gate_failures=gate_failures,
        claim_equivalence_verdict=equiv_verdict,
        claim_equivalence_notes=equiv_notes,
        semantic_equivalence_artifact=semantic_artifact,
        review_required=review_required,
        review_queue_id=review_queue_id,
        context_pack=dict(context_pack or {}),
        axiom_debt=axiom_debt,
        axiom_debt_hash=axiom_debt_hash,
        closure_claim=(
            "lean_verified_without_paper_local_axioms"
            if status == VerificationStatus.FULLY_PROVEN and not axiom_debt
            else "proved_modulo_paper_local_axioms"
            if status == VerificationStatus.AXIOM_BACKED and axiom_debt
            else "not_closed"
        ),
    )
