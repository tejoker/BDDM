"""Status/quality/failure classifiers extracted from pipeline_status."""

from __future__ import annotations

import os
import re
from typing import Any, Callable

from pipeline_status_models import (
    Assumption,
    GroundingStatus,
    StatusDecision,
    StepObligation,
    StepVerdict,
    FailureOrigin,
    VerificationStatus,
)


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def infer_quality_scores(
    *,
    proved: bool,
    step_records: list[Any],
    error_message: str,
    lean_statement: str,
    translation_fidelity_score: float | None = None,
    status_alignment_score: float | None = None,
    translation_validated: bool | None = None,
    translation_rounds_used: int | None = None,
    translation_uncertainty_flags: list[str] | None = None,
    translation_confidence: float | None = None,
    had_exception: bool = False,
) -> tuple[float, float]:
    fidelity = translation_fidelity_score
    alignment = status_alignment_score

    if fidelity is None:
        if translation_confidence is not None:
            fidelity = clamp01(translation_confidence)
        elif translation_validated is not None:
            base = 0.90 if translation_validated else 0.30
            rounds = max(0, int(translation_rounds_used or 0) - 1)
            penalty_rounds = min(0.20, 0.05 * rounds)
            flags = translation_uncertainty_flags or []
            penalty_flags = min(0.20, 0.04 * len(flags))
            fidelity = clamp01(base - penalty_rounds - penalty_flags)
        else:
            stmt_l = (lean_statement or "").strip().lower()
            if stmt_l.startswith("theorem ") or stmt_l.startswith("lemma "):
                fidelity = 0.80
            elif stmt_l:
                fidelity = 0.65
            else:
                fidelity = 0.40

    if alignment is None:
        record_results: list[str] = []
        for rec in step_records:
            if isinstance(rec, dict):
                record_results.append(str(rec.get("result", "")).strip().lower())
            else:
                record_results.append(str(getattr(rec, "result", "")).strip().lower())

        if proved:
            if "proof-finished" in record_results:
                alignment = 0.95
            elif "state-advanced" in record_results:
                alignment = 0.85
            else:
                alignment = 0.75
        else:
            if had_exception:
                alignment = 0.65
            elif any(r in {"lean-error", "proof-given-up"} for r in record_results):
                alignment = 0.88
            elif error_message.strip():
                alignment = 0.80
            else:
                alignment = 0.70

    return clamp01(fidelity), clamp01(alignment)


def infer_status(
    *,
    proved: bool,
    step_obligations: list[StepObligation],
    assumptions: list[Assumption],
    step_verdict: StepVerdict,
    error: str = "",
) -> VerificationStatus:
    if proved:
        ungrounded = [
            a for a in assumptions
            if a.grounding in (GroundingStatus.UNGROUNDED, GroundingStatus.UNKNOWN)
        ]
        if not ungrounded:
            return VerificationStatus.FULLY_PROVEN
        return VerificationStatus.INTERMEDIARY_PROVEN

    if step_verdict == StepVerdict.FLAWED:
        return VerificationStatus.FLAWED

    return VerificationStatus.UNRESOLVED


def derive_step_verdict(
    *,
    proved: bool,
    step_obligations: list[StepObligation],
    error_message: str = "",
    assess_step_entailment_fn: Callable[[list[StepObligation]], Any] | None = None,
) -> StepVerdict:
    if proved:
        return StepVerdict.VERIFIED

    if not step_obligations:
        err_l = (error_message or "").lower()
        if any(tok in err_l for tok in ("lean-error", "tactic failed", "proof-given-up", "could not", "failed")):
            return StepVerdict.FLAWED
        return StepVerdict.INCOMPLETE

    error_l = (error_message or "").lower()
    if "timeout" in error_l or "interrupted" in error_l:
        return StepVerdict.INCOMPLETE

    failing_results = {"lean-error", "proof-given-up"}
    if any((s.result or "").strip().lower() in failing_results for s in step_obligations):
        return StepVerdict.FLAWED

    if any("failed" in (s.detail or "").lower() for s in step_obligations):
        return StepVerdict.FLAWED

    if os.environ.get("DESOL_ENABLE_STEP_ENTAILMENT", "0") == "1" and assess_step_entailment_fn is not None:
        try:
            assessment = assess_step_entailment_fn(step_obligations)
            if assessment.is_flawed:
                return StepVerdict.FLAWED
        except Exception:
            pass

    if any(s.verified for s in step_obligations):
        return StepVerdict.INCOMPLETE

    return StepVerdict.INCOMPLETE


def infer_failure_origin(
    *,
    proved: bool,
    lean_statement: str,
    step_obligations: list[StepObligation],
    step_records: list[Any] | None = None,
    error_message: str = "",
    min_false_seeds: int = 3,
) -> FailureOrigin:
    if proved:
        return FailureOrigin.NOT_FAILED

    stmt_l = (lean_statement or "").strip().lower()
    err_l = (error_message or "").lower()

    if stmt_l.startswith("def ") or "not a proposition" in err_l:
        return FailureOrigin.FORMALIZATION_ERROR

    formalization_markers = (
        "unknown identifier",
        "unknown constant",
        "invalid field",
        "unexpected token",
        "type mismatch",
        "not found in source",
        "translation",
        "elaborate",
    )
    if any(m in err_l for m in formalization_markers):
        return FailureOrigin.FORMALIZATION_ERROR

    search_markers = (
        "timeout",
        "proof-given-up",
        "failed after repair_rounds",
        "no proof backend available",
        "interrupted",
        "keyboardinterrupt",
        "mcts",
        "parallel draft mcts exhausted",
        "no successful workers",
        "resource exhausted",
    )
    if any(m in err_l for m in search_markers):
        return FailureOrigin.PROOF_SEARCH_ERROR

    records = step_records or []
    distinct_attempts: set[tuple[int, int]] = set()
    lean_error_records = 0
    contradiction_like_records = 0
    contradiction_markers = (
        "contradiction",
        "false",
        "not provable",
        "cannot be proved",
        "failed to close goal",
        "unsolved goals",
        "no goals to be solved",
        "tactic failed",
    )
    for rec in records:
        if isinstance(rec, dict):
            step_idx = int(rec.get("step", 0) or 0)
            attempt_idx = int(rec.get("attempt", 0) or 0)
            result = str(rec.get("result", "")).strip().lower()
            detail = str(rec.get("detail", "")).lower()
        else:
            step_idx = int(getattr(rec, "step", 0) or 0)
            attempt_idx = int(getattr(rec, "attempt", 0) or 0)
            result = str(getattr(rec, "result", "")).strip().lower()
            detail = str(getattr(rec, "detail", "")).lower()

        if result in {"lean-error", "proof-given-up"}:
            lean_error_records += 1
            distinct_attempts.add((step_idx, attempt_idx))
            if any(tok in detail for tok in contradiction_markers):
                contradiction_like_records += 1

    worker_ids = {m.group(1) for m in re.finditer(r"worker\s+(\d+)\s*:", err_l)}
    independent_runs = max(len(distinct_attempts), len(worker_ids))

    if (
        step_obligations
        and all((s.result or "").lower() == "lean-error" for s in step_obligations)
        and independent_runs >= min_false_seeds
        and contradiction_like_records >= min_false_seeds
    ):
        return FailureOrigin.POSSIBLY_FALSE_STATEMENT

    if lean_error_records > 0 and independent_runs < min_false_seeds:
        return FailureOrigin.PROOF_SEARCH_ERROR

    return FailureOrigin.UNKNOWN


def reconstruct_step_obligations(
    *,
    step_records: list[Any],
    error_message: str = "",
) -> tuple[list[StepObligation], int]:
    obligations: list[StepObligation] = []
    first_failing = -1

    for i, rec in enumerate(step_records):
        if isinstance(rec, dict):
            result = rec.get("result", "")
            tactic = rec.get("tactic", "")
            detail = rec.get("detail", "")
            step_idx = rec.get("step", i)
        else:
            result = getattr(rec, "result", "")
            tactic = getattr(rec, "tactic", "")
            detail = getattr(rec, "detail", "")
            step_idx = getattr(rec, "step", i)

        result_str = str(result)
        verified = result_str in ("state-advanced", "proof-finished")
        obligations.append(
            StepObligation(
                step_index=int(step_idx),
                tactic=str(tactic),
                result=result_str,
                detail=str(detail),
                verified=verified,
            )
        )

        if not verified and first_failing == -1:
            first_failing = int(step_idx)

    if not obligations:
        err_l = (error_message or "").lower()
        if any(tok in err_l for tok in ("lean-error", "tactic failed", "proof-given-up", "could not", "failed")):
            obligations.append(
                StepObligation(
                    step_index=0,
                    tactic="",
                    result="lean-error",
                    detail=error_message[:300],
                    verified=False,
                )
            )
            first_failing = 0

    return obligations, first_failing


def classify_theorem_result(
    *, translated: bool, proved: bool, had_exception: bool
) -> StatusDecision:
    if proved and translated:
        return StatusDecision(
            verification_status=VerificationStatus.INTERMEDIARY_PROVEN,
            grounding_status=GroundingStatus.UNKNOWN,
            reason=(
                "Theorem closed by Lean pipeline, but assumption grounding "
                "and step-level obligation verification are not yet integrated."
            ),
        )

    if had_exception:
        return StatusDecision(
            verification_status=VerificationStatus.UNRESOLVED,
            grounding_status=GroundingStatus.UNKNOWN,
            reason="Pipeline exception during proving/validation.",
        )

    if not translated:
        return StatusDecision(
            verification_status=VerificationStatus.UNRESOLVED,
            grounding_status=GroundingStatus.UNKNOWN,
            reason="Statement translation did not validate.",
        )

    return StatusDecision(
        verification_status=VerificationStatus.UNRESOLVED,
        grounding_status=GroundingStatus.UNKNOWN,
        reason="No closed Lean proof for theorem under current search budget.",
    )

