"""Unit tests for proof backend scaffold (phase-1 migration)."""
from __future__ import annotations

import json
import importlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

try:
    _pb = importlib.import_module("proof_backend")
except ModuleNotFoundError:
    _pb = importlib.import_module("scripts.proof_backend")

ProofBackendFlags = _pb.ProofBackendFlags
BackendStartupSummary = _pb.BackendStartupSummary
build_backend_startup_summary = _pb.build_backend_startup_summary
build_backend_health_report = _pb.build_backend_health_report
classify_backend_init_error = _pb.classify_backend_init_error
emit_backend_parity_event = _pb.emit_backend_parity_event
format_backend_startup_summary = _pb.format_backend_startup_summary
load_proof_backend_flags = _pb.load_proof_backend_flags
resolve_backend_choice = _pb.resolve_backend_choice


def test_load_flags_defaults(monkeypatch):
    monkeypatch.delenv("DESOL_BACKEND_PHASE1", raising=False)
    monkeypatch.delenv("DESOL_PROOF_BACKEND", raising=False)
    monkeypatch.delenv("DESOL_FORCE_REPL_DOJO", raising=False)
    monkeypatch.delenv("DESOL_BACKEND_PARITY_LOG", raising=False)
    monkeypatch.delenv("DESOL_BACKEND_PARITY_LOG_PATH", raising=False)
    monkeypatch.delenv("DESOL_BACKEND_PARITY_RUN_ID", raising=False)

    flags = load_proof_backend_flags()

    assert flags.phase1_enabled is False
    assert flags.backend_mode == "auto"
    assert flags.force_repl_dojo is False
    assert flags.parity_log_enabled is False
    assert flags.parity_log_path == Path("output/reports/proof_backend_parity.jsonl")
    assert flags.parity_run_id


def test_load_flags_invalid_mode_falls_back_to_auto():
    flags = load_proof_backend_flags(
        {
            "DESOL_BACKEND_PHASE1": "1",
            "DESOL_PROOF_BACKEND": "bad-mode",
            "DESOL_BACKEND_PARITY_LOG": "1",
            "DESOL_BACKEND_PARITY_RUN_ID": "run-x",
        }
    )

    assert flags.phase1_enabled is True
    assert flags.backend_mode == "auto"
    assert flags.parity_log_enabled is True
    assert flags.parity_run_id == "run-x"


@pytest.mark.parametrize(
    "leandojo_available,force_repl,expected",
    [
        (True, False, "leandojo"),
        (False, False, "repldojo"),
        (True, True, "repldojo"),
        (False, True, "repldojo"),
    ],
)
def test_resolve_choice_legacy_behavior_when_phase1_disabled(leandojo_available, force_repl, expected):
    flags = ProofBackendFlags(
        phase1_enabled=False,
        backend_mode="auto",
        force_repl_dojo=force_repl,
        parity_log_enabled=False,
        parity_log_path=Path("unused.jsonl"),
        parity_run_id="test",
    )

    assert resolve_backend_choice(leandojo_available=leandojo_available, flags=flags) == expected


def test_resolve_choice_phase1_auto_prefers_leandojo_then_repl():
    flags = ProofBackendFlags(
        phase1_enabled=True,
        backend_mode="auto",
        force_repl_dojo=False,
        parity_log_enabled=False,
        parity_log_path=Path("unused.jsonl"),
        parity_run_id="test",
    )

    assert resolve_backend_choice(leandojo_available=True, flags=flags) == "leandojo"
    assert resolve_backend_choice(leandojo_available=False, flags=flags) == "repldojo"


def test_resolve_choice_phase1_explicit_repldojo():
    flags = ProofBackendFlags(
        phase1_enabled=True,
        backend_mode="repldojo",
        force_repl_dojo=False,
        parity_log_enabled=False,
        parity_log_path=Path("unused.jsonl"),
        parity_run_id="test",
    )

    assert resolve_backend_choice(leandojo_available=True, flags=flags) == "repldojo"


def test_resolve_choice_phase1_explicit_leandojo_requires_availability():
    flags = ProofBackendFlags(
        phase1_enabled=True,
        backend_mode="leandojo",
        force_repl_dojo=False,
        parity_log_enabled=False,
        parity_log_path=Path("unused.jsonl"),
        parity_run_id="test",
    )

    assert resolve_backend_choice(leandojo_available=True, flags=flags) == "leandojo"
    with pytest.raises(RuntimeError):
        resolve_backend_choice(leandojo_available=False, flags=flags)


def test_emit_parity_event_writes_jsonl(tmp_path):
    log_path = tmp_path / "parity.jsonl"
    flags = ProofBackendFlags(
        phase1_enabled=True,
        backend_mode="auto",
        force_repl_dojo=False,
        parity_log_enabled=True,
        parity_log_path=log_path,
        parity_run_id="run-123",
    )

    emit_backend_parity_event(flags, "backend-open-start", {"backend": "leandojo", "x": 1})

    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["run_id"] == "run-123"
    assert row["event"] == "backend-open-start"
    assert row["payload"] == {"backend": "leandojo", "x": 1}
    assert "ts" in row and isinstance(row["ts"], str)


def test_emit_parity_event_disabled_does_not_write(tmp_path):
    log_path = tmp_path / "parity.jsonl"
    flags = ProofBackendFlags(
        phase1_enabled=True,
        backend_mode="auto",
        force_repl_dojo=False,
        parity_log_enabled=False,
        parity_log_path=log_path,
        parity_run_id="run-123",
    )

    emit_backend_parity_event(flags, "preflight-start", {"backend": "repldojo"})

    assert not log_path.exists()


@pytest.mark.parametrize(
    "msg,expected_code",
    [
        ("ExtractData.lean broke on newer lake", "LEANDOJO_EXTRACTDATA_COMPAT"),
        ("Lean version mismatch: expected v4.15 got v4.18", "LEAN_VERSION_MISMATCH"),
        ("git clone failed with code 128 https unreachable", "TOOLCHAIN_FETCH_FAILURE"),
        ("lake executable not found", "LAKE_NOT_FOUND"),
    ],
)
def test_classify_backend_init_error(msg, expected_code):
    code, _ = classify_backend_init_error(msg)
    assert code == expected_code


def test_build_backend_health_report_ok_and_fail():
    ok_report = build_backend_health_report(backend="repldojo")
    assert ok_report.ok is True
    assert ok_report.error_code == "NONE"

    fail_report = build_backend_health_report(
        backend="leandojo",
        error_text="Lean version mismatch on toolchain",
    )
    assert fail_report.ok is False
    assert fail_report.error_code == "LEAN_VERSION_MISMATCH"
    assert "Align lean-toolchain" in fail_report.recommendation


def test_format_backend_startup_summary_lines():
    summary = BackendStartupSummary(
        backend_requested="auto",
        backend_resolved="leandojo",
        phase1_enabled=True,
        leandojo_available=True,
        repo_lean_toolchain="leanprover/lean4:v4.15.0",
        runtime_lean_version="Lean (version 4.18.0)",
        extractdata_patch_status="unpatched",
        hints=("toolchain mismatch: repo=4.15.0 runtime=4.18.0; align Lean/Lake versions",),
    )

    lines = format_backend_startup_summary(summary)
    assert any("[startup] backend=" in line for line in lines)
    assert any("extractdata_patch_status=unpatched" in line for line in lines)
    assert any("[startup][hint]" in line for line in lines)


def test_build_backend_startup_summary_when_leandojo_missing(tmp_path):
    (tmp_path / "lean-toolchain").write_text("leanprover/lean4:v4.15.0\n", encoding="utf-8")
    flags = ProofBackendFlags(
        phase1_enabled=True,
        backend_mode="leandojo",
        force_repl_dojo=False,
        parity_log_enabled=False,
        parity_log_path=Path("unused.jsonl"),
        parity_run_id="test",
    )

    summary = build_backend_startup_summary(
        project_root=tmp_path,
        flags=flags,
        leandojo_available=False,
    )

    assert summary.backend_resolved == "resolve-error"
    assert summary.extractdata_patch_status in {"unavailable", "missing", "patched", "unpatched", "unknown"}
    assert any("lean_dojo unavailable" in hint for hint in summary.hints)
