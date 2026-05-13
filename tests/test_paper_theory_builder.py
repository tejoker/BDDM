from __future__ import annotations

import json
from pathlib import Path

from paper_symbol_inventory import declaration_name
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


def test_declaration_name_preserves_prime_suffix() -> None:
    assert declaration_name("axiom Γ' : Prop") == "Γ'"
    assert declaration_name("def f'' : ℝ → ℝ := fun x => x") == "f''"
    assert declaration_name("axiom Γ : Prop") == "Γ"
    assert declaration_name("def NuclNorm : Type := ℝ") == "NuclNorm"


def test_paper_theory_builder_exports_primed_names(tmp_path: Path) -> None:
    (tmp_path / "Desol").mkdir()
    plan = plan_paper_theory(
        paper_id="2604.21884",
        domain="probability",
        seed_text="Γ' cutoff_solution",
    )
    out = write_paper_theory(project_root=tmp_path, plan=plan)
    text = out.read_text(encoding="utf-8")
    export_line = next((l for l in text.splitlines() if l.startswith("export ")), "")
    if "Γ'" in text:
        assert "Γ'" in export_line, f"Γ' missing from export line: {export_line!r}"


def test_paper_theory_import_header_injects_module_before_proof_search() -> None:
    header = paper_theory_import_header("import Mathlib\nimport Aesop", module_name="Paper_2604_21884")

    assert "import Desol.PaperTheory.Paper_2604_21884" in header
    assert "open Paper_2604_21884" in header


def _bare_plan(definitions: list[str], axioms: list[str]) -> "PaperTheoryPlan":
    """Construct a minimal PaperTheoryPlan for emission-only tests."""
    from paper_theory_builder import PaperTheoryPlan
    return PaperTheoryPlan(
        paper_id="0000.99999",
        domain="test",
        module_name="Paper_0000_99999",
        imports=["Mathlib"],
        open_scopes=[],
        definitions=definitions,
        lemmas=[],
        axioms=axioms,
        symbols=[],
        manifest={"schema_version": "1.1.0", "grounding_policy": {"proof_countable": False}},
        notes=[],
    )


def test_write_paper_theory_emits_standard_instances_for_nat_abbrev(tmp_path: Path) -> None:
    """Generalisation: every type abbrev over a known instance-bearing underlying
    type must auto-emit LE/LT/Preorder/PartialOrder/DecidableEq instances. This
    is what 2304.09598 had hand-curated; future papers get it for free."""
    (tmp_path / "Desol").mkdir()
    plan = _bare_plan(definitions=["abbrev Multisegment : Type := ℕ"], axioms=[])
    out = write_paper_theory(project_root=tmp_path, plan=plan)
    text = out.read_text(encoding="utf-8")
    for cls in ("LE", "LT", "Preorder", "PartialOrder", "DecidableEq"):
        assert f"instance : {cls} Multisegment := inferInstance" in text, (
            f"missing auto-emitted instance: {cls}"
        )


def test_write_paper_theory_emits_extended_instances_for_real_abbrev(tmp_path: Path) -> None:
    """An `abbrev Foo : Type := ℝ` should auto-emit not only LE/LT but also
    the analysis-typical classes (Norm, NormedField, Field) so paper-theory
    elaboration doesn't fail with `synthInstanceFailed: Norm Foo` when a
    translated statement uses `‖foo‖`."""
    (tmp_path / "Desol").mkdir()
    plan = _bare_plan(definitions=["abbrev MyReal : Type := ℝ"], axioms=[])
    out = write_paper_theory(project_root=tmp_path, plan=plan)
    text = out.read_text(encoding="utf-8")
    for cls in ("Zero", "One", "Add", "Mul", "Norm", "Field", "MeasurableSpace"):
        assert f"instance : {cls} MyReal := inferInstance" in text, (
            f"missing auto-emitted instance for ℝ-abbrev: {cls}"
        )


def test_write_paper_theory_emits_measurablespace_instance_for_nat_abbrev(tmp_path: Path) -> None:
    """Counting-measure-style theorems require `MeasurableSpace ℕ`. Auto-emit
    it for ℕ-abbrevs so paper-theory elaboration covers the typeclass."""
    (tmp_path / "Desol").mkdir()
    plan = _bare_plan(definitions=["abbrev PaperIndex : Type := ℕ"], axioms=[])
    out = write_paper_theory(project_root=tmp_path, plan=plan)
    text = out.read_text(encoding="utf-8")
    assert "instance : MeasurableSpace PaperIndex := inferInstance" in text


def test_write_paper_theory_emits_function_norm_for_analysis_domain(tmp_path: Path) -> None:
    """Analysis-domain papers (e.g. PDE / Sobolev results) routinely quantify
    `‖w‖` for `w : ℝ → ℝ` even though Mathlib provides no canonical norm for
    that function type. Auto-emit a placeholder `Norm (ℝ → ℝ)` so the
    statement at least elaborates; the bound itself is a real proof
    obligation tracked downstream. POC came from 2604.21884."""
    (tmp_path / "Desol").mkdir()
    from paper_theory_builder import PaperTheoryPlan
    plan = PaperTheoryPlan(
        paper_id="0000.99998",
        domain="analysis",
        module_name="Paper_0000_99998",
        imports=["Mathlib"],
        open_scopes=[],
        definitions=[],
        lemmas=[],
        axioms=[],
        symbols=[],
        manifest={"schema_version": "1.1.0", "grounding_policy": {"proof_countable": False}},
        notes=[],
    )
    out = write_paper_theory(project_root=tmp_path, plan=plan)
    text = out.read_text(encoding="utf-8")
    assert "noncomputable instance : Norm (ℝ → ℝ)" in text


def test_write_paper_theory_no_function_norm_for_generic_domain(tmp_path: Path) -> None:
    """The fallback Norm instances are gated to analysis/probability domains.
    A generic-domain paper-theory must NOT carry the placeholder norm — it
    would shadow a legitimate Mathlib instance later if one is added."""
    (tmp_path / "Desol").mkdir()
    from paper_theory_builder import PaperTheoryPlan
    plan = PaperTheoryPlan(
        paper_id="0000.99997",
        domain="",  # default / generic pack
        module_name="Paper_0000_99997",
        imports=["Mathlib"],
        open_scopes=[],
        definitions=[],
        lemmas=[],
        axioms=[],
        symbols=[],
        manifest={"schema_version": "1.1.0", "grounding_policy": {"proof_countable": False}},
        notes=[],
    )
    out = write_paper_theory(project_root=tmp_path, plan=plan)
    text = out.read_text(encoding="utf-8")
    assert "Norm (ℝ → ℝ)" not in text


def test_write_paper_theory_does_not_emit_norm_for_nat_abbrev(tmp_path: Path) -> None:
    """ℕ doesn't carry `Norm`. The per-underlying-type allowlist must filter
    Norm from a ℕ-abbrev — else `inferInstance` would fail at lake-build time
    and knock the whole paper-theory module out."""
    (tmp_path / "Desol").mkdir()
    plan = _bare_plan(definitions=["abbrev PaperIndex : Type := ℕ"], axioms=[])
    out = write_paper_theory(project_root=tmp_path, plan=plan)
    text = out.read_text(encoding="utf-8")
    assert "instance : Norm PaperIndex" not in text
    assert "instance : Field PaperIndex" not in text


def test_write_paper_theory_skips_instances_for_unknown_underlying(tmp_path: Path) -> None:
    """When the underlying type is not in the known-supported list, no instances
    are emitted (avoids `lake build` failures from `inferInstance` not finding a
    typeclass on a bespoke underlying type)."""
    (tmp_path / "Desol").mkdir()
    plan = _bare_plan(
        definitions=["abbrev SomeBespokeType : Type := MyCustomThing"],
        axioms=[],
    )
    out = write_paper_theory(project_root=tmp_path, plan=plan)
    text = out.read_text(encoding="utf-8")
    assert "instance : LE SomeBespokeType" not in text


def test_write_paper_theory_attaches_aesop_attribute_to_axioms(tmp_path: Path) -> None:
    """Generalisation: every paper-local axiom gets an `[aesop safe apply]` tag so
    proof search can find it. Replaces the hand-tagged `attribute [aesop safe apply]
    paper_L_alpha_simple_injective` line in 2304.09598."""
    (tmp_path / "Desol").mkdir()
    plan = _bare_plan(
        definitions=[],
        axioms=[
            "axiom paper_foo_inj (a b : ℕ) (h : a = b) : a = b",
            "axiom paper_bar_le (a b : ℕ) : a ≤ b ∨ b ≤ a",
        ],
    )
    out = write_paper_theory(project_root=tmp_path, plan=plan)
    text = out.read_text(encoding="utf-8")
    assert "attribute [aesop safe apply] paper_foo_inj" in text
    assert "attribute [aesop safe apply] paper_bar_le" in text


def test_write_paper_theory_does_not_attribute_non_axiom_decls(tmp_path: Path) -> None:
    """Aesop attribute auto-emission is gated to `axiom` declarations only — must
    not pollute `def`/`theorem`/`lemma` decls."""
    (tmp_path / "Desol").mkdir()
    plan = _bare_plan(
        definitions=["def f (x : ℕ) : ℕ := x"],
        axioms=[],
    )
    out = write_paper_theory(project_root=tmp_path, plan=plan)
    text = out.read_text(encoding="utf-8")
    assert "attribute [aesop safe apply] f" not in text


def test_write_paper_theory_export_line_only_includes_defined_names(tmp_path: Path) -> None:
    """Generalisation: every name in the `export Paper_<id> (...)` line MUST
    correspond to a top-level declaration in the same file. Without this filter
    a stale name (e.g., `ξ` exported while only `ξ'` is defined) crashes
    `lake build` with `Unknown constant`. The filter is purely defensive — it
    drops invalid entries, never adds anything."""
    (tmp_path / "Desol").mkdir()
    plan = _bare_plan(
        definitions=["def primed' : ℝ := 0"],
        axioms=["axiom paper_inj (a b : ℕ) : a = b"],
    )
    out = write_paper_theory(project_root=tmp_path, plan=plan)
    text = out.read_text(encoding="utf-8")
    export_line = next(l for l in text.splitlines() if l.startswith("export "))
    # Every name in the export list must literally appear as a `def`/`axiom`.
    names = export_line.split("(", 1)[1].rstrip(")").split()
    assert "primed'" in names
    assert "paper_inj" in names
    # Bare `primed` (without prime) was never defined — must not be exported.
    assert "primed" not in names

