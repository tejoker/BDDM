"""Hermetic tests for the sorry-dependent factored-aux deflation pass.

No lake calls, no Mistral. All file content constructed in tmp_path.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import deflate_sorry_dependent_factored as defl


def test_scan_sorry_bodied_factored_aux() -> None:
    text = (
        "theorem closed_aux__factored_aux (n : ℕ) : 0 ≤ n := by\n"
        "  exact Nat.zero_le _\n"
        "\n"
        "theorem sorry_aux__factored_aux (n : ℕ) : True := by sorry\n"
        "\n"
        "theorem regular_decl (n : ℕ) : True := by trivial\n"
    )
    sorries = defl._scan_sorry_bodied_factored_aux(text)
    assert sorries == {"sorry_aux__factored_aux"}


def test_references_sorry_aux_finds_apply_invocation() -> None:
    proof = "apply sorry_aux__factored_aux x y; exact rfl"
    refs = defl._references_sorry_aux(proof, {"sorry_aux__factored_aux", "other__factored_aux"})
    assert refs == {"sorry_aux__factored_aux"}


def test_references_sorry_aux_empty_proof_returns_empty() -> None:
    assert defl._references_sorry_aux("", {"foo__factored_aux"}) == set()


def test_references_sorry_aux_ignores_closed_aux() -> None:
    refs = defl._references_sorry_aux(
        "apply closed_aux__factored_aux x; rfl",
        {"sorry_aux__factored_aux"},
    )
    assert refs == set()


def test_revert_parent_in_file_multi_line(tmp_path: Path) -> None:
    text = (
        "import Mathlib\n"
        "namespace ArxivPaper\n"
        "theorem parent (n : ℕ) : True := by\n"
        "  apply bad__factored_aux\n"
        "  rfl\n"
        "\n"
        "theorem next_decl : True := by trivial\n"
        "end ArxivPaper\n"
    )
    new, modified = defl._revert_parent_in_file(text, "parent")
    assert modified
    assert "apply bad__factored_aux" not in new
    assert ":= by\n  sorry\n" in new
    # next_decl unchanged.
    assert "theorem next_decl : True := by trivial" in new


def test_strip_all_factored_aux_removes_all_marked_decls() -> None:
    text = (
        "theorem keep_me (n : ℕ) : True := by trivial\n"
        "theorem a__factored_aux (n : ℕ) : True := by sorry\n"
        "theorem b__factored_aux (n : ℕ) : True := by\n"
        "  exact trivial\n"
        "\n"
        "theorem keep_me_too : True := trivial\n"
    )
    new, count = defl._strip_all_factored_aux(text)
    assert count == 2
    assert "a__factored_aux" not in new
    assert "b__factored_aux" not in new
    assert "theorem keep_me" in new
    assert "theorem keep_me_too" in new


def test_deflate_paper_end_to_end(tmp_path: Path) -> None:
    """Conservative deflation: ANY parent referencing a `__factored_aux`
    lemma is reverted, because the aux closure was not transitively
    sorry-checked.
    """
    project = tmp_path
    out_dir = project / "output"
    out_dir.mkdir(parents=True)
    led_dir = out_dir / "verification_ledgers"
    led_dir.mkdir()
    lean_file = out_dir / "9999.0001.lean"
    lean_file.write_text(
        "import Mathlib\n"
        "namespace ArxivPaper\n"
        "\n"
        "theorem fake_aux__factored_aux (n : ℕ) : 0 < n + 1 := by sorry\n"
        "\n"
        "theorem closed_aux__factored_aux (n : ℕ) : n + 0 = n := by\n"
        "  simp\n"
        "\n"
        "theorem hollow_parent (n : ℕ) : 0 < n + 1 := by\n"
        "  apply fake_aux__factored_aux\n"
        "\n"
        "theorem aux_dependent_parent (n : ℕ) : n + 0 = n := by\n"
        "  apply closed_aux__factored_aux\n"
        "\n"
        "theorem standalone_parent (n : ℕ) : True := by trivial\n"
        "\n"
        "end ArxivPaper\n",
        encoding="utf-8",
    )
    led_file = led_dir / "9999.0001.json"
    entries = [
        {
            "theorem_name": "hollow_parent",
            "status": "AXIOM_BACKED",
            "proof_text": "apply fake_aux__factored_aux",
            "validation_gates": {"lean_proof_closed": True, "step_verdict_verified": True},
            "gate_failures": [],
        },
        {
            "theorem_name": "aux_dependent_parent",
            "status": "AXIOM_BACKED",
            "proof_text": "apply closed_aux__factored_aux",
            "validation_gates": {"lean_proof_closed": True, "step_verdict_verified": True},
            "gate_failures": [],
        },
        {
            "theorem_name": "standalone_parent",
            "status": "FULLY_PROVEN",
            "proof_text": "trivial",
            "validation_gates": {"lean_proof_closed": True},
            "gate_failures": [],
        },
    ]
    led_file.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    summary = defl.deflate_paper("9999.0001", write=True, project_root=project)
    assert summary["sorry_factored_aux"] == 1
    # Both parents that referenced factored_aux are deflated.
    assert summary["deflated_rows"] == 2
    assert set(summary["deflated_names"]) == {"hollow_parent", "aux_dependent_parent"}
    final_lean = lean_file.read_text(encoding="utf-8")
    # All factored_aux removed.
    assert "fake_aux__factored_aux" not in final_lean
    assert "closed_aux__factored_aux" not in final_lean
    # Standalone parent untouched.
    assert "theorem standalone_parent (n : ℕ) : True := by trivial" in final_lean
    final_ledger = json.loads(led_file.read_text())
    by_name = {e["theorem_name"]: e for e in final_ledger}
    assert by_name["hollow_parent"]["status"] == "UNRESOLVED"
    assert by_name["aux_dependent_parent"]["status"] == "UNRESOLVED"
    # The clean parent that didn't touch factored_aux is untouched.
    assert by_name["standalone_parent"]["status"] == "FULLY_PROVEN"


def test_deflate_paper_no_write_does_not_modify(tmp_path: Path) -> None:
    project = tmp_path
    (project / "output").mkdir(parents=True)
    (project / "output" / "verification_ledgers").mkdir()
    lean_file = project / "output" / "9999.0002.lean"
    lean_file.write_text(
        "theorem aux__factored_aux : True := by sorry\n"
        "theorem parent : True := by apply aux__factored_aux\n",
        encoding="utf-8",
    )
    led_file = project / "output" / "verification_ledgers" / "9999.0002.json"
    led_file.write_text(json.dumps([{
        "theorem_name": "parent",
        "status": "AXIOM_BACKED",
        "proof_text": "apply aux__factored_aux",
        "validation_gates": {"lean_proof_closed": True},
    }]), encoding="utf-8")
    original_lean = lean_file.read_text(encoding="utf-8")
    original_ledger = led_file.read_text(encoding="utf-8")
    summary = defl.deflate_paper("9999.0002", write=False, project_root=project)
    # Detected, but no file change.
    assert summary["deflated_rows"] == 1
    assert lean_file.read_text(encoding="utf-8") == original_lean
    assert led_file.read_text(encoding="utf-8") == original_ledger


def test_deflate_paper_no_factored_aux_is_noop(tmp_path: Path) -> None:
    project = tmp_path
    (project / "output").mkdir(parents=True)
    (project / "output" / "verification_ledgers").mkdir()
    lean_file = project / "output" / "9999.0003.lean"
    lean_file.write_text(
        "theorem clean_parent (n : ℕ) : 0 ≤ n := by exact Nat.zero_le _\n",
        encoding="utf-8",
    )
    led_file = project / "output" / "verification_ledgers" / "9999.0003.json"
    led_file.write_text(json.dumps([{
        "theorem_name": "clean_parent",
        "status": "FULLY_PROVEN",
        "proof_text": "exact Nat.zero_le _",
        "validation_gates": {"lean_proof_closed": True},
    }]), encoding="utf-8")
    summary = defl.deflate_paper("9999.0003", write=True, project_root=project)
    assert summary["sorry_factored_aux"] == 0
    assert summary["deflated_rows"] == 0
    assert summary["removed_aux_decls"] == 0
