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
)
