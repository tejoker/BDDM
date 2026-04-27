#!/usr/bin/env python3
"""Review and adjudication helpers for paper-claim equivalence evidence."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Iterable

from pipeline_status import evaluate_promotion_gates
from pipeline_status_models import (
    Assumption,
    ClaimEquivalenceVerdict,
    GroundingStatus,
    ProvenanceLink,
    StepVerdict,
    TrustClass,
    VerificationStatus,
    derive_theorem_trust,
)

SCHEMA_VERSION = "1.1.0"
ADJUDICATION_VERDICTS = {"equivalent", "weaker", "stronger", "not_equivalent", "unclear"}
ASSUMPTION_ALIGNMENT_STATUSES = {"matched", "missing", "extra", "weakened", "strengthened"}
CONCLUSION_ALIGNMENT_STATUSES = {"matched", "weakened", "strengthened", "different", "unclear"}
REVIEWER_TYPES = {"human", "llm", "hybrid"}
REVIEW_POLICIES = {"triage_only", "release_eligible", "requires_human_for_release"}
RELEASE_ELIGIBLE_REVIEWERS = {"human", "hybrid"}
BLOCKING_RISK_FLAGS = {
    "axiom_backed_semantic_shortcut",
    "conflicting_reviews",
    "different_conclusion",
    "incomplete_assumption_alignment",
    "malformed_alignment",
    "missing_assumption",
    "missing_conclusion_alignment",
    "needs_human_review",
    "placeholder_target",
    "semantic_mismatch",
    "strengthened_claim",
    "trivially_true",
    "verdict_wrong",
    "weakened_claim",
    "weakened_conclusion",
}
SEMANTIC_GATE_FAILURES = {
    "claim_equivalent",
    "independent_semantic_equivalence_evidence",
    "semantic_adversarial_checks_passed",
}
FIDELITY_GATE_FAILURES = {"translation_fidelity_ok", "status_alignment_ok"}


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict):
            rows.append(raw)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def ledger_entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        rows = payload.get("entries") or payload.get("rows") or payload.get("results")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def ledger_document_like(original: Any, entries: list[dict[str, Any]]) -> Any:
    if isinstance(original, dict):
        out = dict(original)
        if isinstance(original.get("entries"), list):
            out["entries"] = entries
        elif isinstance(original.get("rows"), list):
            out["rows"] = entries
        elif isinstance(original.get("results"), list):
            out["results"] = entries
        else:
            out["entries"] = entries
        return out
    return entries


def stable_review_id(*, paper_id: str, theorem_name: str, original_latex_theorem: str, lean_statement: str) -> str:
    payload = {
        "paper_id": paper_id or "",
        "theorem_name": theorem_name or "",
        "original_latex_theorem": re.sub(r"\s+", " ", (original_latex_theorem or "").strip()),
        "lean_statement": re.sub(r"\s+", " ", (lean_statement or "").strip()),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _artifact(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("semantic_equivalence_artifact")
    return raw if isinstance(raw, dict) else {}


def _context_pack(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("context_pack")
    return raw if isinstance(raw, dict) else {}


def _translation_schema(row: dict[str, Any]) -> dict[str, Any]:
    ctx = _context_pack(row)
    schema = ctx.get("translation_statement_schema")
    return schema if isinstance(schema, dict) else {}


def _source_latex(row: dict[str, Any]) -> str:
    artifact = _artifact(row)
    ctx = _context_pack(row)
    return str(
        artifact.get("original_latex_theorem")
        or row.get("original_latex_theorem")
        or ctx.get("original_latex_theorem")
        or row.get("source_statement")
        or ""
    )


def _normalized_theorem(row: dict[str, Any]) -> str:
    artifact = _artifact(row)
    return str(artifact.get("normalized_natural_language_theorem") or row.get("normalized_natural_language_theorem") or "")


def _extracted_assumptions(row: dict[str, Any]) -> list[str]:
    artifact = _artifact(row)
    raw = artifact.get("extracted_assumptions") or row.get("extracted_assumptions") or _translation_schema(row).get("assumptions") or []
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _extracted_conclusion(row: dict[str, Any]) -> str:
    artifact = _artifact(row)
    return str(artifact.get("extracted_conclusion") or row.get("extracted_conclusion") or _translation_schema(row).get("claim") or "")


def should_review_row(row: dict[str, Any]) -> bool:
    verdict = str(row.get("claim_equivalence_verdict", "") or "").lower()
    failures = {str(x) for x in (row.get("gate_failures") or [])}
    gates = row.get("validation_gates") if isinstance(row.get("validation_gates"), dict) else {}
    if verdict and verdict != ClaimEquivalenceVerdict.EQUIVALENT.value:
        return True
    if failures & SEMANTIC_GATE_FAILURES:
        return True
    if any(gates.get(name) is False for name in SEMANTIC_GATE_FAILURES):
        return True
    auto_core = row.get("auto_reliable_core")
    if isinstance(auto_core, dict) and auto_core.get("strict_gate_passed") is False:
        strict = {str(x) for x in (auto_core.get("strict_gate_failures") or [])}
        return bool(strict & SEMANTIC_GATE_FAILURES)
    return False


def _gate_failures(row: dict[str, Any]) -> list[str]:
    failures = [str(x) for x in (row.get("gate_failures") or []) if str(x).strip()]
    gates = row.get("validation_gates") if isinstance(row.get("validation_gates"), dict) else {}
    failures.extend(str(k) for k, v in gates.items() if v is False)
    auto_core = row.get("auto_reliable_core")
    if isinstance(auto_core, dict):
        failures.extend(str(x) for x in (auto_core.get("strict_gate_failures") or []) if str(x).strip())
    return list(dict.fromkeys(failures))


def semantic_gate_failures(row: dict[str, Any]) -> list[str]:
    return [failure for failure in _gate_failures(row) if failure in SEMANTIC_GATE_FAILURES]


def nonsemantic_gate_failures(row: dict[str, Any]) -> list[str]:
    return [failure for failure in _gate_failures(row) if failure not in SEMANTIC_GATE_FAILURES]


def remaining_blockers_if_equivalent(row: dict[str, Any]) -> list[str]:
    """Return blockers that would remain after claim-equivalence approval."""
    return nonsemantic_gate_failures(row)


def _has_auto_reliable_core(row: dict[str, Any]) -> bool:
    auto_core = row.get("auto_reliable_core")
    return isinstance(auto_core, dict) and bool(auto_core.get("theorem_name") or auto_core.get("core_file"))


def _has_closed_proof_signal(row: dict[str, Any]) -> bool:
    gates = row.get("validation_gates") if isinstance(row.get("validation_gates"), dict) else {}
    return bool(
        row.get("proved", False)
        or str(row.get("proof_method", "") or "").lower() == "lean_verified"
        or gates.get("lean_proof_closed") is True
        or _has_auto_reliable_core(row)
    )


def _has_axiom_debt(row: dict[str, Any]) -> bool:
    return any(str(x).strip() for x in (row.get("axiom_debt") or []))


def _is_placeholder_statement(row: dict[str, Any]) -> bool:
    text = str(row.get("lean_statement", "") or "")
    return bool(
        "PaperClaim" in text
        or "sorry_placeholder" in text
        or re.search(r"\(p_c\d+\s*:\s*Prop\)", text)
    )


def promotion_potential(row: dict[str, Any]) -> dict[str, Any]:
    """Estimate whether reviewing claim equivalence can move verified closure."""
    reasons: list[str] = []
    score = 0.0
    theorem = str(row.get("theorem_name", "") or "").lower()
    auto_core = _has_auto_reliable_core(row)
    closed = _has_closed_proof_signal(row)
    axiom_debt = _has_axiom_debt(row)
    placeholder = _is_placeholder_statement(row)
    remaining = remaining_blockers_if_equivalent(row)

    if closed:
        score += 35.0
        reasons.append("closed_proof_signal")
    else:
        score -= 25.0
        reasons.append("no_closed_proof_signal")
    if auto_core:
        score += 30.0
        reasons.append("auto_reliable_core_available")
    if not axiom_debt:
        score += 20.0
        reasons.append("no_axiom_debt")
    else:
        score -= 35.0
        reasons.append("axiom_debt_blocks_promotion")
    if placeholder:
        score -= 20.0
        reasons.append("placeholder_or_paper_claim_statement")

    fidelity = float(row.get("translation_fidelity_score", 0.0) or 0.0)
    alignment = float(row.get("status_alignment_score", 0.0) or 0.0)
    if fidelity >= 0.80:
        score += 8.0
        reasons.append("high_translation_fidelity")
    elif "translation_fidelity_ok" in remaining:
        score -= 8.0
        reasons.append("translation_fidelity_still_blocks")
    if alignment >= 0.80:
        score += 8.0
        reasons.append("high_status_alignment")
    elif "status_alignment_ok" in remaining:
        score -= 8.0
        reasons.append("status_alignment_still_blocks")

    if theorem.startswith(("thm_", "prop_", "cor_")):
        score += 6.0
        reasons.append("main_statement_name")
    if theorem == "remark_20":
        score += 10.0
        reasons.append("known_reliable_core_target")

    would_promote = bool(closed and not remaining)
    if would_promote:
        score += 20.0
        reasons.append("would_promote_if_equivalent")
    elif remaining:
        reasons.append("remaining_nonsemantic_blockers")

    score = max(0.0, min(100.0, round(score, 3)))
    if would_promote or score >= 65:
        tier = "high"
    elif score >= 40:
        tier = "medium"
    elif score >= 15:
        tier = "low"
    else:
        tier = "diagnostic_only"

    return {
        "promotion_potential_score": score,
        "promotion_potential_tier": tier,
        "promotion_potential_reasons": list(dict.fromkeys(reasons)),
        "semantic_gate_failures": semantic_gate_failures(row),
        "nonsemantic_gate_failures": remaining,
        "remaining_blockers_after_adjudication": remaining,
        "would_promote_if_equivalent": would_promote,
        "has_closed_proof_signal": closed,
        "has_auto_reliable_core": auto_core,
        "has_axiom_debt": axiom_debt,
    }


def build_review_prompt(row: dict[str, Any]) -> str:
    return "\n\n".join(
        [
            "Decide whether the Lean theorem states the same mathematical claim as the paper theorem.",
            f"Paper theorem:\n{_source_latex(row)}",
            f"Normalized theorem:\n{_normalized_theorem(row)}",
            f"Lean statement:\n{row.get('lean_statement', '')}",
            f"Extracted assumptions:\n{json.dumps(_extracted_assumptions(row), ensure_ascii=False)}",
            f"Extracted conclusion:\n{_extracted_conclusion(row)}",
            "Classify only semantic relationship: equivalent, weaker, stronger, not_equivalent, or unclear.",
        ]
    )


def build_review_queue_row(*, row: dict[str, Any], paper_id: str, source_ledger: str = "", report_context: dict[str, Any] | None = None) -> dict[str, Any]:
    theorem_name = str(row.get("theorem_name", "") or row.get("name", "") or "")
    original = _source_latex(row)
    lean_statement = str(row.get("lean_statement", "") or "")
    impact = promotion_potential(row)
    return {
        "schema_version": SCHEMA_VERSION,
        "paper_id": paper_id or str(row.get("paper_id", "") or ""),
        "theorem_name": theorem_name,
        "review_id": stable_review_id(
            paper_id=paper_id or str(row.get("paper_id", "") or ""),
            theorem_name=theorem_name,
            original_latex_theorem=original,
            lean_statement=lean_statement,
        ),
        "source_ledger": source_ledger,
        "original_latex_theorem": original,
        "normalized_natural_language_theorem": _normalized_theorem(row),
        "lean_statement": lean_statement,
        "extracted_assumptions": _extracted_assumptions(row),
        "extracted_conclusion": _extracted_conclusion(row),
        "translation_schema": _translation_schema(row),
        "claim_equivalence_verdict": str(row.get("claim_equivalence_verdict", "") or "unclear"),
        "claim_equivalence_notes": [str(x) for x in (row.get("claim_equivalence_notes") or [])],
        "gate_failures": [str(x) for x in (row.get("gate_failures") or [])],
        "axiom_debt": [str(x) for x in (row.get("axiom_debt") or [])],
        "proof_status": str(row.get("status", "") or ""),
        "auto_reliable_core": row.get("auto_reliable_core") if isinstance(row.get("auto_reliable_core"), dict) else {},
        "report_context": report_context or {},
        "promotion_potential": impact,
        "promotion_potential_score": impact["promotion_potential_score"],
        "promotion_potential_tier": impact["promotion_potential_tier"],
        "remaining_blockers_after_adjudication": impact["remaining_blockers_after_adjudication"],
        "would_promote_if_equivalent": impact["would_promote_if_equivalent"],
        "review_prompt": build_review_prompt(row),
    }


def build_review_queue(
    *,
    ledger_payload: Any,
    paper_id: str = "",
    source_ledger: str = "",
    report_payload: Any = None,
    release_eligible_only: bool = False,
) -> list[dict[str, Any]]:
    report_by_name: dict[str, dict[str, Any]] = {}
    if isinstance(report_payload, dict):
        for item in ((report_payload.get("auto_reliable_core_promotion") or {}).get("strict_gate_blocked_theorems") or []):
            if isinstance(item, dict) and item.get("theorem_name"):
                report_by_name[str(item["theorem_name"])] = item
    queue: list[dict[str, Any]] = []
    seen: set[str] = set()
    doc_paper = paper_id or (ledger_payload.get("paper_id", "") if isinstance(ledger_payload, dict) else "")
    for row in ledger_entries(ledger_payload):
        if not should_review_row(row):
            continue
        q = build_review_queue_row(
            row=row,
            paper_id=str(doc_paper or row.get("paper_id", "")),
            source_ledger=source_ledger,
            report_context=report_by_name.get(str(row.get("theorem_name", "")), {}),
        )
        if release_eligible_only and not bool(q.get("would_promote_if_equivalent", False)):
            continue
        if q["review_id"] in seen:
            continue
        seen.add(q["review_id"])
        queue.append(q)
    queue.sort(
        key=lambda r: (
            -float(r.get("promotion_potential_score", 0.0) or 0.0),
            0 if bool(r.get("would_promote_if_equivalent", False)) else 1,
            str(r.get("theorem_name", "")),
            str(r.get("review_id", "")),
        )
    )
    return queue


def summarize_review_queue(rows: Iterable[dict[str, Any]], *, top_n: int = 10) -> dict[str, Any]:
    queue = [row for row in rows if isinstance(row, dict)]
    reason_counts: dict[str, int] = {}
    for row in queue:
        for reason in row.get("remaining_blockers_after_adjudication") or []:
            key = str(reason)
            reason_counts[key] = reason_counts.get(key, 0) + 1
    high = [row for row in queue if str(row.get("promotion_potential_tier", "")) == "high"]
    would_promote = [row for row in queue if bool(row.get("would_promote_if_equivalent", False))]
    top_targets = [
        {
            "theorem_name": str(row.get("theorem_name", "") or ""),
            "promotion_potential_score": float(row.get("promotion_potential_score", 0.0) or 0.0),
            "promotion_potential_tier": str(row.get("promotion_potential_tier", "") or ""),
            "would_promote_if_equivalent": bool(row.get("would_promote_if_equivalent", False)),
            "remaining_blockers_after_adjudication": [
                str(x) for x in (row.get("remaining_blockers_after_adjudication") or [])
            ],
        }
        for row in queue[:top_n]
    ]
    return {
        "pending_review_count": len(queue),
        "high_potential_review_count": len(high),
        "would_promote_if_equivalent_count": len(would_promote),
        "top_review_targets": top_targets,
        "remaining_blocker_counts": dict(sorted(reason_counts.items())),
    }


def validate_adjudication(row: dict[str, Any]) -> dict[str, Any]:
    verdict = str(row.get("verdict", "") or "").strip().lower()
    if verdict not in ADJUDICATION_VERDICTS:
        raise ValueError(f"invalid verdict: {verdict}")
    rationale = str(row.get("rationale", "") or "").strip()
    if not rationale:
        raise ValueError("adjudication rationale is required")
    confidence = float(row.get("confidence", 0.0) or 0.0)
    if confidence < 0.0 or confidence > 1.0:
        raise ValueError("confidence must be between 0 and 1")
    normalized = dict(row)
    reviewer_type = _reviewer_type(row)
    review_policy = str(row.get("review_policy", "") or "").strip().lower()
    if review_policy not in REVIEW_POLICIES:
        review_policy = "release_eligible" if reviewer_type in RELEASE_ELIGIBLE_REVIEWERS else "requires_human_for_release"
    if reviewer_type == "llm" and review_policy == "release_eligible":
        review_policy = "requires_human_for_release"
    risk_flags = [str(x).strip().lower() for x in (row.get("risk_flags") or []) if str(x).strip()]
    assumptions: list[dict[str, Any]] = []
    for item in row.get("assumption_alignment") or []:
        if not isinstance(item, dict):
            raise ValueError("assumption_alignment entries must be objects")
        status = str(item.get("status", "") or "").strip().lower()
        if status and status not in ASSUMPTION_ALIGNMENT_STATUSES:
            raise ValueError(f"invalid assumption alignment status: {status}")
        assumptions.append(
            {
                "paper": str(item.get("paper", "") or ""),
                "lean": str(item.get("lean", "") or ""),
                "status": status or "unclear",
                "notes": str(item.get("notes", "") or ""),
            }
        )
    conclusion_raw = row.get("conclusion_alignment")
    conclusion = conclusion_raw if isinstance(conclusion_raw, dict) else {}
    conclusion_status = str(conclusion.get("status", "") or "").strip().lower()
    if conclusion_status and conclusion_status not in CONCLUSION_ALIGNMENT_STATUSES:
        raise ValueError(f"invalid conclusion alignment status: {conclusion_status}")
    conclusion = {
        "paper": str(conclusion.get("paper", "") or ""),
        "lean": str(conclusion.get("lean", "") or ""),
        "status": conclusion_status or "unclear",
        "notes": str(conclusion.get("notes", "") or ""),
    }
    if verdict == "equivalent" and conclusion["status"] != "matched":
        risk_flags.append("missing_conclusion_alignment")
    if verdict == "equivalent" and any(item["status"] != "matched" for item in assumptions):
        risk_flags.append("incomplete_assumption_alignment")
    normalized["schema_version"] = str(row.get("schema_version") or SCHEMA_VERSION)
    normalized["verdict"] = verdict
    normalized["confidence"] = confidence
    normalized["reviewer_type"] = reviewer_type
    normalized["review_policy"] = review_policy
    normalized["assumption_alignment"] = assumptions
    normalized["conclusion_alignment"] = conclusion
    normalized["risk_flags"] = list(dict.fromkeys(risk_flags))
    normalized["required_ledger_markers"] = [str(x) for x in (row.get("required_ledger_markers") or [])]
    normalized["applied_at_unix"] = int(row.get("applied_at_unix", 0) or 0)
    return normalized


def _reviewer_type(row: dict[str, Any]) -> str:
    explicit = str(row.get("reviewer_type", "") or "").strip().lower()
    if explicit in REVIEWER_TYPES:
        return explicit
    adjudicator = str(row.get("adjudicator", "") or "").strip().lower()
    if adjudicator.startswith("human"):
        return "human"
    if adjudicator.startswith("hybrid"):
        return "hybrid"
    return "llm"


def _is_release_eligible(adjudication: dict[str, Any]) -> bool:
    return (
        str(adjudication.get("reviewer_type", "")).lower() in RELEASE_ELIGIBLE_REVIEWERS
        and str(adjudication.get("review_policy", "")).lower() == "release_eligible"
    )


def adjudication_rank(row: dict[str, Any]) -> tuple[int, float, int]:
    reviewer_type = _reviewer_type(row)
    source_rank = 3 if reviewer_type == "human" else 2 if reviewer_type == "hybrid" else 1
    return (source_rank, float(row.get("confidence", 0.0) or 0.0), int(row.get("created_at_unix", 0) or 0))


def adjudication_decisions(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for raw in rows:
        try:
            row = validate_adjudication(raw)
        except Exception:
            continue
        rid = str(row.get("review_id", "") or "")
        if not rid:
            continue
        grouped.setdefault(rid, []).append(row)
    decisions: dict[str, dict[str, Any]] = {}
    for rid, group in grouped.items():
        sorted_group = sorted(group, key=adjudication_rank, reverse=True)
        release_reviews = [row for row in sorted_group if _is_release_eligible(row)]
        release_verdicts = {str(row.get("verdict", "") or "") for row in release_reviews}
        all_verdicts = {str(row.get("verdict", "") or "") for row in sorted_group}
        release_conflict = len(release_verdicts) > 1
        any_conflict = len(all_verdicts) > 1
        selected = release_reviews[0] if release_reviews else sorted_group[0]
        decisions[rid] = {
            "selected": selected,
            "reviews": sorted_group,
            "llm_only": all(str(row.get("reviewer_type", "")) == "llm" for row in sorted_group),
            "requires_human": not release_reviews,
            "conflicting": any_conflict,
            "release_conflict": release_conflict,
        }
    return decisions


def best_adjudications(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {rid: decision["selected"] for rid, decision in adjudication_decisions(rows).items()}


def _enum_value(enum_cls, value: Any, default):
    try:
        return enum_cls(value)
    except Exception:
        return default


def _assumptions_from_row(row: dict[str, Any]) -> list[Assumption]:
    out: list[Assumption] = []
    for item in row.get("assumptions") or []:
        if not isinstance(item, dict):
            continue
        out.append(
            Assumption(
                label=str(item.get("label", "") or ""),
                lean_expr=str(item.get("lean_expr", "") or ""),
                grounding=_enum_value(GroundingStatus, item.get("grounding"), GroundingStatus.UNKNOWN),
                grounding_source=str(item.get("grounding_source", "") or ""),
                trust_class=_enum_value(TrustClass, item.get("trust_class"), TrustClass.TRUST_PLACEHOLDER),
                trust_reference=str(item.get("trust_reference", "") or ""),
            )
        )
    return out


def _provenance_from_row(row: dict[str, Any]) -> ProvenanceLink | None:
    raw = row.get("provenance")
    if not isinstance(raw, dict):
        return None
    return ProvenanceLink(
        paper_id=str(raw.get("paper_id", "") or row.get("paper_id", "") or ""),
        section=str(raw.get("section", "") or ""),
        label=str(raw.get("label", "") or ""),
        cited_refs=[str(x) for x in (raw.get("cited_refs") or [])],
    )


def _bool_field_or_gate(row: dict[str, Any], field: str, gate: str, default: bool | None = None) -> bool | None:
    if field in row:
        return bool(row.get(field))
    gates = row.get("validation_gates") if isinstance(row.get("validation_gates"), dict) else {}
    if gate in gates:
        return bool(gates[gate])
    return default


def _semantic_checks_passed(row: dict[str, Any]) -> bool | None:
    artifact = _artifact(row)
    checks = artifact.get("adversarial_checks")
    if not isinstance(checks, dict) or not checks:
        gates = row.get("validation_gates") if isinstance(row.get("validation_gates"), dict) else {}
        return bool(gates.get("semantic_adversarial_checks_passed", True))
    return all(bool(v.get("passed", False)) for v in checks.values() if isinstance(v, dict))


def _candidate_full_status(row: dict[str, Any]) -> VerificationStatus:
    status = _enum_value(VerificationStatus, row.get("status"), VerificationStatus.UNRESOLVED)
    if status == VerificationStatus.FULLY_PROVEN:
        return status
    proved = bool(row.get("proved", False))
    proof_method = str(row.get("proof_method", "") or "")
    step_verdict = str(row.get("step_verdict", "") or "")
    gates = row.get("validation_gates") if isinstance(row.get("validation_gates"), dict) else {}
    if proved or proof_method == "lean_verified" or (step_verdict == StepVerdict.VERIFIED.value and gates.get("lean_proof_closed") is True):
        return VerificationStatus.FULLY_PROVEN
    return status


def _norm_alignment_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _adjudication_blockers(
    adjudication: dict[str, Any],
    *,
    row: dict[str, Any],
    min_confidence: float,
    decision: dict[str, Any] | None = None,
) -> list[str]:
    blockers: list[str] = []
    if adjudication.get("verdict") != "equivalent":
        blockers.append("verdict_not_equivalent")
    if float(adjudication.get("confidence", 0.0) or 0.0) < min_confidence:
        blockers.append("confidence_below_threshold")
    if not _is_release_eligible(adjudication):
        blockers.append("requires_human_for_release")
    if decision and decision.get("release_conflict"):
        blockers.append("conflicting_reviews")
    risks = {str(x).lower() for x in (adjudication.get("risk_flags") or [])}
    if risks & BLOCKING_RISK_FLAGS:
        blockers.extend(sorted(risks & BLOCKING_RISK_FLAGS))
    conclusion = adjudication.get("conclusion_alignment")
    if isinstance(conclusion, dict) and str(conclusion.get("status", "")).lower() not in {"", "matched"}:
        status = str(conclusion.get("status", "")).lower()
        blockers.append("weakened_conclusion" if status == "weakened" else "different_conclusion")
    if not isinstance(conclusion, dict) or str(conclusion.get("status", "")).lower() != "matched":
        if adjudication.get("verdict") == "equivalent":
            blockers.append("missing_conclusion_alignment")
    for item in adjudication.get("assumption_alignment") or []:
        if isinstance(item, dict) and str(item.get("status", "")).lower() not in {"", "matched"}:
            blockers.append("missing_assumption")
    expected = [_norm_alignment_text(x) for x in _extracted_assumptions(row)]
    if expected:
        matched = {
            _norm_alignment_text(str(item.get("paper", "") or ""))
            for item in (adjudication.get("assumption_alignment") or [])
            if isinstance(item, dict) and str(item.get("status", "")).lower() == "matched"
        }
        missing = [item for item in expected if item not in matched]
        if missing:
            blockers.append("incomplete_assumption_alignment")
    return list(dict.fromkeys(blockers))


def _adjudication_blocks_equivalence(adjudication: dict[str, Any], *, min_confidence: float) -> bool:
    return bool(_adjudication_blockers(adjudication, row={}, min_confidence=min_confidence))


def apply_adjudication_to_row(
    row: dict[str, Any],
    adjudication: dict[str, Any],
    *,
    min_confidence: float = 0.80,
    decision: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Merge one adjudication into a ledger row and rerun promotion gates."""
    out = dict(row)
    artifact = dict(_artifact(out))
    evidence = artifact.get("reviewer_evaluator_evidence")
    if not isinstance(evidence, list):
        evidence = []
    rationale = str(adjudication.get("rationale", "") or "")
    reviewer_type = str(adjudication.get("reviewer_type", "") or _reviewer_type(adjudication))
    marker = f"claim_equivalent:{reviewer_type}"
    evidence_items = [
        f"claim_equivalence_adjudication:{adjudication.get('review_id', '')}",
        f"adjudicator:{adjudication.get('adjudicator', '')}",
        f"reviewer_type:{reviewer_type}",
        f"review_policy:{adjudication.get('review_policy', '')}",
        f"adjudication_verdict:{adjudication.get('verdict', '')}",
        f"adjudication_confidence:{adjudication.get('confidence', '')}",
    ]
    if decision and decision.get("llm_only"):
        evidence_items.append("claim_equivalence:llm_triage_only")
    if decision and decision.get("conflicting"):
        evidence_items.append("claim_equivalence:conflicting_reviews")
    if rationale:
        evidence_items.append(f"adjudication_rationale:{rationale}")
    blockers = _adjudication_blockers(
        adjudication,
        row=out,
        min_confidence=min_confidence,
        decision=decision,
    )
    approved = not blockers
    if approved:
        evidence_items.extend(["semantic_equivalence:verified", marker, "equivalent_independent_semantic_evidence"])
        artifact["equivalence_verdict"] = ClaimEquivalenceVerdict.EQUIVALENT.value
        artifact["independent_semantic_evidence"] = True
        out["claim_equivalence_verdict"] = ClaimEquivalenceVerdict.EQUIVALENT.value
    else:
        verdict = str(adjudication.get("verdict", "unclear") or "unclear")
        artifact["equivalence_verdict"] = verdict if verdict in {"weaker", "stronger", "unclear"} else ClaimEquivalenceVerdict.UNCLEAR.value
        artifact["independent_semantic_evidence"] = False
        out["claim_equivalence_verdict"] = artifact["equivalence_verdict"]
    artifact["reviewer_evaluator_evidence"] = list(dict.fromkeys([*evidence, *evidence_items]))
    artifact["adjudication"] = {
        "review_id": adjudication.get("review_id", ""),
        "adjudicator": adjudication.get("adjudicator", ""),
        "reviewer_type": reviewer_type,
        "review_policy": adjudication.get("review_policy", ""),
        "verdict": adjudication.get("verdict", ""),
        "confidence": adjudication.get("confidence", 0.0),
        "rationale": rationale,
        "risk_flags": adjudication.get("risk_flags", []),
        "release_eligible": _is_release_eligible(adjudication),
        "blockers": blockers,
    }
    if decision:
        artifact["adjudication"]["review_chain"] = [
            {
                "adjudicator": r.get("adjudicator", ""),
                "reviewer_type": r.get("reviewer_type", ""),
                "review_policy": r.get("review_policy", ""),
                "verdict": r.get("verdict", ""),
                "confidence": r.get("confidence", 0.0),
                "risk_flags": r.get("risk_flags", []),
            }
            for r in decision.get("reviews", [])
        ]
        artifact["adjudication"]["conflicting_reviews"] = bool(decision.get("conflicting"))
        artifact["adjudication"]["requires_human_for_release"] = bool(decision.get("requires_human"))
    out["semantic_equivalence_artifact"] = artifact
    notes = [str(x) for x in (out.get("claim_equivalence_notes") or [])]
    if approved:
        notes.extend(["equivalent_independent_semantic_evidence", marker, "semantic_equivalence:verified"])
    else:
        notes.append(f"claim_equivalence_adjudicated:{adjudication.get('verdict', 'unclear')}")
        notes.extend(f"claim_equivalence_blocked:{b}" for b in blockers)
    out["claim_equivalence_notes"] = list(dict.fromkeys(notes))

    claim_verdict = _enum_value(ClaimEquivalenceVerdict, out.get("claim_equivalence_verdict"), ClaimEquivalenceVerdict.UNCLEAR)
    candidate_status = _candidate_full_status(out)
    strict_status, gates, failures = evaluate_promotion_gates(
        status=candidate_status,
        proved=bool(out.get("proved", False) or candidate_status == VerificationStatus.FULLY_PROVEN),
        step_verdict=_enum_value(StepVerdict, out.get("step_verdict"), StepVerdict.INCOMPLETE),
        assumptions=_assumptions_from_row(out),
        provenance=_provenance_from_row(out),
        project_root=None,
        translation_fidelity_score=float(out.get("translation_fidelity_score", 0.0) or 0.0),
        status_alignment_score=float(out.get("status_alignment_score", 0.0) or 0.0),
        dependency_trust_complete=_bool_field_or_gate(out, "dependency_trust_complete", "dependency_trust_complete"),
        reproducible_env=_bool_field_or_gate(out, "reproducible_env", "reproducible_env"),
        lean_statement=str(out.get("lean_statement", "") or ""),
        proof_text=str(out.get("proof_text", "") or ""),
        run_independent_verify=False,
        claim_equivalence_verdict=claim_verdict,
        independent_semantic_evidence=bool(artifact.get("independent_semantic_evidence")),
        semantic_adversarial_checks_passed=_semantic_checks_passed(out),
        axiom_debt=[str(x) for x in (out.get("axiom_debt") or [])],
    )
    out["status"] = strict_status.value
    out["validation_gates"] = gates
    out["gate_failures"] = failures
    out["remaining_blockers_after_adjudication"] = remaining_blockers_if_equivalent(out)
    trust_class, trust_ref, promotion_ok = derive_theorem_trust(assumptions=_assumptions_from_row(out), status=strict_status)
    if failures:
        trust_ref += ";gate_failures=" + ",".join(failures)
    out["trust_class"] = trust_class.value
    out["trust_reference"] = trust_ref
    out["promotion_gate_passed"] = bool(promotion_ok and not failures)
    out["review_required"] = bool(
        claim_verdict != ClaimEquivalenceVerdict.EQUIVALENT
        or "claim_equivalent" in failures
        or "independent_semantic_equivalence_evidence" in failures
        or bool(blockers)
    )
    out["review_queue_id"] = f"review::{out.get('theorem_name', '')}" if out["review_required"] else ""
    return out, approved


def apply_adjudications_to_entries(
    entries: list[dict[str, Any]],
    adjudications: Iterable[dict[str, Any]],
    *,
    paper_id: str = "",
    min_confidence: float = 0.80,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    decisions = adjudication_decisions(adjudications)
    out: list[dict[str, Any]] = []
    applied = promoted = rejected = approved_count = pending = 0
    llm_only = requires_human = conflicts = hard_blocked = 0
    human_approved = hybrid_approved = 0
    verdict_counts = {
        "rejected_weaker_count": 0,
        "rejected_stronger_count": 0,
        "rejected_not_equivalent_count": 0,
        "unclear_count": 0,
    }
    still_blocked = 0
    still_blocked_reason_counts: dict[str, int] = {}
    impacted_theorems: list[dict[str, Any]] = []
    for row in entries:
        review_id = stable_review_id(
            paper_id=paper_id or str(row.get("paper_id", "") or ""),
            theorem_name=str(row.get("theorem_name", "") or ""),
            original_latex_theorem=_source_latex(row),
            lean_statement=str(row.get("lean_statement", "") or ""),
        )
        decision = decisions.get(review_id)
        if decision is None:
            pending += int(should_review_row(row))
            out.append(row)
            continue
        adj = decision["selected"]
        applied += 1
        llm_only += int(bool(decision.get("llm_only")))
        requires_human += int(bool(decision.get("requires_human")))
        conflicts += int(bool(decision.get("conflicting")))
        updated, approved = apply_adjudication_to_row(row, adj, min_confidence=min_confidence, decision=decision)
        remaining = [str(x) for x in (updated.get("remaining_blockers_after_adjudication") or [])]
        promoted_now = bool(approved and updated.get("promotion_gate_passed", False))
        if approved:
            approved_count += 1
            promoted += int(promoted_now)
            reviewer_type = str(adj.get("reviewer_type", ""))
            human_approved += int(reviewer_type == "human")
            hybrid_approved += int(reviewer_type == "hybrid")
            if not promoted_now:
                still_blocked += 1
                for reason in remaining:
                    still_blocked_reason_counts[reason] = still_blocked_reason_counts.get(reason, 0) + 1
        else:
            rejected += 1
            verdict = str(adj.get("verdict", "") or "").lower()
            if verdict == "weaker":
                verdict_counts["rejected_weaker_count"] += 1
            elif verdict == "stronger":
                verdict_counts["rejected_stronger_count"] += 1
            elif verdict == "not_equivalent":
                verdict_counts["rejected_not_equivalent_count"] += 1
            else:
                verdict_counts["unclear_count"] += 1
            hard_blocked += int(bool((updated.get("semantic_equivalence_artifact") or {}).get("adjudication", {}).get("blockers")))
        impacted_theorems.append(
            {
                "theorem_name": str(updated.get("theorem_name", "") or ""),
                "review_id": review_id,
                "verdict": str(adj.get("verdict", "") or ""),
                "approved": approved,
                "promoted": promoted_now,
                "remaining_blockers": remaining,
            }
        )
        out.append(updated)
    return out, {
        "pending_review_count": pending,
        "approved_equivalent_count": approved_count,
        **verdict_counts,
        "claim_equivalence_applied_count": applied,
        "claim_equivalence_promoted_count": promoted,
        "claim_equivalence_rejected_count": rejected,
        "promoted_after_adjudication_count": promoted,
        "still_blocked_after_adjudication_count": still_blocked,
        "still_blocked_reason_counts": dict(sorted(still_blocked_reason_counts.items())),
        "impacted_theorems": impacted_theorems,
        "claim_equivalence_llm_only_triage_count": llm_only,
        "claim_equivalence_requires_human_count": requires_human,
        "claim_equivalence_conflict_count": conflicts,
        "claim_equivalence_hard_blocked_count": hard_blocked,
        "claim_equivalence_human_approved_count": human_approved,
        "claim_equivalence_hybrid_approved_count": hybrid_approved,
    }
