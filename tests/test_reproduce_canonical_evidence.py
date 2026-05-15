"""Tests for `scripts/reproduce_canonical_evidence.py`.

These tests pin the verifier's invariants:

  - A FP/AB/IP row backed by a real tactic-mode body is CREDITED.
  - A FP row whose `.lean` body is `sorry` is REPORTED AS A MISMATCH and
    drops the CLI exit code to non-zero (the script's whole point).
  - Term-mode declarations are CREDITED (Lean accepted them).
  - Curated `__audited_core` rows are CREDITED without scanning the file.
  - A theorem named in the ledger but absent from the file is a MISMATCH
    (the published .lean must back every promotion claim).
  - The `--paper-id` filter restricts to the requested papers.
  - The rendered table is parseable: header + one row per paper + TOTAL.
  - A trivialized `lean_statement` is a MISMATCH even when the body looks
    real (matches the audit's anti-bypass behaviour).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from reproduce_canonical_evidence import (  # noqa: E402
    PaperReport,
    RowVerdict,
    TierTally,
    build_summary,
    main,
    render_table,
    verify_paper,
    verify_row,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_canonical_paper(
    tmp_path: Path,
    paper_id: str,
    *,
    entries: list[dict],
    lean_body: str,
) -> tuple[Path, Path]:
    """Create a minimal canonical layout under tmp_path.

    Returns (repro_dir, lean_dir).
    """
    repro_dir = tmp_path / "repro"
    lean_dir = tmp_path / "lean"
    paper_dir = repro_dir / paper_id
    paper_dir.mkdir(parents=True)
    lean_dir.mkdir(parents=True)
    (paper_dir / "verification_ledger.json").write_text(
        json.dumps({"entries": entries}, indent=2),
        encoding="utf-8",
    )
    (lean_dir / f"{paper_id}.lean").write_text(lean_body, encoding="utf-8")
    return repro_dir, lean_dir


def _fp_entry(name: str, *, proof_text: str = "decide", stmt: str | None = None) -> dict:
    """Build a synthetic FP ledger row.

    The default `lean_statement` is a fully-formed `theorem` declaration so
    the trivialization detector treats it as a real claim. Tests that
    deliberately want triviality stub their own trivialized callable.
    """
    if stmt is None:
        stmt = f"theorem {name} : 1 + 1 = 2"
    return {
        "theorem_name": name,
        "status": "FULLY_PROVEN",
        "proved": True,
        "step_verdict": "VERIFIED",
        "proof_text": proof_text,
        "lean_statement": stmt,
        "validation_gates": {"lean_proof_closed": True},
    }


# ---------------------------------------------------------------------------
# Unit-level row verdicts
# ---------------------------------------------------------------------------


def test_verify_row_credits_real_body() -> None:
    """A real tactic-mode body is credited."""
    src = "theorem foo : 1 + 1 = 2 := by\n  decide\n"
    entry = _fp_entry("foo")
    v = verify_row(entry, paper_id="px", lean_src=src, trivialized=lambda _: False)
    assert v.verified is True
    assert v.reason == "body_ok"


def test_verify_row_flags_sorry_body() -> None:
    """`:= by sorry` is a mismatch even though Lean compiles it."""
    src = "theorem foo : 1 + 1 = 2 := by\n  sorry\n"
    entry = _fp_entry("foo")
    v = verify_row(entry, paper_id="px", lean_src=src, trivialized=lambda _: False)
    assert v.verified is False
    assert v.reason == "sorry_body"


def test_verify_row_credits_term_mode() -> None:
    """Term-mode `:= rfl` has no `:= by` block but Lean has accepted it."""
    src = "theorem foo : True ↔ True := Iff.rfl\n"
    entry = _fp_entry("foo", proof_text="Iff.rfl")
    v = verify_row(entry, paper_id="px", lean_src=src, trivialized=lambda _: False)
    assert v.verified is True
    assert v.reason == "term_mode"


def test_verify_row_credits_audited_core() -> None:
    """`__audited_core` replacements are credited without file scan — their
    proof source lives in `Desol/PaperProofs/...`, not `output/<id>.lean`."""
    src = "-- file has no curated theorem\n"
    entry = _fp_entry("foo__audited_core")
    entry["ledger_role"] = "audited_core_replacement"
    v = verify_row(entry, paper_id="px", lean_src=src, trivialized=lambda _: False)
    assert v.verified is True
    assert v.reason == "audited_core"


def test_verify_row_flags_theorem_missing() -> None:
    src = "theorem other : True := by trivial\n"
    entry = _fp_entry("foo")
    v = verify_row(entry, paper_id="px", lean_src=src, trivialized=lambda _: False)
    assert v.verified is False
    assert v.reason == "theorem_missing"


def test_verify_row_flags_trivialized_statement() -> None:
    """A vacuous claim is unverified even when its body is non-sorry."""
    src = "theorem foo : True := by trivial\n"
    entry = _fp_entry("foo", proof_text="trivial", stmt="theorem foo : True")
    # Force the trivialization detector to fire.
    v = verify_row(entry, paper_id="px", lean_src=src, trivialized=lambda _: True)
    assert v.verified is False
    assert v.reason == "trivialized_statement"


def test_verify_row_handles_namespace_fallback() -> None:
    """Ledger row `ArxivPaper.foo` must locate bare `theorem foo`."""
    src = (
        "namespace ArxivPaper\n"
        "theorem foo : 1 + 1 = 2 := by\n  decide\n"
        "end ArxivPaper\n"
    )
    entry = _fp_entry("ArxivPaper.foo")
    v = verify_row(entry, paper_id="px", lean_src=src, trivialized=lambda _: False)
    assert v.verified is True
    assert v.reason == "body_ok"


# ---------------------------------------------------------------------------
# Paper-level happy path and mismatches
# ---------------------------------------------------------------------------


def test_verify_paper_happy_path(tmp_path: Path) -> None:
    entries = [
        _fp_entry("foo", proof_text="decide"),
        {**_fp_entry("bar"), "status": "AXIOM_BACKED"},
        {**_fp_entry("baz"), "status": "INTERMEDIARY_PROVEN"},
    ]
    src = (
        "theorem foo : 1 + 1 = 2 := by decide\n"
        "theorem bar : 1 + 1 = 2 := by decide\n"
        "theorem baz : 1 + 1 = 2 := by decide\n"
    )
    repro, lean = _make_canonical_paper(tmp_path, "9999.99999", entries=entries, lean_body=src)
    report = verify_paper(
        "9999.99999",
        repro_dir=repro,
        lean_dir=lean,
        trivialized=lambda _: False,
    )
    assert report.mismatches == 0
    assert report.tiers["FULLY_PROVEN"].claimed == 1
    assert report.tiers["FULLY_PROVEN"].verified == 1
    assert report.tiers["AXIOM_BACKED"].claimed == 1
    assert report.tiers["AXIOM_BACKED"].verified == 1
    assert report.tiers["INTERMEDIARY_PROVEN"].claimed == 1
    assert report.tiers["INTERMEDIARY_PROVEN"].verified == 1


def test_verify_paper_sorry_body_creates_mismatch(tmp_path: Path) -> None:
    entries = [_fp_entry("foo")]
    src = "theorem foo : 1 + 1 = 2 := by\n  sorry\n"
    repro, lean = _make_canonical_paper(tmp_path, "9999.99999", entries=entries, lean_body=src)
    report = verify_paper(
        "9999.99999",
        repro_dir=repro,
        lean_dir=lean,
        trivialized=lambda _: False,
    )
    assert report.mismatches == 1
    assert report.tiers["FULLY_PROVEN"].claimed == 1
    assert report.tiers["FULLY_PROVEN"].verified == 0
    assert report.rows[0].reason == "sorry_body"


def test_verify_paper_missing_lean_file(tmp_path: Path) -> None:
    repro_dir = tmp_path / "repro"
    lean_dir = tmp_path / "lean"
    paper_dir = repro_dir / "9999.99999"
    paper_dir.mkdir(parents=True)
    lean_dir.mkdir(parents=True)
    (paper_dir / "verification_ledger.json").write_text(
        json.dumps({"entries": [_fp_entry("foo")]}),
        encoding="utf-8",
    )
    report = verify_paper(
        "9999.99999",
        repro_dir=repro_dir,
        lean_dir=lean_dir,
        trivialized=lambda _: False,
    )
    assert report.lean_file_present is False
    assert report.mismatches == 1
    assert report.rows[0].reason == "lean_file_missing"


# ---------------------------------------------------------------------------
# CLI shape: table, JSON, exit codes, per-paper filter
# ---------------------------------------------------------------------------


def test_render_table_is_parseable(tmp_path: Path) -> None:
    """The table has a header row, one row per paper, and a TOTAL row.

    Each row has at least 8 whitespace-separated fields. This makes the
    table easy to grep / parse in CI consumers that don't want to depend
    on the JSON output.
    """
    reports = [
        PaperReport(
            paper_id="9999.99999",
            tiers={
                "FULLY_PROVEN": TierTally(claimed=2, verified=2),
                "AXIOM_BACKED": TierTally(claimed=1, verified=1),
                "INTERMEDIARY_PROVEN": TierTally(claimed=0, verified=0),
            },
        ),
    ]
    table = render_table(reports)
    lines = [ln for ln in table.splitlines() if ln.strip() and not ln.startswith("-")]
    header, body, total = lines[0], lines[1], lines[-1]
    assert "Paper" in header
    assert "Claimed-FP" in header
    assert "Verified-FP" in header
    assert "Mismatches" in header
    assert body.split()[0] == "9999.99999"
    assert total.startswith("TOTAL")
    # Body fields: paper, FPc, FPv, ABc, ABv, IPc, IPv, mism
    assert len(body.split()) == 8


def test_main_exit_zero_on_happy_path(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    entries = [_fp_entry("foo")]
    src = "theorem foo : 1 + 1 = 2 := by decide\n"
    repro, lean = _make_canonical_paper(tmp_path, "9999.99999", entries=entries, lean_body=src)
    rc = main([
        "--repro-dir", str(repro),
        "--lean-dir", str(lean),
    ])
    captured = capsys.readouterr()
    assert rc == 0, captured.out
    assert "9999.99999" in captured.out
    assert "TOTAL" in captured.out


def test_main_exit_nonzero_on_mismatch(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    entries = [_fp_entry("foo")]
    src = "theorem foo : 1 + 1 = 2 := by sorry\n"
    repro, lean = _make_canonical_paper(tmp_path, "9999.99999", entries=entries, lean_body=src)
    rc = main([
        "--repro-dir", str(repro),
        "--lean-dir", str(lean),
    ])
    captured = capsys.readouterr()
    assert rc == 1
    assert "sorry_body" in captured.out


def test_main_per_paper_filter(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """`--paper-id` restricts the verification scope."""
    src_ok = "theorem foo : True := by trivial\n"
    src_bad = "theorem bar : True := by sorry\n"
    repro_dir = tmp_path / "repro"
    lean_dir = tmp_path / "lean"
    lean_dir.mkdir(parents=True)
    for pid, entries, body in (
        ("9001.00001", [_fp_entry("foo", proof_text="trivial")], src_ok),
        ("9002.00002", [_fp_entry("bar", proof_text="sorry")], src_bad),
    ):
        paper_dir = repro_dir / pid
        paper_dir.mkdir(parents=True)
        (paper_dir / "verification_ledger.json").write_text(
            json.dumps({"entries": entries}), encoding="utf-8"
        )
        (lean_dir / f"{pid}.lean").write_text(body, encoding="utf-8")

    # Filter to the good paper -> exit 0, only one paper in output.
    rc = main([
        "--repro-dir", str(repro_dir),
        "--lean-dir", str(lean_dir),
        "--paper-id", "9001.00001",
    ])
    out = capsys.readouterr().out
    assert rc == 0
    assert "9001.00001" in out
    assert "9002.00002" not in out

    # Filter to the bad paper -> exit 1.
    rc = main([
        "--repro-dir", str(repro_dir),
        "--lean-dir", str(lean_dir),
        "--paper-id", "9002.00002",
    ])
    out = capsys.readouterr().out
    assert rc == 1
    assert "9002.00002" in out
    assert "9001.00001" not in out


def test_main_json_output_carries_totals(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    entries = [_fp_entry("foo"), {**_fp_entry("bar"), "status": "AXIOM_BACKED"}]
    src = "theorem foo : True := by trivial\ntheorem bar : True := by trivial\n"
    repro, lean = _make_canonical_paper(tmp_path, "9999.99999", entries=entries, lean_body=src)
    rc = main([
        "--repro-dir", str(repro),
        "--lean-dir", str(lean),
        "--json",
    ])
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["schema_version"] == "reproduce_canonical_evidence.v1"
    assert payload["total_mismatches"] == 0
    assert payload["totals"]["FULLY_PROVEN"]["claimed"] == 1
    assert payload["totals"]["FULLY_PROVEN"]["verified"] == 1
    assert payload["totals"]["AXIOM_BACKED"]["verified"] == 1


def test_main_json_out_writes_file(tmp_path: Path) -> None:
    entries = [_fp_entry("foo")]
    src = "theorem foo : True := by trivial\n"
    repro, lean = _make_canonical_paper(tmp_path, "9999.99999", entries=entries, lean_body=src)
    out_path = tmp_path / "summary.json"
    rc = main([
        "--repro-dir", str(repro),
        "--lean-dir", str(lean),
        "--json-out", str(out_path),
        "--quiet",
    ])
    assert rc == 0
    assert out_path.exists()
    payload = json.loads(out_path.read_text())
    assert payload["totals"]["FULLY_PROVEN"]["verified"] == 1


def test_main_returns_two_on_empty_repro_dir(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    rc = main([
        "--repro-dir", str(tmp_path / "empty"),
        "--lean-dir", str(tmp_path),
    ])
    assert rc == 2
    out = capsys.readouterr().out
    assert "no_canonical_papers" in out


def test_build_summary_aggregates_totals() -> None:
    """`build_summary` totals match the per-paper sums even when claimed != verified."""
    reports = [
        PaperReport(
            paper_id="A",
            tiers={
                "FULLY_PROVEN": TierTally(claimed=3, verified=2),
                "AXIOM_BACKED": TierTally(claimed=1, verified=1),
                "INTERMEDIARY_PROVEN": TierTally(claimed=0, verified=0),
            },
            rows=[
                RowVerdict(paper_id="A", theorem_name="x", status="FULLY_PROVEN",
                           verified=False, reason="sorry_body"),
            ],
        ),
        PaperReport(
            paper_id="B",
            tiers={
                "FULLY_PROVEN": TierTally(claimed=2, verified=2),
                "AXIOM_BACKED": TierTally(claimed=0, verified=0),
                "INTERMEDIARY_PROVEN": TierTally(claimed=1, verified=1),
            },
        ),
    ]
    summary = build_summary(reports)
    assert summary["totals"]["FULLY_PROVEN"]["claimed"] == 5
    assert summary["totals"]["FULLY_PROVEN"]["verified"] == 4
    assert summary["totals"]["AXIOM_BACKED"]["claimed"] == 1
    assert summary["totals"]["INTERMEDIARY_PROVEN"]["claimed"] == 1
    assert summary["total_mismatches"] == 1
