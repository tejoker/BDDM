-- Auto-generated paper theory module
-- paper_id: 1701.01421
-- domain: default

import Mathlib
import Aesop

open MeasureTheory ProbabilityTheory Filter Set

namespace Paper_1701_01421

-- note: declared 93 paper-local symbol(s) from inventory
-- note: definition stubs are notation grounding only and are not proof-countable

-- ------------------------------------------------------------------
-- Paper-local definitions and explicit axiom debt
-- Any result depending on this module must be reported as proved
-- modulo any paper-local axioms below, not as unconditional closure.
-- ------------------------------------------------------------------

-- Definition stubs ground paper-local identifiers before proof search.
-- They are transparent Lean definitions for elaboration, not hidden proofs of paper claims.
def infty : ℝ := 0

def C : ℝ := 0

def a : ℝ := 0

def acknowledgements_10 : ℝ := 0

def alpha_1 : ℝ := 0

def alpha_1' : ℝ := 0

def alpha_2 : ℝ := 0

def alpha_2' : ℝ := 0

def c_0 : ℝ := 0

def d_1 : ℝ := 0

def d_N : ℝ := 0

def dual1 : ℝ := 0

def dual2 : ℝ := 0

def genus2 : ℝ := 0

def k_1 : ℝ := 0

def k_N : ℝ := 0

def kappa_1 : ℝ := 0

def kappa_2 : ℝ := 0

def pi_1 : ℝ := 0

def ques_1 : ℝ := 0

def t_1 : ℝ := 0

def tau_D : ℝ := 0

def v_1 : ℝ := 0

def v_2 : ℝ := 0

def w_1 : ℝ := 0

def w_2 : ℝ := 0

def gamma_1 : ℝ := 0

def gamma_2 : ℝ := 0

def hK_surgery : ℝ := 0

def hK_nontrivial : ℝ := 0

def hK_lens_surgery : ℝ := 0

def hK_tunnel_one : ℝ := 0

def u_1 : ℝ := 0

def hK_irreducible : ℝ := 0

def hK_surgery_lens : ℝ := 0

def inst_1 : ℝ := 0

def hAD_pair : ℝ := 0

def hAD_surgery_slope : ℝ := 0

def hM_S3_or_connected_sum : ℝ := 0

def pro_DPEquivalentToAD : ℝ := 0

def hM_S3_or_L : ℝ := 0

def u_2 : ℝ := 0

def c0 : ℝ := 0

def h_c0 : ℝ := 0

def pro_Tao : ℝ := 0

def u_3 : ℝ := 0

def u_7 : ℝ := 0

def u_8 : ℝ := 0

def alpha1 : ℝ := 0

def alpha2 : ℝ := 0

def d_boundaryP : ℝ := 0

def hR_d : ℝ := 0

def hR_d_closed : ℝ := 0

def hR_u : ℝ := 0

def hR_u_R_d : ℝ := 0

def hR_u_closed : ℝ := 0

def hV_complete : ℝ := 0

def hW_complete : ℝ := 0

def h_blocking1 : ℝ := 0

def h_blocking2 : ℝ := 0

def w1 : ℝ := 0

def w2 : ℝ := 0

def h_consider_heegaard_diagram_2 : ℝ := 0

def h_enumerate_wave_respect_intersection_4 : ℝ := 0

def h_heegaard_diagram_standard_heegaard_3 : ℝ := 0

def h_paths_above_1 : ℝ := 0

def pro_Paths : ℝ := 0

def a1 : ℝ := 0

def a2 : ℝ := 0

def b1 : ℝ := 0

def b2 : ℝ := 0

def d_dtR_d : ℝ := 0

def h1 : ℝ := 0

def h2 : ℝ := 0

def h3 : ℝ := 0

def hDelta_rho : ℝ := 0

def hP_l : ℝ := 0

def hP_r : ℝ := 0

def hkappa_1 : ℝ := 0

def u_5 : ℝ := 0

def pro_OneRTwoL : ℝ := 0

def pro_AllPaths : ℝ := 0

def assumption_slot_2_anchor_missing : ℝ := 0

def assumption_slot_3_anchor_missing : ℝ := 0

def assumption_slot_4_anchor_missing : ℝ := 0

def assumption_slot_5_anchor_missing : ℝ := 0

def assumption_slot_6_anchor_missing : ℝ := 0

def assumption_slot_7_anchor_missing : ℝ := 0

def expected_at_least_7_assumption_hypotheses_found_3 : ℝ := 0

def pro_LongPath : ℝ := 0

def delta_takes_one_short_path_in_A_l : ℝ := 0

def delta_takes_one_short_path_in_A_r : ℝ := 0

def pro_OneAndOne : ℝ := 0

-- Local lemmas / theorem-like facts.
-- Explicit axioms / unresolved paper assumptions.

end Paper_1701_01421

export Paper_1701_01421 (infty C a acknowledgements_10 alpha_1 alpha_1' alpha_2 alpha_2' c_0 d_1 d_N dual1 dual2 genus2 k_1 k_N kappa_1 kappa_2 pi_1 ques_1 t_1 tau_D v_1 v_2 w_1 w_2 gamma_1 gamma_2 hK_surgery hK_nontrivial hK_lens_surgery hK_tunnel_one u_1 hK_irreducible hK_surgery_lens inst_1 hAD_pair hAD_surgery_slope hM_S3_or_connected_sum pro_DPEquivalentToAD hM_S3_or_L u_2 c0 h_c0 pro_Tao u_3 u_7 u_8 alpha1 alpha2 d_boundaryP hR_d hR_d_closed hR_u hR_u_R_d hR_u_closed hV_complete hW_complete h_blocking1 h_blocking2 w1 w2 h_consider_heegaard_diagram_2 h_enumerate_wave_respect_intersection_4 h_heegaard_diagram_standard_heegaard_3 h_paths_above_1 pro_Paths a1 a2 b1 b2 d_dtR_d h1 h2 h3 hDelta_rho hP_l hP_r hkappa_1 u_5 pro_OneRTwoL pro_AllPaths assumption_slot_2_anchor_missing assumption_slot_3_anchor_missing assumption_slot_4_anchor_missing assumption_slot_5_anchor_missing assumption_slot_6_anchor_missing assumption_slot_7_anchor_missing expected_at_least_7_assumption_hypotheses_found_3 pro_LongPath delta_takes_one_short_path_in_A_l delta_takes_one_short_path_in_A_r pro_OneAndOne)
