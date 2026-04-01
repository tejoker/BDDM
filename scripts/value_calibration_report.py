#!/usr/bin/env python3
"""Summarize URM value calibration signals from verification ledgers.

Reads `value-estimate` step events emitted by draft MCTS traces and reports
raw/normalized distributions, cache-hit rates, and simple calibration buckets.

Examples:
  python3 scripts/value_calibration_report.py
  python3 scripts/value_calibration_report.py --paper 2304.09598
  python3 scripts/value_calibration_report.py --dir output/verification_ledgers --show-top 20
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def _iter_ledger_files(ledger_dir: Path, paper: str) -> list[Path]:
    if paper:
        safe = paper.replace("/", "_").replace(":", "_")
        p = ledger_dir / f"{safe}.json"
        return [p] if p.exists() else []
    return sorted(ledger_dir.glob("*.json"))


def _load_entries(path: Path) -> list[dict]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict):
        rows = raw.get("entries", [])
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    return []


def _extract_value_events(entry: dict) -> list[dict]:
    events: list[dict] = []
    for obligation in entry.get("step_obligations", []):
        if not isinstance(obligation, dict):
            continue
        if str(obligation.get("result", "")).strip().lower() != "value-estimate":
            continue
        detail = str(obligation.get("detail", "")).strip()
        if not detail:
            continue
        try:
            payload = json.loads(detail)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        events.append(payload)
    return events


def _bucket(v: float) -> str:
    if v < 0.2:
        return "[0.0,0.2)"
    if v < 0.4:
        return "[0.2,0.4)"
    if v < 0.6:
        return "[0.4,0.6)"
    if v < 0.8:
        return "[0.6,0.8)"
    return "[0.8,1.0]"


def main() -> int:
    p = argparse.ArgumentParser(description="Summarize URM value calibration from ledger traces")
    p.add_argument("--dir", default="output/verification_ledgers", help="Ledger directory")
    p.add_argument("--paper", default="", help="Single paper id (e.g. 2304.09598)")
    p.add_argument("--show-top", type=int, default=10, help="Show top N theorem rows by #samples")
    args = p.parse_args()

    ledger_dir = Path(args.dir)
    if not ledger_dir.exists():
        print(f"[fail] ledger directory not found: {ledger_dir}")
        return 1

    files = _iter_ledger_files(ledger_dir, args.paper)
    if not files:
        print("[fail] no ledger files matched")
        return 1

    samples: list[dict] = []
    theorem_sample_counts: dict[str, int] = defaultdict(int)
    theorem_means: dict[str, list[float]] = defaultdict(list)

    for path in files:
        for row in _load_entries(path):
            theorem_name = str(row.get("theorem_name", "?"))
            events = _extract_value_events(row)
            theorem_sample_counts[theorem_name] += len(events)
            for ev in events:
                raw = float(ev.get("raw_value", 0.0) or 0.0)
                normalized = float(ev.get("normalized_value", 0.0) or 0.0)
                tactics_estimate = ev.get("tactics_estimate", None)
                cache_hit = bool(ev.get("cache_hit", False))
                samples.append(
                    {
                        "theorem": theorem_name,
                        "raw": raw,
                        "normalized": normalized,
                        "delta": normalized - raw,
                        "tactics_estimate": tactics_estimate,
                        "cache_hit": cache_hit,
                    }
                )
                theorem_means[theorem_name].append(normalized)

    if not samples:
        print("[info] no value-estimate samples found in selected ledgers")
        print("[hint] run mcts-draft mode after Sprint 2 changes to populate calibration events")
        return 0

    n = len(samples)
    avg_raw = sum(s["raw"] for s in samples) / n
    avg_norm = sum(s["normalized"] for s in samples) / n
    avg_delta = sum(s["delta"] for s in samples) / n
    cache_hits = sum(1 for s in samples if s["cache_hit"])
    with_tactics = [s for s in samples if s["tactics_estimate"] is not None]

    raw_buckets = Counter(_bucket(s["raw"]) for s in samples)
    norm_buckets = Counter(_bucket(s["normalized"]) for s in samples)

    print("VALUE CALIBRATION SUMMARY")
    print(f"  files={len(files)} samples={n}")
    print(f"  avg_raw={avg_raw:.4f} avg_normalized={avg_norm:.4f} avg_delta={avg_delta:.4f}")
    print(f"  cache_hits={cache_hits}/{n}")
    print(f"  tactics_estimate_present={len(with_tactics)}/{n}")
    print(f"  raw_buckets={dict(raw_buckets)}")
    print(f"  normalized_buckets={dict(norm_buckets)}")

    ranked = sorted(
        theorem_sample_counts.items(),
        key=lambda kv: (kv[1], kv[0]),
        reverse=True,
    )

    show_top = max(1, args.show_top)
    print("\nTOP THEOREMS BY CALIBRATION SAMPLES")
    for theorem, count in ranked[:show_top]:
        vals = theorem_means.get(theorem, [])
        mean_norm = (sum(vals) / len(vals)) if vals else 0.0
        print(f"  - {theorem}: samples={count} mean_normalized={mean_norm:.4f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
