"""Tests for `scripts/audit_fuzz_mutations.py`.

The fuzzer's role is unknown-unknown coverage for
`audit_fully_proven_integrity`. These tests pin three properties of
the fuzzer itself:

  1. **Reproducibility**: same seed → identical iteration sequence.
     Without this, fuzz failures aren't reproducible and the audit
     can't be regressioned against a specific bypass.
  2. **Label correctness**: the fuzzer's ground-truth labels
     (`bypass` vs `legitimate`) must match what the audit detector
     would say for the same row IF the contract held. We probe this
     indirectly: a smoke run of N iterations on the current audit
     must produce 0 escapes and 0 unexpected demotions.
  3. **Coverage**: both `bypass` and `legitimate` arms fire across a
     batch; both sub-arms within each (sorry_body / trivialized,
     tactic_real / term_mode / audited_core) are reachable.

All tests are hermetic — no filesystem, no network, no lake. Each runs
in <1 s.
"""

from __future__ import annotations

import random

import pytest

from audit_fuzz_mutations import (
    _gen_bypass_iteration,
    _gen_legitimate_iteration,
    _stmt_template_to_tail_and_target,
    fuzz_audit_against_random_bypasses,
)


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


def test_fuzzer_is_reproducible_across_runs() -> None:
    """Calling the fuzzer twice with the same (seed, n_iterations) must
    produce identical results. This is the load-bearing property — without
    it, fuzz failures can't be replayed against a fixed audit."""
    a = fuzz_audit_against_random_bypasses(seed=42, n_iterations=100)
    b = fuzz_audit_against_random_bypasses(seed=42, n_iterations=100)
    assert a == b, "fuzzer is non-deterministic at seed=42"


def test_fuzzer_seed_changes_iteration_sequence() -> None:
    """Different seeds must produce different generator sequences. We
    can't compare aggregate counts (caught/preserved happen to collide
    near 50/50 across seeds on a clean audit), so we compare the actual
    iteration record streams via the bypass-generator directly."""
    rng_a = random.Random(1)
    rng_b = random.Random(2)
    samples_a = [_gen_bypass_iteration(rng_a, i)["lean_src"] for i in range(20)]
    samples_b = [_gen_bypass_iteration(rng_b, i)["lean_src"] for i in range(20)]
    assert samples_a != samples_b, (
        "seeds 1 and 2 produced identical bypass-iteration streams; seed "
        "isn't threaded into the generator"
    )


# ---------------------------------------------------------------------------
# Contract: smoke run on current audit produces 0 escapes / 0 unexpected
# ---------------------------------------------------------------------------


def test_fuzz_smoke_100_iterations_is_clean() -> None:
    """A 100-iteration smoke against the current audit must produce 0
    escapes and 0 unexpected demotions. If this fails, EITHER the audit
    has a real gap OR the fuzzer's label generator is out of sync with
    the audit's detector."""
    result = fuzz_audit_against_random_bypasses(seed=1, n_iterations=100)
    assert result["escaped"] == [], (
        f"fuzzer surfaced {len(result['escaped'])} bypass escapes — audit "
        f"has a gap. First: {result['escaped'][0]}"
    )
    assert result["unexpected_demotions"] == [], (
        f"fuzzer surfaced {len(result['unexpected_demotions'])} unexpected "
        f"demotions — fuzzer or audit miscalibration. First: "
        f"{result['unexpected_demotions'][0]}"
    )


@pytest.mark.parametrize("seed", [0, 7, 42, 99, 1234])
def test_fuzz_clean_across_seeds(seed: int) -> None:
    """Smoke the fuzzer across a handful of seeds. If any seed surfaces
    a real escape, the audit has an unknown-unknown gap and the failure
    payload will pin the exact bypass shape."""
    result = fuzz_audit_against_random_bypasses(seed=seed, n_iterations=200)
    assert result["escaped"] == [], (
        f"seed={seed}: {len(result['escaped'])} escapes; first: "
        f"{result['escaped'][0]}"
    )
    assert result["unexpected_demotions"] == [], (
        f"seed={seed}: {len(result['unexpected_demotions'])} unexpected "
        f"demotions; first: {result['unexpected_demotions'][0]}"
    )


# ---------------------------------------------------------------------------
# Coverage: both arms fire
# ---------------------------------------------------------------------------


def test_fuzz_covers_both_bypass_and_legitimate_arms() -> None:
    """A 200-iteration run must exercise BOTH arms — otherwise the
    fuzzer is effectively only testing one side of the contract."""
    result = fuzz_audit_against_random_bypasses(seed=1, n_iterations=200)
    # caught counts bypass-arm hits; preserved counts legitimate-arm hits.
    assert result["caught"] > 0, "no bypass iterations were generated"
    assert result["preserved"] > 0, "no legitimate iterations were generated"


def test_generator_emits_both_bypass_subarms() -> None:
    """The bypass arm has two sub-arms: `sorry_body` and `trivialized`.
    Across many iterations both must be exercised — otherwise one whole
    class of bypass is silently untested."""
    rng = random.Random(0)
    arms_seen = set()
    for i in range(200):
        it = _gen_bypass_iteration(rng, i)
        arms_seen.add(it["arm"])
    assert arms_seen == {"sorry_body", "trivialized"}, (
        f"bypass generator missed sub-arms: {arms_seen}"
    )


def test_generator_emits_all_legitimate_subarms() -> None:
    """The legitimate arm has three sub-arms: tactic_real, term_mode,
    audited_core. Across 300 iterations all three should appear."""
    rng = random.Random(0)
    arms_seen = set()
    for i in range(300):
        it = _gen_legitimate_iteration(rng, i)
        arms_seen.add(it["arm"])
    assert arms_seen == {"tactic_real", "term_mode", "audited_core"}, (
        f"legitimate generator missed sub-arms: {arms_seen}"
    )


# ---------------------------------------------------------------------------
# Helper: template parser
# ---------------------------------------------------------------------------


def test_stmt_template_parser_handles_nested_colons() -> None:
    """Statements with `:` inside binders (e.g. `(p q : Prop)`) must
    split at the TOP-LEVEL `:`, not the first one. Otherwise the
    fuzzer's lean_statement field would be garbled and the audit's
    trivialization check would misclassify."""
    tail, target = _stmt_template_to_tail_and_target(
        "theorem foo (p q : Prop) (hp : p) (hpq : p → q) : q"
    )
    assert tail == " (p q : Prop) (hp : p) (hpq : p → q) : q"
    assert target == "q"


def test_stmt_template_parser_extracts_target_after_binders() -> None:
    """For a real-quantifier theorem, the target is everything past the
    top-level `:`. We verify the parser handles unicode binders."""
    tail, target = _stmt_template_to_tail_and_target(
        "theorem foo (n : ℕ) (h : n > 0) : n + 1 > 1"
    )
    assert target == "n + 1 > 1"
    assert "(n : ℕ)" in tail
    assert "(h : n > 0)" in tail
