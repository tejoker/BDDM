"""Tests for the quantifier-scope-flip adversarial check.

The pre-existing `_quantifier_mismatch_issue` only flagged DROPPED
quantifiers. This new check tightens the gate against more subtle
violations: `∀x ∃y P(x, y)` is genuinely weaker than `∃y ∀x P(x, y)`,
so flipping the scope is a real translation error even when both
quantifiers appear in the Lean signature."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from translator._translate import _quantifier_scope_flip_issue


def test_no_flip_when_orders_match() -> None:
    """`for every x there exists y` (UE) ↔ `∀ x, ∃ y, …` (UE) — no flip."""
    latex = "For every x in S, there exists y such that P(x, y)."
    sig = "theorem foo : ∀ x, ∃ y, P x y := by sorry"
    assert _quantifier_scope_flip_issue(latex, sig) is None


def test_flip_detected_when_universal_first_in_latex_existential_first_in_lean() -> None:
    """Latex `∀ x ∃ y` but Lean `∃ y ∀ x` — flagged."""
    latex = "For all x, there exists y such that P(x, y)."
    sig = "theorem foo : ∃ y, ∀ x, P x y := by sorry"
    issue = _quantifier_scope_flip_issue(latex, sig)
    assert issue is not None
    assert "scope_flipped" in issue


def test_flip_detected_when_existential_first_in_latex_universal_first_in_lean() -> None:
    """Latex `∃ y ∀ x` but Lean `∀ x ∃ y` — flagged."""
    latex = "There exists y such that for all x, P(x, y)."
    sig = "theorem foo : ∀ x, ∃ y, P x y := by sorry"
    issue = _quantifier_scope_flip_issue(latex, sig)
    assert issue is not None
    assert "scope_flipped" in issue


def test_no_flip_when_only_one_quantifier() -> None:
    """A single quantifier on each side can't cause a scope flip."""
    latex = "For every n in ℕ, P(n) holds."
    sig = "theorem foo : ∀ n : ℕ, P n := by sorry"
    assert _quantifier_scope_flip_issue(latex, sig) is None


def test_no_flip_when_latex_has_only_one_quantifier() -> None:
    latex = "For every n, P(n)."
    sig = "theorem foo : ∀ n, ∃ m, P n m := by sorry"
    # Only one latex quantifier → no comparison possible.
    assert _quantifier_scope_flip_issue(latex, sig) is None


def test_lean_pre_binders_count_as_universals() -> None:
    """Top-level theorem binders `(x : T)` count as universal quantifiers,
    so `theorem foo (x : T) : ∃ y, P x y` should match latex `∀x ∃y`."""
    latex = "For every x there exists y such that P(x, y)."
    sig = "theorem foo (x : ℝ) : ∃ y : ℝ, P x y := by sorry"
    # ∀ x (binder) ∃ y (explicit) → UE; matches latex UE → no flip.
    assert _quantifier_scope_flip_issue(latex, sig) is None


def test_unicode_forall_exists_recognized() -> None:
    latex = "∀ x, ∃ y, P(x, y)."
    sig = "theorem foo : ∃ y, ∀ x, P x y := by sorry"
    issue = _quantifier_scope_flip_issue(latex, sig)
    assert issue is not None
    assert "scope_flipped" in issue


def test_empty_inputs_return_none() -> None:
    assert _quantifier_scope_flip_issue("", "") is None
    assert _quantifier_scope_flip_issue("", "theorem foo : ∀ x, P x := by sorry") is None
    assert _quantifier_scope_flip_issue("for all x", "") is None
