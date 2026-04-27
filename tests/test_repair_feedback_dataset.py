from __future__ import annotations

import json
from pathlib import Path

import pytest

from repair_feedback_dataset import (
    classify_error,
    compute_row_id,
    default_run_dataset_path,
    format_repair_examples,
    make_repair_row,
    merge_deduped_rows,
    parse_lean_error,
    retrieve_repair_examples,
)


@pytest.mark.parametrize(
    ("message", "lean_kind", "failure_class"),
    [
        ("line=12; column=7; message=unknown identifier 'Foo'", "unknown_identifier", "name_resolution"),
        ("failed to synthesize instance OfNat α 0", "typeclass_synthesis_failed", "typeclass_stuck"),
        ("unsolved goals\n⊢ True", "unsolved_goals", "tactic_failure"),
        ("unexpected identifier; expected command", "syntax_error", "syntax_or_repl_startup"),
        ("application type mismatch\n  f x\nhas type Nat but is expected to have type Int", "type_mismatch", "type_mismatch"),
        ("lake env lean timed out after 30s", "timeout", "timeout"),
        ("semantic_policy_violation:claim_shape_mismatch", "semantic_policy_violation", "semantic_fidelity"),
        ("vacuity: statement is trivially provable by `trivial`", "vacuity_failure", "trivialization"),
    ],
)
def test_classify_error_covers_representative_lean_feedback(
    message: str,
    lean_kind: str,
    failure_class: str,
) -> None:
    parsed = parse_lean_error(message)
    assert parsed["lean_error_kind"] == lean_kind
    assert classify_error(message) == failure_class


def test_parse_lean_error_extracts_identifier_and_line_col() -> None:
    parsed = parse_lean_error("line=12; column=7; message=unknown identifier 'Foo.bar'")
    assert parsed["line_col"] == "12:7"
    assert parsed["primary_identifier"] == "Foo.bar"

    file_position = parse_lean_error("/tmp/Paper.lean:31:4: error: unknown constant `Baz`")
    assert file_position["line_col"] == "31:4"
    assert file_position["primary_identifier"] == "Baz"


def test_make_repair_row_preserves_old_fields_and_adds_enriched_fields(tmp_path: Path) -> None:
    row = make_repair_row(
        paper_id="2401.00001",
        theorem_name="main_result",
        failing_lean="theorem main_result : Foo := by sorry",
        error_message="line=3; column=10; message=unknown identifier 'Foo'",
        local_context="latex_statement: Foo holds",
        previous_attempt="by exact Foo",
        successful_repair="by trivial",
        stage="translation_validation",
        repair_source="unit_test_pair",
        project_root=tmp_path,
    )

    assert row["failing_lean"] == row["failed_candidate"]
    assert row["error_message"] == "line=3; column=10; message=unknown identifier 'Foo'"
    assert row["raw_error_message"] == row["error_message"]
    assert row["normalized_error_message"] == row["error_message"]
    assert row["lean_error_kind"] == "unknown_identifier"
    assert row["primary_identifier"] == "Foo"
    assert row["line_col"] == "3:10"
    assert row["failure_class"] == "name_resolution"
    assert "latex_statement" in row["repair_prompt_context"]
    assert "previous_attempt" in row["repair_prompt_context"]
    assert row["repair_source"] == "unit_test_pair"


def test_retrieve_repair_examples_prefers_matching_failure_class(tmp_path: Path) -> None:
    dataset = tmp_path / "repairs.jsonl"
    rows = [
        {
            "failure_class": "assumption_mismatch",
            "error_message": "Tactic `assumption` failed",
            "previous_attempt": "assumption",
            "successful_repair": "constructor <;> assumption",
            "failing_lean": "theorem t : A ∧ B := by assumption",
        },
        {
            "failure_class": "type_mismatch",
            "error_message": "Application type mismatch",
            "successful_repair": "exact h",
            "failing_lean": "theorem u : P := by exact bad",
        },
    ]
    dataset.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    examples = retrieve_repair_examples(
        dataset_path=dataset,
        error_message="line=1; message=Tactic `assumption` failed",
        lean_state="⊢ A ∧ B",
        current_draft="assumption",
        limit=1,
    )

    assert examples[0]["failure_class"] == "assumption_mismatch"
    assert examples[0]["successful_repair"] == "constructor <;> assumption"
    assert "Successful repair" in format_repair_examples(examples)


def test_row_id_is_stable_across_run_metadata(tmp_path: Path) -> None:
    base = dict(
        paper_id="2600.00002",
        theorem_name="stable",
        failing_lean="theorem stable : Foo := by sorry",
        error_message="unknown identifier 'Foo'",
        stage="translation_validation",
        project_root=tmp_path,
    )
    row_a = make_repair_row(**base, run_id="run_a", model="model-a")
    row_b = make_repair_row(**base, run_id="run_b", model="model-b", source_artifacts=["artifact.json"])

    assert row_a["row_id"] == row_b["row_id"]
    assert row_a["normalized_error_message"] == "unknown identifier 'Foo'"
    assert row_a["row_id"] == compute_row_id(
        paper_id="2600.00002",
        theorem_name="stable",
        stage="translation_validation",
        failing_lean="theorem stable : Foo := by sorry",
        normalized_error_message="unknown identifier 'Foo'",
    )


def test_run_dataset_path_and_dedupe_prefer_repaired_row(tmp_path: Path) -> None:
    path = default_run_dataset_path(tmp_path, run_id="demo/run")
    assert path == tmp_path / "output" / "flywheel" / "runs" / "demo_run" / "compiler_feedback_repair_dataset.jsonl"

    failure = make_repair_row(
        paper_id="2600.00003",
        theorem_name="dedupe",
        failing_lean="theorem dedupe : Foo := by sorry",
        error_message="unknown identifier 'Foo'",
        stage="translation_validation",
        run_id="run_a",
        source_artifacts=["a.json"],
        project_root=tmp_path,
    )
    repaired = make_repair_row(
        paper_id="2600.00003",
        theorem_name="dedupe",
        failing_lean="theorem dedupe : Foo := by sorry",
        error_message="unknown identifier 'Foo'",
        successful_repair="theorem dedupe : True := by trivial",
        stage="translation_validation",
        run_id="run_b",
        source_artifacts=["b.json"],
        project_root=tmp_path,
    )

    rows = merge_deduped_rows([[failure], [repaired]])
    assert len(rows) == 1
    assert rows[0]["successful_repair"] == "theorem dedupe : True := by trivial"
    assert rows[0]["source_artifacts"] == ["b.json", "a.json"]
