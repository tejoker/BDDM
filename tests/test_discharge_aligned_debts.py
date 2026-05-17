"""Hermetic tests for `scripts/discharge_aligned_debts.py`.

Pin the discharge logic: load alignments, walk AB/IP rows, match
axiom_debt entries against the alignment registry, move matched to
discharged_axiom_debt, and re-classify status based on remaining
gates.
"""
from __future__ import annotations

import json
from pathlib import Path

from discharge_aligned_debts import (
    _promotion_status,
    load_alignments,
)


# ---------------------------------------------------------------------------
# load_alignments
# ---------------------------------------------------------------------------


def test_load_alignments_missing_file(tmp_path: Path) -> None:
    out = load_alignments(tmp_path / "missing.json")
    assert out == {}


def test_load_alignments_simple(tmp_path: Path) -> None:
    p = tmp_path / "alignments.json"
    p.write_text(json.dumps({
        "alignments": [
            {"paper_id": "2604.21884", "paper_local_name": "alpha"},
            {"paper_id": "2604.21884", "paper_local_name": "beta"},
            {"paper_id": "2304.09598", "paper_local_name": "gamma"},
        ],
    }), encoding="utf-8")
    out = load_alignments(p)
    assert out == {
        "2604.21884": {"alpha", "beta"},
        "2304.09598": {"gamma"},
    }


def test_load_alignments_skips_empty_names(tmp_path: Path) -> None:
    p = tmp_path / "alignments.json"
    p.write_text(json.dumps({
        "alignments": [
            {"paper_id": "X", "paper_local_name": ""},
            {"paper_id": "", "paper_local_name": "foo"},
            {"paper_id": "X", "paper_local_name": "bar"},
        ],
    }), encoding="utf-8")
    out = load_alignments(p)
    assert out == {"X": {"bar"}}


# ---------------------------------------------------------------------------
# _promotion_status
# ---------------------------------------------------------------------------


def test_promotion_status_keeps_status_when_proof_not_closed() -> None:
    entry = {
        "status": "UNRESOLVED",
        "axiom_debt": [],
        "gate_failures": [],
        "validation_gates": {"lean_proof_closed": False},
    }
    assert _promotion_status(entry) == "UNRESOLVED"


def test_promotion_status_fp_when_all_gates_pass() -> None:
    entry = {
        "status": "AXIOM_BACKED",
        "axiom_debt": [],
        "gate_failures": [],  # axiom_debt was the only blocker
        "validation_gates": {"lean_proof_closed": True},
    }
    assert _promotion_status(entry) == "FULLY_PROVEN"


def test_promotion_status_axiom_backed_when_debt_remains() -> None:
    entry = {
        "status": "AXIOM_BACKED",
        "axiom_debt": ["paper_definition_stub:Foo"],
        "gate_failures": [],
        "validation_gates": {"lean_proof_closed": True},
    }
    assert _promotion_status(entry) == "AXIOM_BACKED"


def test_promotion_status_ip_when_other_gates_fail() -> None:
    """The key bug-fix path: AB row with axiom_debt cleared but other
    gates failing → must demote to IP, not promote to FP."""
    entry = {
        "status": "AXIOM_BACKED",
        "axiom_debt": [],
        "gate_failures": ["claim_equivalent", "no_paper_axiom_debt"],
        "validation_gates": {"lean_proof_closed": True},
    }
    assert _promotion_status(entry) == "INTERMEDIARY_PROVEN"


def test_promotion_status_excludes_proven_gates_from_failure_list() -> None:
    """`lean_proof_closed` / `step_verdict_verified` failures don't
    block status transitions because they're handled by the
    `validation_gates.lean_proof_closed` check earlier."""
    entry = {
        "status": "AXIOM_BACKED",
        "axiom_debt": [],
        "gate_failures": ["lean_proof_closed"],
        "validation_gates": {"lean_proof_closed": True},
    }
    # `lean_proof_closed` excluded from failures → empty → FP.
    assert _promotion_status(entry) == "FULLY_PROVEN"


# ---------------------------------------------------------------------------
# end-to-end via CLI
# ---------------------------------------------------------------------------


def test_main_dry_run_does_not_mutate_files(tmp_path: Path) -> None:
    """The --dry-run flag must not mutate any ledger."""
    import subprocess
    import sys

    # Construct an alignment + a matching ledger row.
    alignments = tmp_path / "alignments.json"
    alignments.write_text(json.dumps({
        "alignments": [{"paper_id": "test_pid", "paper_local_name": "Foo"}],
    }), encoding="utf-8")

    ledger_root = tmp_path / "ledgers"
    paper_dir = ledger_root / "test_pid"
    paper_dir.mkdir(parents=True)
    ledger = paper_dir / "verification_ledger.json"
    original = {
        "entries": [{
            "theorem_name": "thm",
            "status": "AXIOM_BACKED",
            "axiom_debt": ["paper_definition_stub:Foo"],
            "gate_failures": ["no_paper_axiom_debt"],
            "validation_gates": {"lean_proof_closed": True},
        }],
    }
    ledger.write_text(json.dumps(original), encoding="utf-8")

    # Invoke the script with --dry-run.
    repo_root = Path(__file__).resolve().parent.parent
    script = repo_root / "scripts" / "discharge_aligned_debts.py"
    proc = subprocess.run(
        [sys.executable, str(script),
         "--alignments", str(alignments),
         "--ledger-root", str(ledger_root),
         "--dry-run"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0
    assert "Status changes: 1" in proc.stdout

    # Ledger file must be unchanged.
    after = json.loads(ledger.read_text(encoding="utf-8"))
    assert after == original
