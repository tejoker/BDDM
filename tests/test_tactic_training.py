import json
import importlib
from pathlib import Path

import numpy as np

tactic_training = importlib.import_module("tactic_training")


def test_export_triples_and_train_stub(tmp_path: Path):
    ledger_dir = tmp_path / "ledgers"
    ledger_dir.mkdir()
    ledger_path = ledger_dir / "sample.json"
    ledger_payload = {
        "entries": [
            {
                "theorem_name": "thm_one",
                "lean_statement": "theorem thm_one : True := by",
                "step_obligations": [
                    {"tactic": "trivial", "result": "ok", "detail": "goal: True"},
                    {"tactic": "exact False.elim ?h", "result": "error", "detail": "goal: False"},
                ],
            }
        ]
    }
    ledger_path.write_text(json.dumps(ledger_payload), encoding="utf-8")

    triples_path = tmp_path / "triples.jsonl"
    summary = tactic_training.export_triples(
        ledger_dir=ledger_dir,
        out_path=triples_path,
    )

    assert summary["triples"] == 2
    assert summary["successes"] == 1
    lines = triples_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["tactic"] == "trivial"
    assert first["outcome"] == 1
    assert first["state"] == "goal: True"

    manifest_path = tmp_path / "manifest.json"
    manifest = tactic_training.train_stub(
        triples_path=triples_path,
        out_manifest=manifest_path,
        epochs=2,
        model_name="toy-model",
    )
    assert manifest["num_samples"] == 2
    assert manifest["num_positive"] == 1
    assert manifest["epochs"] == 2
    assert manifest["model"] == "toy-model"
    assert manifest_path.exists()


def test_train_sft_and_rl(tmp_path: Path):
    triples = tmp_path / "triples.jsonl"
    rows = [
        {"state": "⊢ n + 0 = n", "tactic": "simp", "outcome": 1},
        {"state": "⊢ n + 0 = n", "tactic": "ring", "outcome": 0},
        {"state": "⊢ x = x", "tactic": "rfl", "outcome": 1},
        {"state": "⊢ False", "tactic": "trivial", "outcome": 0},
    ]
    triples.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    out_dir = tmp_path / "policy"
    sft_summary = tactic_training.train_sft(
        triples_path=triples,
        out_dir=out_dir,
        epochs=3,
        lr=0.3,
        dims=256,
    )
    sft_weights = Path(sft_summary["weights"])
    assert sft_weights.exists()
    assert np.load(sft_weights).shape[0] == 257

    rl_summary = tactic_training.train_rl_refinement(
        triples_path=triples,
        sft_weights_path=sft_weights,
        out_dir=out_dir,
        epochs=2,
        lr=0.1,
        baseline_momentum=0.9,
    )
    rl_weights = Path(rl_summary["weights"])
    assert rl_weights.exists()
    assert Path(rl_summary["meta"]).exists()
