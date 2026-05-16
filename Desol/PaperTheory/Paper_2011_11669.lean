-- Auto-generated paper theory module
-- paper_id: 2011.11669
-- domain: default

import Mathlib
import Aesop

open MeasureTheory ProbabilityTheory Filter Set

namespace Paper_2011_11669

-- note: declared 274 paper-local symbol(s) from inventory
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

def a_0 : ℝ := 0

def a_0a : ℝ := 0

def a_1 : ℝ := 0

def a_iKa_i : ℝ := 0

def a_iS : ℝ := 0

def a_iY_i : ℝ := 0

def a_jS : ℝ := 0

def aleph_0 : ℝ := 0

def alpha_0 : ℝ := 0

def b2 : ℝ := 0

def b_0 : ℝ := 0

def b_1 : ℝ := 0

def bigvee_A : ℝ := 0

def bigwedge_0 : ℝ := 0

def bigwedge_A : ℝ := 0

def bigwedge_B : ℝ := 0

def bigwedge_N : ℝ := 0

def box0 : ℝ := 0

def c_0 : ℝ := 0

def coro_137 : ℝ := 0

def coro_54 : ℝ := 0

def coro_56 : ℝ := 0

def d_0 : ℝ := 0

def eta_A : ℝ := 0

def eta_B : ℝ := 0

def ex_116 : ℝ := 0

def ex_127 : ℝ := 0

def ex_2 : ℝ := 0

def ex_78 : ℝ := 0

def gH_1 : ℝ := 0

def g_0 : ℝ := 0

def g_0K : ℝ := 0

def h_0 : ℝ := 0

def i_1 : ℝ := 0

def j_1 : ℝ := 0

def jx_0 : ℝ := 0

def lang_0 : ℝ := 0

def lang_1 : ℝ := 0

def log_2 : ℝ := 0

def nx_0 : ℝ := 0

def phi_0 : ℝ := 0

def pi_1 : ℝ := 0

def pi_2 : ℝ := 0

def pi_L : ℝ := 0

def proj_P : ℝ := 0

def proj_Q : ℝ := 0

def q_2 : ℝ := 0

def quot_P : ℝ := 0

def quot_Q : ℝ := 0

def s1 : ℝ := 0

def setbox0 : ℝ := 0

def sim_P : ℝ := 0

def theo_95 : ℝ := 0

def tp_A : ℝ := 0

def utf8 : ℝ := 0

def varepsilon_0 : ℝ := 0

def varepsilon_1 : ℝ := 0

def wd0 : ℝ := 0

def xU_x : ℝ := 0

def xU_xU : ℝ := 0

def xV_x : ℝ := 0

def x_0 : ℝ := 0

def x_1 : ℝ := 0

def x_aY_i : ℝ := 0

def y_1 : ℝ := 0

def z_0 : ℝ := 0

def h_invariance_hyperdefinable_1 : ℝ := 0

def h_invariant_2 : ℝ := 0

def h_invariant_3 : ℝ := 0

def hquot_P : ℝ := 0

def h_definable_2 : ℝ := 0

def h_hyperdefinable_1 : ℝ := 0

def f_A_invariant : ℝ := 0

def u_1 : ℝ := 0

def a_in_P : ℝ := 0

def bigwedge_A_definable : ℝ := 0

def hA_star : ℝ := 0

def hA_star_star : ℝ := 0

def hV_def : ℝ := 0

def hV_nonempty : ℝ := 0

def hV_subset : ℝ := 0

def b_star_0 : ℝ := 0

def c_star_0 : ℝ := 0

def h_hyperdefinable_sets_1 : ℝ := 0

def delta_P : ℝ := 0

def delta_P_A_star : ℝ := 0

def hB_A : ℝ := 0

def hP_A : ℝ := 0

def hP_B : ℝ := 0

def hP_i : ℝ := 0

def u_3 : ℝ := 0

def u_4 : ℝ := 0

def hP_incr : ℝ := 0

def hP_normal : ℝ := 0

def hVW_closed : ℝ := 0

def hVW_disj : ℝ := 0

def u_2 : ℝ := 0

def h1 : ℝ := 0

def h2 : ℝ := 0

def h3 : ℝ := 0

def h4 : ℝ := 0

def h5 : ℝ := 0

def h6 : ℝ := 0

def h7 : ℝ := 0

def hP_i_closed : ℝ := 0

def hP_i_normal : ℝ := 0

def hP_loc : ℝ := 0

def hU_0 : ℝ := 0

def hU_0_Pi : ℝ := 0

def hU_1_def : ℝ := 0

def htp_U_1 : ℝ := 0

def hxU_0 : ℝ := 0

def hQ_top : ℝ := 0

def p_functions_logic_topology_2 : ℝ := 0

def isomorphism_of_piecewise_A_hyperdefinable_sets : ℝ := 0

def piecewise_A_hyperdefinable : ℝ := 0

def piecewise_bigwedge_A_definable : ℝ := 0

def hK1 : ℝ := 0

def hK2 : ℝ := 0

def h_proof_suffices_note_topological_2 : ℝ := 0

def h_subspace_locally_hyperdefinable_locally_1 : ℝ := 0

def hP_top : ℝ := 0

def h_metrisation_theorem_metrisation_theorem_1 : ℝ := 0

def h_proof_each_piece_pseudo_2 : ℝ := 0

def pi_Delta_P : ℝ := 0

def e_example_1 : ℝ := 0

def h_explicitly_1 : ℝ := 0

def h_instance_consider_direct_system_2 : ℝ := 0

def hP_global : ℝ := 0

def phi_BA : ℝ := 0

def piecewise_A_hyperdefinable_dense_subset : ℝ := 0

def eps_0 : ℝ := 0

def h_locally_compact_topological_group_1 : ℝ := 0

def h_neighbourhood_identity_2 : ℝ := 0

def h_open_precompact_approximate_subgroup_2 : ℝ := 0

def e_example_3 : ℝ := 0

def hQ_not_locally_compact : ℝ := 0

def hQ_piecewise_hyperdefinable : ℝ := 0

def e_example_6 : ℝ := 0

def h10 : ℝ := 0

def h8 : ℝ := 0

def h9 : ℝ := 0

def hH1 : ℝ := 0

def hH2 : ℝ := 0

def pi1 : ℝ := 0

def pi2 : ℝ := 0

def hD_L : ℝ := 0

def hK_normal : ℝ := 0

def hK_piecewise : ℝ := 0

def hK_subset : ℝ := 0

def hX_def : ℝ := 0

def hX_symm : ℝ := 0

def hG_top : ℝ := 0

def hpi_L : ℝ := 0

def hpi_L_closed : ℝ := 0

def hpi_L_open : ℝ := 0

def hpi_L_proper : ℝ := 0

def hpi_L_surj : ℝ := 0

def eps0 : ℝ := 0

def eps1 : ℝ := 0

def h0 : ℝ := 0

def d_S : ℝ := 0

def h_U : ℝ := 0

def h_U_1 : ℝ := 0

def h_d_S : ℝ := 0

def hG_gen : ℝ := 0

def hT_piecewise : ℝ := 0

def hT_small : ℝ := 0

def hC_countable : ℝ := 0

def hV_compact : ℝ := 0

def delta_iMinimal : ℝ := 0

def hG_A : ℝ := 0

def hG_union : ℝ := 0

def hU_i : ℝ := 0

def hU_i_eq : ℝ := 0

def hV_A : ℝ := 0

def hV_gen : ℝ := 0

def f_S : ℝ := 0

def hG_lie : ℝ := 0

def hK_nhd : ℝ := 0

def hK_symm : ℝ := 0

def hT_inv : ℝ := 0

def a_iKa : ℝ := 0

def hG00 : ℝ := 0

def piecewise_A_hyperdefinable_group_G000_invariant_normal_subgroup : ℝ := 0

def hY1 : ℝ := 0

def hK_le_K' : ℝ := 0

def hG_ap : ℝ := 0

def pi_H_K : ℝ := 0

def closure_B : ℝ := 0

def min_Yamabe_pair : ℝ := 0

def has_generic_piece_modulo_G00_A : ℝ := 0

def is_minimal_A_Lie_core_L : ℝ := 0

def piecewise_A_hyperdefinable_group_G : ℝ := 0

def restriction_to_G0_A_of_pi_L_G0_A : ℝ := 0

def hG_hat : ℝ := 0

def hK_H : ℝ := 0

def hK_N_subset_T : ℝ := 0

def hL_G_N : ℝ := 0

def hL_N : ℝ := 0

def hL_N_aperiodic : ℝ := 0

def is_bigwedge_A_definable_normal_subgroup_of : ℝ := 0

def is_piecewise_bigwedge_A_definable_normal_subgroup_of_small_index : ℝ := 0

def pi_L_mid_H : ℝ := 0

def piecewise_bounded_bigwedge_A_definable_surjective_group_homomorphism : ℝ := 0

def u_7 : ℝ := 0

def a_i_1 : ℝ := 0

def h_W_A : ℝ := 0

def h_W_def : ℝ := 0

def hP_t : ℝ := 0

def hT_0 : ℝ := 0

def h_i_1 : ℝ := 0

def h_j_1 : ℝ := 0

def f_1_to_1 : ℝ := 0

def f_bigwedge_A_definable : ℝ := 0

def hU_Gk : ℝ := 0

def card_N_lt_lambda : ℝ := 0

def hV0 : ℝ := 0

def hV_forks : ℝ := 0

def b0 : ℝ := 0

def b1 : ℝ := 0

def forking_ideal_is_A_invariant_and_A_medium : ℝ := 0

def fundamental_lemma_for_the_stabilizer_theorem_1 : ℝ := 0

def hP_hyper : ℝ := 0

def hQ_hyper : ℝ := 0

def hW_stable : ℝ := 0

def hY_in_q : ℝ := 0

def hrushovski_2011_lemma_2_2 : ℝ := 0

def is_A_invariant_ideal_of_bigwedge_lt_lambda_definable_subsets_of_piecewise_A_hyperdefinable_group : ℝ := 0

def is_A_medium_bigwedge_A_definable_subset : ℝ := 0

def mos_2_8 : ℝ := 0

def is_stable_over_A : ℝ := 0

def hB_size : ℝ := 0

def p1 : ℝ := 0

def mos_2_11 : ℝ := 0

def p_hat_is_A_star_invariant : ℝ := 0

def p_inv_W_is_A_star_medium : ℝ := 0

def p_is_A_star_type : ℝ := 0

def p_nf_product_p_inv_subset_St_W : ℝ := 0

def l_index_lemma_2 : ℝ := 0

def bigwedge_A_definable_set_V : ℝ := 0

def bigwedge_A_definable_subgroup_S : ℝ := 0

def bigwedge_N_definableSubgroup : ℝ := 0

def hN_lt : ℝ := 0

def stabilizer_theorem_1 : ℝ := 0

def bigwedge_lt_lambda_definableSubsets : ℝ := 0

def q_subset_St_p : ℝ := 0

def hG_hyperdefinable : ℝ := 0

def hN_size : ℝ := 0

def stabilizer_theorem_mos_b2 : ℝ := 0

def index_lemma_1 : ℝ := 0

def bigwedge_A_star_definable_subgroup : ℝ := 0

def p_underline_defines_p_over_A_star : ℝ := 0

def piecewise_A_star_hyperdefinable_group : ℝ := 0

def stabilizer_theorem_2 : ℝ := 0

def bigwedge_N_definable : ℝ := 0

def mu_mid_X_star_X_star_X_is_N_medium : ℝ := 0

def p_is_wide_N_type_subset_X : ℝ := 0

def ppp_inv_eq_pS : ℝ := 0

def ppp_inv_eq_yS_for_any_y_in_p : ℝ := 0

def tent2012 : ℝ := 0

def bigwedge_N_definable_normal_subgroup_of_small_index : ℝ := 0

def piecewise_A_hyperdefinable_group : ℝ := 0

def hX_wide : ℝ := 0

def hY_b_def : ℝ := 0

def hY_def : ℝ := 0

def hY_not_in_mu : ℝ := 0

def hrushovski_2011_corollary_3_11_montenegro_2018_proposition_2_13 : ℝ := 0

def hX_norm : ℝ := 0

def DyadicScale (_l _N : ℕ) : Prop := True

-- Local lemmas / theorem-like facts.
-- Explicit axioms / unresolved paper assumptions.

end Paper_2011_11669

export Paper_2011_11669 (omega infty C a a_0 a_0a a_1 a_iKa_i a_iS a_iY_i a_jS aleph_0 alpha_0 b2 b_0 b_1 bigvee_A bigwedge_0 bigwedge_A bigwedge_B bigwedge_N box0 c_0 coro_137 coro_54 coro_56 d_0 eta_A eta_B ex_116 ex_127 ex_2 ex_78 gH_1 g_0 g_0K h_0 i_1 j_1 jx_0 lang_0 lang_1 log_2 nx_0 phi_0 pi_1 pi_2 pi_L proj_P proj_Q q_2 quot_P quot_Q s1 setbox0 sim_P theo_95 tp_A utf8 varepsilon_0 varepsilon_1 wd0 xU_x xU_xU xV_x x_0 x_1 x_aY_i y_1 z_0 h_invariance_hyperdefinable_1 h_invariant_2 h_invariant_3 hquot_P h_definable_2 h_hyperdefinable_1 f_A_invariant u_1 a_in_P bigwedge_A_definable hA_star hA_star_star hV_def hV_nonempty hV_subset b_star_0 c_star_0 h_hyperdefinable_sets_1 delta_P delta_P_A_star hB_A hP_A hP_B hP_i u_3 u_4 hP_incr hP_normal hVW_closed hVW_disj u_2 h1 h2 h3 h4 h5 h6 h7 hP_i_closed hP_i_normal hP_loc hU_0 hU_0_Pi hU_1_def htp_U_1 hxU_0 hQ_top p_functions_logic_topology_2 isomorphism_of_piecewise_A_hyperdefinable_sets piecewise_A_hyperdefinable piecewise_bigwedge_A_definable hK1 hK2 h_proof_suffices_note_topological_2 h_subspace_locally_hyperdefinable_locally_1 hP_top h_metrisation_theorem_metrisation_theorem_1 h_proof_each_piece_pseudo_2 pi_Delta_P e_example_1 h_explicitly_1 h_instance_consider_direct_system_2 hP_global phi_BA piecewise_A_hyperdefinable_dense_subset eps_0 h_locally_compact_topological_group_1 h_neighbourhood_identity_2 h_open_precompact_approximate_subgroup_2 e_example_3 hQ_not_locally_compact hQ_piecewise_hyperdefinable e_example_6 h10 h8 h9 hH1 hH2 pi1 pi2 hD_L hK_normal hK_piecewise hK_subset hX_def hX_symm hG_top hpi_L hpi_L_closed hpi_L_open hpi_L_proper hpi_L_surj eps0 eps1 h0 d_S h_U h_U_1 h_d_S hG_gen hT_piecewise hT_small hC_countable hV_compact delta_iMinimal hG_A hG_union hU_i hU_i_eq hV_A hV_gen f_S hG_lie hK_nhd hK_symm hT_inv a_iKa hG00 piecewise_A_hyperdefinable_group_G000_invariant_normal_subgroup hY1 hK_le_K' hG_ap pi_H_K closure_B min_Yamabe_pair has_generic_piece_modulo_G00_A is_minimal_A_Lie_core_L piecewise_A_hyperdefinable_group_G restriction_to_G0_A_of_pi_L_G0_A hG_hat hK_H hK_N_subset_T hL_G_N hL_N hL_N_aperiodic is_bigwedge_A_definable_normal_subgroup_of is_piecewise_bigwedge_A_definable_normal_subgroup_of_small_index pi_L_mid_H piecewise_bounded_bigwedge_A_definable_surjective_group_homomorphism u_7 a_i_1 h_W_A h_W_def hP_t hT_0 h_i_1 h_j_1 f_1_to_1 f_bigwedge_A_definable hU_Gk card_N_lt_lambda hV0 hV_forks b0 b1 forking_ideal_is_A_invariant_and_A_medium fundamental_lemma_for_the_stabilizer_theorem_1 hP_hyper hQ_hyper hW_stable hY_in_q hrushovski_2011_lemma_2_2 is_A_invariant_ideal_of_bigwedge_lt_lambda_definable_subsets_of_piecewise_A_hyperdefinable_group is_A_medium_bigwedge_A_definable_subset mos_2_8 is_stable_over_A hB_size p1 mos_2_11 p_hat_is_A_star_invariant p_inv_W_is_A_star_medium p_is_A_star_type p_nf_product_p_inv_subset_St_W l_index_lemma_2 bigwedge_A_definable_set_V bigwedge_A_definable_subgroup_S bigwedge_N_definableSubgroup hN_lt stabilizer_theorem_1 bigwedge_lt_lambda_definableSubsets q_subset_St_p hG_hyperdefinable hN_size stabilizer_theorem_mos_b2 index_lemma_1 bigwedge_A_star_definable_subgroup p_underline_defines_p_over_A_star piecewise_A_star_hyperdefinable_group stabilizer_theorem_2 bigwedge_N_definable mu_mid_X_star_X_star_X_is_N_medium p_is_wide_N_type_subset_X ppp_inv_eq_pS ppp_inv_eq_yS_for_any_y_in_p tent2012 bigwedge_N_definable_normal_subgroup_of_small_index piecewise_A_hyperdefinable_group hX_wide hY_b_def hY_def hY_not_in_mu hrushovski_2011_corollary_3_11_montenegro_2018_proposition_2_13 hX_norm DyadicScale)
