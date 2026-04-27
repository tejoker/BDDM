from __future__ import annotations

import json
from pathlib import Path

from bridge_proofs import execute_bridge_chain
from diagnose_grounding_bottlenecks import diagnose


def test_execute_bridge_chain_collects_failure_reasons(tmp_path: Path) -> None:
    ledger_root = tmp_path / "ledgers"
    ledger_root.mkdir(parents=True)
    payload = {
        "entries": [
            {
                "theorem_name": "t_target",
                "status": "INTERMEDIARY_PROVEN",
                "assumptions": [
                    {
                        "grounding": "UNGROUNDED",
                        "lean_expr": "(h : foo_bar_baz)",
                        "lean_statement": "",
                        "label": "foo",
                    }
                ],
            }
        ]
    }
    (ledger_root / "p1.json").write_text(json.dumps(payload), encoding="utf-8")
    res = execute_bridge_chain(target_theorem="t_target", ledger_root=ledger_root, use_z3=True, use_lean=True)
    assert len(res.still_ungrounded) == 0
    assert res.failure_reasons.get("context_only_assumption", 0) >= 1
    assert isinstance(res.assumption_diagnostics, list)


def test_execute_bridge_chain_enforces_assumption_slot_coverage(tmp_path: Path) -> None:
    ledger_root = tmp_path / "ledgers"
    ledger_root.mkdir(parents=True)
    payload = {
        "entries": [
            {
                "theorem_name": "t_target",
                "status": "INTERMEDIARY_PROVEN",
                "assumptions": [
                    {
                        "grounding": "UNGROUNDED",
                        "lean_expr": "",
                        "lean_statement": "theorem bridge_goal : x <= y",
                        "label": "",
                    }
                ],
            }
        ]
    }
    (ledger_root / "p1.json").write_text(json.dumps(payload), encoding="utf-8")
    res = execute_bridge_chain(
        target_theorem="t_target",
        ledger_root=ledger_root,
        use_z3=False,
        use_lean=False,
        require_assumption_slot_coverage=True,
    )
    assert res.failure_reasons.get("assumption_slot_unmapped", 0) >= 1


def test_execute_bridge_chain_writes_failure_artifact(tmp_path: Path) -> None:
    ledger_root = tmp_path / "ledgers"
    ledger_root.mkdir(parents=True)
    payload = {
        "entries": [
            {
                "theorem_name": "t_target",
                "status": "INTERMEDIARY_PROVEN",
                "assumptions": [
                    {
                        "grounding": "UNGROUNDED",
                        "lean_expr": "",
                        "lean_statement": "",
                        "label": "",
                    }
                ],
            }
        ]
    }
    (ledger_root / "p1.json").write_text(json.dumps(payload), encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    execute_bridge_chain(
        target_theorem="t_target",
        ledger_root=ledger_root,
        use_z3=False,
        use_lean=False,
        require_assumption_slot_coverage=True,
        failure_artifact_root=artifacts,
    )
    files = list(artifacts.glob("*.jsonl"))
    assert len(files) >= 1
    raw = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(raw) >= 1
    row = json.loads(raw[-1])
    assert "taxonomy" in row
    assert row.get("target_theorem") == "t_target"


def test_diagnose_bottlenecks_runs_on_empty_ledger(tmp_path: Path) -> None:
    ledger_root = tmp_path / "ledgers"
    ledger_root.mkdir(parents=True)
    out = diagnose(
        ledger_root=ledger_root,
        limit=5,
        budget=10,
        max_depth=3,
        max_candidates_per_assumption=2,
    )
    assert out["summary"]["count"] == 0
    assert "world_model_top_bottlenecks" in out
