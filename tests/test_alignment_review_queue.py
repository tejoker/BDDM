from __future__ import annotations

from build_alignment_review_queue import alignment_review_reasons, build_alignment_review_queue


def test_alignment_review_queue_includes_string_recovered_context() -> None:
    row = {
        "row_id": "r1",
        "arxiv_id": "2300.00001",
        "theorem_id": "thm:demo",
        "canonical_theorem_id": "demo",
        "statement_alignment_class": "exact",
        "alignment_confidence": 0.9,
        "alignment_tier": "alignment_review_required",
        "alignment_review_required": True,
        "source_span_quality": "string_recovered",
        "source_span": {"span_confidence": "string_recovered_exact"},
        "alignment_evidence": {"source_match": {"match_status": "matched"}},
        "source_latex": "source statement",
        "lean_statement": "theorem demo : True",
        "dataset_tier": "gold_proof",
    }

    reasons = alignment_review_reasons(row)
    queue, summary = build_alignment_review_queue([row])

    assert "source_span_quality:string_recovered" in reasons
    assert "high_value_alignment_candidate" in reasons
    assert queue[0]["source_latex"] == "source statement"
    assert queue[0]["lean_statement"] == "theorem demo : True"
    assert queue[0]["source_span"]["span_confidence"] == "string_recovered_exact"
    assert summary["rows"] == 1
    assert summary["string_recovered_rows"] == 1


def test_alignment_review_queue_skips_alignment_gold_rows() -> None:
    row = {
        "row_id": "r2",
        "alignment_tier": "alignment_gold",
        "alignment_review_required": False,
        "source_span_quality": "extractor_native",
        "statement_alignment_class": "exact",
        "alignment_evidence": {"source_match": {"match_status": "matched"}},
    }

    queue, summary = build_alignment_review_queue([row])

    assert queue == []
    assert summary["rows"] == 0
