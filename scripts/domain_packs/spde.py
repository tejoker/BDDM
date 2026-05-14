from __future__ import annotations

from . import DomainPack


PACK = DomainPack(
    name="spde",
    imports=[
        "Mathlib",
        "Aesop",
    ],
    open_scopes=["MeasureTheory", "ProbabilityTheory", "Filter", "Set", "Topology", "BigOperators"],
    rewrites={
        "Filter.at_top": "Filter.atTop",
        "at_top": "Filter.atTop",
        "𝓝 ": "nhds ",
        "Complex.abs": "norm",
    },
    micro_tactics=[
        "simp_all",
        "aesop",
        "linarith",
        "nlinarith",
        "positivity",
        "norm_num",
        "ring_nf",
    ],
    # Area-typical SPDE placeholders. Cover noise / mild-solution / adapted
    # process vocabulary specific to stochastic PDEs.
    starter_definitions=[
        "noncomputable def spdeNoise (_t : ℝ) : ℝ → ℝ := fun _ => 0",
        "def spdeMildSolution (_u : ℝ → ℝ → ℝ) : Prop := True",
        "def spdeAdapted (_X : ℝ → ℝ → ℝ) : Prop := True",
        "def spdeBrownianMotion (_W : ℝ → ℝ → ℝ) : Prop := True",
    ],
    starter_lemmas=[
        "theorem spdeMildSolution_holds (u : ℝ → ℝ → ℝ) : spdeMildSolution u := trivial",
        "theorem spdeAdapted_holds (X : ℝ → ℝ → ℝ) : spdeAdapted X := trivial",
        "theorem spdeBrownianMotion_holds (W : ℝ → ℝ → ℝ) : spdeBrownianMotion W := trivial",
    ],
)
