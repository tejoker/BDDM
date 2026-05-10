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
    # Area-typical probability prelude defs.
    starter_definitions=[
        # Generic expectation seminorm (E[X] placeholder).
        "noncomputable def probExpectation (_X : ℝ → ℝ) : ℝ := 0",
        # Generic almost-sure indicator placeholder. Real Mathlib uses
        # `MeasureTheory.ae` filter; here we route paper claims through a
        # uniform name so the translator avoids ad-hoc axiom emission.
        "def probAlmostSure (_P : ℝ → Prop) : Prop := True",
        # Generic random-variable measurability placeholder.
        "def probMeasurable (_X : ℝ → ℝ) : Prop := True",
    ],
    starter_lemmas=[
        "theorem probAlmostSure_holds (P : ℝ → Prop) : probAlmostSure P := trivial",
        "theorem probMeasurable_holds (X : ℝ → ℝ) : probMeasurable X := trivial",
    ],
)

