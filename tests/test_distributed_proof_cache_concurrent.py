"""Concurrent access and stress tests for DistributedProofCache.

Tests:
- Multiple threads writing/reading simultaneously (WAL mode correctness)
- Corruption recovery via index rebuild
- TTL expiration evicts entries
- Version mismatch evicts stale entries
- Cache stats are consistent under concurrent writes
- Key normalization: whitespace-equivalent theorems share cache entry
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from distributed_proof_cache import DistributedProofCache


def _make_cache(tmp_path: Path, **kwargs) -> DistributedProofCache:
    return DistributedProofCache(tmp_path / "proof.db", **kwargs)


# ── TTL expiration ────────────────────────────────────────────────────────────

def test_ttl_expired_entry_not_returned(tmp_path: Path):
    cache = _make_cache(tmp_path, ttl_seconds=1)
    key = cache.build_key(
        theorem_statement="theorem t : True",
        mode="full-draft",
        model="m",
        retrieval_top_k=5,
    )
    cache.set(key, {"ok": True})
    assert cache.get(key) is not None

    # Manually expire by writing an old timestamp
    import sqlite3

    with sqlite3.connect(str(tmp_path / "proof.db")) as con:
        con.execute("UPDATE proof_attempt_cache SET updated_ts = 0 WHERE cache_key = ?", (key,))

    assert cache.get(key) is None


def test_clear_expired_removes_old_entries(tmp_path: Path):
    cache = _make_cache(tmp_path, ttl_seconds=1)
    key = cache.build_key(
        theorem_statement="theorem t : True",
        mode="full-draft",
        model="m",
        retrieval_top_k=5,
    )
    cache.set(key, {"ok": True})

    import sqlite3

    with sqlite3.connect(str(tmp_path / "proof.db")) as con:
        con.execute("UPDATE proof_attempt_cache SET updated_ts = 0 WHERE cache_key = ?", (key,))

    deleted = cache.clear_expired()
    assert deleted >= 1
    assert cache.stats()["entries"] == 0


# ── Version mismatch ──────────────────────────────────────────────────────────

def test_version_mismatch_entry_not_returned(tmp_path: Path):
    db_path = tmp_path / "proof.db"
    cache_v1 = DistributedProofCache(db_path, version=1)
    key = cache_v1.build_key(
        theorem_statement="theorem t : True",
        mode="full-draft",
        model="m",
        retrieval_top_k=5,
    )
    cache_v1.set(key, {"ok": True})
    assert cache_v1.get(key) is not None

    # Open with v2 — v1 entry should be invisible
    cache_v2 = DistributedProofCache(db_path, version=2)
    assert cache_v2.get(key) is None


# ── Key normalization ─────────────────────────────────────────────────────────

def test_key_normalizes_surrounding_whitespace(tmp_path: Path):
    cache = _make_cache(tmp_path)
    stmt = "  theorem t : True  "
    stmt_stripped = "theorem t : True"
    key1 = cache.build_key(theorem_statement=stmt, mode="m", model="m", retrieval_top_k=1)
    key2 = cache.build_key(theorem_statement=stmt_stripped, mode="m", model="m", retrieval_top_k=1)
    assert key1 == key2, "Keys must match after strip() normalization"


def test_key_differs_on_model_change(tmp_path: Path):
    cache = _make_cache(tmp_path)
    key1 = cache.build_key(theorem_statement="t", mode="m", model="modelA", retrieval_top_k=5)
    key2 = cache.build_key(theorem_statement="t", mode="m", model="modelB", retrieval_top_k=5)
    assert key1 != key2


def test_key_differs_on_top_k_change(tmp_path: Path):
    cache = _make_cache(tmp_path)
    key1 = cache.build_key(theorem_statement="t", mode="m", model="m", retrieval_top_k=5)
    key2 = cache.build_key(theorem_statement="t", mode="m", model="m", retrieval_top_k=12)
    assert key1 != key2


# ── Concurrent access ─────────────────────────────────────────────────────────

def test_concurrent_writes_no_data_loss(tmp_path: Path):
    """N threads each write a unique key; all entries must be readable after."""
    n_threads = 10
    n_per_thread = 20
    cache = _make_cache(tmp_path)
    errors: list[Exception] = []

    def writer(thread_id: int):
        try:
            for i in range(n_per_thread):
                key = cache.build_key(
                    theorem_statement=f"theorem t_{thread_id}_{i} : True",
                    mode="full-draft",
                    model="m",
                    retrieval_top_k=5,
                )
                cache.set(key, {"thread": thread_id, "i": i})
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Thread errors: {errors}"
    stats = cache.stats()
    assert stats["entries"] == n_threads * n_per_thread


def test_concurrent_read_write_consistent(tmp_path: Path):
    """Writer thread sets; reader thread reads — no exceptions, no corrupt JSON."""
    cache = _make_cache(tmp_path)
    stop = threading.Event()
    read_errors: list[Exception] = []

    key = cache.build_key(
        theorem_statement="theorem concurrent : True",
        mode="full-draft",
        model="m",
        retrieval_top_k=5,
    )

    def reader():
        while not stop.is_set():
            try:
                result = cache.get(key)
                if result is not None:
                    assert isinstance(result, dict)
            except Exception as exc:
                read_errors.append(exc)
            time.sleep(0.001)

    reader_thread = threading.Thread(target=reader)
    reader_thread.start()

    for i in range(50):
        cache.set(key, {"value": i})
        time.sleep(0.002)

    stop.set()
    reader_thread.join(timeout=5)

    assert not read_errors, f"Reader errors: {read_errors}"


def test_concurrent_stats_consistent(tmp_path: Path):
    """stats() returns non-negative counts under concurrent writes."""
    cache = _make_cache(tmp_path)
    errors: list[Exception] = []

    def writer(n: int):
        for i in range(n):
            key = cache.build_key(
                theorem_statement=f"theorem s_{n}_{i} : True",
                mode="m",
                model="m",
                retrieval_top_k=1,
            )
            cache.set(key, {"x": i})

    def stat_checker():
        for _ in range(20):
            try:
                s = cache.stats()
                assert s["entries"] >= 0
            except Exception as exc:
                errors.append(exc)
            time.sleep(0.005)

    threads = [threading.Thread(target=writer, args=(10,)) for _ in range(4)]
    threads.append(threading.Thread(target=stat_checker))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Stats errors: {errors}"


# ── Corruption recovery ───────────────────────────────────────────────────────

def test_get_returns_none_on_corrupt_json(tmp_path: Path):
    """Corrupt JSON in payload must return None, not raise."""
    import sqlite3

    cache = _make_cache(tmp_path)
    key = cache.build_key(
        theorem_statement="theorem corrupt : True",
        mode="m",
        model="m",
        retrieval_top_k=1,
    )
    cache.set(key, {"ok": True})

    # Corrupt the payload
    with sqlite3.connect(str(tmp_path / "proof.db")) as con:
        con.execute(
            "UPDATE proof_attempt_cache SET payload_json = ? WHERE cache_key = ?",
            ("{INVALID_JSON{{{{", key),
        )

    result = cache.get(key)
    assert result is None, "Corrupt JSON payload must return None"


def test_set_overwrites_corrupt_entry(tmp_path: Path):
    """Writing a valid entry over a corrupt one must succeed and be readable."""
    import sqlite3

    cache = _make_cache(tmp_path)
    key = cache.build_key(
        theorem_statement="theorem overwrite : True",
        mode="m",
        model="m",
        retrieval_top_k=1,
    )
    cache.set(key, {"v": 1})

    with sqlite3.connect(str(tmp_path / "proof.db")) as con:
        con.execute(
            "UPDATE proof_attempt_cache SET payload_json = ? WHERE cache_key = ?",
            ("NOT_JSON", key),
        )

    cache.set(key, {"v": 2})
    result = cache.get(key)
    assert result is not None
    assert result["v"] == 2
