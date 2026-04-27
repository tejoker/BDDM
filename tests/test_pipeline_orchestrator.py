from __future__ import annotations

from pathlib import Path

from pipeline_orchestrator import PipelineOrchestrator


def test_orchestrator_enqueue_begin_finish_and_drift(tmp_path: Path) -> None:
    root = tmp_path / "orch"
    orch = PipelineOrchestrator(root)

    q = orch.enqueue("2604.15191", {"mode": "translate"})
    assert q["status"] in {"queued", "duplicate"}

    item = orch.pop()
    assert item is not None
    assert item["paper_id"] == "2604.15191"
    assert int(item["job_id"]) > 0

    run = orch.begin_stage(paper_id="2604.15191", stage="translate", config={"mode": "translate"})
    assert run.run_id.startswith("run_")

    final = orch.finish_stage(run=run, status="OK", metrics={"latency_s": 12})
    assert final.status == "OK"

    alerts = orch.compute_drift_alerts(window=10)
    assert "alerts" in alerts


def test_orchestrator_retry_backoff_flow(tmp_path: Path) -> None:
    orch = PipelineOrchestrator(tmp_path / "orch2")
    _ = orch.enqueue("2604.15152", {"mode": "translate", "max_attempts": 2})
    item = orch.lease_next(worker_id="w1", lease_seconds=60)
    assert item is not None
    job_id = int(item["job_id"])
    res = orch.fail(job_id, error="transient_error", base_backoff_s=1)
    assert res["status"] in {"retry_scheduled", "failed_terminal"}


def test_orchestrator_reclaim_expired_leases(tmp_path: Path) -> None:
    orch = PipelineOrchestrator(tmp_path / "orch3")
    _ = orch.enqueue("2604.15007", {"mode": "translate", "max_attempts": 3})
    item = orch.lease_next(worker_id="w2", lease_seconds=1)
    assert item is not None
    # Force lease expiry.
    import sqlite3
    import time
    con = sqlite3.connect(str((tmp_path / "orch3" / "queue.db")))
    with con:
        con.execute(
            "UPDATE queue_jobs SET lease_until_unix=?, status='leased' WHERE id=?",
            (int(time.time()) - 5, int(item["job_id"])),
        )
    con.close()
    rec = orch.reclaim_expired_leases()
    assert int(rec["reclaimed_retry"]) >= 1
