#!/usr/bin/env python3
"""Run a MerLean-style comparison suite against this pipeline.

For each benchmarkable arXiv paper in the suite:
1) run formalize_paper_full.py
2) run paper_closure_checklist.py
3) run semantic_fidelity_audit.py
4) aggregate operational vs faithful metrics
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _run(cmd: list[str], cwd: Path) -> dict[str, Any]:
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    return {
        "cmd": cmd,
        "returncode": int(proc.returncode),
        "elapsed_s": round(time.time() - t0, 3),
        "stdout_tail": (proc.stdout or "")[-3000:],
        "stderr_tail": (proc.stderr or "")[-3000:],
    }


def _safe_id(text: str) -> str:
    return str(text).replace("/", "_").replace(":", "_")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run MerLean-style public comparison suite")
    p.add_argument("--suite-json", default="reproducibility/merlean_suite_2026_public.json")
    p.add_argument("--project-root", default=".")
    p.add_argument("--out-json", default="")
    p.add_argument("--model", default="")
    p.add_argument("--max-theorems", type=int, default=0)
    p.add_argument("--max-passes", type=int, default=4)
    p.add_argument("--prove-repair-rounds", type=int, default=5)
    p.add_argument("--mandatory-retry-rounds", type=int, default=1)
    p.add_argument("--bridge-rounds", type=int, default=2)
    p.add_argument("--bridge-depth", type=int, default=2)
    p.add_argument("--bridge-max-candidates", type=int, default=4)
    p.add_argument(
        "--focus-no-world-model",
        action="store_true",
        help="Use blocker-first stable lane for prove stage",
    )
    p.add_argument(
        "--skip-formalize",
        action="store_true",
        help="Skip formalize stage and only recompute audits from existing ledger/artifacts",
    )
    return p


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    args = _build_parser().parse_args()
    root = Path(args.project_root).resolve()
    suite_path = (root / args.suite_json).resolve() if not Path(args.suite_json).is_absolute() else Path(args.suite_json)
    suite = _load_json(suite_path)
    papers = suite.get("papers", []) if isinstance(suite, dict) else []
    if not isinstance(papers, list) or not papers:
        print(json.dumps({"ok": False, "reason": "empty_suite", "suite_json": str(suite_path)}, indent=2))
        return 1

    run_rows: list[dict[str, Any]] = []
    for row in papers:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label", "")).strip() or "paper"
        arxiv_id = str(row.get("arxiv_id", "")).strip()
        status = str(row.get("status", "")).strip()
        out_row: dict[str, Any] = {
            "label": label,
            "status": status,
            "arxiv_id": arxiv_id,
            "benchmarkable": bool(arxiv_id and status == "benchmarkable"),
            "steps": [],
        }
        if not out_row["benchmarkable"]:
            out_row["skip_reason"] = "non-benchmarkable entry (missing public arXiv id)"
            run_rows.append(out_row)
            continue

        safe = _safe_id(arxiv_id)
        report_out = root / "output" / "reports" / "full_paper" / f"{safe}_merlean_compare_report.json"
        checklist_out = root / "output" / "reports" / "full_paper" / f"{safe}_merlean_compare_checklist.json"
        checklist_md = root / "output" / "reports" / "full_paper" / f"{safe}_merlean_compare_checklist.md"
        fidelity_out = root / "output" / "reports" / "full_paper" / f"{safe}_merlean_compare_fidelity.json"
        results_file = root / "logs" / f"full_paper_{safe}_merlean_compare_prove_results.json"

        if not bool(args.skip_formalize):
            cmd_formalize = [
                sys.executable,
                "scripts/formalize_paper_full.py",
                arxiv_id,
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
                cmd_formalize.extend(["--model", args.model.strip()])
            if int(args.max_theorems) > 0:
                cmd_formalize.extend(["--max-theorems", str(int(args.max_theorems))])
            if bool(args.focus_no_world_model):
                cmd_formalize.append("--focus-no-world-model")
            out_row["steps"].append({"name": "formalize_paper_full", **_run(cmd_formalize, cwd=root)})

        cmd_checklist = [
            sys.executable,
            "scripts/paper_closure_checklist.py",
            "--paper-id",
            arxiv_id,
            "--out-json",
            str(checklist_out),
            "--out-md",
            str(checklist_md),
        ]
        out_row["steps"].append({"name": "paper_closure_checklist", **_run(cmd_checklist, cwd=root)})

        cmd_fidelity = [
            sys.executable,
            "scripts/semantic_fidelity_audit.py",
            "--paper-id",
            arxiv_id,
            "--out-json",
            str(fidelity_out),
        ]
        out_row["steps"].append({"name": "semantic_fidelity_audit", **_run(cmd_fidelity, cwd=root)})

        checklist_payload = _load_json(checklist_out)
        fidelity_payload = _load_json(fidelity_out)
        out_row["metrics"] = {
            "total_theorems": int(fidelity_payload.get("total_theorems", checklist_payload.get("total_theorems", 0)) or 0),
            "operational_fully_proven": int(fidelity_payload.get("operational_fully_proven", checklist_payload.get("operational_fully_proven", checklist_payload.get("fully_proven", 0))) or 0),
            "operational_closure_rate": float(fidelity_payload.get("operational_closure_rate", checklist_payload.get("operational_closure_rate", checklist_payload.get("closure_rate", 0.0))) or 0.0),
            "faithful_fully_proven": int(fidelity_payload.get("faithful_fully_proven", checklist_payload.get("faithful_fully_accepted", 0)) or 0),
            "faithful_closure_rate": float(fidelity_payload.get("faithful_closure_rate", checklist_payload.get("faithful_closure_rate", 0.0)) or 0.0),
        }
        run_rows.append(out_row)

    bench_rows = [r for r in run_rows if r.get("benchmarkable")]
    summary = {
        "suite_json": str(suite_path),
        "generated_at_unix": int(time.time()),
        "papers_total": len(run_rows),
        "papers_benchmarkable": len(bench_rows),
        "papers_skipped": len(run_rows) - len(bench_rows),
        "avg_operational_closure_rate": round(
            sum(float((r.get("metrics", {}) or {}).get("operational_closure_rate", 0.0)) for r in bench_rows)
            / max(1, len(bench_rows)),
            4,
        ),
        "avg_faithful_closure_rate": round(
            sum(float((r.get("metrics", {}) or {}).get("faithful_closure_rate", 0.0)) for r in bench_rows)
            / max(1, len(bench_rows)),
            4,
        ),
        "rows": run_rows,
    }

    out_path = (
        Path(args.out_json)
        if args.out_json
        else root / "output" / "reports" / "full_paper" / "merlean_public_comparison_report.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "out_json": str(out_path),
                "papers_total": summary["papers_total"],
                "papers_benchmarkable": summary["papers_benchmarkable"],
                "avg_operational_closure_rate": summary["avg_operational_closure_rate"],
                "avg_faithful_closure_rate": summary["avg_faithful_closure_rate"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

