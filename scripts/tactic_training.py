#!/usr/bin/env python3
"""Minimal tactic training data utilities.

Subcommands:
- export-triples: extract (state, tactic, outcome) triples from verification ledgers
- trainer-stub: build a lightweight manifest from triples for downstream training
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np

_SUCCESS_RESULTS = {
    "ok",
    "success",
    "succeeded",
    "proved",
    "qed",
    "goal_closed",
    "state_advanced",
    "advanced",
}


def _iter_ledger_entries(ledger_dir: Path):
    for ledger_path in sorted(ledger_dir.glob("*.json")):
        try:
            payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            entries = payload.get("entries")
        elif isinstance(payload, list):
            entries = payload
        else:
            entries = None
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict):
                yield ledger_path, entry


def _outcome_from_result(result: str) -> int:
    norm = (result or "").strip().lower()
    if any(token in norm for token in _SUCCESS_RESULTS):
        return 1
    return 0


def export_triples(
    *,
    ledger_dir: Path,
    out_path: Path,
    max_triples: int = 0,
) -> dict:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    success = 0
    with out_path.open("w", encoding="utf-8") as fh:
        for ledger_path, entry in _iter_ledger_entries(ledger_dir):
            theorem_name = str(entry.get("theorem_name", ""))
            state_fallback = str(entry.get("lean_statement", ""))
            obligations = entry.get("step_obligations")
            if not isinstance(obligations, list):
                continue

            for step_index, ob in enumerate(obligations):
                if not isinstance(ob, dict):
                    continue
                tactic = str(ob.get("tactic", "")).strip()
                if not tactic:
                    continue
                result = str(ob.get("result", "")).strip()
                detail = str(ob.get("detail", "")).strip()
                state = detail or state_fallback
                outcome = _outcome_from_result(result)
                success += outcome

                row = {
                    "state": state,
                    "tactic": tactic,
                    "outcome": outcome,
                    "result": result,
                    "theorem_name": theorem_name,
                    "step_index": step_index,
                    "source_ledger": ledger_path.name,
                }
                fh.write(json.dumps(row, ensure_ascii=True) + "\n")
                written += 1
                if max_triples > 0 and written >= max_triples:
                    return {
                        "triples": written,
                        "successes": success,
                        "success_rate": (success / written) if written else 0.0,
                        "output": str(out_path),
                    }

    return {
        "triples": written,
        "successes": success,
        "success_rate": (success / written) if written else 0.0,
        "output": str(out_path),
    }


def train_stub(
    *,
    triples_path: Path,
    out_manifest: Path,
    epochs: int,
    model_name: str,
) -> dict:
    total = 0
    success = 0
    if triples_path.exists():
        with triples_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                total += 1
                success += int(row.get("outcome", 0) == 1)

    out_manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "task": "tactic_policy_training_stub",
        "triples_path": str(triples_path),
        "num_samples": total,
        "num_positive": success,
        "success_rate": (success / total) if total else 0.0,
        "epochs": int(epochs),
        "model": model_name,
        "recommended_command": (
            "python -m torch.distributed.run --nproc_per_node=1 "
            "train_tactic_policy.py --triples "
            f"{triples_path} --epochs {int(epochs)} --model {model_name}"
        ),
        "status": "stub_only_no_training_executed",
    }
    out_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _iter_triples(triples_path: Path):
    if not triples_path.exists():
        return
    with triples_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            state = str(row.get("state", "")).strip()
            tactic = str(row.get("tactic", "")).strip()
            outcome = int(row.get("outcome", 0) == 1)
            if not state or not tactic:
                continue
            yield state, tactic, outcome


def _stable_hash_int(text: str, mod: int) -> int:
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return int(h[:16], 16) % mod


def _featurize(state: str, tactic: str, dims: int) -> np.ndarray:
    vec = np.zeros(dims + 1, dtype=np.float64)
    vec[0] = 1.0  # bias
    tokens = [tok for tok in (state + " " + tactic).split() if tok]
    for tok in tokens:
        idx = 1 + _stable_hash_int(tok.lower(), dims)
        vec[idx] += 1.0
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _evaluate_binary(weights: np.ndarray, rows: list[tuple[np.ndarray, int]]) -> dict:
    if not rows:
        return {"samples": 0, "loss": 0.0, "accuracy": 0.0}
    loss = 0.0
    correct = 0
    for x, y in rows:
        p = _sigmoid(float(np.dot(weights, x)))
        p_clamped = min(max(p, 1e-8), 1.0 - 1e-8)
        loss += -(y * math.log(p_clamped) + (1 - y) * math.log(1.0 - p_clamped))
        pred = 1 if p >= 0.5 else 0
        correct += int(pred == y)
    return {
        "samples": len(rows),
        "loss": loss / len(rows),
        "accuracy": correct / len(rows),
    }


def train_sft(
    *,
    triples_path: Path,
    out_dir: Path,
    epochs: int,
    lr: float,
    dims: int,
) -> dict:
    rows: list[tuple[np.ndarray, int]] = []
    for state, tactic, outcome in _iter_triples(triples_path):
        rows.append((_featurize(state, tactic, dims), outcome))

    if not rows:
        raise ValueError(f"No valid triples found in {triples_path}")

    split = max(1, int(len(rows) * 0.9))
    train_rows = rows[:split]
    val_rows = rows[split:] if split < len(rows) else rows[-1:]

    weights = np.zeros(dims + 1, dtype=np.float64)
    history: list[dict] = []

    for epoch in range(1, max(1, epochs) + 1):
        grad = np.zeros_like(weights)
        for x, y in train_rows:
            p = _sigmoid(float(np.dot(weights, x)))
            grad += (p - y) * x
        grad /= max(1, len(train_rows))
        weights -= lr * grad

        train_eval = _evaluate_binary(weights, train_rows)
        val_eval = _evaluate_binary(weights, val_rows)
        history.append(
            {
                "epoch": epoch,
                "train_loss": train_eval["loss"],
                "train_acc": train_eval["accuracy"],
                "val_loss": val_eval["loss"],
                "val_acc": val_eval["accuracy"],
            }
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    weights_path = out_dir / "sft_weights.npy"
    meta_path = out_dir / "sft_meta.json"
    np.save(weights_path, weights)
    meta = {
        "task": "tactic_policy_sft",
        "triples_path": str(triples_path),
        "dims": dims,
        "epochs": int(epochs),
        "lr": float(lr),
        "num_samples": len(rows),
        "history": history,
        "weights_path": str(weights_path),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {
        "status": "ok",
        "weights": str(weights_path),
        "meta": str(meta_path),
        "final": history[-1] if history else {},
    }


def train_rl_refinement(
    *,
    triples_path: Path,
    sft_weights_path: Path,
    out_dir: Path,
    epochs: int,
    lr: float,
    baseline_momentum: float,
) -> dict:
    if not sft_weights_path.exists():
        raise FileNotFoundError(f"Missing SFT weights: {sft_weights_path}")

    weights = np.load(sft_weights_path)
    dims = int(weights.shape[0] - 1)
    rows: list[tuple[np.ndarray, int]] = []
    for state, tactic, outcome in _iter_triples(triples_path):
        rows.append((_featurize(state, tactic, dims), outcome))
    if not rows:
        raise ValueError(f"No valid triples found in {triples_path}")

    baseline = 0.0
    history: list[dict] = []
    for epoch in range(1, max(1, epochs) + 1):
        grad = np.zeros_like(weights)
        rewards: list[float] = []
        for x, y in rows:
            p = _sigmoid(float(np.dot(weights, x)))
            reward = 1.0 if y == 1 else -1.0
            baseline = baseline_momentum * baseline + (1.0 - baseline_momentum) * reward
            advantage = reward - baseline
            # REINFORCE for observed action=1 (chosen tactic), nudged by advantage.
            grad += -advantage * (1.0 - p) * x
            rewards.append(reward)
        grad /= max(1, len(rows))
        weights -= lr * grad

        eval_all = _evaluate_binary(weights, rows)
        history.append(
            {
                "epoch": epoch,
                "loss": eval_all["loss"],
                "accuracy": eval_all["accuracy"],
                "mean_reward": float(sum(rewards) / max(1, len(rewards))),
                "baseline": baseline,
            }
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    rl_weights_path = out_dir / "rl_weights.npy"
    rl_meta_path = out_dir / "rl_meta.json"
    np.save(rl_weights_path, weights)
    meta = {
        "task": "tactic_policy_rl_refinement",
        "triples_path": str(triples_path),
        "init_sft_weights": str(sft_weights_path),
        "epochs": int(epochs),
        "lr": float(lr),
        "baseline_momentum": float(baseline_momentum),
        "weights_path": str(rl_weights_path),
        "history": history,
    }
    rl_meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {
        "status": "ok",
        "weights": str(rl_weights_path),
        "meta": str(rl_meta_path),
        "final": history[-1] if history else {},
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tactic data exporter and trainer")
    sub = parser.add_subparsers(dest="command", required=True)

    export_parser = sub.add_parser("export-triples", help="Export (state,tactic,outcome) triples")
    export_parser.add_argument(
        "--ledger-dir",
        default="output/verification_ledgers",
        help="Directory containing verification ledger JSON files",
    )
    export_parser.add_argument(
        "--out",
        default="output/research/tactic_triples.jsonl",
        help="Output JSONL path",
    )
    export_parser.add_argument(
        "--max-triples",
        type=int,
        default=0,
        help="Optional cap on exported rows (0 = no cap)",
    )

    train_parser = sub.add_parser("train-stub", help="Write training manifest from triples")
    train_parser.add_argument("--triples", default="output/research/tactic_triples.jsonl")
    train_parser.add_argument("--out", default="output/reports/tactic_trainer_stub_manifest.json")
    train_parser.add_argument("--epochs", type=int, default=1)
    train_parser.add_argument("--model", default="tactic-policy-baseline")

    sft_parser = sub.add_parser("train-sft", help="Train lightweight tactic policy (SFT)")
    sft_parser.add_argument("--triples", default="output/research/tactic_triples.jsonl")
    sft_parser.add_argument("--out-dir", default="output/research/tactic_policy")
    sft_parser.add_argument("--epochs", type=int, default=8)
    sft_parser.add_argument("--lr", type=float, default=0.5)
    sft_parser.add_argument("--dims", type=int, default=2048)

    rl_parser = sub.add_parser("train-rl", help="Refine tactic policy with REINFORCE-style updates")
    rl_parser.add_argument("--triples", default="output/research/tactic_triples.jsonl")
    rl_parser.add_argument("--sft-weights", default="output/research/tactic_policy/sft_weights.npy")
    rl_parser.add_argument("--out-dir", default="output/research/tactic_policy")
    rl_parser.add_argument("--epochs", type=int, default=5)
    rl_parser.add_argument("--lr", type=float, default=0.2)
    rl_parser.add_argument("--baseline-momentum", type=float, default=0.95)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "export-triples":
        summary = export_triples(
            ledger_dir=Path(args.ledger_dir),
            out_path=Path(args.out),
            max_triples=max(0, int(args.max_triples)),
        )
    elif args.command == "train-stub":
        summary = train_stub(
            triples_path=Path(args.triples),
            out_manifest=Path(args.out),
            epochs=max(1, int(args.epochs)),
            model_name=str(args.model),
        )
    elif args.command == "train-sft":
        summary = train_sft(
            triples_path=Path(args.triples),
            out_dir=Path(args.out_dir),
            epochs=max(1, int(args.epochs)),
            lr=float(args.lr),
            dims=max(64, int(args.dims)),
        )
    elif args.command == "train-rl":
        summary = train_rl_refinement(
            triples_path=Path(args.triples),
            sft_weights_path=Path(args.sft_weights),
            out_dir=Path(args.out_dir),
            epochs=max(1, int(args.epochs)),
            lr=float(args.lr),
            baseline_momentum=min(0.999, max(0.0, float(args.baseline_momentum))),
        )
    else:
        parser.error(f"unknown command: {args.command}")
        return 2

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
