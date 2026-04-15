"""Bridge proof integration tests.

Tests the full bridge-proof pipeline with a synthetic ledger that mimics
a real arXiv theorem with ungrounded assumptions.

Run with:
    pytest tests/test_bridge_proofs.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))


def _make_ledger(tmp_path: Path, theorem_name: str, assumptions: list) -> Path:
    """Write a minimal ledger entry to a temp directory."""
    ledger_root = tmp_path / "verification_ledgers"
    ledger_root.mkdir(parents=True)
    entry = {
        "theorem_name": theorem_name,
        "lean_statement": f"theorem {theorem_name} (n : Nat) : n + 0 = n := by simp",
        "proved": True,
        "assumptions": assumptions,
        "status": "INTERMEDIARY_PROVEN",
        "paper_id": "test_paper",
    }
    (ledger_root / "test_paper.json").write_text(json.dumps([entry]), encoding="utf-8")
    return ledger_root


class TestSuggestBridgeCandidates:

    def test_returns_list_for_ungrounded_assumption(self, tmp_path):
        """suggest_bridge_candidates returns a list (may be empty without embeddings)."""
        from bridge_proofs import suggest_bridge_candidates

        ledger_root = tmp_path / "verification_ledgers"
        ledger_root.mkdir(parents=True)
        entries = [
            {
                "theorem_name": "nat_add_comm",
                "lean_statement": "theorem nat_add_comm (a b : Nat) : a + b = b + a",
                "proved": True,
                "assumptions": [{"expr": "a + b = b + a", "grounding": "GROUNDED"}],
                "status": "FULLY_PROVEN",
            },
        ]
        (ledger_root / "test_paper.json").write_text(json.dumps(entries))

        # suggest_bridge_candidates only takes assumption_expr and ledger_root
        candidates = suggest_bridge_candidates(
            assumption_expr="n + m = m + n",
            ledger_root=ledger_root,
        )
        assert isinstance(candidates, list)

    def test_empty_ledger_returns_empty(self, tmp_path):
        """Empty ledger → no candidates."""
        from bridge_proofs import suggest_bridge_candidates

        ledger_root = tmp_path / "verification_ledgers"
        ledger_root.mkdir()
        candidates = suggest_bridge_candidates(
            assumption_expr="n + 0 = n",
            ledger_root=ledger_root,
        )
        assert candidates == []

    def test_missing_ledger_returns_empty(self, tmp_path):
        """Non-existent ledger → no candidates (no crash)."""
        from bridge_proofs import suggest_bridge_candidates

        candidates = suggest_bridge_candidates(
            assumption_expr="n + 0 = n",
            ledger_root=tmp_path / "no_such_dir",
        )
        assert candidates == []


class TestExecuteBridgeChain:

    def test_no_crash_on_grounded_assumptions(self, tmp_path):
        """Fully-grounded assumptions are silently skipped."""
        from bridge_proofs import BridgeExecutionResult, execute_bridge_chain

        ledger_root = _make_ledger(
            tmp_path, "clean_theorem",
            [{"expr": "n = n", "grounding": "GROUNDED"}],
        )
        result = execute_bridge_chain(
            target_theorem="clean_theorem",
            ledger_root=ledger_root,
            use_z3=False,
            use_lean=False,
        )
        assert isinstance(result, BridgeExecutionResult)
        assert result.still_ungrounded == []

    def test_ungrounded_without_lean_statement_goes_to_still_ungrounded(self, tmp_path):
        """An assumption with only 'expr' (no lean_statement) ends up still_ungrounded
        when use_lean=False and use_z3=False."""
        from bridge_proofs import execute_bridge_chain

        ledger_root = _make_ledger(
            tmp_path, "ungrounded_test",
            [{"lean_expr": "∀ n : Nat, n ≥ 0", "grounding": "UNGROUNDED"}],
        )
        result = execute_bridge_chain(
            target_theorem="ungrounded_test",
            ledger_root=ledger_root,
            use_z3=False,
            use_lean=False,
        )
        # No grounding mechanism active → stays ungrounded
        assert len(result.still_ungrounded) == 1

    def test_proof_callback_invoked_when_lean_statement_present(self, tmp_path):
        """proof_callback is called when the assumption has a lean_statement field."""
        from bridge_proofs import execute_bridge_chain

        called_with = []

        def my_callback(lean_statement: str) -> str:
            called_with.append(lean_statement)
            return "omega"  # a real-looking tactic

        # The key: assumption must have 'lean_statement' (not just 'expr') for callback to fire
        ledger_root = _make_ledger(
            tmp_path, "callback_test",
            [{
                "expr": "∀ n : Nat, n ≥ 0",
                "lean_statement": "theorem aux : ∀ n : Nat, n ≥ 0 := by omega",
                "grounding": "UNGROUNDED",
            }],
        )
        execute_bridge_chain(
            target_theorem="callback_test",
            ledger_root=ledger_root,
            proof_callback=my_callback,
            use_z3=False,
            use_lean=True,
            lean_timeout_s=5,
        )
        assert len(called_with) >= 1

    def test_z3_runs_without_crash(self, tmp_path):
        """Z3 path executes without error (result may or may not ground the assumption)."""
        pytest.importorskip("z3", reason="z3-solver not installed")
        from bridge_proofs import execute_bridge_chain

        ledger_root = _make_ledger(
            tmp_path, "arith_test",
            [{"expr": "2 + 2 = 4", "grounding": "UNGROUNDED",
              "trust_class": "TRUST_PLACEHOLDER"}],
        )
        result = execute_bridge_chain(
            target_theorem="arith_test",
            ledger_root=ledger_root,
            use_z3=True,
            use_lean=False,
            lean_timeout_s=10,
        )
        assert result is not None
        assert isinstance(result.newly_grounded, list)
        assert isinstance(result.still_ungrounded, list)

    def test_full_pipeline_with_state_mcts_callback(self, tmp_path):
        """End-to-end: state-MCTS callback + bridge chain on a simple theorem."""
        import os
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
        if not os.environ.get("MISTRAL_API_KEY"):
            pytest.skip("MISTRAL_API_KEY not set")

        from bridge_proofs import execute_bridge_chain
        try:
            from mistralai import Mistral
        except ImportError:
            from mistralai.client import Mistral
        from mcts_search import run_state_mcts

        client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])

        def mcts_callback(lean_statement: str) -> str:
            ok, tactics, _ = run_state_mcts(
                project_root=PROJECT_ROOT,
                theorem_statement=lean_statement,
                client=client,
                model="labs-leanstral-2603",
                iterations=10,
                n_tactics=3,
                max_depth=4,
                repl_timeout=60.0,
                kg_write_on_success=False,
            )
            return "\n".join(tactics) if ok else "sorry"

        # Use a simple Nat theorem as the ungrounded assumption
        ledger_root = _make_ledger(
            tmp_path, "nat_add_zero",
            [{
                "expr": "∀ n : Nat, n + 0 = n",
                "lean_statement": "theorem nat_add_zero_bridge (n : Nat) : n + 0 = n",
                "grounding": "UNGROUNDED",
            }],
        )
        result = execute_bridge_chain(
            target_theorem="nat_add_zero",
            ledger_root=ledger_root,
            proof_callback=mcts_callback,
            use_z3=False,
            use_lean=True,
            lean_timeout_s=30,
        )
        # Pipeline completes; grounding depends on tactic success
        assert result is not None
        assert isinstance(result.newly_grounded, list)
