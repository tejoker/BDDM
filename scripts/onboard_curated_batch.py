#!/usr/bin/env python3
"""Batch-onboard a curated paper list for the closure-rate dilution play.

Reads `data/curated_easy_corpus.txt` (or any list file), filters comments,
then runs `scripts/onboard_arxiv_paper.py <id>` on each. Per-paper timing
+ closure summary written to `logs/curated_batch_<timestamp>.json`.

Use this to scale the corpus from 7 → 27+ papers in one shot, which is
what the dataset paper needs (50+ papers eventually). Each paper takes
30-90 minutes wall-clock for the full pipeline; budget ~12 hours for 20
papers, including some translator-failure retries.

Usage:
    python3 scripts/onboard_curated_batch.py
        [--list data/curated_easy_corpus.txt]
        [--limit 5]              # cap onboarded papers
        [--skip-prove]           # cheaper preview run
        [--max-prove-time 1200]  # per-paper prove budget seconds
        [--publish]              # mirror successful papers to reproducibility/
        [--continue-on-fail]     # keep going on per-paper failures
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
LOGS = ROOT / "logs"

_ARXIV_ID_RE = re.compile(r"^\s*(\d{4}\.\d{4,6}(?:v\d+)?)")


def _read_paper_list(path: Path) -> list[str]:
    """Read paper IDs from a file, skipping blank lines and comments."""
    if not path.exists():
        return []
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _ARXIV_ID_RE.match(line)
        if m:
            out.append(m.group(1))
    return out


def _final_closure(paper_id: str) -> dict[str, Any] | None:
    """Read the per-paper ledger and compute closed/total."""
    from collections import Counter
    ledger_path = ROOT / "output" / "verification_ledgers" / f"{paper_id}.json"
    if not ledger_path.exists():
        return None
    try:
        d = json.loads(ledger_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    entries = d if isinstance(d, list) else d.get("entries", [])
    counts = Counter(r.get("status", "") for r in entries)
    closed = sum(counts.get(s, 0) for s in ("FULLY_PROVEN", "AXIOM_BACKED", "INTERMEDIARY_PROVEN"))
    return {
        "rows": len(entries),
        "closed": closed,
        "closure_pct": round(closed / max(1, len(entries)) * 100, 1),
        "status": dict(counts),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch-onboard a curated paper list")
    parser.add_argument("--list", type=Path, default=ROOT / "data" / "curated_easy_corpus.txt")
    parser.add_argument("--limit", type=int, default=0, help="Cap on number of papers (0=all)")
    parser.add_argument("--skip-prove", action="store_true")
    parser.add_argument("--skip-cot-review", action="store_true")
    parser.add_argument("--max-prove-time", type=int, default=2400)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--continue-on-fail", action="store_true")
    args = parser.parse_args()

    LOGS.mkdir(parents=True, exist_ok=True)

    paper_ids = _read_paper_list(args.list)
    if not paper_ids:
        print(f"No paper IDs found in {args.list}", file=sys.stderr)
        return 2
    if args.limit > 0:
        paper_ids = paper_ids[: args.limit]

    print(f"=== Batch onboarding {len(paper_ids)} papers ===\n")
    started_at = time.time()
    results: list[dict[str, Any]] = []
    for i, pid in enumerate(paper_ids, start=1):
        print(f"[{i}/{len(paper_ids)}] {pid} ...")
        cmd = [
            sys.executable, str(SCRIPTS / "onboard_arxiv_paper.py"),
            pid,
            "--max-prove-time", str(args.max_prove_time),
        ]
        if args.skip_prove:
            cmd.append("--skip-prove")
        if args.skip_cot_review:
            cmd.append("--skip-cot-review")
        if args.publish:
            cmd.append("--publish")
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=args.max_prove_time + 600)
            ok = proc.returncode == 0
            stderr_tail = proc.stderr[-1500:] if proc.stderr else ""
        except subprocess.TimeoutExpired:
            ok = False
            stderr_tail = "ONBOARDER_TIMEOUT"
        wall = round(time.time() - t0, 1)
        closure = _final_closure(pid)
        result = {
            "paper_id": pid,
            "ok": ok,
            "wall_s": wall,
            "closure": closure,
            "stderr_tail": stderr_tail if not ok else "",
        }
        results.append(result)
        marker = "✓" if ok else "✗"
        cs = closure["closure_pct"] if closure else "?"
        print(f"  {marker}  ({wall:.0f}s)  closure: {cs}%")
        if not ok and not args.continue_on_fail:
            print(f"\nAborting due to failure on {pid}; pass --continue-on-fail to keep going")
            break

    total_wall = round(time.time() - started_at, 1)
    summary = {
        "schema_version": "curated_batch_summary.v1",
        "list_path": str(args.list),
        "ran": len(results),
        "succeeded": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "total_wall_s": total_wall,
        "results": results,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    out_path = LOGS / f"curated_batch_{summary['generated_at'].replace(':', '-')}.json"
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nSummary: {out_path}")
    print(f"  succeeded: {summary['succeeded']}/{summary['ran']}  wall: {total_wall:.0f}s")

    # Aggregate corpus-level closure if we proved anything.
    proved_papers = [r for r in results if r.get("closure")]
    if proved_papers:
        total_rows = sum(r["closure"]["rows"] for r in proved_papers)
        total_closed = sum(r["closure"]["closed"] for r in proved_papers)
        avg = round(total_closed / max(1, total_rows) * 100, 1)
        print(f"  aggregate closure across {len(proved_papers)} new papers: {total_closed}/{total_rows} ({avg}%)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
