-- Auto-generated paper theory module
-- paper_id: 2308.07029
-- domain: default

import Mathlib
import Aesop

open MeasureTheory ProbabilityTheory Filter Set

namespace Paper_2308_07029

-- note: declared 58 paper-local symbol(s) from inventory
-- note: definition stubs are notation grounding only and are not proof-countable

-- ------------------------------------------------------------------
-- Paper-local definitions and explicit axiom debt
-- Any result depending on this module must be reported as proved
-- modulo any paper-local axioms below, not as unconditional closure.
-- ------------------------------------------------------------------

-- Definition stubs ground paper-local identifiers before proof search.
-- They are transparent Lean definitions for elaboration, not hidden proofs of paper claims.
def C_T : Set (ℝ → ℝ) := Set.univ

def L2Space : Set (ℝ → ℝ) := Set.univ

def omega (_i _k : ℕ) : ℝ := 0

def infty : ℝ := 0

def C : ℝ := 0

def a : ℝ := 0

def c_1 : ℝ := 0

def c_2 : ℝ := 0

def eps_X : ℝ := 0

def esti_Y : ℝ := 0

def eta_1 : ℝ := 0

def eta_2 : ℝ := 0

def ge0 : ℝ := 0

def hyungbin2015 : ℝ := 0

def int_0 : ℝ := 0

def r_1 : ℝ := 0

def s_0 : ℝ := 0

def t_0 : ℝ := 0

def t_1 : ℝ := 0

def t_2 : ℝ := 0

def tau_0 : ℝ := 0

def theta : ℝ := 0

def x_0 : ℝ := 0

def x_1 : ℝ := 0

def xi_0 : ℝ := 0

def xi_1 : ℝ := 0

def n_0 : ℝ := 0

def gradomega2 : ℝ := 0

def hK1 : ℝ := 0

def hK2 : ℝ := 0

def u_1 : ℝ := 0

def satisfies_BSDE : ℝ := 0

def t1 : ℝ := 0

def t2 : ℝ := 0

def h_Assumptions : ℝ := 0

def h_C_pos : ℝ := 0

def h_Z_eq : ℝ := 0

def n1 : ℝ := 0

def h1 : ℝ := 0

def h2 : ℝ := 0

def h3 : ℝ := 0

def h4 : ℝ := 0

def h5 : ℝ := 0

def h6 : ℝ := 0

def h_assumptions_asmsde_asmbsde_hold_1 : ℝ := 0

def h_YZ_sol : ℝ := 0

def h_W : ℝ := 0

def lipschitz_H : ℝ := 0

def y1 : ℝ := 0

def y2 : ℝ := 0

def d_dtMeasureTheory : ℝ := 0

def hW_copies : ℝ := 0

def hW_indep : ℝ := 0

def l1 : ℝ := 0

def l2 : ℝ := 0

def convergence_truncated_BSDE : ℝ := 0

def h_BSDE : ℝ := 0

-- Local lemmas / theorem-like facts.
-- Explicit axioms / unresolved paper assumptions.
axiom d_dts : Measure ℝ

-- Aesop tactic registration for paper-local axioms.
attribute [aesop safe apply] d_dts

end Paper_2308_07029

export Paper_2308_07029 (C_T L2Space omega infty C a c_1 c_2 eps_X esti_Y eta_1 eta_2 ge0 hyungbin2015 int_0 r_1 s_0 t_0 t_1 t_2 tau_0 theta x_0 x_1 xi_0 xi_1 n_0 gradomega2 hK1 hK2 u_1 satisfies_BSDE t1 t2 h_Assumptions h_C_pos h_Z_eq n1 h1 h2 h3 h4 h5 h6 h_assumptions_asmsde_asmbsde_hold_1 h_YZ_sol h_W lipschitz_H y1 y2 d_dtMeasureTheory hW_copies hW_indep l1 l2 convergence_truncated_BSDE h_BSDE d_dts)
