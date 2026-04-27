from __future__ import annotations

import json
from pathlib import Path

from export_april_repair_dataset import build_april_rows, classify_error, export_dataset, iter_attempts
from repair_feedback_dataset import make_repair_row


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_classify_error_covers_common_lean_feedback() -> None:
    assert classify_error("typeclass instance problem is stuck") == "typeclass_stuck"
    assert classify_error("Unknown identifier `foo`") == "name_resolution"
    assert classify_error("Tactic `assumption` failed") == "assumption_mismatch"
    assert classify_error("final_semantic_hard_block:claim_shape_mismatch") == "semantic_fidelity"


def test_export_dataset_pairs_failure_with_successful_repair(tmp_path: Path) -> None:
    ledger = tmp_path / "ledgers" / "2300.00001.json"
    _write_json(
        ledger,
        {
            "paper_id": "2300.00001",
            "entries": [
                {
                    "theorem_name": "Demo.t1",
                    "status": "FLAWED",
                    "lean_statement": "theorem t1 (h : True) : True := by\n  sorry",
                    "error_message": "line=1; message=Tactic `assumption` failed",
                    "step_obligations": [
                        {"tactic": "assumption", "verified": False, "detail": "Tactic `assumption` failed"}
                    ],
                    "assumptions": [{"lean_expr": "(h : True)", "grounding": "GROUNDED_INTERNAL_KG"}],
                    "gate_failures": ["lean_proof_closed"],
                },
                {
                    "theorem_name": "t1",
                    "status": "FULLY_PROVEN",
                    "lean_statement": "theorem t1 (h : True) : True := by\n  trivial",
                    "proof_text": "trivial",
                },
            ],
        },
    )

    attempts = iter_attempts([tmp_path / "ledgers"])
    rows, summary = build_april_rows(attempts)

    assert summary["rows"] == 1
    assert summary["paired_repairs"] == 1
    row = rows[0]
    assert row["failing_lean"].startswith("theorem t1")
    assert row["error_message"] == "line=1; message=Tactic `assumption` failed"
    assert row["previous_attempt"] == "assumption"
    assert row["successful_repair"] == "trivial"
    assert row["repair_available"] is True
    assert row["repair_source"] == "ledger_pair"
    assert row["failed_candidate"].startswith("theorem t1")
    assert "GROUNDED_INTERNAL_KG" in row["repair_prompt_context"]
    assert row["normalized_error_message"] == "line=1; message=Tactic `assumption` failed"
    assert row["lean_error_kind"] == "assumption_failed"
    assert row["line_col"] == "1"
    assert row["failure_class"] == "assumption_mismatch"
    assert "GROUNDED_INTERNAL_KG" in row["local_context"]


def test_export_dataset_writes_jsonl_and_summary(tmp_path: Path) -> None:
    _write_json(
        tmp_path / "logs" / "run.json",
        [
            {
                "theorem": "bad",
                "file": "paper.lean",
                "proved": False,
                "error": "Failed after repair_rounds=1; last_error=line=1; message=unexpected identifier; expected command",
            }
        ],
    )

    result = export_dataset(
        input_paths=[tmp_path / "logs"],
        out_jsonl=tmp_path / "out" / "april.jsonl",
        out_summary=tmp_path / "out" / "summary.json",
    )

    assert result["rows"] == 1
    assert Path(result["out_jsonl"]).exists()
    row = json.loads(Path(result["out_jsonl"]).read_text(encoding="utf-8"))
    assert row["theorem_name"] == "bad"
    assert row["failure_class"] == "syntax_or_repl_startup"
    assert row["lean_error_kind"] == "syntax_error"
    assert row["repair_source"] == "ledger_unpaired"
    assert "repair_rounds" in row["raw_error_message"]
    assert json.loads(Path(result["out_summary"]).read_text(encoding="utf-8"))["unpaired_failures"] == 1


def test_export_dataset_merges_run_rows_and_deduplicates(tmp_path: Path) -> None:
    ledger = tmp_path / "ledgers" / "2300.00002.json"
    _write_json(
        ledger,
        {
            "paper_id": "2300.00002",
            "entries": [
                {
                    "theorem_name": "Demo.t2",
                    "status": "FLAWED",
                    "lean_statement": "theorem t2 : Foo := by\n  sorry",
                    "error_message": "unknown identifier 'Foo'",
                }
            ],
        },
    )
    run_file = tmp_path / "runs" / "run_a" / "compiler_feedback_repair_dataset.jsonl"
    run_file.parent.mkdir(parents=True)
    run_row = make_repair_row(
        paper_id="2300.00002",
        theorem_name="Demo.t2",
        failing_lean="theorem t2 : Foo := by\n  sorry",
        error_message="unknown identifier 'Foo'",
        successful_repair="trivial",
        stage="ledger_export",
        run_id="run_a",
        source_artifacts=[str(run_file)],
        project_root=tmp_path,
    )
    run_file.write_text(json.dumps(run_row) + "\n", encoding="utf-8")

    result = export_dataset(
        input_paths=[tmp_path / "ledgers"],
        run_roots=[tmp_path / "runs"],
        out_jsonl=tmp_path / "out" / "merged.jsonl",
        out_summary=tmp_path / "out" / "summary.json",
    )

    rows = [json.loads(line) for line in Path(result["out_jsonl"]).read_text(encoding="utf-8").splitlines()]
    assert result["ledger_export_rows"] == 1
    assert result["run_local_rows"] == 1
    assert result["deduplicated_rows"] == 1
    assert len(rows) == 1
    assert rows[0]["successful_repair"] == "trivial"
    assert rows[0]["run_id"] == "run_a"
