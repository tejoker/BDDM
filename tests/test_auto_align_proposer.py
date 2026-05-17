"""Hermetic tests for `scripts/auto_align_proposer.py`.

The aligner parses paper-theory stubs to infer alignment shape and
emits Lean alignment theorems. These tests pin the shape detection
without invoking lake.
"""
from __future__ import annotations

from pathlib import Path

from auto_align_proposer import (
    _SHAPES,
    _count_binders,
    _paper_namespace,
    _render_rhs,
    find_failing_lines,
    parse_paper_theory_defs,
    render_alignment_file,
)


def test_count_binders_single_group_multi_name() -> None:
    assert _count_binders("(_i _k : ℕ)") == 2


def test_count_binders_multiple_groups() -> None:
    assert _count_binders("(_i _k : ℕ) (n : ℕ)") == 3


def test_count_binders_empty() -> None:
    assert _count_binders("") == 0
    assert _count_binders("   ") == 0


def test_paper_namespace_format() -> None:
    assert _paper_namespace("2604.21884") == "Paper_2604_21884"


def test_render_rhs_fun_zero_uses_correct_arity() -> None:
    # Dispatch is on the rhs_template marker (2nd arg), not the shape name.
    out = _render_rhs("fn_returning_zero", "fun_zero", "(_i _k : ℕ)")
    # Two underscores for two binders.
    assert out.count("_") == 2
    assert "(0 : ℝ)" in out
    assert out.startswith("fun ")


def test_render_rhs_fun_setuniv() -> None:
    out = _render_rhs("fn_set_univ", "fun_setuniv", "(_x : ℝ)")
    assert "Set.univ" in out
    assert out.startswith("fun ")


def test_render_rhs_value_returns_template() -> None:
    out = _render_rhs("value_zero_real", "(0 : ℝ)", None)
    assert out == "(0 : ℝ)"


def test_parse_paper_theory_defs_value_zero(tmp_path: Path) -> None:
    src = tmp_path / "Paper_Test.lean"
    src.write_text(
        "namespace Paper_test\n"
        "def alpha : ℝ := 0\n"
        "def beta : ℕ := 0\n"
        "def gamma : ℝ := 0\n"
        "end Paper_test\n",
        encoding="utf-8",
    )
    # Patch the global PAPER_THEORY_DIR to tmp_path via parse function call.
    # Use the module's parse function directly with explicit file path.
    import auto_align_proposer as aap
    orig = aap.PAPER_THEORY_DIR
    try:
        aap.PAPER_THEORY_DIR = tmp_path
        # Rename the file to match the parser's expected naming.
        target = tmp_path / "Paper_test.lean"
        src.rename(target)
        defs = parse_paper_theory_defs("test")
        assert "alpha" in defs
        assert defs["alpha"]["shape"] == "value_zero_real"
        assert defs["alpha"]["rhs"] == "(0 : ℝ)"
        assert "beta" in defs
        assert defs["beta"]["shape"] == "value_zero_nat"
        assert defs["beta"]["rhs"] == "(0 : ℕ)"
    finally:
        aap.PAPER_THEORY_DIR = orig


def test_parse_paper_theory_defs_function_returning_zero(tmp_path: Path) -> None:
    src = tmp_path / "Paper_pid.lean"
    src.write_text(
        "namespace Paper_pid\n"
        "def omega (_i _k : ℕ) : ℝ := 0\n"
        "end Paper_pid\n",
        encoding="utf-8",
    )
    import auto_align_proposer as aap
    orig = aap.PAPER_THEORY_DIR
    try:
        aap.PAPER_THEORY_DIR = tmp_path
        defs = parse_paper_theory_defs("pid")
        assert "omega" in defs
        assert defs["omega"]["shape"] == "fn_returning_zero"
        # Two-arg lambda
        assert defs["omega"]["rhs"].count("_") == 2
    finally:
        aap.PAPER_THEORY_DIR = orig


def test_render_alignment_file_includes_imports_and_theorems() -> None:
    proposals = [
        {
            "paper_id": "2604.21884",
            "name": "alpha",
            "shape": "value_zero_real",
            "rhs": "(0 : ℝ)",
            "proof": "rfl",
            "args": "",
        },
        {
            "paper_id": "2604.21884",
            "name": "beta",
            "shape": "value_zero_nat",
            "rhs": "(0 : ℕ)",
            "proof": "rfl",
            "args": "",
        },
    ]
    text = render_alignment_file(proposals)
    assert "import Mathlib" in text
    assert "import Desol.PaperTheory.Paper_2604_21884" in text
    assert "namespace Desol.PaperAlignments2" in text
    assert "p_2604_21884_alpha_aligned" in text
    assert "p_2604_21884_beta_aligned" in text
    assert "end Desol.PaperAlignments2" in text


def test_find_failing_lines_parses_lake_output() -> None:
    blob = (
        "Desol/PaperAlignmentsAuto2.lean:23:18: error: foo\n"
        "Desol/PaperAlignmentsAuto2.lean:45:12: error: bar\n"
        "some other line that is not an error\n"
    )
    lines = find_failing_lines(blob)
    assert lines == {23, 45}


def test_find_failing_lines_empty_input() -> None:
    assert find_failing_lines("") == set()
    assert find_failing_lines("no errors here") == set()


def test_shapes_ordered_by_specificity() -> None:
    """First shape wins; the function-form regex must precede the value-form
    regex (otherwise `def omega (_i _k : ℕ) : ℝ := 0` would match the
    value-zero shape with the wrong binders)."""
    shape_names = [name for name, _, _, _ in _SHAPES]
    # fn_returning_zero must come before value_zero_real.
    assert shape_names.index("fn_returning_zero") < shape_names.index("value_zero_real")
    assert shape_names.index("fn_set_univ") < shape_names.index("value_set_univ")
