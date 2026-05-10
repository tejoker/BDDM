"""Tests for rewrite_lean_from_ledger."""

from __future__ import annotations

import json
from pathlib import Path

from rewrite_lean_from_ledger import (
    _is_trivial_signature,
    _strip_proof_body,
    rewrite_paper,
)


def _setup(tmp_path: Path, paper_id: str, lean_text: str, ledger_entries: list[dict]) -> tuple[Path, Path]:
    out = tmp_path / "output"
    out.mkdir()
    lean_path = out / f"{paper_id}.lean"
    lean_path.write_text(lean_text, encoding="utf-8")
    ledger_dir = out / "verification_ledgers"
    ledger_dir.mkdir()
    ledger_path = ledger_dir / f"{paper_id}.json"
    ledger_path.write_text(json.dumps({"entries": ledger_entries}, indent=2), encoding="utf-8")
    return lean_path, ledger_path


def test_is_trivial_signature_classifies_basic_cases() -> None:
    """Trivial = conclusion is False/True/(x=x). These signatures add no info
    beyond the existing placeholder, so rewriting is a no-op."""
    assert _is_trivial_signature("theorem foo : False := by sorry")
    assert _is_trivial_signature("theorem foo : True := by sorry")
    # No parameters AND trivial conclusion → trivial.
    assert _is_trivial_signature("theorem foo : ∃ x : ℝ, x = x := by sorry")
    # Even with parameters, trivial-conclusion signatures aren't worth rewriting
    # to (proof search would still face the same trivial goal).
    assert _is_trivial_signature("theorem foo (x : Nat) : ∃ x : ℝ, x = x := by sorry")
    # Real theorem with non-trivial conclusion → NOT trivial.
    assert _is_trivial_signature("theorem foo (x : Nat) : x + 0 = x := by sorry") is False
    assert _is_trivial_signature("theorem foo (n : Nat) (h : 0 < n) : ∃ k, k ≥ n := by sorry") is False
    assert _is_trivial_signature("") is True


def test_strip_proof_body_handles_by_sorry() -> None:
    s = "theorem foo (x : Nat) : x = x := by sorry"
    assert _strip_proof_body(s) == "theorem foo (x : Nat) : x = x"


def test_strip_proof_body_handles_multi_line_proof() -> None:
    s = "theorem foo (x : Nat) : x = x := by\n  rfl"
    assert _strip_proof_body(s) == "theorem foo (x : Nat) : x = x"


def test_rewrite_paper_replaces_placeholder_with_ledger_signature(tmp_path: Path) -> None:
    """The core path: a placeholder `theorem foo : False := by sorry` line is
    replaced by the full signature stored in the ledger."""
    lean_text = (
        "import Mathlib\n"
        "namespace ArxivPaper\n"
        "\n"
        "-- [theorem] foo\n"
        "-- Translation: BLOCKED — claim_shape_mismatch:ineq->exists\n"
        "theorem foo : False := by sorry\n"
        "\n"
        "-- [theorem] bar\n"
        "theorem bar (n : Nat) : n + 0 = n := by rfl\n"
        "\n"
        "end ArxivPaper\n"
    )
    ledger_entries = [
        {
            "theorem_name": "foo",
            "lean_statement": "theorem foo (n : Nat) (hn : 0 < n) : ∃ k, k ≥ n := by sorry",
            "status": "UNRESOLVED",
        },
        {
            "theorem_name": "bar",  # already has a real proof, not a placeholder
            "lean_statement": "theorem bar (n : Nat) : n + 0 = n := by rfl",
            "status": "FULLY_PROVEN",
        },
    ]
    lean_path, _ = _setup(tmp_path, "2222.22222", lean_text, ledger_entries)
    summary = rewrite_paper("2222.22222", project_root=tmp_path, write=True)
    assert summary["placeholders_found"] == 1
    assert summary["rewritten"] == 1
    new_text = lean_path.read_text()
    assert "theorem foo (n : Nat) (hn : 0 < n) : ∃ k, k ≥ n := by sorry" in new_text
    assert "theorem foo : False" not in new_text
    # `bar` was not a placeholder; left untouched.
    assert "theorem bar (n : Nat) : n + 0 = n := by rfl" in new_text


def test_rewrite_paper_skips_when_ledger_has_no_signature(tmp_path: Path) -> None:
    """If the ledger row has no usable signature for a placeholder, leave the
    placeholder in place and report skipped_no_ledger_signature."""
    lean_text = "theorem foo : False := by sorry\n"
    ledger_entries = [{"theorem_name": "foo", "lean_statement": "theorem foo : False := by sorry"}]
    lean_path, _ = _setup(tmp_path, "p1", lean_text, ledger_entries)
    summary = rewrite_paper("p1", project_root=tmp_path, write=True)
    assert summary["placeholders_found"] == 1
    assert summary["rewritten"] == 0
    assert summary["skipped_no_ledger_signature"] == 1
    # File unchanged.
    assert lean_path.read_text() == lean_text


def test_rewrite_paper_dry_run_does_not_write(tmp_path: Path) -> None:
    lean_text = "theorem foo : False := by sorry\n"
    ledger_entries = [{
        "theorem_name": "foo",
        "lean_statement": "theorem foo (n : Nat) : n = n := by sorry",
    }]
    lean_path, _ = _setup(tmp_path, "p1", lean_text, ledger_entries)
    summary = rewrite_paper("p1", project_root=tmp_path, write=False)
    assert summary["rewritten"] == 1
    # File unchanged in dry-run.
    assert lean_path.read_text() == lean_text


def test_rewrite_paper_handles_true_trivial_placeholder(tmp_path: Path) -> None:
    """`theorem foo : True := trivial` is also a placeholder pattern that
    should be rewritten when the ledger has a real signature."""
    lean_text = (
        "namespace ArxivPaper\n"
        "-- [theorem] foo\n"
        "theorem foo : True := trivial\n"
        "end ArxivPaper\n"
    )
    ledger_entries = [{
        "theorem_name": "foo",
        "lean_statement": "theorem foo (a b : Nat) (h : a < b) : ∃ c, a < c ∧ c < b ∨ b = a + 1 := by sorry",
    }]
    lean_path, _ = _setup(tmp_path, "p1", lean_text, ledger_entries)
    summary = rewrite_paper("p1", project_root=tmp_path, write=True)
    assert summary["rewritten"] == 1
    new_text = lean_path.read_text()
    assert "theorem foo (a b : Nat)" in new_text
    assert "theorem foo : True := trivial" not in new_text


def test_rewrite_paper_creates_backup(tmp_path: Path) -> None:
    """When write=True and rewrites happen, the original file is backed up."""
    lean_text = "theorem foo : False := by sorry\n"
    ledger_entries = [{
        "theorem_name": "foo",
        "lean_statement": "theorem foo (n : Nat) : n = n := by sorry",
    }]
    lean_path, _ = _setup(tmp_path, "p1", lean_text, ledger_entries)
    rewrite_paper("p1", project_root=tmp_path, write=True)
    backup = lean_path.with_suffix(".lean.bak.ledger_rewrite")
    assert backup.exists()
    assert backup.read_text() == lean_text


def test_rewrite_paper_strips_existing_proof_body(tmp_path: Path) -> None:
    """If the ledger's stored signature already includes `:= by …`, the rewrite
    must strip that and emit a clean `:= by sorry` so proof search starts fresh."""
    lean_text = "theorem foo : False := by sorry\n"
    ledger_entries = [{
        "theorem_name": "foo",
        # Stored signature has a proof body that should be stripped.
        "lean_statement": "theorem foo (n : Nat) : n = n := by\n  rfl\n",
    }]
    lean_path, _ = _setup(tmp_path, "p1", lean_text, ledger_entries)
    rewrite_paper("p1", project_root=tmp_path, write=True)
    new_text = lean_path.read_text()
    assert "theorem foo (n : Nat) : n = n := by sorry" in new_text
    # The `rfl` from the stored proof must NOT have leaked into the output.
    assert "rfl" not in new_text
