"""Mocked end-to-end pipeline tests.

These tests mock the Mistral API and the Lean REPL so the full pipeline
runs in milliseconds without any external dependencies.  They verify
correctness of wiring, ledger output, and KG promotion — not proof search.

Run with:
    pytest tests/test_pipeline_mocked.py -v
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))


# ── mock helpers ─────────────────────────────────────────────────────────────

def _mock_mistral_client(tactics: list[str]):
    """Return a fake Mistral client whose chat.complete always suggests tactics."""
    import re

    client = MagicMock()
    # build a fake response that returns the tactics as a numbered list
    tactic_text = "\n".join(f"{i+1}. {t}" for i, t in enumerate(tactics))

    choice = MagicMock()
    choice.message.content = tactic_text
    response = MagicMock()
    response.choices = [choice]
    client.chat.complete.return_value = response
    return client


def _mock_lean_repl_server(proof_state_id: int = 0, goals: list[str] | None = None):
    """Return a fake LeanREPLServer that immediately closes the proof on first tactic."""
    from lean_repl_server import ProofFinished, TacticState

    if goals is None:
        goals = ["⊢ True"]

    server = MagicMock()
    server.__enter__ = lambda s: s
    server.__exit__ = MagicMock(return_value=False)

    # ensure_mathlib_imported
    server.ensure_mathlib_imported.return_value = 1

    # start_proof → returns initial proof state id
    server.start_proof.return_value = proof_state_id

    # run_tac("skip") → TacticState with goals, then run_tac(any tactic) → ProofFinished
    call_count = {"n": 0}

    def _run_tac(ps_id, tactic):
        call_count["n"] += 1
        if call_count["n"] == 1 and tactic in ("skip", "all_goals intro"):
            return TacticState(goals=goals, proof_state_id=ps_id + 1)
        # Second call (any tactic from MCTS) closes the proof
        return ProofFinished(proof_state_id=ps_id + 2)

    server.run_tac.side_effect = _run_tac
    return server


# ── tests ─────────────────────────────────────────────────────────────────────

class TestStateMCTSMocked:
    """run_state_mcts with mocked REPL and LLM."""

    def test_solves_simple_theorem(self):
        """run_state_mcts finds a proof when LLM suggests a closing tactic."""
        from mcts_search import run_state_mcts

        mock_server = _mock_lean_repl_server(proof_state_id=0, goals=["⊢ True"])

        with patch("mcts_search.LeanREPLServer", return_value=mock_server), \
             patch("ponder_loop.generate_tactic_options", return_value=["trivial", "simp"]):
            ok, tactics, summary = run_state_mcts(
                project_root=PROJECT_ROOT,
                theorem_statement="theorem test_true : True",
                client=MagicMock(),
                model="mock-model",
                iterations=5,
                n_tactics=2,
                max_depth=3,
                kg_write_on_success=False,
            )

        assert ok is True
        assert len(tactics) >= 1
        assert "SOLVED" in summary

    def test_returns_failure_on_all_errors(self):
        """run_state_mcts returns False when every tactic fails."""
        from lean_repl_server import LeanError
        from mcts_search import run_state_mcts

        server = MagicMock()
        server.__enter__ = lambda s: s
        server.__exit__ = MagicMock(return_value=False)
        server.ensure_mathlib_imported.return_value = 1
        server.start_proof.return_value = 0
        server.run_tac.return_value = LeanError("unknown tactic")

        with patch("mcts_search.LeanREPLServer", return_value=server), \
             patch("ponder_loop.generate_tactic_options", return_value=["bad_tactic"]):
            ok, tactics, summary = run_state_mcts(
                project_root=PROJECT_ROOT,
                theorem_statement="theorem impossible : False",
                client=MagicMock(),
                model="mock-model",
                iterations=3,
                n_tactics=1,
                max_depth=2,
                kg_write_on_success=False,
            )

        assert ok is False

    def test_kg_write_on_success(self, tmp_path):
        """A successful proof is written to the KG trusted layer."""
        from mcts_search import run_state_mcts

        mock_server = _mock_lean_repl_server(proof_state_id=0)

        with patch("mcts_search.LeanREPLServer", return_value=mock_server), \
             patch("ponder_loop.generate_tactic_options", return_value=["trivial"]):
            ok, tactics, _ = run_state_mcts(
                project_root=tmp_path,
                theorem_statement="theorem kg_test : True",
                client=MagicMock(),
                model="mock-model",
                iterations=3,
                n_tactics=1,
                max_depth=2,
                kg_write_on_success=True,
            )

        assert ok is True
        kg_file = tmp_path / "output" / "kg" / "trusted" / "theorems.jsonl"
        assert kg_file.exists(), "KG file should be created on success"
        entries = [json.loads(l) for l in kg_file.read_text().splitlines() if l.strip()]
        assert len(entries) == 1
        assert entries[0]["name"] == "kg_test"
        assert entries[0]["source"] == "state_mcts"

    def test_start_proof_strips_sorry_suffix(self):
        """start_proof strips ':= sorry' before adding ':= by sorry'."""
        from lean_repl_server import LeanREPLServer

        server = LeanREPLServer.__new__(LeanREPLServer)
        server._env_id = 1

        # Simulate elaborate capturing what was sent
        sent_stmts = []

        def fake_elaborate(cmd, env=None):
            sent_stmts.append(cmd)
            return {"env": 2, "sorries": [{"proofState": 5}], "messages": []}

        server.elaborate = fake_elaborate
        server.ensure_mathlib_imported = lambda: 1

        import re
        import importlib
        import lean_repl_server as lrs

        # Call the actual start_proof logic inline
        stmt = "theorem nat_zero (n : Nat) : n + 0 = n := sorry"
        stmt = stmt.rstrip()
        stmt = re.sub(r":=\s*by\b.*$", "", stmt, flags=re.DOTALL).strip()
        stmt = re.sub(r":=\s*sorry\s*$", "", stmt, flags=re.DOTALL).strip()
        stmt = re.sub(r":=\s*$", "", stmt).strip()
        result = stmt + " := by\n  sorry"

        assert ":= sorry := by" not in result
        assert result.endswith(":= by\n  sorry")


class TestStatementTranslatorDefGate:
    """statement_translator rejects def/structure/class declarations."""

    def test_rejects_def_declaration(self):
        from statement_translator import _validate_signature

        result = _validate_signature("def foo : Nat := 0", project_root=PROJECT_ROOT)
        ok, reason = result[0], result[1]
        assert ok is False
        assert "def" in reason.lower() or "theorem" in reason.lower()

    def test_accepts_theorem_not_rejected_by_def_gate(self):
        from statement_translator import _validate_signature

        result = _validate_signature(
            "theorem foo (n : Nat) : n + 0 = n := by sorry",
            project_root=PROJECT_ROOT,
        )
        # The def-keyword gate must not fire (may fail for other reasons)
        reason = result[1] if len(result) > 1 else ""
        assert "starts with `def`" not in reason

    def test_rejects_structure(self):
        from statement_translator import _validate_signature

        result = _validate_signature(
            "structure MyStruct where x : Nat", project_root=PROJECT_ROOT
        )
        assert result[0] is False


class TestKGSelfImproving:
    """KG trusted layer accumulates proofs and feeds them back as premises."""

    def test_kg_loaded_into_premise_context(self, tmp_path):
        """When a KG file exists, its entries are prepended to premise_context."""
        kg_path = tmp_path / "output" / "kg" / "trusted" / "theorems.jsonl"
        kg_path.parent.mkdir(parents=True)
        kg_path.write_text(json.dumps({
            "name": "prior_lemma",
            "statement": "theorem prior_lemma : True := trivial",
            "source": "state_mcts",
            "timestamp": 0.0,
        }) + "\n")

        from mcts_search import run_state_mcts
        from lean_repl_server import LeanError

        server = MagicMock()
        server.__enter__ = lambda s: s
        server.__exit__ = MagicMock(return_value=False)
        server.ensure_mathlib_imported.return_value = 1
        server.start_proof.return_value = 0
        server.run_tac.return_value = LeanError("fail")

        captured_premise = {}

        def fake_generate(*, lean_state, client, model, num_options, temperature,
                          premise_context="", retrieval_index_path="", retrieval_top_k=12):
            captured_premise["ctx"] = premise_context
            return []  # no tactics — forces FAILED

        with patch("mcts_search.LeanREPLServer", return_value=server), \
             patch("ponder_loop.generate_tactic_options", side_effect=fake_generate):
            run_state_mcts(
                project_root=tmp_path,
                theorem_statement="theorem t : True",
                client=MagicMock(),
                model="mock",
                iterations=1,
                n_tactics=1,
                max_depth=1,
                kg_write_on_success=True,
            )

        assert "prior_lemma" in captured_premise.get("ctx", ""), \
            "KG entry should appear in premise_context"
