"""Hermetic tests for the Leanstral whole-proof generator.

The Mistral client is fully mocked; no network calls are made and no `lake`
invocations are run. All file context strings are constructed inline."""
from __future__ import annotations

import json
from typing import Any

import pytest

import leanstral_whole_proof_generator as gen


# --- Mock Mistral client --------------------------------------------------


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
    """A mock Mistral client whose `chat.complete(...)` returns the next item
    from a pre-canned list of response strings."""

    def __init__(self, contents: list[str] | str) -> None:
        if isinstance(contents, str):
            contents = [contents]
        self.chat = _FakeChat(contents)


def _proof_response(
    proof_body: str,
    *,
    reasoning: str = "decompose and apply",
    confidence: float = 0.7,
) -> str:
    return json.dumps(
        {
            "proof_body": proof_body,
            "reasoning": reasoning,
            "confidence": confidence,
        }
    )


# --- Fixtures -------------------------------------------------------------

NEIGHBOUR_SRC = """\
import Mathlib

namespace ArxivPaper

theorem foo (n : ℕ) : 0 ≤ n := by
  exact Nat.zero_le n

theorem bar (n : ℕ) : n + 0 = n := by
  simp

theorem target (n : ℕ) : n ≤ n + 1 := by
  sorry

theorem baz (n : ℕ) : n + 1 ≥ n := by
  linarith
"""


# =====================================================================
# Test 1: happy path — well-formed proof body accepted.
# =====================================================================

def test_well_formed_proof_body_accepted() -> None:
    client = FakeClient(
        _proof_response(
            "intro h\n  exact Nat.le_succ n",
            reasoning="standard succ inequality",
            confidence=0.85,
        )
    )
    out = gen.generate_proof_candidate(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target (n : ℕ) : n ≤ n + 1 := by sorry",
        paper_theory_hint="def Foo := True",
        paper_local_file=NEIGHBOUR_SRC,
        client=client,
    )
    assert out is not None
    assert out["proof_body"].startswith("intro h")
    assert out["confidence"] == pytest.approx(0.85)
    assert out["rejection_reason"] is None
    assert out["protocol"] == "leanstral_whole_proof_v1"
    # The system + user messages were sent in a single call.
    assert len(client.chat.calls) == 1
    msgs = client.chat.calls[0]["messages"]
    assert msgs[0]["role"] == "system"
    assert "sorry" in msgs[0]["content"]  # forbidden-token rule mentioned
    user_content = msgs[1]["content"]
    assert "theorem target" in user_content
    # Neighbour foo/bar/baz should appear in the user prompt.
    assert "theorem foo" in user_content or "theorem bar" in user_content


# =====================================================================
# Test 2: `sorry` in the body must be rejected.
# =====================================================================

def test_sorry_body_rejected() -> None:
    client = FakeClient(_proof_response("intro h\n  sorry"))
    out = gen.generate_proof_candidate(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
    )
    assert out is None


def test_admit_body_rejected() -> None:
    client = FakeClient(_proof_response("admit"))
    out = gen.generate_proof_candidate(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
    )
    assert out is None


# =====================================================================
# Test 3: `apply?` is rejected.
# =====================================================================

def test_apply_question_rejected() -> None:
    client = FakeClient(_proof_response("apply?"))
    out = gen.generate_proof_candidate(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
    )
    assert out is None


def test_native_decide_rejected() -> None:
    client = FakeClient(_proof_response("native_decide"))
    out = gen.generate_proof_candidate(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
    )
    assert out is None


# =====================================================================
# Test 4: malformed JSON -> None.
# =====================================================================

def test_malformed_json_returns_none() -> None:
    client = FakeClient("not a json response at all")
    out = gen.generate_proof_candidate(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
    )
    assert out is None


def test_empty_body_returns_none() -> None:
    client = FakeClient(_proof_response(""))
    out = gen.generate_proof_candidate(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
    )
    assert out is None


# =====================================================================
# Test 5: code-fenced proof body is unwrapped.
# =====================================================================

def test_code_fenced_proof_unwrapped() -> None:
    client = FakeClient(
        _proof_response(
            "```lean\n:= by\nintro h\nexact h\n```",
            confidence=0.6,
        )
    )
    out = gen.generate_proof_candidate(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target (h : True) : True := by sorry",
        paper_theory_hint="",
        paper_local_file=NEIGHBOUR_SRC,
        client=client,
    )
    assert out is not None
    body = out["proof_body"]
    # Fences and leading `:= by` are stripped.
    assert "```" not in body
    assert not body.lstrip().startswith(":=")
    assert "intro h" in body
    assert "exact h" in body


# =====================================================================
# Test 6: retry-with-error-tail injects the tail into the user prompt.
# =====================================================================

def test_retry_with_error_tail_in_user_prompt() -> None:
    err = "type mismatch at `Nat.le_succ`\nexpected `ℕ` got `Int`"
    user = gen.build_user_prompt(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target (n : ℕ) : n ≤ n + 1 := by sorry",
        paper_theory_hint="",
        paper_local_file=NEIGHBOUR_SRC,
        error_tail=err,
    )
    assert "type mismatch" in user
    assert "previous attempt failed" in user.lower()
    # No retry block when error_tail empty.
    user_no_err = gen.build_user_prompt(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target (n : ℕ) : n ≤ n + 1 := by sorry",
        paper_theory_hint="",
        paper_local_file=NEIGHBOUR_SRC,
        error_tail="",
    )
    assert "previous attempt failed" not in user_no_err.lower()


# =====================================================================
# Test 7: neighbour extraction picks declarations close to the target.
# =====================================================================

def test_neighbour_extraction_excludes_target() -> None:
    neighbours = gen._top_neighbour_declarations(
        lean_src=NEIGHBOUR_SRC,
        target_name="target",
    )
    assert "theorem target" not in neighbours
    # Adjacent decls should be included.
    assert "theorem bar" in neighbours
    assert "theorem baz" in neighbours


def test_neighbour_extraction_empty_source() -> None:
    out = gen._top_neighbour_declarations(lean_src="", target_name="target")
    assert out == ""


# =====================================================================
# Test 8: forbidden-token gate uses word boundaries.
# =====================================================================

def test_forbidden_token_word_boundary() -> None:
    # `sorrytown` is NOT a forbidden token — it's an identifier that happens
    # to contain `sorry` as a substring. (Edge case; in practice the LLM
    # would never produce such an identifier in a proof body, but the gate
    # should still respect word boundaries.)
    assert gen._contains_forbidden_token("exact sorrytown") is None
    # `sorry` standalone IS forbidden.
    assert gen._contains_forbidden_token("intro h; sorry") == "sorry"
    # `apply?` substring match is fine because `?` is not an identifier char.
    assert gen._contains_forbidden_token("apply?") == "apply?"
    # `axiom` matches.
    assert gen._contains_forbidden_token("axiom foo : True") == "axiom"


# =====================================================================
# Test 9: client=None or empty statement returns None.
# =====================================================================

def test_no_client_returns_none() -> None:
    out = gen.generate_proof_candidate(
        paper_id="p1",
        theorem_name="t",
        lean_statement="theorem t : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=None,
    )
    assert out is None


def test_empty_statement_returns_none() -> None:
    client = FakeClient(_proof_response("trivial"))
    out = gen.generate_proof_candidate(
        paper_id="p1",
        theorem_name="t",
        lean_statement="",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
    )
    assert out is None
    # No call was made because the input was empty.
    assert client.chat.calls == []
