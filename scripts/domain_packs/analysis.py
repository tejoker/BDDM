from __future__ import annotations

from . import DomainPack

PACK = DomainPack(
    name="analysis",
    imports=[
        "Mathlib",
        "Aesop",
    ],
    open_scopes=["MeasureTheory", "Filter", "Set", "Topology"],
    rewrites={
        "Filter.at_top": "Filter.atTop",
        "at_top": "Filter.atTop",
        "𝓝 ": "nhds ",
        "Complex.abs": "norm",
    },
    micro_tactics=[
        "simp_all",
        "aesop",
        "omega",
        "linarith",
        "nlinarith",
        "positivity",
        "norm_num",
        "ring_nf",
    ],
)

