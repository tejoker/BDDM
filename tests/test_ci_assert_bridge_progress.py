from __future__ import annotations

import json
from pathlib import Path

from ci_assert_bridge_progress import main


def test_ci_assert_bridge_progress_pass(tmp_path: Path, monkeypatch) -> None:
    rep = tmp_path / "weekly.json"
    rep.write_text(
        json.dumps(
            {
                "benchmark_world_model": {
                    "kpis": {
                        "hard_safe_yield": 0.2,
                        "slot_coverage_pass_rate": 1.0,
                        "candidate_empty_rate": 0.1,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "ci_assert_bridge_progress.py",
            "--weekly-report",
            str(rep),
            "--min-hard-safe-yield",
            "0.1",
            "--min-slot-coverage-pass-rate",
            "0.9",
            "--max-candidate-empty-rate",
            "0.5",
        ],
    )
    assert main() == 0

