#!/usr/bin/env python3
"""File-backed orchestration for arXiv pipeline runs.

Provides:
- idempotent run IDs per (paper, stage, config)
- queue + checkpoint lifecycle
- simple drift alert snapshots across runs
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_STAGES = (
    "fetch",
    "translate",
    "canonicalize",
    "lean_validate",
    "kg_write",
    "linkage_search",
)


@dataclass(frozen=True)
class StageRun:
    run_id: str
    paper_id: str
    stage: str
    status: str
    started_at_unix: int
    finished_at_unix: int
    metrics: dict[str, Any]


def _stable_run_id(*, paper_id: str, stage: str, config: dict[str, Any]) -> str:
    payload = {
        "paper_id": paper_id,
        "stage": stage,
        "config": config,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return "run_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


class PipelineOrchestrator:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.queue_path = root / "queue.json"
        self.queue_db_path = root / "queue.db"
        self.runs_path = root / "runs.jsonl"
        self.checkpoints_dir = root / "checkpoints"
        self.alerts_path = root / "drift_alerts.json"
        self._init_queue_db()

    def _init_queue_db(self) -> None:
        self.queue_db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.queue_db_path), timeout=30.0)
        with con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS queue_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    paper_id TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 5,
                    next_attempt_at_unix INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT NOT NULL DEFAULT '',
                    lease_until_unix INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at_unix INTEGER NOT NULL,
                    updated_at_unix INTEGER NOT NULL
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_q_status ON queue_jobs(status, next_attempt_at_unix)")
        con.close()

    def _queue_stats(self) -> dict[str, int]:
        con = sqlite3.connect(str(self.queue_db_path), timeout=10.0)
        rows = con.execute(
            "SELECT status, COUNT(*) FROM queue_jobs GROUP BY status"
        ).fetchall()
        con.close()
        out = {"queued": 0, "leased": 0, "done": 0, "failed": 0}
        for status, cnt in rows:
            out[str(status)] = int(cnt)
        return out

    def enqueue(self, paper_id: str, config: dict[str, Any]) -> dict[str, Any]:
        now = int(time.time())
        payload = {
            "paper_id": paper_id,
            "config": config,
            "enqueued_at_unix": now,
        }
        con = sqlite3.connect(str(self.queue_db_path), timeout=30.0)
        inserted = False
        with con:
            cur = con.execute(
                """
                INSERT OR IGNORE INTO queue_jobs(
                    paper_id, payload_json, status, attempts, max_attempts, next_attempt_at_unix,
                    lease_owner, lease_until_unix, last_error, created_at_unix, updated_at_unix
                )
                VALUES (?, ?, 'queued', 0, ?, 0, '', 0, '', ?, ?)
                """,
                (paper_id, json.dumps(payload, ensure_ascii=False), int(config.get("max_attempts", 5)), now, now),
            )
            inserted = int(cur.rowcount or 0) > 0
        con.close()
        stats = self._queue_stats()
        if inserted:
            return {"status": "queued", "paper_id": paper_id, "depth": stats.get("queued", 0)}
        return {"status": "duplicate", "paper_id": paper_id, "depth": stats.get("queued", 0)}

    def lease_next(self, *, worker_id: str, lease_seconds: int = 600) -> dict[str, Any] | None:
        now = int(time.time())
        con = sqlite3.connect(str(self.queue_db_path), timeout=30.0)
        con.row_factory = sqlite3.Row
        row: sqlite3.Row | None = None
        with con:
            row = con.execute(
                """
                SELECT * FROM queue_jobs
                WHERE status IN ('queued', 'failed')
                  AND next_attempt_at_unix <= ?
                  AND (lease_until_unix <= ? OR lease_owner = '')
                  AND attempts < max_attempts
                ORDER BY created_at_unix ASC
                LIMIT 1
                """,
                (now, now),
            ).fetchone()
            if row is None:
                con.close()
                return None
            jid = int(row["id"])
            con.execute(
                """
                UPDATE queue_jobs
                SET status='leased',
                    lease_owner=?,
                    lease_until_unix=?,
                    attempts=attempts+1,
                    updated_at_unix=?
                WHERE id=?
                """,
                (worker_id, now + max(30, int(lease_seconds)), now, jid),
            )
            row = con.execute("SELECT * FROM queue_jobs WHERE id=?", (jid,)).fetchone()
        con.close()
        if row is None:
            return None
        try:
            payload = json.loads(str(row["payload_json"]))
        except Exception:
            payload = {"paper_id": str(row["paper_id"]), "config": {}}
        payload["job_id"] = int(row["id"])
        payload["attempts"] = int(row["attempts"])
        payload["max_attempts"] = int(row["max_attempts"])
        return payload

    def ack(self, job_id: int) -> None:
        now = int(time.time())
        con = sqlite3.connect(str(self.queue_db_path), timeout=30.0)
        with con:
            con.execute(
                """
                UPDATE queue_jobs
                SET status='done', lease_owner='', lease_until_unix=0, updated_at_unix=?
                WHERE id=?
                """,
                (now, int(job_id)),
            )
        con.close()

    def fail(
        self,
        job_id: int,
        *,
        error: str,
        base_backoff_s: int = 60,
    ) -> dict[str, Any]:
        now = int(time.time())
        con = sqlite3.connect(str(self.queue_db_path), timeout=30.0)
        con.row_factory = sqlite3.Row
        with con:
            row = con.execute("SELECT attempts, max_attempts FROM queue_jobs WHERE id=?", (int(job_id),)).fetchone()
            if row is None:
                con.close()
                return {"status": "missing", "job_id": int(job_id)}
            attempts = int(row["attempts"])
            max_attempts = int(row["max_attempts"])
            retryable = attempts < max_attempts
            if retryable:
                backoff = int(base_backoff_s * (2 ** max(0, attempts - 1)))
                next_at = now + min(backoff, 3600 * 6)
                con.execute(
                    """
                    UPDATE queue_jobs
                    SET status='failed', lease_owner='', lease_until_unix=0,
                        next_attempt_at_unix=?, last_error=?, updated_at_unix=?
                    WHERE id=?
                    """,
                    (next_at, error[:1000], now, int(job_id)),
                )
                status = "retry_scheduled"
            else:
                con.execute(
                    """
                    UPDATE queue_jobs
                    SET status='failed', lease_owner='', lease_until_unix=0,
                        next_attempt_at_unix=0, last_error=?, updated_at_unix=?
                    WHERE id=?
                    """,
                    (error[:1000], now, int(job_id)),
                )
                next_at = 0
                status = "failed_terminal"
        con.close()
        return {"status": status, "job_id": int(job_id), "next_attempt_at_unix": int(next_at)}

    def pop(self) -> dict[str, Any] | None:
        # Backward-compatible alias for lease_next in single-worker mode.
        return self.lease_next(worker_id="legacy-pop", lease_seconds=120)

    def queue_dashboard(self) -> dict[str, Any]:
        return {
            "generated_at_unix": int(time.time()),
            "queue_db": str(self.queue_db_path),
            "stats": self._queue_stats(),
        }

    # Legacy JSON queue kept for backward compatibility with older scripts.
    def enqueue_legacy_json(self, paper_id: str, config: dict[str, Any]) -> dict[str, Any]:
        queue = _read_json(self.queue_path, [])
        if not isinstance(queue, list):
            queue = []
        if any(isinstance(it, dict) and it.get("paper_id") == paper_id for it in queue):
            return {"status": "duplicate", "paper_id": paper_id}
        queue.append(
            {
                "paper_id": paper_id,
                "config": config,
                "enqueued_at_unix": int(time.time()),
            }
        )
        _write_json(self.queue_path, queue)
        return {"status": "queued", "paper_id": paper_id, "depth": len(queue)}

    def pop_legacy_json(self) -> dict[str, Any] | None:
        queue = _read_json(self.queue_path, [])
        if not isinstance(queue, list) or not queue:
            return None
        item = queue.pop(0)
        _write_json(self.queue_path, queue)
        return item if isinstance(item, dict) else None

    def stage_checkpoint_path(self, run_id: str) -> Path:
        return self.checkpoints_dir / f"{run_id}.json"

    def begin_stage(self, *, paper_id: str, stage: str, config: dict[str, Any]) -> StageRun:
        if stage not in _STAGES:
            raise ValueError(f"unknown stage: {stage}")
        rid = _stable_run_id(paper_id=paper_id, stage=stage, config=config)
        cp = self.stage_checkpoint_path(rid)
        if cp.exists():
            raw = _read_json(cp, {})
            return StageRun(
                run_id=rid,
                paper_id=paper_id,
                stage=stage,
                status=str(raw.get("status", "RESUMED")),
                started_at_unix=int(raw.get("started_at_unix", int(time.time()))),
                finished_at_unix=int(raw.get("finished_at_unix", 0)),
                metrics=dict(raw.get("metrics", {})),
            )
        now = int(time.time())
        run = StageRun(
            run_id=rid,
            paper_id=paper_id,
            stage=stage,
            status="RUNNING",
            started_at_unix=now,
            finished_at_unix=0,
            metrics={},
        )
        _write_json(cp, run.__dict__)
        return run

    def finish_stage(self, *, run: StageRun, status: str, metrics: dict[str, Any]) -> StageRun:
        final = StageRun(
            run_id=run.run_id,
            paper_id=run.paper_id,
            stage=run.stage,
            status=status,
            started_at_unix=run.started_at_unix,
            finished_at_unix=int(time.time()),
            metrics=metrics,
        )
        _write_json(self.stage_checkpoint_path(run.run_id), final.__dict__)
        self.runs_path.parent.mkdir(parents=True, exist_ok=True)
        with self.runs_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(final.__dict__, ensure_ascii=False) + "\n")
        return final

    def compute_drift_alerts(self, *, window: int = 50) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        if self.runs_path.exists():
            for ln in self.runs_path.read_text(encoding="utf-8").splitlines():
                try:
                    raw = json.loads(ln)
                except Exception:
                    continue
                if isinstance(raw, dict):
                    rows.append(raw)
        rows = rows[-max(1, window):]
        by_stage: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by_stage.setdefault(str(r.get("stage", "")), []).append(r)

        alerts: list[dict[str, Any]] = []
        for stage, rs in by_stage.items():
            if not rs:
                continue
            fails = sum(1 for r in rs if str(r.get("status", "")).upper() not in {"OK", "SUCCESS", "DONE"})
            fail_rate = fails / max(1, len(rs))
            if fail_rate >= 0.35 and len(rs) >= 10:
                alerts.append(
                    {
                        "stage": stage,
                        "kind": "high_failure_rate",
                        "fail_rate": round(fail_rate, 3),
                        "samples": len(rs),
                    }
                )
            latencies = [
                max(0, int(r.get("finished_at_unix", 0)) - int(r.get("started_at_unix", 0)))
                for r in rs
                if int(r.get("finished_at_unix", 0)) > 0
            ]
            if latencies:
                p95 = sorted(latencies)[int(0.95 * (len(latencies) - 1))]
                if p95 >= 600:
                    alerts.append(
                        {
                            "stage": stage,
                            "kind": "latency_p95_high",
                            "p95_s": int(p95),
                            "samples": len(latencies),
                        }
                    )

        payload = {
            "generated_at_unix": int(time.time()),
            "window": window,
            "alerts": alerts,
        }
        _write_json(self.alerts_path, payload)
        return payload


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Pipeline orchestration queue/checkpoints")
    p.add_argument("--root", default="output/orchestrator")
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("enqueue")
    q.add_argument("--paper-id", required=True)
    q.add_argument("--config-json", default="{}")

    pop = sub.add_parser("pop")
    pop.add_argument("--print-empty", action="store_true")
    pop.add_argument("--worker-id", default="cli-worker")
    pop.add_argument("--lease-seconds", type=int, default=600)

    b = sub.add_parser("begin")
    b.add_argument("--paper-id", required=True)
    b.add_argument("--stage", required=True, choices=list(_STAGES))
    b.add_argument("--config-json", default="{}")

    f = sub.add_parser("finish")
    f.add_argument("--paper-id", required=True)
    f.add_argument("--stage", required=True, choices=list(_STAGES))
    f.add_argument("--config-json", default="{}")
    f.add_argument("--status", default="OK")
    f.add_argument("--metrics-json", default="{}")

    d = sub.add_parser("drift")
    d.add_argument("--window", type=int, default=50)

    ack = sub.add_parser("ack")
    ack.add_argument("--job-id", type=int, required=True)

    fail = sub.add_parser("fail")
    fail.add_argument("--job-id", type=int, required=True)
    fail.add_argument("--error", default="unknown_error")
    fail.add_argument("--base-backoff-s", type=int, default=60)

    sub.add_parser("queue-dashboard")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    orch = PipelineOrchestrator(Path(args.root))

    if args.cmd == "enqueue":
        cfg = json.loads(args.config_json or "{}")
        print(json.dumps(orch.enqueue(args.paper_id, cfg), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "pop":
        item = orch.lease_next(worker_id=args.worker_id, lease_seconds=max(30, int(args.lease_seconds)))
        if item is None:
            if args.print_empty:
                print("{}")
            return 0
        print(json.dumps(item, indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "begin":
        cfg = json.loads(args.config_json or "{}")
        run = orch.begin_stage(paper_id=args.paper_id, stage=args.stage, config=cfg)
        print(json.dumps(run.__dict__, indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "finish":
        cfg = json.loads(args.config_json or "{}")
        run = orch.begin_stage(paper_id=args.paper_id, stage=args.stage, config=cfg)
        metrics = json.loads(args.metrics_json or "{}")
        final = orch.finish_stage(run=run, status=args.status, metrics=metrics)
        print(json.dumps(final.__dict__, indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "drift":
        payload = orch.compute_drift_alerts(window=max(1, int(args.window)))
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "ack":
        orch.ack(int(args.job_id))
        print(json.dumps({"status": "acked", "job_id": int(args.job_id)}, indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "fail":
        payload = orch.fail(
            int(args.job_id),
            error=str(args.error),
            base_backoff_s=max(1, int(args.base_backoff_s)),
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "queue-dashboard":
        print(json.dumps(orch.queue_dashboard(), indent=2, ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
