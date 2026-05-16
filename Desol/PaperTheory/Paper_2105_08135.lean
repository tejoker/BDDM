-- Auto-generated paper theory module
-- paper_id: 2105.08135
-- domain: default

import Mathlib
import Aesop

open MeasureTheory ProbabilityTheory Filter Set

namespace Paper_2105_08135

-- note: declared 240 paper-local symbol(s) from inventory
-- note: definition stubs are notation grounding only and are not proof-countable

-- ------------------------------------------------------------------
-- Paper-local definitions and explicit axiom debt
-- Any result depending on this module must be reported as proved
-- modulo any paper-local axioms below, not as unconditional closure.
-- ------------------------------------------------------------------

-- Definition stubs ground paper-local identifiers before proof search.
-- They are transparent Lean definitions for elaboration, not hidden proofs of paper claims.
noncomputable def def_local_symmetries (x : ℝ) :
  ∃ x : ℝ, eta > 0 := by

def L2Space : Set (ℝ → ℝ) := Set.univ

def omega (_i _k : ℕ) : ℝ := 0

def infty : ℝ := 0

def C : ℝ := 0

def a : ℝ := 0

def a1 : ℝ := 0

def a2 : ℝ := 0

def bA_k : ℝ := 0

def bB_1 : ℝ := 0

def bB_2 : ℝ := 0

def bB_3 : ℝ := 0

def bB_4 : ℝ := 0

def bB_8 : ℝ := 0

def bB_R : ℝ := 0

def bB_r : ℝ := 0

def bC_0 : ℝ := 0

def bC_1 : ℝ := 0

def bC_j : ℝ := 0

def bC_k : ℝ := 0

def bC_q : ℝ := 0

def bE_0 : ℝ := 0

def bE_k : ℝ := 0

def bH_i : ℝ := 0

def bS_0 : ℝ := 0

def bS_k : ℝ := 0

def beta_1 : ℝ := 0

def c_0 : ℝ := 0

def c_Q : ℝ := 0

def d_Q : ℝ := 0

def delta_0 : ℝ := 0

def eps_1 : ℝ := 0

def eps_2 : ℝ := 0

def eta_1 : ℝ := 0

def eta_2 : ℝ := 0

def eta_3 : ℝ := 0

def eta_4 : ℝ := 0

def eta_5 : ℝ := 0

def flat_C_tildeC : ℝ := 0

def flat_L2 : ℝ := 0

def frac1 : ℝ := 0

def frac12 : ℝ := 0

def frac18 : ℝ := 0

def frac32 : ℝ := 0

def frac74 : ℝ := 0

def g_1 : ℝ := 0

def g_2 : ℝ := 0

def g_N : ℝ := 0

def global_selection_1 : ℝ := 0

def global_selection_2 : ℝ := 0

def graph_v1 : ℝ := 0

def graph_v2 : ℝ := 0

def graphical2 : ℝ := 0

def h_0 : ℝ := 0

def hp0 : ℝ := 0

def hp1 : ℝ := 0

def hp2 : ℝ := 0

def hyp_flat_T_C0 : ℝ := 0

def int_0 : ℝ := 0

def int_V : ℝ := 0

def intro_v2 : ℝ := 0

def lemma_74 : ℝ := 0

def nabla_V : ℝ := 0

def p2 : ℝ := 0

def p_K : ℝ := 0

def p_V : ℝ := 0

def p_W : ℝ := 0

def partialT_j : ℝ := 0

def perp_0 : ℝ := 0

def pi_0 : ℝ := 0

def proposition_16 : ℝ := 0

def psi_0 : ℝ := 0

def q_0 : ℝ := 0

def q_1 : ℝ := 0

def q_2 : ℝ := 0

def r_0 : ℝ := 0

def rep_Holder : ℝ := 0

def setbox0 : ℝ := 0

def sfrac1 : ℝ := 0

def sfrac12 : ℝ := 0

def sigma_0 : ℝ := 0

def slicing2 : ℝ := 0

def t_Q : ℝ := 0

def tau_1 : ℝ := 0

def theorem_11 : ℝ := 0

def theta : ℝ := 0

def theta_T : ℝ := 0

def to0 : ℝ := 0

def triangle1 : ℝ := 0

def triangle2 : ℝ := 0

def u_1 : ℝ := 0

def u_2 : ℝ := 0

def v2 : ℝ := 0

def varepsilon_1 : ℝ := 0

def varepsilon_2 : ℝ := 0

def varepsilon_3 : ℝ := 0

def w12 : ℝ := 0

def wd0 : ℝ := 0

def x_0 : ℝ := 0

def x_1 : ℝ := 0

def x_2 : ℝ := 0

def x_3 : ℝ := 0

def y_0 : ℝ := 0

def y_Q : ℝ := 0

def z_1 : ℝ := 0

def z_2 : ℝ := 0

def h_source_1_1 : ℝ := 0

def hdim1 : ℝ := 0

def hdim2 : ℝ := 0

def hC_0 : ℝ := 0

def Θ_T : ℝ := 0

def bC0 : ℝ := 0

def beta1 : ℝ := 0

def d_dtT : ℝ := 0

def hC0 : ℝ := 0

def hT1 : ℝ := 0

def hT2 : ℝ := 0

def hT3 : ℝ := 0

def c_singStar : ℝ := 0

def h_assumption_assumption_manifolds_currents_2 : ℝ := 0

def h_decay_estimate_decay_integer_1 : ℝ := 0

def hE_limit : ℝ := 0

def hTj_bound : ℝ := 0

def hTj_boundary : ℝ := 0

def hTj_flat : ℝ := 0

def flat_L2_estimate : ℝ := 0

def hC_pos : ℝ := 0

def hC_decomp : ℝ := 0

def hV_dim : ℝ := 0

def hV_perp : ℝ := 0

def hd_dtT : ℝ := 0

def hK0 : ℝ := 0

def lemma_L2_controls_Linfty : ℝ := 0

def eta_1_S0 : ℝ := 0

def eta_2_S0 : ℝ := 0

def eta_S0 : ℝ := 0

def h1 : ℝ := 0

def h2 : ℝ := 0

def h3 : ℝ := 0

def h4 : ℝ := 0

def hat_F_p_B_R0_C_C0 : ℝ := 0

def hat_F_p_B_R0_T_C0 : ℝ := 0

def theta_S0 : ℝ := 0

def delta0 : ℝ := 0

def eps1 : ℝ := 0

def hC1 : ℝ := 0

def hS0 : ℝ := 0

def heps1 : ℝ := 0

def lemma_White : ℝ := 0

def hN0 : ℝ := 0

def hQ_i : ℝ := 0

def d_dtmu_C : ℝ := 0

def d_dtmu_T : ℝ := 0

def hE_C : ℝ := 0

def hE_T : ℝ := 0

def hQ_in_W : ℝ := 0

def h_T_restriction : ℝ := 0

def h_S : ℝ := 0

def h_S0 : ℝ := 0

def h_S_tilde : ℝ := 0

def h_eps1 : ℝ := 0

def h_A : ℝ := 0

def h_C : ℝ := 0

def h_C0 : ℝ := 0

def h_E : ℝ := 0

def h_T : ℝ := 0

def hC2 : ℝ := 0

def hd_Q : ℝ := 0

def hQ_max : ℝ := 0

def hC_C : ℝ := 0

def hC_dep : ℝ := 0

def hS_def : ℝ := 0

def eps2 : ℝ := 0

def hbeta1 : ℝ := 0

def hq0 : ℝ := 0

def q0 : ℝ := 0

def x0 : ℝ := 0

def y0 : ℝ := 0

def Θ_C : ℝ := 0

def delta1 : ℝ := 0

def delta2 : ℝ := 0

def hO_C : ℝ := 0

def hO_C0 : ℝ := 0

def hT0 : ℝ := 0

def hT_q0_lam : ℝ := 0

def heps2 : ℝ := 0

def h_Corollary_reparametrization : ℝ := 0

def h_Hardt_Simon_main : ℝ := 0

def h_O_bound : ℝ := 0

def h_O_isometry : ℝ := 0

def h_T_q : ℝ := 0

def h_cone_O_C : ℝ := 0

def d_dtMeasureTheory : ℝ := 0

def h5 : ℝ := 0

def h6 : ℝ := 0

def h7 : ℝ := 0

def l_monot_Jonas : ℝ := 0

def hC_cone : ℝ := 0

def hO_id_minimal : ℝ := 0

def hO_orthogonal : ℝ := 0

def hT_q : ℝ := 0

def h_E_sum : ℝ := 0

def h_beta_lt_beta1 : ℝ := 0

def h_flat_T_C0 : ℝ := 0

def assumption_slot_2_anchor_missing : ℝ := 0

def assumption_slot_3_anchor_missing : ℝ := 0

def assumption_slot_4_anchor_missing : ℝ := 0

def assumption_slot_5_anchor_missing : ℝ := 0

def eps_NH : ℝ := 0

def hT_boundary : ℝ := 0

def hT_min : ℝ := 0

def pi0 : ℝ := 0

def eps3 : ℝ := 0

def hB12 : ℝ := 0

def heps3 : ℝ := 0

def hE_lim : ℝ := 0

def h_U : ℝ := 0

def p0 : ℝ := 0

def p_H0 : ℝ := 0

def ξ_k : ℝ := 0

def hpi0 : ℝ := 0

def d_dtC_0 : ℝ := 0

def elementare_Watson : ℝ := 0

def hB_r : ℝ := 0

def hL_depends : ℝ := 0

def hL_linear : ℝ := 0

def decaduto_A_ltltE : ℝ := 0

def hC_prime : ℝ := 0

def eta5 : ℝ := 0

def heta5 : ℝ := 0

def invariance_rotation_x3_axis : ℝ := 0

def x1 : ℝ := 0

def x2 : ℝ := 0

def x3 : ℝ := 0

def hat_F_p_W : ℝ := 0

def hK_subset : ℝ := 0

def hW_subset : ℝ := 0

def hB1 : ℝ := 0

def hE_zero : ℝ := 0

def hF_lt : ℝ := 0

def hT_modp : ℝ := 0

-- Local lemmas / theorem-like facts.
-- Explicit axioms / unresolved paper assumptions.

end Paper_2105_08135

export Paper_2105_08135 (L2Space omega infty C a a1 a2 bA_k bB_1 bB_2 bB_3 bB_4 bB_8 bB_R bB_r bC_0 bC_1 bC_j bC_k bC_q bE_0 bE_k bH_i bS_0 bS_k beta_1 c_0 c_Q d_Q delta_0 eps_1 eps_2 eta_1 eta_2 eta_3 eta_4 eta_5 flat_C_tildeC flat_L2 frac1 frac12 frac18 frac32 frac74 g_1 g_2 g_N global_selection_1 global_selection_2 graph_v1 graph_v2 graphical2 h_0 hp0 hp1 hp2 hyp_flat_T_C0 int_0 int_V intro_v2 lemma_74 nabla_V p2 p_K p_V p_W partialT_j perp_0 pi_0 proposition_16 psi_0 q_0 q_1 q_2 r_0 rep_Holder setbox0 sfrac1 sfrac12 sigma_0 slicing2 t_Q tau_1 theorem_11 theta theta_T to0 triangle1 triangle2 u_1 u_2 v2 varepsilon_1 varepsilon_2 varepsilon_3 w12 wd0 x_0 x_1 x_2 x_3 y_0 y_Q z_1 z_2 h_source_1_1 hdim1 hdim2 hC_0 Θ_T bC0 beta1 d_dtT hC0 hT1 hT2 hT3 c_singStar h_assumption_assumption_manifolds_currents_2 h_decay_estimate_decay_integer_1 hE_limit hTj_bound hTj_boundary hTj_flat flat_L2_estimate hC_pos hC_decomp hV_dim hV_perp hd_dtT hK0 lemma_L2_controls_Linfty eta_1_S0 eta_2_S0 eta_S0 h1 h2 h3 h4 hat_F_p_B_R0_C_C0 hat_F_p_B_R0_T_C0 theta_S0 delta0 eps1 hC1 hS0 heps1 lemma_White hN0 hQ_i d_dtmu_C d_dtmu_T hE_C hE_T hQ_in_W h_T_restriction h_S h_S0 h_S_tilde h_eps1 h_A h_C h_C0 h_E h_T hC2 hd_Q hQ_max hC_C hC_dep hS_def eps2 hbeta1 hq0 q0 x0 y0 Θ_C delta1 delta2 hO_C hO_C0 hT0 hT_q0_lam heps2 h_Corollary_reparametrization h_Hardt_Simon_main h_O_bound h_O_isometry h_T_q h_cone_O_C d_dtMeasureTheory h5 h6 h7 l_monot_Jonas hC_cone hO_id_minimal hO_orthogonal hT_q h_E_sum h_beta_lt_beta1 h_flat_T_C0 assumption_slot_2_anchor_missing assumption_slot_3_anchor_missing assumption_slot_4_anchor_missing assumption_slot_5_anchor_missing eps_NH hT_boundary hT_min pi0 eps3 hB12 heps3 hE_lim h_U p0 p_H0 ξ_k hpi0 d_dtC_0 elementare_Watson hB_r hL_depends hL_linear decaduto_A_ltltE hC_prime eta5 heta5 invariance_rotation_x3_axis x1 x2 x3 hat_F_p_W hK_subset hW_subset hB1 hE_zero hF_lt hT_modp)
