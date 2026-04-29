import Mathlib
import Aesop

set_option linter.unusedVariables false

open MeasureTheory ProbabilityTheory Filter Set

namespace AutoPaper_2604_21616

theorem auto_proof_8_rank_one_triangle
    {m n : Type*} [Fintype m] [Fintype n]
    {E : Type*} [NormedAddCommGroup E] [NormedSpace ℝ E]
    (A : m → n → ℝ) (B : m → n → E) :
    ‖∑ i, ∑ j, A i j • B i j‖ ≤ ∑ i, ∑ j, |A i j| * ‖B i j‖ := by
  calc
    ‖∑ i, ∑ j, A i j • B i j‖ ≤ ∑ i, ‖∑ j, A i j • B i j‖ := by
      exact norm_sum_le Finset.univ (fun i => ∑ j, A i j • B i j)
    _ ≤ ∑ i, ∑ j, ‖A i j • B i j‖ := by
      exact Finset.sum_le_sum (fun i _ => norm_sum_le Finset.univ (fun j => A i j • B i j))
    _ = ∑ i, ∑ j, |A i j| * ‖B i j‖ := by
      simp [norm_smul, Real.norm_eq_abs]

end AutoPaper_2604_21616
