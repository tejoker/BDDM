import Mathlib
import Aesop

set_option linter.unusedVariables false

open MeasureTheory ProbabilityTheory Filter Set

namespace AutoPaper_2304_09598

theorem auto_defin_14 {alpha : Type*} [Preorder alpha] (a : Multiset (alpha × alpha)) : (∃ (n : ℕ) (delta : Fin n → alpha × alpha), a = Multiset.ofList (List.ofFn delta) ∧
    ∀ (i j : Fin n), i.val < j.val → (delta i).1 < (delta j).1 ∧ (delta i).2 < (delta j).2) = (∃ (n : ℕ) (delta : Fin n → alpha × alpha), a = Multiset.ofList (List.ofFn delta) ∧
    ∀ (i j : Fin n), i.val < j.val → (delta i).1 < (delta j).1 ∧ (delta i).2 < (delta j).2) := by
  rfl

end AutoPaper_2304_09598
