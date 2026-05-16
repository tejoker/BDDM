-- Auto-generated paper theory module
-- paper_id: 2012.11433
-- domain: default

import Mathlib
import Aesop

open MeasureTheory ProbabilityTheory Filter Set

namespace Paper_2012_11433

-- note: declared 218 paper-local symbol(s) from inventory
-- note: definition stubs are notation grounding only and are not proof-countable

-- ------------------------------------------------------------------
-- Paper-local definitions and explicit axiom debt
-- Any result depending on this module must be reported as proved
-- modulo any paper-local axioms below, not as unconditional closure.
-- ------------------------------------------------------------------

-- Definition stubs ground paper-local identifiers before proof search.
-- They are transparent Lean definitions for elaboration, not hidden proofs of paper claims.
def omega (_i _k : ℕ) : ℝ := 0

def infty : ℝ := 0

def C : ℝ := 0

def a : ℝ := 0

def aC_1 : ℝ := 0

def a_1 : ℝ := 0

def a_2 : ℝ := 0

def a_2l : ℝ := 0

def a_2x_2 : ℝ := 0

def a_3 : ℝ := 0

def a_3m : ℝ := 0

def a_3x_3 : ℝ := 0

def a_iD_i : ℝ := 0

def a_iS_i : ℝ := 0

def a_jF_j : ℝ := 0

def ambro01 : ℝ := 0

def bC_2 : ℝ := 0

def birkar17 : ℝ := 0

def bm16 : ℝ := 0

def bm97 : ℝ := 0

def brunella00 : ℝ := 0

def c_1 : ℝ := 0

def c_N : ℝ := 0

def c_R : ℝ := 0

def corollary_23 : ℝ := 0

def eta_W : ℝ := 0

def example_1 : ℝ := 0

def example_16 : ℝ := 0

def example_7 : ℝ := 0

def f_1 : ℝ := 0

def fujita83 : ℝ := 0

def gamma_1 : ℝ := 0

def gamma_N : ℝ := 0

def i00 : ℝ := 0

def inv_antinef2 : ℝ := 0

def ix_1 : ℝ := 0

def jouanolou78 : ℝ := 0

def k00 : ℝ := 0

def kl0 : ℝ := 0

def km0 : ℝ := 0

def kollar13 : ℝ := 0

def kollar96 : ℝ := 0

def kx_2 : ℝ := 0

def lambda_1 : ℝ := 0

def lx_3 : ℝ := 0

def martinet81 : ℝ := 0

def mcq04 : ℝ := 0

def mcq08 : ℝ := 0

def mendes00 : ℝ := 0

def mp13 : ℝ := 0

def mu_C : ℝ := 0

def mu_DA : ℝ := 0

def mu_P : ℝ := 0

def mu_PB : ℝ := 0

def mu_Q : ℝ := 0

def n_1 : ℝ := 0

def n_N : ℝ := 0

def n_U : ℝ := 0

def nabla_E : ℝ := 0

def p_1 : ℝ := 0

def partial_0 : ℝ := 0

def partial_N : ℝ := 0

def partial_S : ℝ := 0

def pi_1 : ℝ := 0

def q_C : ℝ := 0

def question_36 : ℝ := 0

def r_D : ℝ := 0

def r_E : ℝ := 0

def r_Ea : ℝ := 0

def section1 : ℝ := 0

def section2 : ℝ := 0

def section3 : ℝ := 0

def section4 : ℝ := 0

def section5 : ℝ := 0

def section6 : ℝ := 0

def section7 : ℝ := 0

def spicer20 : ℝ := 0

def t_cone2 : ℝ := 0

def t_cone3 : ℝ := 0

def theorem_1 : ℝ := 0

def theorem_2 : ℝ := 0

def theorem_22 : ℝ := 0

def theorem_3 : ℝ := 0

def times_X : ℝ := 0

def times_Y : ℝ := 0

def times_Z : ℝ := 0

def vert_D : ℝ := 0

def vert_E : ℝ := 0

def vert_U : ℝ := 0

def vert_W : ℝ := 0

def vert_Z : ℝ := 0

def x_1 : ℝ := 0

def x_2 : ℝ := 0

def x_3 : ℝ := 0

def z_0 : ℝ := 0

def vert_S : ℝ := 0

def flips_exist2 : ℝ := 0

def hF_canonical : ℝ := 0

def u_1 : ℝ := 0

def u_2 : ℝ := 0

def u_3 : ℝ := 0

def u_4 : ℝ := 0

def u_5 : ℝ := 0

def u_6 : ℝ := 0

def u_7 : ℝ := 0

def hK_F : ℝ := 0

def inst_1 : ℝ := 0

def inst_2 : ℝ := 0

def hK_G : ℝ := 0

def h1 : ℝ := 0

def h2 : ℝ := 0

def h_antinef2_normal_projective_surface_1 : ℝ := 0

def h_divisors_enumerate_curve_which_2 : ℝ := 0

def mu_CDelta : ℝ := 0

def h_contained_invariant_terminal_generic_6 : ℝ := 0

def h_curve_5 : ℝ := 0

def h_divisor_invariant_support_reduced_2 : ℝ := 0

def h_normalisation_3 : ℝ := 0

def h_prop_comp_normal_threefold_1 : ℝ := 0

def h_write_induced_foliation_divisors_4 : ℝ := 0

def sing_plus_F : ℝ := 0

def hF_not_ai : ℝ := 0

def hC_not_sing : ℝ := 0

def hDelta_multiplicity : ℝ := 0

def hDelta_nonneg : ℝ := 0

def hDelta_support : ℝ := 0

def hS_not_sing : ℝ := 0

def hC_nu : ℝ := 0

def hS_nu : ℝ := 0

def h3 : ℝ := 0

def h4 : ℝ := 0

def h5 : ℝ := 0

def h6 : ℝ := 0

def h10 : ℝ := 0

def h7 : ℝ := 0

def h8 : ℝ := 0

def h9 : ℝ := 0

def hC_0 : ℝ := 0

def d_dtN : ℝ := 0

def d_dtS : ℝ := 0

def hd_dtN : ℝ := 0

def hd_dtS : ℝ := 0

def curve_x_eq_y_eq_0 : ℝ := 0

def foliation_F_on_X : ℝ := 0

def quotient_of_C3_by_Z2_action : ℝ := 0

def vector_field_partial_N : ℝ := 0

def vector_field_partial_S : ℝ := 0

def hC_F_inv : ℝ := 0

def hC_smooth : ℝ := 0

def hF_sing : ℝ := 0

def hM_W : ℝ := 0

def h_invariant_curve_3 : ℝ := 0

def h_normal_variety_1 : ℝ := 0

def h_rank_foliation_2 : ℝ := 0

def h_source_5_5 : ℝ := 0

def h_terminal_every_closed_point_4 : ℝ := 0

def cal_F : ℝ := 0

def Γ_is_log_pair : ℝ := 0

def hC_D : ℝ := 0

def hX_dim : ℝ := 0

def hX_quotient : ℝ := 0

def hC_sub : ℝ := 0

def hF_canon : ℝ := 0

def hS_inv : ℝ := 0

def hH_R : ℝ := 0

def hC_D_i : ℝ := 0

def hD_i : ℝ := 0

def exists_L_such_that_for_sufficiently_large_m_general_D_klt_and_log_canonical : ℝ := 0

def log_pair_D_i_C_log_canonical_at_x_i : ℝ := 0

def sing_X_inter_C_finite : ℝ := 0

def f_restricted_to_D_is_contraction_of_relative_Picard_number_one : ℝ := 0

def hC_conn : ℝ := 0

def hF_simple : ℝ := 0

def negative_on_C : ℝ := 0

def hF_term : ℝ := 0

def hX_sing : ℝ := 0

def h_analytic_neighbourhood_3 : ℝ := 0

def h_invariant_1 : ℝ := 0

def h_source_2_2 : ℝ := 0

def h_C : ℝ := 0

def h_D : ℝ := 0

def h_D_inter : ℝ := 0

def h_D_inv : ℝ := 0

def hC1 : ℝ := 0

def hC2 : ℝ := 0

def hKNeg1 : ℝ := 0

def hKNeg2 : ℝ := 0

def hR1 : ℝ := 0

def hR2 : ℝ := 0

def hF_hol : ℝ := 0

def hX_smooth : ℝ := 0

def hR_dim : ℝ := 0

def hGamma_inv : ℝ := 0

def hGamma_lc : ℝ := 0

def hGamma_nonneg : ℝ := 0

def hGamma_not_D : ℝ := 0

def hX_fact : ℝ := 0

def hX_klt : ℝ := 0

def hR_loc : ℝ := 0

def hF_seq : ℝ := 0

def hF_i : ℝ := 0

def sing_F_plus : ℝ := 0

def a_iD : ℝ := 0

def cal_F' : ℝ := 0

def cal_F'_Invariant : ℝ := 0

def hE_i : ℝ := 0

def hcal_F : ℝ := 0

def hcal_F' : ℝ := 0

def a_E_F_ : ℝ := 0

def hKF_delta_pe : ℝ := 0

def hX_delta_ : ℝ := 0

def Θ_plus : ℝ := 0

def hX_Qfactorial : ℝ := 0

def hC_in_R : ℝ := 0

def hF_not_canonical : ℝ := 0

def hP_in_C : ℝ := 0

def hF_lc : ℝ := 0

def hX_lc : ℝ := 0

-- Local lemmas / theorem-like facts.
-- Explicit axioms / unresolved paper assumptions.

end Paper_2012_11433

export Paper_2012_11433 (omega infty C a aC_1 a_1 a_2 a_2l a_2x_2 a_3 a_3m a_3x_3 a_iD_i a_iS_i a_jF_j ambro01 bC_2 birkar17 bm16 bm97 brunella00 c_1 c_N c_R corollary_23 eta_W example_1 example_16 example_7 f_1 fujita83 gamma_1 gamma_N i00 inv_antinef2 ix_1 jouanolou78 k00 kl0 km0 kollar13 kollar96 kx_2 lambda_1 lx_3 martinet81 mcq04 mcq08 mendes00 mp13 mu_C mu_DA mu_P mu_PB mu_Q n_1 n_N n_U nabla_E p_1 partial_0 partial_N partial_S pi_1 q_C question_36 r_D r_E r_Ea section1 section2 section3 section4 section5 section6 section7 spicer20 t_cone2 t_cone3 theorem_1 theorem_2 theorem_22 theorem_3 times_X times_Y times_Z vert_D vert_E vert_U vert_W vert_Z x_1 x_2 x_3 z_0 vert_S flips_exist2 hF_canonical u_1 u_2 u_3 u_4 u_5 u_6 u_7 hK_F inst_1 inst_2 hK_G h1 h2 h_antinef2_normal_projective_surface_1 h_divisors_enumerate_curve_which_2 mu_CDelta h_contained_invariant_terminal_generic_6 h_curve_5 h_divisor_invariant_support_reduced_2 h_normalisation_3 h_prop_comp_normal_threefold_1 h_write_induced_foliation_divisors_4 sing_plus_F hF_not_ai hC_not_sing hDelta_multiplicity hDelta_nonneg hDelta_support hS_not_sing hC_nu hS_nu h3 h4 h5 h6 h10 h7 h8 h9 hC_0 d_dtN d_dtS hd_dtN hd_dtS curve_x_eq_y_eq_0 foliation_F_on_X quotient_of_C3_by_Z2_action vector_field_partial_N vector_field_partial_S hC_F_inv hC_smooth hF_sing hM_W h_invariant_curve_3 h_normal_variety_1 h_rank_foliation_2 h_source_5_5 h_terminal_every_closed_point_4 cal_F Γ_is_log_pair hC_D hX_dim hX_quotient hC_sub hF_canon hS_inv hH_R hC_D_i hD_i exists_L_such_that_for_sufficiently_large_m_general_D_klt_and_log_canonical log_pair_D_i_C_log_canonical_at_x_i sing_X_inter_C_finite f_restricted_to_D_is_contraction_of_relative_Picard_number_one hC_conn hF_simple negative_on_C hF_term hX_sing h_analytic_neighbourhood_3 h_invariant_1 h_source_2_2 h_C h_D h_D_inter h_D_inv hC1 hC2 hKNeg1 hKNeg2 hR1 hR2 hF_hol hX_smooth hR_dim hGamma_inv hGamma_lc hGamma_nonneg hGamma_not_D hX_fact hX_klt hR_loc hF_seq hF_i sing_F_plus a_iD cal_F' cal_F'_Invariant hE_i hcal_F hcal_F' a_E_F_ hKF_delta_pe hX_delta_ Θ_plus hX_Qfactorial hC_in_R hF_not_canonical hP_in_C hF_lc hX_lc)
