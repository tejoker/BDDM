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
)

