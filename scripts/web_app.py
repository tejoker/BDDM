#!/usr/bin/env python3
"""Local web UI for LaTeX → Lean pipeline (upload .tex or .zip, poll job, download .lean, lake build).

API keys stay server-side only (MISTRAL_API_KEY in environment / .env).

Run from repo root:
  source ~/miniconda3/etc/profile.d/conda.sh && conda activate desol-py311
  python scripts/web_app.py

Then open http://127.0.0.1:8765
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import threading
import uuid
import zipfile
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

load_dotenv(PROJECT_ROOT / ".env")

STATIC_DIR = PROJECT_ROOT / "web" / "static"
WEB_UPLOAD_DIR = PROJECT_ROOT / "Desol" / "WebUpload"


def _lean_module_name(project_root: Path, lean_file: Path) -> str:
    rel = lean_file.resolve().relative_to(project_root.resolve())
    return ".".join(rel.with_suffix("").parts)


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    dest = dest.resolve()
    for info in zf.infolist():
        if info.is_dir() or info.filename.endswith("/"):
            continue
        out_path = (dest / info.filename).resolve()
        try:
            out_path.relative_to(dest)
        except ValueError as exc:
            raise ValueError(f"Unsafe zip entry: {info.filename!r}") from exc
    zf.extractall(dest)


def prepare_tex_sources(upload_path: Path, work_dir: Path) -> tuple[list[Path], str]:
    """Mirror arxiv layout: directory of .tex files, then find_main_tex in pipeline."""
    src = work_dir / "source"
    src.mkdir(parents=True, exist_ok=True)
    name = upload_path.name

    if upload_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(upload_path) as zf:
            _safe_extract_zip(zf, src)
        tex_paths = sorted(src.rglob("*.tex"))
        if not tex_paths:
            raise ValueError("Zip archive contains no .tex files")
        return tex_paths, name

    if upload_path.suffix.lower() == ".tex":
        dest = src / upload_path.name
        shutil.copy2(upload_path, dest)
        return [dest], name

    raise ValueError("Upload must be a .tex or .zip file")


def run_lake_build(project_root: Path, lean_file: Path, timeout: int = 3600) -> tuple[int, str, str]:
    module = _lean_module_name(project_root, lean_file)
    env = os.environ.copy()
    proc = subprocess.run(
        ["lake", "build", module],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


# --- job store (in-process; OK for single-user local use) ---

_jobs_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


def _run_job(
    job_id: str,
    upload_path: Path,
    *,
    max_theorems: int,
    translate_only: bool,
    repair_rounds: int,
    parallel_theorems: int,
    retrieval_index: str,
    retrieval_top_k: int,
    temperature: float,
    dojo_timeout: int,
    api_rate: float,
) -> None:
    from arxiv_to_lean import (
        _PROOF_IMPORTS,
        _RateLimiter,
        pipeline_results_to_json,
        run_pipeline,
    )

    try:
        from mistralai import Mistral
    except ImportError:
        from mistralai.client import Mistral  # type: ignore[no-redef]

    work_dir = PROJECT_ROOT / "web" / "work" / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    with _jobs_lock:
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["progress"] = "Preparing sources…"

    log = io.StringIO()

    def progress_hook(msg: str) -> None:
        with _jobs_lock:
            _jobs[job_id]["progress"] = msg

    try:
        tex_paths, upload_name = prepare_tex_sources(upload_path, work_dir)
    except Exception as exc:
        with _jobs_lock:
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = str(exc)
        return

    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        with _jobs_lock:
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = "MISTRAL_API_KEY is not set (server environment)"
        return

    model = os.getenv("MISTRAL_MODEL", "labs-leanstral-2603").strip()
    client = Mistral(api_key=api_key)
    WEB_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    out_lean = WEB_UPLOAD_DIR / f"{job_id}.lean"

    kinds = {"theorem", "lemma", "proposition", "corollary"}
    imports = _PROOF_IMPORTS.strip()

    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        try:
            results = run_pipeline(
                paper_id="local",
                source_label=f"upload:{upload_name}",
                project_root=PROJECT_ROOT,
                out_lean=out_lean,
                work_dir=work_dir,
                client=client,
                model=model,
                kinds=kinds,
                max_theorems=max_theorems,
                translate_only=translate_only,
                repair_rounds=repair_rounds,
                retrieval_index_path=retrieval_index,
                retrieval_top_k=retrieval_top_k,
                imports=imports,
                temperature=temperature,
                dojo_timeout=dojo_timeout,
                parallel_theorems=parallel_theorems,
                rate_limiter=_RateLimiter(rate=api_rate),
                local_tex_paths=tex_paths,
                progress_hook=progress_hook,
            )
        except Exception as exc:
            with _jobs_lock:
                _jobs[job_id]["status"] = "failed"
                _jobs[job_id]["error"] = str(exc)
                _jobs[job_id]["full_log"] = log.getvalue()
            return

    structured = pipeline_results_to_json(results)
    full_log = log.getvalue()

    lake_exit: int | None = None
    lake_out = ""
    lake_err = ""
    if out_lean.is_file():
        with _jobs_lock:
            _jobs[job_id]["progress"] = "Running lake build…"
        try:
            lake_exit, lake_out, lake_err = run_lake_build(PROJECT_ROOT, out_lean)
        except subprocess.TimeoutExpired:
            lake_exit = -1
            lake_out = ""
            lake_err = "lake build timed out"
        except Exception as exc:
            lake_exit = -1
            lake_err = str(exc)
    else:
        lake_err = "No generated .lean file (e.g. no theorem environments in source)."

    with _jobs_lock:
        _jobs[job_id]["status"] = "completed"
        _jobs[job_id]["structured"] = structured
        _jobs[job_id]["full_log"] = full_log
        _jobs[job_id]["out_lean"] = (
            str(out_lean.relative_to(PROJECT_ROOT)) if out_lean.is_file() else None
        )
        _jobs[job_id]["lake_exit_code"] = lake_exit
        _jobs[job_id]["lake_stdout"] = lake_out
        _jobs[job_id]["lake_stderr"] = lake_err
        _jobs[job_id]["progress"] = "Done"


def create_app():
    from fastapi import FastAPI, File, Form, HTTPException, UploadFile
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="DESol local pipeline")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/jobs")
    async def submit_job(
        file: UploadFile = File(...),
        max_theorems: int = Form(0),
        translate_only: bool = Form(False),
        repair_rounds: int = Form(5),
        parallel_theorems: int = Form(2),
        retrieval_index: str = Form("data/mathlib_embeddings"),
        retrieval_top_k: int = Form(12),
        temperature: float = Form(0.2),
        dojo_timeout: int = Form(600),
        api_rate: float = Form(4.0),
    ) -> JSONResponse:
        if not file.filename:
            raise HTTPException(400, "No filename")

        suffix = Path(file.filename).suffix.lower()
        if suffix not in {".tex", ".zip"}:
            raise HTTPException(400, "Upload a .tex or .zip file")

        job_id = str(uuid.uuid4())
        upload_dir = PROJECT_ROOT / "web" / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        save_path = upload_dir / f"{job_id}{suffix}"
        body = await file.read()
        save_path.write_bytes(body)

        with _jobs_lock:
            _jobs[job_id] = {
                "id": job_id,
                "status": "queued",
                "progress": "Queued",
                "filename": file.filename,
                "structured": None,
                "full_log": "",
                "error": None,
                "out_lean": None,
                "lake_exit_code": None,
                "lake_stdout": "",
                "lake_stderr": "",
            }

        def _run() -> None:
            try:
                _run_job(
                    job_id,
                    save_path,
                    max_theorems=max_theorems,
                    translate_only=translate_only,
                    repair_rounds=repair_rounds,
                    parallel_theorems=parallel_theorems,
                    retrieval_index=retrieval_index,
                    retrieval_top_k=retrieval_top_k,
                    temperature=temperature,
                    dojo_timeout=dojo_timeout,
                    api_rate=api_rate,
                )
            finally:
                save_path.unlink(missing_ok=True)

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        return JSONResponse({"job_id": job_id})

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict[str, Any]:
        with _jobs_lock:
            if job_id not in _jobs:
                raise HTTPException(404, "Unknown job")
            j = dict(_jobs[job_id])
        return j

    @app.get("/api/jobs/{job_id}/lean")
    def download_lean(job_id: str) -> FileResponse:
        with _jobs_lock:
            if job_id not in _jobs:
                raise HTTPException(404, "Unknown job")
            status = _jobs[job_id]["status"]
        if status != "completed":
            raise HTTPException(400, "Job not completed")
        path = WEB_UPLOAD_DIR / f"{job_id}.lean"
        if not path.is_file():
            raise HTTPException(404, "Lean file missing")
        return FileResponse(
            path,
            filename=f"desol_{job_id}.lean",
            media_type="text/plain",
        )

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


app = create_app()


def main() -> int:
    try:
        import uvicorn
    except ImportError:
        print("Install uvicorn: pip install uvicorn", file=sys.stderr)
        return 1

    port = int(os.getenv("DESOL_WEB_PORT", "8765"))
    uvicorn.run(app, host="127.0.0.1", port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
