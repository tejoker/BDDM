"""Tests for the elaboration-failure categorizer."""

from __future__ import annotations

from pathlib import Path

from categorize_elaboration_failures import build_summary, categorize_error


def test_typeclass_instance_missing() -> None:
    err = "error(lean.synthInstanceFailed): failed to synthesize instance of type class\n  HasSubset Multisegment"
    assert categorize_error(err) == "typeclass_instance_missing"


def test_unknown_identifier() -> None:
    err = "error(lean.unknownIdentifier): Unknown constant `Paper_2304_09598.Multisegment.ofSegments`"
    assert categorize_error(err) == "unknown_identifier"


def test_invalid_field_projection() -> None:
    err = "error(lean.invalidField): Invalid field `IsSimple`: The environment does not contain `Nat.IsSimple`"
    assert categorize_error(err) == "invalid_field_projection"


def test_invalid_anonymous_constructor() -> None:
    err = "error: Invalid `⟨...⟩` notation: The expected type `Segment` is not an inductive type"
    assert categorize_error(err) == "invalid_field_projection"


def test_application_type_mismatch_function_expected() -> None:
    err = "error: Function expected at\n  u t\nbut this term has type\n  ℝ"
    assert categorize_error(err) == "application_type_mismatch"


def test_application_type_mismatch_type_mismatch() -> None:
    err = "error: Type mismatch\n  beta\nhas type\n  ℝ → ℝ → ℝ\nbut is expected to have type\n  ℝ"
    assert categorize_error(err) == "application_type_mismatch"


def test_parse_error_unexpected_token() -> None:
    err = "error: unexpected token; expected ')', ',' or ':'"
    assert categorize_error(err) == "parse_error"


def test_parse_error_expected_token() -> None:
    err = "error: expected token"
    assert categorize_error(err) == "parse_error"


def test_metavariable_unresolved() -> None:
    err = "error: don't know how to synthesize placeholder\ncontext:\nu0 : ℝ → ℝ"
    assert categorize_error(err) == "metavariable_unresolved"


def test_elaboration_no_detail() -> None:
    """When the translator records the gate-marker without preserving the
    Lean error, classify into a separate bucket — these rows can't be
    further categorized without re-running translation."""
    err = "translation_acceptance_gate:lean_elaboration_failed"
    assert categorize_error(err) == "elaboration_no_detail"


def test_other_for_unrecognized_pattern() -> None:
    err = "some random error message we don't have a pattern for"
    assert categorize_error(err) == "other"


def test_priority_typeclass_beats_application() -> None:
    """A synthInstanceFailed error that ALSO contains 'Type mismatch' (e.g.
    in a hint line) should classify as typeclass_instance_missing, since
    that's the root cause and 'Type mismatch' is a downstream symptom."""
    err = "error(lean.synthInstanceFailed): failed to synthesize instance of\n  Add Foo\nType mismatch in synth"
    assert categorize_error(err) == "typeclass_instance_missing"


def test_build_summary_produces_table_shape() -> None:
    failures = [
        {"paper_id": "p1", "theorem_name": "t1", "error_message": "synthInstanceFailed: HasSubset Foo", "status": "UNRESOLVED", "bucket": "typeclass_instance_missing"},
        {"paper_id": "p1", "theorem_name": "t2", "error_message": "synthInstanceFailed: Add Bar", "status": "UNRESOLVED", "bucket": "typeclass_instance_missing"},
        {"paper_id": "p2", "theorem_name": "t3", "error_message": "unexpected token", "status": "UNRESOLVED", "bucket": "parse_error"},
    ]
    summary = build_summary(failures)
    assert summary["total_elaboration_failures"] == 3
    assert summary["bucket_counts"]["typeclass_instance_missing"] == 2
    assert summary["bucket_counts"]["parse_error"] == 1
    assert summary["bucket_per_paper"]["typeclass_instance_missing"] == {"p1": 2}
    assert summary["bucket_per_paper"]["parse_error"] == {"p2": 1}
