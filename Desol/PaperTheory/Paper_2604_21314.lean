-- Auto-generated paper theory module
-- paper_id: 2604.21314
-- domain: default

import Mathlib
import Aesop

open MeasureTheory ProbabilityTheory Filter Set

namespace Paper_2604_21314

-- note: declared 50 paper-local symbol(s) from inventory
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

def infty : ℝ := 0

def VolterraEstimateStatement : Prop := True

def C : ℝ := 0

def a : ℝ := 0

def alpha_0 : ℝ := 0

def alpha_F : ℝ := 0

def defn_8 : ℝ := 0

def h0 : ℝ := 0

def h1 : ℝ := 0

def h2 : ℝ := 0

def h3 : ℝ := 0

def h4 : ℝ := 0

def h5 : ℝ := 0

def h6 : ℝ := 0

def hH_eps : ℝ := 0

def hT_eps : ℝ := 0

def hT_max : ℝ := 0

def hfL1 : ℝ := 0

def hu0 : ℝ := 0

def hz1 : ℝ := 0

def hz2 : ℝ := 0

def int_0 : ℝ := 0

def longrightarrow0 : ℝ := 0

def mX_i : ℝ := 0

def mt0 : ℝ := 0

def nabla_H : ℝ := 0

def oza3 : ℝ := 0

def p_F : ℝ := 0

def phi_1 : ℝ := 0

def phi_2 : ℝ := 0

def s10 : ℝ := 0

def s3 : ℝ := 0

def theta : ℝ := 0

def u0 : ℝ := 0

def u_0 : ℝ := 0

def utf8 : ℝ := 0

def v_0 : ℝ := 0

def x_1 : ℝ := 0

def x_N : ℝ := 0

def y_1 : ℝ := 0

def y_N : ℝ := 0

def z1 : ℝ := 0

def z2 : ℝ := 0

def z_1 : ℝ := 0

def z_2 : ℝ := 0

def Γ_gamma : ℝ := 0

def ξ' : ℝ := 0

-- Local lemmas / theorem-like facts.
-- Explicit axioms / unresolved paper assumptions.
axiom d_dts : Measure ℝ
-- Aesop tactic registration for paper-local axioms.
attribute [aesop safe apply] d_dts


end Paper_2604_21314

export Paper_2604_21314 (HSobolev C_T infty VolterraEstimateStatement C a alpha_0 alpha_F defn_8 h0 h1 h2 h3 h4 h5 h6 hH_eps hT_eps hT_max hfL1 hu0 hz1 hz2 int_0 longrightarrow0 mX_i mt0 nabla_H oza3 p_F phi_1 phi_2 s10 s3 theta u0 u_0 utf8 v_0 x_1 x_N y_1 y_N z1 z2 z_1 z_2 Γ_gamma d_dts)
