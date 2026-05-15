"""Hermetic tests for `scripts/aux_deterministic_prover.py`.

The deterministic catalog is the non-LLM pre-pass that Round-XII data
showed could attack the aux-closure bottleneck. These tests fix the
catalog ordering, exercise the validator-callback contract, and assert
first-success-wins semantics — none of them call lake/REPL/Mistral.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from aux_deterministic_prover import (
    _DEFAULT_CATALOG,
    catalog_summary,
    try_deterministic_close_aux,
)


def _make_validator(
    *, success_tactics: set[str], err_text: str = "lake_err"
) -> Callable[[Path, str, str], tuple[bool, str]]:
    """Return a callable matching the validator signature.
    Closes when the candidate body string is in `success_tactics`."""
    def _v(lean_file: Path, theorem_name: str, proof_body: str) -> tuple[bool, str]:
        if proof_body.strip() in success_tactics:
            return True, ""
        return False, f"{err_text}:{proof_body.strip()}"
    return _v


def test_catalog_is_not_empty():
    assert len(_DEFAULT_CATALOG) >= 5
    # Trivial-closer comes first (cheapest, most common shallow closure).
    assert _DEFAULT_CATALOG[0] == "trivial"
    # aesop / exact? go last (most expensive).
    assert "aesop" in _DEFAULT_CATALOG[-3:]
    assert "exact?" in _DEFAULT_CATALOG[-3:]


def test_close_with_first_tactic(tmp_path: Path):
    v = _make_validator(success_tactics={"trivial"})
    ok, body, err = try_deterministic_close_aux(
        lean_file=tmp_path / "f.lean",
        aux_name="aux1",
        aux_signature="theorem aux1 : True := by sorry",
        validator=v,
    )
    assert ok is True
    assert body == "trivial"
    assert err == ""


def test_close_with_middle_tactic(tmp_path: Path):
    v = _make_validator(success_tactics={"linarith"})
    ok, body, err = try_deterministic_close_aux(
        lean_file=tmp_path / "f.lean",
        aux_name="aux2",
        aux_signature="theorem aux2 : 0 ≤ 1 := by sorry",
        validator=v,
    )
    assert ok is True
    assert body == "linarith"


def test_no_tactic_closes(tmp_path: Path):
    v = _make_validator(success_tactics=set(), err_text="lake_err")
    ok, body, err = try_deterministic_close_aux(
        lean_file=tmp_path / "f.lean",
        aux_name="aux3",
        aux_signature="theorem aux3 : Goldbach := by sorry",
        validator=v,
    )
    assert ok is False
    assert body == ""
    # last_err is propagated for the Leanstral retry.
    assert err  # non-empty
    assert "lake_err" in err


def test_first_match_wins_over_later_match(tmp_path: Path):
    # `decide` is later in the catalog than `rfl`; both would close.
    v = _make_validator(success_tactics={"rfl", "decide"})
    ok, body, _ = try_deterministic_close_aux(
        lean_file=tmp_path / "f.lean",
        aux_name="aux4",
        aux_signature="theorem aux4 : 1 + 1 = 2 := by sorry",
        validator=v,
    )
    assert ok is True
    assert body == "rfl"


def test_empty_aux_name(tmp_path: Path):
    v = _make_validator(success_tactics={"trivial"})
    ok, body, err = try_deterministic_close_aux(
        lean_file=tmp_path / "f.lean",
        aux_name="",
        aux_signature="theorem _ : True := by sorry",
        validator=v,
    )
    assert ok is False
    assert err == "empty_aux_input"


def test_empty_signature(tmp_path: Path):
    v = _make_validator(success_tactics={"trivial"})
    ok, body, err = try_deterministic_close_aux(
        lean_file=tmp_path / "f.lean",
        aux_name="aux5",
        aux_signature="",
        validator=v,
    )
    assert ok is False
    assert err == "empty_aux_input"


def test_validator_exception_swallowed(tmp_path: Path):
    # If a tactic causes the validator to raise, the loop continues.
    def _v(lean_file: Path, theorem_name: str, proof_body: str) -> tuple[bool, str]:
        if proof_body == "trivial":
            raise RuntimeError("boom")
        if proof_body == "rfl":
            return True, ""
        return False, "lake"

    ok, body, _ = try_deterministic_close_aux(
        lean_file=tmp_path / "f.lean",
        aux_name="aux6",
        aux_signature="theorem aux6 : 1 = 1 := by sorry",
        validator=_v,
    )
    assert ok is True
    assert body == "rfl"


def test_custom_catalog(tmp_path: Path):
    v = _make_validator(success_tactics={"my_tac"})
    ok, body, _ = try_deterministic_close_aux(
        lean_file=tmp_path / "f.lean",
        aux_name="aux7",
        aux_signature="theorem aux7 : ZZZ := by sorry",
        validator=v,
        catalog=("trivial", "my_tac", "aesop"),
    )
    assert ok is True
    assert body == "my_tac"


def test_catalog_summary_shape():
    s = catalog_summary()
    assert s["n_tactics"] == len(_DEFAULT_CATALOG)
    assert s["tactics"][0] == "trivial"
    assert isinstance(s["tactics"], list)


def test_blank_tactic_in_catalog_is_skipped(tmp_path: Path):
    v = _make_validator(success_tactics={"rfl"})
    ok, body, _ = try_deterministic_close_aux(
        lean_file=tmp_path / "f.lean",
        aux_name="aux8",
        aux_signature="theorem aux8 : 0 = 0 := by sorry",
        validator=v,
        catalog=("", "   ", "rfl"),
    )
    assert ok is True
    assert body == "rfl"
