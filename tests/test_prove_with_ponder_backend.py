"""Integration tests for prove_with_ponder backend opening/parity logging."""
from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def test_open_dojo_emits_parity_events_repldojo(tmp_path, monkeypatch):
    try:
        pwp = importlib.import_module("prove_with_ponder")
    except ModuleNotFoundError:
        pwp = importlib.import_module("scripts.prove_with_ponder")

    class FakeREPLDojo:
        def __init__(self, *, project_root, file_path, theorem_name, timeout):
            self.project_root = project_root
            self.file_path = file_path
            self.theorem_name = theorem_name
            self.timeout = timeout

        def __enter__(self):
            return self, types.SimpleNamespace(pp="⊢ True", num_goals=1)

        def __exit__(self, *args):
            return False

    fake_module = types.ModuleType("lean_repl_dojo")
    fake_module.REPLDojo = FakeREPLDojo
    monkeypatch.setitem(sys.modules, "lean_repl_dojo", fake_module)

    parity_path = tmp_path / "parity.jsonl"
    monkeypatch.setenv("DESOL_BACKEND_PHASE1", "1")
    monkeypatch.setenv("DESOL_PROOF_BACKEND", "repldojo")
    monkeypatch.setenv("DESOL_BACKEND_PARITY_LOG", "1")
    monkeypatch.setenv("DESOL_BACKEND_PARITY_LOG_PATH", str(parity_path))
    monkeypatch.setenv("DESOL_BACKEND_PARITY_RUN_ID", "it-open-dojo")

    dojo_ctx, tmp_root = pwp._open_dojo(
        project_root=Path("."),
        file_path=Path("Desol/Basic.lean"),
        theorem_name="demo_theorem",
        dojo_timeout=30,
    )

    assert isinstance(dojo_ctx, FakeREPLDojo)
    assert tmp_root is None

    rows = [json.loads(line) for line in parity_path.read_text(encoding="utf-8").splitlines()]
    events = [row["event"] for row in rows]

    assert "backend-open-start" in events
    assert "backend-open-success" in events
    assert all(row["run_id"] == "it-open-dojo" for row in rows)


def test_check_backend_health_classifies_version_mismatch(monkeypatch):
    try:
        pwp = importlib.import_module("prove_with_ponder")
    except ModuleNotFoundError:
        pwp = importlib.import_module("scripts.prove_with_ponder")

    def _raise(*args, **kwargs):
        raise RuntimeError("Lean version mismatch: expected v4.15 got v4.18")

    monkeypatch.setattr(pwp, "_open_dojo", _raise)
    monkeypatch.setenv("DESOL_BACKEND_PHASE1", "1")
    monkeypatch.setenv("DESOL_PROOF_BACKEND", "repldojo")

    report = pwp.check_backend_health(
        project_root=Path("."),
        file_path=Path("Desol/Basic.lean"),
        theorem_name="demo_theorem",
        dojo_timeout=30,
    )

    assert report.ok is False
    assert report.error_code == "LEAN_VERSION_MISMATCH"
    assert "Align lean-toolchain" in report.recommendation
