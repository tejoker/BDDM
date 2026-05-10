from __future__ import annotations

import json
from typing import Any

import pytest

from leanstral_cot_judge import (
    CoTJudgeResult,
    _parse_cot_response,
    leanstral_cot_judge,
)


class _MockMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _MockChoice:
    def __init__(self, content: str) -> None:
        self.message = _MockMessage(content)


class _MockResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_MockChoice(content)]


class _MockChatAPI:
    def __init__(self, content: str) -> None:
        self._content = content

    def complete(self, **_kwargs: Any) -> _MockResponse:
        return _MockResponse(self._content)


class _MockClient:
    def __init__(self, content: str) -> None:
        self.chat = _MockChatAPI(content)


def _make_cot_response(steps: list[dict], verdict: str, *, rationale: str = "ok",
                       adequate_weaker_evidence: bool = False) -> str:
    payload = {
        "reasoning_steps": steps,
        "verdict": verdict,
        "confidence": 0.0,  # ignored by judge — final is min(step_confidence)
        "rationale": rationale,
        "adequate_weaker_evidence": adequate_weaker_evidence,
    }
    return json.dumps(payload)


def _full_steps(step_confs: list[float]) -> list[dict]:
    """All four required reasoning steps with the given per-step confidences."""
    names = ["quantifiers", "hypotheses", "conclusion", "abstraction_check"]
    return [
        {"name": n, "analysis": f"step {n} analysis", "step_confidence": c}
        for n, c in zip(names, step_confs)
    ]


def test_cot_judge_parses_full_response_and_returns_equivalent_at_min_confidence() -> None:
    """The CoT judge should aggregate per-step confidence pessimistically (min)
    and emit an equivalent verdict when all steps confirm match."""
    raw = _make_cot_response(
        _full_steps([0.95, 0.90, 0.92, 0.88]),
        verdict="equivalent",
        rationale="quantifiers/hypotheses/conclusion all line up",
    )
    client = _MockClient(raw)

    result = leanstral_cot_judge(
        latex_stmt=r"For all $n \in \mathbb{N}$, $n + 0 = n$.",
        lean_sig="theorem t (n : ℕ) : n + 0 = n",
        client=client,
        model="mistral-large",
    )

    assert result.equivalent is True
    assert result.confidence == pytest.approx(0.88, abs=0.01)  # min across steps
    assert result.flag == "claim_equivalent:leanstral_cot"
    assert "claim_equivalent:leanstral_cot" in result.extra_flags
    assert "independent_semantic_evidence:leanstral_cot" in result.extra_flags
    assert len(result.reasoning_steps) == 4


def test_cot_judge_treats_adequate_weaker_as_equivalent_with_flag() -> None:
    """`adequate_weaker` verdict should map to equivalent=True (proof-search
    eligible) but carry an explicit `adequate_weaker_evidence` flag so
    downstream release decisions can distinguish strict-equivalent from
    adequate-weaker translations."""
    raw = _make_cot_response(
        _full_steps([0.92, 0.88, 0.85, 0.90]),
        verdict="adequate_weaker",
        rationale="Lean abstracts the constant C the LaTeX gave explicitly",
        adequate_weaker_evidence=True,
    )
    client = _MockClient(raw)

    result = leanstral_cot_judge(
        latex_stmt=r"There exists $C = 4\pi$ such that $|f(x)| \le C$.",
        lean_sig="theorem t (f : ℝ → ℝ) (x : ℝ) : ∃ C : ℝ, |f x| ≤ C",
        client=client,
        model="mistral-large",
    )

    assert result.equivalent is True
    assert result.adequate_weaker_evidence is True
    assert result.flag == "claim_equivalent:leanstral_cot_adequate_weaker"
    assert "leanstral_cot_adequate_weaker" in result.extra_flags
    assert result.raw_verdict == "adequate_weaker"


def test_cot_judge_marks_low_step_confidence_as_low_overall() -> None:
    """If even one step has low confidence, the aggregate must be low — and
    the flag must shift to `equivalent_low_confidence` rather than the strict
    approval flag."""
    raw = _make_cot_response(
        _full_steps([0.95, 0.93, 0.50, 0.91]),  # one weak step
        verdict="equivalent",
    )
    client = _MockClient(raw)

    result = leanstral_cot_judge(
        latex_stmt="Some claim.",
        lean_sig="theorem t : True",
        client=client,
        model="mistral-large",
    )

    assert result.confidence == pytest.approx(0.50, abs=0.01)
    assert result.flag == "leanstral_cot_judge_equivalent_low_confidence"
    assert not result.approved


def test_cot_judge_rejects_when_required_step_missing() -> None:
    """If the model omits one of the required reasoning steps, the judge MUST
    degrade to `unclear` with an `incomplete` flag — never approve."""
    incomplete_steps = _full_steps([0.95, 0.95, 0.95, 0.95])[:3]  # drop last step
    raw = _make_cot_response(incomplete_steps, verdict="equivalent")
    client = _MockClient(raw)

    result = leanstral_cot_judge(
        latex_stmt="x.",
        lean_sig="theorem t : True",
        client=client,
        model="mistral-large",
    )

    assert result.equivalent is False
    assert result.flag == "leanstral_cot_judge_incomplete"
    assert any("missing_step:abstraction_check" in f for f in result.extra_flags)


def test_cot_judge_marks_not_equivalent_with_low_confidence_flag() -> None:
    """A `not_equivalent` verdict gets the dedicated flag and never sets
    equivalent=True regardless of step_confidence."""
    raw = _make_cot_response(
        _full_steps([0.95, 0.95, 0.95, 0.95]),
        verdict="not_equivalent",
        rationale="conclusion is genuinely different",
    )
    client = _MockClient(raw)

    result = leanstral_cot_judge(
        latex_stmt=r"$\forall n, n > 0$.",
        lean_sig="theorem t (n : ℕ) : n < 0",
        client=client,
        model="mistral-large",
    )

    assert result.equivalent is False
    assert result.flag == "leanstral_cot_judge_not_equivalent"


def test_cot_judge_handles_unparseable_response() -> None:
    """Garbage-back response yields `unclear` (not crash, not approve)."""
    client = _MockClient("not json at all")

    result = leanstral_cot_judge(
        latex_stmt="x", lean_sig="y", client=client, model="mistral-large"
    )

    assert result.equivalent is False
    assert result.flag == "leanstral_cot_judge_unclear"


def test_cot_judge_strips_code_fences_around_json() -> None:
    """Models often wrap JSON in ```json``` fences; the parser must tolerate this."""
    inner = _make_cot_response(_full_steps([0.9, 0.9, 0.9, 0.9]), verdict="equivalent")
    fenced = f"```json\n{inner}\n```"
    client = _MockClient(fenced)

    result = leanstral_cot_judge(latex_stmt="x", lean_sig="y", client=client, model="m")
    assert result.equivalent is True


def test_cot_judge_to_judge_result_is_back_compat() -> None:
    """CoTJudgeResult.to_judge_result() yields a legacy JudgeResult with the
    same approved/equivalent/confidence semantics, so existing callers keep working."""
    raw = _make_cot_response(_full_steps([0.9, 0.9, 0.9, 0.9]), verdict="equivalent")
    client = _MockClient(raw)
    result = leanstral_cot_judge(latex_stmt="x", lean_sig="y", client=client, model="m")

    legacy = result.to_judge_result()
    assert legacy.equivalent == result.equivalent
    assert legacy.confidence == result.confidence
    assert legacy.flag == result.flag
    # `approved` semantics carry over.
    assert legacy.approved == result.approved


def test_parse_cot_response_extracts_nested_json() -> None:
    """Direct test of the parser — no LLM call needed."""
    raw = '{"reasoning_steps": [{"name": "x"}], "verdict": "unclear"}'
    parsed = _parse_cot_response(raw)
    assert parsed["verdict"] == "unclear"
    assert parsed["reasoning_steps"][0]["name"] == "x"


def test_parse_cot_response_returns_empty_on_invalid() -> None:
    assert _parse_cot_response("") == {}
    assert _parse_cot_response("hello world") == {}
    assert _parse_cot_response("{not valid json") == {}


def test_judge_dict_from_cot_maps_equivalent_to_reviewed_exact() -> None:
    """When wired into run_auto_alignment_review, a CoT `equivalent` verdict
    must produce alignment_class='reviewed_exact' so the row gets emitted as
    a hybrid-bridge candidate review."""
    from run_auto_alignment_review import _judge_dict_from_cot
    cot = CoTJudgeResult(
        equivalent=True,
        confidence=0.9,
        rationale="all aligned",
        flag="claim_equivalent:leanstral_cot",
        extra_flags=[],
        reasoning_steps=[
            {"name": "quantifiers", "step_confidence": 0.9},
            {"name": "hypotheses", "step_confidence": 0.9},
            {"name": "conclusion", "step_confidence": 0.9},
            {"name": "abstraction_check", "step_confidence": 0.9},
        ],
        adequate_weaker_evidence=False,
        raw_verdict="equivalent",
    )
    judge = _judge_dict_from_cot(cot)
    assert judge["verdict"] == "EQUIVALENT"
    assert judge["alignment_class"] == "reviewed_exact"
    assert judge["confidence"] == 0.9
    assert judge["component_scores"]["hypotheses"] == 0.9
    # protocol must be 'structured_json' so the `judge_output_not_structured_json`
    # gate in run_auto_alignment_review._promotion_blockers admits the row.
    assert judge["protocol"] == "structured_json"
    assert judge["protocol_origin"] == "leanstral_cot"


def test_judge_dict_from_cot_surfaces_adequate_weaker_as_extra_flag() -> None:
    """`adequate_weaker_evidence` is metadata for downstream consumers, NOT
    a promotion blocker. The previous version put it in `blockers` which
    caused every CoT-confirmed adequate_weaker row to be rejected by the
    strict promotion gate. Now it lives in `extra_flags`."""
    from run_auto_alignment_review import _judge_dict_from_cot
    cot = CoTJudgeResult(
        equivalent=True,
        confidence=0.85,
        rationale="abstracted constant",
        flag="claim_equivalent:leanstral_cot_adequate_weaker",
        extra_flags=[],
        reasoning_steps=[
            {"name": "quantifiers", "step_confidence": 0.85},
            {"name": "hypotheses", "step_confidence": 0.85},
            {"name": "conclusion", "step_confidence": 0.85},
            {"name": "abstraction_check", "step_confidence": 0.85},
        ],
        adequate_weaker_evidence=True,
        raw_verdict="adequate_weaker",
    )
    judge = _judge_dict_from_cot(cot)
    assert judge["verdict"] == "EQUIVALENT"
    assert "cot_adequate_weaker_evidence" not in judge["blockers"]
    assert "cot_adequate_weaker_evidence" in judge["extra_flags"]


def test_judge_dict_from_cot_lifts_per_step_scores_to_aggregate_for_equivalent() -> None:
    """When CoT marks the verdict equivalent at confidence 0.85 but a single
    step's per-step confidence is 0.6, the synthesized component_scores must be
    lifted to the aggregate floor (0.85). Without this lift, downstream
    `component_score_low:*` gates spuriously block rows the judge approved."""
    from run_auto_alignment_review import _judge_dict_from_cot
    cot = CoTJudgeResult(
        equivalent=True,
        confidence=0.85,
        rationale="aligned overall, one step uncertain on naming",
        flag="claim_equivalent:leanstral_cot",
        extra_flags=[],
        reasoning_steps=[
            {"name": "quantifiers", "step_confidence": 0.60},  # would otherwise fail 0.78 threshold
            {"name": "hypotheses", "step_confidence": 0.85},
            {"name": "conclusion", "step_confidence": 0.85},
            {"name": "abstraction_check", "step_confidence": 0.85},
        ],
        raw_verdict="equivalent",
    )
    judge = _judge_dict_from_cot(cot)
    # All five component scores must be at or above the aggregate floor (0.85).
    for k, v in judge["component_scores"].items():
        assert v >= 0.85, f"component_scores[{k}]={v} below aggregate floor 0.85"


def test_judge_dict_from_cot_marks_not_equivalent_correctly() -> None:
    from run_auto_alignment_review import _judge_dict_from_cot
    cot = CoTJudgeResult(
        equivalent=False,
        confidence=0.4,
        rationale="conclusion shape differs",
        flag="leanstral_cot_judge_not_equivalent",
        extra_flags=[],
        reasoning_steps=[
            {"name": "quantifiers", "step_confidence": 0.9},
            {"name": "hypotheses", "step_confidence": 0.9},
            {"name": "conclusion", "step_confidence": 0.4},
            {"name": "abstraction_check", "step_confidence": 0.9},
        ],
        raw_verdict="not_equivalent",
    )
    judge = _judge_dict_from_cot(cot)
    assert judge["verdict"] == "NOT_EQUIVALENT"
    assert judge["alignment_class"] == "unrelated"
