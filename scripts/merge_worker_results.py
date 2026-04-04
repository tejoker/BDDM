#!/usr/bin/env python3
"""Merge benchmark results from multiple parallel worker output directories.

Each worker produces output/mcts_244_wN/ with per-problem JSON files.
This script merges them into a single results summary with pass@1 and
per-problem outcome records.

Usage:
    python scripts/merge_worker_results.py output/mcts_w1 output/mcts_w2 ...
    python scripts/merge_worker_results.py output/mcts_244_w{1,2,3,4}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_results(out_dir: Path) -> list[dict]:
    """Load per-problem result JSON files from a worker output directory."""
    results = []
    for f in sorted(out_dir.glob("*.json")):
        if f.name.startswith("summary"):
            continue
        try:
            results.append(json.loads(f.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            pass
    # Also try a single results.json or summary.json
    for name in ("results.json", "benchmark_results.json"):
        p = out_dir / name
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    results.extend(data)
                elif isinstance(data, dict) and "results" in data:
                    results.extend(data["results"])
            except (ValueError, OSError):
                pass
    return results


def merge_results(out_dirs: list[Path]) -> dict:
    all_results: list[dict] = []
    for d in out_dirs:
        if not d.exists():
            print(f"Warning: {d} does not exist — skipping", file=sys.stderr)
            continue
        r = _load_results(d)
        print(f"  {d}: {len(r)} problems loaded")
        all_results.extend(r)

    if not all_results:
        return {"total": 0, "solved": 0, "pass_at_1": 0.0, "results": []}

    # Deduplicate by theorem_name if any overlap.
    seen: set[str] = set()
    deduped = []
    for r in all_results:
        key = r.get("theorem_name") or r.get("name") or str(r)
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    total = len(deduped)
    solved = sum(1 for r in deduped if r.get("solved") or r.get("pass") or r.get("status") == "proved")
    pass_at_1 = solved / total if total > 0 else 0.0

    return {
        "total": total,
        "solved": solved,
        "pass_at_1": round(pass_at_1, 4),
        "results": deduped,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Merge parallel worker benchmark results")
    p.add_argument("out_dirs", nargs="+", help="Worker output directories")
    p.add_argument("--out", default="output/mcts_merged_results.json", help="Output file")
    args = p.parse_args()

    dirs = [Path(d) for d in args.out_dirs]
    print(f"Merging {len(dirs)} worker result directories...")
    merged = merge_results(dirs)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nMerged results:")
    print(f"  Total problems : {merged['total']}")
    print(f"  Solved         : {merged['solved']}")
    print(f"  pass@1         : {merged['pass_at_1']:.1%}")
    print(f"  Written to     : {out}")


if __name__ == "__main__":
    main()
