#!/usr/bin/env python3
"""Phase-1 proof backend scaffold: flags, interface, parity logging."""

from __future__ import annotations

import json
import importlib
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol
from uuid import uuid4


_VALID_BACKEND_MODES = {"auto", "leandojo", "repldojo"}


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ProofBackendFlags:
    phase1_enabled: bool
    backend_mode: str
    force_repl_dojo: bool
    parity_log_enabled: bool
    parity_log_path: Path
    parity_run_id: str


@dataclass(frozen=True)
class LeanDojoOpenRequest:
    project_root: Path
    file_path: Path
    theorem_name: str
    dojo_timeout: int


@dataclass(frozen=True)
class BackendHealthReport:
    ok: bool
    backend: str
    error_code: str
    message: str
    recommendation: str


@dataclass(frozen=True)
class BackendStartupSummary:
    backend_requested: str
    backend_resolved: str
    phase1_enabled: bool
    leandojo_available: bool
    repo_lean_toolchain: str
    runtime_lean_version: str
    extractdata_patch_status: str
    hints: tuple[str, ...]


class LeanDojoClient(Protocol):
    """Minimal LeanDojo client interface for backend migration."""

    def open_dojo(self, request: LeanDojoOpenRequest) -> tuple[Any, Path | None]:
        ...


class DefaultLeanDojoClient:
    """Thin adapter around existing open function to keep behavior unchanged."""

    def __init__(self, open_fn: Any):
        self._open_fn = open_fn

    def open_dojo(self, request: LeanDojoOpenRequest) -> tuple[Any, Path | None]:
        return self._open_fn(request)


def classify_backend_init_error(error_text: str) -> tuple[str, str]:
    text = (error_text or "").lower()

    if any(token in text for token in ("extractdata", "extract data")):
        return (
            "LEANDOJO_EXTRACTDATA_COMPAT",
            "Apply ExtractData compatibility patch for current Lean/Lake toolchain.",
        )

    if (
        ("lean" in text or "toolchain" in text or "lake" in text)
        and any(token in text for token in ("version", "mismatch", "expected", "incompatible", "unsupported"))
    ):
        return (
            "LEAN_VERSION_MISMATCH",
            "Align lean-toolchain and LeanDojo traced artifacts to same Lean/Lake versions.",
        )

    if any(token in text for token in ("git", "clone", "network", "https", "unreachable", "code 128")):
        return (
            "TOOLCHAIN_FETCH_FAILURE",
            "Ensure git network access and prefetch required mathlib/toolchain dependencies.",
        )

    if "lake" in text and any(token in text for token in ("not found", "no such file", "executable")):
        return (
            "LAKE_NOT_FOUND",
            "Install Lean toolchain and ensure lake is available in PATH.",
        )

    return (
        "BACKEND_INIT_FAILED",
        "Inspect backend initialization logs and retry with --backend-health-check.",
    )


def build_backend_health_report(*, backend: str, error_text: str | None = None) -> BackendHealthReport:
    if not error_text:
        return BackendHealthReport(
            ok=True,
            backend=backend,
            error_code="NONE",
            message=f"{backend} backend health check passed",
            recommendation="",
        )

    error_code, recommendation = classify_backend_init_error(error_text)
    return BackendHealthReport(
        ok=False,
        backend=backend,
        error_code=error_code,
        message=error_text.strip() or "backend initialization failed",
        recommendation=recommendation,
    )


def load_proof_backend_flags(env: Mapping[str, str] | None = None) -> ProofBackendFlags:
    source = env or os.environ
    phase1_enabled = _is_truthy(source.get("DESOL_BACKEND_PHASE1"))
    mode = source.get("DESOL_PROOF_BACKEND", "auto").strip().lower() or "auto"
    if mode not in _VALID_BACKEND_MODES:
        mode = "auto"

    parity_log_enabled = _is_truthy(source.get("DESOL_BACKEND_PARITY_LOG"))
    parity_log_path = Path(
        source.get("DESOL_BACKEND_PARITY_LOG_PATH", "output/reports/proof_backend_parity.jsonl")
    )
    parity_run_id = source.get("DESOL_BACKEND_PARITY_RUN_ID", "").strip() or str(uuid4())

    return ProofBackendFlags(
        phase1_enabled=phase1_enabled,
        backend_mode=mode,
        force_repl_dojo=_is_truthy(source.get("DESOL_FORCE_REPL_DOJO")),
        parity_log_enabled=parity_log_enabled,
        parity_log_path=parity_log_path,
        parity_run_id=parity_run_id,
    )


def _read_repo_toolchain(project_root: Path) -> str:
    toolchain_file = project_root / "lean-toolchain"
    if not toolchain_file.exists():
        return "unknown"
    try:
        return toolchain_file.read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


def _read_runtime_lean_version(project_root: Path) -> str:
    try:
        proc = subprocess.run(
            ["lake", "env", "lean", "--version"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if proc.returncode != 0:
        return "unknown"
    out = (proc.stdout or proc.stderr or "").strip()
    if not out:
        return "unknown"
    first = out.splitlines()[0].strip()
    return first or "unknown"


def _extract_semver_token(text: str) -> str:
    match = re.search(r"v?\d+\.\d+\.\d+", text or "")
    return match.group(0).lstrip("v") if match else ""


def probe_leandojo_importability() -> tuple[bool, str]:
    """Return (available, error_text)."""
    try:
        importlib.import_module("lean_dojo")
        return True, ""
    except ImportError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"unexpected lean_dojo import error: {exc}"


def detect_extractdata_patch_status() -> str:
    """Return one of: unavailable, missing, patched, unpatched, unknown."""
    try:
        import lean_dojo.data_extraction.trace as trace_mod
    except ImportError:
        return "unavailable"
    except Exception:
        return "unknown"

    extract_path = Path(trace_mod.__file__).resolve().parent / "ExtractData.lean"
    if not extract_path.exists():
        return "missing"

    try:
        text = extract_path.read_text(encoding="utf-8")
    except Exception:
        return "unknown"

    header_new = "def getImports (header: Syntax) : IO String := do"
    header_old = "def getImports (header: TSyntax `Lean.Parser.Module.header) : IO String := do"
    lake_new = "let oleanPath1? := Path.toBuildDir \"lib/lean\" relativePath \"olean\""

    if header_new in text and lake_new in text:
        return "patched"
    if header_old in text:
        return "unpatched"
    return "unknown"


def build_backend_startup_summary(
    *,
    project_root: Path,
    flags: ProofBackendFlags,
    leandojo_available: bool,
    leandojo_import_error: str = "",
) -> BackendStartupSummary:
    resolve_error = ""
    try:
        resolved = resolve_backend_choice(leandojo_available=leandojo_available, flags=flags)
    except Exception as exc:
        resolved = "resolve-error"
        resolve_error = str(exc)

    repo_toolchain = _read_repo_toolchain(project_root)
    runtime_version = _read_runtime_lean_version(project_root)
    extractdata_status = detect_extractdata_patch_status()

    hints: list[str] = []
    repo_semver = _extract_semver_token(repo_toolchain)
    runtime_semver = _extract_semver_token(runtime_version)
    if repo_semver and runtime_semver and repo_semver != runtime_semver:
        hints.append(
            f"toolchain mismatch: repo={repo_semver} runtime={runtime_semver}; align Lean/Lake versions"
        )

    if resolved == "leandojo" and extractdata_status in {"unpatched", "missing"}:
        hints.append("ExtractData compatibility patch required for current LeanDojo toolchain")
    if (flags.backend_mode == "leandojo" or resolved == "leandojo") and not leandojo_available:
        hints.append("lean_dojo unavailable; install lean_dojo or switch backend to repldojo")
    if leandojo_import_error:
        hints.append(f"lean_dojo import error: {leandojo_import_error}")
    if resolve_error:
        hints.append(f"backend resolve error: {resolve_error}")

    return BackendStartupSummary(
        backend_requested=flags.backend_mode,
        backend_resolved=resolved,
        phase1_enabled=flags.phase1_enabled,
        leandojo_available=leandojo_available,
        repo_lean_toolchain=repo_toolchain,
        runtime_lean_version=runtime_version,
        extractdata_patch_status=extractdata_status,
        hints=tuple(hints),
    )


def format_backend_startup_summary(summary: BackendStartupSummary) -> list[str]:
    lines = [
        (
            "[startup] backend="
            f"{summary.backend_resolved} "
            f"requested={summary.backend_requested} "
            f"phase1={int(summary.phase1_enabled)} "
            f"leandojo_available={int(summary.leandojo_available)}"
        ),
        (
            "[startup] toolchain="
            f"repo='{summary.repo_lean_toolchain}' "
            f"runtime='{summary.runtime_lean_version}'"
        ),
        f"[startup] extractdata_patch_status={summary.extractdata_patch_status}",
    ]
    for hint in summary.hints:
        lines.append(f"[startup][hint] {hint}")
    return lines


def resolve_backend_choice(*, leandojo_available: bool, flags: ProofBackendFlags) -> str:
    """Resolve backend while preserving legacy behavior when phase-1 disabled."""
    legacy_choice = "repldojo" if flags.force_repl_dojo or not leandojo_available else "leandojo"
    if not flags.phase1_enabled:
        return legacy_choice

    if flags.force_repl_dojo:
        return "repldojo"

    if flags.backend_mode == "leandojo":
        if not leandojo_available:
            raise RuntimeError("DESOL_PROOF_BACKEND=leandojo requested but lean_dojo is unavailable")
        return "leandojo"

    if flags.backend_mode == "repldojo":
        return "repldojo"

    return "leandojo" if leandojo_available else "repldojo"


def emit_backend_parity_event(flags: ProofBackendFlags, event: str, payload: Mapping[str, Any]) -> None:
    """Append one JSONL parity event; best-effort only."""
    if not flags.parity_log_enabled:
        return

    try:
        log_path = flags.parity_log_path
        if not log_path.is_absolute():
            log_path = Path.cwd() / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": flags.parity_run_id,
            "event": event,
            "payload": dict(payload),
        }
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")
    except Exception:
        # Parity logging must not affect proof execution.
        return
