"""Tests for syntax-constrained tactic filtering and error-class dispatch."""
from __future__ import annotations

from ponder_loop import sanitize_tactic_candidate
from prove_with_ponder import (
    _replace_theorem_body_in_source,
    _should_trigger_secondary_verifier,
    classify_lean_error,
    repair_hint_for_error_class,
)


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


def test_classify_lean_error_assumption_mismatch():
    err = "Tactic `assumption` failed"
    assert classify_lean_error(err) == "assumption-mismatch"


def test_classify_lean_error_assumption_mismatch_without_backticks():
    err = "line=1; message=assumption failed to find matching hypothesis"
    assert classify_lean_error(err) == "assumption-mismatch"


def test_classify_lean_error_policy_blocked_marker():
    err = "line=1; message=blocked_non_actionable_tactic:assumption_disabled_policy"
    assert classify_lean_error(err) == "policy-blocked"


def test_repair_hint_for_error_class_nonempty():
    for klass in [
        "name-resolution",
        "type-mismatch",
        "rewrite-mismatch",
        "incomplete-progress",
        "resource-timeout",
        "assumption-mismatch",
        "generic",
    ]:
        assert repair_hint_for_error_class(klass)


def test_replace_theorem_body_in_source_rewrites_target_proof_block():
    src = (
        "theorem A : True := by\n"
        "  trivial\n\n"
        "theorem B : True := by\n"
        "  trivial\n"
    )
    patched, detail = _replace_theorem_body_in_source(
        lean_src=src,
        theorem_name="A",
        draft="simp\ntrivial",
    )
    assert detail == "ok"
    assert patched is not None
    assert "theorem A : True := by\n  simp\n  trivial\n" in patched
    assert "theorem B : True := by\n  trivial\n" in patched


def test_secondary_verifier_trigger_on_generic_rfl_feedback():
    err = "line=1; message=Tactic `rfl` failed: Expected the goal to be a binary relation"
    assert _should_trigger_secondary_verifier(err, "reflexivity-mismatch")


def test_secondary_verifier_not_triggered_for_policy_block() -> None:
    err = "line=1; message=blocked_non_actionable_tactic:assumption_disabled_policy"
    assert _should_trigger_secondary_verifier(err, "policy-blocked") is False
