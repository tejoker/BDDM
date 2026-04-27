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


def _warn(msg: str) -> None:
    print(f"[WARN] {msg}")


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


def check_claim_registry(repo_root: Path) -> bool:
    registry_path = repo_root / "reproducibility" / "claims_registry.json"
    if not registry_path.exists():
        _fail("missing reproducibility/claims_registry.json")
        return False

    try:
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception:
        _fail("invalid JSON in reproducibility/claims_registry.json")
        return False

    toolchain_path = repo_root / "lean-toolchain"
    current_toolchain = toolchain_path.read_text(encoding="utf-8").strip() if toolchain_path.exists() else ""
    if raw.get("current_repo_toolchain") != current_toolchain:
        _fail("claims registry current_repo_toolchain does not match lean-toolchain")
        return False

    claims = raw.get("claims", [])
    if not isinstance(claims, list):
        _fail("claims registry must contain a claims list")
        return False

    ok = True
    for claim in claims:
        if not isinstance(claim, dict):
            _fail("claims registry contains a non-object claim")
            ok = False
            continue
        claim_id = str(claim.get("id", "<missing>"))
        status = str(claim.get("status", ""))
        artifact_path = str(claim.get("artifact_path", "")).strip()
        if status == "current":
            if not artifact_path:
                _fail(f"current claim {claim_id} has no artifact_path")
                ok = False
                continue
            artifact = repo_root / artifact_path
            if not artifact.exists():
                _fail(f"current claim {claim_id} artifact missing: {artifact_path}")
                ok = False
                continue
            recorded_toolchain = str(claim.get("recorded_toolchain", "")).strip()
            if recorded_toolchain and recorded_toolchain != current_toolchain:
                _fail(f"current claim {claim_id} toolchain mismatch")
                ok = False
        elif status in {"historical", "unsupported"}:
            if not str(claim.get("reason_not_current", "")).strip():
                _fail(f"{status} claim {claim_id} must explain why it is not current")
                ok = False
        else:
            _fail(f"claim {claim_id} has invalid status: {status}")
            ok = False

    if ok:
        _ok("claims registry matches current toolchain policy")
    return ok


def check_weekly_release_gate(repo_root: Path) -> bool:
    weekly_dir = repo_root / "output" / "reports" / "weekly"
    if not weekly_dir.exists():
        _warn("weekly report directory missing; skipping generated runtime release gate")
        return True
    reports = sorted(weekly_dir.glob("weekly_report_*.json"))
    if not reports:
        _warn("no weekly_report_*.json found; skipping generated runtime release gate")
        return True
    latest = reports[-1]
    try:
        raw = json.loads(latest.read_text(encoding="utf-8"))
    except Exception:
        _fail(f"invalid JSON in {latest}")
        return False
    gate = raw.get("release_gate", {}) if isinstance(raw, dict) else {}
    if not isinstance(gate, dict):
        _fail("weekly report missing release_gate object")
        return False
    if not bool(gate.get("hard_slice_in_range_50_100", False)):
        _fail("weekly release gate: hard_slice_in_range_50_100=false")
        return False
    if "semantic_safe_yield_pass" in gate and not bool(gate.get("semantic_safe_yield_pass", False)):
        _fail("weekly release gate: semantic_safe_yield_pass=false")
        return False
    if "slot_coverage_pass" in gate and not bool(gate.get("slot_coverage_pass", False)):
        _fail("weekly release gate: slot_coverage_pass=false")
        return False
    _ok(
        "weekly release gate present "
        f"(go_for_controlled_release={bool(gate.get('go_for_controlled_release', False))})"
    )
    return True


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    checks = [
        check_lakefile_pins(repo_root),
        check_toolchain(repo_root),
        check_benchmark_artifact_schema(repo_root),
        check_claim_registry(repo_root),
        check_weekly_release_gate(repo_root),
    ]
    if all(checks):
        print("[PASS] release readiness baseline checks passed")
        return 0
    print("[FAIL] release readiness baseline checks failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
