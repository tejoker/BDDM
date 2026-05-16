-- Auto-generated paper theory module
-- paper_id: 1910.07464
-- domain: default

import Mathlib
import Aesop

open MeasureTheory ProbabilityTheory Filter Set

namespace Paper_1910_07464

-- note: declared 168 paper-local symbol(s) from inventory
-- note: definition stubs are notation grounding only and are not proof-countable

-- ------------------------------------------------------------------
-- Paper-local definitions and explicit axiom debt
-- Any result depending on this module must be reported as proved
-- modulo any paper-local axioms below, not as unconditional closure.
-- ------------------------------------------------------------------

-- Definition stubs ground paper-local identifiers before proof search.
-- They are transparent Lean definitions for elaboration, not hidden proofs of paper claims.
def L2Space : Set (ℝ → ℝ) := Set.univ

def omega (_i _k : ℕ) : ℝ := 0

def infty : ℝ := 0

def C : ℝ := 0

def a : ℝ := 0

def c_1 : ℝ := 0

def delta_0 : ℝ := 0

def downarrow0 : ℝ := 0

def equiv0 : ℝ := 0

def ge0 : ℝ := 0

def ge1 : ℝ := 0

def ge2 : ℝ := 0

def ge3 : ℝ := 0

def h_0 : ℝ := 0

def int_0 : ℝ := 0

def jul102 : ℝ := 0

def jul110 : ℝ := 0

def jul112 : ℝ := 0

def jul118 : ℝ := 0

def jul120 : ℝ := 0

def jul122 : ℝ := 0

def jul124 : ℝ := 0

def jul128 : ℝ := 0

def jul130 : ℝ := 0

def jul140 : ℝ := 0

def jul602 : ℝ := 0

def jul604 : ℝ := 0

def jun2502 : ℝ := 0

def jun2504 : ℝ := 0

def jun2510 : ℝ := 0

def jun2512 : ℝ := 0

def jun2514 : ℝ := 0

def jun2516 : ℝ := 0

def jun2518 : ℝ := 0

def lambda_1 : ℝ := 0

def lambda_2 : ℝ := 0

def lambda_G : ℝ := 0

def le0 : ℝ := 0

def le1 : ℝ := 0

def le2 : ℝ := 0

def le3 : ℝ := 0

def le8 : ℝ := 0

def limis0 : ℝ := 0

def log_2 : ℝ := 0

def ne0 : ℝ := 0

def nu_0 : ℝ := 0

def nu_1 : ℝ := 0

def nu_2 : ℝ := 0

def nu_T : ℝ := 0

def t_1 : ℝ := 0

def t_2 : ℝ := 0

def theta : ℝ := 0

def theta_M : ℝ := 0

def to0 : ℝ := 0

def u_1 : ℝ := 0

def u_2 : ℝ := 0

def u_N : ℝ := 0

def utf8 : ℝ := 0

def v_1 : ℝ := 0

def v_I : ℝ := 0

def v_N : ℝ := 0

def x_0 : ℝ := 0

def x_1 : ℝ := 0

def x_2 : ℝ := 0

def h_source_1_1 : ℝ := 0

def h_source_2_2 : ℝ := 0

def h_source_3_3 : ℝ := 0

def h_source_4_4 : ℝ := 0

def h_L_pos : ℝ := 0

def mu0 : ℝ := 0

def mu1 : ℝ := 0

def u0 : ℝ := 0

def u1 : ℝ := 0

def u2 : ℝ := 0

def eq_uPDE_many : ℝ := 0

def hL1 : ℝ := 0

def c1 : ℝ := 0

def hF0 : ℝ := 0

def hu1 : ℝ := 0

def hu2 : ℝ := 0

def t0 : ℝ := 0

def d_dtPaper_1910_07464 : ℝ := 0

def d_dtlam_G : ℝ := 0

def l1 : ℝ := 0

def v1 : ℝ := 0

def v2 : ℝ := 0

def h_strong_solution_upde_defined_1 : ℝ := 0

def hN1 : ℝ := 0

def hN2 : ℝ := 0

def hnu1 : ℝ := 0

def hnu2 : ℝ := 0

def nu1 : ℝ := 0

def nu2 : ℝ := 0

def hnu_0 : ℝ := 0

def hu0 : ℝ := 0

def t1 : ℝ := 0

def h_jul602 : ℝ := 0

def h_jul604 : ℝ := 0

def h_compact_topology_each_2 : ℝ := 0

def d_dtmu_G : ℝ := 0

def hnu_1 : ℝ := 0

def hnu_1_j : ℝ := 0

def hnu_1_j_e : ℝ := 0

def hnu_1_j_int : ℝ := 0

def hnu_1_j_mean : ℝ := 0

def hnu_2 : ℝ := 0

def hnu_2_j : ℝ := 0

def hnu_2_j_e : ℝ := 0

def hnu_2_j_int : ℝ := 0

def hnu_2_j_mean : ℝ := 0

def d_G_N : ℝ := 0

def f1 : ℝ := 0

def f2 : ℝ := 0

def hmu1 : ℝ := 0

def hmu2 : ℝ := 0

def mu2 : ℝ := 0

def h_w1 : ℝ := 0

def h_w2 : ℝ := 0

def h_w3 : ℝ := 0

def w1 : ℝ := 0

def w2 : ℝ := 0

def w3 : ℝ := 0

def hf_C : ℝ := 0

def hf_Linfty : ℝ := 0

def hf_Lp : ℝ := 0

def eq_thetaLproblem : ℝ := 0

def htheta_L : ℝ := 0

def hv_L : ℝ := 0

def theta_L : ℝ := 0

def v_L : ℝ := 0

def theta_solves_eq_thetaLic : ℝ := 0

def theta_solves_eq_thetaLproblem : ℝ := 0

def h_compact_topology_4 : ℝ := 0

def h_compact_topology_each_5 : ℝ := 0

def h_decreasing_outside_compact_2 : ℝ := 0

def h_symmetric_3 : ℝ := 0

def h_classical_solution_thetalproblem_4 : ℝ := 0

def hC_depends : ℝ := 0

def h0 : ℝ := 0

def h1 : ℝ := 0

def hpsi1 : ℝ := 0

def hpsi2 : ℝ := 0

def htheta1 : ℝ := 0

def htheta2 : ℝ := 0

def htheta_eq1 : ℝ := 0

def htheta_eq2 : ℝ := 0

def hv1 : ℝ := 0

def hv2 : ℝ := 0

def psi1 : ℝ := 0

def psi2 : ℝ := 0

def theta1 : ℝ := 0

def theta2 : ℝ := 0

def h2 : ℝ := 0

def q0 : ℝ := 0

def h_derivatives_most_polynomial_growth_4 : ℝ := 0

def h_schwartz_class_2 : ℝ := 0

def h_smooth_function_3 : ℝ := 0

def solution_to_KPZ_with_initial_condition : ℝ := 0

def hx1 : ℝ := 0

def hx12 : ℝ := 0

def hx2 : ℝ := 0

def x1 : ℝ := 0

def x2 : ℝ := 0

def hyp_L1_morespecific : ℝ := 0

def s0 : ℝ := 0

def hLambda_G : ℝ := 0

def d_G : ℝ := 0

-- Local lemmas / theorem-like facts.
-- Explicit axioms / unresolved paper assumptions.
axiom d_dts : Measure ℝ

-- Aesop tactic registration for paper-local axioms.
attribute [aesop safe apply] d_dts

end Paper_1910_07464

export Paper_1910_07464 (L2Space omega infty C a c_1 delta_0 downarrow0 equiv0 ge0 ge1 ge2 ge3 h_0 int_0 jul102 jul110 jul112 jul118 jul120 jul122 jul124 jul128 jul130 jul140 jul602 jul604 jun2502 jun2504 jun2510 jun2512 jun2514 jun2516 jun2518 lambda_1 lambda_2 lambda_G le0 le1 le2 le3 le8 limis0 log_2 ne0 nu_0 nu_1 nu_2 nu_T t_1 t_2 theta theta_M to0 u_1 u_2 u_N utf8 v_1 v_I v_N x_0 x_1 x_2 h_source_1_1 h_source_2_2 h_source_3_3 h_source_4_4 h_L_pos mu0 mu1 u0 u1 u2 eq_uPDE_many hL1 c1 hF0 hu1 hu2 t0 d_dtPaper_1910_07464 d_dtlam_G l1 v1 v2 h_strong_solution_upde_defined_1 hN1 hN2 hnu1 hnu2 nu1 nu2 hnu_0 hu0 t1 h_jul602 h_jul604 h_compact_topology_each_2 d_dtmu_G hnu_1 hnu_1_j hnu_1_j_e hnu_1_j_int hnu_1_j_mean hnu_2 hnu_2_j hnu_2_j_e hnu_2_j_int hnu_2_j_mean d_G_N f1 f2 hmu1 hmu2 mu2 h_w1 h_w2 h_w3 w1 w2 w3 hf_C hf_Linfty hf_Lp eq_thetaLproblem htheta_L hv_L theta_L v_L theta_solves_eq_thetaLic theta_solves_eq_thetaLproblem h_compact_topology_4 h_compact_topology_each_5 h_decreasing_outside_compact_2 h_symmetric_3 h_classical_solution_thetalproblem_4 hC_depends h0 h1 hpsi1 hpsi2 htheta1 htheta2 htheta_eq1 htheta_eq2 hv1 hv2 psi1 psi2 theta1 theta2 h2 q0 h_derivatives_most_polynomial_growth_4 h_schwartz_class_2 h_smooth_function_3 solution_to_KPZ_with_initial_condition hx1 hx12 hx2 x1 x2 hyp_L1_morespecific s0 hLambda_G d_G d_dts)
