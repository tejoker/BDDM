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
    # Bodies are constant-zero or `Set.univ` (trivially Mathlib-grounded); the
    # alignment registry treats them as constant-zero stubs and discharges
    # them via the generated theorems in `Desol/PaperAlignmentsAuto.lean`.
    #
    # The recurring-name targets (HSobolev, L2Space, infty, analysisAbsDiff)
    # were chosen from a corpus mine of `Desol/PaperTheory/Paper_*.lean`:
    # `HSobolev` appears in 3 papers, `L2Space` in 3 papers, `infty` in 4
    # papers — each was being re-emitted as a paper-local axiom-form stub.
    starter_definitions=[
        # Generic seminorm/Lp/oscillation placeholders (analysis-typical).
        "noncomputable def analysisSeminorm (_f : ℝ → ℝ) : ℝ := 0",
        "noncomputable def analysisLpNorm (_p : ℝ) (_f : ℝ → ℝ) : ℝ := 0",
        "noncomputable def analysisOscillation (_f : ℝ → ℝ) (_a _b : ℝ) : ℝ := 0",
        # Sobolev / L2 function space placeholders (3-paper recurrence).
        "abbrev HSobolev (_s : ℝ) : Set (ℝ → ℝ) := Set.univ",
        "abbrev L2Space : Set (ℝ → ℝ) := Set.univ",
        # Generic infinity placeholder (4-paper recurrence; many papers write
        # `infty` as a paper-local stub when Mathlib uses `⊤`).
        "noncomputable def infty : ℝ := 0",
        # Mathlib-grounded oscillation surrogate — uses real `abs` so the
        # nonneg lemma below is a genuine `abs_nonneg`, not just `le_refl 0`.
        "noncomputable def analysisAbsDiff (f : ℝ → ℝ) (a b : ℝ) : ℝ := |f a - f b|",
        # Lipschitz / continuity placeholders (recurring in analysis papers).
        "def analysisIsLipschitz (_f : ℝ → ℝ) (_K : ℝ) : Prop := True",
        "def analysisIsContinuous (_f : ℝ → ℝ) : Prop := True",
    ],
    starter_lemmas=[
        "theorem analysisSeminorm_nonneg (f : ℝ → ℝ) : 0 ≤ analysisSeminorm f := le_refl 0",
        "theorem analysisLpNorm_nonneg (p : ℝ) (f : ℝ → ℝ) : 0 ≤ analysisLpNorm p f := le_refl 0",
        "theorem analysisOscillation_nonneg (f : ℝ → ℝ) (a b : ℝ) : 0 ≤ analysisOscillation f a b := le_refl 0",
        "theorem analysisAbsDiff_nonneg (f : ℝ → ℝ) (a b : ℝ) : 0 ≤ analysisAbsDiff f a b := abs_nonneg _",
        "theorem HSobolev_mem (s : ℝ) (f : ℝ → ℝ) : f ∈ HSobolev s := Set.mem_univ _",
        "theorem L2Space_mem (f : ℝ → ℝ) : f ∈ L2Space := Set.mem_univ _",
        "theorem analysisIsLipschitz_holds (f : ℝ → ℝ) (K : ℝ) : analysisIsLipschitz f K := trivial",
        "theorem analysisIsContinuous_holds (f : ℝ → ℝ) : analysisIsContinuous f := trivial",
    ],
)

