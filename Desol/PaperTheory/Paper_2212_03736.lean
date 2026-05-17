-- Auto-generated paper theory module
-- paper_id: 2212.03736
-- domain: default

import Mathlib
import Aesop

open MeasureTheory ProbabilityTheory Filter Set

namespace Paper_2212_03736

-- note: declared 115 paper-local symbol(s) from inventory
-- note: definition stubs are notation grounding only and are not proof-countable

-- ------------------------------------------------------------------
-- Paper-local definitions and explicit axiom debt
-- Any result depending on this module must be reported as proved
-- modulo any paper-local axioms below, not as unconditional closure.
-- ------------------------------------------------------------------

-- Definition stubs ground paper-local identifiers before proof search.
-- They are transparent Lean definitions for elaboration, not hidden proofs of paper claims.
-- Was: `noncomputable def var_circ (x : ℝ) : ∃ x : ℝ, B ≥ 0 ∧ operatornamedim Z = 1 := by`
-- Translator emitted malformed signature (empty tactic body + unbound B, operatornamedim, Z).
-- Replaced with a transparent stub matching the other paper-local symbols
-- in this file. The audit's alignment registry can discharge it as ∀ x, var_circ x = 0.
noncomputable def var_circ (_x : ℝ) : ℝ := 0

def omega (_i _k : ℕ) : ℝ := 0

def infty : ℝ := 0

def C : ℝ := 0

def a : ℝ := 0

def bm16 : ℝ := 0

def c_0 : ℝ := 0

def c_1 : ℝ := 0

def cn1 : ℝ := 0

def corollary_36 : ℝ := 0

def def0 : ℝ := 0

def defE_0 : ℝ := 0

def defE_2 : ℝ := 0

def defE_21 : ℝ := 0

def defE_22 : ℝ := 0

def defK_X : ℝ := 0

def e1 : ℝ := 0

def e2 : ℝ := 0

def f_0 : ℝ := 0

def kE_m : ℝ := 0

def kl_0 : ℝ := 0

def km_0 : ℝ := 0

def l_0 : ℝ := 0

def lomitlist30 : ℝ := 0

def mM_0 : ℝ := 0

def mM_X : ℝ := 0

def m_0 : ℝ := 0

def m_1m_2 : ℝ := 0

def n1 : ℝ := 0

def nu_1 : ℝ := 0

def nu_2 : ℝ := 0

def pi_1 : ℝ := 0

def ps09 : ℝ := 0

def s_1 : ℝ := 0

def s_2 : ℝ := 0

def theorem_2 : ℝ := 0

def times_U : ℝ := 0

def times_WZ : ℝ := 0

def times_Y : ℝ := 0

def times_YU : ℝ := 0

def times_Z : ℝ := 0

def times_ZW : ℝ := 0

def times_ZZ' : ℝ := 0

def vert_D : ℝ := 0

def vert_Y : ℝ := 0

def x_1 : ℝ := 0

def h1 : ℝ := 0

def h2 : ℝ := 0

def h3 : ℝ := 0

def h4 : ℝ := 0

def h5 : ℝ := 0

def h_canonical_3 : ℝ := 0

def h_contraction_1 : ℝ := 0

def h_every_canonical_centre_dominates_4 : ℝ := 0

def h_general_fibre_good_minimal_5 : ℝ := 0

def h_source_2_2 : ℝ := 0

def h_general_fibre_admits_good_4 : ℝ := 0

def h_satisfies_property_3 : ℝ := 0

def c1 : ℝ := 0

def u_1 : ℝ := 0

def u_2 : ℝ := 0

def conn_O1 : ℝ := 0

def inst_1 : ℝ := 0

def inst_3 : ℝ := 0

def inst_4 : ℝ := 0

def inst_5 : ℝ := 0

def hE_conn : ℝ := 0

def u_3 : ℝ := 0

def hF_rank : ℝ := 0

def hP_z : ℝ := 0

def hP_z_trans : ℝ := 0

def hY_z : ℝ := 0

def bM_Y : ℝ := 0

def hE_m : ℝ := 0

def hK_XZ_B : ℝ := 0

def mu_max_E_m : ℝ := 0

def mu_min_E_m : ℝ := 0

def hE_m_locally_free : ℝ := 0

def hE_m_num_flat : ℝ := 0

def hB_vert : ℝ := 0

def h_addition_locally_stable_family_5 : ℝ := 0

def h_exists_diagram_center_tikzcd_2 : ℝ := 0

def h_prop_equi_cover_equidimensional_1 : ℝ := 0

def h_semi_ample_enumerate_natural_3 : ℝ := 0

def h_sufficiently_divisible_locally_free_4 : ℝ := 0

def h_equidimensional_4 : ℝ := 0

def h_property_2 : ℝ := 0

def h_reduced_divisor_reduced_5 : ℝ := 0

def h_source_3_3 : ℝ := 0

def h_canonical_pair_1 : ℝ := 0

def h_fibration_2 : ℝ := 0

def hZ_proj : ℝ := 0

def h_fibration_which_also_locally_1 : ℝ := 0

def h_general_fibre_good_minimal_4 : ℝ := 0

def h_quasi_projective_normal_2 : ℝ := 0

def h_semi_ampleness_conjecture_5 : ℝ := 0

def h10 : ℝ := 0

def h11 : ℝ := 0

def h12 : ℝ := 0

def h13 : ℝ := 0

def h14 : ℝ := 0

def h15 : ℝ := 0

def h16 : ℝ := 0

def h6 : ℝ := 0

def h7 : ℝ := 0

def h8 : ℝ := 0

def h9 : ℝ := 0

def delta_Y : ℝ := 0

def m0 : ℝ := 0

def hK_nef : ℝ := 0

def b_semi_ampleness_conjecture_holds_for_LC_trivial_fibrations_of_relative_dimension_at_most_n_minus_1 : ℝ := 0

def crepant_birational_over_generic_point_of_Z : ℝ := 0

def h_K_X_plus_B : ℝ := 0

def hY_plus : ℝ := 0

def delta0 : ℝ := 0

def hdelta0 : ℝ := 0

-- Local lemmas / theorem-like facts.
-- Explicit axioms / unresolved paper assumptions.

end Paper_2212_03736

export Paper_2212_03736 (omega infty C a bm16 c_0 c_1 cn1 corollary_36 def0 defE_0 defE_2 defE_21 defE_22 defK_X e1 e2 f_0 kE_m kl_0 km_0 l_0 lomitlist30 mM_0 mM_X m_0 m_1m_2 n1 nu_1 nu_2 pi_1 ps09 s_1 s_2 theorem_2 times_U times_WZ times_Y times_YU times_Z times_ZW times_ZZ' vert_D vert_Y x_1 h1 h2 h3 h4 h5 h_canonical_3 h_contraction_1 h_every_canonical_centre_dominates_4 h_general_fibre_good_minimal_5 h_source_2_2 h_general_fibre_admits_good_4 h_satisfies_property_3 c1 u_1 u_2 conn_O1 inst_1 inst_3 inst_4 inst_5 hE_conn u_3 hF_rank hP_z hP_z_trans hY_z bM_Y hE_m hK_XZ_B mu_max_E_m mu_min_E_m hE_m_locally_free hE_m_num_flat hB_vert h_addition_locally_stable_family_5 h_exists_diagram_center_tikzcd_2 h_prop_equi_cover_equidimensional_1 h_semi_ample_enumerate_natural_3 h_sufficiently_divisible_locally_free_4 h_equidimensional_4 h_property_2 h_reduced_divisor_reduced_5 h_source_3_3 h_canonical_pair_1 h_fibration_2 hZ_proj h_fibration_which_also_locally_1 h_general_fibre_good_minimal_4 h_quasi_projective_normal_2 h_semi_ampleness_conjecture_5 h10 h11 h12 h13 h14 h15 h16 h6 h7 h8 h9 delta_Y m0 hK_nef b_semi_ampleness_conjecture_holds_for_LC_trivial_fibrations_of_relative_dimension_at_most_n_minus_1 crepant_birational_over_generic_point_of_Z h_K_X_plus_B hY_plus delta0 hdelta0)
