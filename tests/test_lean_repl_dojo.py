"""Unit tests for lean_repl_dojo.py.

Tests cover the pure parsing helpers and the REPLDojo context-manager behaviour
with subprocess.run mocked so no Lean installation is required.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lean_repl_dojo import (
    BatchExpansionRequest,
    LeanError,
    ProofFinished,
    REPLDojo,
    SearchTreeCheckpoint,
    TacticState,
    _extract_lean_error,
    _extract_unsolved_goals,
    _replace_theorem_body,
    _synthetic_initial_state,
    load_tree_checkpoint,
    replay_step_traces,
    save_tree_checkpoint,
)


# ---------------------------------------------------------------------------
# _replace_theorem_body
# ---------------------------------------------------------------------------

_SIMPLE_THM = """\
theorem foo : True := by
  sorry
"""

_MULTI_PARAM_THM = """\
theorem bar (n : Nat) (h : n > 0) : n ≥ 1 := by
  omega
"""


def test_replace_theorem_body_single_tactic():
    result = _replace_theorem_body(_SIMPLE_THM, "foo", ["trivial"])
    assert "trivial" in result
    assert "sorry" not in result.split(":= by")[1]


def test_replace_theorem_body_multiple_tactics():
    result = _replace_theorem_body(_SIMPLE_THM, "foo", ["intro h", "exact h"])
    assert "intro h" in result
    assert "exact h" in result


def test_replace_theorem_body_empty_tactics_inserts_sorry():
    result = _replace_theorem_body(_SIMPLE_THM, "foo", [])
    assert "sorry" in result.split(":= by")[1]


def test_replace_theorem_body_unknown_theorem_raises():
    with pytest.raises(ValueError, match="Could not find"):
        _replace_theorem_body(_SIMPLE_THM, "nonexistent", ["trivial"])


def test_replace_theorem_body_preserves_header():
    result = _replace_theorem_body(_MULTI_PARAM_THM, "bar", ["omega"])
    assert "theorem bar (n : Nat)" in result


# ---------------------------------------------------------------------------
# _synthetic_initial_state
# ---------------------------------------------------------------------------

def test_synthetic_initial_state_simple():
    src = "theorem foo : True := by\n  trivial\n"
    pp = _synthetic_initial_state(src, "foo")
    assert "⊢" in pp
    assert "True" in pp


def test_synthetic_initial_state_with_params():
    src = "theorem bar (n : Nat) (h : n > 0) : n ≥ 1 := by\n  omega\n"
    pp = _synthetic_initial_state(src, "bar")
    assert "n : Nat" in pp
    assert "h : n > 0" in pp
    assert "⊢" in pp


def test_synthetic_initial_state_missing_theorem():
    src = "theorem foo : True := by\n  trivial\n"
    pp = _synthetic_initial_state(src, "missing")
    assert "???" in pp or "⊢" in pp


# ---------------------------------------------------------------------------
# _extract_unsolved_goals
# ---------------------------------------------------------------------------

_UNSOLVED_OUTPUT = """\
error: Desol/Basic.lean:5:2: unsolved goals
n : Nat
h : n > 0
⊢ n ≥ 1
"""


def test_extract_unsolved_goals_found():
    result = _extract_unsolved_goals(_UNSOLVED_OUTPUT)
    assert result is not None
    assert "⊢" in result
    assert "n ≥ 1" in result


def test_extract_unsolved_goals_not_present():
    result = _extract_unsolved_goals("Build completed successfully.\n")
    assert result is None


def test_extract_unsolved_goals_empty():
    assert _extract_unsolved_goals("") is None


# ---------------------------------------------------------------------------
# _extract_lean_error
# ---------------------------------------------------------------------------

_ERROR_OUTPUT = """\
error: Desol/Basic.lean:7:4: unknown identifier 'foo'
"""


def test_extract_lean_error_found():
    result = _extract_lean_error(_ERROR_OUTPUT)
    assert result is not None
    assert "unknown identifier" in result


def test_extract_lean_error_ignores_unsolved_goals():
    output = "error: Desol/Basic.lean:5:2: unsolved goals\n"
    result = _extract_lean_error(output)
    assert result is None


def test_extract_lean_error_empty():
    assert _extract_lean_error("") is None


# ---------------------------------------------------------------------------
# TacticState.num_goals
# ---------------------------------------------------------------------------

def test_tactic_state_num_goals():
    ts = TacticState(pp="⊢ True\n⊢ False", id=0)
    assert ts.num_goals == 2


def test_tactic_state_num_goals_single():
    ts = TacticState(pp="n : Nat\n⊢ n ≥ 0", id=0)
    assert ts.num_goals == 1


# ---------------------------------------------------------------------------
# REPLDojo context-manager (subprocess mocked)
# ---------------------------------------------------------------------------

_THM_SOURCE = """\
theorem my_thm : True := by
  sorry
"""


def _make_dojo(tmp_path: Path) -> REPLDojo:
    lean_file = tmp_path / "Desol" / "Test.lean"
    lean_file.parent.mkdir(parents=True)
    lean_file.write_text(_THM_SOURCE)
    return REPLDojo(
        project_root=tmp_path,
        file_path=Path("Desol/Test.lean"),
        theorem_name="my_thm",
        timeout=10,
    )


def _ok_result() -> MagicMock:
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.returncode = 0
    r.stdout = "Build completed successfully.\n"
    r.stderr = ""
    return r


def _unsolved_result() -> MagicMock:
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.returncode = 1
    r.stdout = ""
    r.stderr = (
        "error: Desol/Test.lean:1:20: unsolved goals\n"
        "⊢ True\n"
    )
    return r


def _error_result() -> MagicMock:
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.returncode = 1
    r.stdout = ""
    r.stderr = "error: Desol/Test.lean:2:4: unknown tactic 'badtac'\n"
    return r


def test_repldojo_enter_returns_tactic_state(tmp_path):
    dojo = _make_dojo(tmp_path)
    with dojo as (d, state):
        assert isinstance(state, TacticState)
        assert "⊢" in state.pp


def test_repldojo_run_tac_proof_finished(tmp_path):
    dojo = _make_dojo(tmp_path)
    with patch("subprocess.run", return_value=_ok_result()):
        with dojo as (d, state):
            result = d.run_tac(state, "trivial")
    assert isinstance(result, ProofFinished)


def test_repldojo_run_tac_unsolved_goals(tmp_path):
    dojo = _make_dojo(tmp_path)
    with patch("subprocess.run", return_value=_unsolved_result()):
        with dojo as (d, state):
            result = d.run_tac(state, "intro")
    assert isinstance(result, TacticState)
    assert "⊢" in result.pp


def test_repldojo_run_tac_lean_error(tmp_path):
    dojo = _make_dojo(tmp_path)
    with patch("subprocess.run", return_value=_error_result()):
        with dojo as (d, state):
            result = d.run_tac(state, "badtac")
    assert isinstance(result, LeanError)
    assert result.error


def test_repldojo_restores_file_on_exit(tmp_path):
    lean_file = tmp_path / "Desol" / "Test.lean"
    lean_file.parent.mkdir(parents=True)
    lean_file.write_text(_THM_SOURCE)
    dojo = REPLDojo(
        project_root=tmp_path,
        file_path=Path("Desol/Test.lean"),
        theorem_name="my_thm",
        timeout=10,
    )
    with patch("subprocess.run", return_value=_ok_result()):
        with dojo as (d, state):
            d.run_tac(state, "trivial")
    assert lean_file.read_text() == _THM_SOURCE


def test_repldojo_sorry_result_is_lean_error(tmp_path):
    """returncode=0 but 'declaration uses sorry' at the decl line → LeanError."""
    dojo = _make_dojo(tmp_path)
    sorry_result = MagicMock(spec=subprocess.CompletedProcess)
    sorry_result.returncode = 0
    sorry_result.stdout = "warning: Desol/Test.lean:1:8: declaration uses 'sorry'\n"
    sorry_result.stderr = ""
    with patch("subprocess.run", return_value=sorry_result):
        with dojo as (d, state):
            result = d.run_tac(state, "sorry")
    assert isinstance(result, LeanError)


def test_repldojo_records_step_traces(tmp_path):
    dojo = _make_dojo(tmp_path)
    with patch("subprocess.run", return_value=_unsolved_result()):
        with dojo as (d, state):
            _ = d.run_tac(state, "intro")
            traces = d.get_step_traces()
    assert len(traces) == 1
    assert traces[0].result_kind == "state_advanced"
    assert traces[0].tactic == "intro"


def test_replay_step_traces(tmp_path):
    dojo = _make_dojo(tmp_path)
    with patch("subprocess.run", return_value=_unsolved_result()):
        with dojo as (d, state):
            _ = d.run_tac(state, "intro")
            traces = d.get_step_traces()
            replay = replay_step_traces(state.pp, traces)
    assert "final_state_pp" in replay
    assert len(replay["steps"]) == 1


def test_tree_checkpoint_roundtrip(tmp_path):
    ck = SearchTreeCheckpoint(
        tree_id="tree1",
        theorem_name="my_thm",
        created_at_unix=1,
        frontier=[{"node": 1}],
        explored=[{"node": 0}],
    )
    p = tmp_path / "ckpt.json"
    save_tree_checkpoint(p, ck)
    loaded = load_tree_checkpoint(p)
    assert loaded is not None
    assert loaded.tree_id == "tree1"


def test_batch_request_dataclass():
    req = BatchExpansionRequest(
        project_root=".",
        file_path="Desol/Test.lean",
        theorem_name="my_thm",
        tactics=["intro"],
    )
    assert req.timeout == 300
