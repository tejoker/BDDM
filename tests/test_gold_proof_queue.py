from __future__ import annotations

from build_gold_proof_queue import build_gold_proof_queue, proof_candidate_blockers, proof_closure_blockers


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "row_id": "r1",
        "arxiv_id": "2300.00001",
        "theorem_id": "thm:demo",
        "canonical_theorem_id": "demo",
        "status": "UNRESOLVED",
        "lean_statement": "theorem demo (n : Nat) : n = n",
        "source_latex": "For all n, n = n.",
        "alignment_tier": "alignment_gold",
        "alignment_gold_eligible": True,
        "statement_alignment_class": "exact",
        "alignment_confidence": 0.95,
        "claim_equivalence_verdict": "equivalent",
        "independent_semantic_equivalence_evidence": True,
        "alignment_review_required": False,
        "source_span_quality": "extractor_native",
        "identity_status": "unknown",
        "mathlib_novelty_status": "unknown",
        "axiom_debt": [],
        "gate_failures": [],
    }
    row.update(overrides)
    return row


def test_gold_proof_queue_requires_gold_alignment_ready_rows() -> None:
    queue, summary = build_gold_proof_queue(
        [
            _row(),
            _row(row_id="r2", theorem_id="thm:low", alignment_gold_eligible=False, alignment_confidence=0.5),
        ]
    )

    assert len(queue) == 1
    assert queue[0]["row_id"] == "r1"
    assert queue[0]["proof_target"] == "lean_checked_exact_statement_or_audited_replacement_only"
    assert summary["candidate_rows"] == 1
    assert summary["rejection_reason_counts"]["alignment_not_exact_or_reviewed_exact"] == 1
    assert summary["attempted_rows"] == 0
    assert summary["newly_verified_rows"] == 0


def test_gold_proof_queue_rejects_partial_low_confidence_rows() -> None:
    row = _row(
        alignment_tier="alignment_candidate",
        alignment_gold_eligible=False,
        statement_alignment_class="partial",
        alignment_confidence=0.2,
    )

    blockers = proof_candidate_blockers(row)
    queue, summary = build_gold_proof_queue([row])

    assert "alignment_not_exact_or_reviewed_exact" in blockers
    assert "statement_alignment_not_exact" in blockers
    assert "alignment_confidence_below_proof_threshold" in blockers
    assert queue == []
    assert summary["candidate_rows"] == 0


def test_gold_proof_queue_accepts_reviewed_exact_rows() -> None:
    row = _row(
        alignment_tier="alignment_candidate",
        alignment_gold_eligible=False,
        statement_alignment_class="partial",
        alignment_confidence=0.2,
        reviewed_statement_alignment_class="exact",
        reviewed_equivalence_verdict="equivalent",
        reviewed_alignment_confidence=0.93,
        review_provenance={"reviewed_by": "human:test"},
    )

    queue, summary = build_gold_proof_queue([row])

    assert len(queue) == 1
    assert queue[0]["reviewed_statement_alignment_class"] == "exact"
    assert "status_not_fully_proven:UNRESOLVED" in queue[0]["proof_closure_blockers"]
    assert summary["candidate_rows"] == 1


def test_proof_closure_blockers_explain_unclosed_candidates() -> None:
    blockers = proof_closure_blockers(_row())

    assert "status_not_fully_proven:UNRESOLVED" in blockers
    assert "proof_method_not_lean_verified:" in blockers
    assert "proof_text_missing" in blockers


def test_gold_proof_queue_rejects_metric_game_rows() -> None:
    bad = _row(
        row_id="bad",
        lean_statement="theorem bad : PaperClaim p",
        axiom_debt=["paper_local_axiom"],
        status="AXIOM_BACKED",
        gate_failures=["paper_local_assumption"],
    )

    blockers = proof_candidate_blockers(bad)
    queue, summary = build_gold_proof_queue([bad])

    assert "paper_claim_artifact" in blockers
    assert "axiom_or_paper_theory_debt" in blockers
    assert "domain_or_paper_local_gate_failure" in blockers
    assert "status_not_queueable:AXIOM_BACKED" in blockers
    assert queue == []
    assert summary["candidate_rows"] == 0
    assert summary["rejection_reason_counts"]["paper_claim_artifact"] == 1
