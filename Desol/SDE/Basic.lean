import Mathlib.Probability.Distributions.Gaussian.HasGaussianLaw.Def
import Mathlib.Probability.Distributions.Gaussian.HasGaussianLaw.Basic
import Mathlib.Probability.Distributions.Gaussian.IsGaussianProcess.Def
import Mathlib.Probability.Distributions.Gaussian.IsGaussianProcess.Basic
import Mathlib.Probability.Independence.Process.HasIndepIncrements
import Mathlib.Probability.Martingale.Basic
import Mathlib.Probability.Process.Adapted
import Mathlib.Probability.Process.Filtration
import Mathlib.Probability.ConditionalExpectation
import Mathlib.Probability.Independence.Basic
import Mathlib.MeasureTheory.Integral.Bochner.Basic
import Mathlib.MeasureTheory.Function.ConditionalExpectation.Basic
import Mathlib.MeasureTheory.Function.StronglyMeasurable.Basic

/-!
# SDE and Gaussian Process Examples

This module is a small first-party Lean surface for stochastic-process examples
used by DESol proof-search experiments. It is not generated from an arXiv paper;
it gives the pipeline concrete declarations over Mathlib probability APIs for
backend health checks and tactic-search development.

The main nontrivial statement is `wiener_martingale_natural`, which sketches the
standard route from Gaussian independent increments and zero mean increments to
the martingale property with respect to the natural filtration.
-/

open MeasureTheory ProbabilityTheory

namespace DESol.SDE

variable {Ω : Type*} [MeasurableSpace Ω] {μ : Measure Ω} [IsProbabilityMeasure μ]

/-- A Gaussian process with zero initial value has integrable marginals at all times. -/
lemma gaussian_process_zero_mean
    (X : ℕ → Ω → ℝ)
    (hG : IsGaussianProcess X μ)
    (h0 : X 0 = 0) :
    ∀ t : ℕ, Integrable (X t) μ := by
  intro t
  exact HasGaussianLaw.integrable (hG.hasGaussianLaw_eval t)

/-- The increment process of a Gaussian process with independent increments is Gaussian. -/
lemma indep_increments_characterization
    (X : ℕ → Ω → ℝ)
    (hG : IsGaussianProcess X μ)
    (hI : HasIndepIncrements X μ) :
    ∀ s : ℕ, IsGaussianProcess (fun t ω => X (t + s) ω - X s ω) μ := by
  intro s
  have h := IsGaussianProcess.shift hG s
  simp_rw [add_comm s] at h
  exact h

/-- A Wiener process has square-integrable increments. -/
lemma wiener_process_variance
    (X : ℕ → Ω → ℝ)
    (hG : IsGaussianProcess X μ)
    (hI : HasIndepIncrements X μ) :
    ∀ s t : ℕ, Integrable (fun ω => (X t ω - X s ω) ^ 2) μ := by
  intro s t
  have h : HasGaussianLaw (X t - X s) μ := hG.hasGaussianLaw_sub
  have hmem : MemLp (X t - X s) 2 μ := h.memLp (by norm_num)
  have key : (fun ω => (X t - X s) ω ^ 2) = (fun ω => ‖(X t - X s) ω‖ ^ (ENNReal.toReal 2)) := by
    ext ω; simp [Real.norm_eq_abs]
  rw [show (fun ω => (X t ω - X s ω) ^ 2) = (fun ω => (X t - X s) ω ^ 2) from rfl, key]
  exact hmem.integrable_norm_rpow (by norm_num) (by norm_num)

/-!
  ### Wiener martingale property

  Proof strategy:
  1. Adapted: X i is ℱ i-measurable by Filtration.stronglyAdapted_natural.
  2. CE step: E[X j | ℱ i] = E[X i | ℱ i] + E[X j - X i | ℱ i]
              = X i + ∫(X j - X i) dμ = X i + 0 = X i.
     - E[X i | ℱ i] = X i via condExp_of_stronglyMeasurable.
     - E[X j - X i | ℱ i] = ∫(X j - X i) via condExp_indep_eq,
       using Indep(comap(X j - X i), ℱ i) μ from increment_indep_naturalFiltration.
-/

set_option maxHeartbeats 800000 in
/-- Independence of X j - X i from the natural filtration at time i.
    Proof: iIndep of consecutive increments + indep_iSup_of_disjoint + σ-algebra containments.
    Hypothesis X 0 = 0 ensures comap(X 0) = ⊥ ≤ everything. -/
private lemma increment_indep_naturalFiltration
    (X : ℕ → Ω → ℝ)
    (hXm : ∀ i, Measurable (X i))
    (hI : HasIndepIncrements X μ)
    (h0 : X 0 = 0)
    (i j : ℕ) (hij : i ≤ j) :
    Indep
      (MeasurableSpace.comap (X j - X i) (inferInstance : MeasurableSpace ℝ))
      (⨆ k ≤ i, MeasurableSpace.comap (X k) (inferInstance : MeasurableSpace ℝ))
      μ := by
  -- Y n = X(n+1) - X n; jointly iIndep (from HasIndepIncrements with identity sequence)
  set Y : ℕ → Ω → ℝ := fun n ω => X (n + 1) ω - X n ω
  have hYfun : iIndepFun Y μ := hI.nat monotone_id
  have hiIndep : iIndep (fun n => MeasurableSpace.comap (Y n) inferInstance) μ := by
    have h := hYfun; rwa [iIndepFun_iff_iIndep] at h
  have hY_le : ∀ n, MeasurableSpace.comap (Y n) (inferInstance : MeasurableSpace ℝ) ≤
      (inferInstance : MeasurableSpace Ω) := by
    intro n; exact measurable_iff_comap_le.mp ((hXm (n + 1)).sub (hXm n))
  -- X k = ∑ n < k, Y n (by induction using X 0 = 0)
  have hXsum : ∀ k : ℕ, X k = fun ω => ∑ n ∈ Finset.range k, Y n ω := fun k => by
    induction k with
    | zero => funext ω; simp [congr_fun h0 ω]
    | succ k ih => funext ω; simp only [Finset.sum_range_succ, ← congr_fun ih ω]; ring
  -- Measurability of ∑ n ∈ s, Y n w.r.t. ⨆ n ∈ S, comap(Y n) when s ⊆ S
  have hmeas : ∀ (s : Finset ℕ) (S : Set ℕ), (∀ n ∈ s, n ∈ S) →
      Measurable[⨆ n ∈ S, MeasurableSpace.comap (Y n) inferInstance]
        (fun ω => ∑ n ∈ s, Y n ω) := by
    intro s S hS
    induction s using Finset.induction_on with
    | empty => exact measurable_const
    | @insert a s' ha ih =>
      simp only [Finset.sum_insert ha]
      apply Measurable.add
      · apply measurable_iff_comap_le.mpr
        exact le_iSup₂ (f := fun n _ => MeasurableSpace.comap (Y n) (inferInstance : MeasurableSpace ℝ))
          a (hS a (Finset.mem_insert_self a s'))
      · exact ih (fun n hn => hS n (Finset.mem_insert_of_mem hn))
  -- X j - X i = ∑ n ∈ Ico i j, Y n
  have hXdiff : X j - X i = fun ω => ∑ n ∈ Finset.Ico i j, Y n ω := by
    ext ω
    simp only [Pi.sub_apply, congr_fun (hXsum j) ω, congr_fun (hXsum i) ω]
    have hrange : Finset.range j = Finset.range i ∪ Finset.Ico i j := by
      ext n; simp [Finset.mem_range, Finset.mem_Ico]; omega
    rw [hrange, Finset.sum_union (by
      simp only [Finset.disjoint_left, Finset.mem_range, Finset.mem_Ico]
      intro n h1 h2; omega)]
    ring
  -- comap(X j - X i) ≤ ⨆ n ∈ Ico i j, comap(Y n)
  have hcovX : MeasurableSpace.comap (X j - X i) inferInstance ≤
               ⨆ n ∈ (Set.Ico i j), MeasurableSpace.comap (Y n) inferInstance := by
    rw [hXdiff]
    exact measurable_iff_comap_le.mp
      (hmeas (Finset.Ico i j) (Set.Ico i j) (fun n hn => Set.mem_Ico.mpr (Finset.mem_Ico.mp hn)))
  -- ⨆ k ≤ i, comap(X k) ≤ ⨆ n ∈ Iio i, comap(Y n)
  have hcovNat : ⨆ k ≤ i, MeasurableSpace.comap (X k) inferInstance ≤
                 ⨆ n ∈ Set.Iio i, MeasurableSpace.comap (Y n) inferInstance := by
    apply iSup₂_le
    intro k hki
    rw [hXsum k]
    exact measurable_iff_comap_le.mp
      (hmeas (Finset.range k) (Set.Iio i)
        (fun n hn => Set.mem_Iio.mpr (Nat.lt_of_lt_of_le (Finset.mem_range.mp hn) hki)))
  -- Set.Ico i j and Set.Iio i are disjoint
  have hdisj : Disjoint (Set.Ico i j) (Set.Iio i) := by
    rw [Set.disjoint_iff]
    intro n hn
    simp only [Set.mem_inter_iff, Set.mem_Ico, Set.mem_Iio] at hn
    omega
  -- indep_iSup_of_disjoint gives Indep on the increment-based σ-algebras
  have hstep : Indep
      (⨆ n ∈ Set.Ico i j, MeasurableSpace.comap (Y n) inferInstance)
      (⨆ n ∈ Set.Iio i, MeasurableSpace.comap (Y n) inferInstance) μ :=
    indep_iSup_of_disjoint hY_le hiIndep hdisj
  -- Conclude by Indep_iff + monotonicity (no Indep.mono in Mathlib; prove inline)
  rw [Indep_iff] at hstep ⊢
  exact fun t1 t2 ht1 ht2 => hstep t1 t2 (hcovX t1 ht1) (hcovNat t2 ht2)

/-- A zero-mean Gaussian process with independent increments and zero start
    is a martingale with respect to its natural filtration. -/
theorem wiener_martingale_natural
    (X : ℕ → Ω → ℝ)
    (hXm : ∀ i, Measurable (X i))
    (hG : IsGaussianProcess X μ)
    (hI : HasIndepIncrements X μ)
    (h0 : X 0 = 0)
    (hmean : ∀ i j : ℕ, i ≤ j → ∫ ω, (X j ω - X i ω) ∂μ = 0) :
    let ℱ := Filtration.natural X (fun i => (hXm i).stronglyMeasurable)
    Martingale X ℱ μ := by
  intro ℱ
  -- 1. Adapted
  have hadapted : StronglyAdapted ℱ X :=
    Filtration.stronglyAdapted_natural (fun i => (hXm i).stronglyMeasurable)
  refine ⟨hadapted, fun i j hij => ?_⟩
  -- 2. Integrability
  have hXi_int : Integrable (X i) μ :=
    HasGaussianLaw.integrable (hG.hasGaussianLaw_eval i)
  have hXj_int : Integrable (X j) μ :=
    HasGaussianLaw.integrable (hG.hasGaussianLaw_eval j)
  have hincr_int : Integrable (X j - X i) μ := hXj_int.sub hXi_int
  -- 3. CE of X i w.r.t. ℱ i equals X i
  have hce_Xi : μ[X i | ℱ i] = X i :=
    condExp_of_stronglyMeasurable (ℱ.le i) (hadapted i) hXi_int
  -- 4. comap(X j - X i) ≤ ambient (measurability)
  have hle_incr : MeasurableSpace.comap (X j - X i) (inferInstance : MeasurableSpace ℝ) ≤
      (inferInstance : MeasurableSpace Ω) :=
    ((hXm j).sub (hXm i)).comap_le
  -- 5. X j - X i is StronglyMeasurable w.r.t. its own comap σ-algebra
  have hincr_sm :
      StronglyMeasurable[MeasurableSpace.comap (X j - X i) (inferInstance : MeasurableSpace ℝ)]
        (X j - X i) := by
    rw [stronglyMeasurable_iff_measurable_separable]
    exact ⟨measurable_iff_comap_le.mpr le_rfl, TopologicalSpace.IsSeparable.of_separableSpace _⟩
  -- 6. Indep(comap(X j - X i), ℱ i) μ
  have hindep : Indep
      (MeasurableSpace.comap (X j - X i) (inferInstance : MeasurableSpace ℝ)) (ℱ i) μ := by
    show Indep (MeasurableSpace.comap (X j - X i) _)
               (⨆ k ≤ i, MeasurableSpace.comap (X k) _) μ
    exact increment_indep_naturalFiltration X hXm hI h0 i j hij
  -- 7. CE of X j - X i equals its mean (by independence)
  have hce_incr : μ[X j - X i | ℱ i] =ᵐ[μ] fun _ => ∫ ω, (X j - X i) ω ∂μ :=
    condExp_indep_eq hle_incr (ℱ.le i) hincr_sm hindep
  -- 8. CE linearity
  have hce_add : μ[X j | ℱ i] =ᵐ[μ] μ[X i | ℱ i] + μ[X j - X i | ℱ i] := by
    have h := condExp_add hXi_int hincr_int (ℱ i)
    have hd : X i + (X j - X i) = X j := by
      ext ω; simp only [Pi.add_apply, Pi.sub_apply]; ring
    rwa [hd] at h
  -- 9. Combine
  filter_upwards [hce_add, hce_incr] with ω hadd hincr
  simp only [Pi.add_apply] at hadd
  rw [hadd, hce_Xi, hincr]
  simp [hmean i j hij]

end DESol.SDE
