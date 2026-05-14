"""Tests for audit_paper_theory_olean_health.

Hermetic: every test stubs the `lake build` invocation so we never shell
to the real Lean toolchain. Real `lake build` execution belongs in the
@pytest.mark.slow tier; these unit tests pin the wiring and the
status-classification invariants.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from audit_paper_theory_olean_health import (
    AuditSummary,
    ModuleHealth,
    audit_paper_theory,
    build_one_module,
    _enumerate_modules,
    _module_for_path,
    _olean_for_module,
    _paper_id_from_module,
    _summary_to_dict,
    write_summary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _make_fake_runner(
    *,
    by_module: dict[str, _FakeProc] | None = None,
    default: _FakeProc | None = None,
    raise_for: dict[str, Exception] | None = None,
):
    """Build a runner stub that maps `lake build <module>` to a fake process
    or raises. The module name is the last positional in the command list."""
    by_module = by_module or {}
    raise_for = raise_for or {}
    default = default or _FakeProc(returncode=0, stdout="ok\n")

    def runner(cmd, **kwargs):  # noqa: ARG001 — kwargs are subprocess-shaped
        module = cmd[-1]
        if module in raise_for:
            raise raise_for[module]
        return by_module.get(module, default)

    return runner


def _build_layout(tmp: Path, *, modules: list[str], repair_modules: list[str] | None = None) -> None:
    """Lay out a fake `Desol/PaperTheory/Paper_*.lean` tree under `tmp`."""
    base = tmp / "Desol" / "PaperTheory"
    base.mkdir(parents=True, exist_ok=True)
    for m in modules:
        (base / f"{m}.lean").write_text("-- stub\n", encoding="utf-8")
    if repair_modules:
        rep = base / "Repair"
        rep.mkdir(parents=True, exist_ok=True)
        for m in repair_modules:
            (rep / f"{m}.lean").write_text("-- repair stub\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Module name / paper id derivation
# ---------------------------------------------------------------------------


def test_module_for_path_translates_relative_paths(tmp_path: Path) -> None:
    p = tmp_path / "Desol" / "PaperTheory" / "Paper_2604_21583.lean"
    p.parent.mkdir(parents=True)
    p.write_text("", encoding="utf-8")
    mod = _module_for_path(p, project_root=tmp_path)
    assert mod == "Desol.PaperTheory.Paper_2604_21583"


def test_module_for_path_handles_repair_subdir(tmp_path: Path) -> None:
    p = tmp_path / "Desol" / "PaperTheory" / "Repair" / "Paper_2604_21314.lean"
    p.parent.mkdir(parents=True)
    p.write_text("", encoding="utf-8")
    assert _module_for_path(p, project_root=tmp_path) == "Desol.PaperTheory.Repair.Paper_2604_21314"


def test_paper_id_from_module_recovers_dot_form() -> None:
    assert _paper_id_from_module("Desol.PaperTheory.Paper_2604_21583") == "2604.21583"
    assert _paper_id_from_module("Desol.PaperTheory.Repair.Paper_2012_09271") == "2012.09271"


def test_paper_id_from_module_empty_when_unrecognized() -> None:
    assert _paper_id_from_module("Desol.PaperTheory.SomethingElse") == ""
    assert _paper_id_from_module("Foo") == ""


def test_olean_for_module_uses_lake_layout(tmp_path: Path) -> None:
    olean = _olean_for_module(tmp_path, "Desol.PaperTheory.Paper_2604_21583")
    assert olean == tmp_path / ".lake" / "build" / "lib" / "lean" / "Desol" / "PaperTheory" / "Paper_2604_21583.olean"


# ---------------------------------------------------------------------------
# Enumeration: top-level first, then Repair
# ---------------------------------------------------------------------------


def test_enumerate_orders_top_level_before_repair(tmp_path: Path) -> None:
    _build_layout(
        tmp_path,
        modules=["Paper_2604_21314", "Paper_2604_21583"],
        repair_modules=["Paper_2604_21314"],
    )
    modules = _enumerate_modules(tmp_path)
    names = [m for m, _ in modules]
    assert names == [
        "Desol.PaperTheory.Paper_2604_21314",
        "Desol.PaperTheory.Paper_2604_21583",
        "Desol.PaperTheory.Repair.Paper_2604_21314",
    ]


def test_enumerate_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert _enumerate_modules(tmp_path) == []


# ---------------------------------------------------------------------------
# build_one_module: status classification
# ---------------------------------------------------------------------------


def test_build_one_module_returncode_zero_is_ok(tmp_path: Path) -> None:
    _build_layout(tmp_path, modules=["Paper_2604_21583"])
    src = tmp_path / "Desol/PaperTheory/Paper_2604_21583.lean"
    runner = _make_fake_runner(default=_FakeProc(returncode=0, stdout="built\n"))
    h = build_one_module(
        project_root=tmp_path,
        module="Desol.PaperTheory.Paper_2604_21583",
        source_path=src,
        runner=runner,
    )
    assert h.status == "ok"
    assert h.returncode == 0
    assert h.paper_id == "2604.21583"


def test_build_one_module_nonzero_returncode_is_fail(tmp_path: Path) -> None:
    _build_layout(tmp_path, modules=["Paper_2604_21314"])
    src = tmp_path / "Desol/PaperTheory/Paper_2604_21314.lean"
    runner = _make_fake_runner(default=_FakeProc(returncode=1, stderr="elaboration error\n"))
    h = build_one_module(
        project_root=tmp_path,
        module="Desol.PaperTheory.Paper_2604_21314",
        source_path=src,
        runner=runner,
    )
    assert h.status == "fail"
    assert h.returncode == 1
    assert "elaboration error" in h.output_tail


def test_build_one_module_timeout_is_timed_out(tmp_path: Path) -> None:
    _build_layout(tmp_path, modules=["Paper_X_99"])
    src = tmp_path / "Desol/PaperTheory/Paper_X_99.lean"

    def runner(cmd, **kwargs):  # noqa: ARG001
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 1))

    h = build_one_module(
        project_root=tmp_path,
        module="Desol.PaperTheory.Paper_X_99",
        source_path=src,
        runner=runner,
        timeout_s=1,
    )
    assert h.status == "timed_out"
    assert "timeout_after_1s" in h.output_tail


# ---------------------------------------------------------------------------
# audit_paper_theory: top-level orchestrator
# ---------------------------------------------------------------------------


def test_audit_classifies_mixed_outcomes(tmp_path: Path) -> None:
    _build_layout(
        tmp_path,
        modules=["Paper_2604_21314", "Paper_2604_21583", "Paper_2304_09598"],
        repair_modules=["Paper_2604_21314"],
    )
    runner = _make_fake_runner(
        by_module={
            "Desol.PaperTheory.Paper_2604_21314": _FakeProc(returncode=0),
            "Desol.PaperTheory.Paper_2604_21583": _FakeProc(returncode=1, stderr="unknown identifier `foo`"),
            "Desol.PaperTheory.Paper_2304_09598": _FakeProc(returncode=0),
            "Desol.PaperTheory.Repair.Paper_2604_21314": _FakeProc(returncode=0),
        },
    )
    summary = audit_paper_theory(project_root=tmp_path, runner=runner)
    statuses = {m.module: m.status for m in summary.modules}
    assert statuses["Desol.PaperTheory.Paper_2604_21314"] == "ok"
    assert statuses["Desol.PaperTheory.Paper_2604_21583"] == "fail"
    assert statuses["Desol.PaperTheory.Paper_2304_09598"] == "ok"
    assert statuses["Desol.PaperTheory.Repair.Paper_2604_21314"] == "ok"

    totals = summary.totals()
    assert totals == {"ok": 3, "fail": 1, "timed_out": 0, "not_attempted": 0, "total": 4}
    failing = summary.failing()
    assert [m.module for m in failing] == ["Desol.PaperTheory.Paper_2604_21583"]


def test_audit_papers_filter_marks_others_not_attempted(tmp_path: Path) -> None:
    _build_layout(
        tmp_path,
        modules=["Paper_2604_21314", "Paper_2604_21583"],
    )
    seen: list[str] = []

    def runner(cmd, **kwargs):  # noqa: ARG001
        seen.append(cmd[-1])
        return _FakeProc(returncode=0)

    summary = audit_paper_theory(
        project_root=tmp_path, runner=runner, papers=["2604.21583"],
    )
    statuses = {m.module: m.status for m in summary.modules}
    assert statuses["Desol.PaperTheory.Paper_2604_21314"] == "not_attempted"
    assert statuses["Desol.PaperTheory.Paper_2604_21583"] == "ok"
    # We must not invoke lake on the filtered-out module.
    assert seen == ["Desol.PaperTheory.Paper_2604_21583"]


def test_summary_to_dict_round_trips(tmp_path: Path) -> None:
    _build_layout(tmp_path, modules=["Paper_2604_21583"])
    runner = _make_fake_runner(default=_FakeProc(returncode=0))
    summary = audit_paper_theory(project_root=tmp_path, runner=runner)
    d = _summary_to_dict(summary)
    assert d["schema_version"] == "paper_theory_olean_health.v1"
    assert d["totals"]["ok"] == 1
    assert d["failing_modules"] == []
    assert d["modules"][0]["module"] == "Desol.PaperTheory.Paper_2604_21583"


# ---------------------------------------------------------------------------
# write_summary
# ---------------------------------------------------------------------------


def test_write_summary_produces_valid_json(tmp_path: Path) -> None:
    _build_layout(tmp_path, modules=["Paper_2604_21583"])
    runner = _make_fake_runner(default=_FakeProc(returncode=1, stderr="x"))
    summary = audit_paper_theory(project_root=tmp_path, runner=runner)
    out_path = tmp_path / "audits" / "summary.json"
    write_summary(summary, out_path=out_path)
    raw = json.loads(out_path.read_text(encoding="utf-8"))
    assert raw["totals"]["fail"] == 1
    assert "Desol.PaperTheory.Paper_2604_21583" in raw["failing_modules"]


# ---------------------------------------------------------------------------
# Publish-time wiring (mirrors the FP-integrity wire test for commit 8eba181)
# ---------------------------------------------------------------------------


def test_publish_bundle_records_paper_theory_olean_health(monkeypatch, tmp_path: Path) -> None:
    """`_publish_reproducibility_bundle` must record the paper-theory olean
    health audit in the manifest, mirroring the FP-integrity audit pattern.
    We stub the audit_paper_theory call so the test is hermetic (no lake)."""
    from formalize_paper_full import _publish_reproducibility_bundle

    paper_id = "2604.21583"
    project = tmp_path
    (project / "output").mkdir()
    (project / "reproducibility" / "full_paper_reports").mkdir(parents=True)
    lean_file = project / "output" / f"{paper_id}.lean"
    lean_file.write_text("theorem t : True := trivial\n", encoding="utf-8")
    ledger_path = project / "output" / f"{paper_id}.json"
    ledger_path.write_text("[]", encoding="utf-8")
    report_out = project / "output" / "report.json"
    report_out.write_text("{}", encoding="utf-8")
    unresolved_out = project / "output" / "unresolved.json"
    unresolved_out.write_text("[]", encoding="utf-8")

    # Lay out the paper-theory module so the auditor sees it.
    _build_layout(project, modules=["Paper_2604_21583"])

    # Stub audit_paper_theory to a known summary so we don't shell to lake.
    import audit_paper_theory_olean_health as audit_mod

    def stub_audit(*, project_root, papers=None, **kwargs):  # noqa: ARG001
        s = AuditSummary()
        s.modules.append(
            ModuleHealth(
                module="Desol.PaperTheory.Paper_2604_21583",
                source_path=str(project / "Desol/PaperTheory/Paper_2604_21583.lean"),
                olean_path=str(project / ".lake/build/lib/lean/Desol/PaperTheory/Paper_2604_21583.olean"),
                olean_present=True,
                status="ok",
                paper_id="2604.21583",
            )
        )
        return s

    monkeypatch.setattr(audit_mod, "audit_paper_theory", stub_audit)

    paths = _publish_reproducibility_bundle(
        project_root=project,
        paper_id=paper_id,
        report_out=report_out,
        ledger_path=ledger_path,
        unresolved_out=unresolved_out,
    )
    manifest = json.loads(Path(paths["manifest"]).read_text(encoding="utf-8"))
    pt_health = manifest.get("paper_theory_olean_health")
    assert isinstance(pt_health, dict), "manifest must record paper_theory_olean_health"
    assert pt_health["totals"]["ok"] == 1
    assert pt_health["failing_modules"] == []
    modules = pt_health["modules"]
    assert len(modules) == 1
    assert modules[0]["module"] == "Desol.PaperTheory.Paper_2604_21583"
    assert modules[0]["status"] == "ok"


def test_publish_bundle_records_paper_theory_failure(monkeypatch, tmp_path: Path) -> None:
    """A failing module must surface as a failing_modules entry in the
    manifest so reviewers can see the gate caught it. Standards-positive:
    we record the failure rather than hide it."""
    from formalize_paper_full import _publish_reproducibility_bundle

    paper_id = "2604.21583"
    project = tmp_path
    (project / "output").mkdir()
    (project / "reproducibility" / "full_paper_reports").mkdir(parents=True)
    (project / "output" / f"{paper_id}.lean").write_text("theorem t : True := trivial\n", encoding="utf-8")
    ledger_path = project / "output" / f"{paper_id}.json"
    ledger_path.write_text("[]", encoding="utf-8")
    report_out = project / "output" / "report.json"
    report_out.write_text("{}", encoding="utf-8")
    unresolved_out = project / "output" / "unresolved.json"
    unresolved_out.write_text("[]", encoding="utf-8")

    _build_layout(project, modules=["Paper_2604_21583"])

    import audit_paper_theory_olean_health as audit_mod

    def stub_audit(*, project_root, papers=None, **kwargs):  # noqa: ARG001
        s = AuditSummary()
        s.modules.append(
            ModuleHealth(
                module="Desol.PaperTheory.Paper_2604_21583",
                source_path="",
                olean_path="",
                olean_present=False,
                status="fail",
                returncode=1,
                output_tail="unknown identifier `xPHpProjector`",
                paper_id="2604.21583",
            )
        )
        return s

    monkeypatch.setattr(audit_mod, "audit_paper_theory", stub_audit)

    paths = _publish_reproducibility_bundle(
        project_root=project,
        paper_id=paper_id,
        report_out=report_out,
        ledger_path=ledger_path,
        unresolved_out=unresolved_out,
    )
    manifest = json.loads(Path(paths["manifest"]).read_text(encoding="utf-8"))
    pt_health = manifest["paper_theory_olean_health"]
    assert pt_health["totals"]["fail"] == 1
    assert "Desol.PaperTheory.Paper_2604_21583" in pt_health["failing_modules"]
