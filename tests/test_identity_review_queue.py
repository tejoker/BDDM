from __future__ import annotations

from build_identity_review_queue import build_identity_review_queue, identity_review_reasons


def test_identity_review_queue_includes_unknown_novelty_context() -> None:
    row = {
        "row_id": "r1",
        "arxiv_id": "2300.00001",
        "theorem_id": "thm:demo",
        "identity_status": "unknown",
        "novelty_status": "unknown",
        "mathlib_novelty_status": "unknown",
        "identity_evidence": {"human_review_required": True},
        "source_latex": "source",
        "lean_statement": "theorem demo : True",
    }

    reasons = identity_review_reasons(row)
    queue, summary = build_identity_review_queue([row])

    assert "identity_status:unknown" in reasons
    assert "mathlib_novelty_unknown" in reasons
    assert queue[0]["source_latex"] == "source"
    assert queue[0]["lean_statement"] == "theorem demo : True"
    assert summary["unknown_identity_rows"] == 1


def test_identity_review_queue_flags_unsupported_new_candidate() -> None:
    row = {
        "row_id": "r2",
        "identity_status": "distinct_candidate",
        "novelty_status": "new_candidate",
        "mathlib_novelty_status": "new_candidate",
        "identity_evidence": {"mathlib_checks_run": []},
    }

    queue, summary = build_identity_review_queue([row])

    assert queue[0]["review_reasons"] == ["unsupported_new_candidate"]
    assert summary["unsupported_new_candidate_rows"] == 1


def test_identity_review_queue_skips_supported_distinct_candidate() -> None:
    row = {
        "row_id": "r3",
        "identity_status": "distinct_candidate",
        "novelty_status": "new_candidate",
        "mathlib_novelty_status": "new_candidate",
        "identity_evidence": {"mathlib_checks_run": ["mathlib_fingerprint"]},
    }

    queue, summary = build_identity_review_queue([row])

    assert queue == []
    assert summary["rows"] == 0
