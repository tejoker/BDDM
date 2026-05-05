"""Leanstral-powered claim equivalence judge.

Compares an original LaTeX theorem statement against its translated Lean 4
signature and returns a machine-readable equivalence verdict.  The judge
contributes alignment evidence to the ``claim_equivalent`` gate in
``evaluate_promotion_gates``.  It does NOT replace human review for
release-grade proof: a passing Leanstral verdict raises confidence but is
not sufficient for promotion on its own.

Usage
-----
    from leanstral_judge import leanstral_equivalence_judge
    result = leanstral_equivalence_judge(
        latex_stmt=entry.statement,
        lean_sig=tr.lean_signature,
        client=client,
        model=model,
    )
    # result.flag is "claim_equivalent:leanstral" when confident
    tr.roundtrip_flags = list(tr.roundtrip_flags or []) + result.extra_flags
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You are a formal mathematics judge trained on Lean 4 and LaTeX.\n"
    "Your task: decide whether a Lean 4 theorem signature is semantically "
    "equivalent to the original LaTeX theorem statement.\n\n"
    "Criteria for EQUIVALENT:\n"
    "  • Same universally-quantified variables and their types.\n"
    "  • Same hypotheses (reordering is fine; renaming is fine).\n"
    "  • Same conclusion — not weakened (e.g. dropping a conjunct) and not "
    "strengthened (e.g. adding a vacuous hypothesis).\n"
    "  • Mathematical objects match: ℝ vs ℕ, bounded vs unbounded, etc.\n\n"
    "Respond ONLY with a JSON object — no prose before or after:\n"
    '{"equivalent": true|false, "confidence": 0.0-1.0, "rationale": "..."}\n'
    "confidence = 1.0 means you are certain; 0.5 means a borderline call.\n"
    "Keep the rationale under 120 characters."
)

_USER_TEMPLATE = (
    "LaTeX theorem:\n{latex}\n\n"
    "Lean 4 signature:\n{lean}\n\n"
    "Are they semantically equivalent? Reply with JSON only."
)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class JudgeResult:
    equivalent: bool
    confidence: float
    rationale: str
    flag: str           # primary flag for infer_claim_equivalence
    extra_flags: list[str] = field(default_factory=list)

    @property
    def approved(self) -> bool:
        return self.equivalent and self.confidence >= _threshold()


def _threshold() -> float:
    return float(os.environ.get("DESOL_LEANSTRAL_JUDGE_THRESHOLD", "0.85"))


# ---------------------------------------------------------------------------
# Judge
# ---------------------------------------------------------------------------

def leanstral_equivalence_judge(
    *,
    latex_stmt: str,
    lean_sig: str,
    client: object,
    model: str,
    api_log_hook: object = None,
) -> JudgeResult:
    """Call Leanstral to judge semantic equivalence of a LaTeX→Lean translation.

    Returns a JudgeResult whose ``.flag`` is ``"claim_equivalent:leanstral"``
    when the model is confident the statements are equivalent, or
    ``"leanstral_judge_unclear"`` otherwise.  Caller should append
    ``result.extra_flags`` to the translation's roundtrip_flags so the
    evidence propagates to ``infer_claim_equivalence``.
    """
    if not latex_stmt or not lean_sig:
        return JudgeResult(
            equivalent=False,
            confidence=0.0,
            rationale="empty input",
            flag="leanstral_judge_skipped",
        )

    user = _USER_TEMPLATE.format(
        latex=latex_stmt.strip()[:1200],
        lean=lean_sig.strip()[:1200],
    )

    try:
        raw = _call(client=client, model=model, user=user, api_log_hook=api_log_hook)
    except Exception as exc:
        return JudgeResult(
            equivalent=False,
            confidence=0.0,
            rationale=str(exc)[:120],
            flag="leanstral_judge_error",
            extra_flags=[f"leanstral_judge_error:{str(exc)[:80]}"],
        )

    parsed = _parse_response(raw)
    equivalent = bool(parsed.get("equivalent", False))
    confidence = float(parsed.get("confidence") or 0.0)
    rationale = str(parsed.get("rationale") or "")[:200]

    if equivalent and confidence >= _threshold():
        flag = "claim_equivalent:leanstral"
        extra = [
            "claim_equivalent:leanstral",
            f"leanstral_judge_confidence:{confidence:.2f}",
            "independent_semantic_evidence:leanstral",
        ]
    elif equivalent:
        # Equivalent but below confidence threshold — record but don't auto-approve.
        flag = "leanstral_judge_equivalent_low_confidence"
        extra = [
            f"leanstral_judge_confidence:{confidence:.2f}",
            "leanstral_judge_equivalent_low_confidence",
        ]
    else:
        flag = "leanstral_judge_not_equivalent"
        extra = [
            "leanstral_judge_not_equivalent",
            f"leanstral_judge_confidence:{confidence:.2f}",
        ]

    return JudgeResult(
        equivalent=equivalent,
        confidence=confidence,
        rationale=rationale,
        flag=flag,
        extra_flags=extra,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _call(*, client: object, model: str, user: str, api_log_hook: object) -> str:
    # Use the same chat.complete pattern as the rest of the pipeline.
    try:
        response = client.chat.complete(  # type: ignore[union-attr]
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=180,
        )
    except Exception:
        raise

    text = ""
    if hasattr(response, "choices") and response.choices:
        msg = response.choices[0].message
        text = getattr(msg, "content", "") or ""
    return text.strip()


def _parse_response(raw: str) -> dict:
    m = re.search(r"\{[^{}]*\}", raw, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except (ValueError, TypeError):
        return {}
