import Mathlib
import Aesop

set_option linter.unusedVariables false

open MeasureTheory ProbabilityTheory Filter Set

namespace AutoPaper_2604_21884

def AutoCenteredFluctuationCondition (eps alpha s2 theta : ℝ) : Prop :=
    s2 < 4 * alpha - 3 - (3 / 2) * theta - eps

def AutoSameColorContractionCondition (eps alpha s2 theta : ℝ) : Prop :=
    3 - 4 * alpha + theta * (s2 + eps) < 0

def AutoNaiveLowHighMappingCondition (eps alpha s1 : ℝ) : Prop :=
    s1 < 2 * alpha - 3 / 2 - eps

def AutoBasicProductTheoryCondition (eps alpha s1 s2 : ℝ) : Prop :=
    3 / 2 - alpha + eps < s2 ∧
    s2 < s1 + 2 * alpha - 3 / 2 - eps

def AutoQuadraticStrichartzClosureCondition (alpha s1 s2 : ℝ) : Prop :=
    s2 ≤ s1 + alpha / 4

def AutoProductsViUjCondition (alpha s1 s2 rhoV : ℝ) : Prop :=
    rhoV + s1 > 0 ∧
    s2 - alpha < rhoV

def AutoAdmissibleFull (eps alpha s1 s2 theta rhoV : ℝ) : Prop :=
    0 < s1 ∧
    s1 < s2 ∧
    0 < theta ∧
    theta < 1 ∧
    AutoCenteredFluctuationCondition eps alpha s2 theta ∧
    AutoSameColorContractionCondition eps alpha s2 theta ∧
    AutoNaiveLowHighMappingCondition eps alpha s1 ∧
    AutoBasicProductTheoryCondition eps alpha s1 s2 ∧
    AutoQuadraticStrichartzClosureCondition alpha s1 s2 ∧
    AutoProductsViUjCondition alpha s1 s2 rhoV

theorem auto_def_admissible_iff (eps alpha s1 s2 theta rhoV : ℝ) :
    AutoAdmissibleFull eps alpha s1 s2 theta rhoV ↔
    (0 < s1 ∧
    s1 < s2 ∧
    0 < theta ∧
    theta < 1 ∧
    s2 < 4 * alpha - 3 - (3 / 2) * theta - eps ∧
    3 - 4 * alpha + theta * (s2 + eps) < 0 ∧
    s1 < 2 * alpha - 3 / 2 - eps ∧
    3 / 2 - alpha + eps < s2 ∧
    s2 < s1 + 2 * alpha - 3 / 2 - eps ∧
    s2 ≤ s1 + alpha / 4 ∧
    rhoV + s1 > 0 ∧
    s2 - alpha < rhoV) := by
  unfold AutoAdmissibleFull AutoCenteredFluctuationCondition
    AutoSameColorContractionCondition AutoNaiveLowHighMappingCondition
    AutoBasicProductTheoryCondition AutoQuadraticStrichartzClosureCondition
    AutoProductsViUjCondition
  aesop

def AutoRemark20ConditionRoles (eps alpha s1 s2 theta rhoV : ℝ) : Prop :=
    AutoCenteredFluctuationCondition eps alpha s2 theta ∧
    AutoSameColorContractionCondition eps alpha s2 theta ∧
    AutoNaiveLowHighMappingCondition eps alpha s1 ∧
    AutoBasicProductTheoryCondition eps alpha s1 s2 ∧
    AutoQuadraticStrichartzClosureCondition alpha s1 s2 ∧
    AutoProductsViUjCondition alpha s1 s2 rhoV

theorem auto_remark_20_condition_roles_iff (eps alpha s1 s2 theta rhoV : ℝ) :
    AutoRemark20ConditionRoles eps alpha s1 s2 theta rhoV ↔
    (s2 < 4 * alpha - 3 - (3 / 2) * theta - eps ∧
    3 - 4 * alpha + theta * (s2 + eps) < 0 ∧
    s1 < 2 * alpha - 3 / 2 - eps ∧
    (3 / 2 - alpha + eps < s2 ∧ s2 < s1 + 2 * alpha - 3 / 2 - eps) ∧
    s2 ≤ s1 + alpha / 4 ∧
    (rhoV + s1 > 0 ∧ s2 - alpha < rhoV)) := by
  unfold AutoRemark20ConditionRoles AutoCenteredFluctuationCondition
    AutoSameColorContractionCondition AutoNaiveLowHighMappingCondition
    AutoBasicProductTheoryCondition AutoQuadraticStrichartzClosureCondition
    AutoProductsViUjCondition
  aesop

def AutoDyadicSharpnessCriticalExponent (alpha : ℝ) : Prop :=
    3 - 4 * alpha = 0

theorem auto_prop_sharpness_critical_exponent_iff (alpha : ℝ) :
    AutoDyadicSharpnessCriticalExponent alpha ↔ alpha = 3 / 4 := by
  unfold AutoDyadicSharpnessCriticalExponent
  constructor
  · intro h
    linarith
  · intro h
    linarith

def AutoStrongLowHighOperatorCondition (eps alpha s2 theta : ℝ) : Prop :=
    s2 < 4 * alpha - 3 - (3 / 2) * theta - eps ∧
    3 - 4 * alpha + theta * (s2 + eps) < 0

theorem auto_prop_det_contraction_condition_rearrange (eps alpha s2 theta : ℝ) :
    AutoStrongLowHighOperatorCondition eps alpha s2 theta →
    s2 + (3 / 2) * theta + eps < 4 * alpha - 3 ∧
    theta * (s2 + eps) < 4 * alpha - 3 := by
  intro h
  constructor
  · linarith [h.1]
  · linarith [h.2]

end AutoPaper_2604_21884
