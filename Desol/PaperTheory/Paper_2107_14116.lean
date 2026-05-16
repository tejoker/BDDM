-- Auto-generated paper theory module
-- paper_id: 2107.14116
-- domain: default

import Mathlib
import Aesop

open MeasureTheory ProbabilityTheory Filter Set

namespace Paper_2107_14116

-- note: declared 112 paper-local symbol(s) from inventory
-- note: definition stubs are notation grounding only and are not proof-countable

-- ------------------------------------------------------------------
-- Paper-local definitions and explicit axiom debt
-- Any result depending on this module must be reported as proved
-- modulo any paper-local axioms below, not as unconditional closure.
-- ------------------------------------------------------------------

-- Definition stubs ground paper-local identifiers before proof search.
-- They are transparent Lean definitions for elaboration, not hidden proofs of paper claims.
noncomputable def W_alpha_def {S : Type*} [MetricSpace S] (X_alpha : Set S) (W_alpha : Set (Set S)) (i : S → S → ℝ) (h_i : ∀ c d, i c d ≥ 0) : W_alpha = {rho : Set S | ∃ c ∈ rho, ∃ d ∈ rho, i c d ≤ 4 ∧ rho = (rho \ {d}) ∪ {c}} := sorry

def omega (_i _k : ℕ) : ℝ := 0

def infty : ℝ := 0

def C : ℝ := 0

def a : ℝ := 0

def a_1 : ℝ := 0

def a_2 : ℝ := 0

def a_2' : ℝ := 0

def annular_C : ℝ := 0

def augment_X_quasi : ℝ := 0

def behrstock_Drutu_Mosher_Thickness : ℝ := 0

def boundary_V_is_in_X : ℝ := 0

def d_H : ℝ := 0

def d_U : ℝ := 0

def d_V : ℝ := 0

def d_X : ℝ := 0

def d_Y : ℝ := 0

def description_of_U : ℝ := 0

def example_13 : ℝ := 0

def example_17 : ℝ := 0

def example_20 : ℝ := 0

def exappend_78 : ℝ := 0

def h_1 : ℝ := 0

def h_2 : ℝ := 0

def hyp_X : ℝ := 0

def mu_1 : ℝ := 0

def mu_2 : ℝ := 0

def partial_M : ℝ := 0

def pi_1 : ℝ := 0

def pi_U : ℝ := 0

def rho_A : ℝ := 0

def rho_U : ℝ := 0

def subsurface_projection_coarsely_Lipschtiz : ℝ := 0

def subsurface_type_QI_embed : ℝ := 0

def sufficent_omega_Morse : ℝ := 0

def tau_0 : ℝ := 0

def top_CHHS_dictionary : ℝ := 0

def utf8 : ℝ := 0

def w_0 : ℝ := 0

def w_1 : ℝ := 0

def x_0 : ℝ := 0

def x_1 : ℝ := 0

def x_2 : ℝ := 0

def y_1 : ℝ := 0

def y_2 : ℝ := 0

def z_1 : ℝ := 0

def z_2 : ℝ := 0

def hE_alpha : ℝ := 0

def hG_alpha : ℝ := 0

def intro_thm_E_alpha_is_HHG : ℝ := 0

def diam_Y : ℝ := 0

def u_1 : ℝ := 0

def u_2 : ℝ := 0

def x1 : ℝ := 0

def x2 : ℝ := 0

def h_source_1_1 : ℝ := 0

def h_source_2_2 : ℝ := 0

def h1 : ℝ := 0

def h2 : ℝ := 0

def h3 : ℝ := 0

def h4 : ℝ := 0

def h5 : ℝ := 0

def h6 : ℝ := 0

def psi_coarsely_Lipschitz : ℝ := 0

def psi_id_on_H0 : ℝ := 0

def y1 : ℝ := 0

def y2 : ℝ := 0

def d_M : ℝ := 0

def d_C : ℝ := 0

def d_dtU : ℝ := 0

def coarsely_Lipschtiz : ℝ := 0

def delta1 : ℝ := 0

def delta2 : ℝ := 0

def u_3 : ℝ := 0

def u_6 : ℝ := 0

def ball_R : ℝ := 0

def h_simplex_1 : ℝ := 0

def h_source_3_3 : ℝ := 0

def hF_guided : ℝ := 0

def hF_conn : ℝ := 0

def hF_tree : ℝ := 0

def is_F_i_guided : ℝ := 0

def hZ_disj : ℝ := 0

def d_dtQ : ℝ := 0

def hd_dtQ_X : ℝ := 0

def hgamma_X : ℝ := 0

def boundary_U_mu : ℝ := 0

def hxy_no_X_edge : ℝ := 0

def d_dtU_mu : ℝ := 0

def u_4 : ℝ := 0

def augment_X_quasi_tree : ℝ := 0

def lk_B : ℝ := 0

def assumption_slot_1_anchor_missing : ℝ := 0

def assumption_slot_2_anchor_missing : ℝ := 0

def annular_C_eq_C0 : ℝ := 0

def hxy_BW_edge : ℝ := 0

def hxy_not_BX_edge : ℝ := 0

def b1 : ℝ := 0

def b2 : ℝ := 0

def equiv_B_X_alpha : ℝ := 0

def equiv_E_alpha : ℝ := 0

def equiv_X_alpha : ℝ := 0

def h_equiv_B_X_alpha : ℝ := 0

def h_equiv_X_alpha : ℝ := 0

def h_compact_2 : ℝ := 0

def h_contains_cantor_space_3 : ℝ := 0

def h_totally_disconnected_1 : ℝ := 0

def hT_max : ℝ := 0

def h_Morse_boundary : ℝ := 0

def d_dt_M : ℝ := 0

def genus_2_handlebody : ℝ := 0

def handlebody_group_genus_2_morse_boundary_is_omega_cantor_space : ℝ := 0

def omega_Cantor_set : ℝ := 0

-- Local lemmas / theorem-like facts.
-- Explicit axioms / unresolved paper assumptions.

end Paper_2107_14116

export Paper_2107_14116 (omega infty C a a_1 a_2 a_2' annular_C augment_X_quasi behrstock_Drutu_Mosher_Thickness boundary_V_is_in_X d_H d_U d_V d_X d_Y description_of_U example_13 example_17 example_20 exappend_78 h_1 h_2 hyp_X mu_1 mu_2 partial_M pi_1 pi_U rho_A rho_U subsurface_projection_coarsely_Lipschtiz subsurface_type_QI_embed sufficent_omega_Morse tau_0 top_CHHS_dictionary utf8 w_0 w_1 x_0 x_1 x_2 y_1 y_2 z_1 z_2 hE_alpha hG_alpha intro_thm_E_alpha_is_HHG diam_Y u_1 u_2 x1 x2 h_source_1_1 h_source_2_2 h1 h2 h3 h4 h5 h6 psi_coarsely_Lipschitz psi_id_on_H0 y1 y2 d_M d_C d_dtU coarsely_Lipschtiz delta1 delta2 u_3 u_6 ball_R h_simplex_1 h_source_3_3 hF_guided hF_conn hF_tree is_F_i_guided hZ_disj d_dtQ hd_dtQ_X hgamma_X boundary_U_mu hxy_no_X_edge d_dtU_mu u_4 augment_X_quasi_tree lk_B assumption_slot_1_anchor_missing assumption_slot_2_anchor_missing annular_C_eq_C0 hxy_BW_edge hxy_not_BX_edge b1 b2 equiv_B_X_alpha equiv_E_alpha equiv_X_alpha h_equiv_B_X_alpha h_equiv_X_alpha h_compact_2 h_contains_cantor_space_3 h_totally_disconnected_1 hT_max h_Morse_boundary d_dt_M genus_2_handlebody handlebody_group_genus_2_morse_boundary_is_omega_cantor_space omega_Cantor_set)
