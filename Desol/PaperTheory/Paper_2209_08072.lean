-- Auto-generated paper theory module
-- paper_id: 2209.08072
-- domain: default

import Mathlib
import Aesop

open MeasureTheory ProbabilityTheory Filter Set

namespace Paper_2209_08072

-- note: declared 173 paper-local symbol(s) from inventory
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

def a_1 : ℝ := 0

def a_2 : ℝ := 0

def a_3 : ℝ := 0

def a_3' : ℝ := 0

def alpha_1 : ℝ := 0

def au0 : ℝ := 0

def au5 : ℝ := 0

def b_0 : ℝ := 0

def b_1 : ℝ := 0

def b_2 : ℝ := 0

def beta_1 : ℝ := 0

def beta_12 : ℝ := 0

def beta_1x_1 : ℝ := 0

def beta_2 : ℝ := 0

def beta_22 : ℝ := 0

def beta_3 : ℝ := 0

def beta_32 : ℝ := 0

def beta_3P : ℝ := 0

def cd1 : ℝ := 0

def cdot2 : ℝ := 0

def chal1 : ℝ := 0

def cj_1 : ℝ := 0

def cj_2 : ℝ := 0

def d22 : ℝ := 0

def ded281 : ℝ := 0

def def11 : ℝ := 0

def defi1 : ℝ := 0

def df31 : ℝ := 0

def dj_2 : ℝ := 0

def dt_1 : ℝ := 0

def dx_1 : ℝ := 0

def dx_2 : ℝ := 0

def dy_1dy_2 : ℝ := 0

def ell_1 : ℝ := 0

def ell_2 : ℝ := 0

def ell_3 : ℝ := 0

def eta_1 : ℝ := 0

def eta_2 : ℝ := 0

def ex21 : ℝ := 0

def ge0 : ℝ := 0

def j4 : ℝ := 0

def j_1 : ℝ := 0

def j_1' : ℝ := 0

def j_2 : ℝ := 0

def j_2d : ℝ := 0

def j_2n : ℝ := 0

def jikim7030 : ℝ := 0

def jq501 : ℝ := 0

def kjh1 : ℝ := 0

def le32 : ℝ := 0

def lem001 : ℝ := 0

def lem002 : ℝ := 0

def lem004 : ℝ := 0

def lem055 : ℝ := 0

def lem82 : ℝ := 0

def lem944 : ℝ := 0

def m_0 : ℝ := 0

def m_1 : ℝ := 0

def m_2 : ℝ := 0

def mt_2 : ℝ := 0

def mu_1 : ℝ := 0

def mu_1q : ℝ := 0

def mu_2 : ℝ := 0

def mu_2q : ℝ := 0

def nj_2 : ℝ := 0

def nonspin0070 : ℝ := 0

def p44 : ℝ := 0

def pol1 : ℝ := 0

def pol2 : ℝ := 0

def prop001 : ℝ := 0

def prop002 : ℝ := 0

def prop003 : ℝ := 0

def prop006 : ℝ := 0

def prop21 : ℝ := 0

def prop812 : ℝ := 0

def prop885 : ℝ := 0

def q2 : ℝ := 0

def qM_j : ℝ := 0

def q_3 : ℝ := 0

def q_32 : ℝ := 0

def qx_1 : ℝ := 0

def sf99 : ℝ := 0

def skc1 : ℝ := 0

def sup_N : ℝ := 0

def t_1 : ℝ := 0

def t_1R_1 : ℝ := 0

def t_1t_2 : ℝ := 0

def t_2 : ℝ := 0

def t_2' : ℝ := 0

def th21 : ℝ := 0

def th22 : ℝ := 0

def variant1 : ℝ := 0

def variant2 : ℝ := 0

def varinat1 : ℝ := 0

def w_1 : ℝ := 0

def w_2 : ℝ := 0

def x_1 : ℝ := 0

def x_2 : ℝ := 0

def x_3 : ℝ := 0

def xi_1 : ℝ := 0

def xi_12 : ℝ := 0

def xi_1t_1 : ℝ := 0

def xi_1x_1 : ℝ := 0

def xi_2 : ℝ := 0

def xi_22 : ℝ := 0

def xi_2t_2 : ℝ := 0

def xi_2x_2 : ℝ := 0

def xi_3 : ℝ := 0

def xi_32 : ℝ := 0

def xi_3P : ℝ := 0

def xi_3R_0 : ℝ := 0

def xi_3R_1 : ℝ := 0

def xi_3R_m : ℝ := 0

def xi_3c_ : ℝ := 0

def xi_3t_1t_2 : ℝ := 0

def y_1 : ℝ := 0

def y_2 : ℝ := 0

def r_1 : ℝ := 0

def assumption_slot_2_anchor_missing : ℝ := 0

def t1 : ℝ := 0

def t2 : ℝ := 0

def ξ_1 : ℝ := 0

def ξ_2 : ℝ := 0

def ξ_3 : ℝ := 0

def h132 : ℝ := 0

def condition_132 : ℝ := 0

def ξ3 : ℝ := 0

def coefficients_of_P_dependent_constant : ℝ := 0

def dual_faces_in_cd1 : ℝ := 0

def hF_in : ℝ := 0

def hF_star : ℝ := 0

def lemma_jq501 : ℝ := 0

def x1 : ℝ := 0

def x2 : ℝ := 0

def hM_j : ℝ := 0

def hM_j_ge : ℝ := 0

def a3 : ℝ := 0

def j1 : ℝ := 0

def ξ1 : ℝ := 0

def ξ2 : ℝ := 0

def a1 : ℝ := 0

def beta1 : ℝ := 0

def beta3 : ℝ := 0

def j2 : ℝ := 0

def mu1 : ℝ := 0

def beta_1x : ℝ := 0

def hP_ge : ℝ := 0

def hP_le : ℝ := 0

def hq1 : ℝ := 0

def hq2 : ℝ := 0

def paper_2209_08072_lemma_4013 : ℝ := 0

def hm0 : ℝ := 0

def m0 : ℝ := 0

def hP_deg : ℝ := 0

def deg_R1 : ℝ := 0

def psi_variant1 : ℝ := 0

def psi_variant2 : ℝ := 0

def deg_R : ℝ := 0

def a2 : ℝ := 0

def beta2 : ℝ := 0

def e1 : ℝ := 0

def e2 : ℝ := 0

def hbeta1 : ℝ := 0

def hbeta2 : ℝ := 0

def hbeta3 : ℝ := 0

def hj1 : ℝ := 0

def hpsi1 : ℝ := 0

def hpsi2 : ℝ := 0

def ht2 : ℝ := 0

-- Local lemmas / theorem-like facts.
-- Explicit axioms / unresolved paper assumptions.
axiom VolterraOscillation : (ℝ → ℝ) → ℝ → ℝ → ℝ

-- Aesop tactic registration for paper-local axioms.
attribute [aesop safe apply] VolterraOscillation

end Paper_2209_08072

export Paper_2209_08072 (infty C a a_1 a_2 a_3 a_3' alpha_1 au0 au5 b_0 b_1 b_2 beta_1 beta_12 beta_1x_1 beta_2 beta_22 beta_3 beta_32 beta_3P cd1 cdot2 chal1 cj_1 cj_2 d22 ded281 def11 defi1 df31 dj_2 dt_1 dx_1 dx_2 dy_1dy_2 ell_1 ell_2 ell_3 eta_1 eta_2 ex21 ge0 j4 j_1 j_1' j_2 j_2d j_2n jikim7030 jq501 kjh1 le32 lem001 lem002 lem004 lem055 lem82 lem944 m_0 m_1 m_2 mt_2 mu_1 mu_1q mu_2 mu_2q nj_2 nonspin0070 p44 pol1 pol2 prop001 prop002 prop003 prop006 prop21 prop812 prop885 q2 qM_j q_3 q_32 qx_1 sf99 skc1 sup_N t_1 t_1R_1 t_1t_2 t_2 t_2' th21 th22 variant1 variant2 varinat1 w_1 w_2 x_1 x_2 x_3 xi_1 xi_12 xi_1t_1 xi_1x_1 xi_2 xi_22 xi_2t_2 xi_2x_2 xi_3 xi_32 xi_3P xi_3R_0 xi_3R_1 xi_3R_m xi_3c_ xi_3t_1t_2 y_1 y_2 r_1 assumption_slot_2_anchor_missing t1 t2 ξ_1 ξ_2 ξ_3 h132 condition_132 ξ3 coefficients_of_P_dependent_constant dual_faces_in_cd1 hF_in hF_star lemma_jq501 x1 x2 hM_j hM_j_ge a3 j1 ξ1 ξ2 a1 beta1 beta3 j2 mu1 beta_1x hP_ge hP_le hq1 hq2 paper_2209_08072_lemma_4013 hm0 m0 hP_deg deg_R1 psi_variant1 psi_variant2 deg_R a2 beta2 e1 e2 hbeta1 hbeta2 hbeta3 hj1 hpsi1 hpsi2 ht2 VolterraOscillation)
