from __future__ import annotations

from . import DomainPack


PACK = DomainPack(
    name="pde",
    imports=[
        "Mathlib",
        "Aesop",
    ],
    open_scopes=["MeasureTheory", "Filter", "Set", "Topology", "BigOperators"],
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
    # Area-typical PDE placeholders. Cover Laplacian / gradient / solution /
    # weak-solution vocabulary. Bodies are constant-zero / `True`.
    starter_definitions=[
        "noncomputable def pdeLaplacian (_f : ℝ → ℝ) : ℝ → ℝ := fun _ => 0",
        "noncomputable def pdeGradient (_f : ℝ → ℝ) : ℝ → ℝ := fun _ => 0",
        "def pdeSolves (_u : ℝ → ℝ) (_F : (ℝ → ℝ) → ℝ → ℝ) : Prop := True",
        "def pdeWeakSolution (_u : ℝ → ℝ) : Prop := True",
        "def pdeBoundaryCondition (_u : ℝ → ℝ) (_b : ℝ → ℝ) : Prop := True",
    ],
    starter_lemmas=[
        "theorem pdeSolves_holds (u : ℝ → ℝ) (F : (ℝ → ℝ) → ℝ → ℝ) : pdeSolves u F := trivial",
        "theorem pdeWeakSolution_holds (u : ℝ → ℝ) : pdeWeakSolution u := trivial",
        "theorem pdeBoundaryCondition_holds (u b : ℝ → ℝ) : pdeBoundaryCondition u b := trivial",
    ],
)
