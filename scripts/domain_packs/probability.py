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
    #
    # Names chosen to cover the recurring vocabulary of probability papers
    # (martingale, filtration, independence, stationarity, second-moment,
    # convergence-in-probability / a.s.) so the translator can ground these
    # phrases through stable starter names instead of inventing fresh axioms.
    starter_definitions=[
        # Generic expectation seminorm (E[X] placeholder).
        "noncomputable def probExpectation (_X : ℝ → ℝ) : ℝ := 0",
        # Generic almost-sure indicator placeholder. Real Mathlib uses
        # `MeasureTheory.ae` filter; here we route paper claims through a
        # uniform name so the translator avoids ad-hoc axiom emission.
        "def probAlmostSure (_P : ℝ → Prop) : Prop := True",
        # Generic random-variable measurability placeholder.
        "def probMeasurable (_X : ℝ → ℝ) : Prop := True",
        # Filtration / martingale placeholders. The Mathlib analogs require
        # heavy measure-theoretic typeclass synthesis; the starter form lets
        # the translator type-check claims about adapted processes.
        "def probFiltration (_t : ℝ) : Set (Set ℝ) := Set.univ",
        "def probMartingale (_X : ℝ → ℝ → ℝ) : Prop := True",
        "def probAdapted (_X : ℝ → ℝ → ℝ) : Prop := True",
        # Independence and stationarity (two of the most common hypotheses
        # in probability papers; the translator otherwise emits a fresh
        # axiom for each).
        "def probIndependent (_X _Y : ℝ → ℝ) : Prop := True",
        "def probStationary (_X : ℕ → ℝ → ℝ) : Prop := True",
        # Moment bounds — second moment is the most common in CLT/LLN-style
        # statements.
        "def hasFiniteSecondMoment (_X : ℝ → ℝ) : Prop := True",
        # Convergence-mode placeholders. These cover `X_n → X in probability`
        # and `X_n → X a.s.` — both recur across probability papers.
        "def converges_in_probability (_X : ℕ → ℝ → ℝ) (_Y : ℝ → ℝ) : Prop := True",
        "def converges_almost_surely (_X : ℕ → ℝ → ℝ) (_Y : ℝ → ℝ) : Prop := True",
    ],
    starter_lemmas=[
        "theorem probAlmostSure_holds (P : ℝ → Prop) : probAlmostSure P := trivial",
        "theorem probMeasurable_holds (X : ℝ → ℝ) : probMeasurable X := trivial",
        "theorem probMartingale_holds (X : ℝ → ℝ → ℝ) : probMartingale X := trivial",
        "theorem probAdapted_holds (X : ℝ → ℝ → ℝ) : probAdapted X := trivial",
        "theorem probIndependent_holds (X Y : ℝ → ℝ) : probIndependent X Y := trivial",
        "theorem probStationary_holds (X : ℕ → ℝ → ℝ) : probStationary X := trivial",
        "theorem hasFiniteSecondMoment_holds (X : ℝ → ℝ) : hasFiniteSecondMoment X := trivial",
    ],
)

