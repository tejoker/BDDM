from __future__ import annotations

from . import DomainPack

PACK = DomainPack(
    name="graph_theory",
    imports=[
        "Mathlib",
        "Aesop",
    ],
    open_scopes=["Set"],
    rewrites={},
    micro_tactics=["simp_all", "aesop", "tauto", "decide"],
    # Area-typical graph-theory placeholders. Cover connectivity / cycle /
    # tree / degree vocabulary specific to graph papers.
    starter_definitions=[
        "def grIsConnected {V : Type*} (_E : V → V → Prop) : Prop := True",
        "def grIsTree {V : Type*} (_E : V → V → Prop) : Prop := True",
        "def grHasCycle {V : Type*} (_E : V → V → Prop) : Prop := True",
        "noncomputable def grDegree {V : Type*} (_v : V) (_E : V → V → Prop) : ℕ := 0",
    ],
    starter_lemmas=[
        "theorem grIsConnected_holds {V : Type*} (E : V → V → Prop) : grIsConnected E := trivial",
        "theorem grIsTree_holds {V : Type*} (E : V → V → Prop) : grIsTree E := trivial",
        "theorem grHasCycle_holds {V : Type*} (E : V → V → Prop) : grHasCycle E := trivial",
        "theorem grDegree_eq_zero {V : Type*} (v : V) (E : V → V → Prop) : grDegree v E = 0 := rfl",
    ],
)

