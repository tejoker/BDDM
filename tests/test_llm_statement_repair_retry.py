"""Hermetic tests for the error-feedback retry loop in LLM statement repair.

The retry loop in `llm_statement_repair.generate_llm_repair_candidate` accepts
an optional `validate_elaboration` callback. When it returns `(False, error)`
the loop builds a follow-up prompt that feeds the Lean error tail back to the
LLM and retries, up to `max_repair_rounds`. These tests use a fully scripted
fake Mistral client + a fake elaboration validator; no network or `lake`
invocations.
"""
from __future__ import annotations

import json
from typing import Any

import llm_statement_repair as lsr


# --- Scripted fake Mistral client ----------------------------------------


class _Msg:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]


class _ScriptedChat:
    """Returns the next pre-scripted response on each `.complete()` call.

    Records (kwargs) of every call so tests can assert on prompt content.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> _Resp:
        self.calls.append(kwargs)
        if not self._responses:
            raise RuntimeError("scripted_chat_exhausted")
        return _Resp(self._responses.pop(0))


class ScriptedClient:
    def __init__(self, responses: list[str]) -> None:
        self.chat = _ScriptedChat(responses)


def _ok_response(decl: str, *, reasoning: str = "encodes claim", confidence: float = 0.8) -> str:
    return json.dumps(
        {
            "verdict": "SIGNATURE",
            "lean_signature": decl,
            "reasoning": reasoning,
            "confidence": confidence,
        }
    )


class _ScriptedValidator:
    """Records every `validate_elaboration(decl)` call, returns scripted verdicts."""

    def __init__(self, verdicts: list[tuple[bool, str]]) -> None:
        self._verdicts = list(verdicts)
        self.calls: list[str] = []

    def __call__(self, decl: str) -> tuple[bool, str]:
        self.calls.append(decl)
        if not self._verdicts:
            raise RuntimeError("scripted_validator_exhausted")
        return self._verdicts.pop(0)


# --- Tests ---------------------------------------------------------------


def test_first_attempt_succeeds_one_llm_call() -> None:
    """When elaboration succeeds on round 1, no retry is needed."""
    # Use a non-trivial claim — the post-Round-X trivialization gate rejects
    # reflexive shapes like `n = n`, so fixtures must carry real content.
    client = ScriptedClient(
        [_ok_response("theorem foo (n : Nat) (h : 0 < n) : 0 < 2 * n := by sorry")]
    )
    validator = _ScriptedValidator([(True, "")])

    result = lsr.generate_llm_repair_candidate(
        source_latex="For positive naturals, 2n is positive.",
        paper_id="p",
        theorem_name="foo",
        paper_theory_hint="",
        client=client,
        max_repair_rounds=3,
        validate_elaboration=validator,
    )

    assert result is not None
    assert result["rejected"] == []
    assert "foo" in result["repaired_decl"]
    assert result["retry_rounds"] == 1
    assert len(client.chat.calls) == 1
    assert len(validator.calls) == 1
    history = result.get("retry_history") or []
    assert len(history) == 1
    assert history[0]["elaboration_ok"] is True


def test_first_fails_second_succeeds_two_llm_calls() -> None:
    """Round 1 fails elaboration, round 2 succeeds. Final candidate is round 2's."""
    bad = "theorem foo (alpha : ConjugacyClass) (h : 0 < alpha) : 0 < 2 * alpha := by sorry"
    good = "theorem foo (n : Nat) (h : 0 < n) : 0 < 2 * n := by sorry"
    client = ScriptedClient([_ok_response(bad), _ok_response(good)])
    error_tail = (
        "error(lean.synthInstanceFailed): failed to synthesize instance"
        " of type class\n  Inhabited ConjugacyClass"
    )
    validator = _ScriptedValidator([(False, error_tail), (True, "")])

    result = lsr.generate_llm_repair_candidate(
        source_latex="Reflexivity claim.",
        paper_id="p",
        theorem_name="foo",
        paper_theory_hint="",
        client=client,
        max_repair_rounds=3,
        validate_elaboration=validator,
    )

    assert result is not None
    assert result["rejected"] == []
    assert "(n : Nat)" in result["repaired_decl"] or "n : Nat" in result["repaired_decl"]
    assert result["retry_rounds"] == 2
    assert len(client.chat.calls) == 2
    assert len(validator.calls) == 2
    history = result["retry_history"]
    assert len(history) == 2
    assert history[0]["elaboration_ok"] is False
    assert history[1]["elaboration_ok"] is True


def test_all_rounds_fail_returns_rejected_dict_with_n_calls() -> None:
    """All N rounds fail elaboration; result is rejected with N LLM calls made."""
    # Use decls that survive the trivialization/placeholder gate (have real
    # hypotheses + a non-trivial conclusion) but would fail elaboration in
    # reality (`MissingType` is an unknown identifier).
    bad1 = "theorem foo (x : MissingType) (h : 0 < x) : 0 < 2 * x := by sorry"
    bad2 = "theorem foo (x : MissingTypeV2) (h : 0 < x) : 0 < 2 * x := by sorry"
    bad3 = "theorem foo (x : MissingTypeV3) (h : 0 < x) : 0 < 2 * x := by sorry"
    client = ScriptedClient([_ok_response(bad1), _ok_response(bad2), _ok_response(bad3)])
    err = "error(lean.unknownIdentifier): Unknown constant `MissingType`"
    validator = _ScriptedValidator([(False, err), (False, err), (False, err)])

    result = lsr.generate_llm_repair_candidate(
        source_latex="Some claim about Frobnicate.",
        paper_id="p",
        theorem_name="foo",
        paper_theory_hint="",
        client=client,
        max_repair_rounds=3,
        validate_elaboration=validator,
    )

    assert result is not None
    # Final result is a rejected dict — no usable candidate.
    assert result["repaired_decl"] == ""
    assert "elaboration_gate_after_retry" in result["rejected"]
    assert result["retry_rounds"] == 3
    assert len(client.chat.calls) == 3
    assert len(validator.calls) == 3
    assert result.get("candidate_decl_before_rejection")
    history = result["retry_history"]
    assert len(history) == 3
    assert all(h["elaboration_ok"] is False for h in history)


def test_error_tail_relayed_into_follow_up_prompt() -> None:
    """The Lean error tail from round 1 must appear in the round-2 user prompt."""
    bad = "theorem foo (x : MissingType) : 0 = 0 := by sorry"
    good = "theorem foo (n : Nat) : 0 = 0 := by sorry"

    # Wait — `0 = 0` would trip the placeholder gate. Use a real claim.
    bad = "theorem foo (x : MissingType) (h : 0 < x) : 0 < 2 * x := by sorry"
    good = "theorem foo (n : Nat) (h : 0 < n) : 0 < 2 * n := by sorry"

    client = ScriptedClient([_ok_response(bad), _ok_response(good)])
    error_tail = "error(lean.unknownIdentifier): Unknown identifier `MissingType` at line 3"
    validator = _ScriptedValidator([(False, error_tail), (True, "")])

    result = lsr.generate_llm_repair_candidate(
        source_latex="For all positive naturals n, 2n is positive.",
        paper_id="p",
        theorem_name="foo",
        paper_theory_hint="",
        client=client,
        max_repair_rounds=3,
        validate_elaboration=validator,
    )

    assert result is not None
    assert result["rejected"] == []
    assert result["retry_rounds"] == 2

    # The follow-up prompt (call #2) must contain the error tail AND the
    # rejected candidate from round 1.
    follow_up_messages = client.chat.calls[1]["messages"]
    user_prompt = follow_up_messages[-1]["content"]
    assert "Lean elaboration error:" in user_prompt
    assert "Unknown identifier" in user_prompt
    assert "MissingType" in user_prompt
    # The previous candidate is included so the LLM can target the specific issue.
    assert "MissingType" in user_prompt  # double-check structurally
    # The original LaTeX is preserved unchanged.
    assert "positive naturals" in user_prompt


def test_first_round_user_prompt_does_not_include_error_section() -> None:
    """On round 1, the prompt is the initial template (no error tail section)."""
    client = ScriptedClient([_ok_response("theorem foo (n : Nat) : n + 0 = n := by sorry")])
    validator = _ScriptedValidator([(True, "")])

    lsr.generate_llm_repair_candidate(
        source_latex="Natural-number identity n + 0 = n.",
        paper_id="p",
        theorem_name="foo",
        paper_theory_hint="",
        client=client,
        max_repair_rounds=3,
        validate_elaboration=validator,
    )

    initial_messages = client.chat.calls[0]["messages"]
    user_prompt = initial_messages[-1]["content"]
    assert "Lean elaboration error:" not in user_prompt
    assert "was REJECTED" not in user_prompt


def test_elaboration_called_between_attempts_not_after_final_when_succeed_early() -> None:
    """Validator is called exactly once per LLM call (after each round), and the
    loop terminates as soon as elaboration passes — no extra validator call."""
    client = ScriptedClient(
        [
            _ok_response("theorem foo (n : Nat) (h : 0 < n) : 0 < 2 * n := by sorry"),
            # Second response should never be consumed.
            _ok_response("theorem foo (m : Nat) : m = m := by sorry"),
        ]
    )
    validator = _ScriptedValidator([(True, ""), (False, "should_not_be_called")])

    result = lsr.generate_llm_repair_candidate(
        source_latex="Reflexivity.",
        paper_id="p",
        theorem_name="foo",
        paper_theory_hint="",
        client=client,
        max_repair_rounds=5,
        validate_elaboration=validator,
    )

    assert result is not None
    assert result["rejected"] == []
    # Only one LLM call and one validator call — the loop short-circuited.
    assert len(client.chat.calls) == 1
    assert len(validator.calls) == 1


def test_no_validator_means_single_attempt_legacy_semantics() -> None:
    """When `validate_elaboration=None`, the loop behaves like the pre-retry path:
    one LLM call, no retry attempted even if `max_repair_rounds=3`."""
    client = ScriptedClient([_ok_response("theorem foo (n : Nat) (h : 0 < n) : 0 < 2 * n := by sorry")])

    result = lsr.generate_llm_repair_candidate(
        source_latex="Reflexivity.",
        paper_id="p",
        theorem_name="foo",
        paper_theory_hint="",
        client=client,
        max_repair_rounds=3,
        validate_elaboration=None,
    )

    assert result is not None
    assert result["rejected"] == []
    assert len(client.chat.calls) == 1
    assert result["retry_rounds"] == 1


def test_max_rounds_one_disables_retry_even_with_validator() -> None:
    """`max_repair_rounds=1` is single-attempt regardless of validator verdict."""
    client = ScriptedClient(
        [_ok_response("theorem foo (x : MissingType) (h : 0 < x) : 0 < 2 * x := by sorry")]
    )
    validator = _ScriptedValidator([(False, "error: Unknown constant `MissingType`")])

    result = lsr.generate_llm_repair_candidate(
        source_latex="Some claim.",
        paper_id="p",
        theorem_name="foo",
        paper_theory_hint="",
        client=client,
        max_repair_rounds=1,
        validate_elaboration=validator,
    )

    assert result is not None
    assert result["repaired_decl"] == ""
    assert "elaboration_gate_after_retry" in result["rejected"]
    # Only one LLM call was made.
    assert len(client.chat.calls) == 1
    assert result["retry_rounds"] == 1


def test_trivialization_failure_does_not_trigger_retry() -> None:
    """A placeholder/trivialized decl is rejected by the synchronous gate; we
    don't waste LLM calls retrying it because the elaboration validator never
    sees it."""
    # Trivial existential is caught by `_is_trivialized_signature`.
    client = ScriptedClient(
        [
            _ok_response("theorem foo : ∃ x : ℝ, x = x := by sorry"),
            # This second response should NOT be consumed.
            _ok_response("theorem foo (n : Nat) (h : 0 < n) : 0 < 2 * n := by sorry"),
        ]
    )
    validator = _ScriptedValidator([(True, "")])

    result = lsr.generate_llm_repair_candidate(
        source_latex="A real claim about a quantity that exists.",
        paper_id="p",
        theorem_name="foo",
        paper_theory_hint="",
        client=client,
        max_repair_rounds=3,
        validate_elaboration=validator,
    )

    assert result is not None
    assert result["repaired_decl"] == ""
    assert result["rejected"]  # at least one rejection reason
    # Only ONE LLM call: trivialization gate short-circuits before the validator.
    assert len(client.chat.calls) == 1
    assert validator.calls == []
    assert result["retry_rounds"] == 1


def test_retry_history_contains_truncated_error_tail() -> None:
    """The Lean error tail in the retry history is truncated to 500 chars."""
    bad = "theorem foo (x : MissingType) (h : 0 < x) : 0 < 2 * x := by sorry"
    good = "theorem foo (n : Nat) : n + 0 = n := by sorry"
    long_error = "X" * 800 + "TAIL_MARKER"  # 811 chars; last 500 will be kept.
    client = ScriptedClient([_ok_response(bad), _ok_response(good)])
    validator = _ScriptedValidator([(False, long_error), (True, "")])

    result = lsr.generate_llm_repair_candidate(
        source_latex="Identity n + 0 = n.",
        paper_id="p",
        theorem_name="foo",
        paper_theory_hint="",
        client=client,
        max_repair_rounds=3,
        validate_elaboration=validator,
    )

    assert result is not None
    history = result["retry_history"]
    # First round's recorded error tail is exactly the trailing 500 chars.
    recorded = history[0]["lean_error_tail"]
    assert len(recorded) == 500
    assert recorded.endswith("TAIL_MARKER")
    # The follow-up prompt got the truncated tail (not the full 800-char string).
    follow_up_prompt = client.chat.calls[1]["messages"][-1]["content"]
    assert "TAIL_MARKER" in follow_up_prompt
