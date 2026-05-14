"""Hermetic tests for composition v3 — per-aux-type role-aware mapping.

v3 was added because Round-VIII's 0/12 parent-composition rate (commit
5157f31) was bounded by aux signatures whose SHAPE matched the parent
role (e.g. "first conjunct") but whose return TYPE didn't fit — most
often when a parent `∃ K D : ℕ → ℕ, ∀ n, P (K n) (D n)` needed an aux
that PRODUCED the function witnesses, not Props about them. v3 inspects
each aux's signature for its return TYPE and matches by TYPE rather
than hint/name alone.

Hermetic: no Mistral, no lake, no filesystem writes.
"""
from __future__ import annotations

import lemma_factor_v2 as lfv2


# --- 1. Aux type classification: at least 4 cases ------------------------


def test_classify_aux_type_witness_producing_exists() -> None:
    sig = "theorem aux_K (n : ℕ) : ∃ K : ℕ → ℕ, K 0 = 1 := by sorry"
    assert lfv2.classify_aux_type(sig) == "witness_producing"


def test_classify_aux_type_witness_producing_forall_exists() -> None:
    sig = "theorem aux_delta (eps : ℝ) (hpos : 0 < eps) : ∀ x > 0, ∃ δ > 0, x + δ < eps := by sorry"
    # `∀ x > 0, ∃ δ > 0, ...` — body opens with ∃, so type is witness-producing.
    assert lfv2.classify_aux_type(sig) == "witness_producing"


def test_classify_aux_type_property_establishing_inequality() -> None:
    sig = "theorem aux_bound (n : ℕ) (hn : 0 < n) : 0 < n + 1 := by sorry"
    assert lfv2.classify_aux_type(sig) == "property_establishing"


def test_classify_aux_type_property_establishing_conjunction() -> None:
    sig = "theorem aux_conj (a b : ℝ) : 0 ≤ a ∧ 0 ≤ b ∧ a + b ≤ 1 := by sorry"
    assert lfv2.classify_aux_type(sig) == "property_establishing"


def test_classify_aux_type_equational() -> None:
    sig = "theorem aux_eq (n : ℕ) : f n = g n := by sorry"
    assert lfv2.classify_aux_type(sig) == "equational"


def test_classify_aux_type_type_coercion_inhabited() -> None:
    sig = "theorem aux_inhab (X : Type) : Inhabited X := by sorry"
    assert lfv2.classify_aux_type(sig) == "type_coercion"


def test_classify_aux_type_unknown_empty_sig() -> None:
    assert lfv2.classify_aux_type("") == "unknown"
    assert lfv2.classify_aux_type("   ") == "unknown"


# --- 2. Counting outer existential binders -------------------------------


def test_count_outer_existential_binders_single() -> None:
    assert lfv2.count_outer_existential_binders("∃ x : ℝ, 0 < x") == 1


def test_count_outer_existential_binders_multi_share_type() -> None:
    # `∃ K D : ℕ → ℕ, ∀ n, P (K n) (D n)` — TWO function witnesses.
    target = "∃ K D : ℕ → ℕ, ∀ n, P (K n) (D n)"
    assert lfv2.count_outer_existential_binders(target) == 2


def test_count_outer_existential_binders_triple_share_type() -> None:
    target = "∃ K D X : ℕ → ℕ, ∀ n, Q (K n) (D n) (X n)"
    assert lfv2.count_outer_existential_binders(target) == 3


def test_count_outer_existential_binders_nested_existentials() -> None:
    target = "∃ x : ℝ, ∃ y : ℝ, x + y = 0"
    assert lfv2.count_outer_existential_binders(target) == 2


def test_count_outer_existential_binders_no_outer_exists() -> None:
    assert lfv2.count_outer_existential_binders("a + b = b + a") == 0
    assert lfv2.count_outer_existential_binders("") == 0
    assert lfv2.count_outer_existential_binders("P ↔ Q") == 0


# --- 3. v3 role assignment: witness aux + property aux ------------------


def test_v3_role_assigns_witness_by_type_not_name() -> None:
    # Two aux: first is property-only (returns inequality), second is
    # witness-producing (returns ∃). The v3 mapper must pick the second
    # for the witness role even though it's listed second.
    aux = [
        {
            "aux_name": "prop_bound",
            "aux_signature": "theorem prop_bound (n : ℕ) : 0 < n + 1 := by sorry",
            "compose_hint": "",
        },
        {
            "aux_name": "witness_K",
            "aux_signature": "theorem witness_K (n : ℕ) : ∃ K : ℕ → ℕ, K n = n := by sorry",
            "compose_hint": "",
        },
    ]
    roles = lfv2.assign_aux_roles_v3(
        shape="exists_with_witness",
        aux=aux,
        parent_target="∃ K : ℕ → ℕ, K 0 = 0",
    )
    assert roles["witness"] == ["witness_K"]
    assert roles["prop"] == ["prop_bound"]


def test_v3_role_two_witness_aux_for_multi_witness_parent() -> None:
    # Parent needs 2 witnesses; both aux are witness-producing.
    aux = [
        {
            "aux_name": "witness_K",
            "aux_signature": "theorem witness_K : ∃ K : ℕ → ℕ, K 0 = 0 := by sorry",
            "compose_hint": "first witness",
        },
        {
            "aux_name": "witness_D",
            "aux_signature": "theorem witness_D : ∃ D : ℕ → ℕ, D 0 = 0 := by sorry",
            "compose_hint": "second witness",
        },
    ]
    roles = lfv2.assign_aux_roles_v3(
        shape="exists_with_witness",
        aux=aux,
        parent_target="∃ K D : ℕ → ℕ, ∀ n, K n = D n",
    )
    assert roles["witness"] == ["witness_K", "witness_D"]


def test_v3_role_type_mismatch_returns_empty() -> None:
    # Parent needs witness; only property-establishing aux exist. v3
    # refuses to synthesize and returns empty roles — caller declines.
    aux = [
        {
            "aux_name": "prop1",
            "aux_signature": "theorem prop1 (n : ℕ) : 0 < n + 1 := by sorry",
            "compose_hint": "first",
        },
        {
            "aux_name": "prop2",
            "aux_signature": "theorem prop2 (n : ℕ) : n ≤ n + 1 := by sorry",
            "compose_hint": "second",
        },
    ]
    roles = lfv2.assign_aux_roles_v3(
        shape="exists_with_witness",
        aux=aux,
        parent_target="∃ K : ℕ → ℕ, K 0 = 0",
    )
    assert roles["witness"] == []
    assert roles["prop"] == []


def test_v3_role_insufficient_witness_count_returns_empty() -> None:
    # Parent needs 3 witnesses; only 2 witness-producing aux available.
    # v3 refuses (won't fake a missing witness).
    aux = [
        {
            "aux_name": "w1",
            "aux_signature": "theorem w1 : ∃ K : ℕ → ℕ, K 0 = 0 := by sorry",
            "compose_hint": "",
        },
        {
            "aux_name": "w2",
            "aux_signature": "theorem w2 : ∃ D : ℕ → ℕ, D 0 = 0 := by sorry",
            "compose_hint": "",
        },
    ]
    roles = lfv2.assign_aux_roles_v3(
        shape="exists_with_witness",
        aux=aux,
        parent_target="∃ K D X : ℕ → ℕ, ∀ n, K n = D n + X n",
    )
    assert roles["witness"] == []


def test_v3_role_non_witness_shape_falls_back_to_v2() -> None:
    # Non-witness shape: v3 defers entirely to v2's role mapper.
    aux = [
        {
            "aux_name": "fwd",
            "aux_signature": "theorem fwd (a : ℝ) (h : a = 0) : a + 0 = 0 := by sorry",
            "compose_hint": "forward (mp)",
        },
        {
            "aux_name": "bwd",
            "aux_signature": "theorem bwd (a : ℝ) (h : a + 0 = 0) : a = 0 := by sorry",
            "compose_hint": "backward (mpr)",
        },
    ]
    roles = lfv2.assign_aux_roles_v3(
        shape="iff_bidirectional",
        aux=aux,
        parent_target="a = 0 ↔ a + 0 = 0",
    )
    assert roles["fwd"] == ["fwd"]
    assert roles["bwd"] == ["bwd"]


# --- 4. Composition emitter integration ----------------------------------


def test_emitter_v3_witness_plus_property_produces_obtain_pack() -> None:
    # Standard witness + property: aux_K witnesses the existential, prop
    # provides the body. Emitter must produce a tuple form.
    aux_records = [
        {
            "aux_name": "aux_K",
            "aux_signature": "theorem aux_K : ∃ K : ℕ → ℕ, K 0 = 0 := by sorry",
            "compose_hint": "witness",
        },
        {
            "aux_name": "aux_prop",
            "aux_signature": "theorem aux_prop (K : ℕ → ℕ) : 0 < K 0 + 1 := by sorry",
            "compose_hint": "property",
        },
    ]
    bodies = lfv2.render_composition_attempts(
        parent_target_shape="exists_with_witness",
        aux_names=["aux_K", "aux_prop"],
        aux_records=aux_records,
        parent_target="∃ K : ℕ → ℕ, K 0 = 0",
    )
    assert bodies, "expected at least one body for witness+property aux"
    # `aux_K` is the witness, `aux_prop` the property — at least one body
    # must reference both names.
    assert any("aux_K" in b and "aux_prop" in b for b in bodies)


def test_emitter_v3_two_witness_aux_emits_nested_obtain() -> None:
    # Parent: `∃ K D : ℕ → ℕ, ∀ n, P (K n) (D n)`. Two witness-producing
    # aux. v3 must emit a nested obtain → exact ⟨w1, w2, ...⟩ skeleton.
    aux_records = [
        {
            "aux_name": "aux_K",
            "aux_signature": "theorem aux_K : ∃ K : ℕ → ℕ, K 0 = 0 := by sorry",
            "compose_hint": "first witness",
        },
        {
            "aux_name": "aux_D",
            "aux_signature": "theorem aux_D : ∃ D : ℕ → ℕ, D 0 = 0 := by sorry",
            "compose_hint": "second witness",
        },
        {
            "aux_name": "aux_property",
            "aux_signature": (
                "theorem aux_property (K D : ℕ → ℕ) : ∀ n, K n = D n := by sorry"
            ),
            "compose_hint": "property",
        },
    ]
    bodies = lfv2.render_composition_attempts(
        parent_target_shape="exists_with_witness",
        aux_names=["aux_K", "aux_D", "aux_property"],
        aux_records=aux_records,
        parent_target="∃ K D : ℕ → ℕ, ∀ n, K n = D n",
    )
    assert bodies, "expected nested-obtain bodies for two-witness parent"
    # At least one body must contain a sequence of `obtain ⟨w1, _⟩ := aux_K`
    # and `obtain ⟨w2, _⟩ := aux_D` then a packed `exact ⟨w1, w2, ...⟩`.
    nested = [b for b in bodies if "obtain ⟨w1" in b and "obtain ⟨w2" in b]
    assert nested, f"no nested-obtain body found in: {bodies!r}"
    nested_b = nested[0]
    assert "aux_K" in nested_b and "aux_D" in nested_b
    assert "exact ⟨w1, w2" in nested_b


def test_emitter_v3_type_mismatch_returns_empty_composition_list() -> None:
    # Parent needs a witness; only property aux exist. v3 returns [].
    aux_records = [
        {
            "aux_name": "prop1",
            "aux_signature": "theorem prop1 (n : ℕ) : 0 < n + 1 := by sorry",
            "compose_hint": "first",
        },
        {
            "aux_name": "prop2",
            "aux_signature": "theorem prop2 (n : ℕ) : n ≤ n + 1 := by sorry",
            "compose_hint": "second",
        },
    ]
    bodies = lfv2.render_composition_attempts(
        parent_target_shape="exists_with_witness",
        aux_names=["prop1", "prop2"],
        aux_records=aux_records,
        parent_target="∃ K : ℕ → ℕ, K 0 = 0",
    )
    # Type mismatch: parent needs witness but no aux is witness-producing.
    # The emitter declines and returns an empty list.
    assert bodies == []


def test_emitter_v3_no_signatures_falls_through_to_v2() -> None:
    # When aux_records have no `aux_signature` field, v3 detection is
    # disabled and we fall back to v2's positional role mapper.
    aux_records = [
        {"aux_name": "a", "compose_hint": ""},
        {"aux_name": "b", "compose_hint": ""},
    ]
    bodies = lfv2.render_composition_attempts(
        parent_target_shape="conjunction_with_ineq",
        aux_names=["a", "b"],
        aux_records=aux_records,
        parent_target="0 ≤ a ∧ 0 ≤ b",
    )
    # v2 produces the And.intro candidates.
    assert any("exact ⟨a, b⟩" in b for b in bodies)


def test_emitter_v3_property_only_for_non_existential_parent_fallback() -> None:
    # All aux are property-establishing, parent is a conjunction — v3
    # doesn't kick in for non-witness shapes; v2's `conjunction_with_ineq`
    # skeleton fires.
    aux_records = [
        {
            "aux_name": "a",
            "aux_signature": "theorem a : 0 ≤ x := by sorry",
            "compose_hint": "first conjunct",
        },
        {
            "aux_name": "b",
            "aux_signature": "theorem b : 0 ≤ y := by sorry",
            "compose_hint": "second conjunct",
        },
    ]
    bodies = lfv2.render_composition_attempts(
        parent_target_shape="conjunction_with_ineq",
        aux_names=["a", "b"],
        aux_records=aux_records,
        parent_target="0 ≤ x ∧ 0 ≤ y",
    )
    assert any("exact ⟨a, b⟩" in b for b in bodies)


# --- 5. End-to-end: classification is consistent with emitter behaviour --


def test_classify_then_emit_consistent_witness_path() -> None:
    aux_records = [
        {
            "aux_name": "witness_K",
            "aux_signature": "theorem witness_K : ∃ K : ℕ → ℕ, K 0 = 0 := by sorry",
            "compose_hint": "",
        },
        {
            "aux_name": "support_lemma",
            "aux_signature": "theorem support_lemma (K : ℕ → ℕ) : 0 < K 0 + 1 := by sorry",
            "compose_hint": "",
        },
    ]
    # Direct classification check.
    types = [lfv2.classify_aux_type(r["aux_signature"]) for r in aux_records]
    assert types == ["witness_producing", "property_establishing"]
    # And the emitter respects them.
    bodies = lfv2.render_composition_attempts(
        parent_target_shape="exists_with_witness",
        aux_names=["witness_K", "support_lemma"],
        aux_records=aux_records,
        parent_target="∃ K : ℕ → ℕ, K 0 = 0",
    )
    assert bodies and any("witness_K" in b for b in bodies)
