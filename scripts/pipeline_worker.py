#!/usr/bin/env python3
"""Distributed queue worker for arXiv pipeline jobs.

Consumes jobs from pipeline_orchestrator SQLite queue with lease/backoff.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from pipeline_orchestrator import PipelineOrchestrator


def _run_one_job(
    *,
    orch: PipelineOrchestrator,
    job: dict,
    project_root: Path,
    worker_id: str,
    paper_timeout_s: int,
) -> dict:
    paper_id = str(job.get("paper_id", "")).strip()
    job_id = int(job.get("job_id", 0))
    cfg = job.get("config", {}) if isinstance(job.get("config"), dict) else {}
    stage = orch.begin_stage(paper_id=paper_id, stage="translate", config=cfg)
    t0 = time.time()
    try:
        cmd = [
            sys.executable,
            "scripts/arxiv_to_lean.py",
            paper_id,
            "--project-root",
            str(project_root),
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=max(60, int(paper_timeout_s)),
        )
        elapsed = round(time.time() - t0, 2)
        if proc.returncode == 0:
            orch.finish_stage(run=stage, status="OK", metrics={"worker_id": worker_id, "elapsed_s": elapsed})
            orch.ack(job_id)
            return {"status": "ok", "paper_id": paper_id, "job_id": job_id, "elapsed_s": elapsed}
        err = (proc.stderr or proc.stdout or "pipeline_failed")[:1000]
        orch.finish_stage(run=stage, status="FAILED", metrics={"worker_id": worker_id, "elapsed_s": elapsed, "error": err})
        fr = orch.fail(job_id, error=err)
        return {"status": fr.get("status", "failed"), "paper_id": paper_id, "job_id": job_id, "elapsed_s": elapsed}
    except subprocess.TimeoutExpired:
        elapsed = round(time.time() - t0, 2)
        err = f"timeout_{paper_timeout_s}s"
        orch.finish_stage(run=stage, status="FAILED", metrics={"worker_id": worker_id, "elapsed_s": elapsed, "error": err})
        fr = orch.fail(job_id, error=err)
        return {"status": fr.get("status", "failed"), "paper_id": paper_id, "job_id": job_id, "elapsed_s": elapsed}
    except Exception as exc:
        elapsed = round(time.time() - t0, 2)
        err = str(exc)[:1000]
        orch.finish_stage(run=stage, status="FAILED", metrics={"worker_id": worker_id, "elapsed_s": elapsed, "error": err})
        fr = orch.fail(job_id, error=err)
        return {"status": fr.get("status", "failed"), "paper_id": paper_id, "job_id": job_id, "elapsed_s": elapsed}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Consume and execute pipeline queue jobs")
    p.add_argument("--orch-root", default="output/orchestrator")
    p.add_argument("--project-root", default=".")
    p.add_argument("--worker-id", default="worker-1")
    p.add_argument("--lease-seconds", type=int, default=1800)
    p.add_argument("--paper-timeout-s", type=int, default=2400)
    p.add_argument("--max-jobs", type=int, default=0, help="0 means run forever")
    p.add_argument("--poll-s", type=int, default=5)
    return p


def main() -> int:
    args = _build_parser().parse_args()
    orch = PipelineOrchestrator(Path(args.orch_root))
    project_root = Path(args.project_root).resolve()
    done = 0
    while True:
        if args.max_jobs > 0 and done >= args.max_jobs:
            break
        job = orch.lease_next(worker_id=args.worker_id, lease_seconds=max(60, int(args.lease_seconds)))
        if job is None:
            time.sleep(max(1, int(args.poll_s)))
            continue
        res = _run_one_job(
            orch=orch,
            job=job,
            project_root=project_root,
            worker_id=str(args.worker_id),
            paper_timeout_s=max(60, int(args.paper_timeout_s)),
        )
        done += 1
        print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

