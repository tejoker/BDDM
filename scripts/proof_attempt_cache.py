#!/usr/bin/env python3
"""Statement-hash cache for proof attempts.

Across sweep rounds (V/VI/VII/VIII/X), the pipeline attempts overlapping
statements with different methods. Each attempt is a fresh Mistral call.
Caching `(statement_hash → tried_proofs → outcomes)` saves repeat spend
on retries — enables MORE attempts within the same budget.

The cache is keyed by SHA-256 of the canonicalized lean_statement
(whitespace-normalized, lowercased) AND a short tag of the attempt
method (e.g. `whole_proof`, `lemma_factor`, `repl`). Different methods
produce different proofs even on the same statement, so each method
gets its own cache namespace.

The cache stores:
  - proof_body: the proof text the LLM returned
  - validated: True/False (passed lake-in-context check)
  - timestamp: when stored
  - method: short tag
  - error_tail: lake error if not validated (≤300 chars)

Cache file: `data/proof_attempt_cache.jsonl` (gitignored).

Standards-positive: a cached "validated" entry still passes through
the integrity audit when applied. The cache is an OPTIMIZATION
(avoid duplicate Mistral spend), not a trust layer.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_CACHE_PATH = Path("data/proof_attempt_cache.jsonl")


@dataclass
class CacheEntry:
    statement_hash: str
    method: str
    proof_body: str
    validated: bool
    timestamp: float
    error_tail: str = ""


def canonicalize_statement(lean_statement: str) -> str:
    """Whitespace-normalize and lowercase. Strip leading `:= by sorry`
    so cache hits across signatures with and without the body."""
    s = (lean_statement or "").strip()
    # Drop the proof body — only the signature shape matters for cache.
    s = re.sub(r":=\s*by\b.*$", "", s, flags=re.DOTALL).strip()
    s = re.sub(r":=\s*$", "", s).strip()
    # Collapse runs of whitespace.
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def statement_hash(lean_statement: str) -> str:
    """SHA-256 of the canonicalized form. Same shape → same hash regardless
    of whitespace / case / trailing body."""
    canonical = canonicalize_statement(lean_statement)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def lookup_cached_proof(
    *,
    lean_statement: str,
    method: str,
    cache_path: Path = DEFAULT_CACHE_PATH,
) -> CacheEntry | None:
    """Return the most-recent cached entry for (statement, method) if any."""
    if not cache_path.exists():
        return None
    h = statement_hash(lean_statement)
    best: CacheEntry | None = None
    for line in cache_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("statement_hash") != h:
            continue
        if str(row.get("method", "")) != method:
            continue
        ts = float(row.get("timestamp", 0.0))
        if best is None or ts > best.timestamp:
            best = CacheEntry(
                statement_hash=h,
                method=method,
                proof_body=str(row.get("proof_body", "") or ""),
                validated=bool(row.get("validated", False)),
                timestamp=ts,
                error_tail=str(row.get("error_tail", "") or "")[:300],
            )
    return best


def record_proof_attempt(
    *,
    lean_statement: str,
    method: str,
    proof_body: str,
    validated: bool,
    error_tail: str = "",
    cache_path: Path = DEFAULT_CACHE_PATH,
) -> None:
    """Append a new attempt record to the JSONL cache.

    Never overwrites; later entries supersede earlier ones at lookup time
    via timestamp ordering. Drops empty (statement, method) records.
    """
    if not lean_statement.strip() or not method.strip():
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "statement_hash": statement_hash(lean_statement),
        "method": method,
        "proof_body": proof_body,
        "validated": bool(validated),
        "timestamp": time.time(),
        "error_tail": (error_tail or "")[:300],
    }
    with cache_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def cache_stats(cache_path: Path = DEFAULT_CACHE_PATH) -> dict[str, Any]:
    """Lightweight stats for telemetry."""
    if not cache_path.exists():
        return {"entries": 0, "validated": 0, "rejected": 0, "methods": {}}
    n = 0
    n_valid = 0
    methods: dict[str, int] = {}
    seen_hashes: set[str] = set()
    for line in cache_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        n += 1
        if row.get("validated"):
            n_valid += 1
        m = str(row.get("method", "") or "unknown")
        methods[m] = methods.get(m, 0) + 1
        h = str(row.get("statement_hash", "") or "")
        if h:
            seen_hashes.add(h)
    return {
        "entries": n,
        "validated": n_valid,
        "rejected": n - n_valid,
        "methods": methods,
        "unique_statements": len(seen_hashes),
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-path", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument("--stats", action="store_true", help="Print cache stats and exit")
    parser.add_argument("--lookup", help="Look up a statement (pass the lean_statement)")
    parser.add_argument("--method", default="whole_proof", help="Method tag for --lookup")
    args = parser.parse_args()

    if args.stats:
        print(json.dumps(cache_stats(args.cache_path), indent=2))
        return 0
    if args.lookup:
        entry = lookup_cached_proof(
            lean_statement=args.lookup,
            method=args.method,
            cache_path=args.cache_path,
        )
        if entry is None:
            print("no cached entry")
            return 1
        print(json.dumps({
            "statement_hash": entry.statement_hash,
            "method": entry.method,
            "validated": entry.validated,
            "proof_body": entry.proof_body[:200],
            "error_tail": entry.error_tail[:200],
            "timestamp": entry.timestamp,
        }, indent=2))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
