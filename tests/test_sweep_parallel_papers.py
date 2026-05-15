"""Hermetic tests for `sweep_lemma_factor_v2` `--parallel-papers`.

These tests monkey-patch `_sweep_paper` so no Mistral / lake calls happen.
We verify:

  1. With `--parallel-papers=1`, papers run sequentially.
  2. With `--parallel-papers=2`, work is interleaved (two `_sweep_paper`
     calls overlap in wall-clock).
  3. Aggregate counts (bucket_counts, totals, reports list) are identical
     across sequential vs parallel runs.
  4. The summary JSON ends up well-formed at the expected path.
  5. A thread exception in one paper does NOT kill the sibling threads.
"""
from __future__ import annotations

import json
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

import sweep_lemma_factor_v2 as sweep


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _make_fake_sweep_paper(
    *,
    started_event: threading.Event,
    barrier: threading.Barrier | None = None,
    sleep_s: float = 0.0,
    failing_paper: str | None = None,
):
    """Return a fake `_sweep_paper` that records overlap.

    If `barrier` is set, every call rendezvous's at the barrier before
    returning — proving real concurrency happened.
    If `failing_paper` matches the incoming paper_id, raise.
    """
    call_log: list[tuple[str, float, float]] = []
    log_lock = threading.Lock()

    def fake(*, paper_id, bucket_counts, **kw) -> dict[str, Any]:
        t0 = time.monotonic()
        started_event.set()
        if failing_paper is not None and paper_id == failing_paper:
            raise RuntimeError(f"boom:{paper_id}")
        if barrier is not None:
            barrier.wait(timeout=5.0)
        if sleep_s > 0:
            time.sleep(sleep_s)
        bucket_counts["lake_errors"] += 1
        bucket_counts["transport_errors"] += 2
        t1 = time.monotonic()
        with log_lock:
            call_log.append((paper_id, t0, t1))
        return {
            "paper_id": paper_id,
            "candidates_elaborated": 3,
            "candidates_attempted": 2,
            "first_pass_validated": 1,
            "factored": 0,
            "aux_proposed": 0,
            "aux_elaborated": 0,
            "aux_closed": 0,
            "composed": 0,
            "audit_survived": 1,
            "routed_to_axiom_backed": 0,
            "factor_recursive_attempts": 0,
            "factor_recursive_closures": 0,
            "details": [],
        }

    return fake, call_log


def _run_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    papers: list[str],
    parallel_n: int,
    fake_sweep,
) -> tuple[int, Path]:
    """Invoke `sweep.main()` with a clean argv and fake `_sweep_paper`."""
    summary_path = tmp_path / "summary.json"
    # Relative-to-PROJECT_ROOT path — sweep.main joins via PROJECT_ROOT.
    rel = summary_path.resolve()
    argv = [
        "sweep_lemma_factor_v2",
        "--dry-run",
        "--no-use-fast-validation",
        "--no-auto-promote-to-curated",
        "--summary",
        str(rel),
        "--parallel-papers",
        str(parallel_n),
    ]
    for p in papers:
        argv += ["--paper", p]
    monkeypatch.setattr(sweep.sys, "argv", argv)
    monkeypatch.setattr(sweep, "_sweep_paper", fake_sweep)
    # `--dry-run` already skips the Mistral client build, so we don't need
    # to patch _build_mistral_client.
    rc = sweep.main()
    return rc, summary_path


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_parallel_papers_runs_concurrently(monkeypatch, tmp_path) -> None:
    """With --parallel-papers=2 and a Barrier(2), the two calls must
    rendezvous within the timeout — proving true concurrency."""
    started = threading.Event()
    barrier = threading.Barrier(2)
    fake, log = _make_fake_sweep_paper(
        started_event=started, barrier=barrier, sleep_s=0.05,
    )
    rc, summary_path = _run_main(
        monkeypatch, tmp_path,
        papers=["paperA", "paperB"], parallel_n=2, fake_sweep=fake,
    )
    assert rc == 0
    assert len(log) == 2
    # Intervals overlap — start of the second begins before end of the first.
    log_sorted = sorted(log, key=lambda r: r[1])
    (_, a_start, a_end) = log_sorted[0]
    (_, b_start, b_end) = log_sorted[1]
    assert b_start < a_end, (
        f"expected overlap: a=[{a_start},{a_end}] b=[{b_start},{b_end}]"
    )


def test_parallel_counts_match_sequential(monkeypatch, tmp_path) -> None:
    """N=1 and N=4 yield identical bucket_counts/totals on the same input."""
    papers = ["pA", "pB", "pC", "pD"]

    started_seq = threading.Event()
    fake_seq, _ = _make_fake_sweep_paper(started_event=started_seq)
    rc1, sp1 = _run_main(
        monkeypatch, tmp_path / "seq",
        papers=papers, parallel_n=1, fake_sweep=fake_seq,
    )
    assert rc1 == 0
    seq_summary = json.loads(sp1.read_text(encoding="utf-8"))

    started_par = threading.Event()
    fake_par, _ = _make_fake_sweep_paper(started_event=started_par)
    rc2, sp2 = _run_main(
        monkeypatch, tmp_path / "par",
        papers=papers, parallel_n=4, fake_sweep=fake_par,
    )
    assert rc2 == 0
    par_summary = json.loads(sp2.read_text(encoding="utf-8"))

    assert seq_summary["bucket_counts"] == par_summary["bucket_counts"]
    assert seq_summary["totals"] == par_summary["totals"]
    # Both runs must list all four papers (order is allowed to differ for
    # the parallel run, so we compare sets).
    assert {r["paper_id"] for r in seq_summary["papers"]} == set(papers)
    assert {r["paper_id"] for r in par_summary["papers"]} == set(papers)


def test_parallel_summary_is_well_formed_json(monkeypatch, tmp_path) -> None:
    """Concurrent partial writes must never leave the summary mid-write."""
    started = threading.Event()
    fake, _ = _make_fake_sweep_paper(started_event=started, sleep_s=0.01)
    rc, summary_path = _run_main(
        monkeypatch, tmp_path,
        papers=["p1", "p2", "p3"], parallel_n=3, fake_sweep=fake,
    )
    assert rc == 0
    # File exists and parses; .tmp sibling does NOT exist after final write.
    txt = summary_path.read_text(encoding="utf-8")
    data = json.loads(txt)
    assert data["parallel_papers"] == 3
    assert "totals" in data
    assert not summary_path.with_suffix(summary_path.suffix + ".tmp").exists()


def test_parallel_isolation_per_paper_thread_failure(monkeypatch, tmp_path) -> None:
    """One thread raising must not crash the sweep nor lose siblings."""
    started = threading.Event()
    fake, _ = _make_fake_sweep_paper(
        started_event=started, sleep_s=0.01, failing_paper="bad",
    )
    rc, summary_path = _run_main(
        monkeypatch, tmp_path,
        papers=["good1", "bad", "good2"], parallel_n=3, fake_sweep=fake,
    )
    assert rc == 0
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    by_id = {r["paper_id"]: r for r in data["papers"]}
    assert "good1" in by_id and "good2" in by_id and "bad" in by_id
    # Failing paper has the recorded error string; siblings have totals.
    assert "thread_exception" in by_id["bad"].get("error", "")
    assert by_id["good1"].get("candidates_elaborated", 0) == 3
    assert by_id["good2"].get("candidates_elaborated", 0) == 3


def test_parallel_default_is_one_paper_at_a_time(monkeypatch, tmp_path) -> None:
    """Without --parallel-papers we must NOT see overlapping intervals."""
    started = threading.Event()
    fake, log = _make_fake_sweep_paper(
        started_event=started, sleep_s=0.05,
    )
    rc, _ = _run_main(
        monkeypatch, tmp_path,
        papers=["pA", "pB"], parallel_n=1, fake_sweep=fake,
    )
    assert rc == 0
    # Intervals must NOT overlap on the sequential path.
    log_sorted = sorted(log, key=lambda r: r[1])
    (_, a_start, a_end) = log_sorted[0]
    (_, b_start, b_end) = log_sorted[1]
    assert b_start >= a_end - 1e-3
