"""Hermetic tests for `scripts/route_to_axiom_backed.py`.

The detector must:
  1. Route to AXIOM_BACKED when a lake error mentions a name declared
     as `axiom <name>` in the paper-theory file.
  2. Route to AXIOM_BACKED when the name is declared as a stubby `def`
     (body in {0, True, False, sorry, Set.univ}).
  3. NOT route when the name is declared as a `def` with a non-trivial
     body -- ordinary proof search should solve those.
  4. Surface ALL matched axiom names in the debt list (multi-axiom error).
  5. Return None for empty / unrelated errors.
  6. Return None when the paper-theory file is absent / unreadable.

Plus tests for `parse_paper_theory_symbols`, `extract_candidate_names_from_error`,
and `apply_route_to_entry`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from route_to_axiom_backed import (
    PaperTheorySymbol,
    apply_route_to_entry,
    detect_paper_axiom_block,
    extract_candidate_names_from_error,
    is_opaque_paper_axiom,
    parse_paper_theory_symbols,
)


PT_HEADER = """\
import Mathlib
import Aesop

namespace Paper_Test

"""

PT_FOOTER = "\nend Paper_Test\n"


def _write_paper_theory(tmp_path: Path, body: str) -> Path:
    """Materialise a Paper_<id>.lean in tmp_path. Returns the file path."""
    p = tmp_path / "Paper_Test.lean"
    p.write_text(PT_HEADER + body + PT_FOOTER, encoding="utf-8")
    return p


# --- Parse-paper-theory unit tests -----------------------------------------


def test_parse_paper_theory_picks_up_axioms_and_stubs(tmp_path: Path) -> None:
    pt = _write_paper_theory(tmp_path, """\
axiom n_tilde_alpha : Nat
def S_alpha : Nat := 0
def c_alpha : Nat := 0
def real_thing : Nat := 42
axiom paper_inj (a b : Nat) : a = b -> a = b
""")
    syms = parse_paper_theory_symbols(pt.read_text(encoding="utf-8"))
    assert syms["n_tilde_alpha"].kind == "axiom"
    assert syms["S_alpha"].kind == "stub_def"
    assert syms["c_alpha"].kind == "stub_def"
    assert syms["real_thing"].kind == "real_def"
    assert syms["paper_inj"].kind == "axiom"


def test_is_opaque_paper_axiom_treats_stubs_and_axioms_as_opaque() -> None:
    assert is_opaque_paper_axiom(PaperTheorySymbol("a", "axiom", ""))
    assert is_opaque_paper_axiom(PaperTheorySymbol("a", "stub_def", "0"))
    assert not is_opaque_paper_axiom(PaperTheorySymbol("a", "real_def", "x + 1"))


# --- Error-extraction tests ------------------------------------------------


def test_extract_candidate_names_pulls_unfold_failures() -> None:
    err = "error: tactic 'unfold' failed because constant 'n_tilde_alpha' has no unfolding"
    cands = extract_candidate_names_from_error(err)
    assert "n_tilde_alpha" in cands


def test_extract_candidate_names_pulls_multiple_from_one_tail() -> None:
    err = (
        "error: failed to unfold S_alpha\n"
        "error: tactic 'unfold' failed because constant 'c_alpha' has no body\n"
        "error: term 'n_tilde_alpha' is opaque"
    )
    cands = extract_candidate_names_from_error(err)
    assert {"S_alpha", "c_alpha", "n_tilde_alpha"}.issubset(set(cands))


def test_extract_candidate_names_empty_for_empty_error() -> None:
    assert extract_candidate_names_from_error("") == []
    assert extract_candidate_names_from_error("\n\n") == []


# --- detect_paper_axiom_block tests ----------------------------------------


def test_detect_routes_axiom_block_named_axiom(tmp_path: Path) -> None:
    pt = _write_paper_theory(tmp_path, "axiom n_tilde_alpha : Nat\n")
    lake_err = (
        "/tmp/foo.lean:3:7: error: tactic 'unfold' failed because constant "
        "'n_tilde_alpha' has no unfolding"
    )
    route = detect_paper_axiom_block(
        paper_id="test",
        theorem_name="Lem_Test",
        lean_statement="theorem Lem_Test : n_tilde_alpha = 0 := by sorry",
        lake_error=lake_err,
        paper_theory_file=pt,
    )
    assert route is not None
    assert route["route_to"] == "AXIOM_BACKED"
    assert "n_tilde_alpha" in route["axiom_debt"]
    assert "reasoning" in route


def test_detect_routes_when_stub_def_blocks(tmp_path: Path) -> None:
    pt = _write_paper_theory(tmp_path, "def S_alpha (_x : Nat) : Nat := 0\n")
    lake_err = (
        "error: failed to unfold S_alpha\n"
        "  in goal:\n"
        "    ⊢ S_alpha x = 0"
    )
    route = detect_paper_axiom_block(
        paper_id="test",
        theorem_name="Lem_Stub",
        lean_statement="theorem Lem_Stub (x : Nat) : S_alpha x = 0 := by sorry",
        lake_error=lake_err,
        paper_theory_file=pt,
    )
    assert route is not None
    assert "S_alpha" in route["axiom_debt"]


def test_detect_does_not_route_real_def(tmp_path: Path) -> None:
    """A name backed by a real definition is not a paper-axiom block; the
    proof should be discoverable via ordinary tactic search."""
    pt = _write_paper_theory(tmp_path, "def real_thing (x : Nat) : Nat := x + 1\n")
    lake_err = "error: failed to unfold real_thing"
    route = detect_paper_axiom_block(
        paper_id="test",
        theorem_name="Lem_Real",
        lean_statement="theorem Lem_Real (x : Nat) : real_thing x = x + 1 := by sorry",
        lake_error=lake_err,
        paper_theory_file=pt,
    )
    assert route is None


def test_detect_collects_multiple_axiom_names(tmp_path: Path) -> None:
    pt = _write_paper_theory(tmp_path, """\
axiom n_tilde_alpha : Nat
def S_alpha : Nat := 0
def c_alpha : Nat := 0
""")
    lake_err = (
        "error: failed to unfold n_tilde_alpha\n"
        "error: tactic 'unfold' failed because constant 'S_alpha' has no body\n"
        "error: c_alpha has no unfolding"
    )
    route = detect_paper_axiom_block(
        paper_id="test",
        theorem_name="Lem_Multi",
        lean_statement="theorem Lem_Multi : n_tilde_alpha + S_alpha + c_alpha = 0 := by sorry",
        lake_error=lake_err,
        paper_theory_file=pt,
    )
    assert route is not None
    assert set(route["axiom_debt"]) == {"n_tilde_alpha", "S_alpha", "c_alpha"}


def test_detect_returns_none_for_empty_error(tmp_path: Path) -> None:
    pt = _write_paper_theory(tmp_path, "axiom n_tilde_alpha : Nat\n")
    assert detect_paper_axiom_block(
        paper_id="test",
        theorem_name="Lem_X",
        lean_statement="theorem Lem_X : 1 = 1 := by sorry",
        lake_error="",
        paper_theory_file=pt,
    ) is None
    assert detect_paper_axiom_block(
        paper_id="test",
        theorem_name="Lem_X",
        lean_statement="theorem Lem_X : 1 = 1 := by sorry",
        lake_error="   \n",
        paper_theory_file=pt,
    ) is None


def test_detect_returns_none_when_error_mentions_only_mathlib_names(
    tmp_path: Path,
) -> None:
    """A lake error referring to Mathlib-level identifiers (not in
    paper-theory) must not route."""
    pt = _write_paper_theory(tmp_path, "axiom n_tilde_alpha : Nat\n")
    lake_err = (
        "error: unknown identifier 'Nat.add_comm'\n"
        "error: failed to unfold Real.cos"
    )
    assert detect_paper_axiom_block(
        paper_id="test",
        theorem_name="Lem_X",
        lean_statement="theorem Lem_X : 1 = 1 := by sorry",
        lake_error=lake_err,
        paper_theory_file=pt,
    ) is None


def test_detect_returns_none_when_paper_theory_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.lean"
    assert detect_paper_axiom_block(
        paper_id="test",
        theorem_name="Lem_X",
        lean_statement="theorem Lem_X : 1 = 1 := by sorry",
        lake_error="error: failed to unfold n_tilde_alpha",
        paper_theory_file=missing,
    ) is None


# --- apply_route_to_entry tests --------------------------------------------


def test_apply_route_mutates_entry_to_axiom_backed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The router writes status=AB, expands axiom_debt with canonical
    prefixes, fails the no_paper_axiom_debt gate, and appends an
    audit_trail entry."""
    # Stand up a fake paper-theory file so apply_route_to_entry classifies
    # 'n_tilde_alpha' as an axiom (paper_axiom: prefix) and 'S_alpha' as
    # a stub-def (paper_definition_stub: prefix).
    pt_dir = tmp_path / "Desol" / "PaperTheory"
    pt_dir.mkdir(parents=True)
    pt = pt_dir / "Paper_test.lean"
    pt.write_text(
        PT_HEADER
        + "axiom n_tilde_alpha : Nat\ndef S_alpha : Nat := 0\n"
        + PT_FOOTER,
        encoding="utf-8",
    )
    import route_to_axiom_backed as mod
    monkeypatch.setattr(mod, "DEFAULT_PAPER_THEORY_DIR", pt_dir)

    entry: dict = {
        "theorem_name": "Lem_X",
        "status": "UNRESOLVED",
        "axiom_debt": ["paper_definition_stub:Existing"],
        "gate_failures": ["lean_proof_closed"],
        "validation_gates": {"lean_proof_closed": False},
    }
    route = {
        "route_to": "AXIOM_BACKED",
        "axiom_debt": ["n_tilde_alpha", "S_alpha"],
        "reasoning": "test",
    }
    apply_route_to_entry(entry, route=route, paper_id="test", lake_error_preview="err")

    assert entry["status"] == "AXIOM_BACKED"
    assert "paper_axiom:n_tilde_alpha" in entry["axiom_debt"]
    assert "paper_definition_stub:S_alpha" in entry["axiom_debt"]
    # Pre-existing debt preserved (deduplicated).
    assert "paper_definition_stub:Existing" in entry["axiom_debt"]
    assert entry["validation_gates"]["no_paper_axiom_debt"] is False
    assert "no_paper_axiom_debt" in entry["gate_failures"]
    trail = entry["audit_trail"]
    assert isinstance(trail, list) and len(trail) == 1
    assert trail[0]["route_to"] == "AXIOM_BACKED"
    assert trail[0]["detected_axiom_names"] == ["n_tilde_alpha", "S_alpha"]
    assert "paper_axiom:n_tilde_alpha" in trail[0]["canonical_axiom_debt"]
    assert "route_to_axiom_backed:paper_local_opacity" in entry["claim_equivalence_notes"]


def test_apply_route_preserves_audit_trail_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pt_dir = tmp_path / "Desol" / "PaperTheory"
    pt_dir.mkdir(parents=True)
    (pt_dir / "Paper_test.lean").write_text(
        PT_HEADER + "axiom n_tilde_alpha : Nat\n" + PT_FOOTER,
        encoding="utf-8",
    )
    import route_to_axiom_backed as mod
    monkeypatch.setattr(mod, "DEFAULT_PAPER_THEORY_DIR", pt_dir)

    entry: dict = {
        "theorem_name": "Lem_Y",
        "status": "UNRESOLVED",
        "audit_trail": [{"prior": "record"}],
    }
    apply_route_to_entry(
        entry,
        route={
            "route_to": "AXIOM_BACKED",
            "axiom_debt": ["n_tilde_alpha"],
            "reasoning": "z",
        },
        paper_id="test",
    )
    assert len(entry["audit_trail"]) == 2
    assert entry["audit_trail"][0] == {"prior": "record"}
