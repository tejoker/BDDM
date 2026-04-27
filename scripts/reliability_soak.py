#!/usr/bin/env python3
"""Reliability soak harness for queue lease/ack/retry invariants."""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

from pipeline_orchestrator import PipelineOrchestrator


def run_soak(
    *,
    orch_root: Path,
    jobs: int,
    workers: int,
    fail_rate: float,
    rounds: int,
) -> dict:
    rnd = random.Random(0)
    orch = PipelineOrchestrator(orch_root)
    for i in range(jobs):
        _ = orch.enqueue(f"soak.{i:05d}", {"mode": "soak", "max_attempts": 4})

    acked = retried = terminal = 0
    for r in range(rounds):
        for w in range(workers):
            item = orch.lease_next(worker_id=f"soak-worker-{w}", lease_seconds=30)
            if item is None:
                continue
            jid = int(item["job_id"])
            if rnd.random() < fail_rate:
                res = orch.fail(jid, error=f"sim_fail_round_{r}", base_backoff_s=1)
                if res["status"] == "retry_scheduled":
                    retried += 1
                else:
                    terminal += 1
            else:
                orch.ack(jid)
                acked += 1
        time.sleep(0.01)

    q = orch.queue_dashboard()
    stats = q.get("stats", {}) if isinstance(q, dict) else {}
    leased = int(stats.get("leased", 0))
    passed = leased == 0
    return {
        "jobs_seeded": jobs,
        "workers": workers,
        "rounds": rounds,
        "acked": acked,
        "retried": retried,
        "terminal_failed": terminal,
        "queue": q,
        "invariant_no_stuck_leases": passed,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Queue reliability soak test")
    p.add_argument("--orch-root", default="output/orchestrator_soak")
    p.add_argument("--jobs", type=int, default=200)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--fail-rate", type=float, default=0.2)
    p.add_argument("--rounds", type=int, default=300)
    p.add_argument("--out", default="")
    args = p.parse_args()
    payload = run_soak(
        orch_root=Path(args.orch_root),
        jobs=max(1, int(args.jobs)),
        workers=max(1, int(args.workers)),
        fail_rate=max(0.0, min(0.95, float(args.fail_rate))),
        rounds=max(1, int(args.rounds)),
    )
    txt = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(txt, encoding="utf-8")
    print(txt)
    return 0 if payload["invariant_no_stuck_leases"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

