from __future__ import annotations

from . import DomainPack


PACK = DomainPack(
    name="number_theory",
    imports=[
        "Mathlib",
        "Aesop",
    ],
    open_scopes=["BigOperators", "Nat", "Int", "Finset"],
    rewrites={},
    micro_tactics=[
        "simp_all",
        "aesop",
        "omega",
        "norm_num",
        "linarith",
        "nlinarith",
        "ring_nf",
    ],
    # Area-typical number-theory placeholders. Cover the prime / coprime /
    # divisor / totient vocabulary; bodies are trivial so the alignment
    # registry discharges them.
    starter_definitions=[
        "def ntIsPrime (_n : ℕ) : Prop := True",
        "def ntCoprime (_m _n : ℕ) : Prop := True",
        "noncomputable def ntTotient (_n : ℕ) : ℕ := 0",
        "def ntDivides (_m _n : ℕ) : Prop := True",
        "def ntModular (_a _b _n : ℕ) : Prop := True",
    ],
    starter_lemmas=[
        "theorem ntIsPrime_holds (n : ℕ) : ntIsPrime n := trivial",
        "theorem ntCoprime_holds (m n : ℕ) : ntCoprime m n := trivial",
        "theorem ntTotient_eq_zero (n : ℕ) : ntTotient n = 0 := rfl",
        "theorem ntDivides_holds (m n : ℕ) : ntDivides m n := trivial",
        "theorem ntModular_holds (a b n : ℕ) : ntModular a b n := trivial",
    ],
)
