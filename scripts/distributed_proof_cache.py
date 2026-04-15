#!/usr/bin/env python3
"""Distributed proof cache for cross-worker reuse.

Stores theorem-level proof attempt results in sqlite (WAL mode) so worker
threads/processes can reuse prior results safely.

Features:
  - Schema versioning to invalidate stale format changes
  - TTL (time-to-live) expiration to prevent unbounded growth
  - Automatic cleanup of expired entries
  - Corruption recovery with index rebuild
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

# Schema version: increment when cache entry format changes
_CACHE_SCHEMA_VERSION = 2


class DistributedProofCache:
    def __init__(self, db_path: str | Path, ttl_seconds: int = 86400 * 30, version: int = _CACHE_SCHEMA_VERSION):
        """
        Args:
            db_path: Path to SQLite database
            ttl_seconds: Time-to-live for cache entries in seconds (default: 30 days)
            version: Schema version for cache invalidation
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self.version = version
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path), timeout=30.0)
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        return con

    def _init_db(self) -> None:
        """Initialize schema with version tracking and TTL support."""
        with self._connect() as con:
            # Metadata table for schema versioning
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            
            # Create or upgrade proof cache table
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS proof_attempt_cache (
                    cache_key TEXT PRIMARY KEY,
                    created_ts REAL NOT NULL,
                    updated_ts REAL NOT NULL,
                    version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            
            # Add version column if it doesn't exist (migration from v1)
            try:
                con.execute("SELECT version FROM proof_attempt_cache LIMIT 1")
            except sqlite3.OperationalError:
                con.execute("ALTER TABLE proof_attempt_cache ADD COLUMN version INTEGER DEFAULT 1")
            
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_proof_cache_updated ON proof_attempt_cache(updated_ts)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_proof_cache_version ON proof_attempt_cache(version)"
            )
            
            # Store current schema version
            con.execute(
                """
                INSERT OR REPLACE INTO cache_metadata (key, value)
                VALUES ('schema_version', ?)
                """,
                (str(self.version),),
            )
            
            # Cleanup: remove expired entries
            self._cleanup_expired(con)

    @staticmethod
    def build_key(*, theorem_statement: str, mode: str, model: str, retrieval_top_k: int) -> str:
        payload = {
            "theorem_statement": theorem_statement.strip(),
            "mode": mode,
            "model": model,
            "retrieval_top_k": int(retrieval_top_k),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _cleanup_expired(self, con: sqlite3.Connection) -> None:
        """Remove entries older than TTL and entries with mismatched version."""
        cutoff_ts = time.time() - self.ttl_seconds
        con.execute(
            """
            DELETE FROM proof_attempt_cache
            WHERE updated_ts < ? OR version != ?
            """,
            (cutoff_ts, self.version),
        )

    def _rebuild_indexes(self) -> None:
        """Rebuild indexes after potential corruption."""
        try:
            with self._connect() as con:
                con.execute("REINDEX;")
        except Exception:
            pass  # Silently ignore reindex failure

    def get(self, cache_key: str) -> dict[str, Any] | None:
        """Get cached entry if it exists and hasn't expired."""
        with self._lock:
            with self._connect() as con:
                row = con.execute(
                    """
                    SELECT payload_json, version, updated_ts
                    FROM proof_attempt_cache
                    WHERE cache_key = ?
                    """,
                    (cache_key,),
                ).fetchone()
        
        if row is None:
            return None
        
        payload_json, entry_version, updated_ts = row
        
        # Check version mismatch
        if entry_version != self.version:
            return None
        
        # Check TTL expiration
        if time.time() - updated_ts > self.ttl_seconds:
            return None
        
        try:
            return json.loads(payload_json)
        except ValueError:
            return None

    def set(self, cache_key: str, payload: dict[str, Any]) -> None:
        """Store cache entry with version and timestamp."""
        now = time.time()
        blob = json.dumps(payload, ensure_ascii=True)
        with self._lock:
            with self._connect() as con:
                con.execute(
                    """
                    INSERT INTO proof_attempt_cache(cache_key, created_ts, updated_ts, version, payload_json)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        updated_ts=excluded.updated_ts,
                        version=excluded.version,
                        payload_json=excluded.payload_json
                    """,
                    (cache_key, now, now, self.version, blob),
                )

    def stats(self) -> dict[str, Any]:
        """Return cache statistics including version and TTL info."""
        with self._lock:
            with self._connect() as con:
                # Total entries
                row = con.execute(
                    "SELECT COUNT(*), MIN(created_ts), MAX(updated_ts) FROM proof_attempt_cache"
                ).fetchone()
                
                # Breakdown by version
                version_row = con.execute(
                    """
                    SELECT version, COUNT(*)
                    FROM proof_attempt_cache
                    GROUP BY version
                    """
                ).fetchall()
        
        total = int(row[0] or 0)
        version_breakdown = {str(v): count for v, count in version_row}
        
        return {
            "entries": total,
            "oldest_ts": row[1],
            "newest_ts": row[2],
            "db_path": str(self.db_path),
            "current_version": self.version,
            "version_breakdown": version_breakdown,
            "ttl_seconds": self.ttl_seconds,
        }

    def clear_expired(self) -> int:
        """Manually trigger cleanup of expired entries. Returns count deleted."""
        with self._lock:
            with self._connect() as con:
                cursor = con.execute(
                    "DELETE FROM proof_attempt_cache WHERE updated_ts < ? OR version != ?",
                    (time.time() - self.ttl_seconds, self.version),
                )
                return cursor.rowcount
