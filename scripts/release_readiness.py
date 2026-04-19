#!/usr/bin/env python3
"""Lightweight release-readiness checks for DESol."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}")


def _ok(msg: str) -> None:
    print(f"[OK] {msg}")


def check_lakefile_pins(repo_root: Path) -> bool:
    lakefile = repo_root / "lakefile.toml"
    if not lakefile.exists():
        _fail("lakefile.toml missing")
        return False
    text = lakefile.read_text(encoding="utf-8")
    bad = re.findall(r'rev\s*=\s*"(master|main)"', text)
    if bad:
        _fail("lakefile.toml contains floating dependency rev(s): master/main")
        return False
    _ok("lakefile.toml dependencies are pinned (no master/main)")
    return True


def check_toolchain(repo_root: Path) -> bool:
    tc = repo_root / "lean-toolchain"
    if not tc.exists():
        _fail("lean-toolchain missing")
        return False
    value = tc.read_text(encoding="utf-8").strip()
    if not value:
        _fail("lean-toolchain is empty")
        return False
    _ok(f"lean-toolchain pinned: {value}")
    return True


def check_benchmark_artifact_schema(repo_root: Path) -> bool:
    artifact = repo_root / "reproducibility" / "minif2f_test_244_results.json"
    if not artifact.exists():
        _fail("missing reproducibility/minif2f_test_244_results.json")
        return False
    data = json.loads(artifact.read_text(encoding="utf-8"))
    required = [
        "schema_version",
        "pass_at_1",
        "k",
        "n_problems",
        "model",
        "mode",
        "retrieval_top_k",
        "lean_timeout_s",
        "retrieval_index",
        "git_commit",
        "lean_version",
        "python_version",
    ]
    missing = [k for k in required if k not in data or data.get(k) in (None, "")]
    if missing:
        _fail(f"benchmark artifact missing fields: {missing}")
        return False
    _ok("benchmark artifact includes key reproducibility fields")
    return True


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    checks = [
        check_lakefile_pins(repo_root),
        check_toolchain(repo_root),
        check_benchmark_artifact_schema(repo_root),
    ]
    if all(checks):
        print("[PASS] release readiness baseline checks passed")
        return 0
    print("[FAIL] release readiness baseline checks failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
