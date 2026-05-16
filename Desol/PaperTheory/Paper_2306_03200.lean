-- Auto-generated paper theory module
-- paper_id: 2306.03200
-- domain: default

import Mathlib
import Aesop

open MeasureTheory ProbabilityTheory Filter Set

namespace Paper_2306_03200

-- note: declared 110 paper-local symbol(s) from inventory
-- note: definition stubs are notation grounding only and are not proof-countable

-- ------------------------------------------------------------------
-- Paper-local definitions and explicit axiom debt
-- Any result depending on this module must be reported as proved
-- modulo any paper-local axioms below, not as unconditional closure.
-- ------------------------------------------------------------------

-- Definition stubs ground paper-local identifiers before proof search.
-- They are transparent Lean definitions for elaboration, not hidden proofs of paper claims.
noncomputable def MRES_root_gerbe (star : Bool) (g : ℕ) (Brn : Type*) [MetricSpace Brn] (hg : Even g) (n : ℕ) (hstar : star = true) : n = 2 := sorry

def L2Space : Set (ℝ → ℝ) := Set.univ

def omega (_i _k : ℕ) : ℝ := 0

def infty : ℝ := 0

def C : ℝ := 0

def a : ℝ := 0

def baselineskip0 : ℝ := 0

def c_1 : ℝ := 0

def cdotE_4 : ℝ := 0

def cong0 : ℝ := 0

def conj_67 : ℝ := 0

def d2 : ℝ := 0

def e4 : ℝ := 0

def e8 : ℝ := 0

def eis_2 : ℝ := 0

def eis_4 : ℝ := 0

def eis_6 : ℝ := 0

def f_L : ℝ := 0

def frac12 : ℝ := 0

def frac14 : ℝ := 0

def frac32 : ℝ := 0

def g2 : ℝ := 0

def g4 : ℝ := 0

def ge0 : ℝ := 0

def ge1 : ℝ := 0

def ge12 : ℝ := 0

def kE_k : ℝ := 0

def mu_2 : ℝ := 0

def n1 : ℝ := 0

def n2 : ℝ := 0

def n3 : ℝ := 0

def n4 : ℝ := 0

def n5 : ℝ := 0

def n6 : ℝ := 0

def n7 : ℝ := 0

def omega_R : ℝ := 0

def omega_T : ℝ := 0

def omega_Y : ℝ := 0

def p_1 : ℝ := 0

def p_2 : ℝ := 0

def pi_J : ℝ := 0

def pi_T : ℝ := 0

def positivity_1 : ℝ := 0

def positivity_2 : ℝ := 0

def psi_L : ℝ := 0

def que_3 : ℝ := 0

def s_1 : ℝ := 0

def s_1' : ℝ := 0

def s_2 : ℝ := 0

def s_2' : ℝ := 0

def sigma_1 : ℝ := 0

def sigma_3 : ℝ := 0

def sigma_5 : ℝ := 0

def sum_L : ℝ := 0

def t_0 : ℝ := 0

def t_1 : ℝ := 0

def t_2 : ℝ := 0

def tfrac12 : ℝ := 0

def tfrac32 : ℝ := 0

def theta : ℝ := 0

def to0 : ℝ := 0

def u_1 : ℝ := 0

def u_2 : ℝ := 0

def utf8 : ℝ := 0

def genus_and_reducibility_of_Severi_variety : ℝ := 0

def genus_V_R_L : ℝ := 0

def hE8 : ℝ := 0

def hB_g : ℝ := 0

def hB_bisec : ℝ := 0

def hB_genus : ℝ := 0

def hB_height : ℝ := 0

def h_isolated_singularities_1 : ℝ := 0

def h_local_analytic_equations_2 : ℝ := 0

def hPi_norm : ℝ := 0

def h_B_Wei : ℝ := 0

def h_L : ℝ := 0

def h_Pi : ℝ := 0

def hB_irr : ℝ := 0

def hB_nodes : ℝ := 0

def hB_sing : ℝ := 0

def p_has_A_d_singularity_in_ : ℝ := 0

def Γ_is_reduced : ℝ := 0

def inst_1 : ℝ := 0

def h_irreducible_rational_bisection_1 : ℝ := 0

def h_source_2_2 : ℝ := 0

def omega_X : ℝ := 0

def j_II : ℝ := 0

def j_II_ : ℝ := 0

def n0 : ℝ := 0

def tT_reg : ℝ := 0

def paper_2306_03200_prop_overline_M_v_G_smooth_submanifold : ℝ := 0

def Γ_p : ℝ := 0

def k1 : ℝ := 0

def hM_v_G : ℝ := 0

def h_Tilde_T : ℝ := 0

def h_j_II : ℝ := 0

def h_intersection_transverse_3 : ℝ := 0

def h_simple_telltale_cycle_1 : ℝ := 0

def h_I : ℝ := 0

def g_L : ℝ := 0

def theta_q2 : ℝ := 0

def s1 : ℝ := 0

def s2 : ℝ := 0

def h_s1_eq_s2 : ℝ := 0

def h_s1_ne_s2 : ℝ := 0

def sigma1 : ℝ := 0

def coeff_of_q_pow_g_plus_2_in_series : ℝ := 0

def coeff_of_q_pow_g_plus_2_minus_2m_in_series : ℝ := 0

def h0 : ℝ := 0

def h1 : ℝ := 0

def h2 : ℝ := 0

-- Local lemmas / theorem-like facts.
-- Explicit axioms / unresolved paper assumptions.


-- Auto-stubbed paper-local symbols (paper_theory_symbol_stubber.py)
-- Each stub is real formalization debt: axioms / sorry-bodied
-- defs. The integrity audit's trivialization detector is the
-- final arbiter on any subsequent closure.
-- `import` is unknown and its usage couldn't be classified; emitting `axiom import : Prop`. Real formalization debt; audit's trivialization detector is the final arbiter.
axiom import : Prop
attribute [aesop safe apply] import

end Paper_2306_03200

export Paper_2306_03200 (L2Space omega infty C a baselineskip0 c_1 cdotE_4 cong0 conj_67 d2 e4 e8 eis_2 eis_4 eis_6 f_L frac12 frac14 frac32 g2 g4 ge0 ge1 ge12 kE_k mu_2 n1 n2 n3 n4 n5 n6 n7 omega_R omega_T omega_Y p_1 p_2 pi_J pi_T positivity_1 positivity_2 psi_L que_3 s_1 s_1' s_2 s_2' sigma_1 sigma_3 sigma_5 sum_L t_0 t_1 t_2 tfrac12 tfrac32 theta to0 u_1 u_2 utf8 genus_and_reducibility_of_Severi_variety genus_V_R_L hE8 hB_g hB_bisec hB_genus hB_height h_isolated_singularities_1 h_local_analytic_equations_2 hPi_norm h_B_Wei h_L h_Pi hB_irr hB_nodes hB_sing p_has_A_d_singularity_in_ Γ_is_reduced inst_1 h_irreducible_rational_bisection_1 h_source_2_2 omega_X j_II j_II_ n0 tT_reg paper_2306_03200_prop_overline_M_v_G_smooth_submanifold Γ_p k1 hM_v_G h_Tilde_T h_j_II h_intersection_transverse_3 h_simple_telltale_cycle_1 h_I g_L theta_q2 s1 s2 h_s1_eq_s2 h_s1_ne_s2 sigma1 coeff_of_q_pow_g_plus_2_in_series coeff_of_q_pow_g_plus_2_minus_2m_in_series h0 h1 h2)
