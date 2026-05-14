"""Hermetic tests for the lemma-factor-v2 sweep driver helpers.

Tests cover:
  - aux insertion above the parent in `output/<paper>.lean`
  - aux removal (rollback)
  - aux name qualification with `__factored_aux` suffix
  - aux signature rename
  - composition body selection (and / exists / iff)

No Mistral, no lake calls.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import sweep_lemma_factor_v2 as sweep


# --- _qualify_aux_name & _rename_aux_in_signature -------------------------


def test_qualify_aux_name_adds_factored_aux_suffix() -> None:
    nm = sweep._qualify_aux_name("thm_baseline", "aux_first", 1)
    assert nm.endswith("__factored_aux")
    assert "aux_first" in nm
    assert nm.startswith("thm_baseline_")


def test_qualify_aux_name_handles_special_chars() -> None:
    nm = sweep._qualify_aux_name("thm.foo", "bad name!!", 2)
    assert nm.endswith("__factored_aux")
    # All non-alnum chars should be stripped or replaced.
    body = nm.removesuffix("__factored_aux")
    assert all(ch.isalnum() or ch == "_" for ch in body)


def test_rename_aux_in_signature_replaces_head() -> None:
    sig = "theorem old_name (n : ℕ) : 0 ≤ n := by sorry"
    out = sweep._rename_aux_in_signature(sig, "new_name__factored_aux")
    assert "theorem new_name__factored_aux" in out
    assert "old_name" not in out
    # Body and binders preserved.
    assert "(n : ℕ)" in out
    assert ":= by sorry" in out


def test_rename_aux_in_signature_handles_noncomputable() -> None:
    sig = "noncomputable theorem foo (n : ℕ) : 0 ≤ n := by sorry"
    out = sweep._rename_aux_in_signature(sig, "bar__factored_aux")
    # The rename strips the `noncomputable` modifier (we only need the
    # head); body preservation is sufficient.
    assert "theorem bar__factored_aux" in out
    assert "(n : ℕ)" in out


# --- _insert_aux_lemmas_above_parent --------------------------------------


def test_insert_aux_lemmas_above_parent(tmp_path: Path) -> None:
    f = tmp_path / "test.lean"
    f.write_text(
        "import Mathlib\n"
        "namespace ArxivPaper\n"
        "\n"
        "theorem other (n : ℕ) : 0 ≤ n := by\n"
        "  exact Nat.zero_le _\n"
        "\n"
        "theorem target_parent (n : ℕ) : n + 0 = n := by\n"
        "  sorry\n"
        "\n"
        "end ArxivPaper\n",
        encoding="utf-8",
    )
    aux_sigs = [
        "theorem aux_a__factored_aux (n : ℕ) : n + 0 = n := by sorry",
        "theorem aux_b__factored_aux (n : ℕ) : 0 ≤ n := by sorry",
    ]
    inserted, lines = sweep._insert_aux_lemmas_above_parent(
        f, "target_parent", aux_sigs,
    )
    assert inserted
    assert len(lines) == 2
    text = f.read_text(encoding="utf-8")
    # Aux lines must appear ABOVE `theorem target_parent`.
    target_idx = text.find("theorem target_parent")
    aux_idx = text.find("aux_a__factored_aux")
    assert 0 <= aux_idx < target_idx
    # Both aux are present.
    assert "aux_a__factored_aux" in text
    assert "aux_b__factored_aux" in text
    # The pre-existing `theorem other` is untouched.
    assert "theorem other (n : ℕ)" in text


def test_insert_aux_lemmas_missing_parent_returns_false(tmp_path: Path) -> None:
    f = tmp_path / "test.lean"
    f.write_text("import Mathlib\n\ntheorem only : True := trivial\n", encoding="utf-8")
    inserted, lines = sweep._insert_aux_lemmas_above_parent(
        f, "nonexistent_parent", ["theorem x : True := by sorry"],
    )
    assert not inserted
    assert lines == []


# --- _remove_aux_lemmas (rollback) ----------------------------------------


def test_remove_aux_lemmas_strips_inserted_blocks(tmp_path: Path) -> None:
    f = tmp_path / "test.lean"
    f.write_text(
        "import Mathlib\n"
        "namespace ArxivPaper\n"
        "theorem aux_a__factored_aux (n : ℕ) : 0 ≤ n := by\n"
        "  exact Nat.zero_le _\n"
        "\n"
        "theorem aux_b__factored_aux (n : ℕ) : n + 0 = n := by\n"
        "  simp\n"
        "\n"
        "theorem keepme (n : ℕ) : True := by trivial\n"
        "end ArxivPaper\n",
        encoding="utf-8",
    )
    removed = sweep._remove_aux_lemmas(f, ["aux_a__factored_aux", "aux_b__factored_aux"])
    assert removed == 2
    text = f.read_text(encoding="utf-8")
    assert "aux_a__factored_aux" not in text
    assert "aux_b__factored_aux" not in text
    # Pre-existing decl untouched.
    assert "theorem keepme" in text


# --- attempt_composition: bodies for each shape ---------------------------


def test_composition_bodies_for_and_two_aux() -> None:
    bodies = sweep.lfv2.render_composition_attempts(
        parent_target_shape="and", aux_names=["a", "b"],
    )
    assert any("⟨a, b⟩" in b for b in bodies)
    assert any("constructor" in b for b in bodies)


def test_composition_bodies_for_exists_two_aux() -> None:
    bodies = sweep.lfv2.render_composition_attempts(
        parent_target_shape="exists", aux_names=["witness_pos", "bound_ok"],
    )
    assert any("⟨witness_pos, bound_ok⟩" in b for b in bodies)


# --- _is_candidate_row ----------------------------------------------------


def test_is_candidate_row_ur_with_equivalent_review() -> None:
    e = {
        "status": "UNRESOLVED",
        "lean_statement": "theorem t (n : ℕ) : 0 ≤ n := by sorry",
        "reviewed_equivalence_verdict": "equivalent",
    }
    ok, prio = sweep._is_candidate_row(e)
    assert ok is True
    assert prio == 0  # highest priority bucket


def test_is_candidate_row_skip_already_closed() -> None:
    e = {
        "status": "INTERMEDIARY_PROVEN",
        "lean_statement": "theorem t : True := by sorry",
        "validation_gates": {"lean_proof_closed": True},
    }
    ok, _ = sweep._is_candidate_row(e)
    assert ok is False


def test_is_candidate_row_skip_when_proof_text_present() -> None:
    e = {
        "status": "UNRESOLVED",
        "lean_statement": "theorem t : True := by sorry",
        "proof_text": "exact trivial",
    }
    ok, _ = sweep._is_candidate_row(e)
    assert ok is False


def test_is_candidate_row_skip_axiom_backed() -> None:
    e = {
        "status": "AXIOM_BACKED",
        "lean_statement": "theorem t : True := by sorry",
    }
    ok, _ = sweep._is_candidate_row(e)
    assert ok is False


# --- End-to-end: insert + remove round-trip ------------------------------


def test_insert_then_remove_aux_round_trip(tmp_path: Path) -> None:
    f = tmp_path / "test.lean"
    original = (
        "import Mathlib\n"
        "namespace ArxivPaper\n"
        "\n"
        "theorem target_parent (n : ℕ) : n + 0 = n := by\n"
        "  sorry\n"
        "\n"
        "end ArxivPaper\n"
    )
    f.write_text(original, encoding="utf-8")
    aux_sigs = [
        "theorem ax1__factored_aux (n : ℕ) : 0 ≤ n := by sorry",
        "theorem ax2__factored_aux (n : ℕ) : n + 0 = n := by sorry",
    ]
    inserted, _ = sweep._insert_aux_lemmas_above_parent(f, "target_parent", aux_sigs)
    assert inserted
    removed = sweep._remove_aux_lemmas(f, ["ax1__factored_aux", "ax2__factored_aux"])
    assert removed == 2
    final = f.read_text(encoding="utf-8")
    # The parent declaration must remain intact.
    assert "theorem target_parent (n : ℕ) : n + 0 = n" in final
    assert "ax1__factored_aux" not in final
    assert "ax2__factored_aux" not in final
