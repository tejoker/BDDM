"""Hermetic integration tests for the Round-VIII shape-aware composition
loop in `scripts/sweep_lemma_factor_v2.py`.

These tests stub `wp_sweep._patch_proof_flex` / `wp_sweep._revert_proof_flex`
so they can simulate "patch + lake-validate" without actually touching lake
or the file system. The validator closure is injected directly into
`attempt_composition`.

Coverage:
  * For a target with 2 closing aux, the driver tries ALL matching
    composition skeletons (not just the first one).
  * A skeleton that returns ok=False is followed by the next skeleton.
  * A skeleton that returns ok=True short-circuits the loop.
  * Empty aux list -> no_composition_bodies returned.
  * Single-aux disjunction can compose via Or.inl / Or.inr.

All tests are pure-Python — no Mistral, no lake, no subprocess.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import sweep_lemma_factor_v2 as sweep
import sweep_leanstral_whole_proof as wp_sweep


class _PatchedIO:
    """Bag holding the tmp_path + attempts list (PosixPath has __slots__,
    so we can't attach attributes directly)."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.attempts: list[str] = []


@pytest.fixture
def patched_io(monkeypatch, tmp_path: Path) -> _PatchedIO:
    """Replace patch/revert helpers with no-ops that write the body into a
    side-channel so the test can inspect what was attempted."""
    bag = _PatchedIO(tmp_path)

    def _patch(_path: Path, _name: str, body: str) -> bool:
        bag.attempts.append(body)
        return True

    def _revert(_path: Path, _name: str) -> bool:
        return True

    monkeypatch.setattr(wp_sweep, "_patch_proof_flex", _patch)
    monkeypatch.setattr(wp_sweep, "_revert_proof_flex", _revert)
    return bag


def test_composition_tries_all_skeletons_when_each_fails(patched_io: _PatchedIO) -> None:
    f = patched_io.tmp_path / "fake.lean"
    f.write_text("dummy", encoding="utf-8")
    attempts = patched_io.attempts

    def _always_fail(_path: Path, _name: str) -> tuple[bool, str]:
        return False, "simulated lake error"

    ok, body, err = sweep.attempt_composition(
        lean_file=f,
        parent_short_name="parent",
        aux_names=["a1", "a2"],
        parent_target_shape="conjunction_with_ineq",
        per_lake_timeout=60,
        baseline_errors=0,
        validator=_always_fail,
        aux_records=[
            {"aux_name": "a1", "compose_hint": "first conjunct"},
            {"aux_name": "a2", "compose_hint": "second conjunct"},
        ],
    )
    assert not ok
    # We should have tried MORE than one skeleton (the new shape-aware
    # emitter returns several candidates for `conjunction_with_ineq`).
    assert len(attempts) >= 3
    assert any("exact ⟨a1, a2⟩" in att for att in attempts)
    assert any("constructor" in att for att in attempts)


def test_composition_short_circuits_on_first_success(patched_io: _PatchedIO) -> None:
    f = patched_io.tmp_path / "fake.lean"
    f.write_text("dummy", encoding="utf-8")
    attempts = patched_io.attempts

    def _always_ok(_path: Path, _name: str) -> tuple[bool, str]:
        return True, ""

    ok, body, err = sweep.attempt_composition(
        lean_file=f,
        parent_short_name="parent",
        aux_names=["a", "b"],
        parent_target_shape="iff_bidirectional",
        per_lake_timeout=60,
        baseline_errors=0,
        validator=_always_ok,
        aux_records=[
            {"aux_name": "a", "compose_hint": "fwd"},
            {"aux_name": "b", "compose_hint": "bwd"},
        ],
    )
    assert ok
    assert "exact" in body or "constructor" in body
    # Only ONE skeleton attempted (the first one passed).
    assert len(attempts) == 1


def test_composition_third_skeleton_succeeds(patched_io: _PatchedIO) -> None:
    f = patched_io.tmp_path / "fake.lean"
    f.write_text("dummy", encoding="utf-8")
    attempts = patched_io.attempts

    # Validator that says ok on the 3rd attempt.
    call_count = {"n": 0}

    def _ok_on_third(_path: Path, _name: str) -> tuple[bool, str]:
        call_count["n"] += 1
        return (call_count["n"] >= 3, "fail #" + str(call_count["n"]) if call_count["n"] < 3 else "")

    ok, body, err = sweep.attempt_composition(
        lean_file=f,
        parent_short_name="parent",
        aux_names=["x", "y"],
        parent_target_shape="conjunction_with_ineq",
        per_lake_timeout=60,
        baseline_errors=0,
        validator=_ok_on_third,
        aux_records=[
            {"aux_name": "x", "compose_hint": "first"},
            {"aux_name": "y", "compose_hint": "second"},
        ],
    )
    assert ok
    assert len(attempts) == 3


def test_composition_no_aux_returns_no_bodies(patched_io: _PatchedIO) -> None:
    f = patched_io.tmp_path / "fake.lean"
    f.write_text("dummy", encoding="utf-8")

    def _fail(_path: Path, _name: str) -> tuple[bool, str]:
        return False, "should not be called"

    ok, body, err = sweep.attempt_composition(
        lean_file=f,
        parent_short_name="parent",
        aux_names=[],
        parent_target_shape="conjunction_with_ineq",
        per_lake_timeout=60,
        baseline_errors=0,
        validator=_fail,
        aux_records=[],
    )
    assert not ok
    assert err == "no_composition_bodies"


def test_composition_disjunction_single_aux_via_or_inl(patched_io: _PatchedIO) -> None:
    """A disjunction target can be composed from a SINGLE aux supplying
    one of the two sides."""
    f = patched_io.tmp_path / "fake.lean"
    f.write_text("dummy", encoding="utf-8")

    def _ok(_path: Path, _name: str) -> tuple[bool, str]:
        return True, ""

    ok, body, err = sweep.attempt_composition(
        lean_file=f,
        parent_short_name="parent",
        aux_names=["lhs_aux"],
        parent_target_shape="disjunction",
        per_lake_timeout=60,
        baseline_errors=0,
        validator=_ok,
        aux_records=[
            {"aux_name": "lhs_aux", "compose_hint": "left side"},
        ],
    )
    assert ok
    assert "Or.inl lhs_aux" in body


def test_composition_implication_single_aux_via_intro(patched_io: _PatchedIO) -> None:
    f = patched_io.tmp_path / "fake.lean"
    f.write_text("dummy", encoding="utf-8")

    def _ok(_path: Path, _name: str) -> tuple[bool, str]:
        return True, ""

    ok, body, err = sweep.attempt_composition(
        lean_file=f,
        parent_short_name="parent",
        aux_names=["deduction"],
        parent_target_shape="implication",
        per_lake_timeout=60,
        baseline_errors=0,
        validator=_ok,
        aux_records=[{"aux_name": "deduction", "compose_hint": "body"}],
    )
    assert ok
    assert "intro h" in body
    assert "exact deduction h" in body


def test_composition_calc_chain_emits_trans(patched_io: _PatchedIO) -> None:
    f = patched_io.tmp_path / "fake.lean"
    f.write_text("dummy", encoding="utf-8")
    attempts = patched_io.attempts

    # Accept only the trans-style body, reject the calc skeleton.
    def _validator(_path: Path, _name: str) -> tuple[bool, str]:
        if attempts and ".trans" in attempts[-1]:
            return True, ""
        return False, "not the trans body"

    ok, body, err = sweep.attempt_composition(
        lean_file=f,
        parent_short_name="parent",
        aux_names=["s1", "s2"],
        parent_target_shape="calc_chain",
        per_lake_timeout=60,
        baseline_errors=0,
        validator=_validator,
        aux_records=[
            {"aux_name": "s1", "compose_hint": "step-1"},
            {"aux_name": "s2", "compose_hint": "step-2"},
        ],
    )
    assert ok
    assert ".trans" in body


def test_composition_legacy_coarse_shape_still_routes(patched_io: _PatchedIO) -> None:
    f = patched_io.tmp_path / "fake.lean"
    f.write_text("dummy", encoding="utf-8")

    def _ok(_path: Path, _name: str) -> tuple[bool, str]:
        return True, ""

    # Pass a legacy coarse label ('and'); driver must still produce a
    # body and accept the first valid composition.
    ok, body, err = sweep.attempt_composition(
        lean_file=f,
        parent_short_name="parent",
        aux_names=["a", "b"],
        parent_target_shape="and",
        per_lake_timeout=60,
        baseline_errors=0,
        validator=_ok,
        aux_records=[
            {"aux_name": "a", "compose_hint": "first conjunct"},
            {"aux_name": "b", "compose_hint": "second conjunct"},
        ],
    )
    assert ok
    assert "⟨a, b⟩" in body
