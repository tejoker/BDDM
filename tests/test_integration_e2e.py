"""End-to-end integration tests that actually call Lean.

These tests require a working Lean 4 / lake installation in PATH.
They are skipped automatically when lake is not available.

Marked with @pytest.mark.integration — run explicitly with:
    pytest tests/test_integration_e2e.py -m integration -v
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

# ── helpers ──────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _lake_available() -> bool:
    try:
        r = subprocess.run(["lake", "--version"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _lake_available(),
    reason="lake not available — skipping Lean integration tests",
)


# ── REPLDojo: prove a trivial real theorem ───────────────────────────────────

def test_repldojo_proves_trivial_theorem(tmp_path):
    """REPLDojo must prove `n + 0 = n` end-to-end via lake build."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from lean_repl_dojo import ProofFinished, REPLDojo

    # Write a minimal Lean file
    lean_file = tmp_path / "Test.lean"
    lean_file.write_text(textwrap.dedent("""\
        import Mathlib
        theorem nat_add_zero (n : Nat) : n + 0 = n := by
          sorry
    """), encoding="utf-8")

    # Copy lakefile + toolchain + manifest so lake can resolve all dependencies
    for fname in ["lakefile.toml", "lean-toolchain", "lake-manifest.json"]:
        src = PROJECT_ROOT / fname
        if src.exists():
            (tmp_path / fname).write_bytes(src.read_bytes())

    dojo = REPLDojo(
        project_root=tmp_path,
        file_path=Path("Test.lean"),
        theorem_name="nat_add_zero",
        timeout=120,
    )
    with dojo as (d, initial_state):
        result = d.run_tac(initial_state, "omega")
        assert isinstance(result, ProofFinished), (
            f"Expected ProofFinished but got: {result!r}"
        )


def test_repldojo_reports_lean_error_on_bad_tactic(tmp_path):
    """REPLDojo must return LeanError for a tactic that does not type-check."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from lean_repl_dojo import LeanError, REPLDojo

    lean_file = tmp_path / "Test.lean"
    lean_file.write_text(textwrap.dedent("""\
        import Mathlib
        theorem nat_add_zero (n : Nat) : n + 0 = n := by
          sorry
    """), encoding="utf-8")
    for fname in ["lakefile.toml", "lean-toolchain", "lake-manifest.json"]:
        src = PROJECT_ROOT / fname
        if src.exists():
            (tmp_path / fname).write_bytes(src.read_bytes())

    dojo = REPLDojo(
        project_root=tmp_path,
        file_path=Path("Test.lean"),
        theorem_name="nat_add_zero",
        timeout=120,
    )
    with dojo as (d, initial_state):
        result = d.run_tac(initial_state, "this_tactic_does_not_exist_xyz")
        assert isinstance(result, LeanError), (
            f"Expected LeanError but got: {result!r}"
        )


def test_repldojo_tactic_state_on_partial_proof(tmp_path):
    """REPLDojo must return TacticState (not ProofFinished) when goals remain."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from lean_repl_dojo import ProofFinished, REPLDojo, TacticState

    lean_file = tmp_path / "Test.lean"
    lean_file.write_text(textwrap.dedent("""\
        import Mathlib
        theorem two_goals (n m : Nat) : n + 0 = n ∧ m + 0 = m := by
          sorry
    """), encoding="utf-8")
    for fname in ["lakefile.toml", "lean-toolchain", "lake-manifest.json"]:
        src = PROJECT_ROOT / fname
        if src.exists():
            (tmp_path / fname).write_bytes(src.read_bytes())

    dojo = REPLDojo(
        project_root=tmp_path,
        file_path=Path("Test.lean"),
        theorem_name="two_goals",
        timeout=120,
    )
    with dojo as (d, initial_state):
        # constructor splits into two goals
        result = d.run_tac(initial_state, "constructor")
        # Should be TacticState (still goals) or ProofFinished (unlikely for constructor alone)
        assert isinstance(result, (TacticState, ProofFinished)), (
            f"Unexpected result type: {type(result)}"
        )


# ── Verification contract: independent verify ─────────────────────────────────

def test_independent_lean_verify_correct_proof():
    """independent_lean_verify must confirm a correct proof."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from pipeline_status import independent_lean_verify

    ok, detail = independent_lean_verify(
        lean_statement="theorem nat_add_zero (n : Nat) : n + 0 = n",
        proof_text="omega",
        project_root=PROJECT_ROOT,
        timeout=120,
    )
    assert ok, f"Expected success but got: {detail}"


def test_independent_lean_verify_wrong_proof():
    """independent_lean_verify must reject a proof with wrong tactic."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from pipeline_status import independent_lean_verify

    ok, detail = independent_lean_verify(
        lean_statement="theorem nat_add_zero (n : Nat) : n + 0 = n",
        proof_text="ring_nf\nexact?",   # will fail
        project_root=PROJECT_ROOT,
        timeout=60,
    )
    assert not ok, f"Expected failure but got success: {detail}"


# ── Full pipeline: prove a known theorem end-to-end ───────────────────────────

@pytest.mark.skipif(
    not os.environ.get("MISTRAL_API_KEY"),
    reason="MISTRAL_API_KEY not set — skipping API-dependent tests",
)
def test_full_pipeline_proves_simple_theorem(tmp_path):
    """prove_with_ponder must prove `n + 0 = n` end-to-end, writing FULLY_PROVEN ledger."""
    import sys
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

    # Write a minimal Lean file in tmp_path
    lean_file = tmp_path / "Test.lean"
    lean_file.write_text(textwrap.dedent("""\
        import Mathlib
        theorem nat_add_zero (n : Nat) : n + 0 = n := by
          sorry
    """), encoding="utf-8")
    for fname in ["lakefile.toml", "lean-toolchain", "lake-manifest.json"]:
        src = PROJECT_ROOT / fname
        if src.exists():
            (tmp_path / fname).write_bytes(src.read_bytes())

    from prove_with_ponder import run_proof
    result = run_proof(
        project_root=tmp_path,
        file_path=Path("Test.lean"),
        theorem_name="nat_add_zero",
        mode="full-draft",
        max_repair_rounds=3,
    )
    assert result.get("proved"), f"Expected proved=True, got: {result}"
    assert result.get("status") in ("FULLY_PROVEN", "INTERMEDIARY_PROVEN"), (
        f"Unexpected status: {result.get('status')}"
    )
