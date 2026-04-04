import Mathlib.Tactic
import Mathlib.Data.Finset.Card
import Mathlib.Combinatorics.Pigeonhole

/-!
# DESol Foundational Lemmas

Manually verified theorems serving as the seed for the internal KG trusted layer.
All theorems here are FULLY_PROVEN: steps verified, all assumptions GROUNDED_MATHLIB.
-/

namespace DESol.Foundations

/-- The sum of the first n natural numbers equals n*(n+1)/2. -/
theorem sum_first_n (n : ℕ) : 2 * ∑ i ∈ Finset.range (n + 1), i = n * (n + 1) := by
  induction n with
  | zero => simp
  | succ n ih =>
    rw [Finset.sum_range_succ]
    linarith [ih]

/-- The square of any integer is non-negative. -/
theorem int_sq_nonneg (n : ℤ) : 0 ≤ n ^ 2 := sq_nonneg n

/-- Pigeonhole: if more objects than boxes, some box has ≥ 2. -/
theorem pigeonhole_two {α β : Type*} [DecidableEq β] (f : α → β) (s : Finset α)
    (t : Finset β) (hst : t.card < s.card) (hf : ∀ x ∈ s, f x ∈ t) :
    ∃ x ∈ s, ∃ y ∈ s, x ≠ y ∧ f x = f y :=
  Finset.exists_ne_map_eq_of_card_lt_of_maps_to hst hf

/-!
## Generalized Fibonacci recurrence (arXiv:2312.13098)

For a sequence satisfying a two-term recurrence with offsets f and d
(where 1 ≤ f ≤ d), the nth term equals the sum of the (n-f)th and (n-d)th terms
for all n ≥ d. This generalizes the standard Fibonacci recurrence (f=1, d=2).

Reference: De Prisco et al., "A simple proof for generalized Fibonacci numbers
with dying rabbits", arXiv:2312.13098.
-/

/-- A sequence satisfying the generalized Fibonacci recurrence F(n) = F(n-f) + F(n-d)
    for all n ≥ d, where 1 ≤ f ≤ d. This captures both Fibonacci (f=1,d=2) and
    Padovan (f=2,d=3) sequences as special cases.

    The recurrence is stated here for natural-number-indexed sequences where
    we track values via a function F : ℕ → ℕ and assume the recurrence holds
    for all indices beyond the initial segment. -/
def SatisfiesGenFibRecurrence (F : ℕ → ℕ) (f d : ℕ) : Prop :=
  1 ≤ f ∧ f ≤ d ∧ ∀ n, d ≤ n → F n = F (n - f) + F (n - d)

/-- If F satisfies the generalized Fibonacci recurrence, then the standard
    Fibonacci recurrence is the special case f=1, d=2. -/
theorem fib_is_special_case (F : ℕ → ℕ)
    (hF : SatisfiesGenFibRecurrence F 1 2) :
    ∀ n, 2 ≤ n → F n = F (n - 1) + F (n - 2) := by
  intro n hn
  exact hF.2.2 n hn


/-- The sum of a geometric series: ∑_{i=0}^{n-1} r^i = (r^n - 1) / (r - 1) for r ≠ 1.
    In integer form: (r - 1) * ∑_{i=0}^{n-1} r^i = r^n - 1. -/
theorem geom_sum_formula (r : ℤ) (n : ℕ) :
    (r - 1) * ∑ i ∈ Finset.range n, r ^ i = r ^ n - 1 := by
  induction n with
  | zero => simp
  | succ n ih =>
    rw [Finset.sum_range_succ]
    ring_nf
    linarith [ih]

/-- Bernoulli's inequality: for any n : ℕ and x ≥ -1, (1 + x)^n ≥ 1 + n * x.
    This is a non-trivial bound used in analysis and combinatorics.
    Proof: by induction; the step uses (1+nx)(1+x) = 1+(n+1)x + nx² ≥ 1+(n+1)x. -/
theorem bernoulli_ineq (n : ℕ) (x : ℝ) (hx : -1 ≤ x) :
    1 + n * x ≤ (1 + x) ^ n := by
  induction n with
  | zero => simp
  | succ n ih =>
    rw [pow_succ]
    have h1 : 0 ≤ 1 + x := by linarith
    have step : 1 + (↑n + 1) * x ≤ (1 + ↑n * x) * (1 + x) := by
      nlinarith [sq_nonneg x, sq_nonneg (↑n * x)]
    calc 1 + (↑(n + 1) : ℝ) * x
        = 1 + (↑n + 1) * x := by push_cast; ring
      _ ≤ (1 + ↑n * x) * (1 + x) := step
      _ ≤ (1 + x) ^ n * (1 + x) := by
          exact mul_le_mul_of_nonneg_right ih h1

end DESol.Foundations
