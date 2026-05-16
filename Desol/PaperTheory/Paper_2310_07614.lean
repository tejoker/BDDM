-- Auto-generated paper theory module
-- paper_id: 2310.07614
-- domain: default

import Mathlib
import Aesop

open MeasureTheory ProbabilityTheory Filter Set

namespace Paper_2310_07614

-- note: declared 202 paper-local symbol(s) from inventory
-- note: definition stubs are notation grounding only and are not proof-countable

-- ------------------------------------------------------------------
-- Paper-local definitions and explicit axiom debt
-- Any result depending on this module must be reported as proved
-- modulo any paper-local axioms below, not as unconditional closure.
-- ------------------------------------------------------------------

-- Definition stubs ground paper-local identifiers before proof search.
-- They are transparent Lean definitions for elaboration, not hidden proofs of paper claims.
noncomputable def defi_80 :
  llangle arrangle_G = G := by

def omega (_i _k : ℕ) : ℝ := 0

def infty : ℝ := 0

def C : ℝ := 0

def a : ℝ := 0

def a0 : ℝ := 0

def a_0 : ℝ := 0

def a_0' : ℝ := 0

def a_0c : ℝ := 0

def a_0c_1 : ℝ := 0

def a_0c_1a'_0c'_1 : ℝ := 0

def a_0c_1a'_0c'_1w_0 : ℝ := 0

def a_1 : ℝ := 0

def a_1' : ℝ := 0

def a_1b : ℝ := 0

def a_2 : ℝ := 0

def a_2b : ℝ := 0

def aleph_0 : ℝ := 0

def aleph_1 : ℝ := 0

def alpha_0 : ℝ := 0

def alphaan1 : ℝ := 0

def alphacn1 : ℝ := 0

def an1 : ℝ := 0

def b_0 : ℝ := 0

def b_0c : ℝ := 0

def b_1 : ℝ := 0

def b_2 : ℝ := 0

def beta_0 : ℝ := 0

def beta_1 : ℝ := 0

def beta_1' : ℝ := 0

def beta_2 : ℝ := 0

def beta_3 : ℝ := 0

def beta_4 : ℝ := 0

def beta_8 : ℝ := 0

def betaa0 : ℝ := 0

def betaalphaan1 : ℝ := 0

def betaan1 : ℝ := 0

def box0 : ℝ := 0

def c_0 : ℝ := 0

def c_0c_1 : ℝ := 0

def c_1 : ℝ := 0

def c_1' : ℝ := 0

def c_1a_ : ℝ := 0

def c_2 : ℝ := 0

def cn1 : ℝ := 0

def conj_93 : ℝ := 0

def copy2 : ℝ := 0

def copy4 : ℝ := 0

def coro_47 : ℝ := 0

def coro_49 : ℝ := 0

def coro_54 : ℝ := 0

def d_1 : ℝ := 0

def d_1d_2 : ℝ := 0

def d_2 : ℝ := 0

def ejem_46 : ℝ := 0

def ejem_52 : ℝ := 0

def ejem_77 : ℝ := 0

def epsilon_1 : ℝ := 0

def epsilon_8 : ℝ := 0

def equiv_C : ℝ := 0

def equiv_Ca' : ℝ := 0

def gamma_0 : ℝ := 0

def gamma_A : ℝ := 0

def gamma_B : ℝ := 0

def gamma_C : ℝ := 0

def gamma_Ca : ℝ := 0

def gamma_Ca' : ℝ := 0

def gamma_X : ℝ := 0

def h_0 : ℝ := 0

def ht0 : ℝ := 0

def ht4 : ℝ := 0

def id_A : ℝ := 0

def kern0 : ℝ := 0

def kern1 : ℝ := 0

def lang_0 : ℝ := 0

def lang_1 : ℝ := 0

def lang_2 : ℝ := 0

def lower0 : ℝ := 0

def lower1 : ℝ := 0

def n_0 : ℝ := 0

def pi_1 : ℝ := 0

def pi_2 : ℝ := 0

def qftp_0 : ℝ := 0

def qftp_1 : ℝ := 0

def qftp_2 : ℝ := 0

def quest_94 : ℝ := 0

def raise1 : ℝ := 0

def rangle_1 : ℝ := 0

def rrangle_G : ℝ := 0

def setbox0 : ℝ := 0

def setbox2 : ℝ := 0

def setbox4 : ℝ := 0

def setbox6 : ℝ := 0

def setbox8 : ℝ := 0

def sigma_1 : ℝ := 0

def sigma_8 : ℝ := 0

def utf8 : ℝ := 0

def uv_0 : ℝ := 0

def v_0 : ℝ := 0

def v_0u' : ℝ := 0

def w_0 : ℝ := 0

def wd0 : ℝ := 0

def wd4 : ℝ := 0

def wd6 : ℝ := 0

def wd8 : ℝ := 0

def x_0 : ℝ := 0

def y_0 : ℝ := 0

def h_meet_tree_1 : ℝ := 0

def h_semibranch_which_branch_2 : ℝ := 0

def h_source_3_3 : ℝ := 0

def hT_infty : ℝ := 0

def Γ' : ℝ := 0

def Γ1 : ℝ := 0

def Γ2 : ℝ := 0

def gamma0 : ℝ := 0

def u_1 : ℝ := 0

def h1 : ℝ := 0

def h2 : ℝ := 0

def h_semibranched_meet_tree_1 : ℝ := 0

def h_substructure_semibranched_meet_tree_2 : ℝ := 0

def Γ_1 : ℝ := 0

def Γ_2 : ℝ := 0

def h_branch_2 : ℝ := 0

def h_subsets_3 : ℝ := 0

def h_substructure_semibranched_meet_trees_4 : ℝ := 0

def h_finite_character_only_every_3 : ℝ := 0

def h_full_existence_embeddings_semibranched_4 : ℝ := 0

def h_satisfies_following_properties_quantifier_2 : ℝ := 0

def h_semibranch_independence_semibranched_meet_1 : ℝ := 0

def u_2 : ℝ := 0

def h_K_1 : ℝ := 0

def h_K_2 : ℝ := 0

def h_M_1 : ℝ := 0

def h_M_2 : ℝ := 0

def a1 : ℝ := 0

def a2 : ℝ := 0

def hB_fg : ℝ := 0

def hB_sub : ℝ := 0

def hM_generic : ℝ := 0

def qftp0 : ℝ := 0

def hIG1 : ℝ := 0

def hIG2 : ℝ := 0

def hS0 : ℝ := 0

def hS1 : ℝ := 0

def hS2 : ℝ := 0

def indepe_1 : ℝ := 0

def indepe_2 : ℝ := 0

def indepe_1_indep_gen : ℝ := 0

def indepe_1_stationary : ℝ := 0

def indepe_2_indep_gen : ℝ := 0

def indepe_2_stationary : ℝ := 0

def indepe_stationary_lang_0 : ℝ := 0

def lang_R : ℝ := 0

def b1 : ℝ := 0

def b2 : ℝ := 0

def h3 : ℝ := 0

def h4 : ℝ := 0

def h5 : ℝ := 0

def h6 : ℝ := 0

def h7 : ℝ := 0

def h10 : ℝ := 0

def h8 : ℝ := 0

def h9 : ℝ := 0

def Γ_C : ℝ := 0

def Γ_Ca : ℝ := 0

def Γ_D : ℝ := 0

def Γ_Da : ℝ := 0

def h_automorphism_fixing_2 : ℝ := 0

def h_cones_above_which_moves_3 : ℝ := 0

def h_point_1 : ℝ := 0

def Γ_branch : ℝ := 0

def hC0 : ℝ := 0

def hD0 : ℝ := 0

def l_construction_1 : ℝ := 0

def construction_2 : ℝ := 0

def construction_3 : ℝ := 0

def beta1 : ℝ := 0

def beta2 : ℝ := 0

def beta3 : ℝ := 0

def beta4 : ℝ := 0

def halpha_U : ℝ := 0

def a_equiv_Rr : ℝ := 0

def a_indepe_Rr : ℝ := 0

def b_indepe_Rr : ℝ := 0

def u_5 : ℝ := 0

def automorphism_of_TtM : ℝ := 0

def Γ_is_a_branch : ℝ := 0

def a_G : ℝ := 0

def Γ_is_branch : ℝ := 0

def hf1 : ℝ := 0

def hf2 : ℝ := 0

def hf3 : ℝ := 0

def hf4 : ℝ := 0

def hf5 : ℝ := 0

def hf6 : ℝ := 0

def u_3 : ℝ := 0

def u_4 : ℝ := 0

def h_case1 : ℝ := 0

def h_case2 : ℝ := 0

def h_case3 : ℝ := 0

def h_case4 : ℝ := 0

def cone_a_A : ℝ := 0

def a_in_A : ℝ := 0

-- Local lemmas / theorem-like facts.
-- Explicit axioms / unresolved paper assumptions.

end Paper_2310_07614

export Paper_2310_07614 (omega infty C a a0 a_0 a_0' a_0c a_0c_1 a_0c_1a'_0c'_1 a_0c_1a'_0c'_1w_0 a_1 a_1' a_1b a_2 a_2b aleph_0 aleph_1 alpha_0 alphaan1 alphacn1 an1 b_0 b_0c b_1 b_2 beta_0 beta_1 beta_1' beta_2 beta_3 beta_4 beta_8 betaa0 betaalphaan1 betaan1 box0 c_0 c_0c_1 c_1 c_1' c_1a_ c_2 cn1 conj_93 copy2 copy4 coro_47 coro_49 coro_54 d_1 d_1d_2 d_2 ejem_46 ejem_52 ejem_77 epsilon_1 epsilon_8 equiv_C equiv_Ca' gamma_0 gamma_A gamma_B gamma_C gamma_Ca gamma_Ca' gamma_X h_0 ht0 ht4 id_A kern0 kern1 lang_0 lang_1 lang_2 lower0 lower1 n_0 pi_1 pi_2 qftp_0 qftp_1 qftp_2 quest_94 raise1 rangle_1 rrangle_G setbox0 setbox2 setbox4 setbox6 setbox8 sigma_1 sigma_8 utf8 uv_0 v_0 v_0u' w_0 wd0 wd4 wd6 wd8 x_0 y_0 h_meet_tree_1 h_semibranch_which_branch_2 h_source_3_3 hT_infty Γ' Γ1 Γ2 gamma0 u_1 h1 h2 h_semibranched_meet_tree_1 h_substructure_semibranched_meet_tree_2 Γ_1 Γ_2 h_branch_2 h_subsets_3 h_substructure_semibranched_meet_trees_4 h_finite_character_only_every_3 h_full_existence_embeddings_semibranched_4 h_satisfies_following_properties_quantifier_2 h_semibranch_independence_semibranched_meet_1 u_2 h_K_1 h_K_2 h_M_1 h_M_2 a1 a2 hB_fg hB_sub hM_generic qftp0 hIG1 hIG2 hS0 hS1 hS2 indepe_1 indepe_2 indepe_1_indep_gen indepe_1_stationary indepe_2_indep_gen indepe_2_stationary indepe_stationary_lang_0 lang_R b1 b2 h3 h4 h5 h6 h7 h10 h8 h9 Γ_C Γ_Ca Γ_D Γ_Da h_automorphism_fixing_2 h_cones_above_which_moves_3 h_point_1 Γ_branch hC0 hD0 l_construction_1 construction_2 construction_3 beta1 beta2 beta3 beta4 halpha_U a_equiv_Rr a_indepe_Rr b_indepe_Rr u_5 automorphism_of_TtM Γ_is_a_branch a_G Γ_is_branch hf1 hf2 hf3 hf4 hf5 hf6 u_3 u_4 h_case1 h_case2 h_case3 h_case4 cone_a_A a_in_A)
