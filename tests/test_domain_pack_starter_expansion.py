"""Tests for the expanded domain-pack starter definitions/lemmas.

Each domain pack (`analysis`, `probability`, `algebra`, `combinatorics`,
`number_theory`, `pde`, `spde`, `graph_theory`) now carries area-typical
starter defs and lemmas that ground recurring identifiers before the
paper-theory builder emits paper-local axioms.

The corpus mine that seeded these names lives in the docstrings of the
individual pack modules under `scripts/domain_packs/`. The asserts below
sanity-check name presence and basic Lean-syntactic well-formedness; the
heavy `lake env lean` smoke checks live behind the `slow` marker so the
hermetic test suite stays fast.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from domain_packs import get_domain_pack
from domain_packs.algebra import PACK as ALGEBRA_PACK
from domain_packs.analysis import PACK as ANALYSIS_PACK
from domain_packs.combinatorics import PACK as COMBINATORICS_PACK
from domain_packs.graph_theory import PACK as GRAPH_PACK
from domain_packs.number_theory import PACK as NUMBER_THEORY_PACK
from domain_packs.pde import PACK as PDE_PACK
from domain_packs.probability import PACK as PROBABILITY_PACK
from domain_packs.spde import PACK as SPDE_PACK
from paper_theory_builder import build_paper_theory_module


_DECL_RE = re.compile(
    r"^(?:noncomputable\s+)?(?:def|abbrev|theorem|lemma)\s+(\w+)\s*"
)


def _decl_names(items: list[str]) -> list[str]:
    names: list[str] = []
    for item in items:
        m = _DECL_RE.match(item.strip())
        if m:
            names.append(m.group(1))
    return names


# ---------------------------------------------------------------------------
# 1. Coverage — every non-default pack has starter defs AND starter lemmas.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pack",
    [
        ANALYSIS_PACK,
        PROBABILITY_PACK,
        ALGEBRA_PACK,
        COMBINATORICS_PACK,
        NUMBER_THEORY_PACK,
        PDE_PACK,
        SPDE_PACK,
        GRAPH_PACK,
    ],
    ids=lambda p: p.name,
)
def test_every_pack_has_starter_defs_and_lemmas(pack) -> None:
    """All non-default packs must populate starter_definitions AND
    starter_lemmas — that's the contract the paper-theory builder relies
    on (file: paper_theory_builder.py:135-150)."""
    assert pack.starter_definitions, f"{pack.name}: empty starter_definitions"
    assert pack.starter_lemmas, f"{pack.name}: empty starter_lemmas"


# ---------------------------------------------------------------------------
# 2. Recurring-identifier targets — these names were chosen from a corpus
#    mine of Desol/PaperTheory/Paper_*.lean and must remain present.
# ---------------------------------------------------------------------------


def test_analysis_pack_grounds_recurring_identifiers() -> None:
    """`HSobolev` (3 papers), `L2Space` (3 papers), `infty` (4 papers) were
    the most common paper-local stubs in the canonical corpus. The analysis
    pack must ground them so the translator stops re-emitting axiom-form
    duplicates."""
    body = "\n".join(ANALYSIS_PACK.starter_definitions)
    assert "HSobolev" in body
    assert "L2Space" in body
    assert "infty" in body


def test_probability_pack_grounds_recurring_idioms() -> None:
    """Martingale / filtration / independence / stationarity / second-moment
    are the recurring vocabulary of probability papers."""
    body = "\n".join(PROBABILITY_PACK.starter_definitions)
    for ident in (
        "probMartingale",
        "probFiltration",
        "probIndependent",
        "probStationary",
        "hasFiniteSecondMoment",
    ):
        assert ident in body, f"probability pack missing {ident!r}"


# ---------------------------------------------------------------------------
# 3. Well-formedness — every starter def/lemma must declare a name
#    extractable by `declaration_name`-style regex. (Catches stray bodies
#    that don't begin with `def`/`theorem`/etc.)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pack",
    [
        ANALYSIS_PACK,
        PROBABILITY_PACK,
        ALGEBRA_PACK,
        COMBINATORICS_PACK,
        NUMBER_THEORY_PACK,
        PDE_PACK,
        SPDE_PACK,
        GRAPH_PACK,
    ],
    ids=lambda p: p.name,
)
def test_starter_entries_are_well_formed(pack) -> None:
    """Each starter def must start with `def`/`abbrev`/`noncomputable def`;
    each starter lemma must start with `theorem`/`lemma`. The paper-theory
    builder splits on these prefixes."""
    for d in pack.starter_definitions:
        s = d.strip()
        assert (
            s.startswith(("def ", "abbrev ", "noncomputable "))
        ), f"{pack.name}: bad def prefix in {s!r}"
        # Must contain `:=` so the body is provided (no axioms-in-disguise).
        assert ":=" in s, f"{pack.name}: starter def has no body: {s!r}"
        # Must NOT use `sorry` — a sorry-bodied starter is an axiom in disguise.
        assert "sorry" not in s, f"{pack.name}: starter def uses sorry: {s!r}"
    for l in pack.starter_lemmas:
        s = l.strip()
        assert s.startswith(("theorem ", "lemma ")), (
            f"{pack.name}: bad lemma prefix in {s!r}"
        )
        assert ":=" in s, f"{pack.name}: starter lemma has no body: {s!r}"
        assert "sorry" not in s, f"{pack.name}: starter lemma uses sorry: {s!r}"


# ---------------------------------------------------------------------------
# 4. Lemmas reference defs — every starter lemma name should evidently
#    relate to one of the pack's starter defs (catches drift where a lemma
#    is renamed but its companion def is removed).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pack",
    [ANALYSIS_PACK, PROBABILITY_PACK, ALGEBRA_PACK, COMBINATORICS_PACK,
     NUMBER_THEORY_PACK, PDE_PACK, SPDE_PACK, GRAPH_PACK],
    ids=lambda p: p.name,
)
def test_starter_lemmas_reference_starter_defs(pack) -> None:
    """A starter lemma's body should reference at least one starter def
    name (otherwise the lemma is dangling and won't help proof search)."""
    def_names = _decl_names(pack.starter_definitions)
    assert def_names, f"{pack.name}: no starter def names extracted"
    for lemma in pack.starter_lemmas:
        # Skip lemma name itself when checking for def references.
        m = _DECL_RE.match(lemma.strip())
        lemma_name = m.group(1) if m else ""
        body = lemma.replace(lemma_name, "", 1)
        assert any(dn in body for dn in def_names), (
            f"{pack.name}: lemma {lemma!r} doesn't reference any def name "
            f"in {def_names}"
        )


# ---------------------------------------------------------------------------
# 5. Default pack still empty — protect the contract that generic / unknown
#    domains do NOT get pollution.
# ---------------------------------------------------------------------------


def test_default_pack_has_no_starters() -> None:
    pack = get_domain_pack("nonexistent_domain_xyz")
    assert pack.starter_definitions == []
    assert pack.starter_lemmas == []


# ---------------------------------------------------------------------------
# 6. End-to-end — the analysis pack's starters land in a generated paper-
#    theory file.
# ---------------------------------------------------------------------------


def test_analysis_starters_land_in_generated_paper_theory(tmp_path: Path) -> None:
    """Smoke: build a paper-theory module for a fake analysis paper and
    confirm every analysis starter def/lemma appears in the generated
    `.lean` file."""
    plan, lean_path = build_paper_theory_module(
        project_root=tmp_path,
        paper_id="9999.88888",
        domain="analysis",
        seed_text="",
        inventory=[],
    )
    text = lean_path.read_text(encoding="utf-8")
    for name in _decl_names(ANALYSIS_PACK.starter_definitions):
        assert name in text, f"analysis starter def {name!r} missing from generated file"
    for name in _decl_names(ANALYSIS_PACK.starter_lemmas):
        assert name in text, f"analysis starter lemma {name!r} missing from generated file"


def test_probability_starters_land_in_generated_paper_theory(tmp_path: Path) -> None:
    plan, lean_path = build_paper_theory_module(
        project_root=tmp_path,
        paper_id="9999.77777",
        domain="probability",
        seed_text="",
        inventory=[],
    )
    text = lean_path.read_text(encoding="utf-8")
    for name in _decl_names(PROBABILITY_PACK.starter_definitions):
        assert name in text, f"probability starter def {name!r} missing"


def test_pde_starters_land_in_generated_paper_theory(tmp_path: Path) -> None:
    plan, lean_path = build_paper_theory_module(
        project_root=tmp_path,
        paper_id="9999.66666",
        domain="pde",
        seed_text="",
        inventory=[],
    )
    text = lean_path.read_text(encoding="utf-8")
    for name in _decl_names(PDE_PACK.starter_definitions):
        assert name in text, f"pde starter def {name!r} missing"


# ---------------------------------------------------------------------------
# 7. Slow live check — every pack's starters must REALLY elaborate against
#    current Mathlib. Mark slow so the default hermetic run skips it.
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_all_starters_elaborate_under_lake(tmp_path: Path) -> None:
    """Build a single combined .lean file containing every pack's starter
    defs and lemmas and run `lake env lean` on it. If any starter is
    syntactically off or uses a Mathlib name that drifted, this fails.

    Skips when `lake` is not on PATH or the project's lakefile is missing
    (so the test is portable to bare check-outs)."""
    project_root = Path(__file__).resolve().parent.parent
    if not (project_root / "lakefile.toml").exists():
        pytest.skip("no lakefile.toml at project root")
    if shutil.which("lake") is None:
        pytest.skip("lake not on PATH")

    packs = [
        ANALYSIS_PACK,
        PROBABILITY_PACK,
        ALGEBRA_PACK,
        COMBINATORICS_PACK,
        NUMBER_THEORY_PACK,
        PDE_PACK,
        SPDE_PACK,
        GRAPH_PACK,
    ]
    lines: list[str] = [
        "-- Combined starter smoke test (auto-generated by test).",
        "import Mathlib",
        "import Aesop",
        "",
        "open MeasureTheory Filter Set Topology BigOperators",
        "",
    ]
    for pack in packs:
        ns = f"TestPack_{pack.name}"
        lines.append(f"namespace {ns}")
        lines.append("")
        for d in pack.starter_definitions:
            lines.append(d)
            lines.append("")
        for l in pack.starter_lemmas:
            lines.append(l)
            lines.append("")
        lines.append(f"end {ns}")
        lines.append("")
    smoke_lean = tmp_path / "all_starters_smoke.lean"
    smoke_lean.write_text("\n".join(lines), encoding="utf-8")

    # `lake env lean` requires the project root for the Mathlib resolution.
    proc = subprocess.run(
        ["lake", "env", "lean", str(smoke_lean)],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        pytest.fail(
            f"lake env lean rejected starter defs:\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
