-- Auto-generated paper theory module
-- paper_id: 1911.01982
-- domain: default

import Mathlib
import Aesop

open MeasureTheory ProbabilityTheory Filter Set

namespace Paper_1911_01982

-- note: declared 90 paper-local symbol(s) from inventory
-- note: definition stubs are notation grounding only and are not proof-countable

-- ------------------------------------------------------------------
-- Paper-local definitions and explicit axiom debt
-- Any result depending on this module must be reported as proved
-- modulo any paper-local axioms below, not as unconditional closure.
-- ------------------------------------------------------------------

-- Definition stubs ground paper-local identifiers before proof search.
-- They are transparent Lean definitions for elaboration, not hidden proofs of paper claims.
def HSobolev (_s : ℝ) : Set (ℝ → ℝ) := Set.univ

def C_T : Set (ℝ → ℝ) := Set.univ

def L2Space : Set (ℝ → ℝ) := Set.univ

def infty : ℝ := 0

def StrichartzAssumptionStatement : Prop := True

def C : ℝ := 0

def a : ℝ := 0

def adj2 : ℝ := 0

def allez_continuous_2015 : ℝ := 0

def alpha_1 : ℝ := 0

def alpha_2 : ℝ := 0

def betternewstr2 : ℝ := 0

def defXi2 : ℝ := 0

def differencet0 : ℝ := 0

def expH2 : ℝ := 0

def int_I : ℝ := 0

def it_0 : ℝ := 0

def j_0 : ℝ := 0

def j_1 : ℝ := 0

def lemma_38 : ℝ := 0

def neg1 : ℝ := 0

def neg2 : ℝ := 0

def p_1 : ℝ := 0

def p_2 : ℝ := 0

def p_3 : ℝ := 0

def p_4 : ℝ := 0

def pde74 : ℝ := 0

def prop11 : ℝ := 0

def prop3 : ℝ := 0

def prop33 : ℝ := 0

def q_1 : ℝ := 0

def q_2 : ℝ := 0

def s_0 : ℝ := 0

def s_1 : ℝ := 0

def t_0 : ℝ := 0

def t_1 : ℝ := 0

def u_0 : ℝ := 0

def v_0 : ℝ := 0

def v_1 : ℝ := 0

def v_2 : ℝ := 0

def v_N : ℝ := 0

def w_0 : ℝ := 0

def h_source_1_1 : ℝ := 0

def u0 : ℝ := 0

def h_K : ℝ := 0

def h_smooth_compact_surface_1 : ℝ := 0

def h_source_2_2 : ℝ := 0

def h0 : ℝ := 0

def h1 : ℝ := 0

def h2 : ℝ := 0

def h3 : ℝ := 0

def u_1 : ℝ := 0

def u_2 : ℝ := 0

def u_3 : ℝ := 0

def burq_2004_strichartz_compact_manifold : ℝ := 0

def t0 : ℝ := 0

def t1 : ℝ := 0

def ξ_eps : ℝ := 0

def ξ_ : ℝ := 0

def hss1 : ℝ := 0

def hu0 : ℝ := 0

def s0 : ℝ := 0

def s1 : ℝ := 0

def v0 : ℝ := 0

def w0 : ℝ := 0

def alpha1 : ℝ := 0

def alpha2 : ℝ := 0

def halpha1 : ℝ := 0

def p1 : ℝ := 0

def p2 : ℝ := 0

def hT_size : ℝ := 0

def h4 : ℝ := 0

def h5 : ℝ := 0

def q1 : ℝ := 0

def q2 : ℝ := 0

def hp12 : ℝ := 0

def h_source_3_3 : ℝ := 0

def h_source_4_4 : ℝ := 0

def p3 : ℝ := 0

def p32 : ℝ := 0

def p4 : ℝ := 0

def p42 : ℝ := 0

def CTHEnvelope (_w : ℝ → ℝ) (_T : ℝ) : ℝ := 0

def h_C : ℝ := 0

def h_E : ℝ := 0

def h_Gamma : ℝ := 0

def h_H : ℝ := 0

def strongH_bounds : ℝ := 0

-- Local lemmas / theorem-like facts.
-- Explicit axioms / unresolved paper assumptions.
axiom VolterraOscillation : (ℝ → ℝ) → ℝ → ℝ → ℝ

axiom d_dts : Measure ℝ

-- Aesop tactic registration for paper-local axioms.
attribute [aesop safe apply] VolterraOscillation
attribute [aesop safe apply] d_dts

end Paper_1911_01982

export Paper_1911_01982 (HSobolev C_T L2Space infty StrichartzAssumptionStatement C a adj2 allez_continuous_2015 alpha_1 alpha_2 betternewstr2 defXi2 differencet0 expH2 int_I it_0 j_0 j_1 lemma_38 neg1 neg2 p_1 p_2 p_3 p_4 pde74 prop11 prop3 prop33 q_1 q_2 s_0 s_1 t_0 t_1 u_0 v_0 v_1 v_2 v_N w_0 h_source_1_1 u0 h_K h_smooth_compact_surface_1 h_source_2_2 h0 h1 h2 h3 u_1 u_2 u_3 burq_2004_strichartz_compact_manifold t0 t1 ξ_eps ξ_ hss1 hu0 s0 s1 v0 w0 alpha1 alpha2 halpha1 p1 p2 hT_size h4 h5 q1 q2 hp12 h_source_3_3 h_source_4_4 p3 p32 p4 p42 CTHEnvelope h_C h_E h_Gamma h_H strongH_bounds VolterraOscillation d_dts)
