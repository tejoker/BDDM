"""Tests for automatic translation/status quality score inference."""

from __future__ import annotations

from pipeline_status import infer_quality_scores


def test_infer_quality_uses_translation_confidence_when_available():
    fidelity, alignment = infer_quality_scores(
        proved=True,
        step_records=[{"result": "proof-finished"}],
        error_message="",
        lean_statement="theorem t : True := by trivial",
        translation_confidence=0.91,
    )
    assert abs(fidelity - 0.91) < 1e-9
    assert alignment >= 0.9


def test_infer_quality_penalizes_unvalidated_translation_with_flags():
    fidelity, _alignment = infer_quality_scores(
        proved=False,
        step_records=[{"result": "lean-error"}],
        error_message="unknown identifier",
        lean_statement="theorem t : True := by sorry",
        translation_validated=False,
        translation_rounds_used=4,
        translation_uncertainty_flags=["unknown_symbol", "syntax_error"],
    )
    assert fidelity < 0.35


def test_infer_alignment_drops_on_exception_without_records():
    _fidelity, alignment = infer_quality_scores(
        proved=False,
        step_records=[],
        error_message="pipeline exception",
        lean_statement="theorem t : True := by sorry",
        had_exception=True,
    )
    assert alignment <= 0.7
