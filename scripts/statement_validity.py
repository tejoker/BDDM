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
    novelty_status: str = "unknown"
    statement_fingerprint: str = ""
    canonical_theorem_id: str = ""
    novelty_evidence: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StatementFidelityGate:
    """Canonical pre-proof statement-fidelity decision.

    This is intentionally stricter than `StatementValidity`: validity asks
    whether a statement is worth repairing/proving; the fidelity gate decides
    whether proof search is allowed to spend budget on it in release mode.
    """

    theorem_name: str
    proof_eligible: bool
    statement_fidelity_verdict: str
    statement_fidelity_blockers: list[str]
    statement_fidelity_source: str
    lean_statement: str = ""
    validity_primary_blocker: str = ""
    next_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_EQUIVALENT_VERDICTS = {"equivalent", "exact"}
_RELEASE_REVIEW_SOURCES = {"human", "hybrid"}
_VALID_FIDELITY_VERDICTS = {"exact", "reviewed_exact", "repair_candidate", "blocked"}
_VALID_FIDELITY_SOURCES = {"automatic", "human", "hybrid", "llm_triage", "none"}


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


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        val = value.strip().lower()
        if val in {"true", "1", "yes", "y"}:
            return True
        if val in {"false", "0", "no", "n"}:
            return False
    return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def raw_latex_leak_reason(lean_statement: str) -> str:
    stmt = str(lean_statement or "")
    if not stmt.strip():
        return ""
    if re.search(r"\\(?:frac|sum|int|mathbb|mathbf|operatorname|begin|end|leq|geq|infty)\b", stmt):
        return "raw_latex_command_leak"
    if re.search(r"\$[^$]+\$", stmt):
        return "raw_latex_dollar_leak"
    if re.search(r"\^\{[^}]*[;,][^}]*\}", stmt):
        return "raw_latex_superscript_leak"
    return ""


def translation_limited_reason(lean_statement: str) -> str:
    stmt = _norm(lean_statement)
    target = decl_target(lean_statement)
    if not stmt:
        return "empty_translation"
    if "statement_repair_needed" in stmt.lower() or "schema_unavailable" in stmt.lower():
        return "statement_repair_needed_marker"
    if re.search(r"\(p_c\d+\s*:\s*Prop\)\s*\(h_c\d+\s*:\s*p_c\d+\)", stmt):
        return "schema_placeholder_identity"
    if re.search(r"\(p_c\d+\s*:\s*Prop\)", stmt):
        return "prop_slot_placeholder"
    if re.search(r"\b(?:schema_translation|schema_fallback|literal_schema_translation)\b", stmt):
        return "schema_translation_placeholder"
    if target in {"True", "Nonempty Unit", "Nonempty (Unit)"}:
        return "trivial_target"
    if re.fullmatch(r"\(?\s*0\s*:\s*ℕ\s*\)?\s*=\s*0", target):
        return "trivial_nat0eq0_target"
    if re.search(r"∃\s+([A-Za-z_][A-Za-z0-9_']*)\s*:\s*[^,]+,\s*\1\s*=\s*\1", stmt):
        return "trivial_exists_self_equality_target"
    return ""


def _source_claim_is_contradiction(row: dict[str, Any]) -> bool:
    source = " ".join(
        str(row.get(key, "") or "")
        for key in ("source_latex", "normalized_text", "original_latex_theorem")
    ).lower()
    if not source.strip():
        return False
    # Be deliberately strict here.  In mathematical prose, phrases like
    # "does not exist" or "no such object" usually formalize as a negated
    # existential, not as the proposition `False`.
    contradiction_patterns = (
        r"\bimplies?\s+(?:a\s+)?contradiction\b",
        r"\bleads?\s+to\s+(?:a\s+)?contradiction\b",
        r"\bderive(?:s|d)?\s+(?:a\s+)?contradiction\b",
        r"\bis\s+(?:a\s+)?contradiction\b",
        r"\bcontradictory\b",
        r"\babsurd(?:ity)?\b",
        r"\bfalse\s+proposition\b",
        r"\bempty\s+proposition\b",
        r"\\bot\b",
    )
    return any(re.search(pattern, source) for pattern in contradiction_patterns)


def false_target_reason(row: dict[str, Any]) -> str:
    """Return a blocker for generated `: False` statements unless source demands it."""
    statement = str(row.get("lean_statement", "") or "")
    target = _norm(decl_target(statement))
    if target != "False":
        return ""
    if _source_claim_is_contradiction(row):
        return ""
    return "false_target_without_source_contradiction"


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
    novelty = row.get("novelty_evidence") if isinstance(row.get("novelty_evidence"), dict) else {}
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
            str(row.get("novelty_status", "unknown") or "unknown"),
            str(row.get("statement_fingerprint", "") or ""),
            str(row.get("canonical_theorem_id", "") or ""),
            novelty,
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

    false_target = false_target_reason(row)
    if false_target:
        blocker = "bad_translation_artifact"
        return validity(False, blocker, [false_target])

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
    # Note: "no_paper_axiom_debt" in gates is NOT checked here. When all axiom_debt is
    # paper_definition_stub:* (transparent stubs), statement_debt is already empty, and
    # the gate's presence adds no new information. Checking it independently would block
    # rows that the fidelity gate is designed to allow through (inconsistent with the
    # statement_debt filter above and with build_gold_proof_queue's stub-debt allowance).
    if regenerated or statement_debt:
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


def _semantic_artifact(row: dict[str, Any]) -> dict[str, Any]:
    artifact = row.get("semantic_equivalence_artifact")
    return artifact if isinstance(artifact, dict) else {}


def _alignment_decision(row: dict[str, Any]) -> dict[str, Any]:
    artifact = _semantic_artifact(row)
    decision = artifact.get("alignment_decision")
    return decision if isinstance(decision, dict) else {}


def _gate_failures(row: dict[str, Any]) -> list[str]:
    return _as_list(row.get("gate_failures"))


def _validation_gate_false(row: dict[str, Any], *names: str) -> bool:
    gates = row.get("validation_gates")
    if not isinstance(gates, dict):
        return False
    for name in names:
        val = _as_bool(gates.get(name))
        if val is False:
            return True
    return False


def _field_or_artifact(row: dict[str, Any], field: str, artifact_field: str = "") -> str:
    val = str(row.get(field, "") or "").strip()
    if val:
        return val
    artifact = _semantic_artifact(row)
    if artifact_field:
        val = str(artifact.get(artifact_field, "") or "").strip()
        if val:
            return val
    return ""


def _claim_equivalence_verdict(row: dict[str, Any]) -> str:
    return _field_or_artifact(row, "claim_equivalence_verdict", "equivalence_verdict").lower()


def _statement_alignment_class(row: dict[str, Any]) -> str:
    decision = _alignment_decision(row)
    for source, key in (
        (row, "statement_alignment_class"),
        (row, "alignment_class"),
        (_semantic_artifact(row), "alignment_class"),
        (decision, "alignment_class"),
    ):
        val = str(source.get(key, "") or "").strip().lower()
        if val:
            return val
    return "unknown"


def _alignment_confidence(row: dict[str, Any]) -> float:
    decision = _alignment_decision(row)
    for source, key in (
        (row, "alignment_confidence"),
        (row, "statement_alignment_confidence"),
        (decision, "confidence"),
    ):
        val = _float_or_none(source.get(key))
        if val is not None:
            return val
    return 0.0


def _review_adjudication(row: dict[str, Any]) -> dict[str, Any]:
    artifact = _semantic_artifact(row)
    adj = artifact.get("adjudication")
    return adj if isinstance(adj, dict) else {}


def _review_source(row: dict[str, Any]) -> str:
    adj = _review_adjudication(row)
    provenance = row.get("review_provenance") if isinstance(row.get("review_provenance"), dict) else {}
    candidates = [
        row.get("statement_fidelity_source"),
        row.get("reviewer_type"),
        row.get("review_source"),
        row.get("adjudicator"),
        adj.get("reviewer_type"),
        adj.get("adjudicator"),
        provenance.get("reviewer_type"),
        provenance.get("reviewed_by"),
    ]
    joined = " ".join(str(x).lower() for x in candidates if str(x or "").strip())
    if "human" in joined:
        return "human"
    if "hybrid" in joined:
        return "hybrid"
    if "llm" in joined or "model" in joined:
        return "llm_triage"
    if provenance.get("reviewed_by"):
        return "human"
    return "none"


def _reviewed_exact(row: dict[str, Any]) -> tuple[bool, str, list[str]]:
    blockers: list[str] = []
    source = _review_source(row)
    adj = _review_adjudication(row)
    alignment = str(row.get("reviewed_statement_alignment_class", "") or row.get("reviewed_alignment_class", "") or "").strip().lower()
    if not alignment:
        alignment = str(adj.get("alignment_class", "") or "").strip().lower()
    verdict = str(
        row.get("reviewed_equivalence_verdict", "")
        or row.get("reviewed_claim_equivalence_verdict", "")
        or adj.get("verdict", "")
        or ""
    ).strip().lower()
    confidence = _float_or_none(row.get("reviewed_alignment_confidence"))
    if confidence is None:
        confidence = _float_or_none(adj.get("confidence"))
    if confidence is None:
        confidence = 0.0

    if alignment != "exact":
        blockers.append(f"reviewed_alignment_not_exact:{alignment or 'missing'}")
    if verdict not in _EQUIVALENT_VERDICTS:
        blockers.append(f"reviewed_equivalence_not_equivalent:{verdict or 'missing'}")
    if confidence < 0.75:
        blockers.append("reviewed_alignment_confidence_below_0_75")
    if source not in _RELEASE_REVIEW_SOURCES:
        blockers.append(
            "llm_review_not_release_eligible"
            if source == "llm_triage"
            else "release_grade_review_missing"
        )
    adj_blockers = _as_list(adj.get("blockers"))
    if adj_blockers:
        blockers.extend(f"review_blocker:{b}" for b in adj_blockers)
    if adj and _as_bool(adj.get("release_eligible")) is False:
        blockers.append("review_not_release_eligible")
    return (not blockers, source, list(dict.fromkeys(blockers)))


def _automatic_exact(row: dict[str, Any]) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    alignment = _statement_alignment_class(row)
    verdict = _claim_equivalence_verdict(row)
    confidence = _alignment_confidence(row)
    artifact = _semantic_artifact(row)
    gates = row.get("validation_gates") if isinstance(row.get("validation_gates"), dict) else {}
    independent = (
        _as_bool(row.get("independent_semantic_equivalence_evidence"))
        or _as_bool(gates.get("independent_semantic_equivalence_evidence"))
        or _as_bool(artifact.get("independent_semantic_evidence"))
    )
    if alignment != "exact":
        blockers.append(f"statement_alignment_not_exact:{alignment or 'missing'}")
    if verdict not in _EQUIVALENT_VERDICTS:
        blockers.append(f"claim_equivalence_not_equivalent:{verdict or 'missing'}")
    if confidence and confidence < 0.75:
        blockers.append("alignment_confidence_below_0_75")
    if independent is not True and not bool(row.get("alignment_gold_eligible")):
        blockers.append("independent_semantic_equivalence_evidence_missing")
    for failure in _gate_failures(row):
        if failure in {
            "claim_equivalent",
            "independent_semantic_equivalence_evidence",
            "translation_fidelity_ok",
            "translation_acceptance_gate",
            "semantic_adversarial_checks",
        }:
            blockers.append(f"validation_gate_failure:{failure}")
    return (not blockers, list(dict.fromkeys(blockers)))


def _elaboration_blockers(row: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if _validation_gate_false(row, "lean_elaboration", "statement_elaborates", "statement_elaboration_ok"):
        blockers.append("lean_elaboration_failed")
    for failure in _gate_failures(row):
        if "elaboration" in failure or "statement_elaborates" in failure:
            blockers.append(f"validation_gate_failure:{failure}")
    err = str(row.get("error_message", "") or row.get("error", "") or "").lower()
    if "validation_gate_elaboration_failed" in err or "statement does not elaborate" in err:
        blockers.append("lean_elaboration_failed")
    return list(dict.fromkeys(blockers))


def statement_fidelity_gate(row: dict[str, Any]) -> StatementFidelityGate:
    """Return the release-mode pre-proof statement-fidelity gate decision."""
    validity = classify_statement(row)
    blockers: list[str] = []
    source = "none"

    raw_latex = raw_latex_leak_reason(validity.lean_statement)
    if raw_latex:
        blockers.append(raw_latex)
    if not decl_target(validity.lean_statement):
        blockers.append("bad_theorem_shape:missing_target")
    blockers.extend(_elaboration_blockers(row))
    reviewed_ok, review_source, review_blockers = _reviewed_exact(row)

    if not validity.valid_for_proof:
        if validity.primary_blocker == "claim_review_pending" and reviewed_ok and not blockers:
            return StatementFidelityGate(
                theorem_name=validity.theorem_name,
                proof_eligible=True,
                statement_fidelity_verdict="reviewed_exact",
                statement_fidelity_blockers=[],
                statement_fidelity_source=review_source if review_source in _RELEASE_REVIEW_SOURCES else "hybrid",
                lean_statement=validity.lean_statement,
                validity_primary_blocker=validity.primary_blocker,
                next_action="Proceed to proof search.",
            )
        blockers.append(f"statement_validity:{validity.primary_blocker}")
        blockers.extend(f"statement_validity_reason:{r}" for r in validity.reasons)
        repairable = validity.primary_blocker in {
            "translation_limited",
            "bad_translation_artifact",
            "ill_typed_statement",
        } or bool(raw_latex)
        verdict = "repair_candidate" if repairable else "blocked"
        return StatementFidelityGate(
            theorem_name=validity.theorem_name,
            proof_eligible=False,
            statement_fidelity_verdict=verdict,
            statement_fidelity_blockers=list(dict.fromkeys(blockers)),
            statement_fidelity_source=source,
            lean_statement=validity.lean_statement,
            validity_primary_blocker=validity.primary_blocker,
            next_action=validity.next_action,
        )

    if blockers:
        return StatementFidelityGate(
            theorem_name=validity.theorem_name,
            proof_eligible=False,
            statement_fidelity_verdict="repair_candidate",
            statement_fidelity_blockers=list(dict.fromkeys(blockers)),
            statement_fidelity_source=source,
            lean_statement=validity.lean_statement,
            validity_primary_blocker=validity.primary_blocker,
            next_action="Repair statement/elaboration, then re-run fidelity validation before proof search.",
        )

    if reviewed_ok:
        return StatementFidelityGate(
            theorem_name=validity.theorem_name,
            proof_eligible=True,
            statement_fidelity_verdict="reviewed_exact",
            statement_fidelity_blockers=[],
            statement_fidelity_source=review_source if review_source in _RELEASE_REVIEW_SOURCES else "hybrid",
            lean_statement=validity.lean_statement,
            validity_primary_blocker=validity.primary_blocker,
            next_action="Proceed to proof search.",
        )

    auto_ok, auto_blockers = _automatic_exact(row)
    if auto_ok:
        return StatementFidelityGate(
            theorem_name=validity.theorem_name,
            proof_eligible=True,
            statement_fidelity_verdict="exact",
            statement_fidelity_blockers=[],
            statement_fidelity_source="automatic",
            lean_statement=validity.lean_statement,
            validity_primary_blocker=validity.primary_blocker,
            next_action="Proceed to proof search.",
        )

    blockers.extend(auto_blockers)
    blockers.extend(review_blockers)
    source = review_source if review_source in _VALID_FIDELITY_SOURCES else "none"
    if source == "llm_triage":
        blockers.append("llm_triage_cannot_enable_proof_eligibility")
    verdict = "blocked"
    if validity.primary_blocker in {"proof_search_failure", "claim_review_pending"}:
        verdict = "blocked"
    if verdict not in _VALID_FIDELITY_VERDICTS:
        verdict = "blocked"
    return StatementFidelityGate(
        theorem_name=validity.theorem_name,
        proof_eligible=False,
        statement_fidelity_verdict=verdict,
        statement_fidelity_blockers=list(dict.fromkeys(blockers)),
        statement_fidelity_source=source,
        lean_statement=validity.lean_statement,
        validity_primary_blocker=validity.primary_blocker,
        next_action=(
            "Send to statement-fidelity review queue; proof search is blocked until "
            "automatic exact evidence or release-grade human/hybrid equivalence review exists."
        ),
    )


def annotate_statement_fidelity(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    gate = statement_fidelity_gate(out)
    out.update(
        {
            "proof_eligible": gate.proof_eligible,
            "statement_fidelity_verdict": gate.statement_fidelity_verdict,
            "statement_fidelity_blockers": gate.statement_fidelity_blockers,
            "statement_fidelity_source": gate.statement_fidelity_source,
            "statement_fidelity_next_action": gate.next_action,
        }
    )
    return out


def annotate_statement_fidelity_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [annotate_statement_fidelity(row) for row in rows if isinstance(row, dict)]


def summarize_statement_fidelity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    items = [statement_fidelity_gate(row) for row in rows if isinstance(row, dict)]
    verdict_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    blocker_counts: dict[str, int] = {}
    for item in items:
        verdict_counts[item.statement_fidelity_verdict] = verdict_counts.get(item.statement_fidelity_verdict, 0) + 1
        source_counts[item.statement_fidelity_source] = source_counts.get(item.statement_fidelity_source, 0) + 1
        for blocker in item.statement_fidelity_blockers:
            blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
    proof_eligible = [item for item in items if item.proof_eligible]
    repair_candidates = [item for item in items if item.statement_fidelity_verdict == "repair_candidate"]
    review_needed = [
        item for item in items
        if (not item.proof_eligible) and item.statement_fidelity_verdict == "blocked"
    ]
    return {
        "schema_version": "statement_fidelity_gate.v1",
        "total_extracted_statements": len(items),
        "proof_eligible": len(proof_eligible),
        "blocked_before_proof": len(items) - len(proof_eligible),
        "repair_candidates": len(repair_candidates),
        "review_needed": len(review_needed),
        "verdict_counts": dict(sorted(verdict_counts.items())),
        "source_counts": dict(sorted(source_counts.items())),
        "blocker_counts": dict(sorted(blocker_counts.items())),
        "items": [item.to_dict() for item in items],
    }


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
