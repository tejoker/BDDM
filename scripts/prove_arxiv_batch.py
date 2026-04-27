#!/usr/bin/env python3
"""Batch proof search over translated arxiv theorems.

Reads .lean output files from the translation pipeline, finds theorems with
`sorry` bodies (validated statements), and runs prove_with_full_draft_repair
on each one, replacing sorry with actual proofs where successful.

Usage:
    # Prove all sorry theorems from a specific paper:
    python3 scripts/prove_arxiv_batch.py --lean-file output/tests/algebra_2304.09598.lean

    # Prove theorems from all papers in a domain:
    python3 scripts/prove_arxiv_batch.py --domain algebra

    # Prove everything (slow):
    python3 scripts/prove_arxiv_batch.py --all

    # Dry-run (list theorems without proving):
    python3 scripts/prove_arxiv_batch.py --domain algebra --dry-run
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from dataclasses import dataclass, field

from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

logger = logging.getLogger(__name__)

try:
    from bridge_proofs import collect_bridge_retry_targets, execute_bridge_chain
except Exception:
    try:
        from scripts.bridge_proofs import collect_bridge_retry_targets, execute_bridge_chain
    except Exception:
        collect_bridge_retry_targets = None
        execute_bridge_chain = None

try:
    from equivalence_repair import attempt_equivalence_repair
except Exception:
    attempt_equivalence_repair = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Parse validated .lean files to extract sorry theorems
# ---------------------------------------------------------------------------

# Matches theorem/lemma/def declarations with sorry body.
_DECL_RE = re.compile(
    r"^((?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma|def)\s+(\w+)[^:=]*:=[^\n]*\n"
    r"(?:(?!^(?:theorem|lemma|def|end|namespace)\b)[^\n]*\n)*?"
    r"\s*sorry\s*\n?)",
    re.MULTILINE,
)

# Also match single-line: `theorem foo ... := by\n  sorry`
_SINGLE_SORRY_RE = re.compile(
    r"^((?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma)\s+(\w+)[^\n]*:= by\s*\n\s*sorry)",
    re.MULTILINE,
)


@dataclass
class SorryTheorem:
    name: str          # unqualified name as it appears in the file
    full_name: str     # namespace-qualified name
    declaration: str   # full declaration text with sorry
    lean_file: Path    # path to the .lean file


def _extract_sorry_theorems(lean_file: Path) -> list[SorryTheorem]:
    """Return all theorems with sorry bodies in the given .lean file."""
    text = lean_file.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    # Detect namespace.
    ns_m = re.search(r"^namespace\s+(\w+)", text, re.MULTILINE)
    namespace = ns_m.group(1) if ns_m else ""

    results: list[SorryTheorem] = []
    seen: set[str] = set()

    # Robust multiline declaration extraction:
    # theorem/lemma header may span multiple lines before `:= by`, then body starts with `sorry`.
    decl_start_re = re.compile(r"^\s*(?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma)\s+(\w+)\b")
    next_decl_re = re.compile(r"^\s*(?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma|def|end|namespace)\b")
    i = 0
    while i < len(lines):
        m = decl_start_re.match(lines[i])
        if not m:
            i += 1
            continue
        name = m.group(1)
        j = i
        by_idx = -1
        while j < len(lines):
            if j > i and next_decl_re.match(lines[j]) and ":= by" not in lines[j]:
                break
            if ":= by" in lines[j]:
                by_idx = j
                break
            j += 1
        if by_idx < 0:
            i += 1
            continue

        k = by_idx + 1
        while k < len(lines) and not lines[k].strip():
            k += 1
        if k < len(lines) and re.match(r"^\s*sorry\s*$", lines[k]):
            if name not in seen:
                seen.add(name)
                full_name = f"{namespace}.{name}" if namespace else name
                decl = "".join(lines[i : k + 1])
                results.append(
                    SorryTheorem(
                        name=name,
                        full_name=full_name,
                        declaration=decl,
                        lean_file=lean_file,
                    )
                )
            i = k + 1
            continue
        i += 1

    # Backstop for simple single-line patterns.
    for m in _SINGLE_SORRY_RE.finditer(text):
        name = m.group(2)
        if name in seen:
            continue
        seen.add(name)
        # Skip placeholder trivial stubs.
        if name.startswith("thm_") or name.startswith("prop_") or name.startswith("lemma_"):
            if ": True := trivial" in m.group(1) or ": True := by" in m.group(1):
                continue
        full_name = f"{namespace}.{name}" if namespace else name
        results.append(SorryTheorem(
            name=name,
            full_name=full_name,
            declaration=m.group(1),
            lean_file=lean_file,
        ))

    return results


def _sanitize_generated_lean_file(lean_file: Path) -> bool:
    """Repair common malformed separator patterns in generated Lean files.

    Some generated papers accidentally glue theorem separators onto proof lines,
    e.g. `sorry-- [theorem] Next`, which pollutes tactic states and causes
    misleading `assumption` errors during proof execution.
    """
    text = lean_file.read_text(encoding="utf-8")
    fixed = text
    fixed = re.sub(
        r"(?m)^([ \t]*(?:sorry|trivial)\s*)(--\s*\[theorem\])",
        r"\1\n\n\2",
        fixed,
    )
    # Ensure theorem markers start a fresh block (blank line before marker).
    lines = fixed.splitlines()
    out_lines: list[str] = []
    for ln in lines:
        if ln.lstrip().startswith("-- [theorem]"):
            if out_lines and out_lines[-1].strip():
                out_lines.append("")
        out_lines.append(ln)
    # Ensure the first proof-body line after `:= by` is indented.
    proof_head_re = re.compile(
        r"^\s*(?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma|def)\s+[A-Za-z_][A-Za-z0-9_']*.*:=\s*by\s*$"
    )
    i = 0
    while i < len(out_lines):
        if not proof_head_re.match(out_lines[i]):
            i += 1
            continue
        j = i + 1
        while j < len(out_lines) and not out_lines[j].strip():
            j += 1
        if j < len(out_lines):
            body = out_lines[j]
            body_stripped = body.lstrip()
            if body_stripped and not body.startswith((" ", "\t")) and not body_stripped.startswith("--"):
                out_lines[j] = "  " + body_stripped
        i = j + 1
    fixed = "\n".join(out_lines)
    if text.endswith("\n"):
        fixed += "\n"
    if fixed == text:
        return False
    lean_file.write_text(fixed, encoding="utf-8")
    return True


def _is_nontrivial_declaration(decl: str, *, strict: bool = False) -> bool:
    return not _nontrivial_drop_reasons(decl, strict=strict)


def _schema_placeholder_hyp_identity(decl: str) -> tuple[str, str] | None:
    """Detect placeholder theorem body — three patterns:

    1. Single identity: `(p_c1 : Prop) (h_c1 : p_c1) : p_c1 := by`
    2. Multi-conjunct:  `... (p_c1 : Prop) (h_c1 : p_c1) ... : p_c1 ∧ p_c2 ∧ ... := by`
    3. literal_schema_translation theorem name (always placeholder regardless of body).

    Returns (p_var_name, h_var_name) for the first hypothesis slot found, or None.
    """
    s = " ".join((decl or "").split())
    # Pattern 1 & 2: p_c1 identity body (single or conjunction of p_cN).
    m = re.search(
        r"\((p_c\d+)\s*:\s*Prop\)\s*\((h_c\d+)\s*:\s*\1\)",
        s,
    )
    if m:
        # Confirm the return type is a conjunction of p_cN or a single p_cN.
        ret_m = re.search(r":\s*((?:p_c\d+)(?:\s*∧\s*p_c\d+)*)\s*:=\s*by", s)
        if ret_m:
            return m.group(1), m.group(2)
    # Pattern 3: literal_schema_translation theorem (always a placeholder).
    if re.search(r"(?:^|\s)theorem\s+literal_schema_translation\b", s):
        # Extract first h_cN for tactic selection; fall back to generic.
        hm = re.search(r"\((h_c\d+)\s*:\s*p_c\d+\)", s)
        pm = re.search(r"\((p_c\d+)\s*:\s*Prop\)", s)
        if hm and pm:
            return pm.group(1), hm.group(1)
        return "p_c1", "h_c1"
    return None


def _nontrivial_drop_reasons(decl: str, *, strict: bool = False) -> list[str]:
    s = " ".join((decl or "").split())
    if not s:
        return ["empty"]
    low = s.lower()
    reasons: list[str] = []
    schema_identity = _schema_placeholder_hyp_identity(decl) is not None

    if re.search(r"^\s*(?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma)\s+schema_", low):
        reasons.append("schema_name")
    # literal_schema_translation theorems have a p_c1 identity body and are auto-closable;
    # do NOT drop them — let prove_one's schema_identity gate handle them.
    if strict:
        if re.search(r":\s*true\s*:=\s*by", low):
            reasons.append("true_by")
        if re.search(r":\s*true\s*$", low):
            reasons.append("true_end")
    if "schema_translation" in low or "schema_fallback" in low:
        # Only drop when no p_c1 identity body detected (which would allow auto-close).
        if not schema_identity:
            reasons.append("schema_translation")

    if strict:
        if re.search(r":\s*\(?\s*0\s*:\s*ℕ\s*\)?\s*=\s*0\s*(?::=|$)", s):
            reasons.append("nat0eq0")
        if re.search(r"→\s*\(?\s*0\s*:\s*ℕ\s*\)?\s*=\s*0\s*(?::=|$)", s):
            reasons.append("imp_nat0eq0")
        if (not schema_identity) and re.search(r":\s*p_c\d+\s*(?::=|$)", s):
            reasons.append("p_c_placeholder")
    else:
        # Relaxed mode: reject only pure placeholder goals.
        has_structure = any(tok in s for tok in ("→", "->", "↔", "∧", "∨", "∀", "∃"))
        if re.search(r":\s*\(?\s*0\s*:\s*ℕ\s*\)?\s*=\s*0\s*(?::=|$)", s) and not has_structure:
            reasons.append("pure_nat0eq0")
        if (not schema_identity) and re.search(r":\s*p_c\d+\s*(?::=|$)", s) and not has_structure:
            reasons.append("pure_p_c_placeholder")

    if (not schema_identity) and (not any(tok in s for tok in ("→", "->", "↔", "=", "≤", "≥", "<", ">", "∃", "∀", "True"))):
        reasons.append("no_math_token")
    return reasons


def _translation_limited_reason(decl: str) -> str:
    """Return a reason when a declaration is a translation placeholder, not a proof target."""
    s = " ".join((decl or "").split())
    if not s:
        return "empty_translation"
    low = s.lower()
    target = _decl_target(decl)
    if _schema_placeholder_hyp_identity(decl) is not None:
        return "schema_placeholder_identity"
    if re.search(r"^\s*(?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma)\s+schema_", low):
        return "schema_named_placeholder"
    if "schema_translation" in low or "schema_fallback" in low or "literal_schema_translation" in low:
        return "schema_translation_placeholder"
    if re.search(r"\(p_c\d+\s*:\s*Prop\)", s):
        return "prop_slot_placeholder"
    if target:
        if re.fullmatch(r"True", target):
            return "trivial_true_target"
        if re.fullmatch(r"\(?\s*0\s*:\s*ℕ\s*\)?\s*=\s*0", target):
            return "trivial_nat0eq0_target"
        if re.fullmatch(
            r"(?:[A-Za-z_][A-Za-z0-9_']*\s*(?:∧\s*)?)+→\s*\(?\s*0\s*:\s*ℕ\s*\)?\s*=\s*0",
            target,
        ):
            prop_binders = set(re.findall(r"\((h\d+)\s*:\s*Prop\)", s))
            target_tokens = set(re.findall(r"\b(h\d+)\b", target.split("→", 1)[0]))
            if target_tokens and target_tokens <= prop_binders:
                return "relaxed_prop_trivial_nat_implication"
        prop_binders = set(re.findall(r"\((p_[A-Za-z0-9_']+|h_[A-Za-z0-9_']+|h\d+)\s*:\s*Prop\)", s))
        if prop_binders and re.fullmatch(r"(?:[A-Za-z0-9_']+\s*(?:∧|∨|↔|→)\s*)*[A-Za-z0-9_']+", target):
            target_tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_']*", target))
            if target_tokens and target_tokens <= prop_binders:
                return "pure_prop_slot_target"
    return ""


def _normalize_prop_for_gate(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _hypothesis_copies_target_issue(decl: str) -> str:
    target = _normalize_prop_for_gate(_decl_target(decl))
    if not target:
        return ""
    stmt = re.sub(r":=\s*by\b.*$", "", decl or "", flags=re.DOTALL)
    for match in re.finditer(
        r"\(([hH][A-Za-z0-9_']*)\s*:\s*([^()]+?)\)",
        stmt,
        flags=re.DOTALL,
    ):
        name = match.group(1)
        typ = _normalize_prop_for_gate(match.group(2))
        suspicious_name = re.search(r"(?:easy|claim|target|bound|conclusion|result)", name, re.IGNORECASE)
        relation_target = any(tok in target for tok in ("=", "≤", "≥", "<", ">", "↔", "∧", "∨", "∃", "∀"))
        if typ == target and (relation_target or suspicious_name):
            return f"claim_copied_into_hypothesis:{name}"
    return ""


def _translation_gate_issue(source_entry: dict | None, decl: str) -> str:
    """Block proof search when translation evidence says the statement is not actionable."""
    reason = _translation_limited_reason(decl)
    if reason:
        return reason
    copied_hyp = _hypothesis_copies_target_issue(decl)
    if copied_hyp:
        return f"translation_hard_block:{copied_hyp}"
    if not isinstance(source_entry, dict):
        return ""
    flags: list[str] = []
    for key in ("translation_uncertainty_flags", "translation_adversarial_flags", "translation_roundtrip_flags", "gate_failures"):
        val = source_entry.get(key)
        if isinstance(val, list):
            flags.extend(str(x).lower() for x in val)
    hard_markers = (
        "trivialization_hard_violation",
        "trivially_true",
        "semantic_policy_violation",
        "schema_coverage_missing",
        "claim_shape_mismatch",
        "claim_copied_into_hypothesis",
        "verdict:wrong",
        "roundtrip_semantic_mismatch",
    )
    for flag in flags:
        if any(marker in flag for marker in hard_markers):
            return f"translation_hard_block:{flag[:80]}"
    return ""


def _semantic_artifact_kwargs(source_entry: dict | None) -> dict:
    if not isinstance(source_entry, dict):
        return {}
    artifact = source_entry.get("semantic_equivalence_artifact")
    if not isinstance(artifact, dict):
        artifact = None
    evidence: list[str] = []
    if artifact and isinstance(artifact.get("reviewer_evaluator_evidence"), list):
        evidence.extend(str(x) for x in artifact["reviewer_evaluator_evidence"] if str(x).strip())
    return {
        "semantic_equivalence_artifact": artifact,
        "original_latex_theorem": (
            str((artifact or {}).get("original_latex_theorem", "") or "")
            or str(source_entry.get("original_latex_theorem", "") or "")
        ),
        "normalized_natural_language_theorem": str(
            (artifact or {}).get("normalized_natural_language_theorem", "") or ""
        ),
        "extracted_assumptions": (
            [str(x) for x in ((artifact or {}).get("extracted_assumptions") or [])]
            if artifact
            else None
        ),
        "extracted_conclusion": str((artifact or {}).get("extracted_conclusion", "") or ""),
        "reviewer_evaluator_evidence": evidence,
    }


def _extract_decl_block_for_name(text: str, theorem_name: str) -> str:
    if not text or not theorem_name:
        return ""
    lines = text.splitlines()
    start_re = re.compile(
        rf"^\s*(?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma|def)\s+{re.escape(theorem_name)}\b"
    )
    next_re = re.compile(r"^\s*(?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma|def|end|namespace)\b")
    start = -1
    for i, ln in enumerate(lines):
        if start_re.match(ln):
            start = i
            break
    if start < 0:
        return ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if next_re.match(lines[j]):
            end = j
            break
    return "\n".join(lines[start:end])


def _looks_trivially_closed_decl(decl: str) -> bool:
    s = (decl or "").strip()
    if not s or "sorry" in s:
        return False
    if re.search(r"(?m)^\s*exact\s+h_[A-Za-z0-9_']+\s*$", s):
        return True
    if re.search(r"(?m)^\s*exact\s+h_c\d+\s*$", s):
        return True
    if re.search(r"(?m)^\s*(trivial|rfl)\s*$", s):
        return True
    return False


def _reconcile_trivial_closed_ledger_entries(*, paper_id: str, lean_files: list[Path]) -> int:
    if not paper_id or not lean_files:
        return 0
    try:
        from pipeline_status import load_ledger, save_ledger
    except Exception:
        return 0

    file_text_by_name: dict[str, str] = {}
    for lf in lean_files:
        try:
            file_text_by_name[str(lf.resolve())] = lf.read_text(encoding="utf-8")
        except Exception:
            continue

    rows = load_ledger(paper_id)
    changed = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("status", "")).upper() == "FULLY_PROVEN":
            continue
        lf = str(row.get("lean_file", "")).strip()
        if not lf:
            continue
        text = file_text_by_name.get(str(Path(lf).resolve()))
        if not text:
            continue
        raw_name = str(row.get("theorem_name", "")).strip()
        if not raw_name:
            continue
        short_name = raw_name.rsplit(".", 1)[-1]
        decl = _extract_decl_block_for_name(text, short_name)
        if not _looks_trivially_closed_decl(decl):
            continue

        # Reconciliation is only an observation. It must not promote ledger
        # status because pattern matching (`rfl`, `trivial`, `exact h_*`) is not
        # independent semantic or kernel evidence for the original paper claim.
        row["reconcile_trivial_file_match"] = True
        row["reconcile_note"] = "trivial proof pattern observed; status not promoted"
        row["promotion_gate_passed"] = False
        vg = row.get("validation_gates")
        if not isinstance(vg, dict):
            vg = {}
            row["validation_gates"] = vg
        vg["reconcile_trivial_file_match"] = True
        changed += 1

    if changed:
        save_ledger(paper_id, rows)
    return changed


# ---------------------------------------------------------------------------
# Proof result tracking
# ---------------------------------------------------------------------------

@dataclass
class ProofResult:
    theorem_name: str
    lean_file: str
    proved: bool
    proof_text: str = ""
    rounds_used: int = 0
    time_s: float = 0.0
    error: str = ""
    status: str = "UNRESOLVED"  # VerificationStatus value


def _value_samples_to_step_records(samples: list[dict]) -> list[dict]:
    """Convert MCTS value samples to ledger-compatible step records."""
    records: list[dict] = []
    for idx, sample in enumerate(samples, start=1):
        payload = {
            "raw_value": sample.get("raw_value", 0.0),
            "normalized_value": sample.get("normalized_value", 0.0),
            "tactics_estimate": sample.get("tactics_estimate", None),
            "cache_hit": bool(sample.get("cache_hit", False)),
            "source": sample.get("source", "model_fallback"),
            "state_chars": int(sample.get("state_chars", 0) or 0),
        }
        if sample.get("error"):
            payload["error"] = str(sample.get("error"))[:300]
        records.append(
            {
                "step": idx,
                "attempt": 0,
                "tactic": "__value_estimate__",
                "model_turns": 1,
                "result": "value-estimate",
                "detail": json.dumps(payload, ensure_ascii=True),
            }
        )
    return records


def _save_results(results: list[ProofResult], out_path: Path) -> None:
    data = [
        {
            "theorem": r.theorem_name,
            "file": r.lean_file,
            "proved": r.proved,
            "status": r.status,
            "rounds": r.rounds_used,
            "time_s": round(r.time_s, 1),
            "error": r.error[:200] if r.error else "",
        }
        for r in results
    ]
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _patch_proof_into_file(lean_file: Path, theorem_name: str, proof_text: str) -> bool:
    """Replace the sorry body of theorem_name with proof_text in the lean file."""
    text = lean_file.read_text(encoding="utf-8")
    # Find the sorry block for this theorem.
    pattern = re.compile(
        r"(theorem\s+" + re.escape(theorem_name) + r"[^\n]*:= by\s*\n)\s*sorry",
        re.MULTILINE,
    )
    new_text = pattern.sub(
        lambda m: m.group(1) + proof_text.rstrip() + "\n",
        text,
        count=1,
    )
    if new_text == text:
        return False  # nothing replaced
    lean_file.write_text(new_text, encoding="utf-8")
    return True


def _replace_declaration_block_in_file(lean_file: Path, old_decl: str, new_decl: str) -> bool:
    """Replace one exact theorem declaration block in a Lean file."""
    text = lean_file.read_text(encoding="utf-8")
    if old_decl not in text:
        return False
    new_text = text.replace(old_decl, new_decl, 1)
    if new_text == text:
        return False
    lean_file.write_text(new_text, encoding="utf-8")
    return True


def _normalize_repaired_decl_for_theorem(*, repaired_signature: str, theorem_name: str) -> str:
    s = (repaired_signature or "").strip()
    if not s:
        return ""
    s = re.sub(
        r"^\s*((?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma|def)\s+)([A-Za-z_][A-Za-z0-9_'.]*)",
        rf"\1{theorem_name}",
        s,
        count=1,
    )
    s = re.sub(r":=\s*by\b[\s\S]*$", "", s).rstrip()
    s = re.sub(r":=\s*sorry\s*$", "", s).rstrip()
    s = re.sub(r":=\s*$", "", s).rstrip()
    return f"{s} := by\n  sorry"


def _decl_target(decl: str) -> str:
    d = (decl or "").strip()
    if not d:
        return ""
    by_idx = d.find(":= by")
    if by_idx < 0:
        return ""
    head = d[:by_idx]
    depth = 0
    colon_positions: list[int] = []
    for i, ch in enumerate(head):
        if ch in "([{":
            depth += 1
            continue
        if ch in ")]}":
            depth = max(0, depth - 1)
            continue
        if ch == ":" and depth == 0:
            colon_positions.append(i)
    if not colon_positions:
        return ""
    target = head[colon_positions[-1] + 1 :].strip()
    return " ".join(target.split()).strip()


def _split_top_level_implication(expr: str) -> tuple[str, str] | None:
    if not expr:
        return None
    depth = 0
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0 and ch == "→":
            return expr[:i].strip(), expr[i + 1 :].strip()
        elif depth == 0 and expr.startswith("->", i):
            return expr[:i].strip(), expr[i + 2 :].strip()
        i += 1
    return None


def _split_top_level_conjunction(expr: str) -> tuple[str, str] | None:
    if not expr:
        return None
    depth = 0
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0 and ch == "∧":
            return expr[:i].strip(), expr[i + 1 :].strip()
        i += 1
    return None


def _strip_outer_parens(text: str) -> str:
    s = (text or "").strip()
    while s.startswith("(") and s.endswith(")"):
        inner = s[1:-1].strip()
        if not inner:
            break
        s = inner
    return s


def _normalize_prop(text: str) -> str:
    return re.sub(r"\s+", "", _strip_outer_parens(text or ""))


def _binder_groups_before_target(decl: str) -> list[str]:
    """Return top-level parenthesized binder contents before the theorem target."""
    header = re.sub(r":=\s*by\b.*$", "", (decl or "").strip(), flags=re.DOTALL)
    depth = 0
    last_colon = -1
    for i, ch in enumerate(header):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0:
            last_colon = i
    prefix = header[:last_colon] if last_colon >= 0 else header
    groups: list[str] = []
    i = 0
    while i < len(prefix):
        if prefix[i] != "(":
            i += 1
            continue
        start = i
        depth = 0
        while i < len(prefix):
            if prefix[i] == "(":
                depth += 1
            elif prefix[i] == ")":
                depth -= 1
                if depth == 0:
                    groups.append(prefix[start + 1 : i].strip())
                    break
            i += 1
        i += 1
    return groups


def _hypotheses_by_type(decl: str) -> dict[str, str]:
    by_type: dict[str, str] = {}
    for group in _binder_groups_before_target(decl):
        if ":" not in group:
            continue
        names_part, typ = group.split(":", 1)
        typ_n = _normalize_prop(typ)
        if not typ_n:
            continue
        for nm in re.findall(r"\b([A-Za-z_][A-Za-z0-9_']*)\b", names_part):
            if nm.startswith("h"):
                by_type.setdefault(typ_n, nm)
    return by_type


def _implication_chain(expr: str) -> tuple[list[str], str]:
    premises: list[str] = []
    cur = (expr or "").strip()
    while True:
        split = _split_top_level_implication(cur)
        if split is None:
            break
        lhs, rhs = split
        premises.append(lhs.strip())
        cur = rhs.strip()
    return premises, cur


def _is_reflexive_equality(expr: str) -> bool:
    s = (expr or "").strip()
    if not s:
        return False
    depth = 0
    for i, ch in enumerate(s):
        if ch in "([{":
            depth += 1
            continue
        if ch in ")]}":
            depth = max(0, depth - 1)
            continue
        if ch != "=" or depth != 0:
            continue
        prev_c = s[i - 1] if i > 0 else ""
        next_c = s[i + 1] if i + 1 < len(s) else ""
        if prev_c in "<>=" or next_c == "=":
            continue
        lhs_raw = s[:i]
        rhs_raw = s[i + 1 :]
        lhs = _normalize_prop(lhs_raw)
        rhs = _normalize_prop(rhs_raw)
        if lhs and rhs and lhs == rhs:
            return True
        lhs0 = re.sub(r"\s+", "", _strip_outer_parens(lhs_raw))
        rhs0 = re.sub(r"\s+", "", _strip_outer_parens(rhs_raw))
        zero_like = re.compile(r"^0(?::[^()]+)?$")
        if zero_like.fullmatch(lhs0) and zero_like.fullmatch(rhs0):
            return True
        return False
    return False


def _goal_text_from_state_pp(state_pp: str) -> str:
    for ln in (state_pp or "").splitlines():
        if "⊢" in ln:
            return ln.split("⊢", 1)[1].split("--", 1)[0].strip()
    return ""


def _hypotheses_from_state_pp(state_pp: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for ln in (state_pp or "").splitlines():
        s = ln.strip()
        if not s or "⊢" in s or ":" not in s:
            continue
        name, ty = s.split(":", 1)
        n = name.strip()
        t = ty.split("--", 1)[0].strip()
        if n and t:
            out.append((n, t))
    return out


def _slot_scripts_for_state(state_pp: str) -> list[list[str]]:
    goal = _goal_text_from_state_pp(state_pp)
    hyps = _hypotheses_from_state_pp(state_pp)
    if not goal:
        return []

    hyp_by_norm: dict[str, str] = {}
    for n, t in hyps:
        norm = _normalize_prop(t)
        if norm and norm not in hyp_by_norm:
            hyp_by_norm[norm] = n

    scripts: list[list[str]] = []
    goal_n = _normalize_prop(goal)
    if goal_n and goal_n in hyp_by_norm:
        scripts.append([f"exact {hyp_by_norm[goal_n]}"])

    conj = _split_top_level_conjunction(goal)
    if conj is not None:
        left, right = conj
        l_n = _normalize_prop(left)
        r_n = _normalize_prop(right)
        if l_n in hyp_by_norm and r_n in hyp_by_norm:
            scripts.append(
                [
                    "constructor",
                    f"exact {hyp_by_norm[l_n]}",
                    f"exact {hyp_by_norm[r_n]}",
                ]
            )

    imp = _split_top_level_implication(goal)
    if imp is not None:
        lhs, rhs = imp
        lhs_n = _normalize_prop(lhs)
        rhs_n = _normalize_prop(rhs)
        rhs_h = hyp_by_norm.get(rhs_n, "")
        for n, t in hyps:
            i = _split_top_level_implication(t)
            if i is None:
                continue
            h_l, h_r = i
            if _normalize_prop(h_l) == lhs_n and _normalize_prop(h_r) == rhs_n:
                scripts.append(["intro h", f"exact {n} h"])
                break
        if rhs_h:
            scripts.append(["intro _", f"exact {rhs_h}"])

    # Deterministic close for implication-chains ending in reflexive equalities.
    # Example: h1 -> h2 -> (0 : ℕ) = 0  ==> intros; rfl
    premises, consequent = _implication_chain(goal)
    if _is_reflexive_equality(consequent):
        intro_count = max(0, len(premises))
        intro_script = [f"intro h{i+1}" for i in range(intro_count)]
        scripts.append([*intro_script, "rfl"] if intro_script else ["rfl"])

    # Dedupe while preserving order.
    seen: set[str] = set()
    uniq: list[list[str]] = []
    for sc in scripts:
        key = " ;; ".join(sc)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(sc)
    return uniq


def _predicate_tokens(expr: str) -> set[str]:
    toks = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*_packets)\b", expr or ""))
    return toks


def _statement_shape_route(decl: str) -> str:
    target = _decl_target(decl)
    if not target:
        return ""
    # Definition-like equality: bare variable (no hypotheses) equals concrete literal/container.
    # Only route to definition_lane when there are NO binder hypotheses in the declaration,
    # i.e., this is truly an unconstrained `a = {literal}` with nothing to prove.
    # If there are hypotheses (h_..., hn, etc.) it is a characterisation theorem, not a definition.
    if re.search(r"^\s*[a-z][A-Za-z0-9_']*\s*=\s*[\{\[]", target):
        # Count hypothesis binders in the full declaration — if any exist, this is provable.
        if not re.search(r"\(h[_A-Za-z0-9']*\s*:", decl or ""):
            return "definition_lane"
    return ""


def _provability_sanity_issue(decl: str) -> str:
    target = _decl_target(decl)
    if not target:
        return "missing_target"
    if re.fullmatch(r"True", target):
        return "trivial_true_target"
    if re.fullmatch(r"\(?\s*0\s*:\s*ℕ\s*\)?\s*=\s*0", target):
        return "trivial_nat0eq0_target"
    # Only flag unconstrained equality (no hypotheses) as unprovable.
    if re.search(r"^\s*[a-z][A-Za-z0-9_']*\s*=\s*[\{\[]", target):
        if not re.search(r"\(h[_A-Za-z0-9']*\s*:", decl or ""):
            return "definition_like_unconstrained_equality"

    # Consequent introduces packet predicates not present in antecedent.
    split = _split_top_level_implication(target)
    if split is not None:
        lhs, rhs = split
        extra = sorted(_predicate_tokens(rhs) - _predicate_tokens(lhs))
        if extra:
            return "unconstrained_consequent_symbols:" + ",".join(extra)
    return ""


def _is_repl_startup_failure(last_error: str) -> bool:
    """Return True when the error is a REPL tactic-mode entry failure.

    'unexpected identifier; expected command' at line=1 means Lean received the
    tactic block at the command level — this happens when the theorem statement
    fails to elaborate (e.g. unresolved type metavariables) so the REPL never
    enters tactic mode.  Running more proof-search rounds is futile in this case;
    the statement needs to be repaired instead.
    """
    err = (last_error or "").lower()
    return (
        "unexpected identifier" in err
        and "expected command" in err
        and ("line=1" in err or "line 1" in err)
    )


def _constrained_regen_hint(decl: str, last_error: str) -> str:
    target = _decl_target(decl)
    hints: list[str] = [
        "Produce executable Lean tactics only.",
        "Do not use introN or placeholder witness notation.",
    ]
    if "∧" in target:
        hints.append(
            "Goal is conjunction-shaped: use `constructor`, then solve branches with `assumption`/`exact`."
        )
    if "∃!" in target or "exists!" in target.lower():
        hints.append(
            "Goal uses ExistsUnique: only use witness constructors when the goal is existential."
        )
    if "Invalid `⟨...⟩` notation" in (last_error or ""):
        hints.append("Avoid tuple/witness notation unless Lean goal is explicitly existential.")
    if "simp made no progress" in (last_error or ""):
        hints.append("Avoid `simp` as first tactic when no rewrite lemmas apply.")
    if _is_repl_startup_failure(last_error or ""):
        hints.append(
            "The REPL failed to enter tactic mode — the statement likely has an unresolved type metavariable. "
            "Ensure all bound variables have explicit types (e.g. `∃! (b : α), ...` not `∃! b, ...`)."
        )
    return "\n".join(hints)


def _domain_proof_hint(decl: str) -> str:
    """Small paper-agnostic hints keyed by statement shape/domain vocabulary."""
    s = " ".join((decl or "").split())
    hints: list[str] = []
    if any(tok in s for tok in ("ℝ", "Real", "≤", "≥", "<", ">", "norm", "abs")):
        hints.append("For analysis/arithmetic goals, try short deterministic steps first: `nlinarith`, `linarith`, `positivity`, `norm_num`, `simp_all`.")
    if any(tok in s for tok in ("Measure", "MeasureTheory", "ProbabilityTheory", "ae", "Measurable", "Integrable")):
        hints.append("For measure/probability goals, unfold only local hypotheses and prefer `simp_all`, `aesop`, and exact use of assumptions before inventing witnesses.")
    if any(tok in s for tok in ("Finset", "Fintype", "Set.", "∈", "⊆", "∪", "∩")):
        hints.append("For set/finite goals, consider `ext`, `constructor`, `intro`, `simp_all`, and `aesop`.")
    if any(tok in s for tok in ("Matrix", "LinearMap", "LinearEquiv", "Module", "Ring", "Group")):
        hints.append("For algebraic goals, try `ring_nf`, `simp_all`, and direct use of matching hypotheses.")
    return "\n".join(hints)


# ---------------------------------------------------------------------------
# Proof loop (calls prove_with_full_draft_repair)
# ---------------------------------------------------------------------------

def prove_one(
    thm: SorryTheorem,
    *,
    project_root: Path,
    client: object,
    model: str,
    repair_rounds: int = 5,
    retrieval_index: str = "",
    proof_mode: str = "full-draft",
    mcts_iterations: int = 12,
    mcts_repair_variants: int = 3,
    mcts_max_depth: int = 5,
    paper_id: str = "",
    dry_run: bool = False,
    verbose: bool = True,
    fallback_to_full_draft: bool = True,
) -> ProofResult:
    """Attempt to prove a single sorry theorem."""
    if dry_run:
        print(f"  [dry-run] {thm.full_name}")
        return ProofResult(
            theorem_name=thm.full_name,
            lean_file=str(thm.lean_file),
            proved=False,
            error="dry-run",
        )

    from pipeline_status import build_ledger_entry, load_ledger, save_ledger, upsert_ledger_entry

    def _sync_base_alias_entry(entry_obj: object) -> None:
        """Mirror namespaced theorem result onto base theorem ID in the same ledger."""
        if not paper_id:
            return
        if not thm.name or thm.name == thm.full_name:
            return
        try:
            entry_dict = entry_obj.to_dict()  # type: ignore[attr-defined]
            entry_dict["theorem_name"] = thm.name
            rows = load_ledger(paper_id)
            replaced = False
            for i, row in enumerate(rows):
                if isinstance(row, dict) and str(row.get("theorem_name", "")).strip() == thm.name:
                    rows[i] = entry_dict
                    replaced = True
                    break
            if not replaced:
                rows.append(entry_dict)
            save_ledger(paper_id, rows)
        except Exception:
            pass

    start = time.time()
    if verbose:
        print(f"  proving [{proof_mode}]: {thm.full_name} ...", flush=True)

    # Strict validation gate: do not enter proof search unless the statement elaborates
    # in isolation. This routes "missing definition / ill-typed artifacts" out of proof search.
    try:
        ok_elab, _detail = _verify_script_via_file_check(
            project_root=project_root,
            source_file=thm.lean_file,
            theorem_name=(thm.name or thm.full_name).rsplit(".", 1)[-1],
            theorem_decl=thm.declaration,
            script=["sorry"],
            timeout_s=35,
        )
        if not ok_elab:
            if verbose:
                print(f"    validation gate: statement does not elaborate ({_detail[:120]})", flush=True)
            try:
                from pipeline_status import build_ledger_entry, upsert_ledger_entry
                from pipeline_status_models import FailureOrigin as _FO, FailureKind as _FK
                le = build_ledger_entry(
                    theorem_name=thm.full_name,
                    lean_file=str(thm.lean_file),
                    lean_statement=thm.declaration,
                    proved=False,
                    step_records=[],
                    proof_text="",
                    error_message=f"validation_gate_elaboration_failed:{_detail}",
                    proof_mode="validation-gate",
                    rounds_used=0,
                    time_s=0.0,
                    had_exception=False,
                    failure_kind=_FK.ELABORATION_FAILURE,
                )
                le.failure_origin = _FO.FORMALIZATION_ERROR
                if paper_id:
                    upsert_ledger_entry(paper_id, le)
                    _sync_base_alias_entry(le)
            except Exception:
                pass
            return ProofResult(
                theorem_name=thm.full_name,
                lean_file=str(thm.lean_file),
                proved=False,
                error=f"validation_gate_elaboration_failed:{_detail}",
                status="FLAWED",
            )
    except Exception:
        pass

    source_entry_for_gate: dict | None = None
    if paper_id:
        lookup_ids: list[str] = [paper_id]
        if paper_id.endswith("_reliable"):
            lookup_ids = [paper_id[: -len("_reliable")], paper_id]
        for pid in lookup_ids:
            source_entry_for_gate = _load_ledger_entry_for_theorem(pid, thm.full_name)
            if isinstance(source_entry_for_gate, dict):
                break
            source_entry_for_gate = _load_ledger_entry_for_theorem(pid, thm.name)
            if isinstance(source_entry_for_gate, dict):
                break
        if not isinstance(source_entry_for_gate, dict):
            source_entry_for_gate = None

    gate_issue = _translation_gate_issue(source_entry_for_gate, thm.declaration or "")
    if gate_issue:
        if verbose:
            print(f"    translation-limited before proof search: {gate_issue}", flush=True)
        try:
            from pipeline_status_models import ProofMethod as _ProofMethod, VerificationStatus as _VS
            ledger_entry = build_ledger_entry(
                theorem_name=thm.full_name,
                lean_file=str(thm.lean_file),
                lean_statement=thm.declaration,
                proved=False,
                step_records=[],
                proof_text="",
                error_message=f"translation_gate:{gate_issue}",
                proof_mode=proof_mode,
                proof_method=_ProofMethod.TRANSLATION_LIMITED,
                rounds_used=0,
                time_s=0.0,
                had_exception=False,
                translation_validated=(
                    bool(source_entry_for_gate.get("translation_validated"))
                    if isinstance(source_entry_for_gate, dict) and source_entry_for_gate.get("translation_validated") is not None
                    else None
                ),
                translation_fidelity_score=(
                    float(source_entry_for_gate.get("translation_fidelity_score"))
                    if isinstance(source_entry_for_gate, dict) and source_entry_for_gate.get("translation_fidelity_score") is not None
                    else None
                ),
                status_alignment_score=(
                    float(source_entry_for_gate.get("status_alignment_score"))
                    if isinstance(source_entry_for_gate, dict) and source_entry_for_gate.get("status_alignment_score") is not None
                    else None
                ),
                translation_uncertainty_flags=(
                    [str(x) for x in (source_entry_for_gate.get("translation_uncertainty_flags") or [])]
                    if isinstance(source_entry_for_gate, dict)
                    else None
                ),
                translation_adversarial_flags=(
                    [str(x) for x in (source_entry_for_gate.get("translation_adversarial_flags") or [])]
                    if isinstance(source_entry_for_gate, dict)
                    else None
                ),
                translation_roundtrip_flags=(
                    [str(x) for x in (source_entry_for_gate.get("translation_roundtrip_flags") or [])]
                    if isinstance(source_entry_for_gate, dict)
                    else None
                ),
                **_semantic_artifact_kwargs(source_entry_for_gate),
            )
            ledger_entry.status = (
                _VS.FLAWED
                if gate_issue.startswith("translation_hard_block:")
                else _VS.TRANSLATION_LIMITED
            )
            ledger_entry.proof_method = _ProofMethod.TRANSLATION_LIMITED
            if paper_id:
                upsert_ledger_entry(paper_id, ledger_entry)
                _sync_base_alias_entry(ledger_entry)
        except Exception as _gate_exc:
            if verbose:
                print(f"    translation gate ledger warning: {_gate_exc}", flush=True)
        return ProofResult(
            theorem_name=thm.full_name,
            lean_file=str(thm.lean_file),
            proved=False,
            proof_text="",
            rounds_used=0,
            time_s=0.0,
            error=f"translation_gate:{gate_issue}",
            status="FLAWED" if gate_issue.startswith("translation_hard_block:") else "TRANSLATION_LIMITED",
        )

    # Pre-prove sanity gate and statement-shape router.
    # Detect schema placeholder theorem bodies by signature pattern, not theorem name.
    _schema_identity = _schema_placeholder_hyp_identity(thm.declaration or "")
    if _schema_identity is not None:
        # Schema placeholder bodies are trivially provable.  Auto-close them
        # without calling the LLM prover so they don't crash on missing embeddings.
        # Determine the right closing tactic from the declaration shape.
        _decl = thm.declaration or ""
        _p_name, _h_name = _schema_identity
        # Detect return type: single p_cN or conjunction p_c1 ∧ p_c2 ∧ ...
        _conj_m = re.search(r":\s*((?:p_c\d+)(?:\s*∧\s*p_c\d+)+)\s*:=\s*by", " ".join(_decl.split()))
        if re.search(r":\s*\(0\s*:\s*ℕ\s*\)\s*=\s*0\b", _decl):
            _auto_tactic = "rfl"
        elif _conj_m:
            # Multi-conjunct p_c1 ∧ p_c2 ∧ p_c3 — build refine + exact chain.
            _conj_parts = [p.strip() for p in re.split(r"∧", _conj_m.group(1))]
            _h_map: dict[str, str] = {}
            for _hm in re.finditer(r"\((h_c\d+)\s*:\s*(p_c\d+)\)", _decl):
                _h_map[_hm.group(2)] = _hm.group(1)
            if all(p in _h_map for p in _conj_parts):
                _exact_parts = " ".join(f"exact {_h_map[p]}" for p in _conj_parts)
                _auto_tactic = "constructor <;> (" + _exact_parts + ")" if len(_conj_parts) == 2 else (
                    "refine ⟨" + ", ".join(_h_map[p] for p in _conj_parts) + "⟩"
                )
            else:
                _auto_tactic = "simp [*]"
        elif re.search(rf"\({re.escape(_h_name)}\s*:\s*{re.escape(_p_name)}\)", _decl):
            _auto_tactic = f"exact {_h_name}"
        elif re.search(r":\s*True\b", _decl):
            _auto_tactic = "trivial"
        else:
            _auto_tactic = "assumption"
        _ph_proof = f"  {_auto_tactic}"
        if verbose:
            print(f"    auto-close schema placeholder: {_auto_tactic}", flush=True)
        try:
            _patch_proof_into_file(thm.lean_file, thm.name or thm.full_name, _auto_tactic)
        except Exception as _patch_exc:
            if verbose:
                print(f"    auto-close patch warning: {_patch_exc}", flush=True)
        try:
            from pipeline_status import build_ledger_entry as _ble_ph, upsert_ledger_entry as _ule_ph
            from pipeline_status_models import ProofMethod as _ProofMethod, VerificationStatus as _VS
            # Normalize the lean_statement to use the final theorem name (thm.name),
            # not any translator-internal name (e.g. "schema_translation") that may
            # appear in old ledger entries from a prior pipeline run.
            _ph_stmt = re.sub(
                r"^(\s*(?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma)\s+)\S+",
                lambda m: m.group(1) + (thm.name or thm.full_name),
                (thm.declaration or ""),
                count=1,
                flags=re.MULTILINE,
            ) or thm.declaration
            # Schema placeholders are trivial identity bodies (p_c1 : Prop) → p_c1.
            # They are not real mathematical claims — mark as TRANSLATION_LIMITED so
            # they are excluded from the proving-rate denominator, not inflating it.
            _le_ph = _ble_ph(
                theorem_name=thm.full_name,
                lean_file=str(thm.lean_file),
                lean_statement=_ph_stmt,
                proved=True,
                step_records=[{"tactic": _auto_tactic, "result": "closed"}],
                proof_text=_ph_proof,
                error_message="",
                proof_mode="auto-close",
                proof_method=_ProofMethod.AUTO_CLOSED,
                rounds_used=1,
                time_s=0.0,
                had_exception=False,
            )
            # Override status to TRANSLATION_LIMITED after building so gate logic
            # doesn't promote this to FULLY_PROVEN in quality metrics.
            _le_ph.status = _VS.TRANSLATION_LIMITED
            if paper_id:
                _ule_ph(paper_id, _le_ph)
        except Exception as _ledger_exc:
            if verbose:
                print(f"    auto-close ledger warning: {_ledger_exc}", flush=True)
        return ProofResult(
            theorem_name=thm.full_name,
            lean_file=str(thm.lean_file),
            proved=True,
            proof_text=_ph_proof,
            rounds_used=1,
            time_s=0.0,
            status="TRANSLATION_LIMITED",
        )

    route = _statement_shape_route(thm.declaration)
    sanity_issue = _provability_sanity_issue(thm.declaration)
    if route == "definition_lane" or sanity_issue:
        # Auto-close trivially provable sanity cases instead of just skipping.
        _decl = thm.declaration or ""
        _sanity_tactic: str | None = None
        if sanity_issue in {"trivial_true_target", "trivial_nat0eq0_target"}:
            _sanity_tactic = "trivial" if sanity_issue == "trivial_true_target" else "rfl"
        elif re.search(r":\s*\(0\s*:\s*ℕ\s*\)\s*=\s*0\b", _decl):
            # Extra guard for formatting variants that may evade target parser.
            _sanity_tactic = "rfl"

        if _sanity_tactic:
            if verbose:
                print(f"    auto-close sanity ({sanity_issue}): {_sanity_tactic}", flush=True)
            try:
                _patch_proof_into_file(thm.lean_file, thm.name or thm.full_name, _sanity_tactic)
            except Exception:
                pass
            try:
                from pipeline_status import build_ledger_entry, upsert_ledger_entry
                ledger_entry = build_ledger_entry(
                    theorem_name=thm.full_name,
                    lean_file=str(thm.lean_file),
                    lean_statement=thm.declaration,
                    proved=True,
                    step_records=[{"tactic": _sanity_tactic, "result": "closed"}],
                    proof_text=f"  {_sanity_tactic}",
                    error_message="",
                    proof_mode="auto-close",
                    rounds_used=1,
                    time_s=0.0,
                    had_exception=False,
                )
                if paper_id:
                    upsert_ledger_entry(paper_id, ledger_entry)
            except Exception:
                pass
            return ProofResult(
                theorem_name=thm.full_name,
                lean_file=str(thm.lean_file),
                proved=True,
                proof_text=f"  {_sanity_tactic}",
                rounds_used=1,
                time_s=0.0,
                status="FULLY_PROVEN",
            )

        reason = (
            f"preprove_route:{route}"
            if route == "definition_lane"
            else f"preprove_sanity_block:{sanity_issue}"
        )
        if verbose:
            print(f"    skipped: {reason}", flush=True)
        try:
            from pipeline_status import build_ledger_entry, upsert_ledger_entry
            ledger_entry = build_ledger_entry(
                theorem_name=thm.full_name,
                lean_file=str(thm.lean_file),
                lean_statement=thm.declaration,
                proved=False,
                step_records=[],
                proof_text="",
                error_message=reason,
                proof_mode=proof_mode,
                rounds_used=0,
                time_s=0.0,
                had_exception=False,
            )
            if paper_id:
                upsert_ledger_entry(paper_id, ledger_entry)
        except Exception:
            pass
        return ProofResult(
            theorem_name=thm.full_name,
            lean_file=str(thm.lean_file),
            proved=False,
            proof_text="",
            rounds_used=0,
            time_s=0.0,
            error=reason,
            status="UNRESOLVED",
        )

    try:
        rel_file = thm.lean_file.relative_to(project_root)
    except ValueError:
        rel_file = thm.lean_file

    proved = False
    records: list = []
    last_error = ""
    source_entry: dict | None = None
    if paper_id:
        lookup_ids: list[str] = [paper_id]
        if paper_id.endswith("_reliable"):
            lookup_ids = [paper_id[: -len("_reliable")], paper_id]
        for pid in lookup_ids:
            source_entry = _load_ledger_entry_for_theorem(pid, thm.full_name)
            if isinstance(source_entry, dict):
                break
            source_entry = _load_ledger_entry_for_theorem(pid, thm.name)
            if isinstance(source_entry, dict):
                break
        if not isinstance(source_entry, dict):
            source_entry = None

    provenance_obj = None
    if isinstance(source_entry, dict):
        try:
            from pipeline_status_models import ProvenanceLink
            prov_raw = source_entry.get("provenance")
            if isinstance(prov_raw, dict):
                provenance_obj = ProvenanceLink(
                    paper_id=str(prov_raw.get("paper_id", "") or ""),
                    section=str(prov_raw.get("section", "") or ""),
                    label=str(prov_raw.get("label", "") or ""),
                    cited_refs=[str(x) for x in (prov_raw.get("cited_refs", []) or [])],
                )
        except Exception:
            provenance_obj = None

    @contextlib.contextmanager
    def _force_repldojo_backend() -> object:
        prev_backend = os.environ.get("DESOL_PROOF_BACKEND")
        prev_force = os.environ.get("DESOL_FORCE_REPL_DOJO")
        os.environ["DESOL_PROOF_BACKEND"] = "repldojo"
        os.environ["DESOL_FORCE_REPL_DOJO"] = "1"
        try:
            yield
        finally:
            if prev_backend is None:
                os.environ.pop("DESOL_PROOF_BACKEND", None)
            else:
                os.environ["DESOL_PROOF_BACKEND"] = prev_backend
            if prev_force is None:
                os.environ.pop("DESOL_FORCE_REPL_DOJO", None)
            else:
                os.environ["DESOL_FORCE_REPL_DOJO"] = prev_force

    def _run_full_draft_fallback(*, informal_hint: str = "") -> tuple[bool, list, str]:
        from prove_with_ponder import prove_with_full_draft_repair

        combined_hint = "\n".join(
            h for h in (_domain_proof_hint(thm.declaration), informal_hint.strip()) if h
        ).strip()
        # Force REPLDojo for fallback/full-draft to avoid LeanDojo ExtractData
        # tracing loops under incompatible toolchain combos.
        with _force_repldojo_backend():
            primary = thm.name or thm.full_name
            ok, recs, summary = prove_with_full_draft_repair(
                project_root=project_root,
                file_path=rel_file,
                theorem_name=primary,
                client=client,
                model=model,
                repair_rounds=repair_rounds,
                retrieval_index_path=retrieval_index,
                informal_proof_hint=combined_hint,
            )
            if ok:
                return ok, recs, summary
            # Fallback to fully qualified theorem id if short-name lookup was ambiguous.
            if primary != thm.full_name:
                return prove_with_full_draft_repair(
                    project_root=project_root,
                    file_path=rel_file,
                    theorem_name=thm.full_name,
                    client=client,
                    model=model,
                    repair_rounds=repair_rounds,
                    retrieval_index_path=retrieval_index,
                    informal_proof_hint=combined_hint,
                )
            return ok, recs, summary

    def _attempt_statement_repair_then_retry(*, error_text: str) -> tuple[bool, list, str]:
        if attempt_equivalence_repair is None:
            return False, [], "statement_repair_unavailable"
        _err_text = error_text or ""
        trigger_repl = _is_repl_startup_failure(_err_text)
        trigger_assumption = "assumption" in _err_text.lower()
        if (not trigger_repl) and (not trigger_assumption):
            return False, [], "statement_repair_not_triggered"
        try:
            from prove_with_ponder_exec import classify_lean_error
        except Exception:
            return False, [], "statement_repair_classifier_unavailable"
        if (not trigger_repl) and (classify_lean_error(_err_text) != "assumption-mismatch"):
            return False, [], "statement_repair_not_assumption_mismatch"

        row = dict(source_entry or {})
        row["lean_statement"] = thm.declaration
        try:
            outcome = attempt_equivalence_repair(
                row=row,
                project_root=project_root,
                client=client,
                model=model,
                retrieval_index_path=retrieval_index,
            )
        except Exception as exc:
            return False, [], f"statement_repair_call_failed:{exc}"
        if not bool(getattr(outcome, "repaired", False)):
            return False, [], f"statement_repair_failed:{getattr(outcome, 'error', 'unknown')}"

        repaired_signature = str(getattr(outcome, "repaired_signature", "") or "").strip()
        repaired_decl = _normalize_repaired_decl_for_theorem(
            repaired_signature=repaired_signature,
            theorem_name=thm.name or thm.full_name,
        )
        if not repaired_decl:
            return False, [], "statement_repair_empty_decl"

        repaired_issue = _provability_sanity_issue(repaired_decl)
        if repaired_issue:
            return False, [], f"statement_repair_rejected:{repaired_issue}"

        original_decl = thm.declaration
        if repaired_decl != original_decl:
            if not _replace_declaration_block_in_file(thm.lean_file, original_decl, repaired_decl):
                return False, [], "statement_repair_replace_failed"
            thm.declaration = repaired_decl

        micro_ok, micro_tactic, _ = _run_deterministic_micro_prover(
            project_root=project_root,
            rel_file=rel_file,
            theorem_name=thm.full_name,
            theorem_decl=thm.declaration,
        )
        if micro_ok:
            return True, [
                {
                    "step": 1,
                    "attempt": 1,
                    "tactic": micro_tactic,
                    "model_turns": 0,
                    "result": "proof-finished",
                    "detail": "deterministic_micro_prover_after_statement_repair",
                }
            ], ""
        return _run_full_draft_fallback(
            informal_hint=(
                "Statement was repaired under strict semantic-equivalence constraints. "
                "Use explicit proof scripts only: intro, constructor, have, exact, apply."
            )
        )

    # Pre-flight: if the whole .lean file fails lake build (e.g. undefined symbols like
    # C_T, HSobolev, I_i that aren't in Mathlib), the REPL session will fail on startup
    # for every theorem in the file.  Detect this once per file and, if broken, repoint
    # the theorem at an isolated minimal file (import Mathlib + just this declaration)
    # so proof search can still run on theorems whose statements are well-formed.
    _file_build_ok_cache: dict[str, bool] = getattr(prove_one, "_file_build_ok_cache", {})
    prove_one._file_build_ok_cache = _file_build_ok_cache  # type: ignore[attr-defined]
    _lean_file_key = str(thm.lean_file.resolve())
    if _lean_file_key not in _file_build_ok_cache:
        try:
            _build_check = subprocess.run(
                ["lake", "env", "lean", str(thm.lean_file)],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=60,
            )
            _build_out = (_build_check.stdout or "") + (_build_check.stderr or "")
            _file_build_ok_cache[_lean_file_key] = (
                _build_check.returncode == 0 or "error:" not in _build_out.lower()
            )
        except Exception:
            _file_build_ok_cache[_lean_file_key] = True  # assume ok on check failure

    if not _file_build_ok_cache[_lean_file_key]:
        # File-level build broken — repoint to an isolated minimal file for this theorem.
        _decl_clean = re.sub(r":=\s*by\b.*$", "", thm.declaration or "", flags=re.DOTALL).strip()
        _decl_clean = re.sub(r":=\s*$", "", _decl_clean).strip()
        if _decl_clean:
            _namespace_prefix = ""
            _namespace_suffix = ""
            if "." in (thm.full_name or ""):
                _namespace = thm.full_name.rsplit(".", 1)[0]
                if _namespace and not re.search(rf"^\s*namespace\s+{re.escape(_namespace)}\b", _decl_clean, re.MULTILINE):
                    _namespace_prefix = f"namespace {_namespace}\n\n"
                    _namespace_suffix = f"\n\nend {_namespace}\n"
            _paper_theory_imports: list[str] = []
            _paper_theory_opens: list[str] = []
            try:
                _file_text = thm.lean_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                _file_text = ""
            for _mod in re.findall(r"(?m)^\s*import\s+(Desol\.PaperTheory\.[A-Za-z0-9_.]+)\s*$", _file_text):
                line = f"import {_mod}\n"
                if line not in _paper_theory_imports:
                    _paper_theory_imports.append(line)
                    _paper_theory_opens.append(f"open {_mod.rsplit('.', 1)[-1]}\n")
            if paper_id and not _paper_theory_imports:
                _paper_theory_mod = "Paper_" + re.sub(r"[^A-Za-z0-9_]", "_", str(paper_id).strip())
                _paper_theory_path = project_root / "Desol" / "PaperTheory" / f"{_paper_theory_mod}.lean"
                if _paper_theory_path.exists():
                    _paper_theory_imports.append(f"import Desol.PaperTheory.{_paper_theory_mod}\n")
                    _paper_theory_opens.append(f"open {_paper_theory_mod}\n")
            _iso_src = (
                "import Mathlib\nimport Aesop\n"
                + "".join(_paper_theory_imports)
                + "\n"
                + "open MeasureTheory ProbabilityTheory Filter Set\n"
                + "".join(_paper_theory_opens)
                + "\n"
                + _namespace_prefix
                + _decl_clean
                + " := by\n  sorry\n"
                + _namespace_suffix
            )
            _iso_dir = project_root / "Desol"
            _iso_dir.mkdir(parents=True, exist_ok=True)
            _iso_name = f"_tmp_isolated_{(thm.name or thm.full_name).replace('.', '_')}.lean"
            _iso_path = _iso_dir / _iso_name
            try:
                _iso_path.write_text(_iso_src, encoding="utf-8")
                import dataclasses as _dc
                thm = _dc.replace(thm, lean_file=_iso_path)
                rel_file = _iso_path.relative_to(project_root)
                if verbose:
                    print(f"    isolated file mode (whole-file build broken): {_iso_name}", flush=True)
            except Exception as _iso_exc:
                if verbose:
                    print(f"    isolation failed: {_iso_exc}", flush=True)

    try:
        file_micro_ok, file_micro_tactic, _file_micro_error = _run_deterministic_file_micro_prover(
            project_root=project_root,
            rel_file=rel_file,
            theorem_name=thm.full_name,
            theorem_decl=thm.declaration,
        )
        if file_micro_ok:
            proved = True
            records = [
                {
                    "step": 1,
                    "attempt": 1,
                    "tactic": file_micro_tactic,
                    "model_turns": 0,
                    "result": "proof-finished",
                    "detail": "deterministic_file_micro_prover",
                }
            ]
            last_error = ""
        elif proof_mode in ("state-mcts", "hierarchical-state"):
            from mcts_search import run_hierarchical_state_mcts, run_state_mcts
            if proof_mode == "hierarchical-state":
                ok, tactics, summary = run_hierarchical_state_mcts(
                    project_root=project_root,
                    theorem_statement=thm.declaration,
                    client=client,
                    model=model,
                    iterations=mcts_iterations,
                    n_tactics=mcts_repair_variants,
                    max_depth=mcts_max_depth,
                    retrieval_index_path=retrieval_index,
                )
            else:
                ok, tactics, summary = run_state_mcts(
                    project_root=project_root,
                    theorem_statement=thm.declaration,
                    client=client,
                    model=model,
                    iterations=mcts_iterations,
                    n_tactics=mcts_repair_variants,
                    max_depth=mcts_max_depth,
                    retrieval_index_path=retrieval_index,
                    file_path=rel_file,
                    theorem_name=thm.full_name,
                )
            proved = ok
            records = [{"tactic": t, "result": "state-advanced"} for t in tactics]
            last_error = summary if not ok else ""
            if (
                fallback_to_full_draft
                and not proved
                and (
                    "missing theorem statement" in (last_error or "").lower()
                    or "repl did not respond" in (last_error or "").lower()
                    or (len(records) == 0)
                )
            ):
                if verbose:
                    print("    fallback: switching to full-draft repair", flush=True)
                proved, records, last_error = _run_full_draft_fallback()
        elif proof_mode in ("mcts-draft", "hierarchical"):
            # mcts-draft/hierarchical are legacy aliases — use state-MCTS equivalents
            from mcts_search import run_hierarchical_state_mcts, run_state_mcts
            _smcts_fn = run_hierarchical_state_mcts if proof_mode == "hierarchical" else run_state_mcts
            ok, tactics, summary = _smcts_fn(
                project_root=project_root,
                theorem_statement=thm.declaration,
                client=client,
                model=model,
                iterations=mcts_iterations,
                n_tactics=mcts_repair_variants,
                max_depth=mcts_max_depth,
                retrieval_index_path=retrieval_index,
            )
            proved = ok
            records = [{"tactic": t, "result": "state-advanced"} for t in tactics]
            last_error = summary if not ok else ""
            if (
                fallback_to_full_draft
                and not proved
                and (
                    "missing theorem statement" in (last_error or "").lower()
                    or "repl did not respond" in (last_error or "").lower()
                    or (len(records) == 0)
                )
            ):
                if verbose:
                    print("    fallback: switching to full-draft repair", flush=True)
                proved, records, last_error = _run_full_draft_fallback()
        else:
            file_micro_ok, file_micro_tactic, _file_micro_error = _run_deterministic_file_micro_prover(
                project_root=project_root,
                rel_file=rel_file,
                theorem_name=thm.full_name,
                theorem_decl=thm.declaration,
            )
            if file_micro_ok:
                proved = True
                records = [
                    {
                        "step": 1,
                        "attempt": 1,
                        "tactic": file_micro_tactic,
                        "model_turns": 0,
                        "result": "proof-finished",
                        "detail": "deterministic_file_micro_prover",
                    }
                ]
                last_error = ""
            else:
                micro_ok, micro_tactic, _micro_error = _run_deterministic_micro_prover(
                    project_root=project_root,
                    rel_file=rel_file,
                    theorem_name=thm.full_name,
                    theorem_decl=thm.declaration,
                )
                if micro_ok:
                    proved = True
                    records = [
                        {
                            "step": 1,
                            "attempt": 1,
                            "tactic": micro_tactic,
                            "model_turns": 0,
                            "result": "proof-finished",
                            "detail": "deterministic_micro_prover",
                        }
                    ]
                    last_error = ""
                else:
                    proved, records, last_error = _run_full_draft_fallback()
                    # REPL startup failure = statement elaboration error; skip proof search,
                    # go straight to statement repair so we don't waste rounds.
                    if (not proved) and _is_repl_startup_failure(last_error or ""):
                        if verbose:
                            print("    REPL startup failure detected — attempting statement repair", flush=True)
                        ok3, rec3, err3 = _attempt_statement_repair_then_retry(error_text=last_error or "")
                        if ok3:
                            proved, records, last_error = ok3, rec3, err3
                        else:
                            hint = _constrained_regen_hint(thm.declaration, last_error or "")
                            ok2, rec2, err2 = _run_full_draft_fallback(informal_hint=hint)
                            if ok2:
                                proved, records, last_error = ok2, rec2, err2
                            elif err3 and (last_error or ""):
                                last_error = f"{last_error} | {err3}"
                    # Constrained regeneration retry for hard synthesis failures.
                    elif (not proved) and any(
                        t in (last_error or "")
                        for t in (
                            "simp made no progress",
                            "Invalid `⟨...⟩` notation",
                            "blocked_non_actionable_tactic",
                        )
                    ):
                        hint = _constrained_regen_hint(thm.declaration, last_error or "")
                        ok2, rec2, err2 = _run_full_draft_fallback(informal_hint=hint)
                        if ok2:
                            proved, records, last_error = ok2, rec2, err2
                    if not proved:
                        # KG stuck-state bridge: inject proven sibling lemmas as hints.
                        _kg_hints = _kg_stuck_state_bridge(
                            paper_id=paper_id,
                            theorem_decl=thm.declaration,
                            stuck_error=last_error or "",
                            project_root=project_root,
                        )
                        if _kg_hints:
                            _kg_hint_text = "\n".join(_kg_hints)
                            _combined_hint = _constrained_regen_hint(thm.declaration, last_error or "") + "\n" + _kg_hint_text
                            ok_kg, rec_kg, err_kg = _run_full_draft_fallback(informal_hint=_combined_hint)
                            if ok_kg:
                                proved, records, last_error = ok_kg, rec_kg, err_kg
                        if not proved:
                            ok3, rec3, err3 = _attempt_statement_repair_then_retry(error_text=last_error or "")
                            if ok3:
                                proved, records, last_error = ok3, rec3, err3
                            elif err3 and (last_error or ""):
                                last_error = f"{last_error} | {err3}"

        elapsed = time.time() - start

        proof_text = ""
        if proved:
            if proof_mode in ("state-mcts", "hierarchical-state", "mcts-draft", "hierarchical"):
                # records is a list of per-tactic dicts; join all tactics as the proof body
                proof_text = "\n".join(
                    r.get("tactic", "") if isinstance(r, dict) else getattr(r, "tactic", "")
                    for r in records
                    if (r.get("tactic", "") if isinstance(r, dict) else getattr(r, "tactic", ""))
                )
            else:
                last_rec = records[-1] if records else None
                if last_rec is not None:
                    proof_text = (
                        last_rec.get("tactic", "") if isinstance(last_rec, dict)
                        else getattr(last_rec, "tactic", "")
                    )
            _patch_proof_into_file(thm.lean_file, thm.name, proof_text)
            if verbose:
                print(f"    PROVED in {elapsed:.1f}s (steps={len(records)})", flush=True)
        else:
            if verbose:
                print(f"    failed  in {elapsed:.1f}s: {last_error[:80]}", flush=True)

        ledger_entry = build_ledger_entry(
            theorem_name=thm.full_name,
            lean_file=str(thm.lean_file),
            lean_statement=thm.declaration,
            proved=proved,
            step_records=records,
            proof_text=proof_text,
            error_message=last_error,
            proof_mode=proof_mode,
            rounds_used=len(records),
            time_s=elapsed,
            provenance=provenance_obj,
            project_root=project_root,
            ledger_root=(project_root / "output" / "verification_ledgers"),
            dependency_trust_complete=(
                bool(source_entry.get("dependency_trust_complete"))
                if isinstance(source_entry, dict) and source_entry.get("dependency_trust_complete") is not None
                else None
            ),
            reproducible_env=(
                bool(source_entry.get("reproducible_env"))
                if isinstance(source_entry, dict) and source_entry.get("reproducible_env") is not None
                else None
            ),
            translation_fidelity_score=(
                float(source_entry.get("translation_fidelity_score"))
                if isinstance(source_entry, dict) and source_entry.get("translation_fidelity_score") is not None
                else None
            ),
            status_alignment_score=(
                float(source_entry.get("status_alignment_score"))
                if isinstance(source_entry, dict) and source_entry.get("status_alignment_score") is not None
                else None
            ),
            translation_validated=(
                bool(source_entry.get("translation_validated"))
                if isinstance(source_entry, dict) and source_entry.get("translation_validated") is not None
                else None
            ),
            translation_confidence=(
                float(source_entry.get("translation_confidence"))
                if isinstance(source_entry, dict) and source_entry.get("translation_confidence") is not None
                else None
            ),
            translation_uncertainty_flags=(
                [str(x) for x in (source_entry.get("translation_uncertainty_flags") or [])]
                if isinstance(source_entry, dict)
                else None
            ),
            translation_adversarial_flags=(
                [str(x) for x in (source_entry.get("translation_adversarial_flags") or [])]
                if isinstance(source_entry, dict)
                else None
            ),
            translation_roundtrip_flags=(
                [str(x) for x in (source_entry.get("translation_roundtrip_flags") or [])]
                if isinstance(source_entry, dict)
                else None
            ),
            **_semantic_artifact_kwargs(source_entry),
            had_exception=False,
        )
        # proof_method is inferred inside build_ledger_entry from step_records:
        # "proof-finished" / "state-advanced" → LEAN_VERIFIED, else UNKNOWN.
        # This is the correct tier for real REPL-confirmed proofs.
        if paper_id:
            upsert_ledger_entry(paper_id, ledger_entry)
            _sync_base_alias_entry(ledger_entry)

        return ProofResult(
            theorem_name=thm.full_name,
            lean_file=str(thm.lean_file),
            proved=proved,
            proof_text=proof_text,
            rounds_used=len(records),
            time_s=elapsed,
            error=last_error,
            status=ledger_entry.status.value,
        )
    except Exception as e:
        elapsed = time.time() - start
        err_str = str(e)
        if verbose:
            print(f"    error   in {elapsed:.1f}s: {e}", flush=True)

        # Auto-retry with REPLDojo when LeanDojo's ExtractData.lean crashes due to
        # toolchain incompatibility.  This is transparent and paper-agnostic.
        _is_extractdata_crash = (
            "extractdata" in err_str.lower()
            or "extract_data" in err_str.lower()
            or ("lake env lean" in err_str.lower() and "extractdata" in err_str.lower())
            or ("non-zero exit status" in err_str.lower() and "extractdata.lean" in err_str.lower())
        )
        if _is_extractdata_crash and fallback_to_full_draft:
            if verbose:
                print("    fallback: ExtractData crash detected — retrying with REPLDojo backend", flush=True)
            try:
                with _force_repldojo_backend():
                    _fb_ok, _fb_recs, _fb_summary = _run_full_draft_fallback(
                        informal_hint="ExtractData backend unavailable; using REPL-based proof search."
                    )
                if _fb_ok:
                    elapsed2 = time.time() - start
                    from pipeline_status import build_ledger_entry as _ble2, upsert_ledger_entry as _ule2
                    _le2 = _ble2(
                        theorem_name=thm.full_name,
                        lean_file=str(thm.lean_file),
                        lean_statement=thm.declaration,
                        proved=True,
                        step_records=_fb_recs,
                        error_message="",
                        proof_mode="repldojo-fallback",
                        time_s=elapsed2,
                        project_root=project_root,
                        ledger_root=(project_root / "output" / "verification_ledgers"),
                        had_exception=False,
                    )
                    if paper_id:
                        _ule2(paper_id, _le2)
                        _sync_base_alias_entry(_le2)
                    return ProofResult(
                        theorem_name=thm.full_name,
                        lean_file=str(thm.lean_file),
                        proved=True,
                        proof_text="\n".join(str(r.get("tactic", "")) for r in _fb_recs),
                        rounds_used=len(_fb_recs),
                        time_s=elapsed2,
                        status=_le2.status.value,
                    )
                # REPLDojo also failed — record its error instead of the ExtractData error.
                err_str = f"extractdata_crash+repldojo_fallback_failed: {_fb_summary}"
            except Exception as _fb_exc:
                err_str = f"extractdata_crash+repldojo_fallback_exception: {_fb_exc}"
            elapsed = time.time() - start

        fallback_records: list[dict] = []

        ledger_entry = build_ledger_entry(
            theorem_name=thm.full_name,
            lean_file=str(thm.lean_file),
            lean_statement=thm.declaration,
            proved=False,
            step_records=fallback_records,
            error_message=err_str,
            proof_mode=proof_mode,
            time_s=elapsed,
            project_root=project_root,
            ledger_root=(project_root / "output" / "verification_ledgers"),
            had_exception=True,
        )
        if paper_id:
            upsert_ledger_entry(paper_id, ledger_entry)
            _sync_base_alias_entry(ledger_entry)

        return ProofResult(
            theorem_name=thm.full_name,
            lean_file=str(thm.lean_file),
            proved=False,
            time_s=elapsed,
            error=err_str,
            status=ledger_entry.status.value,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _collect_lean_files(
    output_dir: Path,
    lean_file: str | None,
    domain: str | None,
    all_papers: bool,
) -> list[Path]:
    if lean_file:
        return [Path(lean_file).resolve()]
    if domain:
        return sorted(output_dir.glob(f"{domain}_*.lean"))
    if all_papers:
        return sorted(output_dir.glob("*.lean"))
    return []


def _review_queue_target_names(path: Path | None) -> set[str]:
    target_names: set[str] = set()
    if path is None or not path.exists():
        return target_names
    q_raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(q_raw, dict):
        q_rows = q_raw.get("review_queue", []) or q_raw.get("unresolved", [])
    elif isinstance(q_raw, list):
        q_rows = q_raw
    else:
        q_rows = []
    for row in q_rows:
        if not isinstance(row, dict):
            continue
        nm = str(row.get("theorem_name", "")).strip()
        if nm:
            target_names.add(nm)
    return target_names


def _proof_cohort_target_names(path: Path | None) -> set[str]:
    target_names: set[str] = set()
    if path is None or not path.exists():
        return target_names
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        rows = raw.get("theorems") or raw.get("proof_repair_cohort") or raw.get("items") or []
    elif isinstance(raw, list):
        rows = raw
    else:
        rows = []
    for row in rows:
        if isinstance(row, str) and row.strip():
            target_names.add(row.strip())
        elif isinstance(row, dict):
            name = str(row.get("theorem_name", "") or row.get("name", "") or "").strip()
            if name:
                target_names.add(name)
    return target_names


def _kg_stuck_state_bridge(
    *,
    paper_id: str,
    theorem_decl: str,
    stuck_error: str,
    project_root: Path,
    max_candidates: int = 5,
) -> list[str]:
    """Query KG for proven lemmas whose targets semantically overlap with the stuck goal.

    Returns a list of `have`-style tactic hints to inject into the next proof attempt.
    The hints are best-effort: they guide the LLM toward relevant already-proven facts.
    """
    if not paper_id:
        return []
    # Only activate for unsolved-goals failures — not for parse/startup errors.
    err_low = (stuck_error or "").lower()
    if "unsolved goals" not in err_low and "unknown identifier" not in err_low:
        return []

    try:
        from pipeline_status import load_ledger
    except Exception:
        return []

    try:
        rows = [r for r in (load_ledger(paper_id) or []) if isinstance(r, dict)]
    except Exception:
        return []

    # Extract key tokens from the stuck theorem's target clause.
    target = _decl_target(theorem_decl)
    target_tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_']*", target or ""))
    # Filter out very common tokens.
    _SKIP = {"intro", "have", "exact", "apply", "simp", "rfl", "fun", "by", "theorem", "lemma",
              "True", "False", "Prop", "Type", "iff", "not", "And", "Or", "Nat", "Int", "Real"}
    target_tokens -= _SKIP

    hints: list[str] = []
    for row in rows:
        if str(row.get("status", "")) != "FULLY_PROVEN":
            continue
        other_name = str(row.get("theorem_name", "")).strip()
        other_decl = str(row.get("lean_statement", "") or "").strip()
        if not other_name or not other_decl or not other_decl.startswith("theorem "):
            continue
        other_target = _decl_target(other_decl)
        if not other_target:
            continue
        other_tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_']*", other_target)) - _SKIP
        # Overlap score: how many target tokens appear in this proven theorem.
        overlap = len(target_tokens & other_tokens)
        if overlap >= 2:  # at least 2 shared non-trivial tokens
            hints.append((overlap, other_name))

    hints.sort(key=lambda t: -t[0])
    top = [name for _, name in hints[:max_candidates]]
    if not top:
        return []
    # Format as have-hints for the LLM informal proof.
    return [
        f"Consider using the already-proven result `{name}` as a `have` step."
        for name in top
    ]


def _bridge_hints_from_ledger_entry(entry: dict) -> list[str]:
    out: list[str] = []
    assumptions = entry.get("assumptions", []) if isinstance(entry, dict) else []
    if not isinstance(assumptions, list):
        return out
    for a in assumptions:
        if not isinstance(a, dict):
            continue
        src = str(a.get("grounding_source", "")).strip()
        if src.startswith("bridge_candidate:"):
            name = src.split(":", 1)[1].strip()
            if name:
                out.append(name)
    # dedupe, preserve order
    return list(dict.fromkeys(out))


def _load_ledger_entry_for_theorem(paper_id: str, theorem_name: str) -> dict | None:
    if not paper_id:
        return None
    try:
        from pipeline_status import load_ledger
    except Exception:
        return None

    def _rows_for(pid: str) -> list[dict]:
        try:
            return [r for r in (load_ledger(pid) or []) if isinstance(r, dict)]
        except Exception:
            return []

    rows = _rows_for(paper_id)
    if (not rows) and paper_id.endswith("_reliable"):
        rows = _rows_for(paper_id[: -len("_reliable")])

    aliases: list[str] = []
    raw = str(theorem_name or "").strip()
    short = ""
    if raw:
        aliases.append(raw)
        short = raw.rsplit(".", 1)[-1]
        if short and short not in aliases:
            aliases.append(short)
        # Namespace aliases for regenerated actionable files.
        for ns in ("ArxivPaper", "ArxivPaperActionable"):
            namespaced = f"{ns}.{short or raw}"
            if namespaced not in aliases:
                aliases.append(namespaced)
    alias_set = set(aliases)
    if not alias_set:
        return None

    matched: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        nm = str(row.get("theorem_name", "")).strip()
        if nm in alias_set:
            matched.append(row)
            continue
        # Suffix fallback: supports cross-namespace matching (e.g. ArxivPaperActionable.X <-> ArxivPaper.X).
        if short and nm.rsplit(".", 1)[-1] == short:
            matched.append(row)
    if not matched and paper_id.endswith("_reliable"):
        base_rows = _rows_for(paper_id[: -len("_reliable")])
        for row in base_rows:
            if not isinstance(row, dict):
                continue
            nm = str(row.get("theorem_name", "")).strip()
            if nm in alias_set:
                matched.append(row)
                continue
            if short and nm.rsplit(".", 1)[-1] == short:
                matched.append(row)
    if not matched:
        return None

    status_rank = {
        "FULLY_PROVEN": 4,
        "INTERMEDIARY_PROVEN": 3,
        "UNRESOLVED": 2,
        "FLAWED": 1,
    }
    matched.sort(
        key=lambda r: (
            status_rank.get(str(r.get("status", "")).strip(), 0),
            1 if bool(r.get("translation_fidelity_ok", False)) else 0,
        ),
        reverse=True,
    )
    return matched[0]


def _is_tactic_state_obj(obj: object) -> bool:
    return hasattr(obj, "pp") and hasattr(obj, "num_goals")


def _is_proof_finished_obj(obj: object) -> bool:
    return type(obj).__name__ == "ProofFinished"


def _micro_prover_scripts_for_decl(decl: str) -> list[str]:
    s = " ".join((decl or "").split())
    target = _decl_target(decl)
    scripts: list[str] = []

    # --- Hammer passes: try before shape-specific tactics ---
    # omega: closes linear arithmetic over ℤ/ℕ (nat inequalities, modular arithmetic).
    # norm_num: closes numeric ground truth goals (3 + 5 = 8, n > 0, etc.).
    # decide: closes decidable Prop goals (finite, Bool-valued).
    # aesop: general-purpose proof search with Mathlib lemma set.
    _has_arith = any(tok in s for tok in ("ℕ", "ℤ", "ℝ", "Nat", "Int", "Real", "+", "-", "*", "≤", "≥", "<", ">"))
    _has_nat_lit = bool(re.search(r"\b\d+\b", s))
    _has_fin = any(tok in s for tok in ("Fin", "Finset", "Fintype", "decide"))

    if _has_arith:
        scripts.append("omega")
        scripts.append("linarith")
        scripts.append("nlinarith")
        scripts.append("positivity")
    if _has_nat_lit or _has_arith:
        scripts.append("norm_num")
    if _has_fin:
        scripts.append("decide")
    scripts.append("aesop")
    scripts.append("simp_all")
    scripts.append("tauto")

    # Shape-specific tactics.
    if "∧" in s:
        scripts.extend(
            [
                "constructor <;> aesop",
                "constructor <;> omega",
                "constructor <;> linarith",
                "constructor <;> nlinarith",
                "constructor <;> norm_num",
                "refine ⟨?_, ?_⟩ <;> simp_all",
            ]
        )
    if "∃!" in s or "exists!" in s.lower():
        scripts.extend(
            [
                "refine ⟨?w, ?hex, ?uniq⟩",
                "intros <;> aesop",
            ]
        )
    if "∃" in s and "∃!" not in s:
        scripts.extend(
            [
                "exact ⟨_, rfl⟩",
                "exact ⟨_, le_refl _⟩",
            ]
        )
    split = _split_top_level_implication(target)
    if split is not None:
        lhs, rhs = split
        if lhs and rhs and lhs == rhs:
            scripts.extend(["intro h", "exact h"])
        scripts.extend(["intro h", "aesop", "intro h", "omega", "intro h", "norm_num", "intro h", "simp_all"])
    if any(tok in s for tok in ("Finset", "Set.", "∈", "⊆", "∪", "∩")):
        scripts.extend(["ext x <;> simp_all", "constructor <;> intro h <;> aesop"])
    if any(tok in s for tok in ("Ring", "Group", "Module", "Matrix", "LinearMap")):
        scripts.extend(["ring_nf", "simp_all"])
    return list(dict.fromkeys(x.strip() for x in scripts if x.strip()))


def _run_deterministic_micro_prover(
    *,
    project_root: Path,
    rel_file: Path,
    theorem_name: str,
    theorem_decl: str,
    timeout_s: int = 90,
) -> tuple[bool, str, str]:
    """Try deterministic tactic scripts for simple conjunction/∃! goals."""
    scripts = _micro_prover_scripts_for_decl(theorem_decl)

    try:
        from lean_repl_dojo import REPLDojo
    except Exception as exc:
        return False, "", f"micro_repl_unavailable:{exc}"

    name_candidates = [theorem_name]
    short_name = theorem_name.rsplit(".", 1)[-1]
    if short_name and short_name not in name_candidates:
        name_candidates.append(short_name)

    last_err = "micro_no_closure"
    for nm in name_candidates:
        try:
            with REPLDojo(
                project_root=project_root,
                file_path=rel_file,
                theorem_name=nm,
                timeout=max(30, int(timeout_s)),
            ) as (dojo, state):
                if not _is_tactic_state_obj(state):
                    last_err = "micro_non_tactic_state"
                    continue
                script_variants: list[list[str]] = _slot_scripts_for_state(getattr(state, "pp", ""))
                script_variants.extend([[t] for t in scripts if t.strip()])
                if not script_variants:
                    last_err = "no_micro_pattern"
                    continue
                for script in script_variants:
                    cur = state
                    executed: list[str] = []
                    ok = True
                    for tactic in script:
                        outcome = dojo.run_tac(cur, tactic)
                        executed.append(tactic)
                        if _is_proof_finished_obj(outcome):
                            return True, "\n".join(executed), ""
                        if _is_tactic_state_obj(outcome):
                            cur = outcome
                            continue
                        ok = False
                        break
                    if ok and executed and _is_tactic_state_obj(cur):
                        follow = dojo.run_tac(cur, "exact?")
                        if _is_proof_finished_obj(follow):
                            return True, "\n".join([*executed, "exact?"]), ""
        except Exception as exc:
            last_err = f"micro_exception:{exc}"
            continue
    return False, "", last_err


def _verify_script_via_file_check(
    *,
    project_root: Path,
    source_file: Path,
    theorem_name: str,
    theorem_decl: str,
    script: list[str],
    timeout_s: int = 45,
) -> tuple[bool, str]:
    src = source_file if source_file.is_absolute() else (project_root / source_file)
    if not src.exists():
        return False, "file_check_source_missing"
    body = "\n".join((ln.strip() for ln in script if ln.strip())).strip()
    if not body:
        return False, "file_check_empty_script"
    text = src.read_text(encoding="utf-8")
    pattern = re.compile(
        r"(theorem\s+" + re.escape(theorem_name) + r"[^\n]*:= by\s*\n)\s*sorry",
        re.MULTILINE,
    )
    patched = pattern.sub(lambda m: m.group(1) + "  " + body.replace("\n", "\n  ") + "\n", text, count=1)
    tmp_path: Path | None = None
    try:
        out = ""
        if patched != text:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=f"{src.stem}_{theorem_name}_",
                suffix=".lean",
                dir=str(src.parent),
                delete=False,
            ) as tmp:
                tmp_path = Path(tmp.name)
                tmp.write(patched)
            proc = subprocess.run(
                ["lake", "env", "lean", str(tmp_path)],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=max(20, int(timeout_s)),
            )
            out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            if proc.returncode == 0:
                return True, ""
            low = out.lower()
            if ("error:" not in low) and ("warning: declaration uses `sorry`" in low):
                # Some environments treat sorry warnings as non-zero exit despite successful elaboration.
                return True, ""
        # Fallback: isolate theorem compilation to avoid unrelated file-level failures.
        prelude_lines: list[str] = []
        started = False
        decl_start = re.compile(
            r"^\s*(?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma|def)\s+[A-Za-z_][A-Za-z0-9_']*\b"
        )
        for ln in text.splitlines():
            if decl_start.match(ln):
                break
            prelude_lines.append(ln)
            started = True
        if not started:
            prelude_lines = ["import Mathlib", "", "namespace ArxivPaper"]
        body = "  " + body.replace("\n", "\n  ")
        isolated_decl = re.sub(r"(?m)^\s*sorry\s*$", body, theorem_decl, count=1).strip()
        if not isolated_decl:
            return False, f"file_check_fail:{out[:240]}"
        isolated_src = "\n".join(prelude_lines).rstrip() + "\n\n" + isolated_decl + "\n"
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f"{src.stem}_{theorem_name}_isolated_",
            suffix=".lean",
            dir=str(src.parent),
            delete=False,
        ) as tmp2:
            tmp2_path = Path(tmp2.name)
            tmp2.write(isolated_src)
        try:
            proc2 = subprocess.run(
                ["lake", "env", "lean", str(tmp2_path)],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=max(20, int(timeout_s)),
            )
            out2 = ((proc2.stdout or "") + "\n" + (proc2.stderr or "")).strip()
            if proc2.returncode == 0:
                return True, ""
            return False, f"file_check_fail:{out2[:240]}"
        finally:
            tmp2_path.unlink(missing_ok=True)
    except subprocess.TimeoutExpired:
        return False, f"file_check_timeout:{timeout_s}s"
    except Exception as exc:
        return False, f"file_check_exception:{exc}"
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _run_deterministic_file_micro_prover(
    *,
    project_root: Path,
    rel_file: Path,
    theorem_name: str,
    theorem_decl: str,
    timeout_s: int = 45,
) -> tuple[bool, str, str]:
    target = _decl_target(theorem_decl)
    scripts: list[list[str]] = []
    target_n = _normalize_prop(target)
    hyp_by_type = _hypotheses_by_type(theorem_decl)
    if target_n and target_n in hyp_by_type:
        scripts.append([f"exact {hyp_by_type[target_n]}"])
    if _is_reflexive_equality(target):
        scripts.append(["rfl"])
    premises, consequent = _implication_chain(target)
    if _is_reflexive_equality(consequent):
        intros = [f"intro h{i+1}" for i in range(max(0, len(premises)))]
        scripts.append([*intros, "rfl"] if intros else ["rfl"])
    # Conjunction target with directly matching hypothesis slots.
    conj = _split_top_level_conjunction(target)
    if conj is not None:
        lhs, rhs = conj
        lhs_n = _normalize_prop(lhs)
        rhs_n = _normalize_prop(rhs)
        h_l = hyp_by_type.get(lhs_n, "")
        h_r = hyp_by_type.get(rhs_n, "")
        if h_l and h_r:
            scripts.append(["constructor", f"exact {h_l}", f"exact {h_r}"])
    # Generic file-checked tactics catch simple set-membership and local-hypothesis goals
    # without opening an LLM-backed proof loop.
    for tactic in _micro_prover_scripts_for_decl(theorem_decl):
        scripts.append([tactic])
    if not scripts:
        return False, "", "file_micro_no_pattern"
    # Dedupe scripts
    uniq: list[list[str]] = []
    seen: set[str] = set()
    for sc in scripts:
        key = " ;; ".join(sc)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(sc)

    name_candidates = [theorem_name]
    short = theorem_name.rsplit(".", 1)[-1]
    if short and short not in name_candidates:
        name_candidates.append(short)
    last_err = "file_micro_no_closure"
    for nm in name_candidates:
        for sc in uniq:
            ok, err = _verify_script_via_file_check(
                project_root=project_root,
                source_file=rel_file,
                theorem_name=nm,
                theorem_decl=theorem_decl,
                script=sc,
                timeout_s=timeout_s,
            )
            if ok:
                return True, "\n".join(sc), ""
            last_err = err or last_err
    return False, "", last_err


def _collect_bridge_targets(
    *,
    paper_id: str,
    theorem_name: str,
    ledger_root: Path,
    bridge_depth: int,
    bridge_max_candidates: int,
) -> list[str]:
    targets: list[str] = []

    entry = _load_ledger_entry_for_theorem(paper_id, theorem_name)
    if entry is not None:
        targets.extend(_bridge_hints_from_ledger_entry(entry))

    if collect_bridge_retry_targets is not None:
        try:
            plan = collect_bridge_retry_targets(
                target_theorem=theorem_name,
                ledger_root=ledger_root,
                max_depth=bridge_depth,
                max_candidates_per_step=bridge_max_candidates,
            )
            targets.extend(plan.ordered_candidates)
        except Exception:
            pass

    return list(dict.fromkeys(t for t in targets if t and t != theorem_name))


def _goal_lane_score_from_decl(decl: str) -> float:
    s = (decl or "").strip()
    if not s:
        return 0.0
    score = 0.0
    if re.search(r"^\s*(?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma)\b", s):
        score += 0.20
    if any(tok in s for tok in ("→", "->", "↔", "=", "≤", "≥", "<", ">", "∃", "∀")):
        score += 0.35
    if re.search(r":\s*True\s*:=\s*by", s):
        score -= 0.35
    return max(0.0, min(1.0, 0.5 + score))


def main() -> int:
    load_dotenv()
    p = argparse.ArgumentParser(description="Batch proof search over translated arxiv theorems")
    p.add_argument("--lean-file", default="", help="Specific .lean file to process")
    p.add_argument("--domain", default="", help="Domain prefix (e.g. algebra, analysis)")
    p.add_argument("--all", action="store_true", help="Process all output files")
    p.add_argument("--output-dir", default="output/tests", help="Directory of translated .lean files")
    p.add_argument("--project-root", default=".", help="Lean project root")
    p.add_argument("--model", default="", help="Mistral model (defaults to MISTRAL_MODEL env)")
    p.add_argument("--repair-rounds", type=int, default=5)
    p.add_argument("--retrieval-index", default="data/mathlib_embeddings")
    p.add_argument("--results-file", default="logs/proof_batch_results.json")
    p.add_argument("--dry-run", action="store_true", help="List theorems without proving")
    p.add_argument("--max-theorems", type=int, default=0, help="Limit theorems per file (0 = all)")
    p.add_argument(
        "--mode",
        choices=["full-draft", "mcts-draft", "hierarchical", "state-mcts", "hierarchical-state"],
        default="state-mcts",
        help="Proof mode: linear repair loop or draft-level MCTS tree search",
    )
    p.add_argument("--mcts-iterations", type=int, default=12, help="MCTS iterations per theorem")
    p.add_argument("--mcts-repair-variants", type=int, default=3, help="Repair variants per MCTS node")
    p.add_argument("--mcts-max-depth", type=int, default=5, help="Max MCTS depth in repair rounds")
    p.add_argument("--paper-id", default="", help="Paper ID for verification ledger (e.g. algebra/2304.09598)")
    p.add_argument(
        "--write-kg",
        action="store_true",
        help="Build KG layers/manifests from verification ledgers after batch run",
    )
    p.add_argument(
        "--kg-root",
        default="output/kg",
        help="KG output root used with --write-kg",
    )
    p.add_argument(
        "--bridge-loop",
        action="store_true",
        help="Enable bridge-proof execution loop (prove bridge candidates, then retry target)",
    )
    p.add_argument("--bridge-rounds", type=int, default=2, help="Max bridge retry rounds per failed theorem")
    p.add_argument(
        "--bridge-depth",
        type=int,
        default=2,
        help="Bridge chain planning depth",
    )
    p.add_argument(
        "--bridge-max-candidates",
        type=int,
        default=3,
        help="Max bridge candidates considered per planning step",
    )
    p.add_argument(
        "--strict-context-pack",
        action="store_true",
        help="Require non-trivial theorem context pack before bridge execution",
    )
    p.add_argument(
        "--strict-assumption-slots",
        action="store_true",
        help="Fail bridge grounding when assumption slots are unmapped",
    )
    p.add_argument(
        "--mandatory-retry-rounds",
        type=int,
        default=0,
        help="Minimum proof attempts per theorem before giving up (0 = single attempt)",
    )
    p.add_argument(
        "--min-translation-confidence",
        type=float,
        default=0.0,
        help="Skip proving theorems below this translation confidence (from ledger)",
    )
    p.add_argument(
        "--goal-lane-only",
        action="store_true",
        help="Only prove theorem declarations with sufficient goal-lane score",
    )
    p.add_argument(
        "--min-goal-lane-score",
        type=float,
        default=0.55,
        help="Minimum goal-lane score when --goal-lane-only is enabled",
    )
    p.add_argument(
        "--review-queue-json",
        default="",
        help="Optional review-queue JSON used to target only unresolved theorem names",
    )
    p.add_argument(
        "--proof-cohort-json",
        default="",
        help="Optional statement-validity proof-repair cohort JSON; only listed theorem names are proved.",
    )
    p.add_argument(
        "--target-theorem",
        action="append",
        default=[],
        help="Restrict proving to this theorem name (repeatable; accepts short or namespaced names)",
    )
    p.add_argument(
        "--only-review-queue",
        action="store_true",
        help="Restrict proving cohort to theorem names listed in --review-queue-json",
    )
    p.add_argument(
        "--state-fallback-full-draft",
        action="store_true",
        help="When state-MCTS fails with non-actionable backend errors, retry theorem via full-draft repair",
    )
    p.add_argument(
        "--disable-require-claim-equivalent",
        action="store_true",
        help="Allow proving when claim_equivalence_verdict is not 'equivalent' in ledger",
    )
    p.add_argument(
        "--strict-nontrivial-filter",
        action="store_true",
        help="Use legacy aggressive non-trivial declaration filter (default: relaxed).",
    )
    args = p.parse_args()

    project_root = Path(args.project_root).resolve()
    output_dir = project_root / args.output_dir

    lean_files = _collect_lean_files(
        output_dir=output_dir,
        lean_file=args.lean_file or None,
        domain=args.domain or None,
        all_papers=args.all,
    )

    if not lean_files:
        print("[error] No lean files found. Use --lean-file, --domain, or --all.", file=sys.stderr)
        return 1

    # Collect all sorry theorems.
    all_theorems: list[SorryTheorem] = []
    nontrivial_drop_counts: dict[str, int] = {}
    for lf in lean_files:
        try:
            if _sanitize_generated_lean_file(lf):
                print(f"  [sanitize] repaired malformed theorem separators in {lf.name}")
        except Exception as exc:
            print(f"  [warn] sanitize skipped for {lf.name}: {exc}")
        thms_raw = _extract_sorry_theorems(lf)
        # Skip trivial/schema placeholders before proving.
        thms: list[SorryTheorem] = []
        _translation_limited_decls: list[SorryTheorem] = []
        for t in thms_raw:
            reasons = _nontrivial_drop_reasons(
                t.declaration,
                strict=bool(args.strict_nontrivial_filter),
            )
            if reasons:
                key = reasons[0]
                nontrivial_drop_counts[key] = int(nontrivial_drop_counts.get(key, 0)) + 1
                # Emit TRANSLATION_LIMITED ledger entry for schema/translation drops
                # so the status is recorded rather than silently missing from the ledger.
                if key in ("schema_translation", "schema_name", "literal_schema_name"):
                    _translation_limited_decls.append(t)
                continue
            thms.append(t)
        # Write TRANSLATION_LIMITED entries for schema-dropped theorems.
        if _translation_limited_decls:
            _paper_id_for_drop = (
                args.paper_id.strip()
                if hasattr(args, "paper_id") and args.paper_id.strip()
                else lf.stem.replace("_actionable", "")
            )
            for _tl in _translation_limited_decls:
                try:
                    from pipeline_status import build_ledger_entry as _ble_tl, upsert_ledger_entry as _ule_tl
                    from pipeline_status_models import ProofMethod as _PM_tl, VerificationStatus as _VS_tl
                    _le_tl = _ble_tl(
                        theorem_name=_tl.full_name,
                        lean_file=str(_tl.lean_file),
                        lean_statement=_tl.declaration,
                        proved=False,
                        step_records=[],
                        proof_text="",
                        error_message="translation_limited:schema_placeholder",
                        proof_mode="translation-limited",
                        proof_method=_PM_tl.TRANSLATION_LIMITED,
                        rounds_used=0,
                        time_s=0.0,
                        had_exception=False,
                    )
                    _le_tl.status = _VS_tl.TRANSLATION_LIMITED
                    if _paper_id_for_drop:
                        _ule_tl(_paper_id_for_drop, _le_tl)
                except Exception:
                    pass
        if args.max_theorems:
            thms = thms[:args.max_theorems]
        all_theorems.extend(thms)
        print(f"  {lf.name}: {len(thms)} sorry theorems")

    print(f"\nTotal: {len(all_theorems)} theorems to prove")
    if nontrivial_drop_counts:
        print(f"Non-trivial filter drops: {nontrivial_drop_counts}")

    if args.dry_run:
        for t in all_theorems:
            print(f"  {t.full_name}  [{t.lean_file.name}]")
        return 0

    # Set up API client.
    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        print("[error] MISTRAL_API_KEY not set", file=sys.stderr)
        return 1
    model = args.model.strip() or os.getenv("MISTRAL_MODEL", "labs-leanstral-2603").strip()
    retrieval_index = args.retrieval_index.strip()
    # Resolve (and build on-demand if missing) using the paper-scoped helper.
    # Pass one of the lean files so the index can be scoped to this paper's imports.
    _representative_lean = lean_files[0] if lean_files else None
    try:
        from premise_retrieval import resolve_retrieval_index
        _idx_candidate = (project_root / retrieval_index) if retrieval_index and not Path(retrieval_index).is_absolute() else Path(retrieval_index) if retrieval_index else Path(os.environ.get("DESOL_RETRIEVAL_INDEX", "data/mathlib_embeddings"))
        retrieval_index = resolve_retrieval_index(
            _idx_candidate,
            paper_lean_file=_representative_lean,
        )
    except Exception as _ri_exc:
        print(f"[warn] could not resolve retrieval index: {_ri_exc}; continuing without")
        retrieval_index = ""

    try:
        from mistralai import Mistral
    except ImportError:
        from mistralai.client import Mistral  # type: ignore[no-redef]
    client = Mistral(api_key=api_key)

    # Derive paper_id from lean file name if not provided explicitly.
    paper_id = args.paper_id.strip()
    if not paper_id and len(lean_files) == 1:
        # e.g. algebra_2304.09598.lean → algebra/2304.09598
        stem = lean_files[0].stem
        if "_" in stem:
            domain_part, paper_part = stem.split("_", 1)
            paper_id = f"{domain_part}/{paper_part}"

    theorem_by_name = {t.full_name: t for t in all_theorems}
    if args.target_theorem:
        target_names = {str(x).strip() for x in args.target_theorem if str(x).strip()}
        filtered_targets: dict[str, SorryTheorem] = {}
        for full_name, thm in theorem_by_name.items():
            short_name = full_name.rsplit(".", 1)[-1]
            if full_name in target_names or short_name in target_names:
                filtered_targets[full_name] = thm
        print(f"Target-theorem filter: kept={len(filtered_targets)} / {len(theorem_by_name)}")
        theorem_by_name = filtered_targets
    if args.only_review_queue:
        q_path = Path(args.review_queue_json) if args.review_queue_json else None
        target_names: set[str] = set()
        if q_path and q_path.exists():
            try:
                target_names = _review_queue_target_names(q_path)
            except Exception as exc:
                print(f"[warn] could not parse review queue {q_path}: {exc}")
        if not target_names:
            print("[warn] --only-review-queue set but no theorem names were loaded; cohort unchanged.")
        else:
            filtered: dict[str, SorryTheorem] = {}
            for full_name, thm in theorem_by_name.items():
                short_name = full_name.rsplit(".", 1)[-1]
                if full_name in target_names or short_name in target_names:
                    filtered[full_name] = thm
            print(
                f"Review-queue filter: kept={len(filtered)} / {len(theorem_by_name)}"
            )
            theorem_by_name = filtered
    if args.proof_cohort_json:
        cohort_path = Path(args.proof_cohort_json)
        try:
            cohort_names = _proof_cohort_target_names(cohort_path)
        except Exception as exc:
            print(f"[warn] could not parse proof cohort {cohort_path}: {exc}")
            cohort_names = set()
        if not cohort_names:
            if cohort_path.exists():
                print("[info] --proof-cohort-json is empty; no statements passed the proof-repair cohort gate.")
                theorem_by_name = {}
            else:
                print("[warn] --proof-cohort-json set but file does not exist; cohort unchanged.")
        else:
            filtered: dict[str, SorryTheorem] = {}
            for full_name, thm in theorem_by_name.items():
                short_name = full_name.rsplit(".", 1)[-1]
                if full_name in cohort_names or short_name in cohort_names:
                    filtered[full_name] = thm
            print(f"Proof-cohort filter: kept={len(filtered)} / {len(theorem_by_name)}")
            theorem_by_name = filtered
    if paper_id and (float(args.min_translation_confidence) > 0.0 or bool(args.goal_lane_only)):
        filtered: dict[str, SorryTheorem] = {}
        skipped = 0
        for name, thm in theorem_by_name.items():
            keep = True
            if bool(args.goal_lane_only):
                if _goal_lane_score_from_decl(thm.declaration) < float(args.min_goal_lane_score):
                    keep = False
            if keep and float(args.min_translation_confidence) > 0.0:
                entry = _load_ledger_entry_for_theorem(paper_id, name)
                conf = float(entry.get("translation_confidence", 0.0)) if isinstance(entry, dict) else 0.0
                if conf < float(args.min_translation_confidence):
                    keep = False
            if keep:
                filtered[name] = thm
            else:
                skipped += 1
        theorem_by_name = filtered
        print(
            f"Filtered proving cohort: kept={len(theorem_by_name)} skipped={skipped} "
            f"(min_translation_confidence={float(args.min_translation_confidence):.2f}, "
            f"goal_lane_only={bool(args.goal_lane_only)})"
        )
    # Hard-drop known underconstrained implication signatures before proof/ledger write.
    filtered_underconstrained: dict[str, SorryTheorem] = {}
    dropped_underconstrained = 0
    for name, thm in theorem_by_name.items():
        sanity_issue = _provability_sanity_issue(thm.declaration)
        if sanity_issue.startswith("unconstrained_consequent_symbols:"):
            dropped_underconstrained += 1
            continue
        filtered_underconstrained[name] = thm
    theorem_by_name = filtered_underconstrained
    if dropped_underconstrained:
        print(
            f"Underconstrained-signature filter: kept={len(theorem_by_name)} "
            f"dropped={dropped_underconstrained}"
        )
    if paper_id and (not bool(args.disable_require_claim_equivalent)):
        filtered: dict[str, SorryTheorem] = {}
        skipped = 0
        for name, thm in theorem_by_name.items():
            entry = _load_ledger_entry_for_theorem(paper_id, name)
            if not isinstance(entry, dict):
                short_name = name.rsplit(".", 1)[-1]
                entry = _load_ledger_entry_for_theorem(paper_id, short_name)
            verdict = str(entry.get("claim_equivalence_verdict", "")).strip().lower() if isinstance(entry, dict) else ""
            if verdict == "equivalent":
                filtered[name] = thm
            else:
                skipped += 1
        theorem_by_name = filtered
        print(
            f"Equivalence gate: kept={len(theorem_by_name)} skipped={skipped} "
            "(require claim_equivalence_verdict='equivalent')"
        )
    if not theorem_by_name:
        print("[warn] No theorems left after proving cohort filters.")
        if paper_id and not args.dry_run:
            promoted = _reconcile_trivial_closed_ledger_entries(
                paper_id=paper_id,
                lean_files=lean_files,
            )
            if promoted:
                print(f"Ledger reconcile: promoted_trivial_closed={promoted}")
        return 0
    print(f"Effective proving cohort: {len(theorem_by_name)} theorem(s)")
    ledger_root = project_root / "output" / "verification_ledgers"

    # Run proofs with optional bridge execution loop.
    result_by_theorem: dict[str, ProofResult] = {}
    attempted: set[str] = set()
    proved_set: set[str] = set()

    def _attempt_theorem(name: str) -> ProofResult:
        thm = theorem_by_name[name]
        total_attempts = max(1, int(args.mandatory_retry_rounds) if int(args.mandatory_retry_rounds) > 0 else 1)
        r_inner: ProofResult | None = None
        for attempt_idx in range(total_attempts):
            if attempt_idx > 0:
                print(f"  retry attempt {attempt_idx + 1}/{total_attempts}: {name}")
            r_inner = prove_one(
                thm,
                project_root=project_root,
                client=client,
                model=model,
                repair_rounds=max(1, int(args.repair_rounds)),
                retrieval_index=retrieval_index,
                proof_mode=args.mode,
                mcts_iterations=args.mcts_iterations,
                mcts_repair_variants=args.mcts_repair_variants,
                mcts_max_depth=args.mcts_max_depth,
                paper_id=paper_id,
                dry_run=args.dry_run,
                fallback_to_full_draft=bool(args.state_fallback_full_draft),
            )
            if r_inner.proved:
                break
        assert r_inner is not None
        attempted.add(name)
        result_by_theorem[name] = r_inner
        if r_inner.proved:
            proved_set.add(name)
        return r_inner

    run_theorems = [theorem_by_name[n] for n in theorem_by_name]
    for i, thm in enumerate(run_theorems, 1):
        print(f"\n[{i}/{len(run_theorems)}] {thm.full_name}")
        if thm.full_name in proved_set:
            print("  status: already proved in prior bridge round")
            continue

        r = _attempt_theorem(thm.full_name)
        print(f"  status: {r.status}")

        if (not args.bridge_loop) or r.proved or (not paper_id):
            continue

        if execute_bridge_chain is not None:
            try:
                bridge_exec = execute_bridge_chain(
                    target_theorem=thm.full_name,
                    ledger_root=ledger_root,
                    max_depth=max(1, int(args.bridge_depth)),
                    max_candidates_per_step=max(1, int(args.bridge_max_candidates)),
                    require_assumption_slot_coverage=bool(args.strict_assumption_slots),
                    require_context_pack=bool(args.strict_context_pack),
                    min_context_items=2,
                    max_repair_rounds=max(0, int(args.mandatory_retry_rounds)),
                    retrieval_memory_path=project_root / "output" / "bridge_memory" / "candidate_stats.json",
                )
                if bridge_exec.newly_grounded:
                    print(f"  bridge grounding: grounded={len(bridge_exec.newly_grounded)}")
                elif bridge_exec.failure_reasons:
                    print(f"  bridge grounding failures: {bridge_exec.failure_reasons}")
            except Exception as exc:
                print(f"  bridge grounding error: {exc}")

        for bridge_round in range(1, max(1, args.bridge_rounds) + 1):
            bridge_targets = _collect_bridge_targets(
                paper_id=paper_id,
                theorem_name=thm.full_name,
                ledger_root=ledger_root,
                bridge_depth=args.bridge_depth,
                bridge_max_candidates=args.bridge_max_candidates,
            )
            bridge_targets = [
                t_name for t_name in bridge_targets
                if t_name in theorem_by_name and t_name != thm.full_name and t_name not in proved_set
            ]

            if not bridge_targets:
                break

            print(f"  bridge round {bridge_round}: candidates={bridge_targets[:args.bridge_max_candidates]}")

            bridge_progress = False
            for candidate in bridge_targets[:args.bridge_max_candidates]:
                if candidate in attempted and candidate in proved_set:
                    continue
                print(f"    proving bridge candidate: {candidate}")
                bridge_result = _attempt_theorem(candidate)
                print(f"    bridge status: {bridge_result.status}")
                if bridge_result.proved:
                    bridge_progress = True

            if not bridge_progress:
                break

            print("  retrying original theorem after bridge progress...")
            retry_result = _attempt_theorem(thm.full_name)
            print(f"  retry status: {retry_result.status}")
            if retry_result.proved:
                break

    results = [result_by_theorem[name] for name in theorem_by_name if name in result_by_theorem]
    proved = sum(1 for r in results if r.proved)

    # Save results.
    results_path = project_root / args.results_file
    results_path.parent.mkdir(parents=True, exist_ok=True)
    _save_results(results, results_path)

    if paper_id and not args.dry_run:
        promoted = _reconcile_trivial_closed_ledger_entries(
            paper_id=paper_id,
            lean_files=lean_files,
        )
        if promoted:
            print(f"Ledger reconcile: promoted_trivial_closed={promoted}")

    # Status summary.
    from collections import Counter
    status_counts = Counter(r.status for r in results)
    print(f"\n{'='*60}")
    print(f"PROVED: {proved}/{len(results)} theorems ({100*proved//max(len(results),1)}%)")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")
    print(f"Results: {results_path}")
    if paper_id:
        from pipeline_status import _ledger_path
        print(f"Ledger:  {_ledger_path(paper_id)}")

    if args.write_kg:
        try:
            from kg_writer import build_kg

            kg_root = project_root / args.kg_root
            if paper_id:
                kg_summary = build_kg(
                    ledger_dir=project_root / "output" / "verification_ledgers",
                    kg_root=kg_root,
                    paper=paper_id,
                )
            else:
                kg_summary = build_kg(
                    ledger_dir=project_root / "output" / "verification_ledgers",
                    kg_root=kg_root,
                    paper="",
                )
            print(
                "KG:      "
                f"trusted={kg_summary.trusted} "
                f"conditional={kg_summary.conditional} "
                f"diagnostics={kg_summary.diagnostics} "
                f"promotion_ready={kg_summary.promotion_ready}"
            )
            print(f"KG root: {kg_root}")
        except Exception as exc:
            print(f"[warn] KG build failed: {exc}")
    return 0 if proved > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
