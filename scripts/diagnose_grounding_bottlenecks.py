#!/usr/bin/env python3
"""Diagnose why bridge grounding is failing (world-model and baseline)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from benchmark_bridge_world_model import run_benchmark


def _top_items(counter: dict[str, int], k: int = 15) -> list[dict[str, Any]]:
    items = sorted(counter.items(), key=lambda kv: (-int(kv[1]), kv[0]))
    return [{"reason": r, "count": int(c)} for r, c in items[: max(1, k)]]


def diagnose(
    *,
    ledger_root: Path,
    limit: int,
    budget: int,
    max_depth: int,
    max_candidates_per_assumption: int,
    baseline_lean_timeout_s: int = 60,
    baseline_max_repair_rounds: int = 2,
) -> dict[str, Any]:
    bench = run_benchmark(
        ledger_root=ledger_root,
        limit=limit,
        budget=budget,
        max_depth=max_depth,
        max_candidates_per_assumption=max_candidates_per_assumption,
        baseline_lean_timeout_s=max(5, int(baseline_lean_timeout_s)),
        baseline_max_repair_rounds=max(0, int(baseline_max_repair_rounds)),
    )
    wm_fr = bench.get("world_model_failure_reasons", {}) or {}
    bl_fr = bench.get("baseline_failure_reasons", {}) or {}
    return {
        "summary": {
            "count": int(bench.get("count", 0)),
            "world_model_grounded_total": int(bench.get("world_model_grounded_total", 0)),
            "baseline_grounded_total": int(bench.get("baseline_grounded_total", 0)),
            "delta_grounded": int(bench.get("delta_grounded", 0)),
        },
        "world_model_top_bottlenecks": _top_items({str(k): int(v) for k, v in wm_fr.items()}),
        "baseline_top_bottlenecks": _top_items({str(k): int(v) for k, v in bl_fr.items()}),
        "raw_failure_reasons": {
            "world_model": wm_fr,
            "baseline": bl_fr,
        },
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Diagnose bridge grounding bottlenecks")
    p.add_argument("--ledger-root", default="output/verification_ledgers")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--budget", type=int, default=40)
    p.add_argument("--max-depth", type=int, default=4)
    p.add_argument("--max-candidates-per-assumption", type=int, default=3)
    p.add_argument("--baseline-lean-timeout-s", type=int, default=60)
    p.add_argument("--baseline-max-repair-rounds", type=int, default=2)
    p.add_argument("--out", default="")
    args = p.parse_args()
    payload = diagnose(
        ledger_root=Path(args.ledger_root),
        limit=max(1, int(args.limit)),
        budget=max(1, int(args.budget)),
        max_depth=max(1, int(args.max_depth)),
        max_candidates_per_assumption=max(1, int(args.max_candidates_per_assumption)),
        baseline_lean_timeout_s=max(5, int(args.baseline_lean_timeout_s)),
        baseline_max_repair_rounds=max(0, int(args.baseline_max_repair_rounds)),
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
