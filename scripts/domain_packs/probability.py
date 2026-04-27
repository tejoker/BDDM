from __future__ import annotations

from . import DomainPack

PACK = DomainPack(
    name="probability",
    imports=[
        "Mathlib",
        "Aesop",
    ],
    open_scopes=["MeasureTheory", "ProbabilityTheory", "Filter", "Set", "Topology"],
    rewrites={
        "Filter.at_top": "Filter.atTop",
        "at_top": "Filter.atTop",
        "𝓝 ": "nhds ",
        "Complex.abs": "norm",
    },
    micro_tactics=[
        "simp_all",
        "aesop",
        "tauto",
        "omega",
        "linarith",
        "nlinarith",
        "norm_num",
    ],
)

