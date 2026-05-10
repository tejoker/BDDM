"""Tests for the area-aware CoT judge prompt extension."""

from __future__ import annotations

from leanstral_cot_judge import _AREA_HINTS, _USER_TEMPLATE, _area_hint


def test_area_hint_returns_empty_for_none_or_generic() -> None:
    assert _area_hint(None) == ""
    assert _area_hint("") == ""
    assert _area_hint("generic") == ""


def test_area_hint_returns_analysis_text() -> None:
    h = _area_hint("analysis")
    assert "[Area: analysis]" in h
    assert "Lipschitz" in h or "Sobolev" in h or "Lp" in h or "Tendsto" in h
    # Must mention the key idiom that was being mis-rejected.
    assert "∀ ε > 0" in h or "(ε : ℝ)" in h


def test_area_hint_returns_probability_text() -> None:
    h = _area_hint("probability")
    assert "[Area: probability]" in h
    assert "almost surely" in h.lower()
    assert "ae" in h.lower() or "MeasureTheory" in h


def test_area_hint_returns_algebra_text() -> None:
    h = _area_hint("algebra")
    assert "[Area: algebra]" in h
    assert "ring" in h.lower() or "module" in h.lower()


def test_area_hint_case_insensitive() -> None:
    """Caller may pass `analysis` / `Analysis` / `ANALYSIS` — all work."""
    base = _area_hint("analysis")
    assert _area_hint("Analysis") == base
    assert _area_hint("ANALYSIS") == base


def test_area_hint_unknown_returns_empty() -> None:
    """Unknown area falls back to no hint (judge uses base prompt only)."""
    assert _area_hint("not_an_area") == ""


def test_user_template_includes_area_hint_placeholder() -> None:
    """The template must have a `{area_hint}` slot the caller substitutes
    so the area-specific text is spliced before the JSON-emit instruction."""
    assert "{area_hint}" in _USER_TEMPLATE
    # And the placeholder must come AFTER the LaTeX/Lean and BEFORE the
    # final reasoning instruction.
    latex_pos = _USER_TEMPLATE.index("{latex}")
    hint_pos = _USER_TEMPLATE.index("{area_hint}")
    final_pos = _USER_TEMPLATE.index("Reason step-by-step")
    assert latex_pos < hint_pos < final_pos


def test_all_area_hints_have_expected_shape() -> None:
    """Every non-generic hint must be non-empty, mention `[Area: …]`, and
    include at least one ≡ equivalence rule. This guards against an area
    being added without per-area equivalence rules."""
    for area, hint in _AREA_HINTS.items():
        if area == "generic":
            assert hint == ""
            continue
        assert hint, f"Area {area} has empty hint"
        assert f"[Area: {area}]" in hint
        assert "≡" in hint, f"Area {area} hint must contain at least one ≡ rule"
