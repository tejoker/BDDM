"""Regression tests for translator repair-dispatch logic.

Each test pins a specific error class that was exposed during the 2304.09598
proof-of-concept run, where 4 theorems required patching due to translation
failures the general pipeline did not yet handle robustly.

Error classes covered:
  - synthInst / failed to synthesize (Precedes)
  - Function expected (Prop_IncreasingLength)
  - unexpected token in signature (Lem_PrecedesQuantum)
  - claim_shape_mismatch:ineq->exists + assumption_slot_missing (defin_14)
  - semantic_policy_violation / trivialization (Lem_Quant)
  - semantic_repair_invalid triggers fallback, not crash (Lem_Quant round ≥1)
"""
from __future__ import annotations

from translator._translate import (
    _apply_schema_fallback,
    _add_missing_function_binders,
    _basic_assumption_slot_issue,
    _claim_shape_mismatch_issue,
    _deterministic_signature_cleanup,
    _extra_retry_rounds_for_error,
    _extract_autoimplicit_function_names,
    _is_trivialized_signature,
    _is_schema_scaffold_signature,
    _retry_directive_for_error,
    _relax_fragile_hypotheses,
    _schema_self_check_hard_issues,
    _semantic_policy_issues,
)


# ---------------------------------------------------------------------------
# _retry_directive_for_error
# ---------------------------------------------------------------------------


class TestRetryDirectiveForError:
    """_retry_directive_for_error must route each 2304.09598 error class correctly."""

    def test_synthinst_error_triggers_typeclass_fix_mode(self) -> None:
        # Precedes: lean.synthInst — typeclass synthesis failure
        err = "/tmp/validate_abc.lean:8:35: error: failed to synthesize\n  Decidable (b1 < b2)"
        directive = _retry_directive_for_error(err)
        assert "TYPECLASS FIX MODE" in directive
        assert "explicit hypotheses" in directive

    def test_synthinst_short_form_also_routes(self) -> None:
        err = "synthInst failed for SomeClass"
        directive = _retry_directive_for_error(err)
        assert "TYPECLASS FIX MODE" in directive

    def test_unexpected_token_triggers_syntax_normalizer(self) -> None:
        # Lem_PrecedesQuantum: unexpected token in signature
        err = "/tmp/validate_xyz.lean:6:113: error: unexpected token '→'"
        directive = _retry_directive_for_error(err)
        assert "SYNTAX NORMALIZER MODE" in directive

    def test_semantic_policy_violation_triggers_hard_mode(self) -> None:
        # Lem_Quant / defin_14: semantic_policy_violation
        err = "semantic_policy_violation:claim_shape_mismatch:ineq->exists,assumption_slot_missing"
        directive = _retry_directive_for_error(err)
        assert "SEMANTIC HARD MODE" in directive
        assert "claim shape" in directive

    def test_trivialization_hard_violation_triggers_hard_mode(self) -> None:
        err = "trivialization_hard_violation"
        directive = _retry_directive_for_error(err)
        assert "SEMANTIC HARD MODE" in directive

    def test_vacuity_triggers_non_tautology_mode(self) -> None:
        err = "vacuity: statement is trivially provable by `trivial`"
        directive = _retry_directive_for_error(err)
        assert "NON-TAUTOLOGY MODE" in directive

    def test_unknown_error_returns_generic_preserve_directive(self) -> None:
        err = "some completely unknown error"
        directive = _retry_directive_for_error(err)
        assert directive  # non-empty
        assert "Preserve theorem intent" in directive


# ---------------------------------------------------------------------------
# _extra_retry_rounds_for_error
# ---------------------------------------------------------------------------


class TestExtraRetryRoundsForError:
    """Certain error classes must grant extra retry budget."""

    def test_synthinst_grants_one_extra_round(self) -> None:
        # Precedes needed exactly one repair round
        err = "failed to synthesize\n  DecidableEq Seg"
        assert _extra_retry_rounds_for_error(err) == 1

    def test_unexpected_token_grants_one_extra_round(self) -> None:
        err = "unexpected token ':=' at position 7"
        assert _extra_retry_rounds_for_error(err) == 1

    def test_schema_coverage_missing_grants_two_extra_rounds(self) -> None:
        err = "schema_coverage_missing:assumption_b"
        assert _extra_retry_rounds_for_error(err) == 2

    def test_unknown_error_grants_zero_extra_rounds(self) -> None:
        err = "application type mismatch"
        assert _extra_retry_rounds_for_error(err) == 0

    def test_semantic_policy_violation_grants_zero_extra_rounds(self) -> None:
        # semantic_policy_violation alone does NOT grant extra rounds
        # (the hard-mode prompt change is sufficient)
        err = "semantic_policy_violation:trivialization_hard_violation"
        assert _extra_retry_rounds_for_error(err) == 0


# ---------------------------------------------------------------------------
# _claim_shape_mismatch_issue  (defin_14 regression)
# ---------------------------------------------------------------------------


class TestClaimShapeMismatchIssue:
    """_claim_shape_mismatch_issue detects when latex says ∃ but Lean uses ineq."""

    def test_ineq_to_exists_mismatch_detected(self) -> None:
        # defin_14: latex uses ≤ ordering but model produced an ∃ ... ∃ signature
        latex = "We say that a multisegment α is a ladder if b_i ≤ b_j for all i ≤ j."
        sig = "theorem defin_14 (a : Multiset (α × α)) : (∃ n, ∃ f : Fin n → α × α, True) = (∃ n, ∃ f : Fin n → α × α, True) := by rfl"
        issue = _claim_shape_mismatch_issue(latex, sig)
        assert issue == "claim_shape_mismatch:ineq->exists"

    def test_exists_to_ineq_mismatch_detected(self) -> None:
        latex = "There exists a segment Δ such that P(Δ)."
        sig = "theorem t (n : ℕ) : n ≤ n := le_refl n"
        issue = _claim_shape_mismatch_issue(latex, sig)
        assert issue == "claim_shape_mismatch:exists->ineq"

    def test_matching_shapes_return_none(self) -> None:
        latex = "For all x, we have x ≤ x + 1."
        sig = "theorem t (x : ℕ) : x ≤ x + 1 := Nat.le_succ x"
        assert _claim_shape_mismatch_issue(latex, sig) is None

    def test_prop_shape_never_flagged(self) -> None:
        # If latex has no shape keyword, mismatch check is suppressed
        latex = "The set is non-empty."
        sig = "theorem t (n : ℕ) : n ≤ n := le_refl n"
        assert _claim_shape_mismatch_issue(latex, sig) is None

    def test_iff_mismatch_detected(self) -> None:
        latex = "P holds if and only if Q holds."
        sig = "theorem t (p : ℕ) : p ≤ p := le_refl p"
        assert _claim_shape_mismatch_issue(latex, sig) == "claim_shape_mismatch:iff->ineq"


# ---------------------------------------------------------------------------
# _basic_assumption_slot_issue  (defin_14 regression)
# ---------------------------------------------------------------------------


class TestBasicAssumptionSlotIssue:
    """_basic_assumption_slot_issue catches signatures missing hypothesis slots."""

    def test_missing_slot_when_latex_has_assumption(self) -> None:
        # defin_14: latex says "given" but signature had no (h : ...) binders
        latex = "Given any multisegment α, if α is a ladder then b_i ≤ b_j."
        sig = "theorem defin_14 (a : Multiset (ℕ × ℕ)) : True := trivial"
        issue = _basic_assumption_slot_issue(latex, sig)
        assert issue == "assumption_slot_missing:no_hypothesis_slots"

    def test_no_issue_when_slot_present(self) -> None:
        latex = "If α is a ladder then b_i ≤ b_j."
        sig = "theorem t (h1 : IsLadder α) : b ≤ e := by exact h1.mono"
        assert _basic_assumption_slot_issue(latex, sig) is None

    def test_no_issue_when_latex_has_no_condition_keywords(self) -> None:
        latex = "The set is non-empty."
        sig = "theorem t : True := trivial"
        assert _basic_assumption_slot_issue(latex, sig) is None


class TestFaithfulnessPolicy:
    def test_schema_definition_first_is_scaffold_not_translation(self) -> None:
        sig = "theorem schema_definition_first (h1 : Prop) : (let Claim : Prop := h1; Claim) := by"
        assert _is_schema_scaffold_signature(sig) is True
        issues = _semantic_policy_issues(
            latex_statement="If P holds, then Q follows.",
            signature=sig,
            schema=None,
            strict_assumption_slot_coverage=True,
        )
        assert "schema_scaffold_not_faithful" in issues

    def test_policy_blocks_claim_copied_into_hypothesis(self) -> None:
        issues = _semantic_policy_issues(
            latex_statement="The estimate is bounded by C.",
            signature="theorem t (C : ℝ) (h_easy : C ≤ C) : C ≤ C := by",
            schema=None,
            strict_assumption_slot_coverage=True,
        )
        assert "claim_copied_into_hypothesis:h_easy" in issues


# ---------------------------------------------------------------------------
# _is_trivialized_signature  (Lem_Quant regression)
# ---------------------------------------------------------------------------


class TestIsTrivializedSignature:
    """Trivialization check blocks (0 : ℕ) = 0 and True stubs (but not defin_* stubs)."""

    def test_nat_zero_eq_zero_is_trivialized(self) -> None:
        sig = "theorem Lem_Quant (h : True) : (0 : ℕ) = 0 := by rfl"
        assert _is_trivialized_signature(sig) is True

    def test_true_body_is_trivialized(self) -> None:
        sig = "theorem Lem_Quant : True := trivial"
        assert _is_trivialized_signature(sig) is True

    def test_defin_prefix_exempted_from_trivialization(self) -> None:
        sig = "theorem defin_14 (h : Prop) : True := trivial"
        assert _is_trivialized_signature(sig) is False

    def test_real_statement_not_trivialized(self) -> None:
        sig = "theorem Lem_Quant (h : n + m = S + c) (hm : IsLadderMultisegment α) : IsLadderMultisegment α := hm"
        assert _is_trivialized_signature(sig) is False


# ---------------------------------------------------------------------------
# _semantic_policy_issues  (integration across all 2304.09598 patterns)
# ---------------------------------------------------------------------------


class TestSemanticPolicyIssues:
    """_semantic_policy_issues aggregates all checks into one blocking list."""

    def test_trivialization_alone_blocks(self) -> None:
        issues = _semantic_policy_issues(
            latex_statement="If n + m = S + c then α is a ladder multisegment.",
            signature="theorem Lem_Quant (h : Prop) : (0 : ℕ) = 0 := by rfl",
            schema=None,
            strict_assumption_slot_coverage=False,
        )
        assert "trivialization_hard_violation" in issues

    def test_claim_shape_mismatch_blocks(self) -> None:
        issues = _semantic_policy_issues(
            latex_statement="For all i ≤ j, we have b_i ≤ b_j.",
            signature="theorem t (a : Multiset (ℕ × ℕ)) : (∃ n, True) = (∃ n, True) := rfl",
            schema=None,
            strict_assumption_slot_coverage=False,
        )
        shape_issues = [x for x in issues if "claim_shape_mismatch" in x]
        assert shape_issues, f"Expected claim_shape_mismatch in {issues}"

    def test_clean_signature_returns_no_issues(self) -> None:
        issues = _semantic_policy_issues(
            latex_statement="For all x ≤ y, P(x, y) holds.",
            signature="theorem t (x y : ℕ) (h : x ≤ y) (hP : P x y) : P x y := hP",
            schema=None,
            strict_assumption_slot_coverage=False,
        )
        assert issues == []

    def test_assumption_slot_missing_blocked_when_strict(self) -> None:
        issues = _semantic_policy_issues(
            latex_statement="Suppose that α is a ladder multisegment.",
            signature="theorem t : True := trivial",
            schema=None,
            strict_assumption_slot_coverage=True,
        )
        slot_issues = [x for x in issues if "assumption_slot" in x]
        assert slot_issues, f"Expected assumption_slot issue in {issues}"


class TestPaper260421884Resilience:
    """Regression coverage for blocker classes from paper 2604.21884."""

    def test_schema_self_check_notes_are_advisory(self) -> None:
        issues = _schema_self_check_hard_issues(
            {
                "consistent": True,
                "missing_assumptions": [],
                "missing_claim_parts": [],
                "notes": ["extra eps hypothesis is harmless"],
            }
        )
        assert issues == []

    def test_schema_self_check_missing_parts_still_block(self) -> None:
        issues = _schema_self_check_hard_issues(
            {
                "consistent": False,
                "missing_assumptions": ["s_2 < 4 alpha - 3"],
                "missing_claim_parts": ["converge almost surely"],
                "notes": ["diagnostic detail"],
            }
        )
        assert "schema_self_check_inconsistent" in issues
        assert any(i.startswith("missing_assumptions:") for i in issues)
        assert any(i.startswith("missing_claim_parts:") for i in issues)
        assert not any(i.startswith("notes:") for i in issues)

    def test_filter_eventually_syntax_is_normalized(self) -> None:
        sig = (
            "theorem t : "
            "∀f (N : ℕ) in Filter.atTop, ∀ (i : Fin 2), P N i := by"
        )
        cleaned = _deterministic_signature_cleanup(sig)
        assert "∀ᶠ (N : ℕ) in Filter.atTop" in cleaned
        assert "∀f" not in cleaned

    def test_common_analysis_syntax_is_normalized(self) -> None:
        sig = (
            "theorem volterra {T : ℝ} {a : ℝ → ℝ} : "
            "ContDiff ℝ 1 (Set.Icc 0 T) a → Complex.abs (a 0) ≤ 1 := by"
        )
        cleaned = _deterministic_signature_cleanup(sig)
        assert "ContDiffOn ℝ 1 a (Set.Icc 0 T)" in cleaned
        assert "Complex.abs" not in cleaned

    def test_continuous_linear_map_spelling_is_normalized(self) -> None:
        sig = "theorem t (E : Type*) (eval_zero : ContinuousLinearMap ℝ E ℝ) : True := by"
        cleaned = _deterministic_signature_cleanup(sig)
        assert "(eval_zero : E →L[ℝ] ℝ)" in cleaned

    def test_lipschitzwith_unit_constant_is_nnreal(self) -> None:
        sig = "theorem t (D : ℝ → ℝ) (hD : LipschitzWith (1 : ℝ) D) : True := by"
        cleaned = _deterministic_signature_cleanup(sig)
        assert "LipschitzWith (1 : ℝ≥0) D" in cleaned

    def test_lipschitzcontinuous_is_rewritten_to_mathlib_predicate(self) -> None:
        sig = "theorem t (D : ℝ → ℝ) (hD : LipschitzContinuous D) : True := by"
        cleaned = _deterministic_signature_cleanup(sig)
        assert "(hD : ∃ K : ℝ≥0, LipschitzWith K D)" in cleaned

    def test_two_argument_latex_call_is_normalized(self) -> None:
        sig = "theorem t (v : ℝ → ℝ) : v ∈ L2_f(0, z_F) := by"
        cleaned = _deterministic_signature_cleanup(sig)
        assert "L2_f 0 z_F" in cleaned

    def test_multiline_let_chain_keeps_rhs_on_next_line(self) -> None:
        sig = "theorem t :\n  let f : ℕ → ℕ :=\n    fun n => n\n  f 0 = 0 := by"
        cleaned = _deterministic_signature_cleanup(sig)
        assert "let f : ℕ → ℕ :=;" not in cleaned

    def test_colon_in_theorem_name_is_normalized(self) -> None:
        sig = "theorem lemm:SD (D : ℝ → ℝ) : True := by"
        assert _deterministic_signature_cleanup(sig).startswith("theorem lemm_SD ")

    def test_matrix_is_pos_def_field_is_expanded(self) -> None:
        sig = (
            "theorem t (n : ℕ) "
            "(M : Matrix (Fin (n - 1)) (Fin (n - 1)) ℝ) : "
            "M.IsSymm ∧ M.IsPosDef := by"
        )
        cleaned = _deterministic_signature_cleanup(sig)
        assert "M.IsPosDef" not in cleaned
        assert "∀ y : (Fin (n - 1)) → ℝ" in cleaned

    def test_autoimplicit_function_names_are_detected(self) -> None:
        err = (
            "error: Function expected at\n"
            "  a_N\n"
            "but this term has type ?m.3\n"
            "error: Function expected at\n"
            "  V_i\n"
            "but this term has type ?m.4"
        )
        sig = "theorem t (N : ℕ) : a_N N ≠ 0 := by"
        assert _extract_autoimplicit_function_names(err, sig) == ["a_N", "V_i"]

    def test_missing_function_binders_are_inserted_locally(self) -> None:
        sig = "theorem t (N : ℕ) : a_N N ≠ 0 ∧ V_i N = 0 := by"
        repaired = _add_missing_function_binders(sig, ["a_N", "V_i"])
        assert "(a_N : ℕ → ℝ)" in repaired
        assert "(V_i : ℕ → ℝ)" in repaired
        assert repaired.index("(V_i : ℕ → ℝ)") < repaired.index(": a_N N")

    def test_two_argument_basis_function_gets_binary_type(self) -> None:
        sig = "theorem t : phi (i + 1) z = 0 := by"
        repaired = _add_missing_function_binders(sig, ["phi"])
        assert "(phi : ℕ → ℝ → ℝ)" in repaired

    def test_l2_space_symbol_gets_set_valued_type(self) -> None:
        sig = "theorem t : v ∈ L2_f 0 z_F := by"
        repaired = _add_missing_function_binders(sig, ["L2_f"])
        assert "(L2_f : ℝ → ℝ → Set (ℝ → ℝ))" in repaired

    def test_fragile_membership_hypothesis_relaxes_to_prop_slot(self) -> None:
        sig = "theorem t (hU : U ∈ C_TH ^ s1) : True := by"
        assert _relax_fragile_hypotheses(sig) == "theorem t (hU : Prop) : True := by"

    def test_fragile_abs_hypothesis_relaxes_to_prop_slot(self) -> None:
        sig = "theorem t (hA : ∀ i j, i ≠ j ∧ |i - j| ≠ 1 → A i j = 0) : True := by"
        assert _relax_fragile_hypotheses(sig) == "theorem t (hA : Prop) : True := by"

    def test_fragile_nested_exponent_hypothesis_relaxes_to_prop_slot(self) -> None:
        sig = "theorem t (h : ∃ C, ∀ N, N^(s2 + 3 - 4*alpha - theta*s1 + eps) ≤ C) (ok : P) : P := by"
        assert _relax_fragile_hypotheses(sig) == "theorem t (h : Prop) (ok : P) : P := by"

    def test_schema_missing_fallback_comments_all_original_lines(self) -> None:
        sig = "theorem bad\n  (x : ℕ) : x = x := by"
        fallback = _apply_schema_fallback(sig, None)
        assert "STATEMENT_REPAIR_NEEDED: schema_unavailable" in fallback
        assert "\ntheorem bad : False := by sorry" in fallback
        for line in fallback.splitlines()[1:-1]:
            assert line.startswith("-- ")

    def test_schema_fallback_marks_statement_repair_needed(self) -> None:
        fallback = _apply_schema_fallback(
            "theorem t (p_c1 : Prop) (h_c1 : p_c1) : p_c1 := by",
            {"claim": "The paper claim has real mathematical content.", "assumptions": ["Assume alpha > 3/4."]},
        )

        assert "STATEMENT_REPAIR_NEEDED: schema_fallback" in fallback
        assert "(p_c1 : Prop)" not in fallback
        assert "theorem t : False := by sorry" in fallback
