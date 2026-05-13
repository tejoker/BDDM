"""Tests for audit_fully_proven_integrity.

These tests pin the audit script's invariants:

  - A FULLY_PROVEN row whose `.lean` body is `sorry` IS demoted, and the
    demotion mutates exactly the fields the audit promises (status,
    proved, step_verdict, failure_kind, validation_gates, audit_demotion).
  - Term-mode declarations (`:= rfl`, `:= Iff.rfl`, …) are SKIPPED. They
    have no `:= by` block; the regex must not falsely demote them.
  - `__audited_core` replacements (and the `superseded_by_audited_core`
    generated diagnostic that retired) are SKIPPED — their proof source
    is not `output/<id>.lean`.
  - Single-line bodies (`theorem X := False := by sorry`) are correctly
    classified as sorry-bearing. The legacy regex only matched
    `:= by\\n  sorry`; this regression test prevents it from recurring.
  - The audit is idempotent: running it twice yields zero additional
    demotions on the second pass.
"""

from __future__ import annotations

import json
from pathlib import Path

from audit_fully_proven_integrity import (
    _body_is_sorry,
    _is_audited_core_row,
    _theorem_body_in_file,
    audit_ledger_entries,
    audit_ledger_file,
    audit_paper,
)


# ---------------------------------------------------------------------------
# Body inspection
# ---------------------------------------------------------------------------


def test_body_is_sorry_detects_bare_sorry() -> None:
    assert _body_is_sorry("sorry") is True
    assert _body_is_sorry("  sorry") is True
    assert _body_is_sorry("sorry\n\n-- next theorem\n") is True


def test_body_is_sorry_ignores_comments_before_sorry() -> None:
    assert _body_is_sorry("-- TODO\nsorry") is True


def test_body_is_sorry_returns_false_on_real_tactics() -> None:
    assert _body_is_sorry("simp\n  rfl") is False
    assert _body_is_sorry("aesop") is False
    # `sorryAx` is not `sorry` (a different identifier).
    assert _body_is_sorry("sorryAx") is False


def test_body_is_sorry_returns_false_on_empty() -> None:
    assert _body_is_sorry("") is False
    assert _body_is_sorry("   \n\n") is False


# ---------------------------------------------------------------------------
# Theorem body extraction
# ---------------------------------------------------------------------------


def test_extract_body_multiline_sorry() -> None:
    src = "theorem foo (x : Nat) :\n    x = x := by\n  sorry\n\n-- next\n"
    body = _theorem_body_in_file(src, "foo")
    assert body is not None
    assert _body_is_sorry(body) is True


def test_extract_body_single_line_sorry() -> None:
    """Same-line `:= by sorry` (e.g. `theorem Beta : False := by sorry`)
    must be classified as sorry-bearing."""
    src = "theorem Beta : False := by sorry\n\n-- next\n"
    body = _theorem_body_in_file(src, "Beta")
    assert body is not None
    assert _body_is_sorry(body) is True


def test_extract_body_returns_none_for_term_mode() -> None:
    """`:= rfl` term-mode proofs have no `:= by` block; the audit must
    skip them so legitimate compile-checked definitions are not flagged."""
    src = "theorem def_bal (n : Nat) : n = n := rfl\n"
    body = _theorem_body_in_file(src, "def_bal")
    assert body is None


def test_extract_body_returns_none_for_iff_rfl() -> None:
    src = "theorem def_ci : True ↔ True := Iff.rfl\n"
    body = _theorem_body_in_file(src, "def_ci")
    assert body is None


def test_extract_body_returns_none_for_missing_name() -> None:
    src = "theorem foo : True := by trivial\n"
    assert _theorem_body_in_file(src, "bar") is None


def test_extract_body_finds_real_proof() -> None:
    src = "theorem foo : 1 + 1 = 2 := by\n  decide\n"
    body = _theorem_body_in_file(src, "foo")
    assert body is not None
    assert _body_is_sorry(body) is False


# ---------------------------------------------------------------------------
# Audited-core classification
# ---------------------------------------------------------------------------


def test_audited_core_recognized_by_name_suffix() -> None:
    assert _is_audited_core_row({"theorem_name": "foo__audited_core"}) is True


def test_audited_core_recognized_by_ledger_role() -> None:
    assert _is_audited_core_row({"theorem_name": "foo", "ledger_role": "audited_core_replacement"}) is True


def test_audited_core_recognized_by_proof_mode() -> None:
    assert _is_audited_core_row({"theorem_name": "foo", "proof_mode": "audited-core-replacement"}) is True


def test_audited_core_recognized_by_superseded_flag() -> None:
    """The `generated_diagnostic` row that has been retired must be
    skipped: its FP status is a trace artefact, not a fraudulent claim."""
    assert _is_audited_core_row({"theorem_name": "foo", "superseded_by_audited_core": True}) is True


def test_audited_core_not_recognized_for_plain_row() -> None:
    assert _is_audited_core_row({"theorem_name": "foo", "ledger_role": "generated"}) is False


# ---------------------------------------------------------------------------
# Row-level demotion
# ---------------------------------------------------------------------------


def _entries_with_one_fp(name: str, proof_text: str) -> list[dict]:
    return [
        {
            "theorem_name": name,
            "status": "FULLY_PROVEN",
            "proved": True,
            "step_verdict": "VERIFIED",
            "proof_text": proof_text,
            "proof_method": "lean_verified",
            "validation_gates": {
                "lean_proof_closed": True,
                "step_verdict_verified": True,
            },
            "gate_failures": [],
        }
    ]


def test_audit_demotes_sorry_body() -> None:
    src = "theorem foo : True := by\n  sorry\n"
    entries = _entries_with_one_fp("foo", "aesop")
    result = audit_ledger_entries(entries, paper_id="px", lean_src=src)
    assert result.fp_pre == 1
    assert result.demoted == 1
    assert result.fp_post == 0
    e = entries[0]
    # Required field mutations
    assert e["status"] == "UNRESOLVED"
    assert e["proved"] is False
    assert e["step_verdict"] == "INCOMPLETE"
    assert e["failure_kind"] == "proof_search_unattempted"
    assert e["failure_origin"] == "PROOF_SEARCH_ERROR"
    assert e["validation_gates"]["lean_proof_closed"] is False
    assert e["validation_gates"]["step_verdict_verified"] is False
    assert "lean_proof_closed" in e["gate_failures"]
    # Forensic capture: the previously stored proof_text is preserved.
    assert e["audit_demotion"]["captured_proof_text"] == "aesop"
    assert e["audit_demotion"]["previous_status"] == "FULLY_PROVEN"


def test_audit_skips_term_mode_row() -> None:
    """A term-mode proof (`:= rfl`) must not be flagged: Lean accepted it
    at compile time, and the audit's regex finds no `:= by` block."""
    src = "theorem def_bal : True ↔ True := Iff.rfl\n"
    entries = _entries_with_one_fp("def_bal", "Iff.rfl")
    result = audit_ledger_entries(entries, paper_id="px", lean_src=src)
    assert result.demoted == 0
    assert result.term_mode_skipped == 1
    assert entries[0]["status"] == "FULLY_PROVEN"


def test_audit_skips_audited_core_row() -> None:
    src = "-- file has no theorem foo__audited_core anywhere\n"
    entries = _entries_with_one_fp("foo__audited_core", "calc ...")
    entries[0]["ledger_role"] = "audited_core_replacement"
    result = audit_ledger_entries(entries, paper_id="px", lean_src=src)
    assert result.demoted == 0
    assert result.audited_core_skipped == 1
    assert entries[0]["status"] == "FULLY_PROVEN"


def test_audit_skips_superseded_generated_diagnostic() -> None:
    """A `superseded_by_audited_core=True` row stays FP for trace continuity."""
    src = "-- canonical lean file content omitted\n"
    entries = _entries_with_one_fp("prop_sharpness", "DyadicBlockBound_sharpness alpha")
    entries[0]["superseded_by_audited_core"] = True
    entries[0]["ledger_role"] = "generated_diagnostic"
    result = audit_ledger_entries(entries, paper_id="px", lean_src=src)
    assert result.demoted == 0
    assert result.audited_core_skipped == 1
    assert entries[0]["status"] == "FULLY_PROVEN"


def test_audit_preserves_clean_row() -> None:
    src = "theorem foo : 1 + 1 = 2 := by\n  decide\n"
    entries = _entries_with_one_fp("foo", "decide")
    result = audit_ledger_entries(entries, paper_id="px", lean_src=src)
    assert result.demoted == 0
    assert result.validated_clean == 1
    assert entries[0]["status"] == "FULLY_PROVEN"


def test_audit_single_line_sorry_demotes(tmp_path: Path) -> None:
    """Regression: `theorem Beta : False := by sorry` on one line MUST
    be demoted. The earlier regex required a newline after `by`."""
    src = "theorem Beta : False := by sorry\n\n-- next\n"
    entries = _entries_with_one_fp("Beta", "omega")
    result = audit_ledger_entries(entries, paper_id="px", lean_src=src)
    assert result.demoted == 1
    assert entries[0]["status"] == "UNRESOLVED"


# ---------------------------------------------------------------------------
# File-level audit (with tmp_path)
# ---------------------------------------------------------------------------


def test_audit_ledger_file_writes_only_when_flag_set(tmp_path: Path) -> None:
    """Dry-run mode (write=False) must not touch the ledger file."""
    lean_path = tmp_path / "p.lean"
    lean_path.write_text("theorem foo : True := by\n  sorry\n", encoding="utf-8")
    ledger_path = tmp_path / "p.json"
    ledger_path.write_text(
        json.dumps({"entries": _entries_with_one_fp("foo", "aesop")}, indent=2),
        encoding="utf-8",
    )
    pre = ledger_path.read_text(encoding="utf-8")
    result = audit_ledger_file(ledger_path, lean_path, paper_id="p", write=False)
    assert result.demoted == 1
    # File contents unchanged in dry-run mode.
    assert ledger_path.read_text(encoding="utf-8") == pre


def test_audit_ledger_file_persists_with_write(tmp_path: Path) -> None:
    lean_path = tmp_path / "p.lean"
    lean_path.write_text("theorem foo : True := by\n  sorry\n", encoding="utf-8")
    ledger_path = tmp_path / "p.json"
    ledger_path.write_text(
        json.dumps({"entries": _entries_with_one_fp("foo", "aesop")}, indent=2),
        encoding="utf-8",
    )
    result = audit_ledger_file(ledger_path, lean_path, paper_id="p", write=True)
    assert result.demoted == 1
    after = json.loads(ledger_path.read_text(encoding="utf-8"))
    e = after["entries"][0]
    assert e["status"] == "UNRESOLVED"
    assert e["audit_demotion"]["previous_status"] == "FULLY_PROVEN"


def test_audit_is_idempotent(tmp_path: Path) -> None:
    """Running the audit twice yields zero demotions on the second pass."""
    lean_path = tmp_path / "p.lean"
    lean_path.write_text("theorem foo : True := by\n  sorry\n", encoding="utf-8")
    ledger_path = tmp_path / "p.json"
    ledger_path.write_text(
        json.dumps({"entries": _entries_with_one_fp("foo", "aesop")}, indent=2),
        encoding="utf-8",
    )
    first = audit_ledger_file(ledger_path, lean_path, paper_id="p", write=True)
    assert first.demoted == 1
    second = audit_ledger_file(ledger_path, lean_path, paper_id="p", write=True)
    assert second.demoted == 0
    assert second.fp_pre == 0  # The row is no longer FP.


def test_audit_paper_audits_both_ephemeral_and_canonical(tmp_path: Path) -> None:
    """`audit_paper` must demote in BOTH the ephemeral and canonical ledger
    so committed evidence and ephemeral state remain in sync after the fix."""
    lean_dir = tmp_path / "output"
    ledger_dir = tmp_path / "output" / "verification_ledgers"
    repro_dir = tmp_path / "reproducibility" / "full_paper_reports"
    lean_dir.mkdir(parents=True)
    ledger_dir.mkdir(parents=True)
    repro_dir.mkdir(parents=True)
    (lean_dir / "p1.lean").write_text("theorem foo : True := by\n  sorry\n", encoding="utf-8")
    ephem = ledger_dir / "p1.json"
    canonical = repro_dir / "p1"
    canonical.mkdir()
    payload = json.dumps({"entries": _entries_with_one_fp("foo", "aesop")}, indent=2)
    ephem.write_text(payload, encoding="utf-8")
    (canonical / "verification_ledger.json").write_text(payload, encoding="utf-8")

    out = audit_paper(
        "p1",
        ledger_dir=ledger_dir,
        lean_dir=lean_dir,
        repro_dir=repro_dir,
        write=True,
    )
    assert out["ephemeral"]["demoted"] == 1
    assert out["canonical"]["demoted"] == 1
    eph_after = json.loads(ephem.read_text(encoding="utf-8"))
    can_after = json.loads((canonical / "verification_ledger.json").read_text(encoding="utf-8"))
    assert eph_after["entries"][0]["status"] == "UNRESOLVED"
    assert can_after["entries"][0]["status"] == "UNRESOLVED"


def test_audit_paper_skips_when_lean_file_missing(tmp_path: Path) -> None:
    """If `output/<id>.lean` is missing, the audit returns a skip marker
    instead of mutating the ledger. (Pipeline state can legitimately have
    ledgers without canonical lean files during early translation rounds.)"""
    ledger_dir = tmp_path / "ledgers"
    lean_dir = tmp_path / "output"
    repro_dir = tmp_path / "repro"
    ledger_dir.mkdir(); lean_dir.mkdir(); repro_dir.mkdir()
    out = audit_paper(
        "p1",
        ledger_dir=ledger_dir,
        lean_dir=lean_dir,
        repro_dir=repro_dir,
        write=True,
    )
    assert out["skipped"] == "lean_file_missing"
