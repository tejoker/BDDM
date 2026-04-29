#!/usr/bin/env python3
"""Shared release/audit metadata helpers for DESol corpus artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


CORPUS_RELEASE_SCHEMA_VERSION = "1.1.0"

DEFAULT_PROVENANCE_POLICY = {
    "source_traceability": "Rows must retain arXiv paper id and artifact paths; theorem span fields are required when extraction provides them.",
    "license_note": "arXiv source text remains attributed to the source paper; DESol-generated Lean and audit metadata are derived artifacts.",
    "trust_tiers": "Gold, silver, diagnostic, and blocker labels must not be collapsed into one training tier.",
}


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def relpath(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_text(cmd: list[str], *, cwd: Path, timeout_s: int = 5) -> str:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except Exception:
        return "unknown"
    out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    if proc.returncode == 0 and out:
        return out.splitlines()[0].strip()
    return "unknown"


def git_commit(project_root: Path) -> str:
    return _run_text(["git", "rev-parse", "HEAD"], cwd=project_root)


def git_dirty(project_root: Path) -> bool | None:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return bool((proc.stdout or "").strip())


def lean_toolchain(project_root: Path) -> str:
    path = project_root / "lean-toolchain"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def lean_version(project_root: Path) -> str:
    return _run_text(["lean", "--version"], cwd=project_root)


def lake_dependencies(project_root: Path) -> list[dict[str, str]]:
    path = project_root / "lakefile.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    deps: list[dict[str, str]] = []
    for block in re.split(r"(?m)^\s*\[\[require\]\]\s*$", text)[1:]:
        dep: dict[str, str] = {}
        for key in ("name", "git", "rev", "path"):
            match = re.search(rf'(?m)^\s*{key}\s*=\s*"([^"]*)"', block)
            if match:
                dep[key] = match.group(1)
        if dep.get("name"):
            deps.append(dep)
    return deps


def schema_version_for_file(path: Path) -> str:
    if not path.exists() or not path.is_file() or path.suffix not in {".json"}:
        return ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if isinstance(raw, dict):
        return str(raw.get("schema_version", "") or raw.get("corpus_release_schema_version", ""))
    return ""


def artifact_entry(
    root: Path,
    role: str,
    path: Path,
    *,
    paper_id: str = "",
    required: bool = False,
) -> dict[str, Any]:
    exists = path.exists()
    row: dict[str, Any] = {
        "role": role,
        "path": relpath(root, path),
        "exists": exists,
        "required": bool(required),
    }
    if paper_id:
        row["paper_id"] = paper_id
    if exists and path.is_file():
        row["size_bytes"] = path.stat().st_size
        row["sha256"] = sha256_file(path)
        schema_version = schema_version_for_file(path)
        if schema_version:
            row["schema_version"] = schema_version
    return row


def artifact_summary(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    existing = [row for row in artifacts if row.get("exists")]
    required = [row for row in artifacts if row.get("required")]
    missing_required = [row for row in required if not row.get("exists")]
    hashed_existing = [row for row in existing if row.get("sha256")]
    return {
        "artifact_count": len(artifacts),
        "existing_artifact_count": len(existing),
        "required_artifact_count": len(required),
        "missing_required_count": len(missing_required),
        "checksum_count": len(hashed_existing),
        "checksum_coverage": (len(hashed_existing) / len(existing)) if existing else 1.0,
    }


def build_release_audit(
    *,
    project_root: Path,
    generated_at: str | None = None,
    command: list[str] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    provenance_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    deps = lake_dependencies(project_root)
    mathlib = next((dep for dep in deps if dep.get("name") == "mathlib"), {})
    audit = {
        "schema_version": CORPUS_RELEASE_SCHEMA_VERSION,
        "generated_at": generated_at or utc_now(),
        "command": command or [],
        "repository": {
            "git_commit": git_commit(project_root),
            "dirty_tree": git_dirty(project_root),
        },
        "toolchain": {
            "lean_toolchain": lean_toolchain(project_root),
            "lean_version": lean_version(project_root),
            "python_version": sys.version.split()[0],
            "lake_dependencies": deps,
            "mathlib": mathlib,
        },
        "environment": {
            "platform": sys.platform,
            "mistral_model": os.environ.get("MISTRAL_MODEL", "unknown"),
        },
        "provenance_policy": provenance_policy or DEFAULT_PROVENANCE_POLICY,
    }
    if artifacts is not None:
        audit["artifact_summary"] = artifact_summary(artifacts)
    return audit


def validate_release_manifest(manifest: dict[str, Any], *, project_root: Path) -> list[str]:
    errors: list[str] = []
    audit = manifest.get("release_audit")
    if not isinstance(audit, dict):
        return ["missing release_audit block"]

    if str(audit.get("schema_version", "")) != CORPUS_RELEASE_SCHEMA_VERSION:
        errors.append("release_audit.schema_version mismatch")

    repo = audit.get("repository", {})
    recorded_commit = str(repo.get("git_commit", "")).strip() if isinstance(repo, dict) else ""
    if not recorded_commit or recorded_commit == "unknown":
        errors.append("release_audit.repository.git_commit missing")

    toolchain = audit.get("toolchain", {})
    expected_toolchain = lean_toolchain(project_root)
    recorded_toolchain = str(toolchain.get("lean_toolchain", "")).strip() if isinstance(toolchain, dict) else ""
    if recorded_toolchain != expected_toolchain:
        errors.append("release_audit.toolchain.lean_toolchain mismatch")
    mathlib = toolchain.get("mathlib", {}) if isinstance(toolchain, dict) else {}
    if not isinstance(mathlib, dict) or not str(mathlib.get("rev", "")).strip():
        errors.append("release_audit.toolchain.mathlib.rev missing")

    policy = audit.get("provenance_policy", {})
    if not isinstance(policy, dict):
        errors.append("release_audit.provenance_policy missing")
    else:
        for key in ("source_traceability", "license_note", "trust_tiers"):
            if not str(policy.get(key, "")).strip():
                errors.append(f"release_audit.provenance_policy.{key} missing")

    artifacts = manifest.get("artifacts", [])
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("artifacts inventory missing")
    else:
        for idx, row in enumerate(artifacts):
            if not isinstance(row, dict):
                errors.append(f"artifacts[{idx}] is not an object")
                continue
            role = str(row.get("role", f"#{idx}"))
            if row.get("exists") and not str(row.get("sha256", "")).strip():
                errors.append(f"artifact {role} missing sha256")
            if row.get("required") and not row.get("exists"):
                errors.append(f"required artifact {role} missing")

    return errors
