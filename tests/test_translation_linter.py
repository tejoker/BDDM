"""Tests for the translation-quality linter."""

from __future__ import annotations

from pathlib import Path

from translation_linter import lint_lean_file


def _write(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_linter_flags_typeclass_in_existential(tmp_path: Path) -> None:
    src = tmp_path / "test.lean"
    _write(src, """
import Mathlib

theorem corollary_2 :
  ∃ (N K D : ℕ) (alpha : Type*) [MetricSpace alpha] [NormedAddCommGroup alpha],
  True := by
  sorry
""")
    report = lint_lean_file(src)
    kinds = {i["kind"] for i in report["issues"]}
    assert "typeclass_in_existential" in kinds


def test_linter_flags_latex_leak_token(tmp_path: Path) -> None:
    """A LaTeX command like `\\frac` or `\\mathbf` left in the Lean output as
    a bare identifier is an error."""
    src = tmp_path / "test.lean"
    _write(src, """
import Mathlib

theorem bad_thm (x : ℝ) : frac x 2 = mathbf x := by sorry
""")
    report = lint_lean_file(src)
    kinds = [i["kind"] for i in report["issues"]]
    assert "latex_leak_token" in kinds


def test_linter_flags_subscript_braces(tmp_path: Path) -> None:
    src = tmp_path / "test.lean"
    _write(src, """
import Mathlib

theorem bad_subscript (n : ℕ) : (x_{i}) = (x^{2}) := by sorry
""")
    report = lint_lean_file(src)
    kinds = [i["kind"] for i in report["issues"]]
    assert "latex_subscript_or_superscript_braces" in kinds


def test_linter_flags_false_target_warning(tmp_path: Path) -> None:
    """`: False := by sorry` is a fallback the translator emitted — flag as warning."""
    src = tmp_path / "test.lean"
    _write(src, """
import Mathlib

theorem fallback_thm : False := by sorry
""")
    report = lint_lean_file(src)
    kinds = {i["kind"] for i in report["issues"]}
    assert "false_target_translator_gave_up" in kinds
    severity_for_kind = {i["kind"]: i["severity"] for i in report["issues"]}
    assert severity_for_kind["false_target_translator_gave_up"] == "warning"


def test_linter_flags_placeholder_target(tmp_path: Path) -> None:
    """`: True` or `: x = x` are vacuous targets — translator likely degraded."""
    src = tmp_path / "test.lean"
    _write(src, """
import Mathlib

theorem vacuous : True := by trivial

theorem self_eq (x : ℕ) : x = x := by rfl
""")
    report = lint_lean_file(src)
    kinds = [i["kind"] for i in report["issues"]]
    # Both should be flagged
    assert kinds.count("placeholder_target") >= 1


def test_linter_does_not_flag_legitimate_namespace_close(tmp_path: Path) -> None:
    """`end ArxivPaper` is legal Lean for namespace closure — must not be
    flagged as a LaTeX leak."""
    src = tmp_path / "test.lean"
    _write(src, """
import Mathlib

namespace ArxivPaper

theorem ok_thm : True := by trivial

end ArxivPaper
""")
    report = lint_lean_file(src)
    leak_issues = [i for i in report["issues"] if i["kind"] == "latex_leak_token"]
    assert leak_issues == [], f"Should not flag namespace close as leak, got: {leak_issues}"


def test_linter_does_not_flag_clean_theorem(tmp_path: Path) -> None:
    """A clean translation should produce no error-severity issues."""
    src = tmp_path / "test.lean"
    _write(src, """
import Mathlib

theorem clean_thm (n : ℕ) (h : n > 0) : n + 0 = n := by
  rfl
""")
    report = lint_lean_file(src)
    errors = [i for i in report["issues"] if i["severity"] == "error"]
    assert errors == [], f"Clean theorem should produce no errors, got: {errors}"


def test_linter_returns_kind_counts(tmp_path: Path) -> None:
    src = tmp_path / "test.lean"
    _write(src, """
import Mathlib

theorem fallback_a : False := by sorry
theorem fallback_b : False := by sorry
""")
    report = lint_lean_file(src)
    assert report["issue_kind_counts"].get("false_target_translator_gave_up", 0) == 2
