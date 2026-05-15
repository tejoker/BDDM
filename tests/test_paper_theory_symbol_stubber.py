"""Hermetic tests for paper_theory_symbol_stubber.

The stubber extends `signature_typeclass_patcher` from missing typeclass
instances to ALL missing paper-local symbols. It parses `unknown
identifier 'X'` / `unknown constant 'X'` from lake errors, drops names
already declared in paper-theory or resolvable against Mathlib, infers
the right kind+signature from usage, and emits a stub.

All tests are hermetic: no lake, no network, no Mistral.
"""
from __future__ import annotations

from pathlib import Path

import paper_theory_symbol_stubber as stubber


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_paper_theory(tmp_path: Path, paper_id: str, body: str = "") -> Path:
    """Build a minimal paper-theory file with `body` between the namespace
    block. Returns the on-disk path.
    """
    pid = paper_id.replace(".", "_")
    path = tmp_path / f"Paper_{pid}.lean"
    path.write_text(
        "import Mathlib\nimport Aesop\n\n"
        f"namespace Paper_{pid}\n\n"
        f"{body}\n\n"
        f"end Paper_{pid}\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# extract_unknown_identifiers_from_error (re-export sanity)
# ---------------------------------------------------------------------------


def test_extract_unknown_identifiers_recognises_both_phrasings() -> None:
    err = (
        "error: unknown identifier 'foo'\n"
        "error: Unknown constant `Paper_X.bar`\n"
    )
    names = stubber.extract_unknown_identifiers_from_error(err)
    assert set(names) == {"foo", "Paper_X.bar"}


def test_extract_unknown_identifiers_empty_input_returns_empty() -> None:
    assert stubber.extract_unknown_identifiers_from_error("") == []
    assert stubber.extract_unknown_identifiers_from_error("error: tactic failed") == []


# ---------------------------------------------------------------------------
# propose_paper_theory_stubs — basic acceptance / rejection
# ---------------------------------------------------------------------------


def test_propose_emits_stub_when_name_unknown_and_not_in_mathlib(tmp_path: Path) -> None:
    pt = _make_paper_theory(tmp_path, "X")
    proposals = stubber.propose_paper_theory_stubs(
        paper_id="X",
        theorem_name="thm",
        lean_statement="theorem thm : weirdSymbol = weirdSymbol := by rfl",
        baseline_error="error: unknown identifier 'weirdSymbol'\n",
        paper_theory_file=pt,
        mathlib_name_index={"entries": [], "by_last": {}},
    )
    assert proposals, "expected a stub proposal"
    assert proposals[0]["name"] == "weirdSymbol"
    assert "weirdSymbol" in proposals[0]["signature"]


def test_propose_skips_name_already_in_paper_theory(tmp_path: Path) -> None:
    pt = _make_paper_theory(tmp_path, "X", body="def alreadyHere : Prop := True")
    proposals = stubber.propose_paper_theory_stubs(
        paper_id="X",
        theorem_name="thm",
        lean_statement="theorem thm : alreadyHere := by trivial",
        baseline_error="error: unknown identifier 'alreadyHere'\n",
        paper_theory_file=pt,
        mathlib_name_index={"entries": [], "by_last": {}},
    )
    assert proposals == []


def test_propose_skips_name_in_mathlib_index(tmp_path: Path) -> None:
    pt = _make_paper_theory(tmp_path, "X")
    mathlib_idx = {
        "entries": [{"name": "Matrix.dotProduct", "last": "dotProduct",
                     "module": "Mathlib.LinearAlgebra.Matrix", "kind": "def"}],
        "by_last": {"dotProduct": [0]},
    }
    proposals = stubber.propose_paper_theory_stubs(
        paper_id="X",
        theorem_name="thm",
        lean_statement="theorem thm (v w : Fin 3 → ℝ) : Matrix.dotProduct v w = 0 := by sorry",
        baseline_error="error: unknown identifier 'Matrix.dotProduct'\n",
        paper_theory_file=pt,
        mathlib_name_index=mathlib_idx,
    )
    assert proposals == []


def test_propose_returns_empty_when_no_unknown_marker(tmp_path: Path) -> None:
    pt = _make_paper_theory(tmp_path, "X")
    proposals = stubber.propose_paper_theory_stubs(
        paper_id="X",
        theorem_name="thm",
        lean_statement="theorem thm : True := trivial",
        baseline_error="error: tactic 'omega' failed\n",
        paper_theory_file=pt,
    )
    assert proposals == []


def test_propose_emits_multiple_for_multiple_unknowns(tmp_path: Path) -> None:
    pt = _make_paper_theory(tmp_path, "X")
    proposals = stubber.propose_paper_theory_stubs(
        paper_id="X",
        theorem_name="thm",
        lean_statement=(
            "theorem thm (n : ℕ) : sigmaTerm n + tauTerm n = 0 := by sorry"
        ),
        baseline_error=(
            "error: unknown identifier 'sigmaTerm'\n"
            "error: unknown identifier 'tauTerm'\n"
        ),
        paper_theory_file=pt,
        mathlib_name_index={"entries": [], "by_last": {}},
    )
    names = {p["name"] for p in proposals}
    assert names == {"sigmaTerm", "tauTerm"}


# ---------------------------------------------------------------------------
# Usage-pattern inference
# ---------------------------------------------------------------------------


def test_propose_application_pattern_emits_axiom_with_binders(tmp_path: Path) -> None:
    pt = _make_paper_theory(tmp_path, "X")
    proposals = stubber.propose_paper_theory_stubs(
        paper_id="X",
        theorem_name="thm",
        lean_statement=(
            "theorem thm (a b : ℕ) : myFunc a b = 0 := by sorry"
        ),
        baseline_error="error: unknown identifier 'myFunc'\n",
        paper_theory_file=pt,
        mathlib_name_index={"entries": [], "by_last": {}},
    )
    assert proposals
    sig = proposals[0]["signature"]
    assert sig.startswith("axiom myFunc")
    # Should bind at least one argument placeholder + a universe-poly
    # type-binder per arg so the axiom elaborates without forcing a
    # concrete type at the stub site.
    assert "_a1" in sig
    assert "Sort _" in sig
    assert sig.endswith(": Prop")


def test_propose_prop_position_emits_def_prop_true(tmp_path: Path) -> None:
    pt = _make_paper_theory(tmp_path, "X")
    proposals = stubber.propose_paper_theory_stubs(
        paper_id="X",
        theorem_name="thm",
        lean_statement=(
            "theorem thm (n : ℕ) (h : IsParaWeird) : n = n := by rfl"
        ),
        baseline_error="error: unknown identifier 'IsParaWeird'\n",
        paper_theory_file=pt,
        mathlib_name_index={"entries": [], "by_last": {}},
    )
    assert proposals
    sig = proposals[0]["signature"]
    assert sig == "def IsParaWeird : Prop := True"


def test_propose_returns_dotted_name_as_last_component(tmp_path: Path) -> None:
    """`Paper_X.ofSegments` should produce a stub named `ofSegments` so it
    lands inside the namespace block correctly.
    """
    pt = _make_paper_theory(tmp_path, "X")
    proposals = stubber.propose_paper_theory_stubs(
        paper_id="X",
        theorem_name="thm",
        lean_statement="theorem thm : alpha = Multisegment.ofSegments seg := by sorry",
        baseline_error="error: Unknown constant `Paper_X.Multisegment.ofSegments`\n",
        paper_theory_file=pt,
        mathlib_name_index={"entries": [], "by_last": {}},
    )
    assert proposals
    # The stub declared inside the namespace uses the LAST component only.
    assert proposals[0]["name"] == "ofSegments"
    # And it carries a `qualifier="Multisegment"` so the rendering helper
    # can wrap it in `namespace Multisegment ... end Multisegment`.
    assert proposals[0].get("qualifier") == "Multisegment"


def test_render_stubs_block_wraps_qualified_stubs_in_namespace() -> None:
    stubs = [
        {"name": "ofSegments", "qualifier": "Multisegment", "kind": "axiom",
         "signature": "axiom ofSegments {_T1 : Sort _} (_a1 : _T1) : Prop",
         "rationale": "test"},
        {"name": "freeForm", "qualifier": "", "kind": "axiom",
         "signature": "axiom freeForm : Prop", "rationale": "test"},
    ]
    block = stubber.render_stubs_block(stubs)
    # Qualified group is wrapped.
    assert "namespace Multisegment" in block
    assert "end Multisegment" in block
    # Order: the qualified-namespace block opens BEFORE the stub head.
    assert block.index("namespace Multisegment") < block.index("axiom ofSegments")
    assert block.index("axiom ofSegments") < block.index("end Multisegment")
    # Free-form stub is NOT inside the Multisegment namespace.
    assert block.index("end Multisegment") < block.index("axiom freeForm")


# ---------------------------------------------------------------------------
# Validator handling
# ---------------------------------------------------------------------------


def test_propose_rejects_when_validator_says_no(tmp_path: Path) -> None:
    pt = _make_paper_theory(tmp_path, "X")
    proposals = stubber.propose_paper_theory_stubs(
        paper_id="X",
        theorem_name="thm",
        lean_statement="theorem thm : weirdSymbol = weirdSymbol := rfl",
        baseline_error="error: unknown identifier 'weirdSymbol'\n",
        paper_theory_file=pt,
        mathlib_name_index={"entries": [], "by_last": {}},
        validate=lambda _stubs: (False, "rejected for testing"),
    )
    assert proposals == []


def test_propose_accepts_when_validator_says_yes(tmp_path: Path) -> None:
    pt = _make_paper_theory(tmp_path, "X")
    proposals = stubber.propose_paper_theory_stubs(
        paper_id="X",
        theorem_name="thm",
        lean_statement="theorem thm : weirdSymbol = weirdSymbol := rfl",
        baseline_error="error: unknown identifier 'weirdSymbol'\n",
        paper_theory_file=pt,
        mathlib_name_index={"entries": [], "by_last": {}},
        validate=lambda _stubs: (True, ""),
    )
    assert proposals
    assert proposals[0]["name"] == "weirdSymbol"


# ---------------------------------------------------------------------------
# Rendering + insertion
# ---------------------------------------------------------------------------


def test_render_stubs_block_includes_aesop_attribute_for_axioms() -> None:
    stubs = [
        {"name": "fooSymbol", "kind": "axiom",
         "signature": "axiom fooSymbol : _",
         "rationale": "test"},
        {"name": "barProp", "kind": "def",
         "signature": "def barProp : Prop := True",
         "rationale": "test"},
    ]
    block = stubber.render_stubs_block(stubs)
    assert "axiom fooSymbol" in block
    assert "def barProp : Prop := True" in block
    # Axioms get aesop attributes.
    assert "attribute [aesop safe apply] fooSymbol" in block
    # Defs do NOT.
    assert "attribute [aesop safe apply] barProp" not in block


def test_insert_stubs_into_paper_theory_writes_block_before_end(tmp_path: Path) -> None:
    pt = _make_paper_theory(tmp_path, "X")
    stubs = [{"name": "weirdSymbol", "kind": "axiom",
              "signature": "axiom weirdSymbol : _",
              "rationale": "test"}]
    ok, old = stubber.insert_stubs_into_paper_theory(pt, stubs)
    assert ok
    assert old  # snapshot returned
    new_text = pt.read_text(encoding="utf-8")
    # New text must contain the stub AND still have the `end Paper_X` line.
    assert "axiom weirdSymbol : _" in new_text
    assert "end Paper_X" in new_text
    # The stub must come BEFORE the `end Paper_X` line.
    assert new_text.index("axiom weirdSymbol") < new_text.index("end Paper_X")


def test_restore_paper_theory_undoes_insert(tmp_path: Path) -> None:
    pt = _make_paper_theory(tmp_path, "X")
    original = pt.read_text(encoding="utf-8")
    stubs = [{"name": "weirdSymbol", "kind": "axiom",
              "signature": "axiom weirdSymbol : _",
              "rationale": "test"}]
    _ok, snap = stubber.insert_stubs_into_paper_theory(pt, stubs)
    assert pt.read_text(encoding="utf-8") != original  # was modified
    assert stubber.restore_paper_theory(pt, snap)
    assert pt.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# Hypothesis-name noise filtering
# ---------------------------------------------------------------------------


def test_propose_skips_local_hypothesis_names(tmp_path: Path) -> None:
    """Names like `h_foo` that look like local hypotheses are skipped — they
    are bound variables the LLM hallucinated, not real paper-theory
    symbols. (The translator's separate hypothesis-binding repair pass
    handles these.)
    """
    pt = _make_paper_theory(tmp_path, "X")
    proposals = stubber.propose_paper_theory_stubs(
        paper_id="X",
        theorem_name="thm",
        lean_statement="theorem thm (n : ℕ) : n = n := by rfl",
        baseline_error="error: unknown identifier 'hX_local_hyp'\n",
        paper_theory_file=pt,
        mathlib_name_index={"entries": [], "by_last": {}},
    )
    assert proposals == []


def test_propose_skips_greek_bound_variable_names(tmp_path: Path) -> None:
    """Common Greek-letter bound-variable names (`alpha`, `beta`, ...) and
    single-letter math variables (`f`, `n`, `N`) are NEVER paper-theory
    symbols — the translator just forgot to bind them. We must not
    emit `axiom alpha : _` for these.
    """
    pt = _make_paper_theory(tmp_path, "X")
    proposals = stubber.propose_paper_theory_stubs(
        paper_id="X",
        theorem_name="thm",
        lean_statement="theorem thm : True := trivial",
        baseline_error=(
            "error: unknown identifier 'alpha'\n"
            "error: unknown identifier 'beta'\n"
            "error: unknown identifier 'f'\n"
            "error: unknown identifier 'N'\n"
        ),
        paper_theory_file=pt,
        mathlib_name_index={"entries": [], "by_last": {}},
    )
    assert proposals == []
