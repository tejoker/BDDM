-- Auto-generated paper theory module
-- paper_id: 2206.03028
-- domain: default

import Mathlib
import Aesop

open MeasureTheory ProbabilityTheory Filter Set

namespace Paper_2206_03028

-- note: declared 276 paper-local symbol(s) from inventory
-- note: definition stubs are notation grounding only and are not proof-countable

-- ------------------------------------------------------------------
-- Paper-local definitions and explicit axiom debt
-- Any result depending on this module must be reported as proved
-- modulo any paper-local axioms below, not as unconditional closure.
-- ------------------------------------------------------------------

-- Definition stubs ground paper-local identifiers before proof search.
-- They are transparent Lean definitions for elaboration, not hidden proofs of paper claims.
noncomputable def eq_MC :
  checkpartiala + a* a = 0 := by

noncomputable def d_phi_def (p q : ℕ) (phi : ℝ) (b0 a0 : ℝ) (h_phi_deg : p + q = |phi|) :
  d_phi = b0 * phi - (-1)^(p + q) * phi * a0 := sorry

noncomputable def defn_67 (x : ℝ) (h_functors_1 : True) (h_natural_transformation_degree_element_2 : True) :
  ∀ x : ℝ, x = x := by

noncomputable def is_non_Archimedean_norm {R : Type*} [Ring R] (norm : R → ℝ)
    (h_norm_nonneg : ∀ a, 0 ≤ norm a) (h_mul : ∀ a b, norm (a * b) ≤ norm a * norm b)
    (h_add : ∀ a b, norm (a + b) ≤ max (norm a) (norm b)) (h_zero : ∀ a, norm a = 0 ↔ a = 0) :
    ∀ a b, norm (a * b) ≤ norm a * norm b ∧
    ∀ a b, norm (a + b) ≤ max (norm a) (norm b) ∧
    ∀ a, norm a = 0 ↔ a = 0 := sorry

noncomputable def defn_100 (x : ℝ) :
  ∀ x : ℝ, x = x := by

def infty : ℝ := 0

def C : ℝ := 0

def a : ℝ := 0

def a_0 : ℝ := 0

def a_0b_0 : ℝ := 0

def a_1 : ℝ := 0

def a_1a_3 : ℝ := 0

def a_1a_3a_2 : ℝ := 0

def a_1b_1 : ℝ := 0

def a_1b_3 : ℝ := 0

def a_1b_3c_2 : ℝ := 0

def a_1c : ℝ := 0

def a_1c_3b_2 : ℝ := 0

def a_2 : ℝ := 0

def a_2b_1 : ℝ := 0

def a_3 : ℝ := 0

def a_3b_2 : ℝ := 0

def alpha_1 : ℝ := 0

def alpha_2 : ℝ := 0

def alpha_3 : ℝ := 0

def b_0 : ℝ := 0

def b_1 : ℝ := 0

def b_1a : ℝ := 0

def b_1a_1 : ℝ := 0

def b_1a_3 : ℝ := 0

def b_1a_3c_2 : ℝ := 0

def b_1b_3 : ℝ := 0

def b_1b_3b_2 : ℝ := 0

def b_1b_3b_3 : ℝ := 0

def b_1c_1 : ℝ := 0

def b_1c_3 : ℝ := 0

def b_1c_3a_2 : ℝ := 0

def b_2 : ℝ := 0

def b_2a_1 : ℝ := 0

def b_3 : ℝ := 0

def b_3a_2 : ℝ := 0

def b_3b_3 : ℝ := 0

def b_iB_i : ℝ := 0

def beta_0 : ℝ := 0

def beta_1 : ℝ := 0

def beta_2 : ℝ := 0

def beta_3 : ℝ := 0

def c1 : ℝ := 0

def c2 : ℝ := 0

def cA_1 : ℝ := 0

def cA_2 : ℝ := 0

def cA_k : ℝ := 0

def cL_i : ℝ := 0

def c_1 : ℝ := 0

def c_1a : ℝ := 0

def c_1a_1 : ℝ := 0

def c_1a_3a_2 : ℝ := 0

def c_1a_3b_2 : ℝ := 0

def c_1b : ℝ := 0

def c_1b_3 : ℝ := 0

def c_1b_3a_2 : ℝ := 0

def c_1c_3 : ℝ := 0

def c_1c_3c_2 : ℝ := 0

def c_2 : ℝ := 0

def c_3 : ℝ := 0

def c_iC_i : ℝ := 0

def construction_80 : ℝ := 0

def e_1 : ℝ := 0

def e_2 : ℝ := 0

def e_2a_1 : ℝ := 0

def e_2b_1 : ℝ := 0

def e_2b_1b_3b_2 : ℝ := 0

def e_2c_1 : ℝ := 0

def e_3 : ℝ := 0

def example_101 : ℝ := 0

def example_94 : ℝ := 0

def f_1 : ℝ := 0

def f_1' : ℝ := 0

def f_1'' : ℝ := 0

def f_1'a_1 : ℝ := 0

def f_2 : ℝ := 0

def f_3 : ℝ := 0

def fig_S1 : ℝ := 0

def g2 : ℝ := 0

def g_1 : ℝ := 0

def g_1' : ℝ := 0

def g_1'' : ℝ := 0

def g_1'a_1 : ℝ := 0

def g_2 : ℝ := 0

def g_2c_1 : ℝ := 0

def g_3 : ℝ := 0

def g_3c_3 : ℝ := 0

def gluing_ncKP2 : ℝ := 0

def higher0 : ℝ := 0

def homotopy_KP2 : ℝ := 0

def i0 : ℝ := 0

def i_0 : ℝ := 0

def i_0i_ : ℝ := 0

def i_0i_1 : ℝ := 0

def i_0i_1i_2 : ℝ := 0

def i_0i_k : ℝ := 0

def i_0i_n : ℝ := 0

def i_0i_p : ℝ := 0

def i_0i_pi_q : ℝ := 0

def i_0i_pi_r : ℝ := 0

def i_0i_q : ℝ := 0

def i_0i_qi_r : ℝ := 0

def i_1 : ℝ := 0

def i_2 : ℝ := 0

def i_3 : ℝ := 0

def isomorphism_KP2 : ℝ := 0

def j0 : ℝ := 0

def k_1 : ℝ := 0

def k_2 : ℝ := 0

def l_0 : ℝ := 0

def l_1 : ℝ := 0

def lemma_112 : ℝ := 0

def lemma_26 : ℝ := 0

def lemma_33 : ℝ := 0

def lemma_40 : ℝ := 0

def lemma_48 : ℝ := 0

def lemma_81 : ℝ := 0

def m_0 : ℝ := 0

def m_1 : ℝ := 0

def m_2 : ℝ := 0

def mirror_Seidel : ℝ := 0

def mult2 : ℝ := 0

def ncC3 : ℝ := 0

def ncKP2 : ℝ := 0

def nclocP2 : ℝ := 0

def p_0 : ℝ := 0

def p_1 : ℝ := 0

def phi_1 : ℝ := 0

def phi_2 : ℝ := 0

def quiver2222 : ℝ := 0

def r_1 : ℝ := 0

def r_2 : ℝ := 0

def relation_between_Seidel : ℝ := 0

def s_1 : ℝ := 0

def s_2 : ℝ := 0

def sum_1 : ℝ := 0

def theorem_10 : ℝ := 0

def theorem_110 : ℝ := 0

def theorem_60 : ℝ := 0

def theorem_8 : ℝ := 0

def theorem_9 : ℝ := 0

def theorem_91 : ℝ := 0

def theta : ℝ := 0

def v_0 : ℝ := 0

def v_1 : ℝ := 0

def v_2 : ℝ := 0

def v_3 : ℝ := 0

def val_ncC3 : ℝ := 0

def val_ncKP2 : ℝ := 0

def w_1 : ℝ := 0

def w_1x_1 : ℝ := 0

def w_2 : ℝ := 0

def w_3 : ℝ := 0

def w_3x_3 : ℝ := 0

def w_3x_3z_3 : ℝ := 0

def w_3z_3 : ℝ := 0

def x_0 : ℝ := 0

def x_0X_1 : ℝ := 0

def x_1 : ℝ := 0

def x_1y_1 : ℝ := 0

def x_1y_1w_1 : ℝ := 0

def x_3 : ℝ := 0

def x_3w_3z_3 : ℝ := 0

def y_1 : ℝ := 0

def y_1x_1 : ℝ := 0

def y_1x_1w_1 : ℝ := 0

def y_2 : ℝ := 0

def y_2z_2 : ℝ := 0

def z_2 : ℝ := 0

def z_3 : ℝ := 0

def cA_3 : ℝ := 0

def cA_i : ℝ := 0

def cA_l : ℝ := 0

def nat_trans_X_A : ℝ := 0

def nc_deformed_K_P2 : ℝ := 0

def b0 : ℝ := 0

def hX_l : ℝ := 0

def u_1 : ℝ := 0

def u_6 : ℝ := 0

def u_4 : ℝ := 0

def mu_p_q_i0_i_p : ℝ := 0

def i_0i : ℝ := 0

def a1 : ℝ := 0

def a2 : ℝ := 0

def a3 : ℝ := 0

def b1 : ℝ := 0

def b2 : ℝ := 0

def b3 : ℝ := 0

def c3 : ℝ := 0

def a_1b : ℝ := 0

def hC_i : ℝ := 0

def hC_j : ℝ := 0

def c_010 : ℝ := 0

def c_020 : ℝ := 0

def c_030 : ℝ := 0

def h_G_01 : ℝ := 0

def h_G_02 : ℝ := 0

def h_G_03 : ℝ := 0

def h_G_10 : ℝ := 0

def h_G_20 : ℝ := 0

def h_G_30 : ℝ := 0

def c_i_0_i_k_i_l : ℝ := 0

def h1 : ℝ := 0

def h2 : ℝ := 0

def phi_I : ℝ := 0

def psi_I' : ℝ := 0

def a_ij_in_Ai : ℝ := 0

def a_jk_in_Aj : ℝ := 0

def b_ik_in_Ak : ℝ := 0

def b_jk_in_Aj : ℝ := 0

def i1 : ℝ := 0

def mbar_A_infty_equation : ℝ := 0

def hCF_L : ℝ := 0

def exists_nat_A_infty_transformation : ℝ := 0

def h3 : ℝ := 0

def hat_m_1_b1_b2_alpha : ℝ := 0

def hat_m_1_b2_b1_beta : ℝ := 0

def hat_m_2_b2_b1_b2_betaalpha : ℝ := 0

def hat_m_1_b1_b2_ : ℝ := 0

def hat_m_1_b2_b1_ : ℝ := 0

def hat_m_2_b2_b1_b2_ : ℝ := 0

def h_G : ℝ := 0

def m_k_X_b_i0_ : ℝ := 0

def i2 : ℝ := 0

def i3 : ℝ := 0

def alpha0 : ℝ := 0

def defines_twisted_complex_over_X : ℝ := 0

def quiver_algebra_A_infty_operations_coefficients_in_ahbar_nonnegative : ℝ := 0

def sheaf_lemma_A0_Ai_hbar : ℝ := 0

def h_mirror_exists_algebroid_stack_1 : ℝ := 0

def h_sheaves_representations_satisfying_cocycle_2 : ℝ := 0

def hW1 : ℝ := 0

def hW2 : ℝ := 0

def hW3 : ℝ := 0

def hX1 : ℝ := 0

def hX2 : ℝ := 0

def hX3 : ℝ := 0

def hY1 : ℝ := 0

def hY2 : ℝ := 0

def hY3 : ℝ := 0

def hZ2 : ℝ := 0

def hZ3 : ℝ := 0

def v_w1 : ℝ := 0

def v_w2 : ℝ := 0

def v_w3 : ℝ := 0

def v_x1 : ℝ := 0

def v_x2 : ℝ := 0

def v_x3 : ℝ := 0

def v_y1 : ℝ := 0

def v_y2 : ℝ := 0

def v_y3 : ℝ := 0

def v_z2 : ℝ := 0

def v_z3 : ℝ := 0

def alpha1 : ℝ := 0

def alpha2 : ℝ := 0

def alpha3 : ℝ := 0

def beta1 : ℝ := 0

def beta2 : ℝ := 0

def beta3 : ℝ := 0

def a_i_0_1 : ℝ := 0

def a_ij_1_0 : ℝ := 0

def a_ijk_2_neg1 : ℝ := 0

def a_ik_1_0 : ℝ := 0

def a_jk_1_0 : ℝ := 0

def a_k_0_1 : ℝ := 0

def c_iik_1_0 : ℝ := 0

def c_ijk_1_0 : ℝ := 0

def c_ikk_1_0 : ℝ := 0

def nc_deformation_space_S1 : ℝ := 0

def w1 : ℝ := 0

def x1 : ℝ := 0

def y1 : ℝ := 0

def assumption_slot_1_anchor_missing : ℝ := 0

def assumption_slot_2_anchor_missing : ℝ := 0

def hF2 : ℝ := 0

def hF3 : ℝ := 0

-- Local lemmas / theorem-like facts.
-- Explicit axioms / unresolved paper assumptions.

end Paper_2206_03028

export Paper_2206_03028 (infty C a a_0 a_0b_0 a_1 a_1a_3 a_1a_3a_2 a_1b_1 a_1b_3 a_1b_3c_2 a_1c a_1c_3b_2 a_2 a_2b_1 a_3 a_3b_2 alpha_1 alpha_2 alpha_3 b_0 b_1 b_1a b_1a_1 b_1a_3 b_1a_3c_2 b_1b_3 b_1b_3b_2 b_1b_3b_3 b_1c_1 b_1c_3 b_1c_3a_2 b_2 b_2a_1 b_3 b_3a_2 b_3b_3 b_iB_i beta_0 beta_1 beta_2 beta_3 c1 c2 cA_1 cA_2 cA_k cL_i c_1 c_1a c_1a_1 c_1a_3a_2 c_1a_3b_2 c_1b c_1b_3 c_1b_3a_2 c_1c_3 c_1c_3c_2 c_2 c_3 c_iC_i construction_80 e_1 e_2 e_2a_1 e_2b_1 e_2b_1b_3b_2 e_2c_1 e_3 example_101 example_94 f_1 f_1' f_1'' f_1'a_1 f_2 f_3 fig_S1 g2 g_1 g_1' g_1'' g_1'a_1 g_2 g_2c_1 g_3 g_3c_3 gluing_ncKP2 higher0 homotopy_KP2 i0 i_0 i_0i_ i_0i_1 i_0i_1i_2 i_0i_k i_0i_n i_0i_p i_0i_pi_q i_0i_pi_r i_0i_q i_0i_qi_r i_1 i_2 i_3 isomorphism_KP2 j0 k_1 k_2 l_0 l_1 lemma_112 lemma_26 lemma_33 lemma_40 lemma_48 lemma_81 m_0 m_1 m_2 mirror_Seidel mult2 ncC3 ncKP2 nclocP2 p_0 p_1 phi_1 phi_2 quiver2222 r_1 r_2 relation_between_Seidel s_1 s_2 sum_1 theorem_10 theorem_110 theorem_60 theorem_8 theorem_9 theorem_91 theta v_0 v_1 v_2 v_3 val_ncC3 val_ncKP2 w_1 w_1x_1 w_2 w_3 w_3x_3 w_3x_3z_3 w_3z_3 x_0 x_0X_1 x_1 x_1y_1 x_1y_1w_1 x_3 x_3w_3z_3 y_1 y_1x_1 y_1x_1w_1 y_2 y_2z_2 z_2 z_3 cA_3 cA_i cA_l nat_trans_X_A nc_deformed_K_P2 b0 hX_l u_1 u_6 u_4 mu_p_q_i0_i_p i_0i a1 a2 a3 b1 b2 b3 c3 a_1b hC_i hC_j c_010 c_020 c_030 h_G_01 h_G_02 h_G_03 h_G_10 h_G_20 h_G_30 c_i_0_i_k_i_l h1 h2 phi_I psi_I' a_ij_in_Ai a_jk_in_Aj b_ik_in_Ak b_jk_in_Aj i1 mbar_A_infty_equation hCF_L exists_nat_A_infty_transformation h3 hat_m_1_b1_b2_alpha hat_m_1_b2_b1_beta hat_m_2_b2_b1_b2_betaalpha hat_m_1_b1_b2_ hat_m_1_b2_b1_ hat_m_2_b2_b1_b2_ h_G m_k_X_b_i0_ i2 i3 alpha0 defines_twisted_complex_over_X quiver_algebra_A_infty_operations_coefficients_in_ahbar_nonnegative sheaf_lemma_A0_Ai_hbar h_mirror_exists_algebroid_stack_1 h_sheaves_representations_satisfying_cocycle_2 hW1 hW2 hW3 hX1 hX2 hX3 hY1 hY2 hY3 hZ2 hZ3 v_w1 v_w2 v_w3 v_x1 v_x2 v_x3 v_y1 v_y2 v_y3 v_z2 v_z3 alpha1 alpha2 alpha3 beta1 beta2 beta3 a_i_0_1 a_ij_1_0 a_ijk_2_neg1 a_ik_1_0 a_jk_1_0 a_k_0_1 c_iik_1_0 c_ijk_1_0 c_ikk_1_0 nc_deformation_space_S1 w1 x1 y1 assumption_slot_1_anchor_missing assumption_slot_2_anchor_missing hF2 hF3)
