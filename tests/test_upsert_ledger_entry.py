"""Tests for the rank-aware, name-normalised `upsert_ledger_entry` flow.

Both safeguards landed during the multi-paper closure-grind round to prevent
two regression modes:

1. **Name normalisation**: `prove_arxiv_batch` writes back with `thm.full_name`
   (e.g., `ArxivPaper.lem_X`) but the original ledger entry was written with
   the bare theorem_id (`lem_X`). Without normalisation, a re-prove appends
   a duplicate row instead of replacing the existing one.

2. **Rank-aware overwrite**: when a re-prove fails the statement-fidelity
   gate (writes TRANSLATION_LIMITED) it must NOT clobber an already-proven
   row. Without this guard, multi-paper sweeps silently regress proven rows
   to TL.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


def _save_ledger(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"entries": entries}, indent=2), encoding="utf-8")


def _read_entries(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())["entries"]


def test_normalised_theorem_name_strips_namespace_prefix() -> None:
    from pipeline_status import _normalised_theorem_name
    assert _normalised_theorem_name("ArxivPaper.lem_X") == "lem_X"
    assert _normalised_theorem_name("lem_X") == "lem_X"
    assert _normalised_theorem_name("A.B.C") == "C"
    assert _normalised_theorem_name("") == ""
    assert _normalised_theorem_name(None) == ""


def test_status_rank_orders_canonically() -> None:
    """The status-rank lookup must implement FULLY_PROVEN > AXIOM_BACKED >
    INTERMEDIARY_PROVEN > UNRESOLVED > FLAWED > TRANSLATION_LIMITED."""
    from pipeline_status import _STATUS_RANK
    assert _STATUS_RANK["FULLY_PROVEN"] > _STATUS_RANK["AXIOM_BACKED"]
    assert _STATUS_RANK["AXIOM_BACKED"] > _STATUS_RANK["INTERMEDIARY_PROVEN"]
    assert _STATUS_RANK["INTERMEDIARY_PROVEN"] > _STATUS_RANK["UNRESOLVED"]
    assert _STATUS_RANK["UNRESOLVED"] > _STATUS_RANK["FLAWED"]
    assert _STATUS_RANK["FLAWED"] > _STATUS_RANK["TRANSLATION_LIMITED"]


def test_upsert_matches_namespaced_writes_to_bare_existing(tmp_path: Path, monkeypatch) -> None:
    """A write with `theorem_name='ArxivPaper.lem_X'` must overwrite the
    existing `lem_X` row, not append a duplicate. The existing bare name is
    preserved on overwrite (downstream tools may join on it). This is the
    invariant that prevents the 27→70-row inflation we saw on 2604.21884."""
    from pipeline_status import upsert_ledger_entry
    from pipeline_status_models import (
        FailureKind,
        ProofMethod,
        StepVerdict,
        VerificationStatus,
    )

    ledger_dir = tmp_path / "output" / "verification_ledgers"
    ledger_path = ledger_dir / "0000.99999.json"
    _save_ledger(ledger_path, [{
        "theorem_name": "lem_X",
        "lean_file": "output/0000.99999.lean",
        "lean_statement": "theorem lem_X : True := by trivial",
        # Start at UNRESOLVED so any subsequent write (>= UNRESOLVED) overwrites.
        "status": VerificationStatus.UNRESOLVED.value,
    }])
    import pipeline_status as PS
    monkeypatch.setattr(PS, "_LEDGER_DIR", ledger_dir)

    from pipeline_status import build_ledger_entry
    new_entry = build_ledger_entry(
        theorem_name="ArxivPaper.lem_X",
        lean_file="output/0000.99999.lean",
        lean_statement="theorem ArxivPaper.lem_X : True := by trivial",
        proved=True,
        step_records=[],
        proof_text="trivial",
        error_message="",
        proof_mode="state-mcts",
        proof_method=ProofMethod.LEAN_VERIFIED,
        rounds_used=1,
        time_s=1.0,
        had_exception=False,
        failure_kind=FailureKind.UNKNOWN,
    )
    upsert_ledger_entry("0000.99999", new_entry, output_root=tmp_path / "output")

    entries = _read_entries(ledger_path)
    assert len(entries) == 1, f"namespaced write should replace bare row, got {len(entries)} rows"
    # Bare name preserved on overwrite (caller should not silently rebrand the row).
    assert entries[0]["theorem_name"] == "lem_X"


def test_upsert_does_not_demote_higher_rank(tmp_path: Path, monkeypatch) -> None:
    """A new TRANSLATION_LIMITED write must NOT overwrite an existing
    FULLY_PROVEN row. Without this guard, a follow-up sweep that fails the
    fidelity gate silently regresses proven rows."""
    from pipeline_status import upsert_ledger_entry
    from pipeline_status_models import (
        FailureKind,
        ProofMethod,
        StepVerdict,
        VerificationStatus,
    )

    ledger_dir = tmp_path / "output" / "verification_ledgers"
    ledger_path = ledger_dir / "0000.99998.json"
    _save_ledger(ledger_path, [{
        "theorem_name": "good_thm",
        "lean_file": "output/0000.99998.lean",
        "lean_statement": "theorem good_thm : True := by trivial",
        "status": VerificationStatus.FULLY_PROVEN.value,
        "proof_text": "trivial",
        "step_verdict": StepVerdict.VERIFIED.value,
    }])
    import pipeline_status as PS
    monkeypatch.setattr(PS, "_LEDGER_DIR", ledger_dir)

    from pipeline_status import build_ledger_entry
    bad_write = build_ledger_entry(
        theorem_name="ArxivPaper.good_thm",  # namespaced; matches bare via norm
        lean_file="output/0000.99998.lean",
        lean_statement="theorem good_thm : False := by sorry",
        proved=False,
        step_records=[],
        proof_text="",
        error_message="statement_fidelity_gate_blocked",
        proof_mode="statement-fidelity-gate",
        proof_method=ProofMethod.TRANSLATION_LIMITED,
        rounds_used=0,
        time_s=0.0,
        had_exception=False,
        failure_kind=FailureKind.TRANSLATION_FAILURE,
    )
    bad_write.status = VerificationStatus.TRANSLATION_LIMITED
    upsert_ledger_entry("0000.99998", bad_write, output_root=tmp_path / "output")

    entries = _read_entries(ledger_path)
    assert len(entries) == 1
    # FULLY_PROVEN must be preserved.
    assert entries[0]["status"] == "FULLY_PROVEN"
    assert entries[0]["theorem_name"] == "good_thm"


