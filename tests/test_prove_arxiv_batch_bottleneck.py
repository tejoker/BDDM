from __future__ import annotations

import pipeline_status
import re

from prove_arxiv_batch import (
    _GOLD_PROOF_QUEUE_OVERRIDES,
    _binder_groups_before_target,
    _decl_target,
    _domain_proof_hint,
    _extract_sorry_theorems,
    _gold_override_for_theorem,
    _hypotheses_by_type,
    _is_nontrivial_declaration,
    _is_repl_startup_failure,
    _load_gold_proof_queue_overrides,
    _load_ledger_entry_for_theorem,
    _micro_prover_scripts_for_decl,
    _nontrivial_drop_reasons,
    _normalize_substituted_statement_body,
    _provability_sanity_issue,
    _review_queue_target_names,
    _sanitize_generated_lean_file,
    _schema_placeholder_hyp_identity,
    _slot_scripts_for_state,
    _statement_fidelity_gate_issue,
    _statement_shape_route,
    _translation_gate_issue,
    _translation_limited_reason,
)


def test_nontrivial_filter_relaxed_keeps_structured_implication_to_zero() -> None:
    decl = (
        "theorem Thm_ManySimpleA_B (h1 : Prop) (h2 : Prop) (h3 : Prop) : "
        "h1 ∧ h2 ∧ h3 → (0 : ℕ) = 0 := by sorry"
    )
    assert _is_nontrivial_declaration(decl, strict=False) is True
    reasons = _nontrivial_drop_reasons(decl, strict=False)
    assert "imp_nat0eq0" not in reasons


def test_nontrivial_filter_strict_keeps_legacy_aggressive_block() -> None:
    decl = (
        "theorem Thm_ManySimpleA_B (h1 : Prop) (h2 : Prop) (h3 : Prop) : "
        "h1 ∧ h2 ∧ h3 → (0 : ℕ) = 0 := by sorry"
    )
    assert _is_nontrivial_declaration(decl, strict=True) is False
    assert "imp_nat0eq0" in _nontrivial_drop_reasons(decl, strict=True)


def test_nontrivial_filter_keeps_schema_placeholder_identity_body() -> None:
    decl = "theorem Prop_Actions (p_c1 : Prop) (h_c1 : p_c1) : p_c1 := by sorry"
    assert _is_nontrivial_declaration(decl, strict=False) is True
    reasons = _nontrivial_drop_reasons(decl, strict=False)
    assert "pure_p_c_placeholder" not in reasons
    assert "no_math_token" not in reasons


def test_ledger_lookup_uses_base_namespaced_aliases(monkeypatch) -> None:
    rows = [
        {"theorem_name": "Def_Simple", "status": "UNRESOLVED", "translation_fidelity_ok": False},
        {"theorem_name": "ArxivPaper.Def_Simple", "status": "FULLY_PROVEN", "translation_fidelity_ok": True},
    ]
    monkeypatch.setattr(pipeline_status, "load_ledger", lambda paper_id: rows)
    entry = _load_ledger_entry_for_theorem("2304.09598", "Def_Simple")
    assert isinstance(entry, dict)
    assert entry.get("theorem_name") == "ArxivPaper.Def_Simple"


def test_micro_prover_scripts_cover_conjunction_and_exists_unique() -> None:
    decl = "theorem Cor_Arthur : ∀ a, P a → ∃! b, Q b := by sorry"
    scripts = _micro_prover_scripts_for_decl(decl)
    assert "intros <;> aesop" in scripts
    assert "aesop" in scripts


def test_micro_prover_scripts_close_trivial_conjunction_of_rfls() -> None:
    """A theorem of the shape `a = a ∧ b = b` (paper-local placeholder pattern
    after stub recovery) must include `exact ⟨rfl, rfl⟩` and the repeat-fixpoint
    scaffold-closer in the candidate set."""
    decl = "theorem remark_9 : I_i ξ1 = I_i ξ1 ∧ I_i ξ2 = I_i ξ2 := by sorry"
    scripts = _micro_prover_scripts_for_decl(decl)
    assert "exact ⟨rfl, rfl⟩" in scripts
    assert "constructor <;> rfl" in scripts
    assert "repeat (first | constructor | rfl | assumption | trivial)" in scripts


def test_micro_prover_scripts_close_schema_scaffold_propositional_conjunction() -> None:
    """Scaffold rows of the form `(p₁ : Prop) (h₁ : p₁) … : p₁ ∧ p₂ ∧ p₃` are
    closed by the multi-arity refine-and-assumption tactic."""
    decl = (
        "theorem Local (h1 : Prop) (h2 : Prop) (h3 : Prop) "
        "(p1 : h1) (p2 : h2) (p3 : h3) : h1 ∧ h2 ∧ h3 := by sorry"
    )
    scripts = _micro_prover_scripts_for_decl(decl)
    assert "refine ⟨?_, ?_, ?_⟩ <;> assumption" in scripts
    assert "repeat (first | constructor | rfl | assumption | trivial)" in scripts


def test_micro_prover_scripts_close_trivial_existential_with_explicit_witness() -> None:
    """`∃ x : ℝ, x = x` scaffold rows are closed by the explicit-witness
    candidates we add (`exact ⟨0, rfl⟩` etc.) — the bare `exact ⟨_, rfl⟩` was
    insufficient because Lean couldn't always infer the witness."""
    decl = "theorem Cor_Arthur (x : ℝ) : ∃ x : ℝ, x = x := by sorry"
    scripts = _micro_prover_scripts_for_decl(decl)
    assert "exact ⟨0, rfl⟩" in scripts
    assert "exact ⟨(0 : ℝ), rfl⟩" in scripts
    assert "simp" in scripts


def test_statement_shape_router_marks_definition_lane() -> None:
    decl = "theorem Def_Simple (a : Nat) : a = {1,2,3} := by sorry"
    assert _statement_shape_route(decl) == "definition_lane"


def test_provability_sanity_blocks_unconstrained_consequent_symbol() -> None:
    decl = (
        "theorem Cor_Arthur : "
        "(∀ a, orbits a → ∃! b, ABV_packets b) → "
        "(∀ a, orbits a → ∀ b, ABV_packets b → A_packets b) := by sorry"
    )
    issue = _provability_sanity_issue(decl)
    assert issue.startswith("unconstrained_consequent_symbols:")


def test_provability_sanity_flags_trivial_nat0eq0_target() -> None:
    decl = "theorem Basic : (0 : ℕ) = 0 := by sorry"
    assert _provability_sanity_issue(decl) == "trivial_nat0eq0_target"


def test_schema_placeholder_hyp_identity_detects_body_pattern() -> None:
    decl = "theorem Prop_Actions (p_c1 : Prop) (h_c1 : p_c1) : p_c1 := by sorry"
    assert _schema_placeholder_hyp_identity(decl) == ("p_c1", "h_c1")


def test_translation_limited_reason_catches_schema_and_trivial_targets() -> None:
    schema = "theorem Prop_Actions (p_c1 : Prop) (h_c1 : p_c1) : p_c1 := by sorry"
    trivial = "theorem Basic : (0 : ℕ) = 0 := by sorry"
    assert _translation_limited_reason(schema) == "schema_placeholder_identity"
    assert _translation_limited_reason(trivial) == "trivial_nat0eq0_target"


def test_translation_gate_issue_blocks_semantic_hard_flags() -> None:
    entry = {"translation_adversarial_flags": ["verdict:wrong"], "translation_uncertainty_flags": []}
    decl = "theorem RealClaim (n : ℕ) : n = n := by sorry"
    assert _translation_gate_issue(entry, decl).startswith("translation_hard_block:")


def test_statement_fidelity_gate_issue_blocks_llm_only_review_before_proof() -> None:
    entry = {
        "theorem_name": "T",
        "reviewed_statement_alignment_class": "exact",
        "reviewed_equivalence_verdict": "equivalent",
        "reviewed_alignment_confidence": 0.99,
        "review_provenance": {"reviewer_type": "llm", "reviewed_by": "llm:test"},
    }
    decl = "theorem T (n : Nat) : n = n := by sorry"

    issue, payload = _statement_fidelity_gate_issue(entry, decl, "T")

    assert issue.startswith("blocked:")
    assert payload["proof_eligible"] is False
    assert "llm_review_not_release_eligible" in payload["statement_fidelity_blockers"]


def test_repl_startup_failure_detector_matches_expected_signature() -> None:
    err = "line=1; message=unexpected identifier; expected command"
    assert _is_repl_startup_failure(err) is True


def test_slot_scripts_cover_goal_from_hypothesis_and_conjunction() -> None:
    state_pp = (
        "hP : P\n"
        "hQ : Q\n"
        "⊢ P ∧ Q\n"
    )
    scripts = _slot_scripts_for_state(state_pp)
    assert ["constructor", "exact hP", "exact hQ"] in scripts


def test_slot_scripts_cover_implication_chain_to_reflexive_equality() -> None:
    state_pp = "⊢ P → Q → (0 : ℕ) = (0 : ℕ)\n"
    scripts = _slot_scripts_for_state(state_pp)
    assert ["intro h1", "intro h2", "rfl"] in scripts


def test_slot_scripts_cover_implication_chain_to_zero_eq_zero_with_cast() -> None:
    state_pp = "⊢ P → (0 : ℕ) = 0\n"
    scripts = _slot_scripts_for_state(state_pp)
    assert ["intro h1", "rfl"] in scripts


def test_binder_groups_extract_nested_hypothesis_types_before_target() -> None:
    decl = (
        "theorem cor_conditional_cutoff\n"
        "  (hconverge : Filter.Tendsto (fun N => cutoff_solution N) Filter.atTop (nhds paracontrolled_solution)) :\n"
        "  Filter.Tendsto (fun N => cutoff_solution N) Filter.atTop (nhds paracontrolled_solution) := by\n"
        "  sorry\n"
    )
    groups = _binder_groups_before_target(decl)
    assert any(g.startswith("hconverge : Filter.Tendsto") for g in groups)
    by_type = _hypotheses_by_type(decl)
    target = "Filter.Tendsto (fun N => cutoff_solution N) Filter.atTop (nhds paracontrolled_solution)"
    assert by_type[target.replace(" ", "")] == "hconverge"


def test_domain_hint_and_micro_scripts_cover_analysis_arithmetic() -> None:
    decl = "theorem Bound (x y : ℝ) (h : x ≤ y) : x ≤ y := by sorry"
    assert "analysis/arithmetic" in _domain_proof_hint(decl)
    scripts = _micro_prover_scripts_for_decl(decl)
    assert "linarith" in scripts
    assert "nlinarith" in scripts


def test_review_queue_json_accepts_unresolved_list(tmp_path) -> None:
    queue = tmp_path / "unresolved.json"
    queue.write_text('[{"theorem_name": "B"}]', encoding="utf-8")
    assert _review_queue_target_names(queue) == {"B"}


def test_review_queue_json_accepts_report_object(tmp_path) -> None:
    queue = tmp_path / "report.json"
    queue.write_text('{"unresolved": [{"theorem_name": "A"}]}', encoding="utf-8")
    assert _review_queue_target_names(queue) == {"A"}


def test_sanitize_generated_lean_file_splits_sorry_theorem_separator(tmp_path) -> None:
    f = tmp_path / "paper.lean"
    f.write_text(
        "namespace ArxivPaper\n"
        "theorem A : True := by\n"
        "  sorry-- [theorem] B\n"
        "theorem B : True := by\n"
        "  sorry\n"
        "end ArxivPaper\n",
        encoding="utf-8",
    )
    changed = _sanitize_generated_lean_file(f)
    text = f.read_text(encoding="utf-8")
    assert changed is True
    assert "sorry\n\n-- [theorem] B" in text


def test_extract_sorry_theorems_detects_one_line_by_sorry(tmp_path) -> None:
    f = tmp_path / "paper_oneline.lean"
    f.write_text(
        "namespace ArxivPaper\n"
        "theorem nuclear_l1_norms : False := by sorry\n"
        "end ArxivPaper\n",
        encoding="utf-8",
    )

    theorems = _extract_sorry_theorems(f)

    assert [t.name for t in theorems] == ["nuclear_l1_norms"]


def test_sanitize_generated_lean_file_repairs_comma_identifier_artifacts(tmp_path) -> None:
    # Subscript-mangled identifiers (first part contains `_`) are fixed.
    f = tmp_path / "paper_comma.lean"
    f.write_text(
        "namespace ArxivPaper\n"
        "theorem A : C_beta,s1,s2 ≤ C_beta,s := by\n"
        "  sorry\n"
        "end ArxivPaper\n",
        encoding="utf-8",
    )

    changed = _sanitize_generated_lean_file(f)
    text = f.read_text(encoding="utf-8")

    assert changed is True
    assert "C_beta_s1_s2" in text
    assert "C_beta_s" in text


def test_sanitize_generated_lean_file_leaves_short_comma_pairs_intact(tmp_path) -> None:
    # Short identifier pairs like `a,b` are valid in simp lists / constructors
    # and must not be rewritten (no underscore in leading name).
    f = tmp_path / "paper_safe.lean"
    original = (
        "namespace ArxivPaper\n"
        "theorem B : True := by simp [ha,hb]\n"
        "end ArxivPaper\n"
    )
    f.write_text(original, encoding="utf-8")

    _sanitize_generated_lean_file(f)
    text = f.read_text(encoding="utf-8")

    assert "ha,hb" in text, "short comma pair inside simp list must not be rewritten"


def test_sanitize_generated_lean_file_inserts_blank_before_theorem_marker(tmp_path) -> None:
    f = tmp_path / "paper2.lean"
    f.write_text(
        "namespace ArxivPaper\n"
        "theorem A : True := by\n"
        "  sorry\n"
        "-- [theorem] B\n"
        "theorem B : True := by\n"
        "  sorry\n"
        "end ArxivPaper\n",
        encoding="utf-8",
    )
    changed = _sanitize_generated_lean_file(f)
    text = f.read_text(encoding="utf-8")
    assert changed is True
    assert re.search(r"sorry\n\s*\n-- \[theorem\] B", text)


def test_sanitize_generated_lean_file_indents_unindented_proof_line(tmp_path) -> None:
    f = tmp_path / "paper3.lean"
    f.write_text(
        "namespace ArxivPaper\n"
        "theorem A : True := by\n"
        "trivial\n"
        "end ArxivPaper\n",
        encoding="utf-8",
    )
    changed = _sanitize_generated_lean_file(f)
    text = f.read_text(encoding="utf-8")
    assert changed is True
    assert "theorem A : True := by\n  trivial\n" in text


def test_decl_target_uses_theorem_colon_not_binder_colons() -> None:
    decl = (
        "theorem Thm_Simple (h1 : Prop) (h2 : Prop) (h3 : Prop) : "
        "h1 ∧ h2 ∧ h3 → (0 : ℕ) = 0 := by\n"
        "  sorry\n"
    )
    assert _decl_target(decl) == "h1 ∧ h2 ∧ h3 → (0 : ℕ) = 0"


def test_micro_prover_emits_field_simp_for_division_goals() -> None:
    """Field-of-fractions sub/div goals should pull in field_simp; without it,
    ring/linarith stall on inverses. Generalises to every paper that has rational
    or real-valued statements."""
    decl = "theorem t (a b : ℝ) (h : b ≠ 0) : a / b = a * b⁻¹ := by\n  sorry"
    scripts = _micro_prover_scripts_for_decl(decl)
    assert "field_simp" in scripts
    assert "field_simp; ring" in scripts


def test_micro_prover_emits_norm_cast_for_mixed_nat_real_goals() -> None:
    """Goals that mix ℕ and ℝ need norm_cast to push the embedding through."""
    decl = "theorem t (n : ℕ) (x : ℝ) (h : (n : ℝ) ≤ x) : (n + 1 : ℝ) ≤ x + 1 := by\n  sorry"
    scripts = _micro_prover_scripts_for_decl(decl)
    assert "norm_cast" in scripts
    assert "push_cast; norm_num" in scripts


def test_micro_prover_emits_gcongr_for_inequality_goals() -> None:
    """Inequalities over ordered structures benefit from gcongr (generalised
    congruence for ordered ops). Universal across every analysis-style paper."""
    decl = "theorem t (a b c : ℝ) (h : a ≤ b) : a + c ≤ b + c := by\n  sorry"
    scripts = _micro_prover_scripts_for_decl(decl)
    assert "gcongr" in scripts


def test_micro_prover_emits_polyrith_for_polynomial_ring_goals() -> None:
    """Polynomial identity goals get polyrith (Gröbner-basis oracle)."""
    decl = (
        "theorem t {R : Type*} [CommRing R] (a b : R) : "
        "(a + b) ^ 2 = a ^ 2 + 2 * a * b + b ^ 2 := by\n  sorry"
    )
    scripts = _micro_prover_scripts_for_decl(decl)
    assert "polyrith" in scripts


def test_micro_prover_emits_interval_cases_for_finite_goals() -> None:
    """Finite-case goals get interval_cases combined with simp_all to close each branch."""
    decl = "theorem t (n : Fin 4) : n.val < 4 := by\n  sorry"
    scripts = _micro_prover_scripts_for_decl(decl)
    assert "interval_cases <;> simp_all" in scripts


def test_micro_prover_skips_field_tactics_for_pure_nat_goals() -> None:
    """A pure-ℕ goal must NOT pull in field_simp / norm_cast / polyrith — those
    tactics require richer structure and would just be wasted attempts."""
    decl = "theorem t (n : ℕ) : n + 0 = n := by\n  sorry"
    scripts = _micro_prover_scripts_for_decl(decl)
    assert "field_simp" not in scripts
    assert "field_simp; ring" not in scripts


def test_gold_queue_override_prefers_reviewed_verdict_over_unclear() -> None:
    """_load_gold_proof_queue_overrides must use reviewed_equivalence_verdict='equivalent'
    when claim_equivalence_verdict='unclear'. Skipped when the live queue is empty."""
    import pathlib
    qpath = pathlib.Path("output/corpus/gold_proof_growth_queue.jsonl")
    if not qpath.exists() or qpath.stat().st_size == 0:
        import pytest
        pytest.skip("gold queue file not available or empty")
    _GOLD_PROOF_QUEUE_OVERRIDES.clear()
    _load_gold_proof_queue_overrides(qpath)
    if not _GOLD_PROOF_QUEUE_OVERRIDES:
        import pytest
        pytest.skip("no rows in gold queue passed proof_candidate_blockers")
    # Every accepted override that had claim_equivalence_verdict='unclear'
    # should have been upgraded to 'equivalent' via reviewed_equivalence_verdict.
    unclear_and_not_upgraded = [
        (k, v["claim_equivalence_verdict"])
        for k, v in _GOLD_PROOF_QUEUE_OVERRIDES.items()
        if v.get("claim_equivalence_verdict", "") == "unclear"
    ]
    assert unclear_and_not_upgraded == [], (
        f"These overrides still carry 'unclear' verdict: {unclear_and_not_upgraded}"
    )


def test_gold_queue_stmt_substitution_replaces_false_target() -> None:
    """When a gold queue override exists with a real lean_statement,
    _decl_target on the gold statement must not be 'False'.
    Skipped when the live queue lacks the EqualLN entry."""
    import pathlib
    qpath = pathlib.Path("output/corpus/gold_proof_growth_queue.jsonl")
    if not qpath.exists() or qpath.stat().st_size == 0:
        import pytest
        pytest.skip("gold queue file not available or empty")
    _GOLD_PROOF_QUEUE_OVERRIDES.clear()
    _load_gold_proof_queue_overrides(qpath)
    override = _gold_override_for_theorem("2304.09598", "EqualLN")
    if override is None:
        import pytest
        pytest.skip("EqualLN not currently in live gold queue")
    gq_stmt = str((override or {}).get("lean_statement", "") or "").strip()
    assert gq_stmt, "lean_statement should be non-empty"
    assert _decl_target(gq_stmt).strip() != "False", (
        "gold queue lean_statement must not have 'False' as target"
    )
    assert "n_alpha" in gq_stmt, "gold queue EqualLN statement should mention n_alpha"


# ---------------------------------------------------------------------------
# _normalize_substituted_statement_body — POC follow-up B fix
# ---------------------------------------------------------------------------


def test_normalize_substituted_statement_body_appends_by_sorry() -> None:
    """A statement without any `:=` gets `:= by sorry` appended so `_decl_target`
    can locate the target proposition."""
    out = _normalize_substituted_statement_body("theorem t : True")
    assert out == "theorem t : True := by sorry"


def test_normalize_substituted_statement_body_passes_through_by_sorry() -> None:
    """If `:= by` is already present, the statement is returned unchanged."""
    sig = "theorem t (x : ℕ) : x = x := by trivial"
    assert _normalize_substituted_statement_body(sig) == sig


def test_normalize_substituted_statement_body_appends_after_bare_colon_eq() -> None:
    """A statement ending in `:=` (no body) gets ` by sorry` appended (single space)."""
    out = _normalize_substituted_statement_body("theorem t : True :=")
    assert out == "theorem t : True := by sorry"


def test_normalize_substituted_statement_body_strips_trivial_proof_term() -> None:
    """When the ledger statement carries a complete proof term like
    `:= trivial`, the normaliser must strip back to the statement head before
    appending `:= by sorry`. Without this, the substitution path produced
    `… := trivial := by sorry` (double `:=`, invalid Lean) — the bug that
    knocked `thm_baseline_lift` / `prop_mid_completion` out of the gate."""
    sig = "theorem thm_baseline_lift : BaselineLiftStatement := trivial"
    out = _normalize_substituted_statement_body(sig)
    assert out == "theorem thm_baseline_lift : BaselineLiftStatement := by sorry"
    assert ":= trivial := by sorry" not in out


def test_normalize_substituted_statement_body_strips_rfl_proof_term() -> None:
    """Same as the trivial case but for `:= rfl`."""
    sig = "theorem t (n : ℕ) : n = n := rfl"
    out = _normalize_substituted_statement_body(sig)
    assert out == "theorem t (n : ℕ) : n = n := by sorry"


def test_normalize_substituted_statement_body_empty_returns_empty() -> None:
    assert _normalize_substituted_statement_body("") == ""
    assert _normalize_substituted_statement_body("   ") == ""
