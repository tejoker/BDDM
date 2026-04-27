#!/usr/bin/env python3
"""A/B comparator: world-model bridge vs baseline bridge."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from benchmark_bridge_world_model import run_benchmark


def _sign_test_pvalue(wins: int, losses: int) -> float:
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    # two-sided exact sign test
    tail = 0.0
    for i in range(0, k + 1):
        tail += math.comb(n, i) * (0.5 ** n)
    return min(1.0, 2.0 * tail)


def evaluate_ab(payload: dict[str, Any]) -> dict[str, Any]:
    wins = losses = ties = 0
    for row in payload.get("rows", []):
        if not isinstance(row, dict):
            continue
        wm = int(((row.get("world_model") or {}).get("grounded_count", 0)))
        bl = int(((row.get("baseline_text_bridge") or {}).get("grounded_count", 0)))
        if wm > bl:
            wins += 1
        elif wm < bl:
            losses += 1
        else:
            ties += 1
    p_value = _sign_test_pvalue(wins, losses)
    return {
        "count": int(payload.get("count", 0)),
        "wins_world_model": wins,
        "losses_world_model": losses,
        "ties": ties,
        "delta_grounded": int(payload.get("delta_grounded", 0)),
        "sign_test_pvalue": round(p_value, 6),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="A/B compare world-model bridge against baseline")
    p.add_argument("--ledger-root", default="output/verification_ledgers")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--budget", type=int, default=40)
    p.add_argument("--max-depth", type=int, default=4)
    p.add_argument("--max-candidates-per-assumption", type=int, default=3)
    p.add_argument("--baseline-lean-timeout-s", type=int, default=60)
    p.add_argument("--baseline-max-repair-rounds", type=int, default=2)
    p.add_argument("--retrieval-memory-path", default="", help="Optional retrieval memory JSON path")
    p.add_argument("--out", default="")
    args = p.parse_args()
    raw = run_benchmark(
        ledger_root=Path(args.ledger_root),
        limit=max(1, int(args.limit)),
        budget=max(1, int(args.budget)),
        max_depth=max(1, int(args.max_depth)),
        max_candidates_per_assumption=max(1, int(args.max_candidates_per_assumption)),
        baseline_lean_timeout_s=max(5, int(args.baseline_lean_timeout_s)),
        baseline_max_repair_rounds=max(0, int(args.baseline_max_repair_rounds)),
        retrieval_memory_path=Path(args.retrieval_memory_path) if args.retrieval_memory_path else None,
    )
    summary = evaluate_ab(raw)
    payload = {"summary": summary, "raw": raw}
    txt = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(txt, encoding="utf-8")
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
