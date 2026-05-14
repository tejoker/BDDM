"""Hermetic tests for the cluster-B failure-mode anchors.

Three classes drive the new retry-prompt instrumentation, each gated by an
explicit error-tail signature so the prompt only grows when there is real
diagnostic evidence to anchor against:

  * B1 bound-variable hallucination — `unknown identifier 'h4'` when the
    theorem's binders are `(h1 h2 h3 : Prop)`. The anchor lists the actual
    binder names so the model stops inventing suffixed variants.
  * B2 typeclass-instance gap — `failed to synthesize instance of type class
    \n  MeasurableSpace alpha` where `alpha : Type*` is declared in the
    signature without an instance binder. The anchor names the offending
    type variable AND suggests common Mathlib providers.
  * B3 tactic-strategy errors — `Tactic introN failed`, `type mismatch`,
    application/unification failures. The anchor proposes a concrete
    rewrite (e.g. `intro h` instead of `intros`).

All tests are hermetic: no mathlib, no lake, no network, no Mistral.
"""
from __future__ import annotations

import leanstral_proof_anchors as anchors
import leanstral_whole_proof_generator as gen


# =====================================================================
# B1 — bound-variable hallucination
# =====================================================================

def test_b1_detects_h4_when_signature_binds_h1_h2_h3() -> None:
    info = anchors.detect_bound_variable_hallucination(
        error_tail="error: unknown identifier 'h4'",
        lean_statement="theorem t (h1 h2 h3 : Prop) : h1 ∧ h2 ∧ h3 := by sorry",
    )
    assert info is not None
    assert info["hallucinated"] == ["h4"]
    # Multi-name binder group `(h1 h2 h3 : Prop)` → three declared pairs.
    declared_names = [n for (n, _t) in info["declared"]]
    assert declared_names == ["h1", "h2", "h3"]


def test_b1_no_fire_when_identifier_is_declared() -> None:
    """If the unknown identifier matches a declared binder name we DO NOT
    fire — that's a different bug (scope or order), not hallucination."""
    info = anchors.detect_bound_variable_hallucination(
        error_tail="error: unknown identifier 'h1'",
        lean_statement="theorem t (h1 h2 : Prop) : h1 ∧ h2 := by sorry",
    )
    assert info is None


def test_b1_no_fire_on_mathlib_name_shape() -> None:
    """Mathlib names like `IsClosed` / `dotProduct` do NOT match the
    bound-var pattern (`h\\d+` / single letter), so B1 does not fire on
    them — that's A1's job."""
    info = anchors.detect_bound_variable_hallucination(
        error_tail="error: unknown identifier 'IsClosed'",
        lean_statement="theorem t {alpha} (s : Set alpha) : IsClosed s := by sorry",
    )
    assert info is None


def test_b1_handles_underscore_numbered_pattern() -> None:
    """`_1`, `_2` are also bound-var-shaped (anonymous goals)."""
    info = anchors.detect_bound_variable_hallucination(
        error_tail="error: unknown identifier '_2'",
        lean_statement="theorem t (h1 : Prop) : h1 := by sorry",
    )
    assert info is not None
    assert info["hallucinated"] == ["_2"]


def test_b1_block_render_quotes_actual_binders() -> None:
    info = anchors.detect_bound_variable_hallucination(
        error_tail="error: unknown identifier 'h4'\nerror: unknown identifier 'h5'",
        lean_statement="theorem t (h1 h2 h3 : Prop) : h1 ∧ h2 ∧ h3 := by sorry",
    )
    block = anchors.build_bound_variable_anchor_block(info)
    assert "BOUND-VARIABLE ANCHORS" in block
    assert "`h4`" in block and "`h5`" in block
    # The declared binders should each appear on their own line.
    assert "- h1 : Prop" in block
    assert "- h2 : Prop" in block
    assert "- h3 : Prop" in block


def test_b1_block_render_empty_on_none() -> None:
    assert anchors.build_bound_variable_anchor_block(None) == ""
    assert anchors.build_bound_variable_anchor_block({"hallucinated": []}) == ""


# =====================================================================
# B2 — typeclass-instance gap on a free type variable
# =====================================================================

def test_b2_detects_measurablespace_gap_multiline_form() -> None:
    """Real lake output from 2604.21583 — class name on the next line."""
    info = anchors.detect_typeclass_gap(
        error_tail=(
            "failed to synthesize instance of type class\n"
            "  MeasurableSpace alpha\n\n"
            "Hint: ..."
        ),
        lean_statement="theorem t {alpha : Type*} (s : Set alpha) : True := by sorry",
    )
    assert info is not None
    assert info["class_name"] == "MeasurableSpace"
    assert info["type_var"] == "alpha"
    # Curated hints should be present.
    hint_names = [n for (n, _m) in info["instance_hints"]]
    assert "MeasurableSpace.borel" in hint_names


def test_b2_no_fire_when_no_free_type_var() -> None:
    """If the signature has NO free `Type*` binder, B2 does not fire (it's
    not a binder-shape issue)."""
    info = anchors.detect_typeclass_gap(
        error_tail="failed to synthesize instance of type class\n  Module ℝ V",
        lean_statement="theorem t (x : ℝ) : x = x := by sorry",
    )
    assert info is None


def test_b2_handles_synth_instance_failed_single_line() -> None:
    info = anchors.detect_typeclass_gap(
        error_tail="error: synthInstanceFailed: TopologicalSpace beta",
        lean_statement="theorem t {beta : Type*} : True := by sorry",
    )
    assert info is not None
    assert info["class_name"] == "TopologicalSpace"
    assert info["type_var"] == "beta"


def test_b2_block_render_includes_letI_hint_and_signature_fix() -> None:
    info = anchors.detect_typeclass_gap(
        error_tail="failed to synthesize instance of type class\n  MeasurableSpace alpha",
        lean_statement="theorem t {alpha : Type*} (s : Set alpha) : True := by sorry",
    )
    block = anchors.build_typeclass_gap_anchor_block(info)
    assert "TYPECLASS GAP" in block
    assert "`MeasurableSpace alpha`" in block
    assert "letI" in block
    assert "[MeasurableSpace alpha]" in block
    assert "MeasurableSpace.borel" in block


def test_b2_block_render_no_hints_for_unknown_class() -> None:
    """For classes outside our curated hint map we still emit the letI /
    signature-fix advice, just without specific provider suggestions."""
    info = anchors.detect_typeclass_gap(
        error_tail="failed to synthesize instance of type class\n  WeirdClass gamma",
        lean_statement="theorem t {gamma : Type*} : True := by sorry",
    )
    block = anchors.build_typeclass_gap_anchor_block(info)
    assert "TYPECLASS GAP for `WeirdClass gamma`" in block
    assert "letI" in block
    # No curated hint = no "Common Mathlib instances" header.
    assert "Common Mathlib instances" not in block


# =====================================================================
# B3 — tactic-strategy errors
# =====================================================================

def test_b3_detects_intron_failed() -> None:
    info = anchors.detect_tactic_strategy_error(error_tail="error: Tactic introN failed")
    assert info is not None
    assert info["kind"] == "introN_failed"


def test_b3_detects_type_mismatch_with_expected_type() -> None:
    info = anchors.detect_tactic_strategy_error(
        error_tail="error: type mismatch\n  expected Nat\n  got Int",
    )
    assert info is not None
    assert info["kind"] == "type_mismatch"
    # We capture the first line after the marker (best-effort).
    assert "Nat" in info["extras"]["expected"]


def test_b3_detects_application_failed() -> None:
    info = anchors.detect_tactic_strategy_error(
        error_tail="error: application type mismatch at `f x y`",
    )
    assert info is not None
    assert info["kind"] == "application_failed"


def test_b3_no_fire_on_unrelated_error() -> None:
    """`unknown identifier` is A1's territory, not B3."""
    info = anchors.detect_tactic_strategy_error(
        error_tail="error: unknown identifier 'foo'",
    )
    assert info is None


def test_b3_block_render_proposes_concrete_fix_for_intron() -> None:
    info = anchors.detect_tactic_strategy_error(error_tail="Tactic introN failed")
    block = anchors.build_tactic_strategy_anchor_block(info)
    assert "TACTIC-STRATEGY ANCHOR" in block
    # Concrete proposed fix: `intro h` / `obtain ⟨...⟩`.
    assert "intro h" in block
    assert "obtain" in block


def test_b3_block_render_quotes_expected_type_for_type_mismatch() -> None:
    info = anchors.detect_tactic_strategy_error(
        error_tail="type mismatch\n  expected `ℝ`",
    )
    block = anchors.build_tactic_strategy_anchor_block(info)
    assert "TACTIC-STRATEGY ANCHOR" in block
    assert "show" in block or "change" in block


# =====================================================================
# Standards-positive guard — anchors MUST NOT recommend `sorry`/`admit`/...
# =====================================================================

def test_anchors_never_suggest_forbidden_tokens() -> None:
    """Hard guarantee: none of the rendered hint blocks may contain the
    standards-negative tokens. If this test fails, the prompt regressed."""
    b1 = anchors.build_bound_variable_anchor_block({
        "hallucinated": ["h4"],
        "declared": [("h1", "Prop")],
    })
    b2 = anchors.build_typeclass_gap_anchor_block({
        "class_name": "MeasurableSpace",
        "type_var": "alpha",
        "all_type_vars": ["alpha"],
        "instance_hints": [("MeasurableSpace.borel", "Mathlib.MeasureTheory.MeasurableSpace.Constructions")],
    })
    b3_blocks = [
        anchors.build_tactic_strategy_anchor_block({"kind": kind, "extras": {}})
        for kind in ("introN_failed", "type_mismatch", "application_failed", "unification_failed")
    ]
    all_blocks = [b1, b2, *b3_blocks]
    forbidden = ("sorry", "admit", "apply?", "axiom", "native_decide")
    for block in all_blocks:
        lowered = block.lower()
        for tok in forbidden:
            assert tok not in lowered, f"forbidden token {tok!r} leaked into anchor block: {block!r}"


# =====================================================================
# End-to-end: build_user_prompt injects each class on the right error tail
# =====================================================================

NEIGHBOUR_SRC = """\
import Mathlib

theorem helper : True := by trivial
"""


def test_end_to_end_b1_injection_via_build_user_prompt() -> None:
    user = gen.build_user_prompt(
        paper_id="p1",
        theorem_name="t",
        lean_statement="theorem t (h1 h2 h3 : Prop) : h1 ∧ h2 ∧ h3 := by sorry",
        paper_theory_hint="",
        paper_local_file=NEIGHBOUR_SRC,
        error_tail="error: unknown identifier 'h4'",
        # Use no Mathlib index so we isolate the B1 wiring.
        audited_core_hint="",
        latex_proof_hints=[],
    )
    assert "BOUND-VARIABLE ANCHORS" in user
    assert "- h1 : Prop" in user
    # The retry block is still emitted.
    assert "previous attempt failed" in user.lower()


def test_end_to_end_b2_injection_via_build_user_prompt() -> None:
    user = gen.build_user_prompt(
        paper_id="p1",
        theorem_name="t",
        lean_statement="theorem t {alpha : Type*} (s : Set alpha) : True := by sorry",
        paper_theory_hint="",
        paper_local_file=NEIGHBOUR_SRC,
        error_tail=(
            "failed to synthesize instance of type class\n  MeasurableSpace alpha"
        ),
        audited_core_hint="",
        latex_proof_hints=[],
    )
    assert "TYPECLASS GAP" in user
    assert "MeasurableSpace" in user
    assert "alpha" in user


def test_end_to_end_b3_injection_via_build_user_prompt() -> None:
    user = gen.build_user_prompt(
        paper_id="p1",
        theorem_name="t",
        lean_statement="theorem t (n : ℕ) : n ≤ n + 1 := by sorry",
        paper_theory_hint="",
        paper_local_file=NEIGHBOUR_SRC,
        error_tail="error: Tactic introN failed",
        audited_core_hint="",
        latex_proof_hints=[],
    )
    assert "TACTIC-STRATEGY ANCHOR" in user
    assert "intro h" in user
