"""Hermetic tests for the forbidden-token retry clarification (Improvement 1
in `scripts/leanstral_whole_proof_generator.py`).

When the LLM emits a body containing `sorry` / `admit` / `apply?` / `axiom`
/ `native_decide`, the forbidden-token gate rejects it. Previously the
retry prompt fed an empty `error_tail`, so the LLM had no signal about
WHY its previous attempt was discarded. The clarification surfaces the
rejection reason and lists valid closure alternatives — standards-positive:
we never recommend the forbidden tokens themselves.

All tests are pure-Python: no Mistral, no lake, no subprocess.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

import leanstral_whole_proof_generator as gen


# --- Shared fixtures (mirror test_leanstral_whole_proof_generator.py) -----


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


def _proof_response(
    proof_body: str,
    *,
    reasoning: str = "decompose",
    confidence: float = 0.5,
) -> str:
    return json.dumps({
        "proof_body": proof_body,
        "reasoning": reasoning,
        "confidence": confidence,
    })


# =====================================================================
# 1. build_forbidden_token_clarification returns the expected prefix +
#    standards-positive body.
# =====================================================================

def test_build_forbidden_token_clarification_has_prefix_and_body() -> None:
    out = gen.build_forbidden_token_clarification("sorry")
    assert out.startswith(gen.FORBIDDEN_TOKEN_CLARIFICATION_PREFIX)
    body = out[len(gen.FORBIDDEN_TOKEN_CLARIFICATION_PREFIX):]
    assert "rejected" in body.lower()
    assert "forbidden token" in body.lower()
    # The clarification cites the offending token verbatim.
    assert "`sorry`" in body
    # Standards-positive: lists valid closure tactics; the forbidden tokens
    # are only mentioned in the "leave the proof open" sentence.
    assert "aesop" in body
    assert "omega" in body
    assert "linarith" in body
    assert "rfl" in body


def test_build_forbidden_token_clarification_for_each_forbidden_token() -> None:
    for tok in gen.FORBIDDEN_TOKENS:
        out = gen.build_forbidden_token_clarification(tok)
        assert out.startswith(gen.FORBIDDEN_TOKEN_CLARIFICATION_PREFIX)
        body = out[len(gen.FORBIDDEN_TOKEN_CLARIFICATION_PREFIX):]
        # The cited token in the rejection sentence MUST match exactly.
        assert f"`{tok}`" in body


def test_build_forbidden_token_clarification_empty_token_falls_back() -> None:
    # Empty / whitespace token defaults to a safe placeholder so the
    # caller can't accidentally produce an empty rejection sentence.
    out = gen.build_forbidden_token_clarification("")
    assert out.startswith(gen.FORBIDDEN_TOKEN_CLARIFICATION_PREFIX)
    body = out[len(gen.FORBIDDEN_TOKEN_CLARIFICATION_PREFIX):]
    assert "forbidden token" in body.lower()


# =====================================================================
# 2. build_user_prompt: the forbidden clarification prefix triggers the
#    clarification-specific retry block; a plain error_tail still uses
#    the generic lake-error template.
# =====================================================================

def test_build_user_prompt_embeds_clarification_block() -> None:
    clarification = gen.build_forbidden_token_clarification("sorry")
    user = gen.build_user_prompt(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        error_tail=clarification,
    )
    # The clarification body is embedded (and the prefix sentinel is NOT).
    assert gen.FORBIDDEN_TOKEN_CLARIFICATION_PREFIX not in user
    assert "rejected" in user.lower()
    assert "aesop" in user
    assert "omega" in user
    # The generic "previous attempt failed `lake env lean`" wording is NOT
    # used for forbidden-token clarifications.
    assert "lake env lean" not in user.lower()


def test_build_user_prompt_non_forbidden_error_tail_uses_generic_block() -> None:
    err = "type mismatch at `Nat.le_succ`\nexpected `ℕ` got `Int`"
    user = gen.build_user_prompt(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        error_tail=err,
    )
    assert "type mismatch" in user
    assert "previous attempt failed" in user.lower()
    # No clarification wording when the rejection wasn't forbidden-token.
    assert "rejected because it contained the forbidden token" not in user


# =====================================================================
# 3. generate_proof_candidate: rejection_sink is populated with the
#    forbidden token + clarification on rejection.
# =====================================================================

def test_generate_proof_candidate_populates_rejection_sink_on_forbidden() -> None:
    client = FakeClient(_proof_response("intro h\n  sorry"))
    sink: dict[str, Any] = {}
    out = gen.generate_proof_candidate(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
        rejection_sink=sink,
        use_mathlib_anchors=False,
    )
    # Body rejected.
    assert out is None
    # Sink populated.
    assert sink.get("reason") == "forbidden_token:sorry"
    assert sink.get("token") == "sorry"
    assert sink.get("clarification", "").startswith(gen.FORBIDDEN_TOKEN_CLARIFICATION_PREFIX)


def test_generate_proof_candidate_clean_path_does_not_touch_sink() -> None:
    client = FakeClient(_proof_response("intro h\n  exact h"))
    sink: dict[str, Any] = {}
    out = gen.generate_proof_candidate(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target (h : True) : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
        rejection_sink=sink,
        use_mathlib_anchors=False,
    )
    # Successful candidate.
    assert out is not None
    assert out["proof_body"].startswith("intro h")
    # Sink left untouched on the clean path.
    assert sink == {}


def test_generate_proof_candidate_apply_question_populates_sink() -> None:
    client = FakeClient(_proof_response("apply?"))
    sink: dict[str, Any] = {}
    out = gen.generate_proof_candidate(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
        rejection_sink=sink,
        use_mathlib_anchors=False,
    )
    assert out is None
    assert sink.get("token") == "apply?"
    assert "`apply?`" in sink.get("clarification", "")


def test_generate_proof_candidate_omitting_sink_is_backward_compatible() -> None:
    """Callers that don't pass `rejection_sink` continue to get None on
    forbidden-token rejection (no exception, no telemetry leak)."""
    client = FakeClient(_proof_response("admit"))
    out = gen.generate_proof_candidate(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
        use_mathlib_anchors=False,
    )
    assert out is None


# =====================================================================
# 4. Round-trip: a forbidden-token rejection on round 1 produces a
#    clarification that, when fed to round 2, lands in the user prompt.
# =====================================================================

def test_round_trip_forbidden_rejection_threads_into_retry_prompt() -> None:
    # Round 1: model emits `sorry`. Round 2: model emits a valid body.
    client = FakeClient([
        _proof_response("intro h; sorry"),
        _proof_response("intro h; exact h", reasoning="trivial closure", confidence=0.9),
    ])

    # Round 1 — body rejected, sink populated.
    sink1: dict[str, Any] = {}
    out1 = gen.generate_proof_candidate(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target (h : True) : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        client=client,
        rejection_sink=sink1,
        use_mathlib_anchors=False,
    )
    assert out1 is None
    clarification = sink1["clarification"]

    # Round 2 — feed the clarification as error_tail; capture the user
    # prompt the model receives.
    out2 = gen.generate_proof_candidate(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target (h : True) : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        error_tail=clarification,
        client=client,
        use_mathlib_anchors=False,
    )
    assert out2 is not None
    assert out2["proof_body"] == "intro h; exact h"
    # The round-2 user prompt contained the clarification wording but NOT
    # the sentinel prefix.
    user_round2 = client.chat.calls[1]["messages"][1]["content"]
    assert gen.FORBIDDEN_TOKEN_CLARIFICATION_PREFIX not in user_round2
    assert "rejected" in user_round2.lower()
    assert "aesop" in user_round2
