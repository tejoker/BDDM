#!/usr/bin/env python3
"""Run translate-only arxiv_to_lean across the golden10 suite."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _safe_id(text: str) -> str:
    return str(text).replace("/", "_").replace(":", "_")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _copy_if_exists(src: Path, dst: Path) -> str:
    if not src.exists():
        return ""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return str(dst)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run translate-only pipeline over golden10 suite")
    parser.add_argument("--suite-json", default="reproducibility/paper_agnostic_golden10.json")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--out-root", default="output/paper_translations/golden10")
    parser.add_argument("--work-root", default="output/paper_translation_work/golden10")
    parser.add_argument("--evidence-root", default="reproducibility/paper_agnostic_golden10_translation")
    parser.add_argument("--max-papers", type=int, default=0)
    parser.add_argument("--max-theorems", type=int, default=0)
    parser.add_argument("--api-rate", type=float, default=0.2)
    parser.add_argument("--translation-candidates", type=int, default=1)
    parser.add_argument("--parallel-theorems", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    root = Path(args.project_root).resolve()
    suite_path = Path(args.suite_json)
    suite = _read_json(suite_path)
    papers = suite.get("papers", []) if isinstance(suite, dict) else []
    if not isinstance(papers, list) or not papers:
        print(json.dumps({"ok": False, "reason": "empty_suite", "suite_json": str(suite_path)}, indent=2))
        return 1
    if int(args.max_papers) > 0:
        papers = papers[: int(args.max_papers)]

    evidence_root = Path(args.evidence_root)
    summary_path = evidence_root / "summary.json"
    previous = _read_json(summary_path) if args.resume else {}
    done = set(previous.get("done", [])) if isinstance(previous, dict) else set()
    rows = previous.get("rows", []) if isinstance(previous, dict) and isinstance(previous.get("rows"), list) else []

    for idx, paper in enumerate(papers, start=1):
        if not isinstance(paper, dict):
            continue
        paper_id = str(paper.get("paper_id", "")).strip()
        if not paper_id or paper_id in done:
            continue
        safe = _safe_id(paper_id)
        out_lean = root / args.out_root / f"{safe}.lean"
        work_dir = root / args.work_root / safe
        paper_evidence = evidence_root / safe
        cmd = [
            sys.executable,
            "scripts/arxiv_to_lean.py",
            paper_id,
            "--project-root",
            str(root),
            "--out",
            str(out_lean),
            "--work-dir",
            str(work_dir),
            "--translate-only",
            "--translation-candidates",
            str(max(1, int(args.translation_candidates))),
            "--api-rate",
            str(max(0.0, float(args.api_rate))),
            "--parallel-theorems",
            str(max(1, int(args.parallel_theorems))),
        ]
        if int(args.max_theorems) > 0:
            cmd.extend(["--max-theorems", str(int(args.max_theorems))])

        started = time.time()
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True)
        elapsed = round(time.time() - started, 3)

        ledger_src = root / "output" / "verification_ledgers" / f"{safe}.json"
        checkpoint_src = work_dir / "pipeline_checkpoint.json"
        row = {
            "paper_id": paper_id,
            "domain": paper.get("domain", "unknown"),
            "role": paper.get("role", ""),
            "index": idx,
            "returncode": int(proc.returncode),
            "elapsed_s": elapsed,
            "out_lean": str(out_lean),
            "ledger": str(ledger_src) if ledger_src.exists() else "",
            "checkpoint": str(checkpoint_src) if checkpoint_src.exists() else "",
            "stdout_tail": (proc.stdout or "")[-5000:],
            "stderr_tail": (proc.stderr or "")[-5000:],
        }
        row["evidence_lean"] = _copy_if_exists(out_lean, paper_evidence / f"{safe}.lean")
        row["evidence_ledger"] = _copy_if_exists(ledger_src, paper_evidence / "ledger.json")
        row["evidence_checkpoint"] = _copy_if_exists(checkpoint_src, paper_evidence / "pipeline_checkpoint.json")
        _write_json(paper_evidence / "translation_run.json", row)

        if proc.returncode == 0:
            done.add(paper_id)
        rows.append(row)
        summary = {
            "schema_version": "1.0.0",
            "suite_json": str(suite_path),
            "project_root": str(root),
            "evidence_root": str(evidence_root),
            "updated_at_unix": int(time.time()),
            "done": sorted(done),
            "papers_total": len([p for p in papers if isinstance(p, dict)]),
            "papers_attempted": len(rows),
            "papers_succeeded": sum(1 for r in rows if int(r.get("returncode", 1)) == 0),
            "rows": rows,
        }
        _write_json(summary_path, summary)
        print(f"[golden10-translate] {idx}/{len(papers)} paper={paper_id} rc={proc.returncode} elapsed={elapsed:.1f}s", flush=True)

    final_summary = _read_json(summary_path)
    print(
        json.dumps(
            {
                "ok": True,
                "summary": str(summary_path),
                "papers_attempted": final_summary.get("papers_attempted", 0),
                "papers_succeeded": final_summary.get("papers_succeeded", 0),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
