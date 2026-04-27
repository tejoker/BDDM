from __future__ import annotations

from axiom_debt_burndown import (
    build_deep_domain_obligations,
    build_axiom_debt_burndown,
    grounding_metadata_for_debt,
    summarize_paper_theory_debt_tiers,
)


def test_axiom_debt_burndown_ranks_missing_definitions_before_domain_theory() -> None:
    report = build_axiom_debt_burndown(
        [
            {
                "theorem_name": "def_space",
                "status": "AXIOM_BACKED",
                "proof_method": "domain_axiom",
                "axiom_debt": ["paper_definition_stub:L2Space"],
            },
            {
                "theorem_name": "lem_bridge",
                "status": "AXIOM_BACKED",
                "proof_method": "domain_axiom",
                "axiom_debt": ["paper_theory_reference"],
            },
            {
                "theorem_name": "thm_spde",
                "status": "AXIOM_BACKED",
                "proof_method": "domain_axiom",
                "axiom_debt": ["translation_repair_domain_assumption"],
            },
        ]
    )

    assert report["axiom_backed_result_count"] == 3
    assert report["result_buckets"] == {
        "missing_definitions_only": 1,
        "local_lemmas": 2,
        "missing_mathlib": 0,
        "deep_domain_theory": 0,
        "translation_artifacts": 0,
        "unclassified": 0,
    }
    assert (
        report["summary_sentence"]
        == "3 axiom-backed results, 1 depends only on missing definitions, "
        "2 depend on local lemmas."
    )
    ranked = report["ranked_axioms"]
    assert [item["paper_local_axiom"] for item in ranked] == [
        "paper_definition_stub:L2Space",
        "paper_theory_reference",
        "translation_repair_domain_assumption",
    ]
    assert ranked[0]["axiom_kind"] == "definition"
    assert ranked[0]["mathlib_has_close_match"] is True
    assert ranked[0]["mathlib_candidates"] == ["MeasureTheory.Lp", "MeasureTheory.MemLp"]
    assert ranked[2]["axiom_kind"] == "domain_assumption"
    assert ranked[2]["appears_explicitly_in_paper"] == "unknown"
    assert ranked[2]["dependency_bucket"] == "local_lemmas"


def test_axiom_debt_burndown_records_earlier_claim_candidates() -> None:
    report = build_axiom_debt_burndown(
        [
            {
                "theorem_name": "lem_naive_low_high_estimate",
                "status": "FULLY_PROVEN",
                "lean_statement": "theorem lem_naive_low_high_estimate : True",
            },
            {
                "theorem_name": "cor_safe_range",
                "status": "AXIOM_BACKED",
                "axiom_debt": ["paper_symbol:naive_low_high_estimate"],
            },
        ]
    )

    item = report["ranked_axioms"][0]
    assert item["axiom_kind"] == "lemma"
    assert item["can_be_proved_from_earlier_extracted_claims"] == "candidate"
    assert item["earlier_extracted_claim_candidates"] == ["lem_naive_low_high_estimate"]


def test_paper_theory_debt_tiers_separate_proof_value() -> None:
    report = summarize_paper_theory_debt_tiers(
        [
            {
                "theorem_name": "def_admissible__audited_core",
                "ledger_role": "audited_core_replacement",
                "status": "FULLY_PROVEN",
                "proof_method": "lean_verified",
            },
            {
                "theorem_name": "thm_baseline_lift",
                "status": "UNRESOLVED",
                "lean_statement": "theorem thm_baseline_lift : ThmBaselineLiftRegeneratedStatement",
                "axiom_debt": ["paper_theory_reference"],
                "translation_repair": {"statement_repair_kind": "faithful_statement_regeneration"},
            },
            {
                "theorem_name": "space_def",
                "status": "UNRESOLVED",
                "axiom_debt": ["paper_definition_stub:HSobolev"],
            },
            {
                "theorem_name": "deep",
                "status": "AXIOM_BACKED",
                "lean_statement": "theorem deep : cutoff_enhanced_data = cutoff_enhanced_data",
                "axiom_debt": ["paper_symbol:cutoff_enhanced_data"],
            },
        ]
    )

    assert report["counts_by_tier"] == {
        "audited_core_alignment": 1,
        "deep_domain_theory": 1,
        "definition_stub_grounding": 1,
        "regenerated_statement_tautology": 1,
    }
    assert report["counts_by_proof_value"]["audited_real_claim"] == 1
    assert report["counts_by_proof_value"]["tautological_regenerated_statement"] == 1
    assert report["counts_by_proof_value"]["definition_grounding"] == 1
    assert report["actionable_debt_count"] == 3
    assert report["items"][2]["grounding_metadata"][0]["proof_countable"] is False
    assert report["deep_domain_obligations"]["total_obligations"] == 1
    assert report["deep_domain_obligations"]["obligations"][0]["canonical_symbol"] == "cutoff_enhanced_data"


def test_grounding_metadata_keeps_definition_stubs_non_countable() -> None:
    meta = grounding_metadata_for_debt("paper_definition_stub:HSobolev")

    assert meta["grounding_kind"] == "mathlib_close_definition_stub"
    assert meta["grounding_trust"] == "syntax_only_not_semantic_proof"
    assert meta["proof_countable"] is False
    assert meta["hidden_assumption"] is False
    assert meta["paper_agnostic_rule_id"] == "definition_stub.mathlib_close_match"


def test_deep_domain_obligations_group_by_canonical_missing_lemma() -> None:
    report = build_deep_domain_obligations(
        [
            {
                "theorem_name": "prop_sharpness",
                "status": "UNRESOLVED",
                "proof_method": "translation_repaired_pending_proof",
                "axiom_debt": ["paper_local_lemma:DyadicBlockBound"],
            },
            {
                "theorem_name": "thm_pathwise_fluct",
                "status": "UNRESOLVED",
                "proof_method": "translation_repaired_pending_proof",
                "axiom_debt": ["paper_local_lemma:DyadicBlockBound"],
            },
        ]
    )

    assert report["total_obligations"] == 1
    obligation = report["obligations"][0]
    assert obligation["canonical_symbol"] == "DyadicBlockBound"
    assert obligation["needed_by"] == ["prop_sharpness", "thm_pathwise_fluct"]
    assert obligation["proof_countable"] is False
    assert obligation["replacement_gate"]["exact_recorded_statement_required"] is True
