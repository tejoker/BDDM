from __future__ import annotations

from build_statement_fidelity_queue import build_statement_fidelity_queue, fidelity_review_reasons


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "row_id": "r1",
        "arxiv_id": "2300.00001",
        "theorem_id": "thm:demo",
        "canonical_theorem_id": "demo",
        "statement_alignment_class": "partial",
        "alignment_confidence": 0.2,
        "alignment_tier": "alignment_candidate",
        "alignment_gold_eligible": False,
        "claim_equivalence_verdict": "unclear",
        "identity_status": "unknown",
        "source_span_quality": "extractor_native",
        "source_span": {"span_confidence": "exact_extractor"},
        "alignment_evidence": {"source_match": {"match_status": "matched"}},
        "source_latex": "A rich paper claim.",
        "lean_statement": "theorem demo : ∃ x : ℝ, x = x",
        "validation_gates": {},
        "gate_failures": [],
        "artifact_paths": {},
    }
    row.update(overrides)
    return row


def test_statement_fidelity_queue_captures_partial_placeholder_rows() -> None:
    row = _row()

    reasons = fidelity_review_reasons(row)
    queue, summary = build_statement_fidelity_queue([row])

    assert "statement_alignment:partial" in reasons
    assert "alignment_confidence_below_proof_threshold" in reasons
    assert "placeholder_or_trivial_lean_statement" in reasons
    assert queue[0]["suggested_action"] == "review_statement_equivalence_before_proof_search"
    assert queue[0]["source_latex"] == "A rich paper claim."
    assert summary["rows"] == 1
    assert summary["partial_rows"] == 1
    assert summary["placeholder_rows"] == 1


def test_statement_fidelity_queue_skips_alignment_gold_rows() -> None:
    queue, summary = build_statement_fidelity_queue(
        [
            _row(
                statement_alignment_class="exact",
                alignment_confidence=0.9,
                alignment_tier="alignment_gold",
                alignment_gold_eligible=True,
                claim_equivalence_verdict="equivalent",
                identity_status="same_statement",
                lean_statement="theorem demo (n : Nat) : n = n",
            )
        ]
    )

    assert queue == []
    assert summary["rows"] == 0
