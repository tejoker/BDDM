"""Tests for syntax-constrained tactic filtering and error-class dispatch."""
from __future__ import annotations

from ponder_loop import sanitize_tactic_candidate
from prove_with_ponder import classify_lean_error, repair_hint_for_error_class


def test_sanitize_tactic_candidate_accepts_valid_tactic():
    assert sanitize_tactic_candidate("simp [h]") == "simp [h]"


def test_sanitize_tactic_candidate_rejects_sorry_and_admit():
    assert sanitize_tactic_candidate("sorry") == ""
    assert sanitize_tactic_candidate("admit") == ""


def test_sanitize_tactic_candidate_rejects_unbalanced_delimiters():
    assert sanitize_tactic_candidate("simp [h") == ""
    assert sanitize_tactic_candidate("rw [foo))") == ""


def test_sanitize_tactic_candidate_strips_numbered_prefix():
    assert sanitize_tactic_candidate("1. exact h") == "exact h"


def test_sanitize_tactic_candidate_rejects_non_tactic_start():
    assert sanitize_tactic_candidate("fooBar h") == ""


def test_sanitize_tactic_candidate_accepts_qualified_tactic_name():
    assert sanitize_tactic_candidate("Mathlib.Tactic.ring") == "Mathlib.Tactic.ring"


def test_sanitize_tactic_candidate_rejects_broken_separator_sequence():
    assert sanitize_tactic_candidate("rw [h],, simp") == ""


def test_classify_lean_error_name_resolution():
    err = "unknown identifier 'Foo.bar'"
    assert classify_lean_error(err) == "name-resolution"


def test_classify_lean_error_type_mismatch():
    err = "application type mismatch"
    assert classify_lean_error(err) == "type-mismatch"


def test_repair_hint_for_error_class_nonempty():
    for klass in [
        "name-resolution",
        "type-mismatch",
        "rewrite-mismatch",
        "incomplete-progress",
        "resource-timeout",
        "generic",
    ]:
        assert repair_hint_for_error_class(klass)
