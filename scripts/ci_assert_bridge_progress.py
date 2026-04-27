#!/usr/bin/env python3
"""Assert bridge-specific weekly KPI and canary-release gates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def main() -> int:
    p = argparse.ArgumentParser(description="Assert bridge KPI and canary release gates")
    p.add_argument("--weekly-report", required=True)
    p.add_argument("--min-hard-safe-yield", type=float, default=0.05)
    p.add_argument("--min-slot-coverage-pass-rate", type=float, default=0.90)
    p.add_argument("--max-candidate-empty-rate", type=float, default=0.50)
    p.add_argument("--max-canary-drop", type=float, default=0.05, help="max allowed safe-yield drop vs previous")
    p.add_argument("--prev-weekly-report", default="")
    args = p.parse_args()

    rep = _load(Path(args.weekly_report))
    kpis = ((rep.get("benchmark_world_model") or {}).get("kpis") or {})
    hard = float(kpis.get("hard_safe_yield", 0.0))
    slot = float(kpis.get("slot_coverage_pass_rate", 0.0))
    empty = float(kpis.get("candidate_empty_rate", 1.0))

    ok = True
    if hard < float(args.min_hard_safe_yield):
        print(f"[gate_fail] hard_safe_yield={hard:.4f} < {args.min_hard_safe_yield:.4f}")
        ok = False
    if slot < float(args.min_slot_coverage_pass_rate):
        print(f"[gate_fail] slot_coverage_pass_rate={slot:.4f} < {args.min_slot_coverage_pass_rate:.4f}")
        ok = False
    if empty > float(args.max_candidate_empty_rate):
        print(f"[gate_fail] candidate_empty_rate={empty:.4f} > {args.max_candidate_empty_rate:.4f}")
        ok = False

    if args.prev_weekly_report:
        prev = _load(Path(args.prev_weekly_report))
        prev_kpis = ((prev.get("benchmark_world_model") or {}).get("kpis") or {})
        prev_hard = float(prev_kpis.get("hard_safe_yield", hard))
        drop = prev_hard - hard
        if drop > float(args.max_canary_drop):
            print(f"[gate_fail] canary_drop={drop:.4f} > {args.max_canary_drop:.4f}")
            ok = False

    if ok:
        print(
            f"[gate_ok] hard_safe_yield={hard:.4f} slot_coverage={slot:.4f} "
            f"candidate_empty={empty:.4f}"
        )
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
