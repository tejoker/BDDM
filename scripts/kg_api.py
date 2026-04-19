#!/usr/bin/env python3
"""Minimal FastAPI REST gateway for the DESol Knowledge Graph.

Endpoints
---------
GET /health
    Liveness check.

GET /kg/query
    Query KG nodes with optional filters.
    Query params: layer, paper_id, status, limit (default 500).

GET /kg/paper/{paper_id}
    All KG nodes for a single paper.

GET /kg/proof/{paper_id}/{theorem_name}
    Full node payload for a specific theorem.

POST /verify
    Trigger pipeline for a given arxiv paper_id (queues, does not block).

Usage
-----
    uvicorn kg_api:app --host 0.0.0.0 --port 8000

Environment variables
---------------------
    DESOL_KG_DB   Path to kg_index.db  (default: output/kg/kg_index.db)
    DESOL_PROJECT_ROOT  Lean project root (default: .)
"""

from __future__ import annotations

import os
import re
import sys
import time
import json
import logging
import threading
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent

# Validate arXiv paper ID format: YYYY.NNNNN (e.g., 2304.09598)
# P0 mitigation: Prevent path traversal + injection via paper_id parameter
PAPER_ID_PATTERN = re.compile(r"^\d{4}\.\d{5}$")

try:
    from fastapi import FastAPI, HTTPException, Query, Header, Request
except ModuleNotFoundError:
    FastAPI = None  # type: ignore[assignment]
    HTTPException = None  # type: ignore[assignment]
    Query = None  # type: ignore[assignment]
    Header = None  # type: ignore[assignment]
    Request = None  # type: ignore[assignment]

from kg_writer import query_kg, query_kg_edges, query_math_kg
try:
    from pipeline_orchestrator import PipelineOrchestrator
except Exception:
    PipelineOrchestrator = None  # type: ignore[assignment]

if FastAPI is None:
    app = None
else:
    app = FastAPI(
        title="DESol KG API",
        description="Query the DESol Knowledge Graph of verified Lean 4 theorems.",
        version="0.1.0",
    )

_KG_DB = Path(os.environ.get("DESOL_KG_DB", "output/kg/kg_index.db"))
_PROJECT_ROOT = Path(os.environ.get("DESOL_PROJECT_ROOT", "."))
_API_KEY = os.environ.get("DESOL_API_KEY", "").strip()
_RATE_LIMIT_PER_MIN = int(os.environ.get("DESOL_RATE_LIMIT_PER_MIN", "60"))
_VERIFY_MAX_INFLIGHT = int(os.environ.get("DESOL_VERIFY_MAX_INFLIGHT", "2"))
_VERIFY_TIMEOUT_S = int(os.environ.get("DESOL_VERIFY_SLOT_TIMEOUT_S", "1"))
_LOG_LEVEL = os.environ.get("DESOL_API_LOG_LEVEL", "INFO").upper()
_VERIFY_USE_ORCH = os.environ.get("DESOL_VERIFY_USE_ORCHESTRATOR", "0").strip().lower() in {"1", "true", "yes"}
_ORCH_ROOT = Path(os.environ.get("DESOL_ORCHESTRATOR_ROOT", "output/orchestrator"))
_REPORT_ROOT = Path(os.environ.get("DESOL_REPORT_ROOT", "output/reports/weekly"))

logger = logging.getLogger("desol.kg_api")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(_h)
logger.setLevel(getattr(logging, _LOG_LEVEL, logging.INFO))

_rate_lock = threading.Lock()
_rate_windows: dict[str, list[float]] = {}
_verify_sem = threading.BoundedSemaphore(value=max(1, _VERIFY_MAX_INFLIGHT))
_verify_counter_lock = threading.Lock()
_verify_inflight = 0


def _audit(event: str, **fields: Any) -> None:
    payload = {"event": event, "ts": round(time.time(), 3), **fields}
    logger.info("audit=%s", payload)


def _prune_window(ts_values: list[float], now: float) -> None:
    cutoff = now - 60.0
    while ts_values and ts_values[0] < cutoff:
        ts_values.pop(0)


def _check_rate_limit(client_key: str) -> tuple[bool, int]:
    now = time.time()
    with _rate_lock:
        bucket = _rate_windows.setdefault(client_key, [])
        _prune_window(bucket, now)
        if len(bucket) >= max(1, _RATE_LIMIT_PER_MIN):
            retry_after = int(max(1, 60 - (now - bucket[0])))
            return False, retry_after
        bucket.append(now)
    return True, 0


def _require_auth(api_key: str | None) -> None:
    if not _API_KEY:
        return
    if not api_key or api_key.strip() != _API_KEY:
        _audit("auth_failed")
        raise HTTPException(status_code=401, detail="Unauthorized")


def _client_bucket(request: Request, api_key: str | None) -> str:
    client_host = "unknown"
    if request.client is not None and request.client.host:
        client_host = request.client.host
    auth_marker = (api_key or "").strip()[:8] if api_key else "anon"
    return f"{client_host}:{auth_marker}"


def _latest_weekly_report() -> dict[str, Any]:
    if not _REPORT_ROOT.exists():
        return {}
    files = sorted(_REPORT_ROOT.glob("weekly_report_*.json"))
    if not files:
        return {}
    p = files[-1]
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


if app is not None:
    @app.get("/health")
    def health() -> dict[str, str]:
        db_ok = _KG_DB.exists()
        return {"status": "ok", "kg_db": str(_KG_DB), "kg_db_exists": str(db_ok)}


    @app.get("/kg/query")
    def kg_query(
        request: Request,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        layer: str | None = Query(default=None, description="trusted | conditional | diagnostics"),
        paper_id: str | None = Query(default=None),
        status: str | None = Query(default=None, description="FULLY_PROVEN | INTERMEDIARY_PROVEN | …"),
        limit: int = Query(default=100, ge=1, le=2000),
    ) -> list[dict[str, Any]]:
        _require_auth(x_api_key)
        key = _client_bucket(request, x_api_key)
        ok, retry_after = _check_rate_limit(key)
        if not ok:
            _audit("rate_limit", endpoint="/kg/query", client=key, retry_after_s=retry_after)
            raise HTTPException(status_code=429, detail=f"Rate limited. Retry in {retry_after}s")
        if not _KG_DB.exists():
            raise HTTPException(status_code=503, detail=f"KG database not found at {_KG_DB}")
        return query_kg(_KG_DB, layer=layer, paper_id=paper_id, status=status, limit=limit)


    @app.get("/kg/math/query")
    def kg_math_query(
        request: Request,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        layer: str | None = Query(default=None, description="trusted | conditional | diagnostics"),
        paper_id: str | None = Query(default=None),
        status: str | None = Query(default=None, description="FULLY_PROVEN | INTERMEDIARY_PROVEN | …"),
        limit: int = Query(default=100, ge=1, le=2000),
    ) -> list[dict[str, Any]]:
        """Clean public math graph view (no raw evidence payload)."""
        _require_auth(x_api_key)
        key = _client_bucket(request, x_api_key)
        ok, retry_after = _check_rate_limit(key)
        if not ok:
            _audit("rate_limit", endpoint="/kg/math/query", client=key, retry_after_s=retry_after)
            raise HTTPException(status_code=429, detail=f"Rate limited. Retry in {retry_after}s")
        if not _KG_DB.exists():
            raise HTTPException(status_code=503, detail=f"KG database not found at {_KG_DB}")
        return query_math_kg(_KG_DB, layer=layer, paper_id=paper_id, status=status, limit=limit)


    @app.get("/kg/paper/{paper_id}")
    def kg_paper(
        request: Request,
        paper_id: str,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> list[dict[str, Any]]:
        _require_auth(x_api_key)
        key = _client_bucket(request, x_api_key)
        ok, retry_after = _check_rate_limit(key)
        if not ok:
            _audit("rate_limit", endpoint="/kg/paper", client=key, retry_after_s=retry_after)
            raise HTTPException(status_code=429, detail=f"Rate limited. Retry in {retry_after}s")
        if not _KG_DB.exists():
            raise HTTPException(status_code=503, detail=f"KG database not found at {_KG_DB}")
        nodes = query_kg(_KG_DB, paper_id=paper_id, limit=2000)
        if not nodes:
            raise HTTPException(status_code=404, detail=f"No KG entries for paper {paper_id!r}")
        return nodes


    @app.get("/kg/math/paper/{paper_id}")
    def kg_math_paper(
        request: Request,
        paper_id: str,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> list[dict[str, Any]]:
        _require_auth(x_api_key)
        key = _client_bucket(request, x_api_key)
        ok, retry_after = _check_rate_limit(key)
        if not ok:
            _audit("rate_limit", endpoint="/kg/math/paper", client=key, retry_after_s=retry_after)
            raise HTTPException(status_code=429, detail=f"Rate limited. Retry in {retry_after}s")
        if not _KG_DB.exists():
            raise HTTPException(status_code=503, detail=f"KG database not found at {_KG_DB}")
        nodes = query_math_kg(_KG_DB, paper_id=paper_id, limit=2000)
        if not nodes:
            raise HTTPException(status_code=404, detail=f"No KG entries for paper {paper_id!r}")
        return nodes


    @app.get("/kg/math/edges")
    def kg_math_edges(
        request: Request,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        edge_type: str | None = Query(default=None, description="Optional edge type filter"),
        limit: int = Query(default=100, ge=1, le=5000),
    ) -> list[dict[str, str]]:
        _require_auth(x_api_key)
        key = _client_bucket(request, x_api_key)
        ok, retry_after = _check_rate_limit(key)
        if not ok:
            _audit("rate_limit", endpoint="/kg/math/edges", client=key, retry_after_s=retry_after)
            raise HTTPException(status_code=429, detail=f"Rate limited. Retry in {retry_after}s")
        if not _KG_DB.exists():
            raise HTTPException(status_code=503, detail=f"KG database not found at {_KG_DB}")
        return query_kg_edges(_KG_DB, edge_type=edge_type, limit=limit)


    @app.get("/evidence/query")
    def evidence_query(
        request: Request,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        layer: str | None = Query(default=None, description="trusted | conditional | diagnostics"),
        paper_id: str | None = Query(default=None),
        status: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=2000),
    ) -> list[dict[str, Any]]:
        """Internal evidence view (full node payload, raw proofs/provenance included)."""
        _require_auth(x_api_key)
        key = _client_bucket(request, x_api_key)
        ok, retry_after = _check_rate_limit(key)
        if not ok:
            _audit("rate_limit", endpoint="/evidence/query", client=key, retry_after_s=retry_after)
            raise HTTPException(status_code=429, detail=f"Rate limited. Retry in {retry_after}s")
        if not _KG_DB.exists():
            raise HTTPException(status_code=503, detail=f"KG database not found at {_KG_DB}")
        return query_kg(_KG_DB, layer=layer, paper_id=paper_id, status=status, limit=limit)


    @app.get("/evidence/paper/{paper_id}")
    def evidence_paper(
        request: Request,
        paper_id: str,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> list[dict[str, Any]]:
        _require_auth(x_api_key)
        key = _client_bucket(request, x_api_key)
        ok, retry_after = _check_rate_limit(key)
        if not ok:
            _audit("rate_limit", endpoint="/evidence/paper", client=key, retry_after_s=retry_after)
            raise HTTPException(status_code=429, detail=f"Rate limited. Retry in {retry_after}s")
        if not _KG_DB.exists():
            raise HTTPException(status_code=503, detail=f"KG database not found at {_KG_DB}")
        nodes = query_kg(_KG_DB, paper_id=paper_id, limit=5000)
        if not nodes:
            raise HTTPException(status_code=404, detail=f"No evidence entries for paper {paper_id!r}")
        return nodes


    @app.get("/evidence/edges")
    def evidence_edges(
        request: Request,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        edge_type: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=10000),
    ) -> list[dict[str, Any]]:
        _require_auth(x_api_key)
        key = _client_bucket(request, x_api_key)
        ok, retry_after = _check_rate_limit(key)
        if not ok:
            _audit("rate_limit", endpoint="/evidence/edges", client=key, retry_after_s=retry_after)
            raise HTTPException(status_code=429, detail=f"Rate limited. Retry in {retry_after}s")
        if not _KG_DB.exists():
            raise HTTPException(status_code=503, detail=f"KG database not found at {_KG_DB}")
        return query_kg_edges(_KG_DB, edge_type=edge_type, limit=limit)


    @app.get("/ops/dashboard")
    def ops_dashboard(
        request: Request,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> dict[str, Any]:
        _require_auth(x_api_key)
        key = _client_bucket(request, x_api_key)
        ok, retry_after = _check_rate_limit(key)
        if not ok:
            _audit("rate_limit", endpoint="/ops/dashboard", client=key, retry_after_s=retry_after)
            raise HTTPException(status_code=429, detail=f"Rate limited. Retry in {retry_after}s")
        queue = {}
        drift = {}
        if PipelineOrchestrator is not None:
            try:
                orch = PipelineOrchestrator(_ORCH_ROOT)
                queue = orch.queue_dashboard()
                drift = orch.compute_drift_alerts(window=200)
            except Exception as exc:
                queue = {"error": str(exc)}
        manifest_path = _KG_DB.parent / "manifests" / "promotion_manifest_all.json"
        manifest = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                manifest = {}
        return {
            "generated_at_unix": int(time.time()),
            "kg_db": str(_KG_DB),
            "kg_db_exists": _KG_DB.exists(),
            "queue": queue,
            "drift": drift,
            "latest_weekly_report": _latest_weekly_report(),
            "latest_manifest": manifest,
        }


    @app.get("/ops/queue")
    def ops_queue(
        request: Request,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> dict[str, Any]:
        _require_auth(x_api_key)
        key = _client_bucket(request, x_api_key)
        ok, retry_after = _check_rate_limit(key)
        if not ok:
            _audit("rate_limit", endpoint="/ops/queue", client=key, retry_after_s=retry_after)
            raise HTTPException(status_code=429, detail=f"Rate limited. Retry in {retry_after}s")
        if PipelineOrchestrator is None:
            return {"status": "unavailable"}
        orch = PipelineOrchestrator(_ORCH_ROOT)
        return orch.queue_dashboard()


    @app.get("/kg/proof/{paper_id}/{theorem_name}")
    def kg_proof(
        request: Request,
        paper_id: str,
        theorem_name: str,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> dict[str, Any]:
        _require_auth(x_api_key)
        key = _client_bucket(request, x_api_key)
        ok, retry_after = _check_rate_limit(key)
        if not ok:
            _audit("rate_limit", endpoint="/kg/proof", client=key, retry_after_s=retry_after)
            raise HTTPException(status_code=429, detail=f"Rate limited. Retry in {retry_after}s")
        if not _KG_DB.exists():
            raise HTTPException(status_code=503, detail=f"KG database not found at {_KG_DB}")
        nodes = query_kg(_KG_DB, paper_id=paper_id, limit=2000)
        for node in nodes:
            if node.get("theorem_name") == theorem_name:
                return node
        raise HTTPException(
            status_code=404,
            detail=f"Theorem {theorem_name!r} not found in paper {paper_id!r}",
        )


    @app.post("/verify")
    def verify(
        request: Request,
        paper_id: str = Query(..., description="arXiv paper ID (YYYY.NNNNN format, e.g. 2304.09598)"),
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    ) -> dict[str, Any]:
        """Enqueue an arXiv paper for pipeline processing (non-blocking).

    Spawns ``arxiv_to_lean.py`` as a background subprocess and returns
    immediately.  Check ``/kg/paper/{paper_id}`` later for results.
    
    **P0 Security Mitigation**: Validates paper_id format to prevent path traversal.
    """
        _require_auth(x_api_key)
        key = _client_bucket(request, x_api_key)
        ok, retry_after = _check_rate_limit(key)
        if not ok:
            _audit("rate_limit", endpoint="/verify", client=key, retry_after_s=retry_after)
            raise HTTPException(status_code=429, detail=f"Rate limited. Retry in {retry_after}s")

        # P0 mitigation: Validate paper_id format
        if not PAPER_ID_PATTERN.match(paper_id):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid paper ID format: {paper_id!r}. Expected YYYY.NNNNN (e.g. 2304.09598)"
            )

        import subprocess

        script = SCRIPT_DIR / "arxiv_to_lean.py"
        if not script.exists():
            raise HTTPException(status_code=500, detail="arxiv_to_lean.py not found")

        if _VERIFY_USE_ORCH and PipelineOrchestrator is not None:
            try:
                orch = PipelineOrchestrator(_ORCH_ROOT)
                queued = orch.enqueue(
                    paper_id=paper_id,
                    config={"project_root": str(_PROJECT_ROOT), "trigger": "kg_api.verify"},
                )
                _audit("verify_enqueued_orchestrator", paper_id=paper_id, client=key, queue_status=queued.get("status"))
                return {
                    "status": "queued",
                    "queue_status": queued.get("status", "queued"),
                    "paper_id": paper_id,
                    "message": "Enqueued in orchestrator. Start a worker/daemon to consume queue.",
                    "orchestrator_root": str(_ORCH_ROOT),
                }
            except Exception as exc:
                _audit("verify_orchestrator_failed", paper_id=paper_id, client=key, error=str(exc))

        acquired = _verify_sem.acquire(timeout=max(0, _VERIFY_TIMEOUT_S))
        if not acquired:
            _audit("verify_rejected_capacity", paper_id=paper_id, client=key)
            raise HTTPException(status_code=429, detail="Verify queue is at capacity. Retry later.")
        with _verify_counter_lock:
            global _verify_inflight
            _verify_inflight += 1

        proc = subprocess.Popen(
            [sys.executable, str(script), paper_id, "--project-root", str(_PROJECT_ROOT)],
            start_new_session=True,
        )
        _audit("verify_queued", paper_id=paper_id, pid=proc.pid, client=key, inflight=_verify_inflight)
        # Best-effort deferred release in a detached thread when process exits.
        def _release_when_done(p: subprocess.Popen[Any]) -> None:
            try:
                p.wait()
            finally:
                with _verify_counter_lock:
                    global _verify_inflight
                    _verify_inflight = max(0, _verify_inflight - 1)
                _verify_sem.release()
        t = threading.Thread(target=_release_when_done, args=(proc,), daemon=True)
        t.start()

        return {
            "status": "queued",
            "paper_id": paper_id,
            "pid": proc.pid,
            "inflight": _verify_inflight,
            "message": f"Pipeline started in background (pid={proc.pid}). Poll /kg/paper/{paper_id} for results.",
        }
