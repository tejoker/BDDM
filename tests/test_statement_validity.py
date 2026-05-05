from __future__ import annotations

from statement_validity import (
    classify_statement,
    false_target_reason,
    proof_repair_cohort,
    statement_fidelity_gate,
    summarize_statement_fidelity,
    summarize_validity,
)


def test_statement_validity_classifies_current_bad_translation_fixtures() -> None:
    cases = [
        (
            {
                "theorem_name": "thm_baseline_lift",
                "lean_statement": "theorem thm_baseline_lift (p_c1 : Prop) (h_c1 : p_c1) : p_c1",
            },
            "translation_limited",
        ),
        (
            {
                "theorem_name": "prop_cubic_quartic_baseline",
                "lean_statement": "theorem prop_cubic_quartic_baseline : B_N^{i;j,k} = 0",
            },
            "ill_typed_statement",
        ),
        (
            {
                "theorem_name": "remark_9",
                "lean_statement": "theorem remark_9 (U1 : U1) : True",
            },
            "translation_limited",
        ),
        (
            {
                "theorem_name": "remark_10",
                "lean_statement": "theorem remark_10 (C : ℝ) (h_easy : C ≤ C) : C ≤ C",
            },
            "bad_translation_artifact",
        ),
        (
            {
                "theorem_name": "lem_volterra",
                "lean_statement": "theorem lem_volterra : Complex.abs x ≤ C",
            },
            "ill_typed_statement",
        ),
        (
            {
                "theorem_name": "thm_no_singular_centering",
                "lean_statement": "theorem thm_no_singular_centering (h_domain : P) : P",
                "axiom_debt": ["translation_repair_domain_assumption"],
            },
            "bad_translation_artifact",
        ),
        (
            {
                "theorem_name": "cor_safe_range",
                "lean_statement": "theorem cor_safe_range : Foo = Foo",
                "axiom_debt": ["paper_definition_stub:C_T"],
            },
            "proof_search_failure",
        ),
    ]

    for row, expected in cases:
        assert classify_statement(row).primary_blocker == expected


def test_statement_validity_treats_regenerated_statement_as_paper_theory_debt() -> None:
    item = classify_statement(
        {
            "theorem_name": "thm_baseline_lift",
            "lean_statement": "theorem thm_baseline_lift : ThmBaselineLiftRegeneratedStatement",
            "axiom_debt": ["paper_theory_reference"],
            "translation_repair": {"statement_repair_kind": "faithful_statement_regeneration"},
        }
    )

    assert item.primary_blocker == "paper_theory_debt"
    assert "regenerated_statement_atom" in item.reasons
    assert item.debt_tier == "regenerated_statement_tautology"
    assert item.proof_value == "tautological_regenerated_statement"
    assert item.valid_for_proof is False


def test_statement_validity_allows_audited_replacement_atoms_only_when_recorded_as_replacement() -> None:
    item = classify_statement(
        {
            "theorem_name": "def_admissible__audited_core",
            "ledger_role": "audited_core_replacement",
            "status": "FULLY_PROVEN",
            "lean_statement": "theorem def_admissible__audited_core : AutoAdmissibleFull eps alpha s1 s2 theta rhoV",
        }
    )

    assert item.primary_blocker == "release_ready"
    assert item.valid_for_proof is True


def test_statement_validity_keeps_schema_fallback_out_of_proof_cohort() -> None:
    item = classify_statement(
        {
            "theorem_name": "translation_failed",
            "lean_statement": "-- STATEMENT_REPAIR_NEEDED: schema_unavailable\ntheorem translation_failed : False",
        }
    )

    assert item.primary_blocker == "translation_limited"
    assert item.valid_for_proof is False


def test_statement_validity_emits_proof_repair_only_cohort() -> None:
    rows = [
        {"theorem_name": "bad", "lean_statement": "theorem bad (p_c1 : Prop) (h_c1 : p_c1) : p_c1"},
        {"theorem_name": "debt", "lean_statement": "theorem debt : Foo = Foo", "axiom_debt": ["paper_symbol:Foo"]},
        {"theorem_name": "proof", "lean_statement": "theorem proof (n : Nat) : n = n", "status": "UNRESOLVED"},
    ]

    summary = summarize_validity(rows)
    cohort = proof_repair_cohort(rows)

    assert summary["counts"]["proof_search_failure"] == 1
    assert cohort == [{"theorem_name": "proof", "primary_blocker": "proof_search_failure", "reasons": ["proof_not_closed"]}]


def test_statement_validity_treats_definition_stubs_as_statement_valid() -> None:
    item = classify_statement(
        {
            "theorem_name": "operator_statement",
            "lean_statement": "theorem operator_statement : ∃ s : ℝ, HSobolev s = HSobolev s",
            "axiom_debt": ["paper_definition_stub:HSobolev"],
        }
    )

    assert item.primary_blocker == "proof_search_failure"
    assert item.valid_for_proof is True


def test_statement_validity_allows_explicit_faithful_regeneration_without_claim_atom() -> None:
    item = classify_statement(
        {
            "theorem_name": "thm_baseline_lift",
            "lean_statement": "theorem thm_baseline_lift : ∃ alpha : ℝ, (3 / 4 : ℝ) < alpha",
            "translation_repair": {"statement_repair_kind": "faithful_statement_regeneration"},
        }
    )

    assert item.primary_blocker == "proof_search_failure"
    assert item.valid_for_proof is True


def test_statement_validity_blocks_vacuous_faithful_regeneration() -> None:
    item = classify_statement(
        {
            "theorem_name": "weak_regen",
            "lean_statement": "theorem weak_regen : ∃ x : ℝ, x = x",
            "translation_repair": {"statement_repair_kind": "faithful_statement_regeneration"},
        }
    )

    assert item.primary_blocker == "translation_limited"
    assert "trivial_exists_self_equality_target" in item.reasons
    assert item.valid_for_proof is False


def test_statement_fidelity_gate_blocks_placeholders_raw_latex_and_elaboration_failures() -> None:
    cases = [
        {
            "theorem_name": "placeholder",
            "lean_statement": "theorem placeholder (p_c1 : Prop) (h_c1 : p_c1) : p_c1",
        },
        {
            "theorem_name": "raw_latex",
            "lean_statement": r"theorem raw_latex : \frac{x}{y} = z",
        },
        {
            "theorem_name": "bad_elab",
            "lean_statement": "theorem bad_elab : MissingSymbol = MissingSymbol",
            "validation_gates": {"statement_elaborates": False},
        },
    ]

    decisions = [statement_fidelity_gate(row) for row in cases]

    assert all(decision.proof_eligible is False for decision in decisions)
    assert decisions[0].statement_fidelity_verdict == "repair_candidate"
    assert "raw_latex_command_leak" in decisions[1].statement_fidelity_blockers
    assert "lean_elaboration_failed" in decisions[2].statement_fidelity_blockers


def test_statement_fidelity_gate_allows_human_or_hybrid_reviewed_exact_rows() -> None:
    row = {
        "theorem_name": "good",
        "lean_statement": "theorem good (n : Nat) : n = n",
        "reviewed_statement_alignment_class": "exact",
        "reviewed_equivalence_verdict": "equivalent",
        "reviewed_alignment_confidence": 0.92,
        "review_provenance": {"reviewer_type": "human", "reviewed_by": "alice"},
    }

    decision = statement_fidelity_gate(row)

    assert decision.proof_eligible is True
    assert decision.statement_fidelity_verdict == "reviewed_exact"
    assert decision.statement_fidelity_source == "human"


def test_statement_fidelity_gate_reviewed_exact_overrides_claim_review_pending_only() -> None:
    row = {
        "theorem_name": "reviewed_claim",
        "lean_statement": "theorem reviewed_claim (n : Nat) : n = n",
        "gate_failures": ["lean_proof_closed", "step_verdict_verified", "translation_fidelity_ok", "claim_equivalent"],
        "reviewed_statement_alignment_class": "exact",
        "reviewed_equivalence_verdict": "equivalent",
        "reviewed_alignment_confidence": 0.9,
        "review_provenance": {"reviewer_type": "hybrid", "reviewed_by": "hybrid:auto-alignment-review"},
    }

    decision = statement_fidelity_gate(row)

    assert decision.proof_eligible is True
    assert decision.statement_fidelity_verdict == "reviewed_exact"
    assert decision.validity_primary_blocker == "claim_review_pending"


def test_statement_fidelity_gate_reviewed_exact_does_not_override_axiom_debt() -> None:
    row = {
        "theorem_name": "debt",
        "lean_statement": "theorem debt (x : Foo) : x = x",
        "gate_failures": ["no_paper_axiom_debt", "claim_equivalent"],
        "axiom_debt": ["paper_symbol:Foo"],
        "reviewed_statement_alignment_class": "exact",
        "reviewed_equivalence_verdict": "equivalent",
        "reviewed_alignment_confidence": 0.9,
        "review_provenance": {"reviewer_type": "hybrid", "reviewed_by": "hybrid:auto-alignment-review"},
    }

    decision = statement_fidelity_gate(row)

    assert decision.proof_eligible is False
    assert decision.validity_primary_blocker == "paper_theory_debt"
    assert "statement_validity:paper_theory_debt" in decision.statement_fidelity_blockers


def test_statement_fidelity_gate_blocks_llm_only_review_even_if_equivalent() -> None:
    row = {
        "theorem_name": "llm_only",
        "lean_statement": "theorem llm_only (n : Nat) : n = n",
        "reviewed_statement_alignment_class": "exact",
        "reviewed_equivalence_verdict": "equivalent",
        "reviewed_alignment_confidence": 0.99,
        "review_provenance": {"reviewer_type": "llm", "reviewed_by": "llm:test"},
    }

    decision = statement_fidelity_gate(row)

    assert decision.proof_eligible is False
    assert decision.statement_fidelity_source == "llm_triage"
    assert "llm_review_not_release_eligible" in decision.statement_fidelity_blockers
    assert "llm_triage_cannot_enable_proof_eligibility" in decision.statement_fidelity_blockers


def test_statement_fidelity_gate_requires_repaired_statement_to_reenter_review() -> None:
    row = {
        "theorem_name": "repaired",
        "lean_statement": "theorem repaired : ∃ alpha : ℝ, (3 / 4 : ℝ) < alpha",
        "translation_repair": {"statement_repair_kind": "faithful_statement_regeneration"},
    }

    decision = statement_fidelity_gate(row)

    assert decision.proof_eligible is False
    assert decision.statement_fidelity_verdict == "blocked"
    assert any("claim_equivalence_not_equivalent" in b for b in decision.statement_fidelity_blockers)


def test_statement_fidelity_summary_exposes_release_denominators() -> None:
    rows = [
        {
            "theorem_name": "eligible",
            "lean_statement": "theorem eligible (n : Nat) : n = n",
            "statement_alignment_class": "exact",
            "alignment_gold_eligible": True,
            "claim_equivalence_verdict": "equivalent",
        },
        {
            "theorem_name": "blocked",
            "lean_statement": "theorem blocked (p_c1 : Prop) (h_c1 : p_c1) : p_c1",
        },
    ]

    summary = summarize_statement_fidelity(rows)

    assert summary["total_extracted_statements"] == 2
    assert summary["proof_eligible"] == 1
    assert summary["blocked_before_proof"] == 1
    assert summary["repair_candidates"] == 1


def test_false_target_is_bad_translation_unless_source_is_contradiction() -> None:
    row = {
        "theorem_name": "bad",
        "lean_statement": "theorem bad : False",
        "source_latex": "For every n, n = n.",
    }

    classified = classify_statement(row)

    assert false_target_reason(row) == "false_target_without_source_contradiction"
    assert classified.valid_for_proof is False
    assert classified.primary_blocker == "bad_translation_artifact"
    assert "false_target_without_source_contradiction" in classified.reasons


def test_false_target_can_represent_explicit_contradiction_source() -> None:
    row = {
        "theorem_name": "contradiction",
        "lean_statement": "theorem contradiction : False",
        "source_latex": "This hypothesis package implies a contradiction.",
    }

    assert false_target_reason(row) == ""


def test_false_target_does_not_represent_plain_nonexistence_claim() -> None:
    row = {
        "theorem_name": "nonexistence",
        "lean_statement": "theorem nonexistence : False",
        "source_latex": "There does not exist a continuous solution satisfying the boundary condition.",
    }

    assert false_target_reason(row) == "false_target_without_source_contradiction"
