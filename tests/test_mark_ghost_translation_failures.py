"""Tests for mark_ghost_translation_failures."""

from __future__ import annotations

import json
from pathlib import Path

from mark_ghost_translation_failures import is_ghost_row, mark_ledger_file


def _write_ledger(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"entries": entries}, indent=2), encoding="utf-8")


def test_is_ghost_translate_only_message() -> None:
    """Row with status=UNRESOLVED + error_message='translate-only mode' is a ghost."""
    assert is_ghost_row({"status": "UNRESOLVED", "error_message": "translate-only mode"})


def test_is_ghost_no_proof_attempt() -> None:
    """Row with rounds_used=0 and unknown proof_method is a ghost."""
    assert is_ghost_row({"status": "UNRESOLVED", "rounds_used": 0, "proof_method": "unknown"})
    assert is_ghost_row({"status": "UNRESOLVED", "rounds_used": 0, "proof_method": ""})


def test_is_not_ghost_when_proof_attempted() -> None:
    """Row with rounds_used > 0 and proof_method='lean_verified' is NOT a ghost."""
    assert not is_ghost_row({
        "status": "UNRESOLVED",
        "rounds_used": 15,
        "proof_method": "lean_verified",
        "error_message": "proof-given-up",
    })


def test_is_not_ghost_when_status_not_unresolved() -> None:
    """Closed rows (FP/AB/IP/FLAWED) are never ghosts."""
    for status in ("FULLY_PROVEN", "AXIOM_BACKED", "INTERMEDIARY_PROVEN", "FLAWED"):
        assert not is_ghost_row({"status": status, "error_message": "translate-only mode"})


def test_mark_skips_when_lean_file_present(tmp_path: Path) -> None:
    """Even ghost-shaped rows must NOT be marked if the .lean file still exists.
    The .lean's presence means the row may be legitimately UNRESOLVED for a
    proof-search reason, not a translation failure."""
    ledger_dir = tmp_path / "ledgers"
    lean_dir = tmp_path / "lean"
    lean_dir.mkdir()
    (lean_dir / "p1.lean").write_text("-- exists\n", encoding="utf-8")
    ledger_path = ledger_dir / "p1.json"
    _write_ledger(ledger_path, [{"theorem_name": "T1", "status": "UNRESOLVED", "error_message": "translate-only mode"}])

    result = mark_ledger_file(ledger_path, "p1", lean_dir, write=True)
    assert result["marked"] == 0
    assert result["lean_file_missing"] is False
    after = json.loads(ledger_path.read_text())["entries"][0]
    assert after["status"] == "UNRESOLVED"


def test_mark_writes_when_lean_file_missing(tmp_path: Path) -> None:
    """Ghost rows in a paper with a missing .lean file get reclassified to
    TRANSLATION_LIMITED."""
    ledger_dir = tmp_path / "ledgers"
    lean_dir = tmp_path / "lean"
    lean_dir.mkdir()
    # No .lean file written.
    ledger_path = ledger_dir / "p1.json"
    _write_ledger(
        ledger_path,
        [
            {"theorem_name": "T1", "status": "UNRESOLVED", "error_message": "translate-only mode"},
            {"theorem_name": "T2", "status": "FULLY_PROVEN"},  # already closed; untouched
        ],
    )

    result = mark_ledger_file(ledger_path, "p1", lean_dir, write=True)
    assert result["marked"] == 1
    assert result["lean_file_missing"] is True
    after = json.loads(ledger_path.read_text())["entries"]
    assert after[0]["status"] == "TRANSLATION_LIMITED"
    assert after[0]["proof_method"] == "translation_limited"
    assert after[0]["failure_origin"] == "FORMALIZATION_ERROR"
    assert after[1]["status"] == "FULLY_PROVEN"


def test_mark_dry_run_does_not_write(tmp_path: Path) -> None:
    ledger_dir = tmp_path / "ledgers"
    lean_dir = tmp_path / "lean"
    lean_dir.mkdir()
    ledger_path = ledger_dir / "p1.json"
    _write_ledger(ledger_path, [{"theorem_name": "T1", "status": "UNRESOLVED", "error_message": "translate-only mode"}])

    result = mark_ledger_file(ledger_path, "p1", lean_dir, write=False)
    assert result["marked"] == 1
    after = json.loads(ledger_path.read_text())["entries"][0]
    # File unchanged in dry-run mode.
    assert after["status"] == "UNRESOLVED"


def test_mark_is_idempotent(tmp_path: Path) -> None:
    """Running the marker twice yields zero additional marks the second time
    (since marked rows now have status=TRANSLATION_LIMITED, not UNRESOLVED)."""
    ledger_dir = tmp_path / "ledgers"
    lean_dir = tmp_path / "lean"
    lean_dir.mkdir()
    ledger_path = ledger_dir / "p1.json"
    _write_ledger(ledger_path, [{"theorem_name": "T1", "status": "UNRESOLVED", "error_message": "translate-only mode"}])

    first = mark_ledger_file(ledger_path, "p1", lean_dir, write=True)
    assert first["marked"] == 1
    second = mark_ledger_file(ledger_path, "p1", lean_dir, write=True)
    assert second["marked"] == 0
