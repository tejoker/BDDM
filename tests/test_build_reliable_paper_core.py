from __future__ import annotations

import subprocess
from pathlib import Path

import build_reliable_paper_core as reliable_core
from build_reliable_paper_core import _artifact_reason, build_reliable_core


def test_artifact_reason_blocks_paper_symbols_and_relaxed_props() -> None:
    assert _artifact_reason("theorem t : f ∈ C_T := by\n  sorry").startswith("blocked_token")
    assert _artifact_reason("theorem t (h : Prop) : h := by\n  sorry") == "relaxed_prop_hypothesis"


def test_build_reliable_core_writes_only_deterministic_safe_theorems(tmp_path: Path) -> None:
    lean = tmp_path / "paper.lean"
    lean.write_text(
        "import Mathlib\n\n"
        "namespace ArxivPaper\n\n"
        "theorem good (p : Prop) (h : p) : p := by\n"
        "  sorry\n\n"
        "theorem bad_symbol : C_T = C_T := by\n"
        "  sorry\n\n"
        "end ArxivPaper\n",
        encoding="utf-8",
    )

    payload = build_reliable_core(
        project_root=tmp_path,
        paper_id="test.paper",
        lean_file=lean,
        timeout_s=8,
        max_theorems=10,
    )

    assert payload["ok"] is True
    assert payload["theorem_count"] == 1
    out = tmp_path / "Desol" / "PaperProofs" / "Auto" / "Paper_test_paper.lean"
    text = out.read_text(encoding="utf-8")
    assert "theorem auto_good" in text
    assert "exact h" in text
    assert "bad_symbol" not in text
    assert "sorry" not in text


def test_build_reliable_core_adds_audited_2604_admissible_definition(monkeypatch, tmp_path: Path) -> None:
    lean = tmp_path / "paper.lean"
    lean.write_text("import Mathlib\n\nnamespace ArxivPaper\n\nend ArxivPaper\n", encoding="utf-8")

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(reliable_core.subprocess, "run", fake_run)

    payload = build_reliable_core(
        project_root=tmp_path,
        paper_id="2604.21884",
        lean_file=lean,
        timeout_s=8,
        max_theorems=10,
        verify_output=True,
    )

    assert payload["ok"] is True
    assert payload["theorem_count"] == 2
    assert payload["independent_lean_verified"] is True
    assert payload["lean_verification"]["ok"] is True
    by_source = {item["source_theorem"]: item for item in payload["theorems"]}
    assert by_source["def_admissible"]["semantic_equivalence_verified"] is True
    assert by_source["def_admissible"]["supersedes_paper_axiom_debt"] is True
    assert by_source["remark_20"]["semantic_equivalence_verified"] is True
    assert by_source["remark_20"]["supersedes_paper_axiom_debt"] is True
    out = tmp_path / "Desol" / "PaperProofs" / "Auto" / "Paper_2604_21884.lean"
    text = out.read_text(encoding="utf-8")
    assert "theorem auto_def_admissible_iff" in text
    assert "theorem auto_remark_20_condition_roles_iff" in text
    assert "sorry" not in text
