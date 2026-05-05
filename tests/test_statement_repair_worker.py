from __future__ import annotations

import sys
import types
from pathlib import Path

import run_statement_repair_worker as worker


def _queue_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "row_id": "r1",
        "arxiv_id": "2604.21314",
        "theorem_id": "thm:bad",
        "canonical_theorem_id": "bad",
        "priority_score": 50,
        "repair_route": "statement_regeneration",
        "repair_kind": "replace_placeholder_statement",
        "repair_reasons": ["placeholder_or_trivial_lean_statement"],
        "status": "FLAWED",
        "source_span_quality": "extractor_native",
        "source_latex": "A nontrivial theorem.",
        "lean_statement": "theorem bad : True",
        "artifact_paths": {},
    }
    row.update(overrides)
    return row


def _action(**overrides: object) -> dict[str, object]:
    action: dict[str, object] = {
        "paper_id": "2604.21314",
        "repair_route": "source_span_repair",
        "repair_kind": "repair_source_span_provenance",
        "write_capable": True,
        "input_rows": 1,
        "row_ids": ["r1"],
        "theorem_ids": ["thm:bad"],
        "artifacts": {"extracted_theorems": "evidence.json"},
        "status": "planned",
    }
    action.update(overrides)
    return action


def test_worker_groups_dry_run_actions_by_paper_route_and_kind() -> None:
    actions, summary = worker.build_worker_actions(
        [
            _queue_row(row_id="r1", theorem_id="a"),
            _queue_row(row_id="r2", theorem_id="b"),
            _queue_row(
                row_id="r3",
                theorem_id="c",
                repair_route="source_span_repair",
                repair_kind="repair_source_span_provenance",
            ),
        ],
        limit=10,
    )

    assert len(actions) == 2
    assert sorted(action["input_rows"] for action in actions) == [1, 2]
    assert summary["action_groups"] == 2
    assert summary["write"] is False


def test_worker_filters_queue_rows_by_route_and_kind() -> None:
    rows = [
        _queue_row(row_id="r1", repair_route="statement_regeneration", repair_kind="replace_placeholder_statement"),
        _queue_row(row_id="r2", repair_route="source_span_repair", repair_kind="repair_source_span_provenance"),
        _queue_row(row_id="r3", repair_route="source_span_repair", repair_kind="other_span_kind"),
    ]

    filtered = worker._filter_queue_rows(
        rows,
        repair_route="source_span_repair",
        repair_kind="repair_source_span_provenance",
    )

    assert [row["row_id"] for row in filtered] == ["r2"]


def test_worker_filters_queue_rows_by_paper_route_and_kind() -> None:
    rows = [
        _queue_row(row_id="r1", arxiv_id="2604.21583", repair_kind="replace_placeholder_statement"),
        _queue_row(row_id="r2", arxiv_id="2304.09598", repair_kind="replace_placeholder_statement"),
        _queue_row(row_id="r3", arxiv_id="2604.21583", repair_kind="regenerate_flawed_statement"),
    ]

    filtered = worker._filter_queue_rows(
        rows,
        paper_id="2604.21583",
        repair_route="statement_regeneration",
        repair_kind="replace_placeholder_statement",
    )

    assert [row["row_id"] for row in filtered] == ["r1"]


def test_worker_action_targets_include_source_context_lean_names() -> None:
    action = _action(
        theorem_ids=["lem:NQ-double-commutator-direct"],
        source_contexts=[
            {
                "theorem_id": "lem:NQ-double-commutator-direct",
                "lean_statement": "theorem lem_NQ_double_commutator_direct : True := by\n  trivial",
            }
        ],
    )

    targets = worker._action_target_names(action)

    assert "lem:NQ-double-commutator-direct" in targets
    assert "lem_NQ_double_commutator_direct" in targets


def test_worker_write_keeps_non_write_routes_queued() -> None:
    executed = worker.execute_worker_actions(
        [
            _action(
                repair_route="source_alignment_review",
                repair_kind="adjudicate_source_match",
                write_capable=False,
            )
        ],
        project_root=Path("."),
        write=True,
        max_write_groups=1,
        repair_output_root=Path("output/test"),
        validate_candidates=True,
    )

    assert executed[0]["status"] == "queued_source_match_adjudication"
    assert executed[0]["mutated"] is False
    assert executed[0]["mutated_rows"] == 0


def test_worker_respects_max_write_groups_for_mutations(monkeypatch) -> None:
    def fake_span_repair(action: dict[str, object], *, project_root: Path, write: bool) -> dict[str, object]:
        return {**action, "status": "written_source_span_repair", "wrote": True, "mutated": True, "mutated_rows": 1}

    monkeypatch.setattr(worker, "_execute_source_span_repair", fake_span_repair)

    executed = worker.execute_worker_actions(
        [_action(row_ids=["r1"]), _action(row_ids=["r2"])],
        project_root=Path("."),
        write=True,
        max_write_groups=1,
        repair_output_root=Path("output/test"),
        validate_candidates=True,
    )

    assert executed[0]["status"] == "written_source_span_repair"
    assert executed[1]["status"] == "skipped_write_limit"
    assert executed[1]["mutated"] is False


def test_source_translation_recovery_dry_run_reports_retranslation_policy() -> None:
    executed = worker.execute_worker_actions(
        [
            _action(
                repair_route="source_translation_recovery",
                repair_kind="recover_translation_from_source",
                write_capable=True,
            )
        ],
        project_root=Path("."),
        write=False,
        max_write_groups=1,
        repair_output_root=Path("output/test"),
        validate_candidates=True,
    )

    assert executed[0]["status"] == "dry_run_source_retranslation_required"
    assert executed[0]["mutated"] is False
    assert executed[0]["translation_recovery_policy"]["proof_promotion"] is False


def test_statement_regeneration_only_counts_written_when_ledger_changes(monkeypatch, tmp_path: Path) -> None:
    fake_module = types.SimpleNamespace(
        build_repair_pack=lambda **_kwargs: {"candidate_counts": {"changed_elaborating": 0}, "repair_candidates": []}
    )
    monkeypatch.setitem(sys.modules, "repair_bad_translations", fake_module)
    monkeypatch.setattr(
        worker,
        "apply_validated_repair_pack_to_ledger",
        lambda **kwargs: {"ok": True, "updated_count": 0, "wrote": kwargs["write"]},
    )

    result = worker._execute_statement_regeneration(
        {
            **_action(
                repair_route="statement_regeneration",
                repair_kind="replace_placeholder_statement",
                theorem_ids=["bad"],
            ),
            "artifacts": {"report": "report.json", "lean_file": "paper.lean", "ledger": "ledger.json"},
        },
        project_root=tmp_path,
        write=True,
        repair_output_root=tmp_path / "repair",
        validate_candidates=True,
    )

    assert result["status"] == "generated_repair_pack_no_ledger_change"
    assert result["mutated"] is False
    assert result["mutated_rows"] == 0


def test_statement_regeneration_reports_quality_blockers_when_no_ledger_change(monkeypatch, tmp_path: Path) -> None:
    fake_module = types.SimpleNamespace(
        build_repair_pack=lambda **_kwargs: {
            "candidate_counts": {"changed_elaborating": 0, "quality_blocked": 1},
            "repair_candidates": [
                {
                    "theorem_name": "bad",
                    "changes": ["regenerate_explicit_structured_statement"],
                    "repair_quality": {
                        "ok": False,
                        "blockers": ["vacuous_exists_self_equality_after_repair"],
                    },
                    "lean_validation": {
                        "ok": False,
                        "error": "repair_quality_blocked:vacuous_exists_self_equality_after_repair",
                    },
                }
            ],
        }
    )
    monkeypatch.setitem(sys.modules, "repair_bad_translations", fake_module)
    monkeypatch.setattr(
        worker,
        "apply_validated_repair_pack_to_ledger",
        lambda **kwargs: {"ok": True, "updated_count": 0, "wrote": kwargs["write"]},
    )

    result = worker._execute_statement_regeneration(
        {
            **_action(
                repair_route="statement_regeneration",
                repair_kind="replace_placeholder_statement",
                theorem_ids=["bad"],
            ),
            "artifacts": {"report": "report.json", "lean_file": "paper.lean", "ledger": "ledger.json"},
        },
        project_root=tmp_path,
        write=True,
        repair_output_root=tmp_path / "repair",
        validate_candidates=True,
    )

    assert result["status"] == "generated_repair_pack_no_ledger_change"
    assert result["repair_blocker_counts"]["repair_quality:vacuous_exists_self_equality_after_repair"] == 1


def test_statement_regeneration_writes_after_validated_apply_changes(monkeypatch, tmp_path: Path) -> None:
    fake_module = types.SimpleNamespace(
        build_repair_pack=lambda **_kwargs: {"candidate_counts": {"changed_elaborating": 2}, "repair_candidates": []}
    )
    writes: list[bool] = []

    def fake_apply(**kwargs: object) -> dict[str, object]:
        writes.append(bool(kwargs["write"]))
        return {"ok": True, "updated_count": 2, "wrote": kwargs["write"]}

    monkeypatch.setitem(sys.modules, "repair_bad_translations", fake_module)
    monkeypatch.setattr(worker, "apply_validated_repair_pack_to_ledger", fake_apply)

    result = worker._execute_statement_regeneration(
        {
            **_action(repair_route="statement_regeneration", repair_kind="replace_placeholder_statement"),
            "artifacts": {"report": "report.json", "lean_file": "paper.lean", "ledger": "ledger.json"},
        },
        project_root=tmp_path,
        write=True,
        repair_output_root=tmp_path / "repair",
        validate_candidates=True,
    )

    assert writes == [False, True]
    assert result["status"] == "written_translation_repair_pack"
    assert result["mutated"] is True
    assert result["mutated_rows"] == 2


def test_statement_regeneration_uses_source_backed_payload_before_legacy_pack(monkeypatch, tmp_path: Path) -> None:
    fake_module = types.SimpleNamespace(
        build_source_backed_repair_payload=lambda **_kwargs: {
            "candidate_counts": {"changed_elaborating": 1, "source_backed_v2": 1},
            "repair_candidates": [
                {
                    "theorem_name": "bad",
                    "repaired_decl": "theorem bad (n : Nat) : n = n := by\n  sorry",
                    "changes": ["source_backed_regeneration_v2"],
                    "repair_quality": {"ok": True, "blockers": []},
                    "lean_validation": {"ok": True},
                    "statement_repair_kind": "source_backed_statement_regeneration",
                    "regeneration_protocol": "source_backed_v2",
                }
            ],
        }
    )
    writes: list[bool] = []

    def fake_apply(**kwargs: object) -> dict[str, object]:
        writes.append(bool(kwargs["write"]))
        return {"ok": True, "updated_count": 1, "wrote": kwargs["write"], "updated_theorems": ["bad"]}

    monkeypatch.setitem(sys.modules, "repair_bad_translations", fake_module)
    monkeypatch.setattr(worker, "apply_validated_repair_pack_to_ledger", fake_apply)

    result = worker._execute_statement_regeneration(
        {
            **_action(
                repair_route="statement_regeneration",
                repair_kind="replace_placeholder_statement",
                theorem_ids=["bad"],
                source_contexts=[
                    {
                        "row_id": "r1",
                        "paper_id": "2604.21314",
                        "theorem_id": "bad",
                        "theorem_name": "bad",
                        "source_latex": "For every natural number n, n equals itself.",
                        "normalized_text": "For every natural number n, n equals itself.",
                        "source_span_quality": "extractor_native",
                        "source_span": {"source_file": "paper.tex", "start_byte": 0, "end_byte": 10},
                        "source_match": {"match_status": "matched"},
                    }
                ],
            ),
            "artifacts": {"report": "report.json", "lean_file": "paper.lean", "ledger": "ledger.json"},
        },
        project_root=tmp_path,
        write=True,
        repair_output_root=tmp_path / "repair",
        validate_candidates=True,
    )

    assert writes == [False, True]
    assert result["status"] == "written_source_backed_regeneration"
    assert result["repair_summary"]["changed_elaborating"] == 1
    assert result["regeneration_protocol"] == "source_backed_v2"


def test_statement_regeneration_dry_run_builds_source_backed_candidate_preview(monkeypatch, tmp_path: Path) -> None:
    fake_module = types.SimpleNamespace(
        build_source_backed_repair_payload=lambda **_kwargs: {
            "candidate_counts": {"total": 1, "changed_elaborating": 0, "quality_blocked": 1},
            "repair_candidates": [
                {
                    "theorem_name": "bad",
                    "repaired_decl": "theorem bad : ∃ x : ℝ, x = x := by\n  sorry",
                    "changes": ["source_backed_regeneration_v2"],
                    "repair_quality": {
                        "ok": False,
                        "blockers": ["vacuous_exists_self_equality_after_repair"],
                    },
                    "lean_validation": {
                        "ok": False,
                        "error": "repair_quality_blocked:vacuous_exists_self_equality_after_repair",
                    },
                    "review_batch_eligibility_preview": {
                        "eligible": False,
                        "blockers": ["placeholder_or_trivial_lean_statement"],
                    },
                    "statement_repair_kind": "source_backed_statement_regeneration",
                    "regeneration_protocol": "source_backed_v2",
                }
            ],
        }
    )
    monkeypatch.setitem(sys.modules, "repair_bad_translations", fake_module)
    monkeypatch.setattr(worker, "apply_validated_repair_pack_to_ledger", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("ledger must not be touched in dry-run")))

    result = worker._execute_statement_regeneration(
        {
            **_action(
                repair_route="statement_regeneration",
                repair_kind="replace_placeholder_statement",
                theorem_ids=["bad"],
                source_contexts=[
                    {
                        "row_id": "r1",
                        "paper_id": "2604.21314",
                        "theorem_id": "bad",
                        "theorem_name": "bad",
                        "source_latex": "For every natural number n, n equals itself.",
                        "source_span_quality": "extractor_native",
                        "source_span": {"source_file": "paper.tex", "start_byte": 0, "end_byte": 10},
                        "source_match": {"match_status": "matched"},
                    }
                ],
            ),
            "artifacts": {"report": "report.json", "lean_file": "paper.lean", "ledger": "ledger.json"},
        },
        project_root=tmp_path,
        write=False,
        repair_output_root=tmp_path / "repair",
        validate_candidates=True,
    )

    assert result["status"] == "dry_run_source_backed_preview"
    assert result["mutated"] is False
    assert result["repair_blocker_counts"]["repair_quality:vacuous_exists_self_equality_after_repair"] == 1
    assert result["candidate_graduation_preview"][0]["reviewable"] is False
    assert "review_batch:placeholder_or_trivial_lean_statement" in result["candidate_graduation_preview"][0]["blockers"]


def test_statement_regeneration_write_refuses_review_batch_blocked_candidate(monkeypatch, tmp_path: Path) -> None:
    fake_module = types.SimpleNamespace(
        build_source_backed_repair_payload=lambda **_kwargs: {
            "candidate_counts": {"total": 1, "changed_elaborating": 1},
            "repair_candidates": [
                {
                    "theorem_name": "bad",
                    "repaired_decl": "theorem bad (n : Nat) : n = n := by\n  sorry",
                    "changes": ["source_backed_regeneration_v2"],
                    "repair_quality": {"ok": True, "blockers": []},
                    "lean_validation": {"ok": True},
                    "review_batch_eligibility_preview": {
                        "eligible": False,
                        "blockers": ["source_match_not_unique"],
                    },
                    "statement_repair_kind": "source_backed_statement_regeneration",
                    "regeneration_protocol": "source_backed_v2",
                }
            ],
        }
    )
    monkeypatch.setitem(sys.modules, "repair_bad_translations", fake_module)
    monkeypatch.setattr(worker, "apply_validated_repair_pack_to_ledger", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("blocked candidate must not be applied")))

    result = worker._execute_statement_regeneration(
        {
            **_action(
                repair_route="statement_regeneration",
                repair_kind="replace_placeholder_statement",
                theorem_ids=["bad"],
                source_contexts=[{"theorem_name": "bad", "source_latex": "For all n, n = n."}],
            ),
            "artifacts": {"report": "report.json", "lean_file": "paper.lean", "ledger": "ledger.json"},
        },
        project_root=tmp_path,
        write=True,
        repair_output_root=tmp_path / "repair",
        validate_candidates=True,
    )

    assert result["status"] == "source_backed_regeneration_no_ledger_change"
    assert result["mutated"] is False
    assert result["write_eligibility_filter"]["eligible_candidate_count"] == 0
    assert "review_batch:source_match_not_unique" in result["candidate_graduation_preview"][0]["blockers"]


def test_candidate_preview_preserves_paper_theory_debt_without_blocking_review() -> None:
    row = worker._preview_row_for_candidate(
        {
            "row_id": "r1",
            "paper_id": "2604.21583",
            "theorem_id": "hard_formula",
            "source_latex": "A hard source-backed claim.",
            "source_span_quality": "extractor_native",
            "source_span": {"source_file": "paper.tex", "start_byte": 0, "end_byte": 10},
            "source_match": {"match_status": "matched"},
        },
        {
            "repaired_decl": "theorem hard_formula :\n  HardFormulaSourceStatement := by\n  sorry",
            "paper_theory_debt": ["paper_definition_stub:HardFormulaSourceStatement"],
            "statement_repair_kind": "source_backed_statement_regeneration",
            "regeneration_protocol": "source_backed_v2",
        },
    )

    assert row["axiom_debt"] == ["paper_definition_stub:HardFormulaSourceStatement"]
    assert worker.graduation_blockers(row) == []


def test_statement_regeneration_filters_repair_pack_to_action_targets() -> None:
    payload = {
        "repair_candidates": [
            {
                "theorem_name": "target",
                "changes": ["repair"],
                "lean_validation": {"ok": True},
                "statement_repair_kind": "faithful_statement_regeneration",
            },
            {
                "theorem_name": "unrelated",
                "changes": ["repair"],
                "lean_validation": {"ok": True},
            },
        ],
        "candidate_counts": {"total": 2},
    }

    filtered = worker._repair_payload_for_action(payload, {"theorem_ids": ["target"]})

    assert [c["theorem_name"] for c in filtered["repair_candidates"]] == ["target"]
    assert filtered["candidate_counts"]["total"] == 1
    assert filtered["worker_candidate_filter"]["input_candidate_count"] == 2


def test_source_span_repair_writes_only_when_evidence_rows_change(monkeypatch, tmp_path: Path) -> None:
    calls: list[bool] = []

    def fake_repair_file(path: Path, *, project_root: Path, write: bool) -> dict[str, object]:
        calls.append(write)
        return {"ok": True, "path": str(path), "repaired_rows": 1}

    monkeypatch.setitem(sys.modules, "repair_extracted_theorem_spans", types.SimpleNamespace(repair_file=fake_repair_file))

    result = worker._execute_source_span_repair(
        _action(artifacts={"extracted_theorems": "evidence.json", "ledger": "ledger.json"}),
        project_root=tmp_path,
        write=True,
    )

    assert calls == [False, True]
    assert result["status"] == "written_source_span_repair"
    assert result["mutated_rows"] == 1
    assert "ledger_application" not in result


def test_post_rebuild_graduation_uses_rebuilt_rows_not_prefetched_queue() -> None:
    before = [_queue_row(row_id="r1")]
    after = [
        {
            "row_id": "r1",
            "arxiv_id": "2604.21314",
            "theorem_id": "thm:bad",
            "status": "UNRESOLVED",
            "lean_statement": "theorem bad (n : Nat) : n = n",
            "source_latex": "For every natural number n, n equals itself.",
            "source_span_quality": "extractor_native",
            "source_span": {"source_file": "paper.tex", "start_byte": 0, "end_byte": 10},
            "alignment_evidence": {"source_match": {"match_status": "matched"}},
        }
    ]

    report = worker.post_rebuild_graduation_report(
        before_rows=before,
        corpus_rows_after=after,
        repair_queue_after=[],
        review_batch_after=[],
        gold_queue_after=[],
    )

    assert report["graduated_rows_after"] == 1
    assert report["still_blocked_rows_after"] == 0


def test_rebuild_downstream_artifacts_recomputes_review_and_proof_queues(monkeypatch, tmp_path: Path) -> None:
    rows_after = [
        {
            "row_id": "r1",
            "arxiv_id": "2604.21314",
            "theorem_id": "thm:bad",
            "canonical_theorem_id": "bad",
            "status": "UNRESOLVED",
            "lean_statement": "theorem bad (n : Nat) : n = n",
            "source_latex": "For every natural number n, n equals itself.",
            "normalized_text": "For every natural number n, n equals itself.",
            "source_span_quality": "extractor_native",
            "source_span": {"source_file": "paper.tex", "start_byte": 0, "end_byte": 10},
            "alignment_evidence": {"source_match": {"match_status": "matched"}},
            "statement_alignment_class": "partial",
            "alignment_confidence": 0.5,
            "alignment_gold_eligible": False,
            "claim_equivalence_verdict": "unclear",
            "identity_status": "unknown",
            "gate_failures": [],
            "axiom_debt": [],
            "artifact_paths": {},
        }
    ]
    monkeypatch.setattr(worker, "build_corpus_rows", lambda **_kwargs: (rows_after, {"rows": 1, "papers": 1}))
    monkeypatch.setattr(worker, "DEFAULT_CORPUS_OUT", tmp_path / "stable_corpus.jsonl")
    monkeypatch.setattr(worker, "DEFAULT_CORPUS_SUMMARY", tmp_path / "stable_corpus_summary.json")
    monkeypatch.setattr(worker, "DEFAULT_FIDELITY_OUT", tmp_path / "statement_fidelity_queue.jsonl")
    monkeypatch.setattr(worker, "DEFAULT_FIDELITY_SUMMARY", tmp_path / "statement_fidelity_queue_summary.json")
    monkeypatch.setattr(worker, "DEFAULT_REPAIR_QUEUE_OUT", tmp_path / "statement_repair_queue.jsonl")
    monkeypatch.setattr(worker, "DEFAULT_REPAIR_QUEUE_SUMMARY", tmp_path / "statement_repair_queue_summary.json")
    monkeypatch.setattr(worker, "DEFAULT_REVIEW_BATCH_OUT", tmp_path / "statement_review_batch.jsonl")
    monkeypatch.setattr(worker, "DEFAULT_REVIEW_TEMPLATE_OUT", tmp_path / "review_template.jsonl")
    monkeypatch.setattr(worker, "DEFAULT_REVIEW_BATCH_SUMMARY", tmp_path / "statement_review_batch_summary.json")
    monkeypatch.setattr(worker, "DEFAULT_GOLD_PROOF_OUT", tmp_path / "gold_queue.jsonl")
    monkeypatch.setattr(worker, "DEFAULT_GOLD_PROOF_SUMMARY", tmp_path / "gold_queue_summary.json")

    result = worker.rebuild_downstream_artifacts(
        project_root=tmp_path,
        ledger_paths=[],
        report_roots=[],
        evidence_roots=[],
        selected_rows_before=[_queue_row(row_id="r1")],
        limit=10,
    )

    assert result["status"] == "completed"
    assert result["graduated_rows_after"] == 1
    assert result["statement_review_batch_rows_after"] == 1
    assert (tmp_path / "stable_corpus.jsonl").exists()
    assert (tmp_path / "statement_review_batch.jsonl").exists()
    assert (tmp_path / "gold_queue.jsonl").exists()


def test_run_worker_rolls_back_mutation_when_no_rows_graduate(monkeypatch, tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.json"
    ledger.write_text("original", encoding="utf-8")
    row = _queue_row(artifact_paths={"ledger": str(ledger)})

    def fake_execute(actions: list[dict[str, object]], **_kwargs: object) -> list[dict[str, object]]:
        ledger.write_text("mutated", encoding="utf-8")
        return [{**actions[0], "status": "written_translation_repair_pack", "wrote": True, "mutated": True, "mutated_rows": 1}]

    def fake_rebuild(**_kwargs: object) -> dict[str, object]:
        return {
            "status": "completed",
            "graduated_rows_after": 0,
            "still_blocked_reason_counts_after": {"repair_queue:still_present": 1},
            "review_batch_rows_after": 0,
            "gold_proof_queue_rows_after": 0,
        }

    monkeypatch.setattr(worker, "execute_worker_actions", fake_execute)
    monkeypatch.setattr(worker, "rebuild_downstream_artifacts", fake_rebuild)

    executed, summary = worker.run_worker(
        [row],
        project_root=tmp_path,
        write=True,
        max_write_groups=1,
    )

    assert ledger.read_text(encoding="utf-8") == "original"
    assert executed[0]["status"] == "rolled_back_no_post_rebuild_graduation"
    assert executed[0]["mutated"] is False
    assert summary["rollback"]["status"] == "completed"
    assert summary["non_promotable"] is True
