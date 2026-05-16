-- Auto-generated paper theory module
-- paper_id: 2007.03831
-- domain: default

import Mathlib
import Aesop

open MeasureTheory ProbabilityTheory Filter Set

namespace Paper_2007_03831

-- note: declared 394 paper-local symbol(s) from inventory
-- note: definition stubs are notation grounding only and are not proof-countable

-- ------------------------------------------------------------------
-- Paper-local definitions and explicit axiom debt
-- Any result depending on this module must be reported as proved
-- modulo any paper-local axioms below, not as unconditional closure.
-- ------------------------------------------------------------------

-- Definition stubs ground paper-local identifiers before proof search.
-- They are transparent Lean definitions for elaboration, not hidden proofs of paper claims.
noncomputable def scattering_amplitude_MHV_curve
  {C : Type*} [AddCommGroup C] [Module ℝ C] [TopologicalSpace C] [T2Space C]
  (A : C → ℝ)
  (hA : ∀ x, A x ≠ 0)
  (hinv : ∀ (c : ℝ) (x : C), A (c • x) = c * A x)
  (hform : ∀ x, ∃ omega : C → ℝ, A x = omega x)
  : ∃ (k : ℝ), ∀ x, A x = k * (A x) := sorry

noncomputable def SfbXfbSDb (n : ℕ) (h_moreover_following_cases_curve_1 : True) (h_following_cases_especially_interesting_2 : True) :
  n = g+3 := by

noncomputable def definition_100 (k : ℕ) (l : ℕ) (n : ℕ) :
  (k,l) = (n,0) := by

def omega (_i _k : ℕ) : ℝ := 0

def infty : ℝ := 0

def C : ℝ := 0

def a : ℝ := 0

def a_C : ℝ := 0

def a_Y : ℝ := 0

def alpha_1 : ℝ := 0

def amplification_107 : ℝ := 0

def amplification_61 : ℝ := 0

def bp_1 : ℝ := 0

def c_1 : ℝ := 0

def corollary_39 : ℝ := 0

def corollary_60 : ℝ := 0

def corollary_93 : ℝ := 0

def d_A : ℝ := 0

def d_B : ℝ := 0

def d_C : ℝ := 0

def d_Y : ℝ := 0

def delta_1 : ℝ := 0

def delta_2 : ℝ := 0

def delta_3 : ℝ := 0

def delta_4 : ℝ := 0

def delta_5 : ℝ := 0

def elli4 : ℝ := 0

def example_131 : ℝ := 0

def example_24 : ℝ := 0

def example_33 : ℝ := 0

def example_65 : ℝ := 0

def example_76 : ℝ := 0

def example_89 : ℝ := 0

def f_2 : ℝ := 0

def f_4 : ℝ := 0

def g_A : ℝ := 0

def g_B : ℝ := 0

def g_Y : ℝ := 0

def ge1 : ℝ := 0

def ge2 : ℝ := 0

def ge3 : ℝ := 0

def hskip2 : ℝ := 0

def hskip5 : ℝ := 0

def lambda_0 : ℝ := 0

def lemma_121 : ℝ := 0

def lemma_32 : ℝ := 0

def lemma_59 : ℝ := 0

def lemma_91 : ℝ := 0

def lower4 : ℝ := 0

def mathcalO_C : ℝ := 0

def n6 : ℝ := 0

def n_0 : ℝ := 0

def n_A : ℝ := 0

def n_B : ℝ := 0

def n_Y : ℝ := 0

def ne0 : ℝ := 0

def oN_6 : ℝ := 0

def oS_7 : ℝ := 0

def oS_7' : ℝ := 0

def omega_C : ℝ := 0

def on6 : ℝ := 0

def os7 : ℝ := 0

def otimes2 : ℝ := 0

def over2 : ℝ := 0

def over5 : ℝ := 0

def p_1 : ℝ := 0

def p_1p_2 : ℝ := 0

def p_1p_2p_3p_4 : ℝ := 0

def p_1p_3 : ℝ := 0

def p_1p_4 : ℝ := 0

def p_2 : ℝ := 0

def p_2p_3 : ℝ := 0

def p_2p_4 : ℝ := 0

def p_3 : ℝ := 0

def p_3p_4 : ℝ := 0

def p_4 : ℝ := 0

def p_5 : ℝ := 0

def p_6 : ℝ := 0

def pi_1 : ℝ := 0

def pi_A : ℝ := 0

def pic2 : ℝ := 0

def q_1 : ℝ := 0

def q_2 : ℝ := 0

def q_3 : ℝ := 0

def q_4 : ℝ := 0

def review_1 : ℝ := 0

def review_101 : ℝ := 0

def review_112 : ℝ := 0

def review_114 : ℝ := 0

def review_117 : ℝ := 0

def review_120 : ℝ := 0

def review_14 : ℝ := 0

def review_15 : ℝ := 0

def review_16 : ℝ := 0

def review_17 : ℝ := 0

def review_19 : ℝ := 0

def review_20 : ℝ := 0

def review_23 : ℝ := 0

def review_29 : ℝ := 0

def review_3 : ℝ := 0

def review_41 : ℝ := 0

def review_42 : ℝ := 0

def review_5 : ℝ := 0

def review_53 : ℝ := 0

def review_54 : ℝ := 0

def review_66 : ℝ := 0

def review_68 : ℝ := 0

def review_70 : ℝ := 0

def review_74 : ℝ := 0

def review_75 : ℝ := 0

def review_85 : ℝ := 0

def review_86 : ℝ := 0

def review_97 : ℝ := 0

def s_0 : ℝ := 0

def s_0s : ℝ := 0

def s_1 : ℝ := 0

def s_2 : ℝ := 0

def s_A : ℝ := 0

def summary_8 : ℝ := 0

def t_1 : ℝ := 0

def theta : ℝ := 0

def times2 : ℝ := 0

def to0 : ℝ := 0

def ts7 : ℝ := 0

def varphi_K : ℝ := 0

def varphi_L : ℝ := 0

def varphi_P : ℝ := 0

def vec0 : ℝ := 0

def x_0 : ℝ := 0

def x_1 : ℝ := 0

def x_1' : ℝ := 0

def x_A : ℝ := 0

def x_B : ℝ := 0

def y_1 : ℝ := 0

def z_0 : ℝ := 0

def z_1 : ℝ := 0

def z_2 : ℝ := 0

def z_5 : ℝ := 0

def zp_3 : ℝ := 0

def zp_4 : ℝ := 0

def phi_L : ℝ := 0

def h_random_line_bundle_degree_3 : ℝ := 0

def h_smooth_4 : ℝ := 0

def h_source_2_2 : ℝ := 0

def h_stable_curve_genus_1 : ℝ := 0

def h_uniform_respect_translation_invariant_5 : ℝ := 0

def hY_tilde : ℝ := 0

def hI_tilde : ℝ := 0

def hL_tilde : ℝ := 0

def h_general_point_1 : ℝ := 0

def scattering_amplitude_degree_2g : ℝ := 0

def u_1 : ℝ := 0

def u_2 : ℝ := 0

def u_4 : ℝ := 0

def u_5 : ℝ := 0

def u_6 : ℝ := 0

def h_MHV : ℝ := 0

def h_not_2_connected : ℝ := 0

def not_MHV_if_not_2_connected : ℝ := 0

def h_not_3_connected : ℝ := 0

def node1 : ℝ := 0

def node2 : ℝ := 0

def h_gives_double_cover_marked_2 : ℝ := 0

def h_hyperelliptic_curve_equation_polynomial_1 : ℝ := 0

def h_show_lemma_ssrhs_scattering_4 : ℝ := 0

def h_study_pointed_hyperelliptic_curves_3 : ℝ := 0

def h_contents_sfgasgsrh_genus_every_1 : ℝ := 0

def h_general_marked_points_away_4 : ℝ := 0

def h_genus_works_choice_2 : ℝ := 0

def h_show_theorem_sdgsg_whenever_3 : ℝ := 0

def h_PGL2_action : ℝ := 0

def h_phi_L_isomorphism : ℝ := 0

def h_phi_L_maps : ℝ := 0

def p1 : ℝ := 0

def p2 : ℝ := 0

def p3 : ℝ := 0

def scattering_amplitude_MHV_curve : ℝ := 0

def h_E_degenerates : ℝ := 0

def h_Pic_0101_equiv : ℝ := 0

def h_Pic_1010_equiv : ℝ := 0

def h_Pic_E_degenerates : ℝ := 0

def scattering_amplitude_MHV_curve_degeneration : ℝ := 0

def scattering_amplitude_MHV_curve_localization : ℝ := 0

def L2Space : Set (ℝ → ℝ) := Set.univ

def planar_locus_W : ℝ := 0

def h_E : ℝ := 0

def h_Eij : ℝ := 0

def loci_E_and_Eij : ℝ := 0

def genus_1_2_W_empty : ℝ := 0

def f2 : ℝ := 0

def lam0 : ℝ := 0

def p4 : ℝ := 0

def h_aefaf_curve_channel_factorization_1 : ℝ := 0

def h_generic_line_bundle_2 : ℝ := 0

def compact_Jacobian : ℝ := 0

def h_C_glued : ℝ := 0

def h_L_Pic : ℝ := 0

def h_L_decomp : ℝ := 0

def h_Pic_equiv : ℝ := 0

def h_p1_first : ℝ := 0

def h_p2_first : ℝ := 0

def h_p3_second : ℝ := 0

def h_p4_second : ℝ := 0

def h_phi_L_regular : ℝ := 0

def scattering_amplitude_map_Lambda_maps_Pic_1_1_C_to_M_0_4 : ℝ := 0

def u_3 : ℝ := 0

def rational_component_has_less_than_3_special_points : ℝ := 0

def h_curve_channel_factorization_1 : ℝ := 0

def h_does_admit_alternative_channel_4 : ℝ := 0

def h_source_3_3 : ℝ := 0

def h_section_will_globalize_scattering_1 : ℝ := 0

def hphi_L_A : ℝ := 0

def hphi_L_A_x : ℝ := 0

def hphi_L_A_y : ℝ := 0

def hphi_L_B : ℝ := 0

def hphi_L_B_x : ℝ := 0

def hphi_L_B_y : ℝ := 0

def phi_L_A : ℝ := 0

def phi_L_B : ℝ := 0

def phi_zL : ℝ := 0

def separated_Pic_MHV_0_of_MHV_curve : ℝ := 0

def hA_module : ℝ := 0

def hA_t2 : ℝ := 0

def hA_top : ℝ := 0

def hC_module : ℝ := 0

def hC_t2 : ℝ := 0

def hC_top : ℝ := 0

def hL_module : ℝ := 0

def hL_t2 : ℝ := 0

def hL_top : ℝ := 0

def hY_module : ℝ := 0

def hY_t2 : ℝ := 0

def hY_top : ℝ := 0

def h_Gieseker : ℝ := 0

def h_no_deg0 : ℝ := 0

def h_stab_A : ℝ := 0

def h_stab_B : ℝ := 0

def scattering_amplitude_MHV_curve_two_channel_factorization : ℝ := 0

def mhv_line_bundle_A_stable : ℝ := 0

def hC1 : ℝ := 0

def hC2 : ℝ := 0

def hC3 : ℝ := 0

def hC4 : ℝ := 0

def hL_deg2 : ℝ := 0

def hL_deg4 : ℝ := 0

def hphi_L : ℝ := 0

def hphi_L_C1 : ℝ := 0

def hphi_L_C2 : ℝ := 0

def hphi_L_C3 : ℝ := 0

def hphi_L_C4 : ℝ := 0

def hL_deg : ℝ := 0

def boundary_M_0_5 : ℝ := 0

def genus_2_MHV_example : ℝ := 0

def non_MHV_components : ℝ := 0

def g2 : ℝ := 0

def hW_card : ℝ := 0

def hW_fixed : ℝ := 0

def s1 : ℝ := 0

def s2 : ℝ := 0

def scattering_amplitude_map_SSRHS : ℝ := 0

def contraction_of_conic_through_z1_to_z5 : ℝ := 0

def dP4 : ℝ := 0

def dP5 : ℝ := 0

def example_2007_03831 : ℝ := 0

def model1 : ℝ := 0

def model2 : ℝ := 0

def h_indeed_solution_homogeneous_system_3 : ℝ := 0

def h_solutions_infinity_homogeneous_equation_2 : ℝ := 0

def h_srfqwrg_smooth_solution_dimension_1 : ℝ := 0

def el_I : ℝ := 0

def h_M_distinct_eigenspaces : ℝ := 0

def assumption_slot_2_anchor_missing : ℝ := 0

def h_Bertini : ℝ := 0

def h_M_nilpotent : ℝ := 0

def h_Weierstrass : ℝ := 0

def h_count_Weierstrass : ℝ := 0

def count_solutions_with_Weierstrass_points : ℝ := 0

def genus_1_translation_invariant_vector_field : ℝ := 0

def dot_F : ℝ := 0

def dot_F_i : ℝ := 0

def dot_U : ℝ := 0

def dot_V : ℝ := 0

def dot_W : ℝ := 0

def h_A_dual : ℝ := 0

def h_A_vee_def : ℝ := 0

def h_E_map : ℝ := 0

def h_E_slopes : ℝ := 0

def h_Q_map : ℝ := 0

def h_Q_quotient : ℝ := 0

def h_U : ℝ := 0

def h_U_divides : ℝ := 0

def h_U_no_multiple_roots : ℝ := 0

def h_V_interp : ℝ := 0

def h_W : ℝ := 0

def h_W_def : ℝ := 0

def h_deg_U : ℝ := 0

def h_deg_V : ℝ := 0

def h_deg_W : ℝ := 0

def h_dot_U_eq : ℝ := 0

def h_dot_V_eq : ℝ := 0

def h_dot_W_eq : ℝ := 0

def deg_U : ℝ := 0

def deg_V : ℝ := 0

def deg_W : ℝ := 0

def dot_U_eq : ℝ := 0

def dot_V_eq : ℝ := 0

def dot_W_eq : ℝ := 0

def scattering_amplitude_map_degree_4 : ℝ := 0

def hP_in_ : ℝ := 0

def hphi_K : ℝ := 0

def hphi_P : ℝ := 0

def p5 : ℝ := 0

def phi_K : ℝ := 0

def phi_P : ℝ := 0

def h_endowed_real_structure_equivalently_2 : ℝ := 0

def h_real_points_connected_components_3 : ℝ := 0

def h_smooth_projective_complex_algebraic_1 : ℝ := 0

def genus_2_assumption_equivalence : ℝ := 0

def h1 : ℝ := 0

def h2 : ℝ := 0

def h3 : ℝ := 0

def h4 : ℝ := 0

def h5 : ℝ := 0

def h6 : ℝ := 0

def h7 : ℝ := 0

def p6 : ℝ := 0

def conic_through_p1_to_p5 : ℝ := 0

def hDelta_i : ℝ := 0

def hDelta_ij : ℝ := 0

def hE_i : ℝ := 0

def hE_ij : ℝ := 0

def hLambda_morphism : ℝ := 0

def ramified_morphism_2 : ℝ := 0

def scattering_amplitude_MHV_curve_degree_2g : ℝ := 0

def bold_Lambda_I : ℝ := 0

def hbold_Lambda_I : ℝ := 0

def h_p_g2g3 : ℝ := 0

def map_C1_times_Cg_to_Pic_H_g_plus_1 : ℝ := 0

def phi_L_restricted_to_C_i : ℝ := 0

def z_g_plus_1 : ℝ := 0

def f4 : ℝ := 0

def p_12 : ℝ := 0

def p_13 : ℝ := 0

def p_14 : ℝ := 0

def p_23 : ℝ := 0

def p_24 : ℝ := 0

def p_34 : ℝ := 0

def scattering_amplitude_map_MHV_curve_type_A_B : ℝ := 0

def f_degree_5 : ℝ := 0

def h8 : ℝ := 0

def h9 : ℝ := 0

def p_5_ : ℝ := 0

def smooth_MHV_M_curve_of_genus_2 : ℝ := 0

def hA_vanish : ℝ := 0

def hC_real_MHV : ℝ := 0

def hW_codim : ℝ := 0

def hW_param : ℝ := 0

def hW_real : ℝ := 0

def hW_real_Harnack : ℝ := 0

def hW_real_even : ℝ := 0

def hW_real_line_degree : ℝ := 0

def hW_real_line_degree_even : ℝ := 0

def hW_real_odd : ℝ := 0

def hW_real_subset_even : ℝ := 0

def hW_real_subset_odd : ℝ := 0

def real_planar_locus_W : ℝ := 0

def scattering_amplitude_measure_on_dP4_real_viewed_as_measure_on_RP1_squared : ℝ := 0

def h_Bl_P2 : ℝ := 0

def h_Bl_Pic3C : ℝ := 0

def h_connected_P2 : ℝ := 0

def h_p1 : ℝ := 0

def h_p2 : ℝ := 0

def h_p3 : ℝ := 0

def h_p4 : ℝ := 0

def h_p4_p5 : ℝ := 0

def h_p5 : ℝ := 0

def delta_34 : ℝ := 0

def delta_35 : ℝ := 0

def delta_45 : ℝ := 0

def h_Lambda : ℝ := 0

def h_dP4_to_dP5 : ℝ := 0

def h_dP5_exc_div : ℝ := 0

def l1 : ℝ := 0

def l2 : ℝ := 0

def characterization_of_MHV_curves_of_maximally_degenerate_type : ℝ := 0

def Γ_i : ℝ := 0

def degree_of_L_on_component : ℝ := 0

def mhv_curve_degree_L_on_components : ℝ := 0

def components_of_C : ℝ := 0

def scattering_amplitude_MHV_curve_iff_CT_hypertree : ℝ := 0

def Γ_j : ℝ := 0

def hC_no_2_channel : ℝ := 0

def hC_real : ℝ := 0

def hC_t : ℝ := 0

def hW_acnode : ℝ := 0

def hW_deg0 : ℝ := 0

def first_non_MHV_case_666_puzzle : ℝ := 0

-- Local lemmas / theorem-like facts.
-- Explicit axioms / unresolved paper assumptions.

end Paper_2007_03831

export Paper_2007_03831 (omega infty C a a_C a_Y alpha_1 amplification_107 amplification_61 bp_1 c_1 corollary_39 corollary_60 corollary_93 d_A d_B d_C d_Y delta_1 delta_2 delta_3 delta_4 delta_5 elli4 example_131 example_24 example_33 example_65 example_76 example_89 f_2 f_4 g_A g_B g_Y ge1 ge2 ge3 hskip2 hskip5 lambda_0 lemma_121 lemma_32 lemma_59 lemma_91 lower4 mathcalO_C n6 n_0 n_A n_B n_Y ne0 oN_6 oS_7 oS_7' omega_C on6 os7 otimes2 over2 over5 p_1 p_1p_2 p_1p_2p_3p_4 p_1p_3 p_1p_4 p_2 p_2p_3 p_2p_4 p_3 p_3p_4 p_4 p_5 p_6 pi_1 pi_A pic2 q_1 q_2 q_3 q_4 review_1 review_101 review_112 review_114 review_117 review_120 review_14 review_15 review_16 review_17 review_19 review_20 review_23 review_29 review_3 review_41 review_42 review_5 review_53 review_54 review_66 review_68 review_70 review_74 review_75 review_85 review_86 review_97 s_0 s_0s s_1 s_2 s_A summary_8 t_1 theta times2 to0 ts7 varphi_K varphi_L varphi_P vec0 x_0 x_1 x_1' x_A x_B y_1 z_0 z_1 z_2 z_5 zp_3 zp_4 phi_L h_random_line_bundle_degree_3 h_smooth_4 h_source_2_2 h_stable_curve_genus_1 h_uniform_respect_translation_invariant_5 hY_tilde hI_tilde hL_tilde h_general_point_1 scattering_amplitude_degree_2g u_1 u_2 u_4 u_5 u_6 h_MHV h_not_2_connected not_MHV_if_not_2_connected h_not_3_connected node1 node2 h_gives_double_cover_marked_2 h_hyperelliptic_curve_equation_polynomial_1 h_show_lemma_ssrhs_scattering_4 h_study_pointed_hyperelliptic_curves_3 h_contents_sfgasgsrh_genus_every_1 h_general_marked_points_away_4 h_genus_works_choice_2 h_show_theorem_sdgsg_whenever_3 h_PGL2_action h_phi_L_isomorphism h_phi_L_maps p1 p2 p3 scattering_amplitude_MHV_curve h_E_degenerates h_Pic_0101_equiv h_Pic_1010_equiv h_Pic_E_degenerates scattering_amplitude_MHV_curve_degeneration scattering_amplitude_MHV_curve_localization L2Space planar_locus_W h_E h_Eij loci_E_and_Eij genus_1_2_W_empty f2 lam0 p4 h_aefaf_curve_channel_factorization_1 h_generic_line_bundle_2 compact_Jacobian h_C_glued h_L_Pic h_L_decomp h_Pic_equiv h_p1_first h_p2_first h_p3_second h_p4_second h_phi_L_regular scattering_amplitude_map_Lambda_maps_Pic_1_1_C_to_M_0_4 u_3 rational_component_has_less_than_3_special_points h_curve_channel_factorization_1 h_does_admit_alternative_channel_4 h_source_3_3 h_section_will_globalize_scattering_1 hphi_L_A hphi_L_A_x hphi_L_A_y hphi_L_B hphi_L_B_x hphi_L_B_y phi_L_A phi_L_B phi_zL separated_Pic_MHV_0_of_MHV_curve hA_module hA_t2 hA_top hC_module hC_t2 hC_top hL_module hL_t2 hL_top hY_module hY_t2 hY_top h_Gieseker h_no_deg0 h_stab_A h_stab_B scattering_amplitude_MHV_curve_two_channel_factorization mhv_line_bundle_A_stable hC1 hC2 hC3 hC4 hL_deg2 hL_deg4 hphi_L hphi_L_C1 hphi_L_C2 hphi_L_C3 hphi_L_C4 hL_deg boundary_M_0_5 genus_2_MHV_example non_MHV_components g2 hW_card hW_fixed s1 s2 scattering_amplitude_map_SSRHS contraction_of_conic_through_z1_to_z5 dP4 dP5 example_2007_03831 model1 model2 h_indeed_solution_homogeneous_system_3 h_solutions_infinity_homogeneous_equation_2 h_srfqwrg_smooth_solution_dimension_1 el_I h_M_distinct_eigenspaces assumption_slot_2_anchor_missing h_Bertini h_M_nilpotent h_Weierstrass h_count_Weierstrass count_solutions_with_Weierstrass_points genus_1_translation_invariant_vector_field dot_F dot_F_i dot_U dot_V dot_W h_A_dual h_A_vee_def h_E_map h_E_slopes h_Q_map h_Q_quotient h_U h_U_divides h_U_no_multiple_roots h_V_interp h_W h_W_def h_deg_U h_deg_V h_deg_W h_dot_U_eq h_dot_V_eq h_dot_W_eq deg_U deg_V deg_W dot_U_eq dot_V_eq dot_W_eq scattering_amplitude_map_degree_4 hP_in_ hphi_K hphi_P p5 phi_K phi_P h_endowed_real_structure_equivalently_2 h_real_points_connected_components_3 h_smooth_projective_complex_algebraic_1 genus_2_assumption_equivalence h1 h2 h3 h4 h5 h6 h7 p6 conic_through_p1_to_p5 hDelta_i hDelta_ij hE_i hE_ij hLambda_morphism ramified_morphism_2 scattering_amplitude_MHV_curve_degree_2g bold_Lambda_I hbold_Lambda_I h_p_g2g3 map_C1_times_Cg_to_Pic_H_g_plus_1 phi_L_restricted_to_C_i z_g_plus_1 f4 p_12 p_13 p_14 p_23 p_24 p_34 scattering_amplitude_map_MHV_curve_type_A_B f_degree_5 h8 h9 p_5_ smooth_MHV_M_curve_of_genus_2 hA_vanish hC_real_MHV hW_codim hW_param hW_real hW_real_Harnack hW_real_even hW_real_line_degree hW_real_line_degree_even hW_real_odd hW_real_subset_even hW_real_subset_odd real_planar_locus_W scattering_amplitude_measure_on_dP4_real_viewed_as_measure_on_RP1_squared h_Bl_P2 h_Bl_Pic3C h_connected_P2 h_p1 h_p2 h_p3 h_p4 h_p4_p5 h_p5 delta_34 delta_35 delta_45 h_Lambda h_dP4_to_dP5 h_dP5_exc_div l1 l2 characterization_of_MHV_curves_of_maximally_degenerate_type Γ_i degree_of_L_on_component mhv_curve_degree_L_on_components components_of_C scattering_amplitude_MHV_curve_iff_CT_hypertree Γ_j hC_no_2_channel hC_real hC_t hW_acnode hW_deg0 first_non_MHV_case_666_puzzle)
