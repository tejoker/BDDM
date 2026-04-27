#!/usr/bin/env python3
"""Run fixed-config formalization across a paper suite with checkpoint/resume."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _run(cmd: list[str], cwd: Path, timeout_s: int = 0) -> dict[str, Any]:
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=(None if int(timeout_s) <= 0 else int(timeout_s)),
        )
        return {
            "cmd": cmd,
            "returncode": int(proc.returncode),
            "elapsed_s": round(time.time() - t0, 3),
            "stdout_tail": (proc.stdout or "")[-3000:],
            "stderr_tail": (proc.stderr or "")[-3000:],
        }
    except subprocess.TimeoutExpired:
        return {
            "cmd": cmd,
            "returncode": 124,
            "elapsed_s": round(time.time() - t0, 3),
            "stdout_tail": "",
            "stderr_tail": f"timeout_after_{int(timeout_s)}s",
        }


def _safe_id(text: str) -> str:
    return str(text).replace("/", "_").replace(":", "_")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run fixed config across paper-agnostic suite")
    p.add_argument("--suite-json", required=True)
    p.add_argument("--project-root", default=".")
    p.add_argument("--out-progress", default="output/reports/full_paper/paper_agnostic_suite_progress.json")
    p.add_argument("--max-papers", type=int, default=0, help="0 = all")
    p.add_argument("--paper-timeout-s", type=int, default=0, help="0 = no timeout")
    p.add_argument("--model", default="")
    p.add_argument("--max-theorems", type=int, default=0)
    p.add_argument("--max-passes", type=int, default=2)
    p.add_argument("--prove-repair-rounds", type=int, default=4)
    p.add_argument("--mandatory-retry-rounds", type=int, default=1)
    p.add_argument("--bridge-rounds", type=int, default=1)
    p.add_argument("--bridge-depth", type=int, default=2)
    p.add_argument("--bridge-max-candidates", type=int, default=3)
    p.add_argument("--focus-no-world-model", action="store_true")
    p.add_argument("--resume", action="store_true")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    root = Path(args.project_root).resolve()
    suite = _load_json(Path(args.suite_json))
    papers = suite.get("papers", []) if isinstance(suite, dict) else []
    if not isinstance(papers, list) or not papers:
        print(json.dumps({"ok": False, "reason": "empty_suite", "suite_json": args.suite_json}, indent=2))
        return 1

    out_progress = Path(args.out_progress)
    progress = _load_json(out_progress) if bool(args.resume) else {}
    done: set[str] = set(progress.get("done", [])) if isinstance(progress, dict) else set()
    rows: list[dict[str, Any]] = progress.get("rows", []) if isinstance(progress, dict) and isinstance(progress.get("rows"), list) else []

    run_list = []
    for item in papers:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("paper_id", "")).strip()
        if not pid:
            continue
        if pid in done:
            continue
        run_list.append(item)
    if int(args.max_papers) > 0:
        run_list = run_list[: int(args.max_papers)]

    for idx, item in enumerate(run_list, 1):
        pid = str(item.get("paper_id", "")).strip()
        domain = str(item.get("domain", "unknown"))
        safe = _safe_id(pid)
        report_out = root / "output" / "reports" / "full_paper" / f"{safe}_suite_report.json"
        results_file = root / "logs" / f"full_paper_{safe}_suite_results.json"
        cmd = [
            sys.executable,
            "scripts/formalize_paper_full.py",
            pid,
            "--project-root",
            str(root),
            "--out-lean",
            str(root / "output" / f"{safe}.lean"),
            "--max-passes",
            str(max(1, int(args.max_passes))),
            "--prove-repair-rounds",
            str(max(1, int(args.prove_repair_rounds))),
            "--mandatory-retry-rounds",
            str(max(0, int(args.mandatory_retry_rounds))),
            "--bridge-rounds",
            str(max(1, int(args.bridge_rounds))),
            "--bridge-depth",
            str(max(1, int(args.bridge_depth))),
            "--bridge-max-candidates",
            str(max(1, int(args.bridge_max_candidates))),
            "--results-file",
            str(results_file),
            "--report-out",
            str(report_out),
        ]
        if args.model.strip():
            cmd.extend(["--model", args.model.strip()])
        if int(args.max_theorems) > 0:
            cmd.extend(["--max-theorems", str(int(args.max_theorems))])
        if domain and domain.lower() != "unknown":
            cmd.extend(["--library-first-domain", domain])
        if bool(args.focus_no_world_model):
            cmd.append("--focus-no-world-model")

        step = _run(cmd, cwd=root, timeout_s=int(args.paper_timeout_s))
        rows.append(
            {
                "paper_id": pid,
                "domain": domain,
                "index": idx,
                "step": step,
                "report_out": str(report_out),
            }
        )
        done.add(pid)
        _save_json(
            out_progress,
            {
                "suite_json": str(args.suite_json),
                "updated_at_unix": int(time.time()),
                "done": sorted(done),
                "rows": rows,
            },
        )
        print(f"[suite] {idx}/{len(run_list)} paper={pid} rc={step['returncode']} elapsed={step['elapsed_s']:.1f}s")

    print(
        json.dumps(
            {
                "ok": True,
                "out_progress": str(out_progress),
                "papers_attempted": len(run_list),
                "papers_done_total": len(done),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

