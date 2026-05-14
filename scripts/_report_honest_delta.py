#!/usr/bin/env python3
"""Print honest FP/AB/IP/UR counts across the canonical 8 papers from either
the ephemeral ledgers (`output/verification_ledgers/<id>.json`) or the
canonical reproducibility ledgers (`reproducibility/full_paper_reports/<id>/
verification_ledger.json`).

Used to compute the honest delta before/after a sweep + audit + mirror cycle.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

CANONICAL = [
    "2012.09271",
    "2304.09598",
    "2401.04567",
    "2604.21314",
    "2604.21583",
    "2604.21616",
    "2604.21821",
    "2604.21884",
]


def _count(path: Path) -> dict[str, int]:
    out = {"FP": 0, "AB": 0, "IP": 0, "UR": 0, "TL": 0, "FL": 0}
    if not path.exists():
        return out
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data if isinstance(data, list) else data.get("entries", [])
    for e in entries:
        s = str(e.get("status", "") or "")
        key = {
            "FULLY_PROVEN": "FP",
            "AXIOM_BACKED": "AB",
            "INTERMEDIARY_PROVEN": "IP",
            "UNRESOLVED": "UR",
            "TIMED_OUT": "TL",
            "FLAWED": "FL",
        }.get(s)
        if key:
            out[key] += 1
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["ephemeral", "canonical"], default="canonical")
    parser.add_argument("--paper", action="append", default=[])
    args = parser.parse_args()

    papers = args.paper or CANONICAL
    if args.source == "canonical":
        get_path = lambda pid: Path("reproducibility/full_paper_reports") / pid / "verification_ledger.json"
    else:
        get_path = lambda pid: Path("output/verification_ledgers") / f"{pid}.json"

    total = {"FP": 0, "AB": 0, "IP": 0, "UR": 0, "TL": 0, "FL": 0}
    per_paper = {}
    for pid in papers:
        c = _count(get_path(pid))
        per_paper[pid] = c
        for k, v in c.items():
            total[k] += v
    print(f"Source: {args.source}")
    for pid in papers:
        c = per_paper[pid]
        print(f"  {pid}: FP={c['FP']} AB={c['AB']} IP={c['IP']} UR={c['UR']} TL={c['TL']} FL={c['FL']}")
    print(f"TOTAL: FP={total['FP']} AB={total['AB']} IP={total['IP']} UR={total['UR']} TL={total['TL']} FL={total['FL']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
