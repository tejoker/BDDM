"""Hermetic tests for the multi-shot parallel proof attempt generator.

The Mistral client is fully mocked; no network calls and no `lake`
invocations. Every test exercises
`leanstral_whole_proof_generator.generate_proof_candidates_multi_shot`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Ensure scripts/ is on sys.path so we can import the generator module.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import leanstral_whole_proof_generator as gen  # noqa: E402


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
    def __init__(self, contents: list[str]) -> None:
        self.chat = _FakeChat(contents)


def _proof_response(
    proof_body: str,
    *,
    reasoning: str = "decompose",
    confidence: float = 0.7,
) -> str:
    return json.dumps(
        {
            "proof_body": proof_body,
            "reasoning": reasoning,
            "confidence": confidence,
        }
    )


NEIGHBOUR_SRC = """\
import Mathlib

namespace ArxivPaper

theorem target (n : ℕ) : n ≤ n + 1 := by
  sorry
"""

# A common kwargs set so each test stays terse.
_BASE_KWARGS: dict[str, Any] = dict(
    paper_id="p1",
    theorem_name="target",
    lean_statement="theorem target (n : ℕ) : n ≤ n + 1 := by sorry",
    paper_theory_hint="-- nothing",
    paper_local_file=NEIGHBOUR_SRC,
)


# =====================================================================
# Test 1: 5 mock samples, 1 valid -> returns the valid one
# =====================================================================

def test_one_of_five_valid_returns_only_that_one() -> None:
    """Sample 0/1/2 produce non-elaborating bodies (validator says no);
    sample 3 produces an elaborating body. The function MUST short-circuit
    and return only sample 3. Samples 4 should never be requested."""
    contents = [
        _proof_response("intro h\n  exact h"),          # sample 0
        _proof_response("intro h\n  apply Nat.le_succ"),  # sample 1
        _proof_response("intro h\n  exact le_refl _"),    # sample 2
        _proof_response("exact Nat.le_succ n"),           # sample 3 (the winner)
        _proof_response("exact ?_"),                       # sample 4 (must NOT be requested)
    ]
    client = FakeClient(contents)

    def validator(cand: dict[str, Any]) -> tuple[bool, str]:
        return (cand["proof_body"] == "exact Nat.le_succ n", "")

    out = gen.generate_proof_candidates_multi_shot(
        client=client,
        n_samples=5,
        use_mathlib_anchors=False,
        validate_elaboration=validator,
        **_BASE_KWARGS,
    )
    assert isinstance(out, list)
    assert len(out) == 1
    assert out[0]["proof_body"] == "exact Nat.le_succ n"
    assert out[0]["sample_idx"] == 3
    assert out[0]["elaboration_ok"] is True
    # Short-circuit: only 4 LLM calls happened (samples 0..3), NOT 5.
    assert len(client.chat.calls) == 4


# =====================================================================
# Test 2: 5 mock samples, 0 valid -> returns the FULL sorted list
# =====================================================================

def test_none_valid_returns_full_sorted_list() -> None:
    """When no candidate passes the elaboration gate, the function returns
    every forbidden-gate survivor sorted by (elaboration_ok desc,
    temperature asc). With 5 all-invalid samples we expect 5 entries in
    ladder order (0.0/0.3/0.5/0.7/0.9)."""
    contents = [
        _proof_response(f"intro h\n  exact h_{i}") for i in range(5)
    ]
    client = FakeClient(contents)

    def validator(cand: dict[str, Any]) -> tuple[bool, str]:
        return (False, "no_match")

    out = gen.generate_proof_candidates_multi_shot(
        client=client,
        n_samples=5,
        use_mathlib_anchors=False,
        validate_elaboration=validator,
        **_BASE_KWARGS,
    )
    assert len(out) == 5
    # All elaboration_ok=False; sort key is temperature asc.
    temps = [c["temperature"] for c in out]
    assert temps == sorted(temps)
    # Post-Round-XXI: temperature ladder extended to 8-step
    # (0.0, 0.15, 0.30, 0.45, 0.60, 0.75, 0.90, 1.00). With n_samples=5
    # we take the first 5 entries.
    assert temps == [0.0, 0.15, 0.30, 0.45, 0.60]
    assert all(c["elaboration_ok"] is False for c in out)
    assert all(c["elaboration_error"] == "no_match" for c in out)
    # All 5 calls were made (no short-circuit).
    assert len(client.chat.calls) == 5


# =====================================================================
# Test 3: temperature diversification — same statement -> distinct calls
# =====================================================================

def test_temperature_diversification_in_dispatched_calls() -> None:
    """The 5 dispatched calls must use DISTINCT temperatures matching the
    default ladder (post-Round-XXI: 8-step ladder, first 5 entries
    0.0, 0.15, 0.30, 0.45, 0.60). Same lean_statement input,
    different `temperature` kwarg per call."""
    contents = [_proof_response(f"sample_{i}") for i in range(5)]
    client = FakeClient(contents)

    out = gen.generate_proof_candidates_multi_shot(
        client=client,
        n_samples=5,
        use_mathlib_anchors=False,
        validate_elaboration=lambda c: (False, ""),
        **_BASE_KWARGS,
    )
    assert len(out) == 5
    temps_called = [call["temperature"] for call in client.chat.calls]
    assert temps_called == [0.0, 0.15, 0.30, 0.45, 0.60]
    # The same user-message text is used for every call (prompt unchanged).
    user_msgs = [call["messages"][1]["content"] for call in client.chat.calls]
    assert all(m == user_msgs[0] for m in user_msgs)


# =====================================================================
# Test 4: forbidden-token filter is applied per-sample
# =====================================================================

def test_forbidden_token_filter_per_sample() -> None:
    """Sample 0 has a `sorry`-containing body and MUST be rejected by the
    forbidden-token gate (never reaches the validator). Sample 1 is clean
    and is the validated winner."""
    contents = [
        _proof_response("intro h\n  sorry"),     # sample 0: forbidden
        _proof_response("intro h\n  exact h"),    # sample 1: clean winner
    ]
    client = FakeClient(contents)

    seen_by_validator: list[str] = []

    def validator(cand: dict[str, Any]) -> tuple[bool, str]:
        seen_by_validator.append(cand["proof_body"])
        return (True, "")

    out = gen.generate_proof_candidates_multi_shot(
        client=client,
        n_samples=2,
        use_mathlib_anchors=False,
        validate_elaboration=validator,
        **_BASE_KWARGS,
    )
    # Only the clean candidate reached the validator.
    assert seen_by_validator == ["intro h\n  exact h"]
    # And the function short-circuited on sample 1.
    assert len(out) == 1
    assert out[0]["sample_idx"] == 1
    assert out[0]["proof_body"] == "intro h\n  exact h"


# =====================================================================
# Test 5: validate_elaboration callback called per-sample
# =====================================================================

def test_validate_elaboration_called_once_per_clean_sample() -> None:
    """With 3 clean samples (no forbidden tokens) and a validator that
    rejects all of them, we expect EXACTLY 3 validator invocations."""
    contents = [_proof_response(f"intro h\n  exact h_{i}") for i in range(3)]
    client = FakeClient(contents)
    call_count = {"n": 0}

    def validator(cand: dict[str, Any]) -> tuple[bool, str]:
        call_count["n"] += 1
        return (False, "")

    out = gen.generate_proof_candidates_multi_shot(
        client=client,
        n_samples=3,
        use_mathlib_anchors=False,
        validate_elaboration=validator,
        **_BASE_KWARGS,
    )
    assert call_count["n"] == 3
    assert len(out) == 3  # all returned, none short-circuited


# =====================================================================
# Test 6: short-circuit when no validator is supplied
# =====================================================================

def test_no_validator_short_circuits_on_first_forbidden_survivor() -> None:
    """When `validate_elaboration` is None, the function short-circuits on
    the FIRST forbidden-gate survivor (the downstream caller becomes the
    load-bearing validator). With samples [forbidden, clean, clean], we
    expect the SECOND call to win and the third call to never happen."""
    contents = [
        _proof_response("intro h\n  sorry"),    # forbidden
        _proof_response("intro h\n  exact h"),   # winner (no validator gate)
        _proof_response("intro h\n  rfl"),       # must NOT be called
    ]
    client = FakeClient(contents)

    out = gen.generate_proof_candidates_multi_shot(
        client=client,
        n_samples=3,
        use_mathlib_anchors=False,
        validate_elaboration=None,
        **_BASE_KWARGS,
    )
    assert len(out) == 1
    assert out[0]["sample_idx"] == 1
    assert out[0]["proof_body"] == "intro h\n  exact h"
    # Two calls made (sample 0 then sample 1); sample 2 never dispatched.
    assert len(client.chat.calls) == 2


# =====================================================================
# Test 7: rejection_sink threading
# =====================================================================

def test_rejection_sink_populated_with_winning_metadata() -> None:
    """When `rejection_sink` is supplied and the loop short-circuits on a
    win, the sink MUST record the winning sample_idx / temperature plus
    a per-sample rejection_log entry for any preceding miss."""
    contents = [
        _proof_response("intro h\n  sorry"),     # rejected by forbidden gate
        _proof_response("exact Nat.le_succ n"),   # validated winner
    ]
    client = FakeClient(contents)

    sink: dict[str, Any] = {}
    out = gen.generate_proof_candidates_multi_shot(
        client=client,
        n_samples=2,
        use_mathlib_anchors=False,
        validate_elaboration=lambda c: (True, ""),
        rejection_sink=sink,
        **_BASE_KWARGS,
    )
    assert len(out) == 1
    assert sink["short_circuited"] is True
    assert sink["winning_sample_idx"] == 1
    # Post-Round-XXI 8-step ladder: 2nd entry is 0.15.
    assert sink["winning_temperature"] == pytest.approx(0.15)
    log = sink["rejection_log"]
    assert len(log) == 1
    assert log[0]["sample_idx"] == 0
    assert "forbidden_token" in log[0]["reason"]


# =====================================================================
# Test 8: custom temperature ladder respected
# =====================================================================

def test_custom_temperature_ladder() -> None:
    """A caller may pass a custom temperature tuple; we MUST use it."""
    contents = [_proof_response("a"), _proof_response("b"), _proof_response("c")]
    client = FakeClient(contents)
    out = gen.generate_proof_candidates_multi_shot(
        client=client,
        n_samples=3,
        temperatures=(0.1, 0.4, 0.8),
        use_mathlib_anchors=False,
        validate_elaboration=lambda c: (False, ""),
        **_BASE_KWARGS,
    )
    temps_called = [call["temperature"] for call in client.chat.calls]
    assert temps_called == [0.1, 0.4, 0.8]
    # And the returned candidates carry the right temperatures.
    assert sorted(c["temperature"] for c in out) == [0.1, 0.4, 0.8]


# =====================================================================
# Test 9: n_samples > len(ladder) -> extend by repeating last
# =====================================================================

def test_n_samples_exceeds_ladder_extends_by_repeating_last() -> None:
    contents = [_proof_response(f"x{i}") for i in range(6)]
    client = FakeClient(contents)
    out = gen.generate_proof_candidates_multi_shot(
        client=client,
        n_samples=6,
        temperatures=(0.0, 0.5),  # only 2 entries, want 6 samples
        use_mathlib_anchors=False,
        validate_elaboration=lambda c: (False, ""),
        **_BASE_KWARGS,
    )
    temps_called = [call["temperature"] for call in client.chat.calls]
    assert temps_called == [0.0, 0.5, 0.5, 0.5, 0.5, 0.5]
    assert len(out) == 6


# =====================================================================
# Test 10: n_samples=0 and empty statement guards
# =====================================================================

def test_n_samples_zero_returns_empty() -> None:
    client = FakeClient([_proof_response("x")])
    out = gen.generate_proof_candidates_multi_shot(
        client=client,
        n_samples=0,
        use_mathlib_anchors=False,
        validate_elaboration=lambda c: (True, ""),
        **_BASE_KWARGS,
    )
    assert out == []
    assert len(client.chat.calls) == 0


def test_empty_statement_returns_empty() -> None:
    client = FakeClient([_proof_response("x")])
    kwargs = dict(_BASE_KWARGS)
    kwargs["lean_statement"] = "   "
    out = gen.generate_proof_candidates_multi_shot(
        client=client,
        n_samples=3,
        use_mathlib_anchors=False,
        validate_elaboration=lambda c: (True, ""),
        **kwargs,
    )
    assert out == []
    assert len(client.chat.calls) == 0


def test_client_none_returns_empty() -> None:
    out = gen.generate_proof_candidates_multi_shot(
        client=None,
        n_samples=3,
        use_mathlib_anchors=False,
        validate_elaboration=lambda c: (True, ""),
        **_BASE_KWARGS,
    )
    assert out == []
