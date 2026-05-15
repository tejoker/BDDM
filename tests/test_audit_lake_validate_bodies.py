"""Hermetic tests for the lake-validate-bodies check in
`audit_fully_proven_integrity`.

The check catches a bypass class the body-is-sorry detector misses:
ledger claims a proof closed, on-disk body is non-sorry, but the body
DOES NOT ELABORATE (e.g. invokes an identifier that resolves to a
zero-arg `axiom : Prop` but is called with arguments).

Tests pin:
  - `_theorem_line_range_in_file` finds the right block.
  - `audit_ledger_entries(lake_error_lines=...)` demotes rows whose
    body line range overlaps an error line.
  - Rows whose body has NO overlapping lake error are validated_clean.
  - The demotion reason is `file_body_does_not_elaborate` and the
    audit_demotion record carries the offending line numbers.

These tests mock the lake output (a dict of line -> message) — they
never invoke lake/REPL/Mistral.
"""
from __future__ import annotations

import json
from pathlib import Path

from audit_fully_proven_integrity import (
    _theorem_line_range_in_file,
    audit_ledger_entries,
    collect_lake_error_lines,
)


# ---------------------------------------------------------------------------
# _theorem_line_range_in_file
# ---------------------------------------------------------------------------


def test_line_range_simple_decl() -> None:
    src = (
        "import Foo\n"
        "\n"
        "theorem foo : 0 = 0 := by\n"
        "  rfl\n"
    )
    rng = _theorem_line_range_in_file(src, "foo")
    assert rng is not None
    lo, hi = rng
    assert lo == 3
    # End at last line of file (no boundary after `rfl`).
    assert hi == 4


def test_line_range_terminates_at_next_decl() -> None:
    src = (
        "theorem first : True := by trivial\n"
        "theorem second : True := by trivial\n"
        "theorem third : True := by trivial\n"
    )
    rng = _theorem_line_range_in_file(src, "second")
    assert rng == (2, 2)


def test_line_range_namespace_qualified_fallback() -> None:
    # Ledger name is `ArxivPaper.foo`; file uses bare `foo` inside a
    # namespace block.
    src = (
        "namespace ArxivPaper\n"
        "\n"
        "theorem foo : 0 = 0 := by\n"
        "  rfl\n"
        "\n"
        "end ArxivPaper\n"
    )
    rng = _theorem_line_range_in_file(src, "ArxivPaper.foo")
    assert rng is not None
    lo, hi = rng
    assert lo == 3


def test_line_range_aux_local_name() -> None:
    src = (
        "theorem some_long_name__factored_aux : True := by\n"
        "  trivial\n"
        "\n"
        "theorem other : True := by trivial\n"
    )
    rng = _theorem_line_range_in_file(
        src,
        "parent::aux::some_long_name__factored_aux",
        aux_local_name="some_long_name__factored_aux",
    )
    assert rng is not None
    lo, hi = rng
    assert lo == 1


def test_line_range_missing_theorem_returns_none() -> None:
    src = "theorem other : True := by trivial\n"
    assert _theorem_line_range_in_file(src, "missing") is None


# ---------------------------------------------------------------------------
# audit_ledger_entries with lake_error_lines
# ---------------------------------------------------------------------------


def _make_entry(name: str, *, status: str = "FULLY_PROVEN") -> dict:
    return {
        "theorem_name": name,
        "status": status,
        "proof_text": "by some_real_tactic",
        "lean_statement": "theorem foo (n : Nat) (h : 0 < n) : 0 < 2 * n",
        "validation_gates": {"lean_proof_closed": True},
    }


def test_audit_demotes_row_with_overlapping_lake_error() -> None:
    src = (
        "import Foo\n"
        "\n"
        "theorem foo (n : Nat) (h : 0 < n) : 0 < 2 * n := by\n"
        "  nlinarith\n"
    )
    entries = [_make_entry("foo")]
    # Lake reports an error on line 4 (inside the body).
    result = audit_ledger_entries(
        entries,
        paper_id="test",
        lean_src=src,
        statuses=("FULLY_PROVEN",),
        lake_error_lines={4: "unknownIdentifier: foo_ax"},
    )
    assert result.demoted == 1
    assert entries[0]["status"] == "UNRESOLVED"
    assert entries[0]["audit_demotion"]["reason"] == "file_body_does_not_elaborate"
    assert entries[0]["audit_demotion"]["lake_error_lines"]
    assert any(d.reason == "file_body_does_not_elaborate" for d in result.demotions)


def test_audit_keeps_row_with_no_overlapping_lake_error() -> None:
    src = (
        "import Foo\n"
        "\n"
        "theorem foo (n : Nat) (h : 0 < n) : 0 < 2 * n := by\n"
        "  nlinarith\n"
        "\n"
        "theorem broken : 0 = 1 := by\n"
        "  sorry\n"  # would-fail line but in a different theorem
    )
    entries = [_make_entry("foo")]
    # Error is on line 7, outside foo's body range (3-4).
    result = audit_ledger_entries(
        entries,
        paper_id="test",
        lean_src=src,
        statuses=("FULLY_PROVEN",),
        lake_error_lines={7: "some_error"},
    )
    assert result.demoted == 0
    assert entries[0]["status"] == "FULLY_PROVEN"
    assert result.validated_clean == 1


def test_audit_with_no_lake_errors_preserves_clean_rows() -> None:
    src = (
        "theorem foo (n : Nat) (h : 0 < n) : 0 < 2 * n := by\n"
        "  nlinarith\n"
    )
    entries = [_make_entry("foo")]
    result = audit_ledger_entries(
        entries,
        paper_id="test",
        lean_src=src,
        statuses=("FULLY_PROVEN",),
        lake_error_lines={},  # empty error dict
    )
    assert result.demoted == 0
    assert result.validated_clean == 1


def test_audit_legacy_call_without_lake_error_lines_still_works() -> None:
    # Backward-compat: existing callers don't pass lake_error_lines.
    src = (
        "theorem foo (n : Nat) (h : 0 < n) : 0 < 2 * n := by\n"
        "  nlinarith\n"
    )
    entries = [_make_entry("foo")]
    result = audit_ledger_entries(
        entries,
        paper_id="test",
        lean_src=src,
        statuses=("FULLY_PROVEN",),
    )
    assert result.demoted == 0
    assert result.validated_clean == 1


def test_audit_demotes_axiom_invocation_bypass() -> None:
    """The original bypass that surfaced this fix: ledger claims a proof
    closed, body invokes a non-existent identifier."""
    src = (
        "theorem lem_X (a b c : Real) (h1 : 0 < a) (h2 : 0 < b) (h3 : 0 < c) : a + b + c > 0 := by\n"
        "  lem_X_ax h1 h2 h3\n"
    )
    entries = [_make_entry("lem_X")]
    result = audit_ledger_entries(
        entries,
        paper_id="test",
        lean_src=src,
        statuses=("FULLY_PROVEN",),
        lake_error_lines={2: "unknownIdentifier: lem_X_ax"},
    )
    assert result.demoted == 1
    assert entries[0]["status"] == "UNRESOLVED"


def test_audit_demotes_first_error_in_multi_line_body() -> None:
    src = (
        "theorem foo : True := by\n"
        "  have h := some_unknown_lemma\n"
        "  exact h\n"
    )
    entries = [_make_entry("foo")]
    result = audit_ledger_entries(
        entries,
        paper_id="test",
        lean_src=src,
        statuses=("FULLY_PROVEN",),
        lake_error_lines={2: "unknownIdentifier: some_unknown_lemma"},
    )
    assert result.demoted == 1


# ---------------------------------------------------------------------------
# collect_lake_error_lines: parsing
# ---------------------------------------------------------------------------


def test_collect_lake_error_lines_missing_file(tmp_path: Path) -> None:
    # When the file doesn't exist, return empty (audit falls back to
    # body-is-sorry path).
    assert collect_lake_error_lines(tmp_path / "missing.lean") == {}
