#!/usr/bin/env python3
"""Statement-level validity and blocker classification helpers."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StatementValidity:
    theorem_name: str
    valid_for_proof: bool
    primary_blocker: str
    reasons: list[str]
    next_action: str
    lean_statement: str = ""
    debt_tier: str = "none"
    proof_value: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decl_target(lean_statement: str) -> str:
    stmt = re.sub(r":=\s*by\b.*$", "", (lean_statement or "").strip(), flags=re.DOTALL)
    depth = 0
    last_colon = -1
    for idx, ch in enumerate(stmt):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0:
            last_colon = idx
    return stmt[last_colon + 1 :].strip() if last_colon >= 0 else ""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def translation_limited_reason(lean_statement: str) -> str:
    stmt = _norm(lean_statement)
    target = decl_target(lean_statement)
    if not stmt:
        return "empty_translation"
    if re.search(r"\(p_c\d+\s*:\s*Prop\)\s*\(h_c\d+\s*:\s*p_c\d+\)", stmt):
        return "schema_placeholder_identity"
    if re.search(r"\(p_c\d+\s*:\s*Prop\)", stmt):
        return "prop_slot_placeholder"
    if re.search(r"\b(?:schema_translation|schema_fallback|literal_schema_translation)\b", stmt):
        return "schema_translation_placeholder"
    if target in {"True", "False", "Nonempty Unit", "Nonempty (Unit)"}:
        return "trivial_target"
    if re.fullmatch(r"\(?\s*0\s*:\s*ℕ\s*\)?\s*=\s*0", target):
        return "trivial_nat0eq0_target"
    return ""


def ill_typed_artifact_reason(lean_statement: str) -> str:
    stmt = _norm(lean_statement)
    patterns = [
        (r"\bC_T\s+HSobolev\b", "bare_function_space_application"),
        (r"\bB_N\^\{", "latex_superscript_artifact"),
        (r"\^\s*\([^)]*;", "semicolon_tuple_exponent_artifact"),
        (r"\bComplex\.abs\b", "non_mathlib_complex_abs_artifact"),
        (r"\(([A-Z][A-Za-z0-9_']*)\s*:\s*\1\)", "type_name_used_as_term"),
    ]
    for pattern, reason in patterns:
        if re.search(pattern, stmt):
            return reason
    return ""


def weakened_statement_reason(lean_statement: str) -> str:
    target = _norm(decl_target(lean_statement))
    if not target:
        return ""
    stmt = re.sub(r":=\s*by\b.*$", "", lean_statement or "", flags=re.DOTALL)
    for match in re.finditer(r"\(([hH][A-Za-z0-9_']*)\s*:\s*([^()]+?)\)", stmt, flags=re.DOTALL):
        name = match.group(1)
        typ = _norm(match.group(2))
        suspicious_name = re.search(r"(?:easy|claim|target|bound|conclusion|result|domain)", name, re.IGNORECASE)
        relation_target = any(tok in target for tok in ("=", "≤", "≥", "<", ">", "↔", "∧", "∨", "∃", "∀"))
        if typ == target and (suspicious_name or relation_target):
            return f"claim_copied_into_hypothesis:{name}"
    return ""


def semantic_hard_reason(row: dict[str, Any]) -> str:
    chunks = [str(row.get("error_message", "") or "").lower()]
    for key in (
        "translation_uncertainty_flags",
        "translation_adversarial_flags",
        "translation_roundtrip_flags",
        "gate_failures",
        "claim_equivalence_notes",
    ):
        val = row.get(key)
        if isinstance(val, list):
            chunks.extend(str(x).lower() for x in val)
    joined = " ".join(chunks)
    for marker in (
        "semantic_policy_hard_block",
        "semantic_policy_violation",
        "statement_repair_needed",
        "trivialization_hard_violation",
        "claim_shape_mismatch",
        "schema_coverage_missing",
        "verdict:wrong",
        "roundtrip_semantic_mismatch",
    ):
        if marker in joined:
            return marker
    return ""


def _axiom_debt(row: dict[str, Any]) -> list[str]:
    raw = row.get("axiom_debt", [])
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _target_atom(lean_statement: str) -> str:
    target = decl_target(lean_statement)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_']*", target or ""):
        return target
    return ""


def regenerated_statement_reason(row: dict[str, Any]) -> str:
    statement = str(row.get("lean_statement", "") or "")
    if _target_atom(statement).endswith("RegeneratedStatement"):
        return "regenerated_statement_atom"
    return ""


def atomized_claim_reason(row: dict[str, Any]) -> str:
    """Detect paper-claim atoms that hide the source theorem behind one Prop."""
    if str(row.get("ledger_role", "") or "") == "audited_core_replacement":
        return ""
    statement = str(row.get("lean_statement", "") or "")
    target = _target_atom(statement)
    if target.endswith("PaperClaim"):
        return "atomized_claim_target"
    if "PaperClaim" in statement:
        return "paper_claim_atom"
    return ""


def _debt_tier(row: dict[str, Any]) -> str:
    if str(row.get("ledger_role", "") or "") == "audited_core_replacement":
        return "audited_core_alignment"
    if bool(row.get("superseded_by_audited_core")):
        return "audited_core_alignment"
    if regenerated_statement_reason(row):
        return "regenerated_statement_tautology"
    debt = _axiom_debt(row)
    if any(item.startswith("paper_definition_stub:") for item in debt):
        return "definition_stub_grounding"
    if debt:
        return "local_paper_theory"
    return "none"


def _proof_value(row: dict[str, Any]) -> str:
    if str(row.get("ledger_role", "") or "") == "audited_core_replacement":
        return "audited_real_claim"
    if bool(row.get("superseded_by_audited_core")):
        return "superseded_generated_diagnostic"
    if regenerated_statement_reason(row):
        return "tautological_regenerated_statement"
    if any(item.startswith("paper_definition_stub:") for item in _axiom_debt(row)):
        return "definition_grounding"
    if _axiom_debt(row):
        return "local_paper_theory_obligation"
    return "none"


def _next_action(blocker: str) -> str:
    return {
        "bad_translation_artifact": "Repair the Lean statement/schema before proof search.",
        "ill_typed_statement": "Fix generated notation/types or add a typed PaperTheory declaration, then revalidate.",
        "paper_theory_debt": "Replace paper-local axiom debt with definitions or proved local lemmas.",
        "proof_search_failure": "Add targeted proof repair or deterministic tactics for this goal shape.",
        "claim_review_pending": "Apply human/hybrid claim-equivalence review only after other gates pass.",
        "release_ready": "No blocker detected for release promotion.",
        "translation_limited": "Re-translate from source; current statement is placeholder or non-substantive.",
    }.get(blocker, "Inspect theorem-specific gates and error message.")


def classify_statement(row: dict[str, Any]) -> StatementValidity:
    name = str(row.get("theorem_name", "") or row.get("name", "") or "")
    statement = str(row.get("lean_statement", "") or "")
    status = str(row.get("status", "") or "").strip()
    gates = [str(x) for x in (row.get("gate_failures") or [])]
    debt = _axiom_debt(row)
    debt_tier = _debt_tier(row)
    proof_value = _proof_value(row)
    reasons: list[str] = []

    def validity(
        valid_for_proof: bool,
        blocker: str,
        blocker_reasons: list[str],
    ) -> StatementValidity:
        return StatementValidity(
            name,
            valid_for_proof,
            blocker,
            blocker_reasons,
            _next_action(blocker),
            statement,
            debt_tier,
            proof_value,
        )

    atomized = atomized_claim_reason(row)
    if atomized:
        blocker = "bad_translation_artifact"
        return validity(False, blocker, [atomized])

    tl = translation_limited_reason(statement)
    if status == "TRANSLATION_LIMITED" or tl:
        reasons.append(tl or "translation_limited_status")
        blocker = "translation_limited"
        return validity(False, blocker, reasons)

    semantic = semantic_hard_reason(row)
    weakened = weakened_statement_reason(statement)
    if semantic or weakened or "translation_repair_domain_assumption" in debt:
        reasons.extend(x for x in (semantic, weakened) if x)
        if "translation_repair_domain_assumption" in debt:
            reasons.append("translation_repair_domain_assumption")
        blocker = "bad_translation_artifact"
        return validity(False, blocker, list(dict.fromkeys(reasons)))

    artifact = ill_typed_artifact_reason(statement)
    if artifact:
        reasons.append(artifact)
        blocker = "ill_typed_statement"
        return validity(False, blocker, reasons)

    statement_debt = [
        item for item in debt
        if not item.startswith("paper_definition_stub:")
    ]
    regenerated = regenerated_statement_reason(row)
    if regenerated:
        reasons.append(regenerated)
    if regenerated or statement_debt or "no_paper_axiom_debt" in gates:
        reasons.extend(statement_debt or ["no_paper_axiom_debt_gate"])
        blocker = "paper_theory_debt"
        return validity(False, blocker, list(dict.fromkeys(reasons)))

    if status == "FULLY_PROVEN" and not gates:
        blocker = "release_ready"
        return validity(True, blocker, [])

    if "claim_equivalent" in gates or "independent_semantic_equivalence_evidence" in gates:
        blocker = "claim_review_pending"
        return validity(False, blocker, gates)

    blocker = "proof_search_failure"
    reasons.extend(gates or [str(row.get("error_message", "") or "proof_not_closed")])
    return validity(True, blocker, list(dict.fromkeys([r for r in reasons if r])))


def summarize_validity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    items = [classify_statement(row) for row in rows if isinstance(row, dict)]
    counts: dict[str, int] = {}
    for item in items:
        counts[item.primary_blocker] = counts.get(item.primary_blocker, 0) + 1
    return {
        "total": len(items),
        "counts": dict(sorted(counts.items())),
        "items": [item.to_dict() for item in items],
    }


def proof_repair_cohort(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in (classify_statement(row) for row in rows if isinstance(row, dict)):
        if item.valid_for_proof and item.primary_blocker == "proof_search_failure":
            out.append(
                {
                    "theorem_name": item.theorem_name,
                    "primary_blocker": item.primary_blocker,
                    "reasons": item.reasons,
                }
            )
    return out


def _load_rows(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        rows = raw.get("entries") or raw.get("rows") or raw.get("results") or []
    else:
        rows = raw
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify generated Lean statement validity and proof-repair cohorts")
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--out-json", required=True, type=Path)
    parser.add_argument("--cohort-json", type=Path, default=None)
    args = parser.parse_args()

    rows = _load_rows(args.ledger)
    summary = summarize_validity(rows)
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.cohort_json:
        args.cohort_json.parent.mkdir(parents=True, exist_ok=True)
        args.cohort_json.write_text(
            json.dumps({"theorems": proof_repair_cohort(rows)}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    print(json.dumps({"out_json": str(args.out_json), "cohort_json": str(args.cohort_json or ""), "rows": len(rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
