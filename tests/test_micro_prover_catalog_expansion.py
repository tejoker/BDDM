"""Hermetic tests for the micro-prover catalog expansion.

These cover the new fast pre-pass (`rfl`/`decide`/`tauto`/`trivial`),
the new chained closers (`simp_all; omega` etc.), and the optional
domain-stratified entries appended for analysis / probability /
combinatorics / algebra papers.

Run with:  pytest tests/test_micro_prover_catalog_expansion.py -x -q
"""

from __future__ import annotations

import pytest

from prove_arxiv_batch import (
    _FAST_PREPASS_TACTICS,
    _domain_from_paper_id,
    _domain_stratified_scripts,
    _micro_prover_scripts_for_decl,
)


# ---------------------------------------------------------------------------
# Fast pre-pass coverage
# ---------------------------------------------------------------------------


def test_fast_prepass_tactics_present_and_first() -> None:
    """The fast pre-pass tactics must appear at the very start of every catalog
    (before any shape-conditional or domain-conditional entries) so trivially
    true goals close in under a second without wasting state-MCTS budget."""
    decl = "theorem t : 1 = 1 := by sorry"
    scripts = _micro_prover_scripts_for_decl(decl)
    # Each fast pre-pass tactic is in the catalog.
    for tac in _FAST_PREPASS_TACTICS:
        assert tac in scripts, f"missing fast pre-pass: {tac!r}"
    # And the first entry is `rfl` — the cheapest closer.
    assert scripts[0] == "rfl"
    # `decide`, `tauto`, `trivial` come immediately after.
    head = scripts[: len(_FAST_PREPASS_TACTICS)]
    assert head == list(_FAST_PREPASS_TACTICS)


def test_fast_prepass_runs_even_on_propositional_tautology() -> None:
    """For a Boolean-valued tautology like `True ∨ False`, the pre-pass must
    still be the entry point — Lean closes it via `decide`/`tauto`/`trivial`."""
    decl = "theorem t : True ∨ False := by sorry"
    scripts = _micro_prover_scripts_for_decl(decl)
    assert scripts[0] == "rfl"
    assert "tauto" in scripts
    assert "trivial" in scripts


# ---------------------------------------------------------------------------
# Chained closers
# ---------------------------------------------------------------------------


def test_chained_simp_all_omega_in_arith_catalog() -> None:
    """An arithmetic goal must include the chained closer ``simp_all; omega``;
    this catches goals where a single rewrite unblocks the omega hammer."""
    decl = "theorem t (n : Nat) (h : n + 1 = 2) : n = 1 := by sorry"
    scripts = _micro_prover_scripts_for_decl(decl)
    assert "simp_all; omega" in scripts
    assert "simp_all; linarith" in scripts
    assert "simp_all; nlinarith" in scripts


def test_push_neg_aesop_unconditional() -> None:
    """``push_neg; aesop`` is now an unconditional hammer pass — useful when
    the target is a negated quantifier statement that aesop alone misses."""
    decl = "theorem t (P : Nat → Prop) : ¬ (∀ n, P n) → ∃ n, ¬ P n := by sorry"
    scripts = _micro_prover_scripts_for_decl(decl)
    assert "push_neg; aesop" in scripts


def test_intro_trivial_and_intro_exact_for_implication() -> None:
    """For an implication target, the catalog must include the cheap
    ``intro h; trivial`` / ``intro h; exact h`` closers."""
    decl = "theorem t (P : Prop) : P → P := by sorry"
    scripts = _micro_prover_scripts_for_decl(decl)
    assert "intro h; trivial" in scripts
    assert "intro h; exact h" in scripts
    assert "intro h; linarith" in scripts


def test_conjunction_includes_all_goals_trivial_combinator() -> None:
    """Conjunction-shaped targets must include ``constructor; all_goals
    trivial`` — closes ``True ∧ x = x``-style scaffold rows."""
    decl = "theorem t (x : Nat) : True ∧ x = x := by sorry"
    scripts = _micro_prover_scripts_for_decl(decl)
    assert "constructor; all_goals trivial" in scripts
    assert "constructor <;> trivial" in scripts
    assert "refine ⟨?_, ?_⟩ <;> trivial" in scripts


# ---------------------------------------------------------------------------
# Domain stratification
# ---------------------------------------------------------------------------


def test_domain_from_paper_id_recognises_known_prefixes() -> None:
    """Both ``algebra/...`` and ``algebra_...`` forms map to ``"algebra"``."""
    assert _domain_from_paper_id("analysis/2304.09598") == "analysis"
    assert _domain_from_paper_id("probability_2210.12345") == "probability"
    assert _domain_from_paper_id("combinatorics/abc") == "combinatorics"
    assert _domain_from_paper_id("algebra/foo") == "algebra"
    # Unknown / missing prefix returns "".
    assert _domain_from_paper_id("") == ""
    assert _domain_from_paper_id("randomprefix/x") == ""
    assert _domain_from_paper_id("2304.09598") == ""


def test_analysis_domain_adds_more_candidates_than_probability_for_inequality() -> None:
    """For an analysis-flavoured inequality (``Real.exp`` etc.), the analysis
    catalog must add strictly more entries than the probability catalog."""
    decl = "theorem t (x : ℝ) : 0 ≤ Real.exp x := by sorry"
    a = _micro_prover_scripts_for_decl(decl, domain="analysis")
    p = _micro_prover_scripts_for_decl(decl, domain="probability")
    none = _micro_prover_scripts_for_decl(decl)
    # Analysis appends the `Real.exp_pos` hint chain; probability has nothing
    # to add for a pure inequality goal.
    assert "linarith [Real.exp_pos _]" in a
    assert "linarith [Real.exp_pos _]" not in p
    assert "linarith [Real.exp_pos _]" not in none
    # Domain-stratified appends strictly grow the candidate set.
    assert len(a) > len(none)
    assert len(a) > len(p)


def test_probability_domain_adds_measure_univ_when_measure_shows_up() -> None:
    decl = "theorem t (μ : MeasureTheory.Measure α) : μ Set.univ = μ Set.univ := by sorry"
    p = _micro_prover_scripts_for_decl(decl, domain="probability")
    a = _micro_prover_scripts_for_decl(decl, domain="analysis")
    assert "simp [MeasureTheory.measure_univ]" in p
    assert "simp [MeasureTheory.measure_univ]" not in a


def test_combinatorics_domain_adds_interval_cases_omega() -> None:
    decl = "theorem t (n : Nat) (h : n < 3) : n ≤ 2 := by sorry"
    c = _micro_prover_scripts_for_decl(decl, domain="combinatorics")
    assert "interval_cases <;> omega" in c


def test_algebra_domain_adds_ring_for_ring_goals() -> None:
    decl = "theorem t (a b : Int) : a + b = b + a := by sorry"
    a = _micro_prover_scripts_for_decl(decl, domain="algebra")
    none = _micro_prover_scripts_for_decl(decl)
    assert "ring" in a
    assert "ring" not in none  # ring is *not* an unconditional hammer
    assert "ring_nf; ring" in a


def test_domain_stratification_isolation_helper() -> None:
    """``_domain_stratified_scripts`` is independently testable: empty domain
    and unknown domains return nothing."""
    assert _domain_stratified_scripts("", "anything") == []
    assert _domain_stratified_scripts("unknown_domain", "anything") == []
    # Known domain with no matching token also yields nothing.
    assert _domain_stratified_scripts("analysis", "True") == []


# ---------------------------------------------------------------------------
# Determinism / dedup
# ---------------------------------------------------------------------------


def test_catalog_is_deterministic_and_deduplicated() -> None:
    """Calling the catalog twice returns the same ordered list, and there are
    no duplicate entries (the trailing ``dict.fromkeys`` dedup runs)."""
    decl = "theorem t (n : Nat) : n + 0 = n := by sorry"
    a = _micro_prover_scripts_for_decl(decl, domain="analysis")
    b = _micro_prover_scripts_for_decl(decl, domain="analysis")
    assert a == b
    assert len(a) == len(set(a))


@pytest.mark.parametrize(
    "domain",
    ["analysis", "probability", "combinatorics", "algebra", ""],
)
def test_fast_prepass_always_first_regardless_of_domain(domain: str) -> None:
    """No matter which domain is supplied, the fast pre-pass tactics remain at
    the head of the catalog — domain entries only ever append."""
    decl = "theorem t (x : ℝ) : 0 ≤ Real.exp x := by sorry"
    scripts = _micro_prover_scripts_for_decl(decl, domain=domain)
    head = scripts[: len(_FAST_PREPASS_TACTICS)]
    assert head == list(_FAST_PREPASS_TACTICS), f"domain={domain!r} reorders pre-pass"
