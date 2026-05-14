"""Hermetic tests for the Round-VIII fine-grained shape detector,
aux-role mapper, and composition emitter in `scripts/lemma_factor_v2.py`.

Each test is pure-Python — no Mistral, no lake.

Coverage:
  * Shape detector: each fine shape correctly identified from a target
    string (≥9 cases).
  * Composition emitter: each skeleton produces well-formed Lean for a
    sample (≥9 sample outputs).
  * Aux-role mapper: for each shape, the right aux gets the right role.
"""
from __future__ import annotations

import lemma_factor_v2 as lfv2


# --- Shape detector: ≥9 cases --------------------------------------------


def test_shape_exists_with_witness() -> None:
    assert lfv2.detect_target_shape_fine("∃ eps : ℝ, 0 < eps") == "exists_with_witness"


def test_shape_exists_with_prop() -> None:
    # ∃ x, P x ∧ Q x — the body has a conjunction.
    target = "∃ eps : ℝ, 0 < eps ∧ s2 < bound - eps"
    assert lfv2.detect_target_shape_fine(target) == "exists_with_prop"


def test_shape_nested_exists_multi_binder() -> None:
    # ∃ x y, P x y -- multiple binders before the comma.
    target = "∃ x y : ℝ, x + y = 0"
    assert lfv2.detect_target_shape_fine(target) == "nested_exists"


def test_shape_nested_exists_explicit() -> None:
    # ∃ x, ∃ y, P x y — explicit nesting.
    target = "∃ x : ℝ, ∃ y : ℝ, x + y = 0"
    assert lfv2.detect_target_shape_fine(target) == "nested_exists"


def test_shape_iff_bidirectional() -> None:
    assert lfv2.detect_target_shape_fine("P ↔ Q") == "iff_bidirectional"


def test_shape_implication() -> None:
    assert lfv2.detect_target_shape_fine("P → Q") == "implication"


def test_shape_universal_with_bound_explicit() -> None:
    # `∀ n ≥ N, P n` — bound symbol appears in the binder.
    target = "∀ n ≥ N, p n"
    assert lfv2.detect_target_shape_fine(target) == "universal_with_bound"


def test_shape_universal_implication() -> None:
    # `∀ n, P n → Q n` — no explicit bound symbol, body is implication.
    target = "∀ n : ℕ, P n → Q n"
    assert lfv2.detect_target_shape_fine(target) == "universal_implication"


def test_shape_calc_chain_equality_no_connectives() -> None:
    # Plain equality / inequality target with no logical connectives -> calc.
    assert lfv2.detect_target_shape_fine("a + b = b + a") == "calc_chain"
    assert lfv2.detect_target_shape_fine("‖x‖ ≤ ‖y‖") == "calc_chain"


def test_shape_disjunction() -> None:
    assert lfv2.detect_target_shape_fine("P ∨ Q") == "disjunction"


def test_shape_conjunction_with_ineq() -> None:
    # Top-level conjunction with an inequality.
    target = "0 ≤ s1 ∧ s1 < s2"
    assert lfv2.detect_target_shape_fine(target) == "conjunction_with_ineq"


def test_shape_other_no_match() -> None:
    # Pure proposition with no logical structure -> 'other'.
    assert lfv2.detect_target_shape_fine("Continuous f") == "other"
    assert lfv2.detect_target_shape_fine("") == "other"


# Legacy coarse classifier still works through the fine pipeline.


def test_legacy_coarse_classifier_and() -> None:
    assert lfv2.detect_target_shape("0 ≤ n ∧ n ≤ n + 1") == "and"


def test_legacy_coarse_classifier_exists() -> None:
    assert lfv2.detect_target_shape("∃ x : ℝ, 0 < x") == "exists"


def test_legacy_coarse_classifier_iff() -> None:
    assert lfv2.detect_target_shape("P ↔ Q") == "iff"


# --- Aux-role mapper: ≥9 cases ------------------------------------------


def test_role_mapper_exists_with_witness_by_hint() -> None:
    aux = [
        {"aux_name": "eps_pos", "compose_hint": "existential witness positive"},
        {"aux_name": "bound_holds", "compose_hint": "bound holds for chosen eps"},
    ]
    roles = lfv2.assign_aux_roles(shape="exists_with_witness", aux=aux)
    assert roles["witness"] == ["eps_pos"]
    assert roles["prop"] == ["bound_holds"]


def test_role_mapper_exists_with_witness_by_name_fallback() -> None:
    # No hint match — name 'pos' triggers witness.
    aux = [
        {"aux_name": "first_pos", "compose_hint": ""},
        {"aux_name": "second_property", "compose_hint": ""},
    ]
    roles = lfv2.assign_aux_roles(shape="exists_with_witness", aux=aux)
    assert "first_pos" in roles["witness"]


def test_role_mapper_iff_fwd_bwd_by_hint() -> None:
    aux = [
        {"aux_name": "thm_fwd", "compose_hint": "forward direction (mp)"},
        {"aux_name": "thm_bwd", "compose_hint": "backward direction (mpr)"},
    ]
    roles = lfv2.assign_aux_roles(shape="iff_bidirectional", aux=aux)
    assert roles["fwd"] == ["thm_fwd"]
    assert roles["bwd"] == ["thm_bwd"]


def test_role_mapper_iff_positional_fallback() -> None:
    aux = [
        {"aux_name": "p1", "compose_hint": "first"},
        {"aux_name": "p2", "compose_hint": "second"},
    ]
    roles = lfv2.assign_aux_roles(shape="iff_bidirectional", aux=aux)
    # First aux defaults to fwd, second to bwd.
    assert roles["fwd"] == ["p1"]
    assert roles["bwd"] == ["p2"]


def test_role_mapper_implication() -> None:
    aux = [{"aux_name": "deduction", "compose_hint": "body"}]
    roles = lfv2.assign_aux_roles(shape="implication", aux=aux)
    assert roles["body"] == ["deduction"]


def test_role_mapper_calc_chain_ordered_by_step_hint() -> None:
    aux = [
        {"aux_name": "step_two", "compose_hint": "step-2"},
        {"aux_name": "step_one", "compose_hint": "step-1"},
        {"aux_name": "step_three", "compose_hint": "step-3"},
    ]
    roles = lfv2.assign_aux_roles(shape="calc_chain", aux=aux)
    # Order by step number, not list order.
    assert roles["step"] == ["step_one", "step_two", "step_three"]


def test_role_mapper_disjunction_left_right() -> None:
    aux = [
        {"aux_name": "left_aux", "compose_hint": "left side of disjunction"},
        {"aux_name": "right_aux", "compose_hint": "Or.inr"},
    ]
    roles = lfv2.assign_aux_roles(shape="disjunction", aux=aux)
    assert roles["left"] == ["left_aux"]
    assert roles["right"] == ["right_aux"]


def test_role_mapper_conjunction_all_conjuncts() -> None:
    aux = [
        {"aux_name": "a1", "compose_hint": "first conjunct"},
        {"aux_name": "a2", "compose_hint": "second conjunct"},
        {"aux_name": "a3", "compose_hint": "third conjunct"},
    ]
    roles = lfv2.assign_aux_roles(shape="conjunction_with_ineq", aux=aux)
    assert roles["conjunct"] == ["a1", "a2", "a3"]


def test_role_mapper_unknown_shape() -> None:
    aux = [{"aux_name": "only", "compose_hint": ""}]
    roles = lfv2.assign_aux_roles(shape="totally_unknown", aux=aux)
    assert roles["any"] == ["only"]


# --- Composition emitter: ≥9 sample outputs ------------------------------


def test_emitter_exists_with_witness_emits_anon_constructor() -> None:
    bodies = lfv2.render_composition_attempts(
        parent_target_shape="exists_with_witness",
        aux_names=["eps_pos", "bound_holds"],
        aux_records=[
            {"aux_name": "eps_pos", "compose_hint": "positive witness"},
            {"aux_name": "bound_holds", "compose_hint": "property"},
        ],
    )
    # Sample output 1
    assert any("exact ⟨eps_pos, bound_holds⟩" in b for b in bodies)
    # Sample output 2 — obtain-then-exact
    assert any("obtain ⟨w, hw⟩ := eps_pos" in b for b in bodies)


def test_emitter_exists_with_prop_emits_packed_tuple() -> None:
    bodies = lfv2.render_composition_attempts(
        parent_target_shape="exists_with_prop",
        aux_names=["w", "h1", "h2"],
        aux_records=[
            {"aux_name": "w", "compose_hint": "witness"},
            {"aux_name": "h1", "compose_hint": "property 1"},
            {"aux_name": "h2", "compose_hint": "property 2"},
        ],
    )
    # Sample output 3
    assert any("exact ⟨w, h1, h2⟩" in b for b in bodies)


def test_emitter_nested_exists_emits_obtain_chain() -> None:
    bodies = lfv2.render_composition_attempts(
        parent_target_shape="nested_exists",
        aux_names=["aux1", "aux2"],
        aux_records=[
            {"aux_name": "aux1", "compose_hint": "first witness"},
            {"aux_name": "aux2", "compose_hint": "second witness"},
        ],
    )
    # Sample output 4
    assert any("obtain ⟨w1, h1⟩ := aux1" in b for b in bodies)
    assert any("exact ⟨w1, w2" in b for b in bodies)


def test_emitter_iff_bidirectional_emits_constructor() -> None:
    bodies = lfv2.render_composition_attempts(
        parent_target_shape="iff_bidirectional",
        aux_names=["fwd_aux", "bwd_aux"],
        aux_records=[
            {"aux_name": "fwd_aux", "compose_hint": "forward (mp)"},
            {"aux_name": "bwd_aux", "compose_hint": "backward (mpr)"},
        ],
    )
    # Sample output 5
    assert any("exact ⟨fwd_aux, bwd_aux⟩" in b for b in bodies)
    assert any("constructor" in b for b in bodies)


def test_emitter_implication_emits_intro_then_exact() -> None:
    bodies = lfv2.render_composition_attempts(
        parent_target_shape="implication",
        aux_names=["deduction"],
        aux_records=[{"aux_name": "deduction", "compose_hint": "body"}],
    )
    # Sample output 6
    assert any("intro h" in b and "exact deduction h" in b for b in bodies)


def test_emitter_universal_with_bound_emits_intro_n_hN() -> None:
    bodies = lfv2.render_composition_attempts(
        parent_target_shape="universal_with_bound",
        aux_names=["claim"],
        aux_records=[{"aux_name": "claim", "compose_hint": "body"}],
    )
    # Sample output 7
    assert any("intro n hN" in b and "exact claim n hN" in b for b in bodies)


def test_emitter_calc_chain_emits_trans_chain() -> None:
    bodies = lfv2.render_composition_attempts(
        parent_target_shape="calc_chain",
        aux_names=["step1", "step2"],
        aux_records=[
            {"aux_name": "step1", "compose_hint": "step-1"},
            {"aux_name": "step2", "compose_hint": "step-2"},
        ],
    )
    # Sample output 8 — trans-style composition.
    assert any("step1.trans step2" in b for b in bodies)
    # Sample output 8b — calc skeleton.
    assert any("calc" in b for b in bodies)


def test_emitter_disjunction_emits_or_inl_or_inr() -> None:
    bodies = lfv2.render_composition_attempts(
        parent_target_shape="disjunction",
        aux_names=["lhs", "rhs"],
        aux_records=[
            {"aux_name": "lhs", "compose_hint": "left side"},
            {"aux_name": "rhs", "compose_hint": "right side"},
        ],
    )
    # Sample output 9 — at least one Or.inl branch.
    assert any("Or.inl lhs" in b for b in bodies)
    assert any("Or.inr rhs" in b for b in bodies)


def test_emitter_conjunction_with_ineq_two_aux_emits_pair() -> None:
    bodies = lfv2.render_composition_attempts(
        parent_target_shape="conjunction_with_ineq",
        aux_names=["bound1", "bound2"],
        aux_records=[
            {"aux_name": "bound1", "compose_hint": "first conjunct"},
            {"aux_name": "bound2", "compose_hint": "second conjunct"},
        ],
    )
    # Sample output 10 — pair constructor.
    assert any("exact ⟨bound1, bound2⟩" in b for b in bodies)
    assert any("refine ⟨?_, ?_⟩" in b for b in bodies)


def test_emitter_legacy_coarse_label_still_works() -> None:
    # Backward compatibility: passing 'and' / 'exists' / 'iff' still produces
    # candidates (lifted internally to the matching fine label).
    bodies = lfv2.render_composition_attempts(
        parent_target_shape="and",
        aux_names=["a", "b"],
    )
    assert any("exact ⟨a, b⟩" in b for b in bodies)


def test_emitter_empty_aux_names_returns_empty() -> None:
    assert lfv2.render_composition_attempts(
        parent_target_shape="iff_bidirectional",
        aux_names=[],
    ) == []


def test_emitter_dedups_repeated_bodies() -> None:
    bodies = lfv2.render_composition_attempts(
        parent_target_shape="conjunction_with_ineq",
        aux_names=["a", "b"],
    )
    # No duplicates in the returned list.
    assert len(bodies) == len(set(bodies))


# --- Integration: factor_long_theorem_v2 records FINE shape -------------


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeChat:
    def __init__(self, content: str) -> None:
        self._content = content

    def complete(self, **_: object) -> _FakeResponse:
        return _FakeResponse(self._content)


class FakeClient:
    def __init__(self, content: str) -> None:
        self.chat = _FakeChat(content)


def test_factor_v2_records_fine_shape_field() -> None:
    import json

    aux = [
        {
            "aux_name": "thm_fwd",
            "aux_signature": "theorem thm_fwd (a : ℝ) (h : a = 0) : a + 0 = 0 := by sorry",
            "compose_hint": "forward (mp)",
        },
        {
            "aux_name": "thm_bwd",
            "aux_signature": "theorem thm_bwd (a : ℝ) (h : a + 0 = 0) : a = 0 := by sorry",
            "compose_hint": "backward (mpr)",
        },
    ]
    client = FakeClient(
        json.dumps(
            {
                "verdict": "FACTOR",
                "aux_lemmas": aux,
                "compose_strategy": "iff",
                "reasoning": "biconditional split",
                "confidence": 0.8,
            }
        )
    )
    out = lfv2.factor_long_theorem_v2(
        paper_id="p",
        theorem_name="thm",
        lean_statement=(
            "theorem thm (a : ℝ) : a = 0 ↔ a + 0 = 0 := by sorry"
        ),
        paper_theory_hint="",
        exported_symbols="",
        client=client,
    )
    assert len(out) == 2
    # Fine shape is recorded.
    assert all(r["parent_target_shape_fine"] == "iff_bidirectional" for r in out)
    # Coarse shape backed by the same source.
    assert all(r["parent_target_shape"] == "iff" for r in out)
