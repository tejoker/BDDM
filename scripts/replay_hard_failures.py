#!/usr/bin/env python3
"""Replay queue for hard failed theorem slots from bridge failure artifacts."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from world_model_bridge import compare_against_baseline


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def build_replay_queue(*, artifacts_root: Path, max_items: int) -> list[str]:
    files = sorted(artifacts_root.glob("*.jsonl"), reverse=True)
    counts: dict[str, int] = {}
    priority = {
        "lean_type_mismatch": 6,
        "slot_mismatch": 5,
        "quantifier_loss": 4,
        "non_actionable_candidate": 3,
        "symbol_drift": 2,
    }
    for fp in files:
        for row in _load_jsonl(fp):
            theorem = str(row.get("target_theorem", "")).strip()
            if not theorem:
                continue
            taxonomy = str(row.get("taxonomy", "")).strip()
            if taxonomy not in priority:
                continue
            w = priority[taxonomy]
            counts[theorem] = counts.get(theorem, 0) + w
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return [name for name, _ in ranked[: max(1, max_items)]]


def replay(
    *,
    artifacts_root: Path,
    ledger_root: Path,
    max_items: int,
    budget: int,
    max_depth: int,
    max_candidates_per_assumption: int,
    retrieval_memory_path: Path | None,
) -> dict[str, Any]:
    queue = build_replay_queue(artifacts_root=artifacts_root, max_items=max_items)
    rows: list[dict[str, Any]] = []
    for theorem in queue:
        rows.append(
            compare_against_baseline(
                target_theorem=theorem,
                ledger_root=ledger_root,
                budget=budget,
                max_depth=max_depth,
                max_candidates_per_assumption=max_candidates_per_assumption,
                retrieval_memory_path=retrieval_memory_path,
            )
        )
    wm = sum(int((r.get("world_model", {}) or {}).get("grounded_count", 0)) for r in rows)
    bl = sum(int((r.get("baseline_text_bridge", {}) or {}).get("grounded_count", 0)) for r in rows)
    return {
        "generated_at_unix": int(time.time()),
        "queue_size": len(queue),
        "targets": queue,
        "world_model_grounded_total": wm,
        "baseline_grounded_total": bl,
        "delta_grounded": wm - bl,
        "rows": rows,
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Replay hard failed bridge targets")
    p.add_argument("--artifacts-root", default="output/reports/bridge_failures")
    p.add_argument("--ledger-root", default="output/verification_ledgers")
    p.add_argument("--max-items", type=int, default=50)
    p.add_argument("--budget", type=int, default=40)
    p.add_argument("--max-depth", type=int, default=4)
    p.add_argument("--max-candidates-per-assumption", type=int, default=3)
    p.add_argument("--retrieval-memory-path", default="")
    p.add_argument("--out", default="")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    payload = replay(
        artifacts_root=Path(args.artifacts_root),
        ledger_root=Path(args.ledger_root),
        max_items=max(1, int(args.max_items)),
        budget=max(1, int(args.budget)),
        max_depth=max(1, int(args.max_depth)),
        max_candidates_per_assumption=max(1, int(args.max_candidates_per_assumption)),
        retrieval_memory_path=Path(args.retrieval_memory_path) if args.retrieval_memory_path else None,
    )
    txt = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(txt, encoding="utf-8")
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
