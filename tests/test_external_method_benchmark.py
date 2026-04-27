from __future__ import annotations

import json
from pathlib import Path

from external_method_benchmark import build_report, theorem_slogan


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_theorem_slogan_is_deterministic_and_symbol_tolerant() -> None:
    stmt = r"""\label{lem:test} For every $x \in X$, the map $f_x$ is continuous and bounded."""
    assert theorem_slogan(stmt) == "every map continuous bounded"


def test_build_report_tracks_three_external_method_signals(tmp_path: Path) -> None:
    ingestion = tmp_path / "ingestion"
    _write_json(
        ingestion / "2300.00001" / "extracted_theorems.json",
        {
            "paper_id": "2300.00001",
            "entries": [
                {"kind": "theorem", "name": "t1", "statement": "For every compact set, a continuous function is bounded."},
                {"kind": "lemma", "name": "l1", "statement": r"If $x \le y$ then $x+1 \le y+1$."},
            ],
        },
    )

    ledgers = tmp_path / "ledgers"
    _write_json(
        ledgers / "2300.00001.json",
        {
            "paper_id": "2300.00001",
            "entries": [
                {
                    "theorem_name": "t1",
                    "status": "UNRESOLVED",
                    "error_message": "tactic 'assumption' failed",
                    "proof_text": "by assumption",
                },
                {
                    "theorem_name": "t2",
                    "status": "FULLY_PROVEN",
                    "proof_text": "by trivial",
                },
            ],
        },
    )

    reports = tmp_path / "reports"
    _write_json(
        reports / "world.json",
        {
            "benchmark_world_model": {
                "rows": [{"state": "s", "action": "a", "reward": 1.0, "lean_verified": True}],
                "mcts": {"value": 0.8},
            }
        },
    )

    report = build_report(
        project_root=tmp_path,
        ingestion_root=ingestion,
        ledger_dirs=[ledgers],
        theorem_jsons=[],
        report_roots=[reports],
    )

    assert report["summary"]["methods_tracked"] == 3
    sections = {section["method_family"]: section for section in report["sections"]}
    assert sections["compiler_feedback_repair"]["desol_signal"]["feedback_rows"] == 1
    assert sections["theorem_retrieval"]["desol_signal"]["theorem_like_rows"] == 2
    assert sections["theorem_retrieval"]["benchmark_status"] == "slogan_baseline_ready"
    assert sections["verifier_backed_symbolic_search"]["benchmark_status"] == "benchmarkable_small"
