#!/usr/bin/env python3
"""Curated closure slices runner.

Instead of "close the whole paper", run slices:
  - close 5 verified theorems
  - then 10
  - ...

This script runs formalize_paper_full.py with max passes but stops early once
verified_proven reaches the target.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


def _safe_id(paper_id: str) -> str:
    return paper_id.replace("/", "_").replace(":", "_")


def _ledger_path(project_root: Path, paper_id: str) -> Path:
    return project_root / "output" / "verification_ledgers" / f"{_safe_id(paper_id)}.json"


def _report_path(project_root: Path, paper_id: str) -> Path:
    return project_root / "output" / "reports" / "full_paper" / f"{_safe_id(paper_id)}_suite_report.json"


def _load_verified_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    rows = raw.get("entries", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
    n = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        if str(r.get("status", "")).strip() == "FULLY_PROVEN" and str(r.get("proof_method", "")).strip().lower() == "lean_verified":
            n += 1
    return n


def _load_current_verified(project_root: Path, paper_id: str) -> int:
    report = _report_path(project_root, paper_id)
    if report.exists():
        try:
            raw = json.loads(report.read_text(encoding="utf-8"))
            metrics = raw.get("final_metrics", {}) if isinstance(raw, dict) else {}
            value = metrics.get("verified_proven", metrics.get("real_fully_proven", 0))
            return int(value or 0)
        except Exception:
            pass
    return _load_verified_count(_ledger_path(project_root, paper_id))


def main() -> int:
    p = argparse.ArgumentParser(description="Run closure slices for one paper")
    p.add_argument("paper_id")
    p.add_argument("--project-root", default=".")
    p.add_argument("--domain", default="")
    p.add_argument("--slice", type=int, default=5, help="Target additional verified proofs")
    p.add_argument("--max-steps", type=int, default=3, help="Max slice attempts")
    args = p.parse_args()

    project_root = Path(args.project_root).resolve()
    before = _load_current_verified(project_root, args.paper_id)
    target = before + max(1, int(args.slice))

    for step in range(1, max(1, int(args.max_steps)) + 1):
        t0 = time.time()
        proc = subprocess.run(
            [
                "python3",
                "scripts/formalize_paper_full.py",
                args.paper_id,
                "--project-root",
                str(project_root),
                "--no-reset-paper-ledger",
                "--max-passes",
                "2",
                "--prove-repair-rounds",
                "2",
                "--mandatory-retry-rounds",
                "0",
                "--bridge-rounds",
                "1",
                "--bridge-depth",
                "2",
                "--bridge-max-candidates",
                "3",
                "--skip-coverage-screen",
            ]
            + (["--library-first-domain", str(args.domain)] if args.domain else []),
            cwd=str(project_root),
            capture_output=True,
            text=True,
        )
        after = _load_current_verified(project_root, args.paper_id)
        print(
            json.dumps(
                {
                    "slice_step": step,
                    "verified_before": before,
                    "verified_after": after,
                    "target": target,
                    "returncode": proc.returncode,
                    "elapsed_s": round(time.time() - t0, 2),
                },
                indent=2,
            )
        )
        if after >= target:
            return 0
        before = after
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

