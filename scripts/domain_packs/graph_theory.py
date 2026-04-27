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
)

