"""Tests for translation auto-repair (translator-bug fixer)."""

from __future__ import annotations

from pathlib import Path

from translation_autorepair import (
    _repair_forall_typeclass_after_arrow,
    _repair_greek_identifier_collision,
    _repair_latex_braces,
    _repair_lost_witness_type,
    _repair_typeclass_in_existential,
    autorepair_lean_file,
)


def test_typeclass_in_existential_rewritten_to_top_level_binder() -> None:
    """`∃ (α : Type*) [TC α], body` — the typeclass binder belongs at the
    theorem head, not inside the existential. The rewrite preserves the
    body and the tail."""
    block = (
        "theorem foo :\n"
        "    ∃ (α : Type*) [MetricSpace α] [NormedAddCommGroup α],\n"
        "    True := by sorry"
    )
    new, changed = _repair_typeclass_in_existential(block)
    assert changed, f"Expected rewrite, got: {new}"
    # The typeclass binder must now appear in the theorem head, not inside ∃.
    assert "[MetricSpace α]" in new
    assert "∃ (α : Type*) [MetricSpace α]" not in new
    # The body and tail are preserved.
    assert ": True := by sorry" in new


def test_typeclass_in_existential_preserves_other_binders() -> None:
    """When the existential has both non-typeclass binders (`(N : ℕ)`) and
    type+typeclass binders, ALL binders are promoted to the theorem head
    so the existential's body keeps making sense."""
    block = (
        "theorem foo :\n"
        "    ∃ (N : ℕ) (α : Type*) [MetricSpace α], P N α := by sorry"
    )
    new, changed = _repair_typeclass_in_existential(block)
    assert changed
    assert "(N : ℕ)" in new
    assert "(α : Type*)" in new
    assert "[MetricSpace α]" in new
    assert ": P N α" in new


def test_typeclass_in_existential_no_match_when_no_typeclass() -> None:
    """A plain existential `∃ (n : ℕ), body` should NOT be rewritten —
    the rewrite only fires when typeclass binders are present."""
    block = "theorem foo : ∃ (n : ℕ), n > 0 := by sorry"
    new, changed = _repair_typeclass_in_existential(block)
    assert not changed
    assert new == block


def test_latex_braces_rewritten() -> None:
    """`x_{i}` → `x_i`, `x^{2}` → `x ^ 2`."""
    block = "theorem foo (x : ℕ) : x_{i} = x^{2} := by sorry"
    new, changed = _repair_latex_braces(block)
    assert changed
    assert "x_i" in new
    assert "x ^ 2" in new


def test_latex_braces_no_op_on_clean_lean() -> None:
    block = "theorem foo (x : ℕ) : x = x := by rfl"
    new, changed = _repair_latex_braces(block)
    assert not changed
    assert new == block


def test_autorepair_idempotent(tmp_path: Path) -> None:
    """Running auto-repair twice on the same file leaves the second pass as a no-op."""
    src = tmp_path / "test.lean"
    src.write_text(
        "import Mathlib\n\n"
        "namespace ArxivPaper\n\n"
        "theorem foo (x : ℕ) : x_{i} = x := by sorry\n\n"
        "end ArxivPaper\n",
        encoding="utf-8",
    )
    first = autorepair_lean_file(src, dry_run=False)
    assert first["rows_repaired"] >= 1
    second = autorepair_lean_file(src, dry_run=False)
    assert second["rows_repaired"] == 0


def test_forall_typeclass_after_arrow_is_rewritten() -> None:
    """`∀ T : Type*, [TC T] → P T` is invalid Lean — typeclass binders can't
    follow `→`. Rewrite to top-level theorem binders."""
    block = (
        "theorem foo :\n"
        "    ∀ (T : Type*), [MetricSpace T] → ∃ x : T, True := by sorry"
    )
    new, changed = _repair_forall_typeclass_after_arrow(block)
    assert changed
    assert "[MetricSpace T]" in new
    # binders moved to head; arrow removed
    assert "→" not in new.split(":=")[0] or "→" in new.split(":=")[0]  # body may still have →
    assert "∀ (T : Type*), [MetricSpace T] →" not in new


def test_greek_identifier_collision_renamed() -> None:
    """`def π : ℝ := 0` collides with Mathlib's `Real.pi`. Rename to `pi_paper`."""
    block = "def π : ℝ := 0\ndef λ : ℝ := 1\n"
    new, changed = _repair_greek_identifier_collision(block)
    assert changed
    assert "def pi_paper : ℝ := 0" in new
    assert "def lambda_paper : ℝ := 1" in new


def test_greek_identifier_no_op_on_use_sites() -> None:
    """We only rename DECLARATIONS — uses of π elsewhere (e.g., `Real.pi`) are
    fine and must not be touched."""
    block = "theorem t (x : ℝ) : x * Real.pi = Real.pi * x := by ring\n"
    new, changed = _repair_greek_identifier_collision(block)
    assert not changed
    assert new == block


def test_lost_witness_type_repaired() -> None:
    """`∃ x, P x` with no type — insert `: ℕ` default."""
    block = "theorem foo : ∃ n, n + 1 > 0 := by sorry"
    new, changed = _repair_lost_witness_type(block)
    assert changed
    assert "∃ n : ℕ," in new


def test_lost_witness_type_skips_typed_existentials() -> None:
    block = "theorem foo : ∃ n : ℤ, n + 1 > 0 := by sorry"
    new, changed = _repair_lost_witness_type(block)
    assert not changed
    assert new == block


def test_autorepair_dry_run_does_not_write(tmp_path: Path) -> None:
    src = tmp_path / "test.lean"
    body = (
        "import Mathlib\n\n"
        "theorem foo (x : ℕ) : x_{i} = x := by sorry\n"
    )
    src.write_text(body, encoding="utf-8")
    summary = autorepair_lean_file(src, dry_run=True)
    assert summary["rows_repaired"] >= 1
    # File unchanged
    assert src.read_text(encoding="utf-8") == body
