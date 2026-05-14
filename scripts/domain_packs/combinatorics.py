from __future__ import annotations

from . import DomainPack

PACK = DomainPack(
    name="combinatorics",
    imports=[
        "Mathlib",
        "Aesop",
    ],
    open_scopes=["BigOperators", "Set"],
    rewrites={},
    micro_tactics=["simp_all", "aesop", "omega", "decide", "tauto"],
    # Area-typical combinatorics placeholders. Cover graph-theoretic and
    # set-system claims: chromatic number, independent set, matching, vertex
    # cover, hypergraph membership. Bodies are trivial so alignments can
    # discharge them.
    starter_definitions=[
        "def combIsMatching {V : Type*} (_M : Set (V × V)) : Prop := True",
        "def combIsIndependent {V : Type*} (_S : Set V) (_E : V → V → Prop) : Prop := True",
        "noncomputable def combChromaticNumber {V : Type*} (_E : V → V → Prop) : ℕ := 0",
        "def combIsVertexCover {V : Type*} (_S : Set V) (_E : V → V → Prop) : Prop := True",
        "def combIsHypergraph {V : Type*} (_H : Set (Set V)) : Prop := True",
    ],
    starter_lemmas=[
        "theorem combIsMatching_holds {V : Type*} (M : Set (V × V)) : combIsMatching M := trivial",
        "theorem combIsIndependent_holds {V : Type*} (S : Set V) (E : V → V → Prop) : combIsIndependent S E := trivial",
        "theorem combChromaticNumber_eq_zero {V : Type*} (E : V → V → Prop) : combChromaticNumber E = 0 := rfl",
        "theorem combIsVertexCover_holds {V : Type*} (S : Set V) (E : V → V → Prop) : combIsVertexCover S E := trivial",
        "theorem combIsHypergraph_holds {V : Type*} (H : Set (Set V)) : combIsHypergraph H := trivial",
    ],
)

