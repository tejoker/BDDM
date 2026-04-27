from __future__ import annotations

import hashlib

from formalize_paper_full import (
    _apply_auto_reliable_core_promotions,
    _apply_validated_translation_repairs,
    _audited_replacement_verified,
    _axiomize_decl_for_paper_local,
    _blocker_clusters,
    _build_missing_lemma_subledger,
    _claim_equivalence_review_queue_summary,
    _classify_axiom_debt_item,
    _closure_metrics,
    _definition_like_bad_statement_reason,
    _dedupe_final_ledger_entries,
    _detect_curated_paper_package,
    _ill_typed_translation_artifact_reason,
    _normalize_final_ledger_entries,
    _translation_limited_reason,
    _weakened_by_target_hypothesis_reason,
    _write_paper_local_theory_file,
)


def test_closure_metrics_full_and_unresolved() -> None:
    entries = [
        {"theorem_name": "t1", "status": "FULLY_PROVEN", "grounding_status": "GROUNDED_MATHLIB"},
        {"theorem_name": "t2", "status": "INTERMEDIARY_PROVEN", "grounding_status": "UNGROUNDED", "gate_failures": ["assumptions_grounded"]},
    ]
    m = _closure_metrics(entries)
    assert m["total_theorems"] == 2
    assert m["fully_proven"] == 1
    assert m["full_closure"] is False
    assert m["unresolved_count"] == 1
    assert m["status_counts"]["FULLY_PROVEN"] == 1
    assert m["status_counts"]["INTERMEDIARY_PROVEN"] == 1


def test_claim_equivalence_review_queue_summary_surfaces_impact_fields() -> None:
    summary = _claim_equivalence_review_queue_summary(
        [
            {
                "theorem_name": "remark_20",
                "promotion_potential_score": 90.0,
                "promotion_potential_tier": "high",
                "would_promote_if_equivalent": True,
                "remaining_blockers_after_adjudication": [],
            },
            {
                "theorem_name": "lemma_axiom",
                "promotion_potential_score": 10.0,
                "promotion_potential_tier": "diagnostic_only",
                "would_promote_if_equivalent": False,
                "remaining_blockers_after_adjudication": ["no_paper_axiom_debt"],
            },
        ]
    )

    assert summary["review_queue_count"] == 2
    assert summary["pending_review_count"] == 2
    assert summary["high_potential_review_count"] == 1
    assert summary["would_promote_if_equivalent_count"] == 1
    assert summary["top_review_targets"][0]["theorem_name"] == "remark_20"
    assert summary["remaining_blocker_counts"]["no_paper_axiom_debt"] == 1


def test_closure_metrics_separates_verified_and_auto_closed() -> None:
    entries = [
        {"theorem_name": "t1", "status": "FULLY_PROVEN", "proof_method": "lean_verified"},
        {"theorem_name": "t2", "status": "FULLY_PROVEN", "proof_method": "auto_closed"},
        {"theorem_name": "t3", "status": "TRANSLATION_LIMITED", "proof_method": "translation_limited"},
    ]
    m = _closure_metrics(entries)
    assert m["fully_proven"] == 2
    assert m["verified_proven"] == 1
    assert m["real_fully_proven"] == 1
    assert m["auto_closed_count"] == 1
    assert m["translation_limited_count"] == 1
    assert m["closure_by_method"]["auto_closed_or_reconciled"] == 1
    assert m["paper_local_axiom_disclosure"]["required"] is False


def test_closure_metrics_labels_axiom_backed_results_as_modulo_paper_local_axioms() -> None:
    entries = [
        {
            "theorem_name": "t_axiom",
            "status": "AXIOM_BACKED",
            "proof_method": "domain_axiom",
            "axiom_debt": ["paper_symbol:C_T"],
        }
    ]
    m = _closure_metrics(entries)
    assert m["axiom_backed_count"] == 1
    assert m["axiom_backed"][0]["result_label"] == "proved_modulo_paper_local_axioms"
    assert m["axiom_backed"][0]["modulo_paper_local_axioms"] is True
    assert m["paper_local_axiom_disclosure"]["required"] is True
    assert m["paper_local_axiom_disclosure"]["axiom_debt"] == ["paper_symbol:C_T"]
    assert m["axiom_debt_burndown"]["axiom_backed_result_count"] == 1
    assert m["axiom_debt_burndown"]["ranked_axioms"][0]["needed_by"] == ["t_axiom"]
    assert m["missing_lemma_subledger"]["total_obligations"] == 1
    assert m["missing_lemma_subledger"]["attack_queue"][0]["debt_category"] == "easy_missing_definition"


def test_missing_lemma_subledger_reports_definition_stubs_separately() -> None:
    subledger = _build_missing_lemma_subledger(
        [
            {
                "theorem_name": "definition_noise",
                "status": "UNRESOLVED",
                "axiom_debt": ["paper_definition_stub:HSobolev"],
            },
            {
                "theorem_name": "theorem_obligation",
                "status": "UNRESOLVED",
                "axiom_debt": ["paper_theory_reference"],
            },
        ]
    )

    assert subledger["schema_version"] == "1.1.0"
    assert subledger["total_obligations"] == 1
    assert subledger["counts_by_category"]["easy_missing_definition"] == 0
    assert subledger["definition_stub_grounding"]["count"] == 1
    assert subledger["definition_stub_grounding"]["items"][0]["symbol"] == "HSobolev"
    assert subledger["definition_stub_grounding"]["items"][0]["proof_countable"] is False
    assert (
        subledger["definition_stub_grounding"]["items"][0]["grounding_metadata"]["paper_agnostic_rule_id"]
        == "definition_stub.mathlib_close_match"
    )
    assert subledger["attack_queue"][0]["debt_item"] == "paper_theory_reference"
    assert subledger["attack_queue"][0]["requires_verified_replacement"] is True


def test_closure_metrics_exposes_grounding_metadata_on_ledger_rows() -> None:
    row = {
        "theorem_name": "definition_noise",
        "status": "UNRESOLVED",
        "axiom_debt": ["paper_definition_stub:HSobolev"],
    }

    metrics = _closure_metrics([row])
    unresolved = metrics["unresolved"][0]

    assert unresolved["debt_tier"] == "definition_stub_grounding"
    assert unresolved["proof_value"] == "definition_grounding"
    assert unresolved["grounding_metadata"][0]["proof_countable"] is False
    assert metrics["deep_domain_obligations"]["total_obligations"] == 0


def test_audited_replacement_verified_requires_exact_recorded_statement_and_proof() -> None:
    row = {
        "ledger_role": "audited_core_replacement",
        "status": "FULLY_PROVEN",
        "proof_method": "lean_verified",
        "lean_statement": "theorem t__audited_core : True",
        "proof_text": "trivial",
        "trust_reference": (
            "audited_core_replacement:t_core;source_theorem:t;"
            "core_sha256:abc123;semantic_equivalence:verified;lean_verification:fresh"
        ),
        "validation_gates": {
            "ledger_records_audited_statement_proof_pair": True,
            "fresh_lean_verification_evidence": True,
            "claim_equivalent": True,
        },
        "auto_reliable_core": {"ledger_statement_verified_by_core": True},
        "audited_core_replacement": {
            "source_theorem": "t",
            "core_theorem_name": "t_core",
            "core_file": "Desol/PaperProofs/Auto/Paper.lean",
            "core_sha256": "abc123",
            "verification_method": "lake env lean",
            "lean_statement": "theorem t__audited_core : True",
            "proof_text": "trivial",
        },
    }

    assert _audited_replacement_verified(row) is True
    changed = dict(row)
    changed["lean_statement"] = "theorem t__audited_core : False"
    assert _audited_replacement_verified(changed) is False


def test_axiom_debt_classifier_covers_release_categories() -> None:
    assert (
        _classify_axiom_debt_item({"lean_statement": "theorem t : True"}, "paper_symbol:HSobolev")
        == "easy_missing_definition"
    )
    assert (
        _classify_axiom_debt_item({"lean_statement": "theorem t : True"}, "paper_definition_stub:HSobolev")
        == "easy_missing_definition"
    )
    assert (
        _classify_axiom_debt_item({"lean_statement": "theorem t : True"}, "paper_local_lemma:DyadicBlockBound")
        == "local_paper_lemma"
    )
    assert (
        _classify_axiom_debt_item({"lean_statement": "theorem t : True"}, "translation_repair_domain_assumption")
        == "local_paper_lemma"
    )
    assert (
        _classify_axiom_debt_item({"lean_statement": "theorem t : True"}, "paper_symbol:cutoff_solution")
        == "deep_domain_theory_gap"
    )
    assert (
        _classify_axiom_debt_item({"error_message": "unknown identifier Foo", "lean_statement": "theorem t : True"}, "Foo")
        == "missing_mathlib_theorem"
    )
    assert (
        _classify_axiom_debt_item({"lean_statement": "theorem t : Complex.abs z ≤ 1"}, "paper_symbol:C_T")
        == "bad_translation_artifact"
    )
    assert (
        _classify_axiom_debt_item(
            {
                "lean_statement": "theorem t (h : PaperClaim) : PaperClaim",
                "error_message": "translation_repaired:abstract_schema_placeholder_to_paper_claim",
            },
            "translation_repair_domain_assumption",
        )
        == "bad_translation_artifact"
    )


def test_apply_validated_translation_repairs_marks_paper_claim_abstractions_diagnostic() -> None:
    repaired, applied = _apply_validated_translation_repairs(
        [{"theorem_name": "ArxivPaper.t", "status": "FLAWED", "lean_statement": "theorem t : True"}],
        {
            "repair_candidates": [
                {
                    "theorem_name": "t",
                    "repaired_decl": "theorem t (h_t_paper_claim : TPaperClaim) : TPaperClaim := by\n  sorry",
                    "changes": ["abstract_schema_placeholder_to_paper_claim", "insert_domain_lemma_assumption"],
                    "repair_abstraction_kind": "paper_claim_diagnostic",
                    "direct_tactic": "exact h_t_paper_claim",
                    "domain_assumption_backed": True,
                    "lean_validation": {"ok": True},
                }
            ]
        },
    )

    assert applied["updated_count"] == 1
    assert repaired[0]["status"] == "FLAWED"
    assert repaired[0]["proved"] is False
    assert repaired[0]["proof_method"] == "translation_repair_diagnostic"
    assert repaired[0]["repair_abstraction_kind"] == "paper_claim_diagnostic"
    assert repaired[0]["validation_gates"]["paper_claim_diagnostic"] is True
    assert repaired[0]["result_label"] == "not_verified_translation_repair_domain_assumption"
    assert repaired[0]["axiom_debt"] == ["translation_repair_domain_assumption"]


def test_missing_lemma_subledger_sorts_easy_debts_first() -> None:
    subledger = _build_missing_lemma_subledger(
        [
            {
                "theorem_name": "hard",
                "status": "AXIOM_BACKED",
                "proof_method": "domain_axiom",
                "axiom_debt": ["paper_symbol:cutoff_solution"],
            },
            {
                "theorem_name": "easy",
                "status": "AXIOM_BACKED",
                "proof_method": "domain_axiom",
                "axiom_debt": ["paper_symbol:C_T"],
            },
        ]
    )
    assert subledger["counts_by_category"]["easy_missing_definition"] == 1
    assert subledger["counts_by_category"]["deep_domain_theory_gap"] == 1
    assert subledger["attack_queue"][0]["theorem_name"] == "easy"


def test_translation_limited_reason_detects_placeholders() -> None:
    assert (
        _translation_limited_reason("theorem t (p_c1 : Prop) (h_c1 : p_c1) : p_c1 := by\n  sorry")
        == "schema_placeholder_identity"
    )
    assert _translation_limited_reason("theorem t : (0 : ℕ) = 0 := by\n  sorry") == "trivial_nat0eq0_target"
    assert (
        _translation_limited_reason(
            "theorem t (h1 : Prop) (h2 : Prop) : h1 ∧ h2 → (0 : ℕ) = 0 := by\n  sorry"
        )
        == "relaxed_prop_trivial_nat_implication"
    )


def test_normalize_final_ledger_entries_reclassifies_placeholders_and_semantic_blocks() -> None:
    entries = [
        {"theorem_name": "P", "status": "UNRESOLVED", "lean_statement": "theorem P (p_c1 : Prop) (h_c1 : p_c1) : p_c1"},
        {"theorem_name": "S", "status": "UNRESOLVED", "lean_statement": "theorem S : Nat = Nat", "error_message": "semantic_policy_hard_block"},
        {"theorem_name": "W", "status": "UNRESOLVED", "lean_statement": "theorem W (C : ℝ) (h_easy : C ≤ C) : C ≤ C"},
        {"theorem_name": "V", "status": "FULLY_PROVEN", "proof_method": "lean_verified", "lean_statement": "theorem V : True"},
    ]
    rows, counts = _normalize_final_ledger_entries(entries)
    assert rows[0]["status"] == "TRANSLATION_LIMITED"
    assert rows[0]["proof_method"] == "translation_limited"
    assert rows[1]["status"] == "FLAWED"
    assert rows[2]["status"] == "FLAWED"
    assert rows[2]["error_message"] == "final_weakened_statement:claim_copied_into_hypothesis:h_easy"
    assert rows[3]["status"] == "FULLY_PROVEN"
    assert counts["translation_limited_placeholders"] == 1
    assert counts["semantic_hard_flawed"] == 1
    assert counts["weakened_statement_flawed"] == 1


def test_apply_validated_translation_repairs_updates_only_changed_elaborating_candidates() -> None:
    rows = [
        {"theorem_name": "ArxivPaper.bad", "status": "FLAWED", "lean_statement": "theorem bad : Complex.abs z ≤ 1"},
        {"theorem_name": "ArxivPaper.same", "status": "FLAWED", "lean_statement": "theorem same : True"},
    ]
    repaired, applied = _apply_validated_translation_repairs(
        rows,
        {
            "repair_theory": "Desol/PaperTheory/Repair/Paper_x.lean",
            "repair_candidates": [
                {
                    "theorem_name": "bad",
                    "repaired_decl": "theorem bad : True := by\n  sorry",
                    "changes": ["rewrite_bad_notation"],
                    "lean_validation": {"ok": True},
                },
                {
                    "theorem_name": "same",
                    "repaired_decl": "theorem same : True := by\n  sorry",
                    "changes": [],
                    "lean_validation": {"ok": True},
                },
            ],
        },
    )

    assert applied["updated_count"] == 1
    assert repaired[0]["status"] == "UNRESOLVED"
    assert repaired[0]["validation_gates"]["translation_repair_applied"] is True
    assert repaired[1]["status"] == "FLAWED"


def test_apply_validated_translation_repairs_marks_faithful_regeneration_as_paper_theory_debt() -> None:
    repaired, applied = _apply_validated_translation_repairs(
        [
            {
                "theorem_name": "ArxivPaper.t",
                "status": "FLAWED",
                "lean_statement": "theorem t (h_t_paper_claim : TPaperClaim) : TPaperClaim",
                "axiom_debt": ["translation_repair_domain_assumption"],
            }
        ],
        {
            "repair_candidates": [
                {
                    "theorem_name": "t",
                    "repaired_decl": "theorem t : TRegeneratedStatement := by\n  sorry",
                    "changes": ["regenerate_faithful_statement", "use_paper_theory_statement_symbol"],
                    "statement_repair_kind": "faithful_statement_regeneration",
                    "paper_theory_debt": ["paper_theory_reference"],
                    "lean_validation": {"ok": True},
                }
            ]
        },
    )

    assert applied["updated_count"] == 1
    assert repaired[0]["status"] == "UNRESOLVED"
    assert repaired[0]["proved"] is False
    assert repaired[0]["proof_method"] == "translation_repaired_pending_proof"
    assert repaired[0]["axiom_debt"] == ["paper_theory_reference"]
    assert repaired[0]["validation_gates"]["repair_outcome"] == "faithful_statement_regeneration"
    assert repaired[0]["translation_repair"]["statement_repair_kind"] == "faithful_statement_regeneration"


def test_apply_validated_translation_repairs_blocks_direct_domain_assumption_repairs() -> None:
    repaired, applied = _apply_validated_translation_repairs(
        [{"theorem_name": "ArxivPaper.t", "status": "FLAWED", "lean_statement": "theorem t : True"}],
        {
            "repair_candidates": [
                {
                    "theorem_name": "t",
                    "repaired_decl": "theorem t (h_domain : True) : True := by\n  exact h_domain",
                    "changes": ["insert_domain_lemma_assumption"],
                    "direct_tactic": "exact h_domain",
                    "domain_assumption_backed": True,
                    "lean_validation": {"ok": True},
                }
            ]
        },
    )

    assert applied["updated_count"] == 1
    assert repaired[0]["status"] == "FLAWED"
    assert repaired[0]["proved"] is False
    assert repaired[0]["proof_method"] == "translation_repair_diagnostic"
    assert repaired[0]["result_label"] == "not_verified_translation_repair_domain_assumption"
    assert repaired[0]["closure_claim"] == "not_closed"
    assert repaired[0]["error_message"].startswith("translation_repair_domain_assumption_inserted")
    assert repaired[0]["modulo_paper_local_axioms"] is False
    assert repaired[0]["validation_gates"]["lean_proof_closed"] is False
    assert repaired[0]["validation_gates"]["repair_outcome"] == "domain_assumption_inserted"


def test_apply_validated_translation_repairs_marks_direct_existing_hypothesis_axiom_backed() -> None:
    repaired, applied = _apply_validated_translation_repairs(
        [{"theorem_name": "ArxivPaper.t", "status": "UNRESOLVED", "lean_statement": "theorem t : True"}],
        {
            "repair_candidates": [
                {
                    "theorem_name": "t",
                    "repaired_decl": "theorem t (h : True) : True := by\n  exact h",
                    "changes": [],
                    "direct_tactic": "exact h",
                    "direct_proof_without_repair": True,
                    "lean_validation": {"ok": True},
                }
            ]
        },
    )

    assert applied["updated_count"] == 1
    assert repaired[0]["status"] == "AXIOM_BACKED"
    assert repaired[0]["proof_method"] == "domain_axiom"
    assert repaired[0]["result_label"] == "proved_modulo_paper_local_axioms"
    assert repaired[0]["closure_claim"] == "proved_modulo_paper_local_axioms"
    assert repaired[0]["error_message"] == "direct_paper_statement_proved_modulo_paper_local_axioms"
    assert repaired[0]["validation_gates"]["direct_proof_without_repair"] is True


def test_apply_auto_reliable_core_promotions_records_evidence_without_gate_bypass(tmp_path) -> None:
    core = tmp_path / "Paper_x.lean"
    core.write_text("theorem auto_t : True := by\n  trivial\n", encoding="utf-8")

    rows, promoted = _apply_auto_reliable_core_promotions(
        [{"theorem_name": "t", "status": "UNRESOLVED", "lean_statement": "theorem t : True"}],
        {
            "ok": True,
            "out": str(core),
            "theorem_count": 1,
            "theorems": [{"source_theorem": "t", "theorem_name": "auto_t", "tactic": "trivial"}],
        },
    )

    assert promoted["promoted_count"] == 0
    assert promoted["strict_gate_blocked_count"] == 1
    assert rows[0]["status"] == "INTERMEDIARY_PROVEN"
    assert rows[0]["proof_method"] == "auto_reliable_core_evidence"
    assert rows[0]["proved"] is False
    assert rows[0]["validation_gates"]["auto_reliable_core_verified"] is False
    assert rows[0]["validation_gates"]["claim_equivalent"] is False
    assert "claim_equivalent" in rows[0]["gate_failures"]
    assert "independent_semantic_equivalence_evidence" in rows[0]["gate_failures"]
    assert "fresh_lean_verification_evidence" in rows[0]["gate_failures"]
    assert rows[0]["closure_claim"] == "not_closed"


def test_apply_auto_reliable_core_promotions_requires_strict_evidence_for_full(tmp_path) -> None:
    core = tmp_path / "Paper_x.lean"
    core.write_text("theorem auto_t : True := by\n  trivial\n", encoding="utf-8")

    rows, promoted = _apply_auto_reliable_core_promotions(
        [
            {
                "theorem_name": "t",
                "status": "UNRESOLVED",
                "lean_statement": "theorem t : True",
                "translation_validated": True,
                "translation_fidelity_score": 0.95,
                "status_alignment_score": 0.95,
                "translation_uncertainty_flags": ["semantic_equivalence:verified"],
                "translation_adversarial_flags": [],
                "translation_roundtrip_flags": [],
                "provenance": {"paper_id": "x", "section": "1"},
                "validation_gates": {"reproducible_env": True},
            }
        ],
        {
            "ok": True,
            "out": str(core),
            "theorem_count": 1,
            "lean_verification": {
                "ok": True,
                "core_file": str(core),
                "core_sha256": hashlib.sha256(core.read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
                "verified_at": 1.0,
            },
            "theorems": [
                {
                    "source_theorem": "t",
                    "theorem_name": "auto_t",
                    "tactic": "trivial",
                }
            ],
        },
    )

    assert promoted["promoted_count"] == 1
    assert rows[0]["status"] == "FULLY_PROVEN"
    assert rows[0]["proof_method"] == "lean_verified"
    assert rows[0]["proved"] is True
    assert rows[0]["validation_gates"]["auto_reliable_core_verified"] is True
    assert rows[0]["validation_gates"]["claim_equivalent"] is True
    assert rows[0]["validation_gates"]["fresh_lean_verification_evidence"] is True
    assert rows[0]["validation_gates"]["independent_semantic_equivalence_evidence"] is True
    assert rows[0]["promotion_gate_passed"] is True
    assert rows[0]["closure_claim"] == "lean_verified_without_paper_local_axioms"
    assert "gate_failures" not in rows[0]["trust_reference"]
    assert "theorem_not_verified" not in rows[0]["trust_reference"]
    assert rows[0]["auto_reliable_core"]["strict_gate_passed"] is True


def test_apply_auto_reliable_core_promotions_replaces_axiom_backed_repair_with_audited_row(tmp_path) -> None:
    core = tmp_path / "Paper_x.lean"
    core.write_text("theorem auto_t : True := by\n  trivial\n", encoding="utf-8")
    core_hash = hashlib.sha256(core.read_text(encoding="utf-8").encode("utf-8")).hexdigest()

    rows, promoted = _apply_auto_reliable_core_promotions(
        [
            {
                "theorem_name": "t",
                "status": "AXIOM_BACKED",
                "lean_statement": "theorem t : True",
                "proof_text": "exact h_t",
                "translation_validated": True,
                "translation_fidelity_score": 0.95,
                "status_alignment_score": 0.95,
                "provenance": {"paper_id": "x", "section": "1"},
                "validation_gates": {"reproducible_env": True},
                "axiom_debt": ["translation_repair_domain_assumption"],
                "modulo_paper_local_axioms": True,
                "closure_claim": "proved_modulo_paper_local_axioms",
            }
        ],
        {
            "ok": True,
            "out": str(core),
            "theorem_count": 1,
            "lean_verification": {
                "ok": True,
                "core_file": str(core),
                "core_sha256": core_hash,
                "verified_at": 1.0,
            },
            "theorems": [
                {
                    "source_theorem": "t",
                    "theorem_name": "auto_t",
                    "tactic": "trivial",
                    "lean_statement": "theorem auto_t : True",
                    "proof_text": "trivial",
                    "core_declaration": "theorem auto_t : True := by\n  trivial",
                    "semantic_equivalence_verified": True,
                    "claim_equivalence_verdict": "equivalent",
                    "semantic_equivalence": {"independent": True, "verdict": "equivalent"},
                    "supersedes_paper_axiom_debt": True,
                    "translation_fidelity_score": 1.0,
                    "status_alignment_score": 1.0,
                }
            ],
        },
    )

    metrics = _closure_metrics(rows)
    assert promoted["promoted_count"] == 0
    assert promoted["audited_core_replacement_count"] == 1
    assert rows[0]["superseded_by_audited_core"] is True
    assert rows[0]["result_label"] == "superseded_generated_diagnostic"
    assert rows[1]["ledger_role"] == "audited_core_replacement"
    assert rows[1]["source_theorem"] == "t"
    assert rows[1]["status"] == "FULLY_PROVEN"
    assert rows[1]["proof_method"] == "lean_verified"
    assert rows[1]["audited_core_replacement"]["core_sha256"] == core_hash
    assert metrics["verified_proven"] == 1
    assert metrics["audited_core_replacement_count"] == 1
    assert metrics["audited_core_replacements"][0]["proof_countable"] is True
    assert metrics["audited_core_replacements"][0]["replacement_gate"]["exact_recorded_statement_required"] is True
    assert metrics["axiom_backed_count"] == 0
    assert metrics["superseded_diagnostic_count"] == 1


def test_apply_auto_reliable_core_promotions_records_replacement_without_overwriting_generated_row(tmp_path) -> None:
    core = tmp_path / "Paper_x.lean"
    core.write_text("theorem auto_t : True := by\n  trivial\n", encoding="utf-8")

    rows, promoted = _apply_auto_reliable_core_promotions(
        [
            {
                "theorem_name": "t",
                "status": "AXIOM_BACKED",
                "lean_statement": "theorem t : True",
                "proof_text": "exact h_t",
                "translation_validated": True,
                "translation_fidelity_score": 0.2,
                "status_alignment_score": 0.2,
                "translation_uncertainty_flags": ["weaker_than_paper"],
                "translation_adversarial_flags": [],
                "translation_roundtrip_flags": [],
                "provenance": {"paper_id": "x", "section": "1"},
                "validation_gates": {"reproducible_env": True},
                "axiom_debt": ["translation_repair_domain_assumption"],
                "modulo_paper_local_axioms": True,
                "closure_claim": "proved_modulo_paper_local_axioms",
            }
        ],
        {
            "ok": True,
            "out": str(core),
            "theorem_count": 1,
            "lean_verification": {
                "ok": True,
                "core_file": str(core),
                "core_sha256": hashlib.sha256(core.read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
                "verified_at": 1.0,
            },
            "theorems": [
                {
                    "source_theorem": "t",
                    "theorem_name": "auto_t",
                    "tactic": "trivial",
                    "lean_statement": "theorem auto_t : True",
                    "proof_text": "trivial",
                    "core_declaration": "theorem auto_t : True := by\n  trivial",
                    "semantic_equivalence_verified": True,
                    "claim_equivalence_verdict": "equivalent",
                    "semantic_equivalence": {"independent": True, "verdict": "equivalent"},
                    "supersedes_paper_axiom_debt": True,
                    "translation_fidelity_score": 1.0,
                    "status_alignment_score": 1.0,
                }
            ],
        },
    )

    assert promoted["promoted_count"] == 0
    assert promoted["audited_core_replacement_count"] == 1
    assert promoted["audited_reliable_core_evidence_only_count"] == 1
    assert rows[0]["status"] == "AXIOM_BACKED"
    assert rows[0]["proof_method"] == "auto_reliable_core_evidence"
    assert rows[0]["proved"] is False
    assert rows[0]["superseded_by_audited_core"] is True
    assert rows[1]["ledger_role"] == "audited_core_replacement"
    assert rows[1]["status"] == "FULLY_PROVEN"
    assert rows[0]["claim_equivalence_verdict"] == "equivalent"
    assert rows[0]["auto_reliable_core"]["audited_equivalence_applied"] is True
    assert rows[0]["auto_reliable_core"]["ledger_statement_verified_by_core"] is False
    assert rows[0]["validation_gates"]["claim_equivalent"] is True
    assert rows[0]["validation_gates"]["ledger_statement_verified_by_core"] is False
    assert "generated_row_has_axiom_or_domain_assumption_debt" in rows[0]["gate_failures"]


def test_apply_auto_reliable_core_promotions_never_promotes_paper_claim_rows(tmp_path) -> None:
    core = tmp_path / "Paper_x.lean"
    core.write_text("theorem auto_t : True := by\n  trivial\n", encoding="utf-8")

    rows, promoted = _apply_auto_reliable_core_promotions(
        [
            {
                "theorem_name": "t",
                "status": "FLAWED",
                "lean_statement": "theorem t (h_t_paper_claim : TPaperClaim) : TPaperClaim",
                "repair_abstraction_kind": "paper_claim_diagnostic",
                "translation_validated": True,
                "translation_fidelity_score": 1.0,
                "status_alignment_score": 1.0,
                "translation_uncertainty_flags": ["semantic_equivalence:verified"],
                "provenance": {"paper_id": "x", "section": "1"},
                "validation_gates": {"reproducible_env": True},
            }
        ],
        {
            "ok": True,
            "out": str(core),
            "theorem_count": 1,
            "lean_verification": {
                "ok": True,
                "core_file": str(core),
                "core_sha256": hashlib.sha256(core.read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
                "verified_at": 1.0,
            },
            "theorems": [{"source_theorem": "t", "theorem_name": "auto_t", "tactic": "trivial"}],
        },
    )

    assert promoted["promoted_count"] == 0
    assert rows[0]["status"] == "FLAWED"
    assert rows[0]["proved"] is False
    assert rows[0]["auto_reliable_core"]["ledger_statement_verified_by_core"] is False
    assert "ledger_statement_not_verified_by_core" in rows[0]["gate_failures"]


def test_apply_auto_reliable_core_promotions_handles_multiple_audited_sources(tmp_path) -> None:
    core = tmp_path / "Paper_x.lean"
    core.write_text(
        "theorem auto_def_admissible : True := by\n  trivial\n\n"
        "theorem auto_remark_20 : True := by\n  trivial\n",
        encoding="utf-8",
    )
    core_hash = hashlib.sha256(core.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    rows, promoted = _apply_auto_reliable_core_promotions(
        [
            {
                "theorem_name": "def_admissible",
                "status": "FLAWED",
                "lean_statement": "theorem def_admissible : True",
                "translation_validated": True,
                "translation_fidelity_score": 0.1,
                "status_alignment_score": 0.1,
                "translation_uncertainty_flags": ["stronger_than_paper"],
                "provenance": {"paper_id": "2604.21884", "section": "2"},
                "validation_gates": {"reproducible_env": True},
                "axiom_debt": ["translation_repair_domain_assumption"],
                "closure_claim": "proved_modulo_paper_local_axioms",
                "modulo_paper_local_axioms": True,
            },
            {
                "theorem_name": "remark_20",
                "status": "INTERMEDIARY_PROVEN",
                "lean_statement": "theorem remark_20 : True",
                "translation_validated": True,
                "translation_fidelity_score": 0.1,
                "status_alignment_score": 0.1,
                "translation_uncertainty_flags": ["weaker_than_paper"],
                "provenance": {"paper_id": "2604.21884", "section": "2"},
                "validation_gates": {"reproducible_env": True},
            },
        ],
        {
            "ok": True,
            "out": str(core),
            "theorem_count": 2,
            "lean_verification": {
                "ok": True,
                "core_file": str(core),
                "core_sha256": core_hash,
                "verified_at": 1.0,
            },
            "theorems": [
                {
                    "source_theorem": "def_admissible",
                    "theorem_name": "auto_def_admissible",
                    "tactic": "trivial",
                    "lean_statement": "theorem auto_def_admissible : True",
                    "proof_text": "trivial",
                    "core_declaration": "theorem auto_def_admissible : True := by\n  trivial",
                    "semantic_equivalence_verified": True,
                    "claim_equivalence_verdict": "equivalent",
                    "semantic_equivalence": {"independent": True, "verdict": "equivalent"},
                    "supersedes_paper_axiom_debt": True,
                    "translation_fidelity_score": 1.0,
                    "status_alignment_score": 1.0,
                },
                {
                    "source_theorem": "remark_20",
                    "theorem_name": "auto_remark_20",
                    "tactic": "trivial",
                    "lean_statement": "theorem auto_remark_20 : True",
                    "proof_text": "trivial",
                    "core_declaration": "theorem auto_remark_20 : True := by\n  trivial",
                    "semantic_equivalence_verified": True,
                    "claim_equivalence_verdict": "equivalent",
                    "semantic_equivalence": {"independent": True, "verdict": "equivalent"},
                    "supersedes_paper_axiom_debt": True,
                    "translation_fidelity_score": 1.0,
                    "status_alignment_score": 1.0,
                },
            ],
        },
    )

    metrics = _closure_metrics(rows)
    assert promoted["eligible_reliable_core_count"] == 2
    assert promoted["promoted_count"] == 0
    assert promoted["audited_core_replacement_count"] == 2
    assert promoted["audited_reliable_core_evidence_only_count"] == 2
    assert promoted["audited_reliable_core_evidence_only"][0]["reason"] == (
        "audited_core_does_not_verify_recorded_ledger_statement"
    )
    assert rows[0]["auto_reliable_core"]["audited_equivalence_applied"] is True
    assert rows[0]["auto_reliable_core"]["ledger_statement_verified_by_core"] is False
    assert rows[1]["auto_reliable_core"]["audited_equivalence_applied"] is True
    assert rows[1]["auto_reliable_core"]["ledger_statement_verified_by_core"] is False
    assert rows[2]["ledger_role"] == "audited_core_replacement"
    assert rows[3]["ledger_role"] == "audited_core_replacement"
    assert metrics["verified_proven"] == 2
    assert metrics["audited_core_replacement_count"] == 2
    assert metrics["superseded_diagnostic_count"] == 2


def test_apply_auto_reliable_core_promotions_blocks_inconsistent_closure_claim(tmp_path) -> None:
    core = tmp_path / "Paper_x.lean"
    core.write_text("theorem auto_t : True := by\n  trivial\n", encoding="utf-8")

    rows, promoted = _apply_auto_reliable_core_promotions(
        [
            {
                "theorem_name": "t",
                "status": "UNRESOLVED",
                "lean_statement": "theorem t : True",
                "translation_validated": True,
                "translation_fidelity_score": 0.95,
                "status_alignment_score": 0.95,
                "translation_uncertainty_flags": ["semantic_equivalence:verified"],
                "translation_adversarial_flags": [],
                "translation_roundtrip_flags": [],
                "provenance": {"paper_id": "x", "section": "1"},
                "validation_gates": {"reproducible_env": True},
                "closure_claim": "proved_modulo_paper_local_axioms",
                "axiom_debt": ["paper_symbol:C_T"],
            }
        ],
        {
            "ok": True,
            "out": str(core),
            "theorem_count": 1,
            "lean_verification": {
                "ok": True,
                "core_file": str(core),
                "core_sha256": hashlib.sha256(core.read_text(encoding="utf-8").encode("utf-8")).hexdigest(),
                "verified_at": 1.0,
            },
            "theorems": [{"source_theorem": "t", "theorem_name": "auto_t", "tactic": "trivial"}],
        },
    )

    assert promoted["promoted_count"] == 0
    assert rows[0]["status"] == "INTERMEDIARY_PROVEN"
    assert "consistent_closure_claim" in rows[0]["gate_failures"]
    assert rows[0]["promotion_gate_passed"] is False


def test_apply_auto_reliable_core_promotions_blocks_axiom_core(tmp_path) -> None:
    core = tmp_path / "Paper_x.lean"
    core.write_text("axiom ax : True\ntheorem auto_t : True := by\n  exact ax\n", encoding="utf-8")

    rows, promoted = _apply_auto_reliable_core_promotions(
        [{"theorem_name": "t", "status": "UNRESOLVED", "lean_statement": "theorem t : True"}],
        {
            "ok": True,
            "out": str(core),
            "theorem_count": 1,
            "theorems": [{"source_theorem": "t", "theorem_name": "auto_t", "tactic": "exact ax"}],
        },
    )

    assert promoted["promoted_count"] == 0
    assert rows[0]["status"] == "UNRESOLVED"


def test_dedupe_final_ledger_entries_prefers_verified_alias() -> None:
    entries = [
        {"theorem_name": "T", "status": "UNRESOLVED", "proof_method": "unknown"},
        {"theorem_name": "ArxivPaper.T", "status": "FULLY_PROVEN", "proof_method": "lean_verified"},
        {"theorem_name": "U", "status": "TRANSLATION_LIMITED", "proof_method": "translation_limited"},
    ]
    rows = _dedupe_final_ledger_entries(entries)
    assert len(rows) == 2
    assert rows[0]["theorem_name"] == "ArxivPaper.T"
    assert rows[1]["theorem_name"] == "U"


def test_definition_like_bad_statement_reason_detects_unconstrained_equality() -> None:
    decl = "theorem Def_Simple {α : Type*} (a : Multiset (List ℕ)) (b e n : ℕ) : a = {[b, e]} := by\n  sorry"
    assert _definition_like_bad_statement_reason(decl) == "definition_like_unconstrained_equality"


def test_weakened_by_target_hypothesis_reason_detects_easy_hypothesis() -> None:
    decl = "theorem t (C : ℝ) (h_bound : C ≤ C) : C ≤ C := by\n  exact h_bound"
    assert _weakened_by_target_hypothesis_reason(decl) == "claim_copied_into_hypothesis:h_bound"


def test_ill_typed_translation_artifact_reason_detects_latex_leftovers() -> None:
    assert _ill_typed_translation_artifact_reason("theorem t : B_N^{i;j,k} = 0") == "latex_superscript_artifact"
    assert _ill_typed_translation_artifact_reason("theorem t (h : |ℓ| ~ N) : True") == "latex_asymptotic_artifact"
    assert _ill_typed_translation_artifact_reason("theorem t : f ∈ C_T HSobolev s") == "bare_function_space_application"
    assert _ill_typed_translation_artifact_reason("theorem t : Complex.abs z ≤ 1") == "non_mathlib_complex_abs_artifact"
    assert _ill_typed_translation_artifact_reason("theorem t (h : (V1 : V1) = x) : True") == "type_name_used_as_term"


def test_blocker_clusters_groups_final_statuses() -> None:
    clusters = _blocker_clusters(
        [
            {"theorem_name": "P", "status": "TRANSLATION_LIMITED", "gate_failures": ["translation_limited_statement"]},
            {"theorem_name": "A", "status": "AXIOM_BACKED", "gate_failures": ["no_paper_axiom_debt"]},
            {"theorem_name": "S", "status": "FLAWED", "error_message": "final_semantic_hard_block"},
            {"theorem_name": "Q", "status": "UNRESOLVED", "gate_failures": ["lean_proof_closed"]},
            {
                "theorem_name": "D",
                "status": "UNRESOLVED",
                "axiom_debt": ["paper_definition_stub:HSobolev"],
            },
        ]
    )
    assert clusters["translation_limited_placeholder_or_schema"]["theorems"] == ["P"]
    assert clusters["closed_modulo_paper_local_axioms"]["theorems"] == ["A"]
    assert clusters["semantic_fidelity_hard_block"]["theorems"] == ["S"]
    assert clusters["proof_search_gap"]["theorems"] == ["Q"]
    assert clusters["paper_theory_debt_definition_stub_grounding"]["theorems"] == ["D"]


def test_axiomize_decl_for_paper_local_strips_proof_and_namespace() -> None:
    decl = "theorem ArxivPaper.T (n : ℕ) : n = n := by\n  rfl"
    assert _axiomize_decl_for_paper_local(decl, "ArxivPaper.T") == "axiom T (n : ℕ) : n = n"


def test_write_paper_local_theory_file_skips_auto_closed(tmp_path) -> None:
    entries = [
        {
            "theorem_name": "ArxivPaper.T",
            "status": "FULLY_PROVEN",
            "proof_method": "lean_verified",
            "lean_statement": "theorem ArxivPaper.T (n : ℕ) : n = n := by\n  rfl",
        },
        {
            "theorem_name": "ArxivPaper.P",
            "status": "FULLY_PROVEN",
            "proof_method": "auto_closed",
            "lean_statement": "theorem ArxivPaper.P : True := by\n  trivial",
        },
    ]
    out = _write_paper_local_theory_file(project_root=tmp_path, paper_id="2304.09598", entries=entries)
    assert out is not None
    text = out.read_text(encoding="utf-8")
    assert "axiom T (n : ℕ) : n = n" in text
    assert "axiom P" not in text


def test_detect_curated_paper_package_counts_theorems_and_axioms(tmp_path) -> None:
    package = tmp_path / "paper_2304.09598"
    package.mkdir()
    (package / "proofs.lean").write_text(
        "axiom domain_ax : True\n"
        "theorem T : True := by\n"
        "  trivial\n",
        encoding="utf-8",
    )
    info = _detect_curated_paper_package(tmp_path, "2304.09598")
    assert info["available"] is True
    assert info["theorem_count"] == 1
    assert info["axiom_count"] == 1
    assert info["sorry_count"] == 0


def test_detect_curated_paper_package_finds_desol_paper_proofs(tmp_path) -> None:
    package = tmp_path / "Desol" / "PaperProofs"
    package.mkdir(parents=True)
    (package / "Paper_2604_21884.lean").write_text(
        "theorem T : True := by\n"
        "  trivial\n",
        encoding="utf-8",
    )
    info = _detect_curated_paper_package(tmp_path, "2604.21884")
    assert info["available"] is True
    assert info["theorem_count"] == 1
    assert info["axiom_count"] == 0
    assert info["sorry_count"] == 0

