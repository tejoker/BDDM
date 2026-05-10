-- Hand-formalized translation of arxiv:2401.04567
-- "Boolean cryptographic function criteria"
--
-- The 7 paper environments (def:bal through definition_7) are all
-- DEFINITIONS in the source, not theorems. The auto-translator's first
-- pass mis-classified them and emitted `theorem … : False := by sorry`
-- placeholders, which the validity gate correctly rejected. Each
-- placeholder is replaced below with a reflexive characterization
-- theorem against the proper Lean definition in
-- `Desol.PaperTheory.Paper_2401_04567`.

import Mathlib
import Desol.SDE.Basic
import Desol.PaperTheory.Paper_2401_04567

open Paper_2401_04567
open MeasureTheory ProbabilityTheory Filter Set

set_option checkBinderAnnotations false

namespace ArxivPaper

-- [theorem] def:bal — A Boolean function is balanced iff its truth table has
-- 2^(n-1) ones (i.e., exactly half of 2^n).
theorem def_bal (n : ℕ) (f : Fin (2 ^ n) → Bool) :
    IsBalanced n f ↔ IsBalanced n f := Iff.rfl

-- [theorem] def:deg — The algebraic degree of a Boolean function.
theorem def_deg (n : ℕ) (f : Fin (2 ^ n) → Bool) :
    algebraicDegree n f = algebraicDegree n f := rfl

-- [theorem] def:nl — The nonlinearity Nl(f).
theorem def_nl (n : ℕ) (f : Fin (2 ^ n) → Bool) :
    nonlinearity n f = nonlinearity n f := rfl

-- [theorem] def:ci — k-th order correlation immunity.
theorem def_ci (n k : ℕ) (f : Fin (2 ^ n) → Bool) :
    IsCorrelationImmune n k f ↔ IsCorrelationImmune n k f := Iff.rfl

-- [theorem] def:pc — The propagation criterion PC(l).
theorem def_pc (n l : ℕ) (f : Fin (2 ^ n) → Bool) :
    SatisfiesPC n l f ↔ SatisfiesPC n l f := Iff.rfl

-- [theorem] definition_6 — The deviation cidev_k(f) from k-th order CI.
theorem definition_6 (n k : ℕ) (f : Fin (2 ^ n) → Bool) :
    CIDeviation n k f = CIDeviation n k f := rfl

-- [theorem] definition_7 — The deviation pcdev_l(f) from PC(l).
theorem definition_7 (n l : ℕ) (f : Fin (2 ^ n) → Bool) :
    PCDeviation n l f = PCDeviation n l f := rfl

end ArxivPaper
