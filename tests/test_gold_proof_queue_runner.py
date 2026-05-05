from __future__ import annotations

from pathlib import Path

from run_gold_proof_queue import build_gold_proof_runs


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_version": "gold_proof_growth_queue.v1",
        "row_id": "r1",
        "arxiv_id": "2604.21616",
        "theorem_id": "nuclear-l1-norms",
        "lean_statement": "theorem nuclear_l1_norms (n : Nat) : n = n",
        "source_latex": "For every n, n = n.",
        "alignment_tier": "alignment_gold",
        "alignment_gold_eligible": True,
        "statement_alignment_class": "exact",
        "alignment_confidence": 0.95,
        "claim_equivalence_verdict": "equivalent",
        "independent_semantic_equivalence_evidence": True,
        "alignment_review_required": False,
        "source_span_quality": "extractor_native",
        "status": "UNRESOLVED",
        "axiom_debt": [],
        "gate_failures": [],
        "artifact_paths": {"lean_file": "output/2604.21616.lean"},
    }
    row.update(overrides)
    return row


def test_gold_proof_queue_runner_builds_safe_prove_commands(tmp_path: Path) -> None:
    commands, summary = build_gold_proof_runs(
        [_row()],
        project_root=tmp_path,
        queue_jsonl=Path("output/corpus/gold_proof_growth_queue.jsonl"),
        mode="full-draft",
        repair_rounds=3,
    )

    assert summary["accepted_rows"] == 1
    assert summary["rejected_rows"] == 0
    assert commands[0]["paper_id"] == "2604.21616"
    assert "nuclear_l1_norms" in commands[0]["theorems"]
    assert "--gold-proof-queue-jsonl" in commands[0]["command"]


def test_gold_proof_queue_runner_rejects_blocked_rows(tmp_path: Path) -> None:
    commands, summary = build_gold_proof_runs(
        [
            _row(
                row_id="bad",
                lean_statement="theorem false_target : False",
                source_latex="For every n, n = n.",
            )
        ],
        project_root=tmp_path,
    )

    assert commands == []
    assert summary["accepted_rows"] == 0
    assert summary["rejection_reason_counts"]["false_target_without_source_contradiction"] == 1


def test_gold_proof_queue_runner_rejects_stale_rows_not_in_current_sorry_set(tmp_path: Path) -> None:
    lean_file = tmp_path / "paper.lean"
    lean_file.write_text("theorem other : True := by\n  sorry\n", encoding="utf-8")

    commands, summary = build_gold_proof_runs(
        [_row(artifact_paths={"lean_file": str(lean_file)})],
        project_root=tmp_path,
    )

    assert commands == []
    assert summary["rejection_reason_counts"]["target_not_in_current_sorry_set"] == 1


def test_gold_proof_queue_runner_accepts_one_line_sorry_target(tmp_path: Path) -> None:
    lean_file = tmp_path / "paper.lean"
    lean_file.write_text("theorem nuclear_l1_norms (n : Nat) : n = n := by sorry\n", encoding="utf-8")

    commands, summary = build_gold_proof_runs(
        [_row(artifact_paths={"lean_file": str(lean_file)})],
        project_root=tmp_path,
    )

    assert summary["accepted_rows"] == 1
    assert commands[0]["theorems"] == ["nuclear_l1_norms"]


def test_gold_proof_queue_runner_rejects_file_with_no_sorry_targets(tmp_path: Path) -> None:
    lean_file = tmp_path / "paper.lean"
    lean_file.write_text("theorem nuclear_l1_norms : True := by trivial\n", encoding="utf-8")

    commands, summary = build_gold_proof_runs(
        [_row(artifact_paths={"lean_file": str(lean_file)})],
        project_root=tmp_path,
    )

    assert commands == []
    assert summary["rejection_reason_counts"]["target_not_in_current_sorry_set"] == 1


def test_gold_proof_queue_runner_rejects_current_false_target_even_if_queue_row_is_good(tmp_path: Path) -> None:
    lean_file = tmp_path / "paper.lean"
    lean_file.write_text("theorem nuclear_l1_norms : False := by sorry\n", encoding="utf-8")

    commands, summary = build_gold_proof_runs(
        [_row(artifact_paths={"lean_file": str(lean_file)})],
        project_root=tmp_path,
    )

    assert commands == []
    assert summary["rejection_reason_counts"]["current_lean_statement_blocked:false_target_without_source_contradiction"] == 1
