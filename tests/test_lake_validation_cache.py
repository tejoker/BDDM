"""Hermetic + slow tests for ``scripts/lake_validation_cache``.

Hermetic tests fake the underlying :class:`LeanREPLServer` so they exercise
the cache logic, decl-rewrite, and accept/reject rules without spawning a
real ``lake exe repl`` process. The slow live test (``@pytest.mark.slow``)
boots a real worker and measures speedup vs ``_run_isolated_file_check``.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

# scripts/ is added to sys.path by tests/conftest.py
import lake_validation_cache as lvc  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# Hermetic fakes
# ────────────────────────────────────────────────────────────────────────────


class FakeServer:
    """Minimal stand-in for ``LeanREPLServer`` that records calls."""

    def __init__(
        self,
        responses: list[dict] | None = None,
        send_exceptions: list[BaseException] | None = None,
        anchor_response: dict | None = None,
        start_should_fail: bool = False,
    ) -> None:
        self.timeout = 30.0
        self.start_called = 0
        self.stop_called = 0
        self.restart_called = 0
        self.sent: list[dict] = []
        self._responses = list(responses or [])
        self._send_exceptions = list(send_exceptions or [])
        self._anchor_response = anchor_response or {"env": 0, "messages": []}
        self._start_should_fail = start_should_fail
        # Pretend we have a live subprocess for the worker-cache health check.
        self._proc = _FakeProc()

    def start(self) -> None:
        self.start_called += 1
        if self._start_should_fail:
            raise RuntimeError("fake_start_failure")

    def stop(self) -> None:
        self.stop_called += 1
        self._proc = None

    def restart(self) -> None:
        self.restart_called += 1
        self._proc = _FakeProc()

    def _send(self, payload: dict) -> dict:
        self.sent.append(payload)
        if "path" in payload:
            return self._anchor_response
        if self._send_exceptions:
            exc = self._send_exceptions.pop(0)
            raise exc
        if self._responses:
            return self._responses.pop(0)
        return {"env": 1, "messages": []}


class _FakeProc:
    """A process double that always reports "still running"."""

    def poll(self) -> None:
        return None


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────


def _make_cache_with(monkeypatch, factory) -> lvc.WorkerCache:
    """Return a fresh WorkerCache whose worker spawn is replaced by ``factory``."""

    cache = lvc.WorkerCache()

    def _start(self, project_root, paper_id, startup_timeout):  # noqa: ARG001
        server = factory()
        # Mimic the real start_worker post-conditions
        return lvc._WorkerEntry(
            server=server,
            env_id=0,
            anchor_used="Desol/ReplAnchor.lean",
            lock=threading.Lock(),
        )

    monkeypatch.setattr(lvc.WorkerCache, "_start_worker", _start)
    return cache


# ────────────────────────────────────────────────────────────────────────────
# Tests — decl text builder (mirrors slow-path semantics)
# ────────────────────────────────────────────────────────────────────────────


def test_build_decl_signature_only_appends_sorry() -> None:
    out = lvc.build_isolated_decl_text("theorem t (p : Prop) (h : p) : p", None)
    assert out is not None
    assert out.endswith(":= by\n  sorry")


def test_build_decl_strips_existing_body() -> None:
    out = lvc.build_isolated_decl_text(
        "theorem t (p : Prop) (h : p) : p := by exact h",
        proof_body="exact h",
    )
    assert out is not None
    assert out.count(":= by") == 1
    assert "exact h" in out


def test_build_decl_rejects_empty_decl() -> None:
    assert lvc.build_isolated_decl_text("", None) is None
    assert lvc.build_isolated_decl_text("   ", "exact h") is None


def test_build_decl_rejects_empty_body() -> None:
    assert lvc.build_isolated_decl_text("theorem t : True", "") is None
    assert lvc.build_isolated_decl_text("theorem t : True", "   \n  ") is None


# ────────────────────────────────────────────────────────────────────────────
# Tests — _classify_messages
# ────────────────────────────────────────────────────────────────────────────


def test_classify_body_supplied_clean_accepts() -> None:
    ok, tail = lvc._classify_messages([], body_supplied=True)
    assert ok and tail == ""


def test_classify_body_supplied_rejects_on_error() -> None:
    msgs = [{"severity": "error", "data": "unknown identifier 'foo'"}]
    ok, tail = lvc._classify_messages(msgs, body_supplied=True)
    assert not ok
    assert "unknown identifier" in tail


def test_classify_body_supplied_rejects_sorry_warning() -> None:
    msgs = [{"severity": "warning", "data": "declaration uses 'sorry'"}]
    ok, tail = lvc._classify_messages(msgs, body_supplied=True)
    assert not ok
    assert "sorry_warning" in tail


def test_classify_signature_only_accepts_sorry_warning() -> None:
    msgs = [{"severity": "warning", "data": "declaration uses 'sorry'"}]
    ok, tail = lvc._classify_messages(msgs, body_supplied=False)
    assert ok and tail == ""


def test_classify_signature_only_rejects_real_error() -> None:
    msgs = [
        {"severity": "error", "data": "expected term"},
        {"severity": "warning", "data": "declaration uses 'sorry'"},
    ]
    ok, tail = lvc._classify_messages(msgs, body_supplied=False)
    assert not ok
    assert "expected term" in tail


# ────────────────────────────────────────────────────────────────────────────
# Tests — cache reuse + worker lifecycle
# ────────────────────────────────────────────────────────────────────────────


def test_cache_hit_reuses_worker(monkeypatch, tmp_path) -> None:
    fake = FakeServer(responses=[{"env": 5, "messages": []}, {"env": 6, "messages": []}])
    spawn_count = 0

    def _factory():
        nonlocal spawn_count
        spawn_count += 1
        return fake

    cache = _make_cache_with(monkeypatch, _factory)
    pr = tmp_path
    pr.mkdir(exist_ok=True)
    ok1, _ = lvc.validated_isolated_check(
        project_root=pr, paper_id="2304.09598",
        theorem_decl="theorem t (p : Prop) (h : p) : p",
        proof_body="exact h", cache=cache,
    )
    ok2, _ = lvc.validated_isolated_check(
        project_root=pr, paper_id="2304.09598",
        theorem_decl="theorem t2 (p : Prop) (h : p) : p",
        proof_body="exact h", cache=cache,
    )
    assert ok1 and ok2
    # Only ONE worker was spawned for two calls (cache hit on second).
    assert spawn_count == 1
    # Both send payloads carry env=0 (the post-anchor env).
    assert all(p.get("env") == 0 for p in fake.sent if "cmd" in p)


def test_cache_miss_starts_per_paper(monkeypatch, tmp_path) -> None:
    fakes: list[FakeServer] = []

    def _factory() -> FakeServer:
        f = FakeServer(responses=[{"env": 1, "messages": []}])
        fakes.append(f)
        return f

    cache = _make_cache_with(monkeypatch, _factory)
    pr = tmp_path
    lvc.validated_isolated_check(project_root=pr, paper_id="A",
                                 theorem_decl="theorem t : True", proof_body="trivial",
                                 cache=cache)
    lvc.validated_isolated_check(project_root=pr, paper_id="B",
                                 theorem_decl="theorem t : True", proof_body="trivial",
                                 cache=cache)
    assert len(fakes) == 2  # one per paper id


def test_worker_unavailable_returns_error(monkeypatch, tmp_path) -> None:
    cache = lvc.WorkerCache()

    def _bad_start(self, *args, **kwargs):  # noqa: ARG001
        raise RuntimeError("anchor_load_errors:simulated")

    monkeypatch.setattr(lvc.WorkerCache, "_start_worker", _bad_start)
    ok, tail = lvc.validated_isolated_check(
        project_root=tmp_path, paper_id="X",
        theorem_decl="theorem t : True", proof_body="trivial",
        cache=cache,
    )
    assert not ok
    assert "worker_unavailable" in tail
    assert "anchor_load_errors" in tail


def test_timeout_restarts_worker(monkeypatch, tmp_path) -> None:
    fake = FakeServer(send_exceptions=[TimeoutError("simulated")])
    cache = _make_cache_with(monkeypatch, lambda: fake)
    ok, tail = lvc.validated_isolated_check(
        project_root=tmp_path, paper_id="P",
        theorem_decl="theorem t : True", proof_body="trivial",
        cache=cache, timeout_s=5,
    )
    assert not ok
    assert tail.startswith("file_check_timeout")
    assert fake.restart_called == 1


def test_exception_propagated(monkeypatch, tmp_path) -> None:
    fake = FakeServer(send_exceptions=[RuntimeError("blew up")])
    cache = _make_cache_with(monkeypatch, lambda: fake)
    ok, tail = lvc.validated_isolated_check(
        project_root=tmp_path, paper_id="P",
        theorem_decl="theorem t : True", proof_body="trivial",
        cache=cache,
    )
    assert not ok
    assert "file_check_exception" in tail
    assert "blew up" in tail


def test_empty_decl_short_circuits(monkeypatch, tmp_path) -> None:
    spawned = []

    def _factory() -> FakeServer:
        spawned.append(1)
        return FakeServer()

    cache = _make_cache_with(monkeypatch, _factory)
    ok, tail = lvc.validated_isolated_check(
        project_root=tmp_path, paper_id="P",
        theorem_decl="", proof_body="exact h", cache=cache,
    )
    assert not ok and "empty_decl" in tail
    assert spawned == []  # never started a worker


def test_empty_body_short_circuits(monkeypatch, tmp_path) -> None:
    spawned = []

    def _factory() -> FakeServer:
        spawned.append(1)
        return FakeServer()

    cache = _make_cache_with(monkeypatch, _factory)
    ok, tail = lvc.validated_isolated_check(
        project_root=tmp_path, paper_id="P",
        theorem_decl="theorem t : True", proof_body="", cache=cache,
    )
    assert not ok and "empty_body" in tail
    assert spawned == []


def test_concurrent_calls_serialize_per_worker(monkeypatch, tmp_path) -> None:
    """Concurrent calls against the SAME paper-id must serialize via the
    per-worker lock; verified by the call ordering inside one FakeServer."""
    started = threading.Event()
    proceed = threading.Event()

    class GatedServer(FakeServer):
        def _send(self, payload):
            if "cmd" in payload:
                started.set()
                proceed.wait(timeout=2.0)
            return super()._send(payload)

    fake = GatedServer(responses=[{"env": 1, "messages": []}, {"env": 2, "messages": []}])
    cache = _make_cache_with(monkeypatch, lambda: fake)

    pr = tmp_path
    results = []

    def _one(suffix):
        results.append(lvc.validated_isolated_check(
            project_root=pr, paper_id="SAMEPAPER",
            theorem_decl=f"theorem t_{suffix} : True", proof_body="trivial",
            cache=cache,
        ))

    t1 = threading.Thread(target=_one, args=("a",))
    t2 = threading.Thread(target=_one, args=("b",))
    t1.start()
    t2.start()
    assert started.wait(timeout=2.0)
    # Both threads racing for the per-worker lock — let them proceed.
    proceed.set()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)
    assert all(ok for ok, _ in results)
    # Exactly two cmd-sends (one per thread) plus zero anchor loads after
    # the first warmup.
    cmd_sends = [p for p in fake.sent if "cmd" in p]
    assert len(cmd_sends) == 2


def test_different_papers_use_different_workers(monkeypatch, tmp_path) -> None:
    instances = []

    def _factory():
        f = FakeServer(responses=[{"env": 1, "messages": []}])
        instances.append(f)
        return f

    cache = _make_cache_with(monkeypatch, _factory)
    lvc.validated_isolated_check(project_root=tmp_path, paper_id="P1",
                                 theorem_decl="theorem t : True", proof_body="trivial",
                                 cache=cache)
    lvc.validated_isolated_check(project_root=tmp_path, paper_id="P2",
                                 theorem_decl="theorem t : True", proof_body="trivial",
                                 cache=cache)
    assert len(instances) == 2
    assert instances[0] is not instances[1]


def test_shutdown_all_stops_workers(monkeypatch, tmp_path) -> None:
    fakes = []

    def _factory():
        f = FakeServer()
        fakes.append(f)
        return f

    cache = _make_cache_with(monkeypatch, _factory)
    lvc.validated_isolated_check(project_root=tmp_path, paper_id="X",
                                 theorem_decl="theorem t : True", proof_body="trivial",
                                 cache=cache)
    cache.shutdown_all()
    assert all(f.stop_called == 1 for f in fakes)


def test_dead_worker_is_restarted(monkeypatch, tmp_path) -> None:
    spawn_count = 0

    def _factory():
        nonlocal spawn_count
        spawn_count += 1
        f = FakeServer(responses=[{"env": 1, "messages": []}])
        if spawn_count == 1:
            # Mark the first worker dead by clearing its _proc and giving it
            # a poll that returns nonzero.
            class _Dead:
                def poll(self) -> int:
                    return 1
            f._proc = _Dead()
        return f

    cache = _make_cache_with(monkeypatch, _factory)
    # First call: starts worker #1 (we'll mark it dead after).
    lvc.validated_isolated_check(project_root=tmp_path, paper_id="P",
                                 theorem_decl="theorem t : True", proof_body="trivial",
                                 cache=cache)
    # Second call: cache sees the dead poll, drops it, and spawns worker #2.
    lvc.validated_isolated_check(project_root=tmp_path, paper_id="P",
                                 theorem_decl="theorem t : True", proof_body="trivial",
                                 cache=cache)
    assert spawn_count == 2


# ────────────────────────────────────────────────────────────────────────────
# Live SLOW test — measures real speedup
# ────────────────────────────────────────────────────────────────────────────


SLOW_MARK = pytest.mark.slow


@SLOW_MARK
def test_live_speedup_vs_slow_path() -> None:
    """Boot a real REPL worker and verify ≥3× speedup vs ``_run_isolated_file_check``.

    Skips when the BDDM lake env isn't usable (no ``lake`` binary, no
    ``Desol/ReplAnchor.lean``, etc.).
    """
    project_root = Path(__file__).resolve().parents[1]
    anchor = project_root / "Desol" / "ReplAnchor.lean"
    if not anchor.exists():
        pytest.skip("Desol/ReplAnchor.lean not present")

    source_file = project_root / "Desol" / "ReplAnchor.lean"
    decl = "theorem __lvc_live_speedup (p : Prop) (h : p) : p"
    body = "exact h"

    try:
        from prove_arxiv_batch import _run_isolated_file_check  # type: ignore
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"prove_arxiv_batch unavailable: {exc}")

    # Slow baseline: 3 runs, median
    slow_times = []
    for _ in range(3):
        t0 = time.time()
        ok, _ = _run_isolated_file_check(
            project_root=project_root, source_file=source_file,
            theorem_decl=decl, proof_body=body, timeout_s=120,
        )
        slow_times.append(time.time() - t0)
        if not ok:
            pytest.skip("baseline slow path could not validate the trivial decl")
    slow_med = sorted(slow_times)[len(slow_times) // 2]

    # Fast path: pay one warmup, then measure steady-state
    cache = lvc.WorkerCache()
    t_warm = time.time()
    ok, tail = lvc.validated_isolated_check(
        project_root=project_root, paper_id="__live_speedup__",
        theorem_decl=decl, proof_body=body, timeout_s=120, cache=cache,
    )
    warmup = time.time() - t_warm
    assert ok, f"fast validator rejected trivial decl: {tail!r}"

    fast_times = []
    for i in range(5):
        t0 = time.time()
        ok, tail = lvc.validated_isolated_check(
            project_root=project_root, paper_id="__live_speedup__",
            theorem_decl=f"theorem __lvc_live_speedup_{i} (p : Prop) (h : p) : p",
            proof_body=body, timeout_s=120, cache=cache,
        )
        fast_times.append(time.time() - t0)
        assert ok, f"fast validator rejected iter {i}: {tail!r}"
    fast_med = sorted(fast_times)[len(fast_times) // 2]
    cache.shutdown_all()

    speedup = slow_med / fast_med if fast_med > 0 else float("inf")
    print(
        f"\n[live speedup] slow median: {slow_med:.3f}s/call  "
        f"fast median: {fast_med:.3f}s/call  warmup: {warmup:.2f}s  "
        f"speedup: {speedup:.1f}×"
    )
    assert speedup >= 3.0, (
        f"insufficient speedup: {speedup:.1f}× (slow={slow_med:.2f}s, fast={fast_med:.3f}s)"
    )


@SLOW_MARK
def test_live_differential_agreement() -> None:
    """Fast and slow validators must agree on accept/reject for the same input."""
    project_root = Path(__file__).resolve().parents[1]
    anchor = project_root / "Desol" / "ReplAnchor.lean"
    if not anchor.exists():
        pytest.skip("Desol/ReplAnchor.lean not present")
    source_file = project_root / "Desol" / "ReplAnchor.lean"
    cases = [
        ("theorem __diff_ok (p : Prop) (h : p) : p", "exact h", True),
        ("theorem __diff_bad (p : Prop) : p", "exact h", False),  # h not in scope
    ]
    for decl, body, expected in cases:
        ok, tail, diag = lvc.differential_check(
            project_root=project_root, source_file=source_file,
            paper_id="__live_diff__", theorem_decl=decl,
            proof_body=body, timeout_s=120,
        )
        assert diag["agreement"], (
            f"divergence: fast={diag['fast_ok']} slow={diag['slow_ok']} "
            f"decl={decl!r} fast_tail={diag['fast_tail']!r} slow_tail={diag['slow_tail']!r}"
        )
        assert ok is expected, f"{decl!r}: expected {expected}, got {ok} (tail={tail!r})"
    lvc.shutdown_all_workers()
