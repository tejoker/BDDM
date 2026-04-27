import Mathlib

/-!
Curated verified core for arXiv paper 2604.21884.

This file intentionally proves only statements whose Lean formulation is
faithful to the paper text and does not depend on paper-local stochastic PDE
axioms.  The first useful slice is the deterministic parameter bookkeeping
around the paper's admissible range.
-/

namespace PaperProofs
namespace Paper_2604_21884

def Admissible (eps alpha s1 s2 theta : ℝ) : Prop :=
  0 < s1 ∧
  s1 < s2 ∧
  0 < theta ∧
  theta < 1 ∧
  s2 < 4 * alpha - 3 - (3 / 2) * theta - eps

def AdmissibleSet (eps : ℝ) : Set (ℝ × ℝ × ℝ × ℝ) :=
  {p | Admissible eps p.1 p.2.1 p.2.2.1 p.2.2.2}

theorem admissible_intro
    {eps alpha s1 s2 theta : ℝ}
    (h1 : 0 < s1)
    (h2 : s1 < s2)
    (h3 : 0 < theta)
    (h4 : theta < 1)
    (h5 : s2 < 4 * alpha - 3 - (3 / 2) * theta - eps) :
    Admissible eps alpha s1 s2 theta := by
  exact ⟨h1, h2, h3, h4, h5⟩

theorem remark_20_admissible_tuple
    (alpha s1 s2 theta eps : ℝ)
    (h1 : 0 < s1)
    (h2 : s1 < s2)
    (h3 : 0 < theta)
    (h4 : theta < 1)
    (h5 : s2 < 4 * alpha - 3 - (3 / 2) * theta - eps) :
    (alpha, s1, s2, theta) ∈ AdmissibleSet eps := by
  exact admissible_intro h1 h2 h3 h4 h5

theorem remark_20_translated_shape
    (alpha s1 s2 theta eps : ℝ)
    (h1 : 0 < s1)
    (h2 : s1 < s2)
    (h3 : 0 < theta)
    (h4 : theta < 1)
    (h5 : s2 < 4 * alpha - 3 - (3 / 2) * theta - eps) :
    (alpha, s1, s2, theta) ∈
      {p : ℝ × ℝ × ℝ × ℝ |
        0 < p.2.1 ∧
        p.2.1 < p.2.2.1 ∧
        0 < p.2.2.2 ∧
        p.2.2.2 < 1 ∧
        p.2.2.1 < 4 * p.1 - 3 - (3 / 2) * p.2.2.2 - eps} := by
  exact ⟨h1, h2, h3, h4, h5⟩

theorem admissible_s1_positive
    {eps alpha s1 s2 theta : ℝ}
    (h : Admissible eps alpha s1 s2 theta) :
    0 < s1 := by
  exact h.1

theorem admissible_s1_lt_s2
    {eps alpha s1 s2 theta : ℝ}
    (h : Admissible eps alpha s1 s2 theta) :
    s1 < s2 := by
  exact h.2.1

theorem admissible_theta_pos
    {eps alpha s1 s2 theta : ℝ}
    (h : Admissible eps alpha s1 s2 theta) :
    0 < theta := by
  exact h.2.2.1

theorem admissible_theta_lt_one
    {eps alpha s1 s2 theta : ℝ}
    (h : Admissible eps alpha s1 s2 theta) :
    theta < 1 := by
  exact h.2.2.2.1

theorem admissible_upper_bound
    {eps alpha s1 s2 theta : ℝ}
    (h : Admissible eps alpha s1 s2 theta) :
    s2 < 4 * alpha - 3 - (3 / 2) * theta - eps := by
  exact h.2.2.2.2

end Paper_2604_21884
end PaperProofs
