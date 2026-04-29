from __future__ import annotations

from apply_statement_fidelity_reviews import apply_review, apply_reviews, source_span_sha256


def _row() -> dict[str, object]:
    return {
        "row_id": "r1",
        "alignment_tier": "alignment_candidate",
        "alignment_gold_eligible": False,
        "alignment_review_required": False,
        "source_span_quality": "extractor_native",
        "source_span": {"source_file": "paper.tex", "start_byte": 1, "end_byte": 10},
        "statement_alignment_class": "partial",
        "alignment_confidence": 0.2,
    }


def _review(row: dict[str, object], **overrides: object) -> dict[str, object]:
    review: dict[str, object] = {
        "schema_version": "reviewed_statement_alignment.v1",
        "row_id": row["row_id"],
        "source_span_sha256": source_span_sha256(row),
        "reviewed_statement_alignment_class": "exact",
        "reviewed_equivalence_verdict": "equivalent",
        "reviewed_alignment_confidence": 0.95,
        "reviewed_by": "human:test",
        "reviewed_at": "2026-04-27T14:00:00Z",
    }
    review.update(overrides)
    return review


def test_apply_review_promotes_exact_equivalent_review() -> None:
    row = _row()
    review = _review(row, notes="span and statement checked")

    applied, status = apply_review(row, review)

    assert status == "applied_promoted_alignment_gold"
    assert applied["alignment_tier"] == "alignment_gold"
    assert applied["alignment_gold_eligible"] is True
    assert applied["review_provenance"]["reviewed_by"] == "human:test"


def test_apply_review_rejects_source_span_mismatch() -> None:
    row = _row()
    review = _review(row, source_span_sha256="wrong")

    applied, status = apply_review(row, review)

    assert status == "source_span_mismatch"
    assert applied == row


def test_apply_reviews_counts_review_only_and_promotions() -> None:
    row = _row()
    weak_review = _review(
        row,
        reviewed_statement_alignment_class="partial",
        reviewed_equivalence_verdict="unclear",
        reviewed_alignment_confidence=0.4,
    )

    out, summary = apply_reviews([row], [weak_review])

    assert out[0]["reviewed_statement_alignment_class"] == "partial"
    assert summary["review_only"] == 1
    assert summary["promoted_alignment_gold"] == 0


def test_apply_review_rejects_invalid_schema() -> None:
    row = _row()
    review = _review(row, schema_version="wrong")

    applied, status = apply_review(row, review)

    assert status == "invalid_review:schema_version_invalid"
    assert applied == row


def test_apply_reviews_rejects_duplicate_conflicting_reviews() -> None:
    row = _row()
    exact = _review(row)
    partial = _review(
        row,
        reviewed_statement_alignment_class="partial",
        reviewed_equivalence_verdict="unclear",
        reviewed_alignment_confidence=0.4,
    )

    out, summary = apply_reviews([row], [exact, partial])

    assert out[0] == row
    assert summary["duplicate_review_conflicts"] == 1
    assert summary["promoted_alignment_gold"] == 0
