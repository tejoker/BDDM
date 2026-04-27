#!/usr/bin/env python3
"""Blocker-first iteration loop (no world-model benchmark dependency).

Runs a bounded formalization pass and immediately recomputes strict closure
checklist + review queue so we can iterate on top blockers quickly.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any


def _run(cmd: list[str], cwd: Path, timeout_s: int) -> dict[str, Any]:
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=max(30, int(timeout_s)),
        )
        return {
            "cmd": cmd,
            "returncode": int(proc.returncode),
            "elapsed_s": round(time.time() - t0, 3),
            "stdout_tail": (proc.stdout or "")[-2000:],
            "stderr_tail": (proc.stderr or "")[-2000:],
        }
    except subprocess.TimeoutExpired:
        return {
            "cmd": cmd,
            "returncode": 124,
            "elapsed_s": round(time.time() - t0, 3),
            "stdout_tail": "",
            "stderr_tail": f"timeout_after_{int(timeout_s)}s",
        }


def _safe_id(paper_id: str) -> str:
    return paper_id.replace("/", "_").replace(":", "_")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _top_blockers(payload: dict[str, Any], n: int = 5) -> list[dict[str, Any]]:
    items = payload.get("top_blockers", [])
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for row in items[: max(1, n)]:
        if not isinstance(row, dict):
            continue
        out.append({"check": str(row.get("check", "")), "count": int(row.get("count", 0) or 0)})
    return out


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run blocker-first no-world-model paper iteration loop")
    p.add_argument("--paper-id", required=True)
    p.add_argument("--project-root", default=".")
    p.add_argument("--passes", type=int, default=2)
    p.add_argument("--max-theorems", type=int, default=0)
    p.add_argument("--formalize-timeout-s", type=int, default=7200)
    p.add_argument("--report-root", default="output/reports/full_paper")
    p.add_argument("--ledger-root", default="output/verification_ledgers")
    p.add_argument("--out", default="")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    project_root = Path(args.project_root).resolve()
    report_root = Path(args.report_root)
    ledger_root = Path(args.ledger_root)
    safe = _safe_id(args.paper_id)
    loop_rows: list[dict[str, Any]] = []

    for i in range(1, max(1, int(args.passes)) + 1):
        cmd = [
            "python3",
            "scripts/formalize_paper_full.py",
            args.paper_id,
            "--project-root",
            str(project_root),
            "--max-passes",
            "1",
            "--focus-no-world-model",
            "--write-kg",
        ]
        if int(args.max_theorems) > 0:
            cmd.extend(["--max-theorems", str(int(args.max_theorems))])
        formalize = _run(cmd, cwd=project_root, timeout_s=int(args.formalize_timeout_s))

        checklist_cmd = [
            "python3",
            "scripts/paper_closure_checklist.py",
            "--paper-id",
            args.paper_id,
            "--ledger-root",
            str(ledger_root),
            "--out-json",
            str(report_root / f"{safe}_closure_checklist.json"),
            "--out-md",
            str(report_root / f"{safe}_closure_checklist.md"),
            "--out-review-queue",
            str(Path("output/reports/review_queue") / f"{safe}_review_queue.json"),
        ]
        checklist = _run(checklist_cmd, cwd=project_root, timeout_s=600)
        payload = _load_json(report_root / f"{safe}_closure_checklist.json")
        loop_rows.append(
            {
                "pass_idx": i,
                "formalize_returncode": int(formalize.get("returncode", 1)),
                "formalize_elapsed_s": float(formalize.get("elapsed_s", 0.0)),
                "checklist_returncode": int(checklist.get("returncode", 1)),
                "closure_rate": float(payload.get("closure_rate", 0.0) or 0.0),
                "fully_proven": int(payload.get("fully_proven", 0) or 0),
                "total_theorems": int(payload.get("total_theorems", 0) or 0),
                "top_blockers": _top_blockers(payload, n=5),
            }
        )

    result = {
        "paper_id": args.paper_id,
        "generated_at_unix": int(time.time()),
        "passes": loop_rows,
    }
    out = Path(args.out) if args.out else (report_root / f"{safe}_focus_blocker_loop.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"out": str(out), "paper_id": args.paper_id, "passes": len(loop_rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

