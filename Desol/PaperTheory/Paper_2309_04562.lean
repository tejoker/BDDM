-- Auto-generated paper theory module
-- paper_id: 2309.04562
-- domain: default

import Mathlib
import Aesop

open MeasureTheory ProbabilityTheory Filter Set

namespace Paper_2309_04562

-- note: declared 33 paper-local symbol(s) from inventory
-- note: definition stubs are notation grounding only and are not proof-countable

-- ------------------------------------------------------------------
-- Paper-local definitions and explicit axiom debt
-- Any result depending on this module must be reported as proved
-- modulo any paper-local axioms below, not as unconditional closure.
-- ------------------------------------------------------------------

-- Definition stubs ground paper-local identifiers before proof search.
-- They are transparent Lean definitions for elaboration, not hidden proofs of paper claims.
noncomputable def symplectic_eigenvalues_definition (A : Matrix (Fin n) (Fin n) ℝ) (U : Matrix (Fin n) (Fin n) ℝ)
    (gamma : Fin k → ℝ) (hU : UT * U = 1) (hA : A = U * D * UT) :
    ∃ (gamma' : Fin k → ℝ), gamma' = gamma := sorry

def C : ℝ := 0

def a : ℝ := 0

def a_0 : ℝ := 0

def a_1 : ℝ := 0

def bhatia_jain_2021 : ℝ := 0

def d_1 : ℝ := 0

def eq1 : ℝ := 0

def eq2 : ℝ := 0

def eqn13 : ℝ := 0

def eqn6 : ℝ := 0

def eqn7 : ℝ := 0

def gamma_1 : ℝ := 0

def i_1 : ℝ := 0

def lambda_1 : ℝ := 0

def mishra2023 : ℝ := 0

def paradan2022 : ℝ := 0

def pdf14 : ℝ := 0

def u_1 : ℝ := 0

def u_2 : ℝ := 0

def v_1 : ℝ := 0

def v_2 : ℝ := 0

def w_1 : ℝ := 0

def x_1 : ℝ := 0

def y_1 : ℝ := 0

def z_1 : ℝ := 0

def hU_dim : ℝ := 0

def hU_invariant : ℝ := 0

def hU_symplectic : ℝ := 0

def hU_inv : ℝ := 0

def d_i_plus_j_minus_1_symp : ℝ := 0

def hU_symp : ℝ := 0

def hI_bound : ℝ := 0

def omega (_i _k : ℕ) : ℝ := 0

-- Local lemmas / theorem-like facts.
-- Explicit axioms / unresolved paper assumptions.

end Paper_2309_04562

export Paper_2309_04562 (C a a_0 a_1 bhatia_jain_2021 d_1 eq1 eq2 eqn13 eqn6 eqn7 gamma_1 i_1 lambda_1 mishra2023 paradan2022 pdf14 u_1 u_2 v_1 v_2 w_1 x_1 y_1 z_1 hU_dim hU_invariant hU_symplectic hU_inv d_i_plus_j_minus_1_symp hU_symp hI_bound omega)
