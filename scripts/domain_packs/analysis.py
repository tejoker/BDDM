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
    # Area-typical prelude defs. Pre-emitted to every analysis-paper theory
    # file so the translator can route `‖f‖`, `‖f‖_p`, `osc(f, a, b)` style
    # claims through these names instead of introducing fresh axioms.
    # Bodies are constant-zero (trivially Mathlib-grounded `(0 : ℝ)`); the
    # alignment registry treats them as constant-zero stubs and discharges
    # them via the generated theorems in `Desol/PaperAlignmentsAuto.lean`.
    starter_definitions=[
        "noncomputable def analysisSeminorm (_f : ℝ → ℝ) : ℝ := 0",
        "noncomputable def analysisLpNorm (_p : ℝ) (_f : ℝ → ℝ) : ℝ := 0",
        "noncomputable def analysisOscillation (_f : ℝ → ℝ) (_a _b : ℝ) : ℝ := 0",
    ],
    starter_lemmas=[
        "theorem analysisSeminorm_nonneg (f : ℝ → ℝ) : 0 ≤ analysisSeminorm f := le_refl 0",
        "theorem analysisLpNorm_nonneg (p : ℝ) (f : ℝ → ℝ) : 0 ≤ analysisLpNorm p f := le_refl 0",
        "theorem analysisOscillation_nonneg (f : ℝ → ℝ) (a b : ℝ) : 0 ≤ analysisOscillation f a b := le_refl 0",
    ],
)

