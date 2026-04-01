#!/usr/bin/env python3
"""Run benchmark/audit bundle and export a dated report folder.

Pipeline:
1) Optional pipeline test run (`test_pipeline.py`)
2) KG build (`kg_writer.py`)
3) Quality gates + attribution sheet (`quality_gates_report.py`)
4) Bundle artifacts into output/reports/<timestamp>/

Examples:
  python scripts/run_benchmark_audit_bundle.py --paper 2304.09598
  python scripts/run_benchmark_audit_bundle.py --skip-pipeline-test --paper 2304.09598
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class StepResult:
    name: str
    command: list[str]
    return_code: int
    elapsed_s: float
    ok: bool
    output_tail: str


def _run_cmd(name: str, cmd: list[str], cwd: Path) -> StepResult:
    started = time.time()
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    elapsed = time.time() - started
    combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    tail_lines = "\n".join(combined.splitlines()[-40:])
    return StepResult(
        name=name,
        command=cmd,
        return_code=proc.returncode,
        elapsed_s=elapsed,
        ok=(proc.returncode == 0),
        output_tail=tail_lines,
    )


def _copy_if_exists(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_file():
        shutil.copy2(src, dst)
    else:
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run benchmark/audit bundle and export dated report")
    p.add_argument("--project-root", default=".", help="DESol project root")
    p.add_argument("--paper", default="", help="Optional paper id, e.g. 2304.09598")

    p.add_argument("--skip-pipeline-test", action="store_true", help="Skip test_pipeline.py step")
    p.add_argument("--pipeline-domains", nargs="+", default=[], help="Domains for test_pipeline.py")
    p.add_argument("--pipeline-max-theorems", type=int, default=2, help="Max theorems for test run")
    p.add_argument("--pipeline-parallel-papers", type=int, default=1)
    p.add_argument("--pipeline-parallel-theorems", type=int, default=2)

    p.add_argument("--kg-root", default="output/kg", help="KG output root")
    p.add_argument("--audit-out", default="output/audit", help="Audit output dir")
    p.add_argument("--reports-root", default="output/reports", help="Reports output root")
    p.add_argument("--audit-sample-size", type=int, default=25)

    return p


def main() -> int:
    args = _build_parser().parse_args()
    root = Path(args.project_root).resolve()

    timestamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    report_dir = root / args.reports_root / timestamp
    report_dir.mkdir(parents=True, exist_ok=True)

    steps: list[StepResult] = []

    if not args.skip_pipeline_test:
        cmd = [
            sys.executable,
            "scripts/test_pipeline.py",
            "--max-theorems",
            str(args.pipeline_max_theorems),
            "--parallel-papers",
            str(args.pipeline_parallel_papers),
            "--parallel-theorems",
            str(args.pipeline_parallel_theorems),
        ]
        if args.paper:
            cmd.extend(["--paper", args.paper])
        elif args.pipeline_domains:
            cmd.extend(["--domains", *args.pipeline_domains])
        step = _run_cmd("pipeline_test", cmd, cwd=root)
        steps.append(step)

    kg_cmd = [
        sys.executable,
        "scripts/kg_writer.py",
        "--ledger-dir",
        "output/verification_ledgers",
        "--kg-root",
        args.kg_root,
    ]
    if args.paper:
        kg_cmd.extend(["--paper", args.paper])
    steps.append(_run_cmd("kg_writer", kg_cmd, cwd=root))

    audit_cmd = [
        sys.executable,
        "scripts/quality_gates_report.py",
        "--ledger-dir",
        "output/verification_ledgers",
        "--out-dir",
        args.audit_out,
        "--audit-sample-size",
        str(args.audit_sample_size),
    ]
    if args.paper:
        audit_cmd.extend(["--paper", args.paper])
    steps.append(_run_cmd("quality_gates", audit_cmd, cwd=root))

    # Bundle artifacts.
    _copy_if_exists(root / args.kg_root, report_dir / "kg")
    _copy_if_exists(root / args.audit_out, report_dir / "audit")
    _copy_if_exists(root / "output" / "verification_ledgers", report_dir / "verification_ledgers")

    manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project_root": str(root),
        "paper": args.paper,
        "report_dir": str(report_dir),
        "steps": [asdict(s) for s in steps],
        "all_steps_ok": all(s.ok for s in steps),
    }
    (report_dir / "bundle_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"[ok] bundle created at {report_dir}")
    for s in steps:
        status = "ok" if s.ok else "fail"
        print(f"[info] {s.name}: {status} rc={s.return_code} elapsed={s.elapsed_s:.1f}s")

    return 0 if all(s.ok for s in steps) else 1


if __name__ == "__main__":
    raise SystemExit(main())
