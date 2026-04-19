#!/usr/bin/env python3
"""Benchmark world-model bridge scaffold vs current text bridge pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from world_model_bridge import compare_against_baseline


def _iter_target_theorems(ledger_root: Path, limit: int) -> list[str]:
    out: list[str] = []
    for path in sorted(ledger_root.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = raw.get("entries", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            assumptions = row.get("assumptions", [])
            if not isinstance(assumptions, list):
                continue
            has_ungrounded = any(
                isinstance(a, dict) and str(a.get("grounding", "")).upper() in {"UNGROUNDED", "UNKNOWN", ""}
                for a in assumptions
            )
            if not has_ungrounded:
                continue
            name = str(row.get("theorem_name", "")).strip()
            if name:
                out.append(name)
            if len(out) >= limit:
                return out
    return out


def run_benchmark(
    *,
    ledger_root: Path,
    limit: int,
    budget: int,
    max_depth: int,
    max_candidates_per_assumption: int,
) -> dict[str, Any]:
    targets = _iter_target_theorems(ledger_root, limit=limit)
    rows: list[dict[str, Any]] = []
    wm_grounded = 0
    baseline_grounded = 0

    for t in targets:
        comp = compare_against_baseline(
            target_theorem=t,
            ledger_root=ledger_root,
            budget=budget,
            max_depth=max_depth,
            max_candidates_per_assumption=max_candidates_per_assumption,
        )
        rows.append(comp)
        wm_grounded += int(comp["world_model"]["grounded_count"])
        baseline_grounded += int(comp["baseline_text_bridge"]["grounded_count"])

    return {
        "targets": targets,
        "count": len(targets),
        "world_model_grounded_total": wm_grounded,
        "baseline_grounded_total": baseline_grounded,
        "delta_grounded": wm_grounded - baseline_grounded,
        "rows": rows,
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Compare world-model bridge scaffold against baseline")
    p.add_argument("--ledger-root", default="output/verification_ledgers")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--budget", type=int, default=40)
    p.add_argument("--max-depth", type=int, default=4)
    p.add_argument("--max-candidates-per-assumption", type=int, default=3)
    p.add_argument("--out", default="", help="Optional JSON output path")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    payload = run_benchmark(
        ledger_root=Path(args.ledger_root),
        limit=max(1, args.limit),
        budget=max(1, args.budget),
        max_depth=max(1, args.max_depth),
        max_candidates_per_assumption=max(1, args.max_candidates_per_assumption),
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

