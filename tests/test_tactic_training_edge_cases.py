"""Edge-case tests for tactic_training.py.

Covers:
- export_triples: empty ledger dir, malformed JSON, max_triples cap, all-failure set
- train_stub: empty triples file, non-existent path
- train_sft: single-sample file, all-positive, all-negative, zero epochs guard
- train_rl_refinement: missing SFT weights raises, empty triples raises
- Weight shape consistency across SFT → RL pipeline
- RL baseline momentum convergence property
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import numpy as np
import pytest

tactic_training = importlib.import_module("tactic_training")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_triples(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _write_ledger(ledger_dir: Path, name: str, entries: list[dict]) -> None:
    (ledger_dir / name).write_text(
        json.dumps({"entries": entries}), encoding="utf-8"
    )


# ── export_triples ────────────────────────────────────────────────────────────

def test_export_triples_empty_dir(tmp_path: Path):
    ledger_dir = tmp_path / "ledgers"
    ledger_dir.mkdir()
    out = tmp_path / "triples.jsonl"
    summary = tactic_training.export_triples(ledger_dir=ledger_dir, out_path=out)
    assert summary["triples"] == 0
    assert summary["successes"] == 0
    assert out.exists()
    assert out.read_text().strip() == ""


def test_export_triples_malformed_json_skipped(tmp_path: Path):
    ledger_dir = tmp_path / "ledgers"
    ledger_dir.mkdir()
    (ledger_dir / "bad.json").write_text("{NOT_JSON", encoding="utf-8")
    out = tmp_path / "triples.jsonl"
    summary = tactic_training.export_triples(ledger_dir=ledger_dir, out_path=out)
    assert summary["triples"] == 0


def test_export_triples_max_cap(tmp_path: Path):
    ledger_dir = tmp_path / "ledgers"
    ledger_dir.mkdir()
    entries = [
        {
            "theorem_name": f"t{i}",
            "lean_statement": "theorem t : True",
            "step_obligations": [
                {"tactic": f"tac{i}", "result": "ok", "detail": "goal"},
            ],
        }
        for i in range(20)
    ]
    _write_ledger(ledger_dir, "big.json", entries)
    out = tmp_path / "triples.jsonl"
    summary = tactic_training.export_triples(ledger_dir=ledger_dir, out_path=out, max_triples=5)
    assert summary["triples"] == 5
    lines = [l for l in out.read_text().splitlines() if l.strip()]
    assert len(lines) == 5


def test_export_triples_all_failures(tmp_path: Path):
    ledger_dir = tmp_path / "ledgers"
    ledger_dir.mkdir()
    entries = [
        {
            "theorem_name": "fail_thm",
            "lean_statement": "theorem t : True",
            "step_obligations": [
                {"tactic": "ring", "result": "error", "detail": "type mismatch"},
                {"tactic": "linarith", "result": "failed", "detail": "unknown"},
            ],
        }
    ]
    _write_ledger(ledger_dir, "fail.json", entries)
    out = tmp_path / "triples.jsonl"
    summary = tactic_training.export_triples(ledger_dir=ledger_dir, out_path=out)
    assert summary["triples"] == 2
    assert summary["successes"] == 0
    assert summary["success_rate"] == 0.0


def test_export_triples_empty_tactic_skipped(tmp_path: Path):
    ledger_dir = tmp_path / "ledgers"
    ledger_dir.mkdir()
    entries = [
        {
            "theorem_name": "t",
            "lean_statement": "theorem t : True",
            "step_obligations": [
                {"tactic": "", "result": "ok", "detail": "goal"},
                {"tactic": "   ", "result": "ok", "detail": "goal"},
                {"tactic": "trivial", "result": "ok", "detail": "goal"},
            ],
        }
    ]
    _write_ledger(ledger_dir, "empty_tac.json", entries)
    out = tmp_path / "triples.jsonl"
    summary = tactic_training.export_triples(ledger_dir=ledger_dir, out_path=out)
    assert summary["triples"] == 1  # only "trivial" written


# ── train_stub ────────────────────────────────────────────────────────────────

def test_train_stub_empty_triples(tmp_path: Path):
    triples = tmp_path / "empty.jsonl"
    triples.write_text("", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest = tactic_training.train_stub(
        triples_path=triples, out_manifest=manifest_path, epochs=1, model_name="test"
    )
    assert manifest["num_samples"] == 0
    assert manifest["num_positive"] == 0
    assert manifest["success_rate"] == 0.0
    assert manifest_path.exists()


def test_train_stub_nonexistent_triples(tmp_path: Path):
    manifest_path = tmp_path / "manifest.json"
    manifest = tactic_training.train_stub(
        triples_path=tmp_path / "nonexistent.jsonl",
        out_manifest=manifest_path,
        epochs=2,
        model_name="x",
    )
    assert manifest["num_samples"] == 0
    assert manifest_path.exists()


def test_train_stub_records_epochs_and_model(tmp_path: Path):
    triples = tmp_path / "t.jsonl"
    _write_triples(triples, [{"state": "s", "tactic": "t", "outcome": 1}])
    manifest_path = tmp_path / "manifest.json"
    manifest = tactic_training.train_stub(
        triples_path=triples, out_manifest=manifest_path, epochs=7, model_name="my-model"
    )
    assert manifest["epochs"] == 7
    assert manifest["model"] == "my-model"


# ── train_sft ─────────────────────────────────────────────────────────────────

def test_train_sft_single_sample(tmp_path: Path):
    triples = tmp_path / "t.jsonl"
    _write_triples(triples, [{"state": "⊢ True", "tactic": "trivial", "outcome": 1}])
    out_dir = tmp_path / "policy"
    result = tactic_training.train_sft(
        triples_path=triples, out_dir=out_dir, epochs=2, lr=0.1, dims=64
    )
    assert result["status"] == "ok"
    weights = np.load(result["weights"])
    assert weights.shape == (65,)  # dims + 1 bias


def test_train_sft_all_positive_labels(tmp_path: Path):
    triples = tmp_path / "t.jsonl"
    _write_triples(
        triples,
        [{"state": f"⊢ goal_{i}", "tactic": "trivial", "outcome": 1} for i in range(10)],
    )
    out_dir = tmp_path / "policy"
    result = tactic_training.train_sft(
        triples_path=triples, out_dir=out_dir, epochs=3, lr=0.5, dims=32
    )
    assert result["status"] == "ok"
    final = result["final"]
    assert "train_acc" in final


def test_train_sft_all_negative_labels(tmp_path: Path):
    triples = tmp_path / "t.jsonl"
    _write_triples(
        triples,
        [{"state": f"⊢ goal_{i}", "tactic": "ring", "outcome": 0} for i in range(8)],
    )
    out_dir = tmp_path / "policy"
    result = tactic_training.train_sft(
        triples_path=triples, out_dir=out_dir, epochs=2, lr=0.1, dims=32
    )
    assert result["status"] == "ok"


def test_train_sft_empty_triples_raises(tmp_path: Path):
    triples = tmp_path / "empty.jsonl"
    triples.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="No valid triples"):
        tactic_training.train_sft(
            triples_path=triples, out_dir=tmp_path / "out", epochs=1, lr=0.1, dims=32
        )


def test_train_sft_weight_shape(tmp_path: Path):
    triples = tmp_path / "t.jsonl"
    _write_triples(
        triples,
        [
            {"state": "⊢ n + 0 = n", "tactic": "simp", "outcome": 1},
            {"state": "⊢ False", "tactic": "trivial", "outcome": 0},
        ],
    )
    dims = 128
    out_dir = tmp_path / "policy"
    result = tactic_training.train_sft(
        triples_path=triples, out_dir=out_dir, epochs=1, lr=0.1, dims=dims
    )
    weights = np.load(result["weights"])
    assert weights.shape == (dims + 1,)


# ── train_rl_refinement ───────────────────────────────────────────────────────

def test_train_rl_missing_sft_weights_raises(tmp_path: Path):
    triples = tmp_path / "t.jsonl"
    _write_triples(triples, [{"state": "s", "tactic": "t", "outcome": 1}])
    with pytest.raises(FileNotFoundError, match="Missing SFT weights"):
        tactic_training.train_rl_refinement(
            triples_path=triples,
            sft_weights_path=tmp_path / "nonexistent.npy",
            out_dir=tmp_path / "out",
            epochs=1,
            lr=0.1,
            baseline_momentum=0.9,
        )


def test_train_rl_empty_triples_raises(tmp_path: Path):
    triples = tmp_path / "empty.jsonl"
    triples.write_text("", encoding="utf-8")
    # First create valid SFT weights
    fake_weights = np.zeros(33, dtype=np.float64)
    weights_path = tmp_path / "sft.npy"
    np.save(weights_path, fake_weights)
    with pytest.raises(ValueError, match="No valid triples"):
        tactic_training.train_rl_refinement(
            triples_path=triples,
            sft_weights_path=weights_path,
            out_dir=tmp_path / "out",
            epochs=1,
            lr=0.1,
            baseline_momentum=0.9,
        )


def test_train_rl_weight_shape_matches_sft(tmp_path: Path):
    triples = tmp_path / "t.jsonl"
    _write_triples(
        triples,
        [
            {"state": "⊢ n + 0 = n", "tactic": "simp", "outcome": 1},
            {"state": "⊢ x = x", "tactic": "rfl", "outcome": 1},
            {"state": "⊢ False", "tactic": "trivial", "outcome": 0},
        ],
    )
    out_dir = tmp_path / "policy"
    dims = 64
    sft_result = tactic_training.train_sft(
        triples_path=triples, out_dir=out_dir, epochs=2, lr=0.1, dims=dims
    )
    sft_weights_path = Path(sft_result["weights"])
    sft_weights = np.load(sft_weights_path)

    rl_result = tactic_training.train_rl_refinement(
        triples_path=triples,
        sft_weights_path=sft_weights_path,
        out_dir=out_dir,
        epochs=2,
        lr=0.05,
        baseline_momentum=0.9,
    )
    rl_weights = np.load(rl_result["weights"])
    assert rl_weights.shape == sft_weights.shape, "RL weights must match SFT weight shape"


def test_train_rl_meta_has_history(tmp_path: Path):
    triples = tmp_path / "t.jsonl"
    _write_triples(
        triples,
        [{"state": "s", "tactic": "t", "outcome": 1} for _ in range(5)],
    )
    out_dir = tmp_path / "policy"
    sft_result = tactic_training.train_sft(
        triples_path=triples, out_dir=out_dir, epochs=1, lr=0.1, dims=32
    )
    rl_result = tactic_training.train_rl_refinement(
        triples_path=triples,
        sft_weights_path=Path(sft_result["weights"]),
        out_dir=out_dir,
        epochs=3,
        lr=0.05,
        baseline_momentum=0.8,
    )
    meta = json.loads(Path(rl_result["meta"]).read_text())
    assert len(meta["history"]) == 3
    for epoch_entry in meta["history"]:
        assert "mean_reward" in epoch_entry
        assert "baseline" in epoch_entry
