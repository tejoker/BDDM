"""Hermetic tests for `benchmark_minif2f_calibration` helpers.

The script's heavy work — loading the dataset, calling Mistral, running
`lake env lean` — is integration-only and not unit-tested. We pin only
the pure-Python helpers (theorem-name extraction, sorry-stripping,
category derivation) so refactors don't silently break parsing.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _module():
    # Re-import on each call so the `.env` side-effect doesn't leak between
    # tests that mock environment state.
    return importlib.import_module("benchmark_minif2f_calibration")


def test_categorize_handles_mathd_subcategories() -> None:
    m = _module()
    assert m._categorize("mathd_algebra_478") == "mathd_algebra"
    assert m._categorize("mathd_numbertheory_1124") == "mathd_numbertheory"


def test_categorize_handles_competition_prefixes() -> None:
    m = _module()
    assert m._categorize("imo_1983_p6") == "imo"
    assert m._categorize("aime_1983_p1") == "aime"
    assert m._categorize("amc12b_2020_p2") == "amc12b"
    assert m._categorize("algebra_sqineq_unitcircatbpabsamblt1") == "algebra"


def test_categorize_falls_back_for_unknown_shape() -> None:
    m = _module()
    assert m._categorize("nonstandard") == "nonstandard"
    # Empty string split() yields [''] which is truthy with len=1; the
    # function returns the empty head. That's fine — empty IDs shouldn't
    # appear in the dataset, but we pin the deterministic behavior.
    assert m._categorize("") == ""


def test_extract_theorem_name() -> None:
    m = _module()
    stmt = "theorem mathd_algebra_478 (b h : ℝ) : True := sorry"
    assert m._extract_theorem_name(stmt) == "mathd_algebra_478"
    # Same for lemma keyword.
    assert m._extract_theorem_name("lemma foo (x : Nat) : x = x := rfl") == "foo"


def test_extract_theorem_name_falls_back_when_missing() -> None:
    m = _module()
    # Truly malformed input (no `theorem` keyword at all) falls back.
    assert m._extract_theorem_name("just a comment") == "minif2f_problem"


def test_strip_trailing_sorry_handles_common_shapes() -> None:
    m = _module()
    assert m._strip_trailing_sorry("theorem foo : True := sorry").endswith("True")
    assert m._strip_trailing_sorry("theorem foo : True := by sorry").endswith("True")
    assert m._strip_trailing_sorry(
        "theorem foo : True := by\n  sorry"
    ).endswith("True")


def test_strip_trailing_sorry_preserves_real_proofs() -> None:
    m = _module()
    stmt = "theorem foo : 1 + 1 = 2 := by decide"
    # No trailing sorry — should pass through unchanged (modulo trailing
    # whitespace stripping).
    assert m._strip_trailing_sorry(stmt).rstrip() == stmt.rstrip()
