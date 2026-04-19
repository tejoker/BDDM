from __future__ import annotations

import json
from pathlib import Path

from world_model_bridge import run_world_model_bridge_search


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

