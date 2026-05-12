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


def test_mark_respects_entry_lean_file_when_canonical_missing(tmp_path: Path) -> None:
    """Regression: rows whose own `lean_file` (probe/alternate-output path) is
    still on disk must NOT be classified as ghost translations, even when the
    canonical `<paper_id>.lean` is absent.

    This was the source of 53 spurious `lean_file_missing_post_translate`
    rows in the verification ledger: probe runs wrote real `.lean` artefacts
    to `output/pipeline_probe/<id>_realbatch2.lean`, but the marker only
    checked the canonical `output/<paper_id>.lean` path and falsely
    reclassified every translated theorem in those papers.
    """
    ledger_dir = tmp_path / "ledgers"
    lean_dir = tmp_path / "lean"  # canonical
    probe_dir = tmp_path / "probe"  # alternate-output path
    lean_dir.mkdir()
    probe_dir.mkdir()
    probe_file = probe_dir / "p1_realbatch.lean"
    probe_file.write_text("-- exists in alternate path\n", encoding="utf-8")
    # canonical lean is absent — only the probe artefact lives on disk.

    ledger_path = ledger_dir / "p1.json"
    _write_ledger(
        ledger_path,
        [
            {
                "theorem_name": "T_probe",
                "status": "UNRESOLVED",
                "error_message": "translate-only mode",
                "lean_file": str(probe_file),  # absolute path that still exists
            },
            {
                # Second row with NO valid lean_file artefact — genuine ghost.
                "theorem_name": "T_truly_ghost",
                "status": "UNRESOLVED",
                "error_message": "translate-only mode",
                "lean_file": str(tmp_path / "definitely-gone.lean"),
            },
        ],
    )

    result = mark_ledger_file(
        ledger_path, "p1", lean_dir, write=True, project_root=tmp_path
    )
    # Only the truly-ghost row should be marked; the probe-backed one stays UNRESOLVED.
    assert result["marked"] == 1
    after = json.loads(ledger_path.read_text())["entries"]
    assert after[0]["status"] == "UNRESOLVED", "probe-backed row must NOT be reclassified"
    assert after[0].get("failure_kind", "") != "lean_file_missing_post_translate"
    assert after[1]["status"] == "TRANSLATION_LIMITED"
    assert after[1]["failure_kind"] == "lean_file_missing_post_translate"


def test_mark_respects_entry_lean_file_relative_path(tmp_path: Path) -> None:
    """Relative `lean_file` paths must be resolved against the supplied
    project_root so the existence check matches what the pipeline actually
    wrote during translation."""
    ledger_dir = tmp_path / "ledgers"
    lean_dir = tmp_path / "lean"
    lean_dir.mkdir()
    # canonical missing; relative probe path exists under project root.
    rel_probe = Path("output/pipeline_probe/p2_realbatch.lean")
    abs_probe = tmp_path / rel_probe
    abs_probe.parent.mkdir(parents=True, exist_ok=True)
    abs_probe.write_text("-- relative probe artefact\n", encoding="utf-8")

    ledger_path = ledger_dir / "p2.json"
    _write_ledger(
        ledger_path,
        [
            {
                "theorem_name": "T_rel",
                "status": "UNRESOLVED",
                "error_message": "translate-only mode",
                "lean_file": str(rel_probe),  # relative path
            }
        ],
    )

    result = mark_ledger_file(
        ledger_path, "p2", lean_dir, write=True, project_root=tmp_path
    )
    assert result["marked"] == 0, "relative-path probe artefact should not be classified as ghost"
    after = json.loads(ledger_path.read_text())["entries"][0]
    assert after["status"] == "UNRESOLVED"
