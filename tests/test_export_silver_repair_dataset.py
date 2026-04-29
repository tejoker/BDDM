from __future__ import annotations

import json
from pathlib import Path

import export_silver_repair_dataset as silver
from export_silver_repair_dataset import build_silver_rows, export_silver_dataset


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_silver_export_labels_positive_negative_and_diagnostic_rows(tmp_path: Path) -> None:
    ledger = tmp_path / "output" / "verification_ledgers" / "2300.00001.json"
    _write_json(
        ledger,
        {
            "paper_id": "2300.00001",
            "entries": [
                {
                    "theorem_name": "Demo.t_pos",
                    "status": "FLAWED",
                    "lean_statement": "theorem t_pos : True := by\n  assumption",
                    "error_message": "Tactic `assumption` failed",
                    "step_obligations": [{"tactic": "assumption", "verified": False}],
                },
                {
                    "theorem_name": "t_pos",
                    "status": "FULLY_PROVEN",
                    "lean_statement": "theorem t_pos : True := by\n  trivial",
                    "proof_text": "trivial",
                },
                {
                    "theorem_name": "t_axiom",
                    "status": "FLAWED",
                    "lean_statement": "theorem t_axiom : Foo := by\n  sorry",
                    "error_message": "unknown identifier 'Foo'",
                },
                {
                    "theorem_name": "t_axiom",
                    "status": "AXIOM_BACKED",
                    "lean_statement": "theorem t_axiom : Foo := by\n  exact foo_axiom",
                    "proof_text": "exact foo_axiom",
                    "axiom_debt": ["paper_symbol:Foo"],
                },
                {
                    "theorem_name": "t_bad",
                    "status": "FLAWED",
                    "lean_statement": "theorem t_bad : PaperClaim23000001 := by\n  sorry",
                    "error_message": "translation_acceptance_gate:paper_claim_atom",
                },
            ],
        },
    )
    report_root = tmp_path / "reproducibility" / "full_paper_reports" / "2300.00001"
    _write_json(
        report_root / "statement_validity.json",
        {
            "total": 3,
            "counts": {
                "proof_search_failure": 1,
                "paper_theory_debt": 1,
                "bad_translation_artifact": 1,
            },
            "items": [
                {"theorem_name": "t_pos", "primary_blocker": "proof_search_failure", "valid_for_proof": True},
                {"theorem_name": "t_axiom", "primary_blocker": "paper_theory_debt", "valid_for_proof": False},
                {
                    "theorem_name": "t_bad",
                    "primary_blocker": "bad_translation_artifact",
                    "valid_for_proof": False,
                    "reasons": ["paper_claim_atom"],
                },
            ],
        },
    )
    _write_json(report_root / "proof_repair_cohort.json", [{"theorem_name": "t_pos"}])

    rows, summary = build_silver_rows(
        input_paths=[tmp_path / "output" / "verification_ledgers"],
        run_roots=[],
        report_roots=[tmp_path / "reproducibility" / "full_paper_reports"],
        repair_queue_paths=[],
        include_tmp_repair_queues=False,
    )

    by_name = {row["theorem_name"].rsplit(".", 1)[-1]: row for row in rows}
    assert by_name["t_pos"]["label"] == "positive_repair"
    assert by_name["t_pos"]["dataset_tier"] == "silver"
    assert by_name["t_pos"]["training_tier"] == "silver_repair"
    assert by_name["t_pos"]["gold_eligible"] is False
    assert by_name["t_pos"]["in_proof_repair_cohort"] is True
    assert by_name["t_axiom"]["label"] == "diagnostic_only"
    assert by_name["t_axiom"]["negative_reason"] == "axiom_backed_success_excluded"
    assert by_name["t_bad"]["label"] == "negative_bad_translation"
    assert by_name["t_bad"]["label_polarity"] == "negative"
    assert summary["label_counts"]["positive_repair"] == 1
    assert summary["training_tier_counts"]["silver_repair"] == len(rows)
    assert summary["label_counts"]["negative_bad_translation"] == 1
    assert summary["gold_contamination_audit"]["gold_eligible_true_count"] == 0
    assert summary["gold_contamination_audit"]["positive_axiom_backed_rows"] == 0


def test_silver_export_joins_translation_queue_and_keeps_paper_splits_isolated(tmp_path: Path) -> None:
    ledger = tmp_path / "ledgers" / "2300.00002.json"
    _write_json(
        ledger,
        {
            "paper_id": "2300.00002",
            "entries": [
                {
                    "theorem_name": "t_failed",
                    "status": "FLAWED",
                    "lean_statement": "theorem t_failed : Foo := by\n  exact bad",
                    "error_message": "type mismatch",
                }
            ],
        },
    )
    queue = tmp_path / "queues" / "translation_repair_queue.jsonl"
    _write_jsonl(
        queue,
        [
            {
                "paper_id": "2300.00003",
                "theorem_name": "t_queue",
                "source_statement": "Bad raw notation",
                "lean_signature": "theorem t_queue : B_N^{i;j,k} = 0 := by",
                "gate_reason": "raw_latex_leak:latex_superscript_artifact",
                "validated": False,
            }
        ],
    )

    result = export_silver_dataset(
        input_paths=[tmp_path / "ledgers"],
        run_roots=[],
        report_roots=[],
        repair_queue_paths=[queue],
        include_tmp_repair_queues=False,
        out_jsonl=tmp_path / "out" / "silver.jsonl",
        out_summary=tmp_path / "out" / "summary.json",
    )
    rows = [json.loads(line) for line in (tmp_path / "out" / "silver.jsonl").read_text(encoding="utf-8").splitlines()]
    split_by_paper: dict[str, set[str]] = {}
    for row in rows:
        split_by_paper.setdefault(row["paper_id"], set()).add(row["paper_split"])

    assert result["rows"] == 2
    assert result["translation_queue_rows"] == 1
    assert {row["label"] for row in rows} == {"negative_failed_attempt", "negative_bad_translation"}
    assert all(len(splits) == 1 for splits in split_by_paper.values())
    assert json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))["rows"] == 2


def test_silver_export_does_not_include_tmp_repair_queues_by_default(monkeypatch, tmp_path: Path) -> None:
    fake_tmp = tmp_path / "tmp"
    queue = fake_tmp / "arxiv_2300.00004" / "translation_repair_queue.jsonl"
    _write_jsonl(
        queue,
        [
            {
                "paper_id": "2300.00004",
                "theorem_name": "tmp_row",
                "lean_signature": "theorem tmp_row : B_N^{i} = 0 := by",
                "gate_reason": "raw_latex_leak",
            }
        ],
    )

    class FakePath(type(Path())):
        def exists(self) -> bool:  # type: ignore[override]
            if str(self) == "/tmp":
                return True
            return super().exists()

        def glob(self, pattern: str):  # type: ignore[override]
            if str(self) == "/tmp" and pattern == "arxiv_*/translation_repair_queue.jsonl":
                return iter([queue])
            return super().glob(pattern)

    def fake_path(value: str | Path) -> Path:
        if str(value) == "/tmp":
            return FakePath("/tmp")
        return Path(value)

    monkeypatch.setattr(silver, "Path", fake_path)

    rows, summary = build_silver_rows(
        input_paths=[],
        run_roots=[],
        report_roots=[],
        repair_queue_paths=[],
    )
    assert rows == []
    assert summary["translation_queue_rows"] == 0

    rows_with_tmp, summary_with_tmp = build_silver_rows(
        input_paths=[],
        run_roots=[],
        report_roots=[],
        repair_queue_paths=[],
        include_tmp_repair_queues=True,
    )
    assert len(rows_with_tmp) == 1
    assert summary_with_tmp["translation_queue_rows"] == 1
