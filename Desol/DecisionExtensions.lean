/-
# DecisionExtensions — composite decision-procedure tactics that the
# stock Mathlib triplet (`positivity`, `gcongr`, `polyrith`) misses.

Recurring patterns in the BDDM corpus that none of the canonical
decision tactics close in one step:

  * Parametric `Real.rpow` non-negativity: `x ^ a ≥ 0` when `x : ℝ` and
    we don't know `x ≥ 0` until a hypothesis. `positivity` handles
    `x : ℝ≥0` but not `x : ℝ` with side-condition hypothesis.
  * `gcongr` over `Filter.Tendsto` / `Filter.atTop` patterns —
    monotonicity of limits.
  * `field_simp` + `polyrith` chain that the existing micro-prover
    catalogue doesn't always sequence right.

This module provides **composite tactics** that wrap the canonical
trios with the most useful chains. They're not new decision procedures
— they're the boilerplate the prover would otherwise have to discover
through MCTS expansion.
-/

import Lean
import Mathlib

namespace Desol.DecisionExtensions

open Lean Elab Tactic

/-- `bddm_positivity` — try `positivity` first; on failure, try
`positivity_ext` patterns that accept side-conditions: `x ^ a ≥ 0` when
`hx : x ≥ 0` is already in context. -/
syntax (name := bddmPositivity) "bddm_positivity" : tactic

@[tactic bddmPositivity]
def elabBddmPositivity : Tactic := fun _stx => do
  evalTactic (← `(tactic| first
    | positivity
    | (apply Real.rpow_nonneg; assumption)
    | (apply Real.rpow_pos_of_pos; assumption)
    | (apply pow_nonneg; assumption)
    | (apply pow_pos; assumption)))

/-- `bddm_field_ring` — clear denominators then close as a polynomial ring
identity. Common in analysis-paper bounds where translation produces
`a / b = c * d⁻¹` shapes. -/
syntax (name := bddmFieldRing) "bddm_field_ring" : tactic

@[tactic bddmFieldRing]
def elabBddmFieldRing : Tactic := fun _stx => do
  evalTactic (← `(tactic| first
    | (field_simp; ring)
    | (field_simp; ring_nf; norm_num)
    | (field_simp at *; linarith)))

/-- `bddm_gcongr_chain` — generalised-congruence over ordered structures
with a follow-up positivity / linarith step. Closes things like
`a + b ≤ c + d` from `a ≤ c` and `b ≤ d` even when `gcongr` alone leaves
side conditions. -/
syntax (name := bddmGCongrChain) "bddm_gcongr_chain" : tactic

@[tactic bddmGCongrChain]
def elabBddmGCongrChain : Tactic := fun _stx => do
  evalTactic (← `(tactic| first
    | gcongr
    | (gcongr; positivity)
    | (gcongr; linarith)
    | (gcongr; nlinarith)))

/-- `bddm_cast_omega` — push casts then close with omega. Common pattern
when a translated statement mixes ℕ and ℤ via `Int.toNat` etc. -/
syntax (name := bddmCastOmega) "bddm_cast_omega" : tactic

@[tactic bddmCastOmega]
def elabBddmCastOmega : Tactic := fun _stx => do
  evalTactic (← `(tactic| first
    | (push_cast; omega)
    | (push_cast at *; omega)
    | (norm_cast; omega)))

/-- `bddm_summable_chain` — `Summable` proofs via comparison + dominated
convergence. Common in analysis-paper bounds where a translated series
needs to be shown summable from a positivity hypothesis. -/
syntax (name := bddmSummableChain) "bddm_summable_chain" : tactic

@[tactic bddmSummableChain]
def elabBddmSummableChain : Tactic := fun _stx => do
  evalTactic (← `(tactic| first
    | (apply Summable.of_nonneg_of_le; intros; (try positivity); (try linarith); assumption)
    | (apply Summable.comp_injective; assumption)
    | (apply summable_of_nonneg_of_le; intros; (try positivity); (try linarith); assumption)
    | (rw [summable_iff_cauchySeq]; assumption)))

/-- `bddm_integrability_chain` — `MeasureTheory.Integrable` proofs via
local integrability + boundedness or dominated convergence. -/
syntax (name := bddmIntegrabilityChain) "bddm_integrability_chain" : tactic

@[tactic bddmIntegrabilityChain]
def elabBddmIntegrabilityChain : Tactic := fun _stx => do
  evalTactic (← `(tactic| first
    | (apply MeasureTheory.Integrable.mono; (try assumption); (try linarith); intros; (try simp_all); positivity)
    | (apply MeasureTheory.Integrable.comp_measurable; (try assumption); measurability)
    | (rw [MeasureTheory.integrable_iff_integrableOn]; assumption)))

/-- `bddm_inequality_chain` — gcongr → positivity → nlinarith ladder for
ordered-structure inequalities involving `Real.rpow`, `Real.log`, etc.
where standard `gcongr` leaves residual side-conditions. -/
syntax (name := bddmInequalityChain) "bddm_inequality_chain" : tactic

@[tactic bddmInequalityChain]
def elabBddmInequalityChain : Tactic := fun _stx => do
  evalTactic (← `(tactic| first
    | (gcongr; (try positivity); (try linarith); (try nlinarith); assumption)
    | (apply Real.rpow_le_rpow; (try positivity); assumption)
    | (apply Real.rpow_natCast_mul; assumption)
    | (rw [Real.rpow_natCast]; gcongr; (try positivity); linarith)
    | (rw [Real.log_le_log_iff]; (try positivity); assumption)))

/-- `bddm_filter_tendsto` — Filter limit composition + standard
`Tendsto` lemmas. Closes goals of shape `Filter.Tendsto f l₁ l₂` when
`f` decomposes into pieces with known limits. -/
syntax (name := bddmFilterTendsto) "bddm_filter_tendsto" : tactic

@[tactic bddmFilterTendsto]
def elabBddmFilterTendsto : Tactic := fun _stx => do
  evalTactic (← `(tactic| first
    | (apply Filter.Tendsto.comp; assumption)
    | (apply Filter.Tendsto.const_mul; assumption)
    | (apply Filter.Tendsto.add; (try assumption); (try simp); assumption)
    | (apply Filter.Tendsto.mul; (try assumption); (try simp); assumption)
    | (rw [Filter.tendsto_iff_eventually]; intros; (try simp_all); assumption)))

end Desol.DecisionExtensions
