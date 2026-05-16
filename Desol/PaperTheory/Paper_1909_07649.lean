-- Auto-generated paper theory module
-- paper_id: 1909.07649
-- domain: default

import Mathlib
import Aesop

open MeasureTheory ProbabilityTheory Filter Set

namespace Paper_1909_07649

-- note: declared 336 paper-local symbol(s) from inventory
-- note: definition stubs are notation grounding only and are not proof-countable

-- ------------------------------------------------------------------
-- Paper-local definitions and explicit axiom debt
-- Any result depending on this module must be reported as proved
-- modulo any paper-local axioms below, not as unconditional closure.
-- ------------------------------------------------------------------

-- Definition stubs ground paper-local identifiers before proof search.
-- They are transparent Lean definitions for elaboration, not hidden proofs of paper claims.
noncomputable def def_evaluation_maps (beta : ℝ) :
  W = (X,beta) := by

noncomputable def Nbeta (p1 p2 r : ℝ) (A : ℝ) (beta : ℝ) (x1 x2 x3 : ℝ) (h : A * c1 = 0) :
  p1p2r = def0 := sorry

noncomputable def def_tropical_constraint (E_x_i : ℤ) (p_i : ℤ) (u : ℝ → ℝ) (h_enumerate_minimal_cones_containing_1 : True) (h_each_2 : True) (h_endpoint_3 : True) :
  u E_x_i = p_i ∧ u E_ = -r ∧ rnot = 0 ∧ r = 0 := by

noncomputable def definition_87 (v_not : ℤ) :
  v_not = v := by

noncomputable def Paper_1909_07649_A_P_exists (P : Type*) [CommMonoid P] : Nonempty (Type*) := sorry

noncomputable def eq_log_fibre_dim_def (f : ℝ → ℝ) (x : ℝ) (y : ℝ) :
  y = f x := by

def omega (_i _k : ℕ) : ℝ := 0

def infty : ℝ := 0

def C : ℝ := 0

def a : ℝ := 0

def a_1 : ℝ := 0

def a_1v_1 : ℝ := 0

def a_2 : ℝ := 0

def a_2E : ℝ := 0

def a_2v_2 : ℝ := 0

def a_iD_i : ℝ := 0

def alpha_0 : ℝ := 0

def alpha_T : ℝ := 0

def alpha_X : ℝ := 0

def alpha_Y : ℝ := 0

def alpha_Z : ℝ := 0

def arXiv2009 : ℝ := 0

def b_1 : ℝ := 0

def b_2 : ℝ := 0

def b_3 : ℝ := 0

def beta_1 : ℝ := 0

def beta_2 : ℝ := 0

def c1 : ℝ := 0

def c_1 : ℝ := 0

def cap_2 : ℝ := 0

def chi_P : ℝ := 0

def chi_Q : ℝ := 0

def circ_1 : ℝ := 0

def construction_30 : ℝ := 0

def def0 : ℝ := 0

def diagram2 : ℝ := 0

def dim_2 : ℝ := 0

def e23 : ℝ := 0

def e28 : ℝ := 0

def ell_1 : ℝ := 0

def ell_2 : ℝ := 0

def ev_X : ℝ := 0

def example_101 : ℝ := 0

def example_7 : ℝ := 0

def extended1 : ℝ := 0

def extended2 : ℝ := 0

def extended3 : ℝ := 0

def f_1 : ℝ := 0

def f_2 : ℝ := 0

def f_F : ℝ := 0

def f_M : ℝ := 0

def f_S : ℝ := 0

def f_W : ℝ := 0

def f_W' : ℝ := 0

def foC_0 : ℝ := 0

def foM_y : ℝ := 0

def ge0 : ℝ := 0

def i_1 : ℝ := 0

def i_2 : ℝ := 0

def i_3 : ℝ := 0

def i_4 : ℝ := 0

def in_D : ℝ := 0

def in_T : ℝ := 0

def iplus1 : ℝ := 0

def k_1 : ℝ := 0

def k_2 : ℝ := 0

def k_3 : ℝ := 0

def kappa_1 : ℝ := 0

def kappa_2 : ℝ := 0

def lemma_140 : ℝ := 0

def lemma_77 : ℝ := 0

def mW_2 : ℝ := 0

def mainassociativity1 : ℝ := 0

def mainassociativity2 : ℝ := 0

def mirrorconstruction1 : ℝ := 0

def mirrorconstruction2 : ℝ := 0

def mu_2 : ℝ := 0

def nu_1 : ℝ := 0

def nu_2 : ℝ := 0

def nu_R : ℝ := 0

def omega_X : ℝ := 0

def oplus_R : ℝ := 0

def overline_1 : ℝ := 0

def overline_2 : ℝ := 0

def overline_T : ℝ := 0

def overline_W : ℝ := 0

def overline_X : ℝ := 0

def overline_Z : ℝ := 0

def p2 : ℝ := 0

def p_0 : ℝ := 0

def p_1 : ℝ := 0

def p_1p_2r : ℝ := 0

def p_1p_2s : ℝ := 0

def p_1sr : ℝ := 0

def p_2 : ℝ := 0

def p_2p_1r : ℝ := 0

def p_2p_3s : ℝ := 0

def p_2s : ℝ := 0

def p_3 : ℝ := 0

def p_n0 : ℝ := 0

def pi_1 : ℝ := 0

def pi_2 : ℝ := 0

def pi_3 : ℝ := 0

def pi_4 : ℝ := 0

def pi_5 : ℝ := 0

def pi_6 : ℝ := 0

def pm1 : ℝ := 0

def pr_1 : ℝ := 0

def pr_2 : ℝ := 0

def q_0 : ℝ := 0

def r_jD_j : ℝ := 0

def rho_1 : ℝ := 0

def rho_2 : ℝ := 0

def rightarrow_2 : ℝ := 0

def rightarrow_P : ℝ := 0

def rightarrow_Q : ℝ := 0

def rightarrow_T : ℝ := 0

def runningexample1 : ℝ := 0

def runningexample2 : ℝ := 0

def runningexample3 : ℝ := 0

def s_0 : ℝ := 0

def s_2 : ℝ := 0

def scrM_1 : ℝ := 0

def scrM_2 : ℝ := 0

def scrM_y : ℝ := 0

def section1 : ℝ := 0

def section2 : ℝ := 0

def section3 : ℝ := 0

def section4 : ℝ := 0

def section5 : ℝ := 0

def section6 : ℝ := 0

def section7 : ℝ := 0

def section8 : ℝ := 0

def setup1 : ℝ := 0

def setup2 : ℝ := 0

def setup3 : ℝ := 0

def shA_P : ℝ := 0

def shA_Q : ℝ := 0

def shA_X : ℝ := 0

def shK_X : ℝ := 0

def shK_Y : ℝ := 0

def shM_C : ℝ := 0

def shM_T : ℝ := 0

def shM_W : ℝ := 0

def shM_X : ℝ := 0

def shM_Y : ℝ := 0

def shM_Z : ℝ := 0

def shP_1 : ℝ := 0

def shP_2 : ℝ := 0

def shP_W : ℝ := 0

def shP_i : ℝ := 0

def shP_x : ℝ := 0

def shZ_r : ℝ := 0

def shZ_s : ℝ := 0

def sigma_1 : ℝ := 0

def sigma_2 : ℝ := 0

def sigma_P : ℝ := 0

def sigma_Q : ℝ := 0

def snccaseexample1 : ℝ := 0

def snccaseexample2 : ℝ := 0

def sp_3r : ℝ := 0

def structureequation2 : ℝ := 0

def subseteq_1 : ℝ := 0

def t_1 : ℝ := 0

def t_2 : ℝ := 0

def tau_2 : ℝ := 0

def theorem_151 : ℝ := 0

def theta : ℝ := 0

def times_F : ℝ := 0

def times_S : ℝ := 0

def times_SY : ℝ := 0

def times_X : ℝ := 0

def times_Y : ℝ := 0

def to0 : ℝ := 0

def u_1 : ℝ := 0

def u_1W_2 : ℝ := 0

def u_1u_2u_3 : ℝ := 0

def u_1u_3 : ℝ := 0

def u_1u_3W_2 : ℝ := 0

def u_2 : ℝ := 0

def u_2W_1 : ℝ := 0

def u_3 : ℝ := 0

def v_1 : ℝ := 0

def v_1v_2r : ℝ := 0

def v_2 : ℝ := 0

def v_3 : ℝ := 0

def v_4 : ℝ := 0

def varepsilon_1 : ℝ := 0

def varepsilon_2 : ℝ := 0

def varphi_K : ℝ := 0

def vartheta_0 : ℝ := 0

def vartheta_1 : ℝ := 0

def vartheta_2 : ℝ := 0

def vartheta_3 : ℝ := 0

def w_1 : ℝ := 0

def w_2 : ℝ := 0

def wc_1 : ℝ := 0

def widetilde_X : ℝ := 0

def x_0 : ℝ := 0

def x_1 : ℝ := 0

def x_1x_2 : ℝ := 0

def x_1x_2x_3 : ℝ := 0

def x_2 : ℝ := 0

def x_3 : ℝ := 0

def y_1 : ℝ := 0

def hD_I : ℝ := 0

def hD_X : ℝ := 0

def hD_X_star : ℝ := 0

def hD_i_star : ℝ := 0

def s0 : ℝ := 0

def hP_finite_type : ℝ := 0

def hP_saturated : ℝ := 0

def hP_torsion : ℝ := 0

def inst_1 : ℝ := 0

def hH2 : ℝ := 0

def sigma1 : ℝ := 0

def sigma2 : ℝ := 0

def sigma3 : ℝ := 0

def u_x1 : ℝ := 0

def u_x2 : ℝ := 0

def hR_I : ℝ := 0

def hc1 : ℝ := 0

def f_X : ℝ := 0

def c_1_Theta_X_ : ℝ := 0

def hS_log : ℝ := 0

def hc_1 : ℝ := 0

def Θ_ : ℝ := 0

def c_1_Theta_nef_or_anti_nef : ℝ := 0

def hP_units : ℝ := 0

def p1 : ℝ := 0

def theta_v1 : ℝ := 0

def theta_v2 : ℝ := 0

def theta_v3 : ℝ := 0

def hR_I0 : ℝ := 0

def check_XI : ℝ := 0

def dim_X_eq_dim_B : ℝ := 0

def d1 : ℝ := 0

def d2 : ℝ := 0

def hD_distinct : ℝ := 0

def hS_I : ℝ := 0

def h_c1_eq : ℝ := 0

def h_c1_nef : ℝ := 0

def conj_SH : ℝ := 0

def hD_ample : ℝ := 0

def hD_snc : ℝ := 0

def torus_action_on_R_I : ℝ := 0

def f1 : ℝ := 0

def f2 : ℝ := 0

def h1 : ℝ := 0

def h2 : ℝ := 0

def h3 : ℝ := 0

def h4 : ℝ := 0

def h5 : ℝ := 0

def h6 : ℝ := 0

def h7 : ℝ := 0

def h8 : ℝ := 0

def Θ_X_ : ℝ := 0

def h_basic_setup_absolute_relative_2 : ℝ := 0

def h_source_1_1 : ℝ := 0

def overline_0_n : ℝ := 0

def x1 : ℝ := 0

def x2 : ℝ := 0

def u_4 : ℝ := 0

def hypotheses_of_mainassociativity1_or_mainassociativity2 : ℝ := 0

def ex_extended1 : ℝ := 0

def h9 : ℝ := 0

def p3 : ℝ := 0

def v1 : ℝ := 0

def v2 : ℝ := 0

def v3 : ℝ := 0

def vartheta_p1 : ℝ := 0

def vartheta_p2 : ℝ := 0

def vartheta_p3 : ℝ := 0

def vartheta_v1 : ℝ := 0

def vartheta_v2 : ℝ := 0

def vartheta_v3 : ℝ := 0

def hp1 : ℝ := 0

def hp2 : ℝ := 0

def hp3 : ℝ := 0

def p_1p_2 : ℝ := 0

def Θ_X_D : ℝ := 0

def intersection_of_E_with_D1 : ℝ := 0

def intersection_of_E_with_D_1 : ℝ := 0

def ex_extended3 : ℝ := 0

def h_G_in_D : ℝ := 0

def tau1 : ℝ := 0

def x3 : ℝ := 0

def x4 : ℝ := 0

def beta1 : ℝ := 0

def beta2 : ℝ := 0

def eps1 : ℝ := 0

def eps2 : ℝ := 0

def h_contact_x1 : ℝ := 0

def h_contact_x2 : ℝ := 0

def h_s_in_Sigma : ℝ := 0

def pp0 : ℝ := 0

def hx1 : ℝ := 0

def overline_X_r : ℝ := 0

def overline_Z_r : ℝ := 0

def htheta1 : ℝ := 0

def htheta2 : ℝ := 0

def theta1 : ℝ := 0

def theta2 : ℝ := 0

def h_carry_coherent_idealized_structures_5 : ℝ := 0

def h_integral_3 : ℝ := 0

def h_integral_idealized_tale_6 : ℝ := 0

def h_morphism_stacks_1 : ℝ := 0

def h_pure_dimensional_4 : ℝ := 0

def h_tale_2 : ℝ := 0

def Θ_X : ℝ := 0

def overline_0_4 : ℝ := 0

def overline_0_4_boundary : ℝ := 0

def overline_0_4_dagger : ℝ := 0

def tau_VFC : ℝ := 0

def hphi_K : ℝ := 0

def phi_K : ℝ := 0

def q_K : ℝ := 0

def u_5 : ℝ := 0

def u_6 : ℝ := 0

def overline_T_0 : ℝ := 0

def overline_T_0_eq : ℝ := 0

def overline_T_xi : ℝ := 0

def h_transverse_Psi : ℝ := 0

def g_identifies_W_with_z_times_B_m : ℝ := 0

def h10 : ℝ := 0

def h11 : ℝ := 0

def ell_in_Q_dual : ℝ := 0

def g_identifies_W : ℝ := 0

def overline_W_eq_Q : ℝ := 0

def k1 : ℝ := 0

def k2 : ℝ := 0

def k3 : ℝ := 0

def h12 : ℝ := 0

def h13 : ℝ := 0

def h14 : ℝ := 0

def h_A : ℝ := 0

def h_A_dot_c1 : ℝ := 0

def h_Theta : ℝ := 0

def h_VFC_tau : ℝ := 0

def h_VFC_tau' : ℝ := 0

def lemma_invarianceI : ℝ := 0

def conjecture_frob_holds_for_n_eq_2_and_3 : ℝ := 0

-- Local lemmas / theorem-like facts.
-- Explicit axioms / unresolved paper assumptions.

end Paper_1909_07649

export Paper_1909_07649 (omega infty C a a_1 a_1v_1 a_2 a_2E a_2v_2 a_iD_i alpha_0 alpha_T alpha_X alpha_Y alpha_Z arXiv2009 b_1 b_2 b_3 beta_1 beta_2 c1 c_1 cap_2 chi_P chi_Q circ_1 construction_30 def0 diagram2 dim_2 e23 e28 ell_1 ell_2 ev_X example_101 example_7 extended1 extended2 extended3 f_1 f_2 f_F f_M f_S f_W f_W' foC_0 foM_y ge0 i_1 i_2 i_3 i_4 in_D in_T iplus1 k_1 k_2 k_3 kappa_1 kappa_2 lemma_140 lemma_77 mW_2 mainassociativity1 mainassociativity2 mirrorconstruction1 mirrorconstruction2 mu_2 nu_1 nu_2 nu_R omega_X oplus_R overline_1 overline_2 overline_T overline_W overline_X overline_Z p2 p_0 p_1 p_1p_2r p_1p_2s p_1sr p_2 p_2p_1r p_2p_3s p_2s p_3 p_n0 pi_1 pi_2 pi_3 pi_4 pi_5 pi_6 pm1 pr_1 pr_2 q_0 r_jD_j rho_1 rho_2 rightarrow_2 rightarrow_P rightarrow_Q rightarrow_T runningexample1 runningexample2 runningexample3 s_0 s_2 scrM_1 scrM_2 scrM_y section1 section2 section3 section4 section5 section6 section7 section8 setup1 setup2 setup3 shA_P shA_Q shA_X shK_X shK_Y shM_C shM_T shM_W shM_X shM_Y shM_Z shP_1 shP_2 shP_W shP_i shP_x shZ_r shZ_s sigma_1 sigma_2 sigma_P sigma_Q snccaseexample1 snccaseexample2 sp_3r structureequation2 subseteq_1 t_1 t_2 tau_2 theorem_151 theta times_F times_S times_SY times_X times_Y to0 u_1 u_1W_2 u_1u_2u_3 u_1u_3 u_1u_3W_2 u_2 u_2W_1 u_3 v_1 v_1v_2r v_2 v_3 v_4 varepsilon_1 varepsilon_2 varphi_K vartheta_0 vartheta_1 vartheta_2 vartheta_3 w_1 w_2 wc_1 widetilde_X x_0 x_1 x_1x_2 x_1x_2x_3 x_2 x_3 y_1 hD_I hD_X hD_X_star hD_i_star s0 hP_finite_type hP_saturated hP_torsion inst_1 hH2 sigma1 sigma2 sigma3 u_x1 u_x2 hR_I hc1 f_X c_1_Theta_X_ hS_log hc_1 Θ_ c_1_Theta_nef_or_anti_nef hP_units p1 theta_v1 theta_v2 theta_v3 hR_I0 check_XI dim_X_eq_dim_B d1 d2 hD_distinct hS_I h_c1_eq h_c1_nef conj_SH hD_ample hD_snc torus_action_on_R_I f1 f2 h1 h2 h3 h4 h5 h6 h7 h8 Θ_X_ h_basic_setup_absolute_relative_2 h_source_1_1 overline_0_n x1 x2 u_4 hypotheses_of_mainassociativity1_or_mainassociativity2 ex_extended1 h9 p3 v1 v2 v3 vartheta_p1 vartheta_p2 vartheta_p3 vartheta_v1 vartheta_v2 vartheta_v3 hp1 hp2 hp3 p_1p_2 Θ_X_D intersection_of_E_with_D1 intersection_of_E_with_D_1 ex_extended3 h_G_in_D tau1 x3 x4 beta1 beta2 eps1 eps2 h_contact_x1 h_contact_x2 h_s_in_Sigma pp0 hx1 overline_X_r overline_Z_r htheta1 htheta2 theta1 theta2 h_carry_coherent_idealized_structures_5 h_integral_3 h_integral_idealized_tale_6 h_morphism_stacks_1 h_pure_dimensional_4 h_tale_2 Θ_X overline_0_4 overline_0_4_boundary overline_0_4_dagger tau_VFC hphi_K phi_K q_K u_5 u_6 overline_T_0 overline_T_0_eq overline_T_xi h_transverse_Psi g_identifies_W_with_z_times_B_m h10 h11 ell_in_Q_dual g_identifies_W overline_W_eq_Q k1 k2 k3 h12 h13 h14 h_A h_A_dot_c1 h_Theta h_VFC_tau h_VFC_tau' lemma_invarianceI conjecture_frob_holds_for_n_eq_2_and_3)
