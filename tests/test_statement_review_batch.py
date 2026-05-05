from __future__ import annotations

from build_statement_review_batch import build_statement_review_batch, review_batch_exclusion_reasons, source_span_sha256


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "row_id": "r1",
        "arxiv_id": "2300.00001",
        "theorem_id": "thm:demo",
        "canonical_theorem_id": "demo",
        "status": "UNRESOLVED",
        "statement_alignment_class": "partial",
        "alignment_confidence": 0.2,
        "alignment_gold_eligible": False,
        "claim_equivalence_verdict": "unclear",
        "identity_status": "unknown",
        "source_span_quality": "extractor_native",
        "source_span": {"source_file": "paper.tex", "start_byte": 1, "end_byte": 9},
        "alignment_evidence": {"source_match": {"match_status": "matched"}},
        "source_latex": "For all n, n = n.",
        "normalized_text": "For all n, n = n.",
        "lean_statement": "theorem demo (n : Nat) : n = n",
        "validation_gates": {},
        "artifact_paths": {},
    }
    row.update(overrides)
    return row


def test_statement_review_batch_writes_span_bound_templates() -> None:
    row = _row()
    batch, templates, summary = build_statement_review_batch([row])

    assert len(batch) == 1
    assert len(templates) == 1
    assert batch[0]["source_span_sha256"] == source_span_sha256(row)
    assert templates[0]["schema_version"] == "reviewed_statement_alignment.v1"
    assert templates[0]["source_span_sha256"] == batch[0]["source_span_sha256"]
    assert templates[0]["reviewed_statement_alignment_class"] == ""
    assert summary["review_batch_rows"] == 1
    assert summary["template_rows"] == 1


def test_statement_review_batch_excludes_repair_needed_rows() -> None:
    row = _row(status="FLAWED", lean_statement="theorem demo : ∃ x : ℝ, x = x")

    reasons = review_batch_exclusion_reasons(row)
    batch, templates, summary = build_statement_review_batch([row])

    assert "status_needs_statement_repair:FLAWED" in reasons
    assert "placeholder_or_trivial_lean_statement" in reasons
    assert batch == []
    assert templates == []
    assert summary["exclusion_reason_counts"]["status_needs_statement_repair:FLAWED"] == 1


def test_statement_review_batch_excludes_false_targets_without_contradiction_source() -> None:
    row = _row(
        lean_statement="theorem demo : False",
        source_latex="For all n, n = n.",
        normalized_text="For all n, n = n.",
    )

    reasons = review_batch_exclusion_reasons(row)
    batch, templates, summary = build_statement_review_batch([row])

    assert "false_target_without_source_contradiction" in reasons
    assert batch == []
    assert templates == []
    assert summary["exclusion_reason_counts"]["false_target_without_source_contradiction"] == 1


def test_statement_review_batch_accepts_repaired_reviewable_statement() -> None:
    row = _row(
        status="UNRESOLVED",
        lean_statement="theorem repaired_demo (n : Nat) : n = n",
        source_latex="For every natural number n, n equals itself.",
        translation_repair={"statement_repair_kind": "source_backed_statement_regeneration"},
    )

    reasons = review_batch_exclusion_reasons(row)
    batch, templates, summary = build_statement_review_batch([row])

    assert reasons == []
    assert len(batch) == 1
    assert len(templates) == 1
    assert summary["review_batch_rows"] == 1


def test_statement_review_batch_excludes_schema_raw_latex_and_paper_claim_rows() -> None:
    rows = [
        _row(lean_statement="theorem schema (p_c1 : Prop) (h_c1 : p_c1) : p_c1"),
        _row(lean_statement="theorem raw_latex : \\frac{x}{y} = z"),
        _row(lean_statement="theorem claim : PaperClaim"),
    ]

    all_reasons = [review_batch_exclusion_reasons(row) for row in rows]

    assert "placeholder_or_trivial_lean_statement" in all_reasons[0]
    assert "raw_latex_lean_statement" in all_reasons[1]
    assert "placeholder_or_trivial_lean_statement" in all_reasons[2]
