from __future__ import annotations

from claim_equivalence_review import (
    apply_adjudications_to_entries,
    best_adjudications,
    build_review_queue,
    promotion_potential,
    remaining_blockers_if_equivalent,
    stable_review_id,
    summarize_review_queue,
    validate_adjudication,
)
from pipeline_status_models import VerificationStatus
import pytest


def _row() -> dict:
    return {
        "paper_id": "2604.21884",
        "theorem_name": "remark_20",
        "status": "INTERMEDIARY_PROVEN",
        "proved": True,
        "step_verdict": "VERIFIED",
        "proof_method": "lean_verified",
        "proof_text": "trivial",
        "lean_statement": "theorem remark_20 : True := by trivial",
        "translation_fidelity_score": 0.95,
        "status_alignment_score": 0.95,
        "reproducible_env": True,
        "provenance": {"paper_id": "2604.21884", "section": "2", "label": "remark_20"},
        "claim_equivalence_verdict": "unclear",
        "claim_equivalence_notes": ["insufficient_semantic_evidence"],
        "gate_failures": ["claim_equivalent", "independent_semantic_equivalence_evidence"],
        "validation_gates": {
            "lean_proof_closed": True,
            "step_verdict_verified": True,
            "translation_fidelity_ok": True,
            "status_alignment_ok": True,
            "claim_equivalent": False,
            "independent_semantic_equivalence_evidence": False,
        },
        "auto_reliable_core": {
            "theorem_name": "remark_20",
            "strict_gate_passed": False,
            "strict_gate_failures": ["claim_equivalent", "independent_semantic_equivalence_evidence"],
        },
        "semantic_equivalence_artifact": {
            "original_latex_theorem": "The admissible tuple satisfies the stated inequalities.",
            "normalized_natural_language_theorem": "The admissible tuple satisfies the inequalities.",
            "lean_statement": "theorem remark_20 : True := by trivial",
            "extracted_assumptions": [],
            "extracted_conclusion": "the admissible inequalities hold",
            "equivalence_verdict": "unclear",
            "reviewer_evaluator_evidence": [],
            "adversarial_checks": {},
            "independent_semantic_evidence": False,
        },
    }


def test_stable_review_id_is_deterministic() -> None:
    rid1 = stable_review_id(
        paper_id="p",
        theorem_name="t",
        original_latex_theorem="For all x, x = x.",
        lean_statement="theorem t (x : Nat) : x = x",
    )
    rid2 = stable_review_id(
        paper_id="p",
        theorem_name="t",
        original_latex_theorem="For   all x, x = x.",
        lean_statement="theorem t (x : Nat) : x = x",
    )
    assert rid1 == rid2


def test_review_queue_contains_standalone_claim_context() -> None:
    queue = build_review_queue(
        ledger_payload={"paper_id": "2604.21884", "entries": [_row()]},
        paper_id="2604.21884",
        source_ledger="ledger.json",
    )
    assert len(queue) == 1
    item = queue[0]
    assert item["theorem_name"] == "remark_20"
    assert item["source_ledger"] == "ledger.json"
    assert item["claim_equivalence_verdict"] == "unclear"
    assert "Lean statement" in item["review_prompt"]


def test_review_queue_prioritizes_high_potential_auto_core_over_axiom_backed_rows() -> None:
    high = _row()
    low = {
        **_row(),
        "theorem_name": "lemma_axiom_backed",
        "lean_statement": "theorem lemma_axiom_backed (h : PaperClaim) : PaperClaim := h",
        "proof_method": "domain_axiom",
        "axiom_debt": ["translation_repair_domain_assumption"],
        "translation_fidelity_score": 0.2,
        "status_alignment_score": 0.2,
        "auto_reliable_core": {},
        "gate_failures": [
            "claim_equivalent",
            "independent_semantic_equivalence_evidence",
            "translation_fidelity_ok",
            "status_alignment_ok",
            "no_paper_axiom_debt",
        ],
        "validation_gates": {
            "claim_equivalent": False,
            "independent_semantic_equivalence_evidence": False,
            "translation_fidelity_ok": False,
            "status_alignment_ok": False,
            "no_paper_axiom_debt": False,
        },
    }

    queue = build_review_queue(
        ledger_payload={"paper_id": "2604.21884", "entries": [low, high]},
        paper_id="2604.21884",
    )

    assert queue[0]["theorem_name"] == "remark_20"
    assert queue[0]["promotion_potential_tier"] == "high"
    assert promotion_potential(high)["promotion_potential_score"] > promotion_potential(low)["promotion_potential_score"]
    assert "no_paper_axiom_debt" in queue[1]["remaining_blockers_after_adjudication"]


def test_remaining_blockers_remove_semantic_gates_but_keep_nonsemantic_gates() -> None:
    row = {
        **_row(),
        "gate_failures": [
            "claim_equivalent",
            "independent_semantic_equivalence_evidence",
            "translation_fidelity_ok",
            "status_alignment_ok",
            "no_paper_axiom_debt",
        ],
        "validation_gates": {
            "claim_equivalent": False,
            "independent_semantic_equivalence_evidence": False,
            "translation_fidelity_ok": False,
            "status_alignment_ok": False,
            "no_paper_axiom_debt": False,
        },
    }

    blockers = remaining_blockers_if_equivalent(row)

    assert "claim_equivalent" not in blockers
    assert "independent_semantic_equivalence_evidence" not in blockers
    assert blockers == ["translation_fidelity_ok", "status_alignment_ok", "no_paper_axiom_debt"]


def test_equivalent_adjudication_supplies_independent_evidence_and_promotes() -> None:
    row = _row()
    review_id = stable_review_id(
        paper_id="2604.21884",
        theorem_name="remark_20",
        original_latex_theorem=row["semantic_equivalence_artifact"]["original_latex_theorem"],
        lean_statement=row["lean_statement"],
    )
    adjudication = validate_adjudication(
        {
            "review_id": review_id,
            "paper_id": "2604.21884",
            "theorem_name": "remark_20",
            "adjudicator": "human",
            "verdict": "equivalent",
            "confidence": 0.95,
            "rationale": "The assumptions and conclusion match.",
            "assumption_alignment": [],
            "conclusion_alignment": {"paper": "claim", "lean": "claim", "status": "matched"},
            "risk_flags": [],
            "required_ledger_markers": ["semantic_equivalence:verified", "claim_equivalent:human"],
        }
    )
    rows, summary = apply_adjudications_to_entries([row], [adjudication], paper_id="2604.21884")

    updated = rows[0]
    assert summary["claim_equivalence_applied_count"] == 1
    assert updated["claim_equivalence_verdict"] == "equivalent"
    assert updated["semantic_equivalence_artifact"]["independent_semantic_evidence"] is True
    assert updated["validation_gates"]["claim_equivalent"] is True
    assert updated["validation_gates"]["independent_semantic_equivalence_evidence"] is True
    assert updated["status"] == VerificationStatus.FULLY_PROVEN.value
    assert updated["promotion_gate_passed"] is True
    assert summary["approved_equivalent_count"] == 1
    assert summary["promoted_after_adjudication_count"] == 1


def test_non_equivalent_adjudication_keeps_promotion_blocked() -> None:
    row = _row()
    review_id = stable_review_id(
        paper_id="2604.21884",
        theorem_name="remark_20",
        original_latex_theorem=row["semantic_equivalence_artifact"]["original_latex_theorem"],
        lean_statement=row["lean_statement"],
    )
    rows, summary = apply_adjudications_to_entries(
        [row],
        [
            {
                "review_id": review_id,
                "paper_id": "2604.21884",
                "theorem_name": "remark_20",
                "adjudicator": "human",
                "verdict": "weaker",
                "confidence": 0.9,
                "rationale": "The Lean statement loses a condition.",
                "assumption_alignment": [{"paper": "h", "lean": "", "status": "missing"}],
                "conclusion_alignment": {"paper": "claim", "lean": "claim", "status": "matched"},
                "risk_flags": [],
            }
        ],
        paper_id="2604.21884",
    )
    assert summary["claim_equivalence_rejected_count"] == 1
    assert summary["rejected_weaker_count"] == 1
    assert rows[0]["claim_equivalence_verdict"] == "weaker"
    assert rows[0]["promotion_gate_passed"] is False


def test_equivalent_adjudication_counts_approved_but_still_blocked() -> None:
    row = {
        **_row(),
        "translation_fidelity_score": 0.4,
        "status_alignment_score": 0.4,
        "gate_failures": [
            "claim_equivalent",
            "independent_semantic_equivalence_evidence",
            "translation_fidelity_ok",
            "status_alignment_ok",
        ],
        "validation_gates": {
            "lean_proof_closed": True,
            "step_verdict_verified": True,
            "claim_equivalent": False,
            "independent_semantic_equivalence_evidence": False,
            "translation_fidelity_ok": False,
            "status_alignment_ok": False,
        },
    }
    review_id = stable_review_id(
        paper_id="2604.21884",
        theorem_name="remark_20",
        original_latex_theorem=row["semantic_equivalence_artifact"]["original_latex_theorem"],
        lean_statement=row["lean_statement"],
    )

    rows, summary = apply_adjudications_to_entries(
        [row],
        [
            {
                "review_id": review_id,
                "paper_id": "2604.21884",
                "theorem_name": "remark_20",
                "adjudicator": "human",
                "verdict": "equivalent",
                "confidence": 0.95,
                "rationale": "The claims match.",
                "assumption_alignment": [],
                "conclusion_alignment": {"paper": "claim", "lean": "claim", "status": "matched"},
                "risk_flags": [],
            }
        ],
        paper_id="2604.21884",
    )

    assert rows[0]["claim_equivalence_verdict"] == "equivalent"
    assert rows[0]["promotion_gate_passed"] is False
    assert summary["approved_equivalent_count"] == 1
    assert summary["promoted_after_adjudication_count"] == 0
    assert summary["still_blocked_after_adjudication_count"] == 1
    assert summary["still_blocked_reason_counts"]["translation_fidelity_ok"] == 1
    assert summary["still_blocked_reason_counts"]["status_alignment_ok"] == 1


def test_review_queue_summary_reports_top_targets_and_remaining_blockers() -> None:
    queue = build_review_queue(
        ledger_payload={"paper_id": "2604.21884", "entries": [_row()]},
        paper_id="2604.21884",
    )
    summary = summarize_review_queue(queue)

    assert summary["pending_review_count"] == 1
    assert summary["high_potential_review_count"] == 1
    assert summary["top_review_targets"][0]["theorem_name"] == "remark_20"


def test_human_adjudication_beats_lower_confidence_llm() -> None:
    rows = best_adjudications(
        [
            {
                "review_id": "r",
                "adjudicator": "llm:test",
                "verdict": "equivalent",
                "confidence": 0.99,
                "rationale": "LLM says yes.",
            },
            {
                "review_id": "r",
                "adjudicator": "human",
                "verdict": "unclear",
                "confidence": 0.6,
                "rationale": "Human requests more context.",
            },
        ]
    )
    assert rows["r"]["adjudicator"] == "human"
    assert rows["r"]["verdict"] == "unclear"


def _review_id(row: dict) -> str:
    return stable_review_id(
        paper_id=row["paper_id"],
        theorem_name=row["theorem_name"],
        original_latex_theorem=row["semantic_equivalence_artifact"]["original_latex_theorem"],
        lean_statement=row["lean_statement"],
    )


def _equivalent_adjudication(row: dict, *, adjudicator: str = "human", reviewer_type: str = "human") -> dict:
    return {
        "review_id": _review_id(row),
        "paper_id": row["paper_id"],
        "theorem_name": row["theorem_name"],
        "adjudicator": adjudicator,
        "reviewer_type": reviewer_type,
        "verdict": "equivalent",
        "confidence": 0.95,
        "rationale": "All assumptions and the conclusion match.",
        "assumption_alignment": [
            {"paper": item, "lean": item, "status": "matched"}
            for item in row["semantic_equivalence_artifact"].get("extracted_assumptions", [])
        ],
        "conclusion_alignment": {"paper": "claim", "lean": "claim", "status": "matched"},
        "risk_flags": [],
    }


def test_invalid_alignment_status_is_rejected() -> None:
    row = _row()
    bad = _equivalent_adjudication(row)
    bad["conclusion_alignment"] = {"status": "sort_of"}

    with pytest.raises(ValueError):
        validate_adjudication(bad)


def test_llm_only_equivalent_adjudication_cannot_promote() -> None:
    row = _row()
    adjudication = _equivalent_adjudication(row, adjudicator="llm:test", reviewer_type="llm")

    rows, summary = apply_adjudications_to_entries([row], [adjudication], paper_id="2604.21884")

    assert summary["claim_equivalence_applied_count"] == 1
    assert summary["claim_equivalence_llm_only_triage_count"] == 1
    assert summary["claim_equivalence_requires_human_count"] == 1
    assert rows[0]["promotion_gate_passed"] is False
    assert rows[0]["status"] != VerificationStatus.FULLY_PROVEN.value
    artifact = rows[0]["semantic_equivalence_artifact"]
    assert artifact["independent_semantic_evidence"] is False
    assert "requires_human_for_release" in artifact["adjudication"]["blockers"]


def test_incomplete_assumption_alignment_blocks_equivalence() -> None:
    row = _row()
    row["semantic_equivalence_artifact"]["extracted_assumptions"] = ["paper assumption one", "paper assumption two"]
    adjudication = _equivalent_adjudication(row)
    adjudication["assumption_alignment"] = [
        {"paper": "paper assumption one", "lean": "h1", "status": "matched"}
    ]

    rows, summary = apply_adjudications_to_entries([row], [adjudication], paper_id="2604.21884")

    assert summary["claim_equivalence_hard_blocked_count"] == 1
    assert rows[0]["promotion_gate_passed"] is False
    assert "incomplete_assumption_alignment" in rows[0]["semantic_equivalence_artifact"]["adjudication"]["blockers"]


def test_hard_risk_flags_block_equivalence_regardless_of_confidence() -> None:
    row = _row()
    adjudication = _equivalent_adjudication(row)
    adjudication["confidence"] = 1.0
    adjudication["risk_flags"] = ["semantic_mismatch"]

    rows, summary = apply_adjudications_to_entries([row], [adjudication], paper_id="2604.21884")

    assert summary["claim_equivalence_hard_blocked_count"] == 1
    assert rows[0]["promotion_gate_passed"] is False
    assert "semantic_mismatch" in rows[0]["semantic_equivalence_artifact"]["adjudication"]["blockers"]


def test_human_override_beats_conflicting_llm_triage() -> None:
    row = _row()
    llm = {
        **_equivalent_adjudication(row, adjudicator="llm:test", reviewer_type="llm"),
        "verdict": "not_equivalent",
        "rationale": "LLM triage is skeptical.",
    }
    human = _equivalent_adjudication(row)

    rows, summary = apply_adjudications_to_entries([row], [llm, human], paper_id="2604.21884")

    assert summary["claim_equivalence_conflict_count"] == 1
    assert summary["claim_equivalence_human_approved_count"] == 1
    assert rows[0]["status"] == VerificationStatus.FULLY_PROVEN.value
    assert rows[0]["promotion_gate_passed"] is True


def test_conflicting_release_eligible_reviews_block_promotion() -> None:
    row = _row()
    human_yes = _equivalent_adjudication(row)
    human_no = {
        **_equivalent_adjudication(row, adjudicator="human:second", reviewer_type="human"),
        "verdict": "weaker",
        "confidence": 0.91,
        "rationale": "Second reviewer says a condition is missing.",
    }

    rows, summary = apply_adjudications_to_entries([row], [human_yes, human_no], paper_id="2604.21884")

    assert summary["claim_equivalence_conflict_count"] == 1
    assert summary["claim_equivalence_hard_blocked_count"] == 1
    assert rows[0]["promotion_gate_passed"] is False
    assert "conflicting_reviews" in rows[0]["semantic_equivalence_artifact"]["adjudication"]["blockers"]


def test_hybrid_dual_review_can_promote_when_alignment_is_complete() -> None:
    row = _row()
    llm = _equivalent_adjudication(row, adjudicator="llm:test", reviewer_type="llm")
    hybrid = _equivalent_adjudication(row, adjudicator="hybrid:review-board", reviewer_type="hybrid")

    rows, summary = apply_adjudications_to_entries([row], [llm, hybrid], paper_id="2604.21884")

    assert summary["claim_equivalence_hybrid_approved_count"] == 1
    assert rows[0]["status"] == VerificationStatus.FULLY_PROVEN.value
    assert "claim_equivalent:hybrid" in rows[0]["claim_equivalence_notes"]
