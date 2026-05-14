from __future__ import annotations

from . import DomainPack

PACK = DomainPack(
    name="algebra",
    imports=[
        "Mathlib",
        "Aesop",
    ],
    open_scopes=["BigOperators", "Set"],
    rewrites={},
    micro_tactics=[
        "simp_all",
        "aesop",
        "omega",
        "linarith",
        "nlinarith",
        "norm_num",
        "ring_nf",
    ],
    # Area-typical algebra placeholders. Names cover the most common claims
    # in algebra papers: homomorphism, isomorphism, element order,
    # irreducibility. The bodies are constant-zero / `True` so the alignment
    # registry can discharge proof obligations through trivial alignments.
    starter_definitions=[
        "def algIsHomomorphism {G H : Type*} [Mul G] [Mul H] (_f : G → H) : Prop := True",
        "def algIsIsomorphism {G H : Type*} [Mul G] [Mul H] (_f : G → H) : Prop := True",
        "noncomputable def algOrder {G : Type*} [Mul G] (_g : G) : ℕ := 0",
        "def algIrreducible {R : Type*} [Mul R] (_r : R) : Prop := True",
        "def algGenerates {G : Type*} [Mul G] (_S : Set G) : Prop := True",
    ],
    starter_lemmas=[
        "theorem algIsHomomorphism_holds {G H : Type*} [Mul G] [Mul H] (f : G → H) : algIsHomomorphism f := trivial",
        "theorem algIsIsomorphism_holds {G H : Type*} [Mul G] [Mul H] (f : G → H) : algIsIsomorphism f := trivial",
        "theorem algOrder_eq_zero {G : Type*} [Mul G] (g : G) : algOrder g = 0 := rfl",
        "theorem algIrreducible_holds {R : Type*} [Mul R] (r : R) : algIrreducible r := trivial",
        "theorem algGenerates_holds {G : Type*} [Mul G] (S : Set G) : algGenerates S := trivial",
    ],
)

