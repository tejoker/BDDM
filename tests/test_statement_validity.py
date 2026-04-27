from __future__ import annotations

from statement_validity import classify_statement, proof_repair_cohort, summarize_validity


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
