from __future__ import annotations

import pipeline_status
import re

from prove_arxiv_batch import (
    _binder_groups_before_target,
    _decl_target,
    _domain_proof_hint,
    _hypotheses_by_type,
    _is_nontrivial_declaration,
    _is_repl_startup_failure,
    _load_ledger_entry_for_theorem,
    _micro_prover_scripts_for_decl,
    _nontrivial_drop_reasons,
    _provability_sanity_issue,
    _review_queue_target_names,
    _sanitize_generated_lean_file,
    _schema_placeholder_hyp_identity,
    _slot_scripts_for_state,
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
