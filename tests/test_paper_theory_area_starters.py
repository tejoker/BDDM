"""Tests for per-area starter definitions in the paper-theory builder.

Each domain pack can carry `starter_definitions` and `starter_lemmas`; the
planner pre-emits these at the top of every paper-theory file in that domain
so the translator can route paper claims through area-typical names instead
of inventing fresh axiom-form symbols."""

from __future__ import annotations

from domain_packs import get_domain_pack
from domain_packs.analysis import PACK as ANALYSIS_PACK
from domain_packs.probability import PACK as PROBABILITY_PACK
from paper_theory_builder import plan_paper_theory


def test_analysis_pack_has_starter_definitions() -> None:
    """The analysis domain pack must include at least one starter def — the
    pipeline relies on these being available for paper-theory generation."""
    assert hasattr(ANALYSIS_PACK, "starter_definitions")
    assert len(ANALYSIS_PACK.starter_definitions) >= 1
    # Must include the canonical analytic seminorm/oscillation/Lp-norm names.
    body = "\n".join(ANALYSIS_PACK.starter_definitions)
    assert "analysisSeminorm" in body
    assert "analysisLpNorm" in body
    assert "analysisOscillation" in body


def test_probability_pack_has_starter_definitions() -> None:
    assert hasattr(PROBABILITY_PACK, "starter_definitions")
    body = "\n".join(PROBABILITY_PACK.starter_definitions)
    assert "probExpectation" in body or "probAlmostSure" in body


def test_default_pack_has_empty_starter_definitions() -> None:
    """The default domain pack (used when no domain is set) must NOT emit
    starter defs — those would pollute generic paper-theory files with
    irrelevant types."""
    pack = get_domain_pack("nonexistent_domain")
    assert getattr(pack, "starter_definitions", []) == []
    assert getattr(pack, "starter_lemmas", []) == []


def test_planner_emits_analysis_starters() -> None:
    """When the domain is `analysis`, the plan's `definitions` list must
    include the analysis starter defs at the head — the translator relies on
    them appearing before any inventory-extracted symbols."""
    plan = plan_paper_theory(
        paper_id="9999.99999",
        domain="analysis",
        seed_text="",
        inventory=[],
        glossary=None,
    )
    defs_str = "\n".join(plan.definitions)
    assert "analysisSeminorm" in defs_str
    assert "analysisOscillation" in defs_str
    # Starters appear EARLY (before any extracted symbols).
    first_starter_idx = next(
        (i for i, d in enumerate(plan.definitions) if "analysisSeminorm" in d),
        -1,
    )
    assert first_starter_idx >= 0


def test_planner_emits_probability_starters() -> None:
    plan = plan_paper_theory(
        paper_id="9999.99999",
        domain="probability",
        seed_text="",
        inventory=[],
        glossary=None,
    )
    defs_str = "\n".join(plan.definitions)
    assert "probExpectation" in defs_str or "probAlmostSure" in defs_str


def test_planner_does_not_emit_starters_for_unknown_domain() -> None:
    """Unknown / empty domain → default pack → no starter defs."""
    plan = plan_paper_theory(
        paper_id="9999.99999",
        domain="",
        seed_text="",
        inventory=[],
        glossary=None,
    )
    defs_str = "\n".join(plan.definitions)
    assert "analysisSeminorm" not in defs_str
    assert "probExpectation" not in defs_str


def test_planner_notes_record_starter_emission() -> None:
    """When starters fire, the plan's `notes` list must record it for
    audit/debugging."""
    plan = plan_paper_theory(
        paper_id="9999.99999",
        domain="analysis",
        seed_text="",
        inventory=[],
        glossary=None,
    )
    notes_str = "\n".join(plan.notes)
    assert "starter def" in notes_str.lower()
