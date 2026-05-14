"""Hermetic tests for the Leanstral REPL-driven proof generator.

The Mistral client and the REPLDojo are both fully mocked. No network calls
and no real `lake` invocations are made. The mock REPLDojo accepts a
pre-canned tactic transition table that decides whether each `run_tac`
returns ProofFinished, an advanced TacticState, or a LeanError.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pytest

import leanstral_repl_proof_generator as gen


# --- Fake REPL infrastructure --------------------------------------------


@dataclass(frozen=True)
class _FakeTacticState:
    pp: str
    id: int


@dataclass(frozen=True)
class _FakeProofFinished:
    tactic_state_id: int


@dataclass(frozen=True)
class _FakeLeanError:
    error: str


class FakeDojo:
    """Mock for REPLDojo. Driven by a transition table:

        transitions: dict[tuple[int, str], result]

    where the key is (state_id, tactic) and the result is a _FakeTacticState
    (advance), _FakeProofFinished (close), or _FakeLeanError. Any tactic not
    in the table returns a LeanError.
    """

    def __init__(
        self,
        *,
        initial_pp: str,
        transitions: dict[tuple[int, str], Any],
    ) -> None:
        self.initial_pp = initial_pp
        self.transitions = transitions
        self.calls: list[tuple[int, str]] = []
        self.entered = False
        self.exited = False

    def __enter__(self) -> tuple["FakeDojo", _FakeTacticState]:
        self.entered = True
        return self, _FakeTacticState(pp=self.initial_pp, id=0)

    def __exit__(self, *args: Any) -> None:
        self.exited = True

    def run_tac(self, state: _FakeTacticState, tactic: str) -> Any:
        self.calls.append((state.id, tactic))
        key = (state.id, tactic.strip())
        if key in self.transitions:
            return self.transitions[key]
        return _FakeLeanError(error=f"no transition for ({state.id!r}, {tactic.strip()!r})")


# Patch the type checks in the generator module to accept our Fake types.
# We monkey-patch the module-level _is_* helpers to look at the type name —
# which they already do — so our Fake types just need to be named
# 'TacticState' / 'ProofFinished' / 'LeanError' for the duck-typing to work.
# Re-bind here so test fakes are recognised.
_FakeTacticState.__name__ = "TacticState"
_FakeProofFinished.__name__ = "ProofFinished"
_FakeLeanError.__name__ = "LeanError"
# The classes were declared with their natural names already, but be explicit:
assert _FakeTacticState.__name__ == "TacticState"
assert _FakeProofFinished.__name__ == "ProofFinished"
assert _FakeLeanError.__name__ == "LeanError"


# --- Fake Mistral client --------------------------------------------------


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeChat:
    def __init__(self, contents: list[str]) -> None:
        self._contents = list(contents)
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        if not self._contents:
            return _FakeResponse("")
        return _FakeResponse(self._contents.pop(0))


class FakeClient:
    def __init__(self, contents: list[str] | str) -> None:
        if isinstance(contents, str):
            contents = [contents]
        self.chat = _FakeChat(contents)


def _tactics_response(tactics: list[str]) -> str:
    return json.dumps({"tactics": tactics})


def _make_dojo_factory(fake_dojo: FakeDojo) -> Callable[..., FakeDojo]:
    def _factory(**_kwargs: Any) -> FakeDojo:
        return fake_dojo

    return _factory


# =====================================================================
# Test 1: single tactic closes the goal.
# =====================================================================


def test_single_tactic_closes_proof() -> None:
    fake_dojo = FakeDojo(
        initial_pp="⊢ True",
        transitions={(0, "trivial"): _FakeProofFinished(tactic_state_id=0)},
    )
    client = FakeClient(_tactics_response(["trivial", "exact True.intro"]))

    result = gen.prove_via_repl(
        paper_id="p1",
        theorem_name="thm",
        lean_statement="theorem thm : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
        max_steps=4,
        max_attempts_per_step=4,
        _dojo_factory=_make_dojo_factory(fake_dojo),
        _project_root=Path("/tmp"),
        _file_path=Path("dummy.lean"),
    )
    assert result is not None
    assert result["proof_body"] == "trivial"
    assert result["rounds"] == 1
    assert result["protocol"] == "leanstral_repl_v1"
    assert fake_dojo.exited is True


# =====================================================================
# Test 2: two-step proof.
# =====================================================================


def test_two_step_proof_chains_tactics() -> None:
    fake_dojo = FakeDojo(
        initial_pp="h : P\n⊢ P",
        transitions={
            (0, "intro h"): _FakeTacticState(pp="h : P\n⊢ P (advanced)", id=1),
            (1, "exact h"): _FakeProofFinished(tactic_state_id=1),
        },
    )
    client = FakeClient([
        _tactics_response(["intro h", "exact h"]),
        _tactics_response(["exact h", "assumption"]),
    ])

    result = gen.prove_via_repl(
        paper_id="p2",
        theorem_name="thm2",
        lean_statement="theorem thm2 (h : P) : P := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
        max_steps=6,
        max_attempts_per_step=4,
        _dojo_factory=_make_dojo_factory(fake_dojo),
        _project_root=Path("/tmp"),
        _file_path=Path("dummy.lean"),
    )
    assert result is not None
    assert result["proof_body"] == "intro h\nexact h"
    assert result["rounds"] == 2
    assert len(result["steps"]) == 2
    assert len(client.chat.calls) == 2


# =====================================================================
# Test 3: all candidates fail at step 1 -> None.
# =====================================================================


def test_all_candidates_fail_returns_none() -> None:
    fake_dojo = FakeDojo(
        initial_pp="⊢ False",
        transitions={
            (0, "exact rfl"): _FakeLeanError(error="type mismatch"),
            (0, "trivial"): _FakeLeanError(error="tactic failed"),
        },
    )
    client = FakeClient(_tactics_response(["exact rfl", "trivial"]))

    result = gen.prove_via_repl(
        paper_id="p3",
        theorem_name="thm3",
        lean_statement="theorem thm3 : False := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
        max_steps=4,
        max_attempts_per_step=4,
        _dojo_factory=_make_dojo_factory(fake_dojo),
        _project_root=Path("/tmp"),
        _file_path=Path("dummy.lean"),
    )
    assert result is None
    assert fake_dojo.exited is True


# =====================================================================
# Test 4: goal stuck (no-progress -> same state) -> None.
# =====================================================================


def test_no_progress_is_treated_as_failure() -> None:
    # The dojo returns a TacticState with the SAME pp string -> generator
    # must reject the candidate (no progress) and, with no other candidates,
    # return None.
    fake_dojo = FakeDojo(
        initial_pp="⊢ G",
        transitions={
            (0, "skip"): _FakeTacticState(pp="⊢ G", id=1),
        },
    )
    client = FakeClient(_tactics_response(["skip"]))

    result = gen.prove_via_repl(
        paper_id="p4",
        theorem_name="thm4",
        lean_statement="theorem thm4 : G := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
        max_steps=4,
        max_attempts_per_step=4,
        _dojo_factory=_make_dojo_factory(fake_dojo),
        _project_root=Path("/tmp"),
        _file_path=Path("dummy.lean"),
    )
    assert result is None


# =====================================================================
# Test 5: forbidden tokens are filtered before REPL is invoked.
# =====================================================================


def test_forbidden_token_candidates_are_filtered() -> None:
    # The LLM emits 3 candidates: two forbidden (sorry / apply?), one valid.
    # The dojo should ONLY ever see the valid one.
    fake_dojo = FakeDojo(
        initial_pp="⊢ True",
        transitions={(0, "trivial"): _FakeProofFinished(tactic_state_id=0)},
    )
    client = FakeClient(_tactics_response(["sorry", "apply?", "trivial"]))

    result = gen.prove_via_repl(
        paper_id="p5",
        theorem_name="thm5",
        lean_statement="theorem thm5 : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
        max_steps=4,
        max_attempts_per_step=4,
        _dojo_factory=_make_dojo_factory(fake_dojo),
        _project_root=Path("/tmp"),
        _file_path=Path("dummy.lean"),
    )
    assert result is not None
    # Only the surviving (non-forbidden) candidate is tried.
    assert fake_dojo.calls == [(0, "trivial")]


def test_all_forbidden_returns_none() -> None:
    fake_dojo = FakeDojo(initial_pp="⊢ True", transitions={})
    client = FakeClient(_tactics_response(["sorry", "admit", "axiom foo : True"]))

    result = gen.prove_via_repl(
        paper_id="p6",
        theorem_name="thm6",
        lean_statement="theorem thm6 : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
        max_steps=4,
        max_attempts_per_step=4,
        _dojo_factory=_make_dojo_factory(fake_dojo),
        _project_root=Path("/tmp"),
        _file_path=Path("dummy.lean"),
    )
    assert result is None
    # No REPL calls because all candidates were filtered before the loop.
    assert fake_dojo.calls == []


# =====================================================================
# Test 6: max-steps reached -> None.
# =====================================================================


def test_max_steps_reached_returns_none() -> None:
    # Each step advances state but never closes the goal — we cap at 2.
    fake_dojo = FakeDojo(
        initial_pp="⊢ G0",
        transitions={
            (0, "step1"): _FakeTacticState(pp="⊢ G1", id=1),
            (1, "step2"): _FakeTacticState(pp="⊢ G2", id=2),
            (2, "step3"): _FakeTacticState(pp="⊢ G3", id=3),
        },
    )
    client = FakeClient([
        _tactics_response(["step1"]),
        _tactics_response(["step2"]),
        _tactics_response(["step3"]),
    ])

    result = gen.prove_via_repl(
        paper_id="p7",
        theorem_name="thm7",
        lean_statement="theorem thm7 : G := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
        max_steps=2,
        max_attempts_per_step=4,
        _dojo_factory=_make_dojo_factory(fake_dojo),
        _project_root=Path("/tmp"),
        _file_path=Path("dummy.lean"),
    )
    assert result is None
    # Used exactly the budget.
    assert len(client.chat.calls) == 2


# =====================================================================
# Test 7: malformed JSON from the LLM -> None.
# =====================================================================


def test_malformed_json_response_returns_none() -> None:
    fake_dojo = FakeDojo(initial_pp="⊢ True", transitions={})
    client = FakeClient("not json at all")

    result = gen.prove_via_repl(
        paper_id="p8",
        theorem_name="thm8",
        lean_statement="theorem thm8 : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
        max_steps=4,
        max_attempts_per_step=4,
        _dojo_factory=_make_dojo_factory(fake_dojo),
        _project_root=Path("/tmp"),
        _file_path=Path("dummy.lean"),
    )
    assert result is None
    assert fake_dojo.calls == []


def test_empty_tactics_list_returns_none() -> None:
    fake_dojo = FakeDojo(initial_pp="⊢ True", transitions={})
    client = FakeClient(_tactics_response([]))

    result = gen.prove_via_repl(
        paper_id="p9",
        theorem_name="thm9",
        lean_statement="theorem thm9 : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
        _dojo_factory=_make_dojo_factory(fake_dojo),
        _project_root=Path("/tmp"),
        _file_path=Path("dummy.lean"),
    )
    assert result is None


# =====================================================================
# Test 8: max-attempts-per-step caps candidates tried.
# =====================================================================


def test_max_attempts_per_step_caps_candidates() -> None:
    # LLM offers 5 candidates; max_attempts_per_step=2 should only try 2.
    fake_dojo = FakeDojo(
        initial_pp="⊢ G",
        transitions={
            (0, "c1"): _FakeLeanError(error="fail1"),
            (0, "c2"): _FakeLeanError(error="fail2"),
            (0, "c3"): _FakeProofFinished(tactic_state_id=0),  # would succeed but capped out
        },
    )
    client = FakeClient(_tactics_response(["c1", "c2", "c3", "c4", "c5"]))

    result = gen.prove_via_repl(
        paper_id="p10",
        theorem_name="thm10",
        lean_statement="theorem thm10 : G := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
        max_steps=4,
        max_attempts_per_step=2,
        _dojo_factory=_make_dojo_factory(fake_dojo),
        _project_root=Path("/tmp"),
        _file_path=Path("dummy.lean"),
    )
    assert result is None
    # Only c1 and c2 were tried.
    assert fake_dojo.calls == [(0, "c1"), (0, "c2")]


# =====================================================================
# Test 9: client=None / empty statement / empty theorem-name -> None.
# =====================================================================


def test_no_client_returns_none() -> None:
    result = gen.prove_via_repl(
        paper_id="p",
        theorem_name="t",
        lean_statement="theorem t : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=None,
    )
    assert result is None


def test_empty_statement_returns_none() -> None:
    result = gen.prove_via_repl(
        paper_id="p",
        theorem_name="t",
        lean_statement="",
        paper_theory_hint="",
        paper_local_file="",
        client=FakeClient(""),
    )
    assert result is None


def test_empty_theorem_name_returns_none() -> None:
    result = gen.prove_via_repl(
        paper_id="p",
        theorem_name="",
        lean_statement="theorem t : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=FakeClient(""),
    )
    assert result is None


# =====================================================================
# Test 10: prompt construction inputs.
# =====================================================================


def test_build_step_prompt_includes_state_and_history() -> None:
    prompt = gen.build_step_prompt(
        paper_id="2304.09598",
        theorem_name="thm_alpha",
        lean_statement="theorem thm_alpha (n : Nat) : n + 0 = n := by sorry",
        paper_theory_hint="def Foo := True",
        history=["intro n", "simp"],
        state_pp="n : Nat\n⊢ n + 0 = n",
        error_tail="",
    )
    assert "thm_alpha" in prompt
    assert "n + 0 = n" in prompt
    assert "intro n" in prompt
    assert "Foo" in prompt


def test_build_step_prompt_retry_block_only_when_error() -> None:
    p1 = gen.build_step_prompt(
        paper_id="p",
        theorem_name="t",
        lean_statement="theorem t : True := by sorry",
        paper_theory_hint="",
        history=[],
        state_pp="⊢ True",
        error_tail="last tactic failed",
    )
    assert "last tactic failed" in p1
    p2 = gen.build_step_prompt(
        paper_id="p",
        theorem_name="t",
        lean_statement="theorem t : True := by sorry",
        paper_theory_hint="",
        history=[],
        state_pp="⊢ True",
        error_tail="",
    )
    assert "last tactic failed" not in p2


# =====================================================================
# Test 11: forbidden-token gate primitives.
# =====================================================================


def test_contains_forbidden_token_primitives() -> None:
    assert gen._contains_forbidden_token("intro h; sorry") == "sorry"
    assert gen._contains_forbidden_token("apply?") == "apply?"
    assert gen._contains_forbidden_token("axiom foo : True") == "axiom"
    assert gen._contains_forbidden_token("native_decide") == "native_decide"
    # Word-boundary safety.
    assert gen._contains_forbidden_token("sorrytown") is None
    assert gen._contains_forbidden_token("axiomatized") is None
    # Clean tactic passes.
    assert gen._contains_forbidden_token("exact h") is None


def test_extract_candidates_strips_fences_and_normalises() -> None:
    raw = '{"tactics": ["```lean\\n:= by\\nintro h\\n```", "  exact h  "]}'
    cands = gen._extract_candidates(raw)
    assert cands[0].startswith("intro h")
    assert cands[1] == "exact h"


def test_extract_candidates_rejects_forbidden() -> None:
    raw = '{"tactics": ["sorry", "exact h"]}'
    cands = gen._extract_candidates(raw)
    assert cands == ["exact h"]


# =====================================================================
# Test 12: REPL session is closed even on failure.
# =====================================================================


def test_session_is_closed_on_failure() -> None:
    fake_dojo = FakeDojo(
        initial_pp="⊢ G",
        transitions={(0, "tac"): _FakeLeanError(error="boom")},
    )
    client = FakeClient(_tactics_response(["tac"]))

    result = gen.prove_via_repl(
        paper_id="p",
        theorem_name="t",
        lean_statement="theorem t : G := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
        max_steps=2,
        max_attempts_per_step=2,
        _dojo_factory=_make_dojo_factory(fake_dojo),
        _project_root=Path("/tmp"),
        _file_path=Path("dummy.lean"),
    )
    assert result is None
    assert fake_dojo.exited is True


def test_session_is_closed_on_success() -> None:
    fake_dojo = FakeDojo(
        initial_pp="⊢ True",
        transitions={(0, "trivial"): _FakeProofFinished(tactic_state_id=0)},
    )
    client = FakeClient(_tactics_response(["trivial"]))

    result = gen.prove_via_repl(
        paper_id="p",
        theorem_name="t",
        lean_statement="theorem t : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
        _dojo_factory=_make_dojo_factory(fake_dojo),
        _project_root=Path("/tmp"),
        _file_path=Path("dummy.lean"),
    )
    assert result is not None
    assert fake_dojo.exited is True
