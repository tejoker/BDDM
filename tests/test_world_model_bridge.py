from __future__ import annotations

import json
from pathlib import Path

from bridge_proofs import BridgeCandidate
from world_model_bridge import AssumptionSlot, WorldState, _candidate_actions, run_world_model_bridge_search


def test_world_model_bridge_search_runs(tmp_path: Path) -> None:
    ledger_root = tmp_path / "ledgers"
    ledger_root.mkdir(parents=True)
    payload = {
        "entries": [
            {
                "theorem_name": "target_thm",
                "assumptions": [
                    {
                        "grounding": "UNGROUNDED",
                        "lean_expr": "1 <= 2",
                        "lean_statement": "",
                        "label": "arith",
                    }
                ],
            }
        ]
    }
    (ledger_root / "paper1.json").write_text(json.dumps(payload), encoding="utf-8")

    result = run_world_model_bridge_search(
        target_theorem="target_thm",
        ledger_root=ledger_root,
        budget=5,
        max_depth=3,
        max_candidates_per_assumption=2,
    )
    assert result.assumptions_total == 1
    assert result.grounded_count >= 0
    assert isinstance(result.actions_taken, list)


def test_world_model_includes_retrieval_bridge_actions(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "world_model_bridge.suggest_bridge_candidates",
        lambda **_: [
            BridgeCandidate(
                theorem_name="helper_thm",
                paper_id="paper1",
                status="FULLY_PROVEN",
                score=0.9,
                lean_statement="theorem helper_thm : alpha_density_bound_x <= alpha_density_bound_y",
                actionable=True,
            )
        ],
    )
    ledger_root = tmp_path / "ledgers"
    ledger_root.mkdir(parents=True)
    payload = {
        "entries": [
            {
                "theorem_name": "target_thm",
                "status": "INTERMEDIARY_PROVEN",
                "assumptions": [
                    {
                        "grounding": "UNGROUNDED",
                        "lean_expr": "",
                        "lean_statement": "",
                        "label": "alpha_density_bound_x_le_alpha_density_bound_y",
                    }
                ],
            },
            {
                "theorem_name": "helper_thm",
                "status": "FULLY_PROVEN",
                "lean_statement": "theorem helper_thm : alpha_density_bound_x <= alpha_density_bound_y",
            },
        ]
    }
    (ledger_root / "paper1.json").write_text(json.dumps(payload), encoding="utf-8")
    actions = _candidate_actions(
        assumptions=[
            AssumptionSlot(
                idx=0,
                slot_name="hxy",
                lean_expr="",
                lean_statement="",
                label="alpha_density_bound_x_le_alpha_density_bound_y",
            )
        ],
        ledger_root=ledger_root,
        state=WorldState(),
        max_candidates_per_assumption=2,
        context_pack=None,
    )
    retrieval_actions = [a for a in actions if a.proposer == "retrieval"]
    assert isinstance(retrieval_actions, list)
    assert len(retrieval_actions) >= 1
