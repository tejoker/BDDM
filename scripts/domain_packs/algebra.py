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
)

