"""Hermetic tests for `scripts/proof_attempt_cache.py`.

Verifies the (statement_hash, method) cache semantics:
  - Canonicalization collapses whitespace + drops body.
  - Hash is stable across whitespace/case/body variations.
  - Lookup returns the most-recent entry by timestamp.
  - record_proof_attempt is append-only (later entries supersede).
  - Cache survives unparseable lines gracefully.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from proof_attempt_cache import (
    CacheEntry,
    cache_stats,
    canonicalize_statement,
    lookup_cached_proof,
    record_proof_attempt,
    statement_hash,
)


def test_canonicalize_collapses_whitespace():
    a = canonicalize_statement("theorem  foo  :  P  :=  by   sorry")
    b = canonicalize_statement("theorem foo : P := by sorry")
    assert a == b
    # Body dropped.
    assert ":=" not in a or "by sorry" not in a


def test_canonicalize_strips_body():
    a = canonicalize_statement("theorem foo : P := by sorry")
    b = canonicalize_statement("theorem foo : P")
    assert a == b


def test_canonicalize_lowercases():
    a = canonicalize_statement("Theorem Foo : P := by sorry")
    b = canonicalize_statement("theorem foo : p := by sorry")
    assert a == b


def test_statement_hash_stable_across_whitespace(tmp_path: Path):
    h1 = statement_hash("theorem foo  :   P   :=  by sorry")
    h2 = statement_hash("theorem foo : P")
    assert h1 == h2


def test_statement_hash_differs_for_different_shapes():
    h1 = statement_hash("theorem foo : P := by sorry")
    h2 = statement_hash("theorem foo : Q := by sorry")
    assert h1 != h2


def test_record_and_lookup_round_trip(tmp_path: Path):
    cache = tmp_path / "cache.jsonl"
    record_proof_attempt(
        lean_statement="theorem foo : P",
        method="whole_proof",
        proof_body="aesop",
        validated=True,
        cache_path=cache,
    )
    entry = lookup_cached_proof(
        lean_statement="theorem foo : P := by sorry",  # body added
        method="whole_proof",
        cache_path=cache,
    )
    assert entry is not None
    assert entry.proof_body == "aesop"
    assert entry.validated is True


def test_lookup_returns_most_recent_for_same_method(tmp_path: Path):
    cache = tmp_path / "cache.jsonl"
    # Two entries for the same (statement, method), the later one wins
    record_proof_attempt(
        lean_statement="theorem foo : P",
        method="whole_proof",
        proof_body="aesop",
        validated=False,
        cache_path=cache,
    )
    time.sleep(0.001)
    record_proof_attempt(
        lean_statement="theorem foo : P",
        method="whole_proof",
        proof_body="simp_all",
        validated=True,
        cache_path=cache,
    )
    entry = lookup_cached_proof(
        lean_statement="theorem foo : P",
        method="whole_proof",
        cache_path=cache,
    )
    assert entry is not None
    assert entry.proof_body == "simp_all"
    assert entry.validated is True


def test_lookup_separates_methods(tmp_path: Path):
    cache = tmp_path / "cache.jsonl"
    record_proof_attempt(
        lean_statement="theorem foo : P",
        method="whole_proof",
        proof_body="aesop",
        validated=True,
        cache_path=cache,
    )
    record_proof_attempt(
        lean_statement="theorem foo : P",
        method="lemma_factor",
        proof_body="<;> trivial",
        validated=False,
        cache_path=cache,
    )
    e1 = lookup_cached_proof(
        lean_statement="theorem foo : P", method="whole_proof", cache_path=cache,
    )
    e2 = lookup_cached_proof(
        lean_statement="theorem foo : P", method="lemma_factor", cache_path=cache,
    )
    assert e1 is not None and e1.proof_body == "aesop" and e1.validated is True
    assert e2 is not None and e2.proof_body == "<;> trivial" and e2.validated is False


def test_lookup_returns_none_when_no_match(tmp_path: Path):
    cache = tmp_path / "cache.jsonl"
    record_proof_attempt(
        lean_statement="theorem foo : P",
        method="whole_proof",
        proof_body="aesop",
        validated=True,
        cache_path=cache,
    )
    assert lookup_cached_proof(
        lean_statement="theorem bar : Q", method="whole_proof", cache_path=cache,
    ) is None


def test_record_drops_empty_inputs(tmp_path: Path):
    cache = tmp_path / "cache.jsonl"
    record_proof_attempt(
        lean_statement="", method="whole_proof",
        proof_body="aesop", validated=True, cache_path=cache,
    )
    record_proof_attempt(
        lean_statement="theorem foo : P", method="",
        proof_body="aesop", validated=True, cache_path=cache,
    )
    assert not cache.exists() or cache.read_text(encoding="utf-8").strip() == ""


def test_cache_stats(tmp_path: Path):
    cache = tmp_path / "cache.jsonl"
    record_proof_attempt(
        lean_statement="theorem foo : P", method="whole_proof",
        proof_body="aesop", validated=True, cache_path=cache,
    )
    record_proof_attempt(
        lean_statement="theorem bar : Q", method="whole_proof",
        proof_body="simp_all", validated=False, cache_path=cache,
    )
    record_proof_attempt(
        lean_statement="theorem foo : P", method="lemma_factor",
        proof_body="<;> trivial", validated=True, cache_path=cache,
    )
    stats = cache_stats(cache)
    assert stats["entries"] == 3
    assert stats["validated"] == 2
    assert stats["rejected"] == 1
    assert stats["methods"]["whole_proof"] == 2
    assert stats["methods"]["lemma_factor"] == 1
    assert stats["unique_statements"] == 2  # foo and bar


def test_cache_handles_corrupt_lines(tmp_path: Path):
    cache = tmp_path / "cache.jsonl"
    record_proof_attempt(
        lean_statement="theorem foo : P", method="whole_proof",
        proof_body="aesop", validated=True, cache_path=cache,
    )
    # Append a corrupt line
    with cache.open("a") as f:
        f.write("not-json\n")
        f.write("{partially: bad}\n")
    # Lookup still works, ignoring the corrupt lines
    entry = lookup_cached_proof(
        lean_statement="theorem foo : P",
        method="whole_proof",
        cache_path=cache,
    )
    assert entry is not None
    assert entry.proof_body == "aesop"


def test_empty_cache_returns_empty_stats(tmp_path: Path):
    cache = tmp_path / "cache.jsonl"
    stats = cache_stats(cache)
    assert stats["entries"] == 0
    assert stats["validated"] == 0


def test_missing_cache_lookup_returns_none(tmp_path: Path):
    cache = tmp_path / "definitely-does-not-exist.jsonl"
    assert lookup_cached_proof(
        lean_statement="theorem foo : P",
        method="whole_proof",
        cache_path=cache,
    ) is None
