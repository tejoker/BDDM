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
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import JSONResponse
except ImportError as exc:
    raise SystemExit(f"fastapi not installed: {exc}\n  pip install fastapi uvicorn") from exc

from kg_writer import query_kg

# Validate arXiv paper ID format: YYYY.NNNNN (e.g., 2304.09598)
# P0 mitigation: Prevent path traversal + injection via paper_id parameter
PAPER_ID_PATTERN = re.compile(r"^\d{4}\.\d{5}$")

app = FastAPI(
    title="DESol KG API",
    description="Query the DESol Knowledge Graph of verified Lean 4 theorems.",
    version="0.1.0",
)

_KG_DB = Path(os.environ.get("DESOL_KG_DB", "output/kg/kg_index.db"))
_PROJECT_ROOT = Path(os.environ.get("DESOL_PROJECT_ROOT", "."))


@app.get("/health")
def health() -> dict[str, str]:
    db_ok = _KG_DB.exists()
    return {"status": "ok", "kg_db": str(_KG_DB), "kg_db_exists": str(db_ok)}


@app.get("/kg/query")
def kg_query(
    layer: str | None = Query(default=None, description="trusted | conditional | diagnostics"),
    paper_id: str | None = Query(default=None),
    status: str | None = Query(default=None, description="FULLY_PROVEN | INTERMEDIARY_PROVEN | …"),
    limit: int = Query(default=100, ge=1, le=2000),
) -> list[dict[str, Any]]:
    if not _KG_DB.exists():
        raise HTTPException(status_code=503, detail=f"KG database not found at {_KG_DB}")
    return query_kg(_KG_DB, layer=layer, paper_id=paper_id, status=status, limit=limit)


@app.get("/kg/paper/{paper_id}")
def kg_paper(paper_id: str) -> list[dict[str, Any]]:
    if not _KG_DB.exists():
        raise HTTPException(status_code=503, detail=f"KG database not found at {_KG_DB}")
    nodes = query_kg(_KG_DB, paper_id=paper_id, limit=2000)
    if not nodes:
        raise HTTPException(status_code=404, detail=f"No KG entries for paper {paper_id!r}")
    return nodes


@app.get("/kg/proof/{paper_id}/{theorem_name}")
def kg_proof(paper_id: str, theorem_name: str) -> dict[str, Any]:
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
def verify(paper_id: str = Query(..., description="arXiv paper ID (YYYY.NNNNN format, e.g. 2304.09598)")) -> dict[str, Any]:
    """Enqueue an arXiv paper for pipeline processing (non-blocking).

    Spawns ``arxiv_to_lean.py`` as a background subprocess and returns
    immediately.  Check ``/kg/paper/{paper_id}`` later for results.
    
    **P0 Security Mitigation**: Validates paper_id format to prevent path traversal.
    """
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

    proc = subprocess.Popen(
        [sys.executable, str(script), paper_id, "--project-root", str(_PROJECT_ROOT)],
        start_new_session=True,
    )
    return {
        "status": "queued",
        "paper_id": paper_id,
        "pid": proc.pid,
        "message": f"Pipeline started in background (pid={proc.pid}). Poll /kg/paper/{paper_id} for results.",
    }
