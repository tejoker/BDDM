from __future__ import annotations

import json
from pathlib import Path

from paper_theory_builder import paper_theory_import_header, plan_paper_theory, write_paper_theory


def test_paper_theory_builder_writes_importable_module(tmp_path: Path) -> None:
    (tmp_path / "Desol").mkdir()
    plan = plan_paper_theory(
        paper_id="2604.21884",
        domain="probability",
        seed_text="HSobolev C_T I_i ξ1 Ψ1 Γ1 theta cutoff_solution paracontrolled_solution",
    )
    out = write_paper_theory(project_root=tmp_path, plan=plan)
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert f"namespace {plan.module_name}" in text
    assert "modulo any paper-local axioms" in text
    assert "not hidden proofs of paper claims" in text
    assert "def HSobolev (_s : ℝ) : Set (ℝ → ℝ) := Set.univ" in text
    assert "def C_T : Set (ℝ → ℝ) := Set.univ" in text
    assert "def I_i (x : ℝ) : ℝ := x" in text
    assert "def ξ1 : ℝ := 0" in text
    assert "def theta : ℝ := 0" in text
    assert "export Paper_2604_21884" in text
    for name in ("HSobolev", "C_T", "I_i", "ξ1", "Ψ1", "Γ1", "theta", "cutoff_solution", "paracontrolled_solution"):
        assert name in text
    manifest = json.loads(out.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "1.1.0"
    assert manifest["grounding_policy"]["proof_countable"] is False
    assert manifest["symbols"][0]["lean"] == "HSobolev"
    assert manifest["symbols"][0]["grounding"] == "definition_stub"
    assert manifest["symbols"][0]["proof_countable"] is False


def test_paper_theory_import_header_injects_module_before_proof_search() -> None:
    header = paper_theory_import_header("import Mathlib\nimport Aesop", module_name="Paper_2604_21884")

    assert "import Desol.PaperTheory.Paper_2604_21884" in header
    assert "open Paper_2604_21884" in header

