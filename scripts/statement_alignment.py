#!/usr/bin/env python3
"""Deterministic LaTeX-to-Lean statement alignment helpers."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from pipeline_status_models import (
    AlignmentDecision,
    ClaimEquivalenceVerdict,
    FailureKind,
    ProofMethod,
    StatementAlignmentClass,
    VerificationStatus,
)


_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "be",
    "by",
    "for",
    "from",
    "has",
    "have",
    "if",
    "in",
    "is",
    "it",
    "let",
    "of",
    "on",
    "or",
    "such",
    "that",
    "the",
    "then",
    "there",
    "to",
    "with",
}

_DIAGNOSTIC_MARKERS = (
    "translation_limited",
    "translation_acceptance_gate",
    "schema_fallback",
    "schema_translation",
    "placeholder",
    "repair_diagnostic",
    "diagnostic",
    "trivial_target",
    "bad_translation_artifact",
)

_SEMANTIC_HARD_FAIL_MARKERS = (
    "semantic_policy_violation",
    "trivialization_hard_violation",
    "adversarial_check_failed",
    "verdict:wrong",
    "verdict:suspicious",
    "semantic_mismatch",
    "roundtrip_semantic_mismatch",
)


def normalize_latex_statement(text: str, schema: dict[str, Any] | None = None) -> str:
    """Return a deterministic, compact paper-side statement string."""
    schema = schema if isinstance(schema, dict) else {}
    if schema:
        quantifiers = _coerce_str_list(schema.get("quantifiers"))
        assumptions = _coerce_str_list(schema.get("assumptions"))
        claim = str(schema.get("claim", "") or "").strip()
        parts: list[str] = []
        if quantifiers:
            parts.append("Quantifiers: " + "; ".join(_clean_latex(q) for q in quantifiers))
        if assumptions:
            parts.append("Assumptions: " + "; ".join(_clean_latex(a) for a in assumptions))
        if claim:
            parts.append("Conclusion: " + _clean_latex(claim))
        if parts:
            return _normalize_ws(" ".join(parts))
    return _normalize_ws(_clean_latex(text))


def normalize_lean_statement(lean_statement: str) -> str:
    """Return a compact Lean statement with proof bodies and comments removed."""
    stmt = lean_statement or ""
    stmt = re.sub(r"--.*", " ", stmt)
    stmt = re.sub(r"/-.*?-/", " ", stmt, flags=re.DOTALL)
    stmt = re.sub(r":=\s*by\b.*$", " ", stmt, flags=re.DOTALL)
    stmt = stmt.replace("∀", " forall ").replace("∃", " exists ")
    stmt = stmt.replace("→", " -> ").replace("↔", " iff ")
    stmt = stmt.replace("≤", " <= ").replace("≥", " >= ")
    return _normalize_ws(stmt)


def lean_target(lean_statement: str) -> str:
    stmt = re.sub(r":=\s*by\b.*$", "", lean_statement or "", flags=re.DOTALL).strip()
    depth = 0
    target_start = -1
    for i, ch in enumerate(stmt):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0:
            target_start = i + 1
    return stmt[target_start:].strip() if target_start >= 0 else stmt


def compute_paper_statement_id(
    *,
    paper_id: str,
    theorem_name: str,
    normalized_paper_text: str,
    context_pack: dict[str, Any] | None,
) -> str:
    context = context_pack if isinstance(context_pack, dict) else {}
    source_file = str(context.get("source_file", "") or "")
    label = str(context.get("latex_label", "") or "")
    span = context.get("source_char_range") or context.get("source_line_range") or []
    span_text = ",".join(str(x) for x in span) if isinstance(span, list) else str(span)
    payload = "\n".join(
        [
            paper_id.strip(),
            theorem_name.strip(),
            source_file,
            label,
            span_text,
            _normalize_ws(normalized_paper_text),
        ]
    )
    return "pstmt_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def compute_alignment_pair_id(*, paper_statement_id: str, canonical_theorem_id: str, lean_statement: str) -> str:
    lean_key = canonical_theorem_id.strip() or hashlib.sha256(
        normalize_lean_statement(lean_statement).encode("utf-8")
    ).hexdigest()[:24]
    payload = f"{paper_statement_id}\n{lean_key}"
    return "align_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def classify_statement_alignment(
    *,
    paper_id: str,
    theorem_name: str,
    original_latex_theorem: str,
    normalized_paper_text: str,
    extracted_assumptions: list[str] | None,
    extracted_conclusion: str,
    lean_statement: str,
    equivalence_verdict: ClaimEquivalenceVerdict,
    claim_equivalence_notes: list[str] | None,
    uncertainty_flags: list[str] | None,
    adversarial_flags: list[str] | None,
    roundtrip_flags: list[str] | None,
    context_pack: dict[str, Any] | None,
    translation_fidelity_score: float | None,
    status_alignment_score: float | None,
    translation_validated: bool | None,
    independent_semantic_evidence: bool,
    status: VerificationStatus | None = None,
    proof_method: ProofMethod | None = None,
    failure_kind: FailureKind | None = None,
    canonical_theorem_id: str = "",
) -> AlignmentDecision:
    paper_norm = normalized_paper_text.strip() or normalize_latex_statement(original_latex_theorem)
    lean_norm = normalize_lean_statement(lean_statement)
    target = lean_target(lean_statement)
    paper_tokens = _tokens(paper_norm)
    lean_tokens = _tokens(lean_norm)
    overlap = paper_tokens & lean_tokens
    paper_coverage = len(overlap) / len(paper_tokens) if paper_tokens else 0.0
    lean_coverage = len(overlap) / len(lean_tokens) if lean_tokens else 0.0

    flags = [
        str(x).strip().lower()
        for x in [
            *(claim_equivalence_notes or []),
            *(uncertainty_flags or []),
            *(adversarial_flags or []),
            *(roundtrip_flags or []),
        ]
        if str(x).strip()
    ]
    reasons: list[str] = []
    evidence_sources: list[str] = []
    missing_assumptions = [
        a for a in _coerce_str_list(extracted_assumptions) if _coverage(a, lean_norm) < 0.15
    ]
    added_assumptions = _lean_added_assumptions(lean_statement, paper_norm)
    conclusion_relation = _conclusion_relation(extracted_conclusion, target)

    paper_statement_id = compute_paper_statement_id(
        paper_id=paper_id,
        theorem_name=theorem_name,
        normalized_paper_text=paper_norm,
        context_pack=context_pack,
    )
    alignment_pair_id = compute_alignment_pair_id(
        paper_statement_id=paper_statement_id,
        canonical_theorem_id=canonical_theorem_id,
        lean_statement=lean_statement,
    )

    if translation_validated is False:
        reasons.append("translation_unvalidated")
    if independent_semantic_evidence:
        evidence_sources.append("independent_semantic_evidence")
    if context_pack and (context_pack.get("source_span_id") or context_pack.get("source_char_range")):
        evidence_sources.append("source_span")
    if canonical_theorem_id:
        evidence_sources.append("canonical_lean_statement")

    diagnostic = _is_diagnostic(
        lean_statement=lean_statement,
        flags=flags,
        status=status,
        proof_method=proof_method,
        failure_kind=failure_kind,
    )
    if diagnostic:
        alignment = StatementAlignmentClass.DIAGNOSTIC
        reasons.append("diagnostic_or_translation_artifact")
    elif any(any(marker in f for marker in _SEMANTIC_HARD_FAIL_MARKERS) for f in flags):
        alignment = StatementAlignmentClass.UNRELATED
        reasons.append("semantic_hard_fail")
    elif equivalence_verdict == ClaimEquivalenceVerdict.WEAKER:
        alignment = StatementAlignmentClass.WEAKER
        reasons.append("claim_equivalence_weaker")
    elif equivalence_verdict == ClaimEquivalenceVerdict.STRONGER:
        alignment = StatementAlignmentClass.STRONGER
        reasons.append("claim_equivalence_stronger")
    elif any("weaker" in f for f in flags):
        alignment = StatementAlignmentClass.WEAKER
        reasons.append("flagged_weaker")
    elif any(("stronger" in f) or ("dropped hypothesis" in f) for f in flags):
        alignment = StatementAlignmentClass.STRONGER
        reasons.append("flagged_stronger")
    elif (
        equivalence_verdict == ClaimEquivalenceVerdict.EQUIVALENT
        and independent_semantic_evidence
        and float(translation_fidelity_score or 0.0) >= 0.80
        and float(status_alignment_score or 0.0) >= 0.75
        and conclusion_relation in {"matched", "compatible", "unknown"}
        and not missing_assumptions
    ):
        alignment = StatementAlignmentClass.EXACT
        reasons.append("equivalent_with_independent_evidence")
    elif paper_tokens and lean_tokens and paper_coverage < 0.08 and lean_coverage < 0.08:
        alignment = StatementAlignmentClass.UNRELATED
        reasons.append("low_cross_text_overlap")
    else:
        alignment = StatementAlignmentClass.PARTIAL
        if missing_assumptions:
            reasons.append("missing_paper_assumptions")
        if conclusion_relation in {"missing", "trivial_target"}:
            reasons.append(f"conclusion_{conclusion_relation}")
        if not reasons:
            reasons.append("insufficient_exact_alignment_evidence")

    confidence = _confidence(
        alignment=alignment,
        paper_coverage=paper_coverage,
        lean_coverage=lean_coverage,
        fidelity=float(translation_fidelity_score or 0.0),
        status_alignment=float(status_alignment_score or 0.0),
        independent=independent_semantic_evidence,
        missing_assumptions=missing_assumptions,
        diagnostic=diagnostic,
    )

    return AlignmentDecision(
        alignment_class=alignment,
        confidence=confidence,
        reasons=list(dict.fromkeys(reasons)),
        evidence_sources=list(dict.fromkeys(evidence_sources)),
        paper_statement_id=paper_statement_id,
        alignment_pair_id=alignment_pair_id,
        paper_text_coverage=round(paper_coverage, 4),
        lean_text_coverage=round(lean_coverage, 4),
        missing_assumptions=missing_assumptions,
        added_assumptions=added_assumptions,
        conclusion_relation=conclusion_relation,
        diagnostic=diagnostic,
    )


def classify_row_alignment(row: dict[str, Any], *, paper_id: str = "") -> AlignmentDecision:
    """Classify a ledger row, including legacy rows without alignment fields."""
    artifact = row.get("semantic_equivalence_artifact")
    if not isinstance(artifact, dict):
        artifact = {}
    context = row.get("context_pack")
    if not isinstance(context, dict):
        context = {}
    provenance = row.get("provenance")
    if isinstance(provenance, dict) and not paper_id:
        paper_id = str(provenance.get("paper_id", "") or "")
    paper_id = paper_id or str(row.get("paper_id", "") or "")
    schema = context.get("translation_statement_schema")
    if not isinstance(schema, dict):
        schema = context.get("statement_schema") if isinstance(context.get("statement_schema"), dict) else {}
    original = str(
        artifact.get("original_latex_theorem")
        or row.get("original_latex_theorem")
        or context.get("original_latex_theorem")
        or ""
    )
    normalized = str(
        artifact.get("normalized_natural_language_theorem")
        or row.get("normalized_natural_language_theorem")
        or normalize_latex_statement(original, schema)
        or ""
    )
    return classify_statement_alignment(
        paper_id=paper_id,
        theorem_name=str(row.get("theorem_name", "") or ""),
        original_latex_theorem=original,
        normalized_paper_text=normalized,
        extracted_assumptions=_coerce_str_list(
            artifact.get("extracted_assumptions")
            or row.get("extracted_assumptions")
            or schema.get("assumptions")
        ),
        extracted_conclusion=str(
            artifact.get("extracted_conclusion")
            or row.get("extracted_conclusion")
            or schema.get("claim")
            or ""
        ),
        lean_statement=str(artifact.get("lean_statement") or row.get("lean_statement") or ""),
        equivalence_verdict=_coerce_enum(
            ClaimEquivalenceVerdict,
            row.get("claim_equivalence_verdict") or artifact.get("equivalence_verdict"),
            ClaimEquivalenceVerdict.UNCLEAR,
        ),
        claim_equivalence_notes=_coerce_str_list(row.get("claim_equivalence_notes")),
        uncertainty_flags=_coerce_str_list(row.get("translation_uncertainty_flags")),
        adversarial_flags=_coerce_str_list(row.get("translation_adversarial_flags")),
        roundtrip_flags=_coerce_str_list(row.get("translation_roundtrip_flags")),
        context_pack=context,
        translation_fidelity_score=_float_or_none(row.get("translation_fidelity_score")),
        status_alignment_score=_float_or_none(row.get("status_alignment_score")),
        translation_validated=row.get("translation_validated") if isinstance(row.get("translation_validated"), bool) else None,
        independent_semantic_evidence=bool(artifact.get("independent_semantic_evidence")),
        status=_coerce_enum(VerificationStatus, row.get("status"), None),
        proof_method=_coerce_enum(ProofMethod, row.get("proof_method"), None),
        failure_kind=_coerce_enum(FailureKind, row.get("failure_kind"), None),
        canonical_theorem_id=str(row.get("canonical_theorem_id", "") or ""),
    )


def _clean_latex(text: str) -> str:
    out = text or ""
    out = re.sub(r"%.*", " ", out)
    out = re.sub(r"\\(?:begin|end)\{[^}]+\}", " ", out)
    out = re.sub(r"\\(?:label|ref|cite|eqref)\{[^}]*\}", " ", out)
    out = re.sub(r"\\(?:left|right)\b", " ", out)
    out = re.sub(r"\\([a-zA-Z]+)\*?(?:\[[^\]]*\])?\{([^{}]*)\}", r"\1 \2", out)
    out = re.sub(r"\\([a-zA-Z]+)\*?(?:\[[^\]]*\])?", r"\1", out)
    out = out.replace("\\(", " ").replace("\\)", " ")
    out = out.replace("\\[", " ").replace("\\]", " ")
    out = out.replace("$", " ")
    out = out.replace("{", " ").replace("}", " ")
    return out


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, tuple):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    return [text] if text else []


def _coerce_enum(enum_type: Any, value: Any, default: Any) -> Any:
    if value is None:
        return default
    try:
        return enum_type(value)
    except Exception:
        try:
            return enum_type(str(value).lower())
        except Exception:
            return default


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _tokens(text: str) -> set[str]:
    return {
        tok.lower()
        for tok in re.findall(r"[A-Za-z][A-Za-z0-9_']*|[0-9]+", text or "")
        if tok.lower() not in _STOPWORDS
    }


def _coverage(needle: str, haystack: str) -> float:
    a = _tokens(needle)
    b = _tokens(haystack)
    return len(a & b) / len(a) if a else 0.0


def _lean_added_assumptions(lean_statement: str, paper_norm: str) -> list[str]:
    assumptions: list[str] = []
    for m in re.finditer(r"[\(\{\[]([^:\)\}\]]+)\s*:\s*([^\)\}\]]+)[\)\}\]]", lean_statement or ""):
        name = m.group(1).strip()
        typ = _normalize_ws(m.group(2))
        if name.startswith("h") and _coverage(typ, paper_norm) < 0.15:
            assumptions.append(f"{name}: {typ}")
    return assumptions[:8]


def _conclusion_relation(extracted_conclusion: str, target: str) -> str:
    target_low = _normalize_ws(target).lower()
    if not target_low:
        return "missing"
    if target_low in {"true", "false", "proposition"}:
        return "trivial_target"
    if not extracted_conclusion.strip():
        return "unknown"
    if _normalize_ws(extracted_conclusion).lower() == target_low:
        return "matched"
    coverage = _coverage(extracted_conclusion, target)
    if coverage >= 0.45:
        return "matched"
    if coverage >= 0.15:
        return "compatible"
    return "mismatch"


def _is_diagnostic(
    *,
    lean_statement: str,
    flags: list[str],
    status: VerificationStatus | None,
    proof_method: ProofMethod | None,
    failure_kind: FailureKind | None,
) -> bool:
    lean_low = (lean_statement or "").lower()
    if status == VerificationStatus.TRANSLATION_LIMITED:
        return True
    if proof_method == ProofMethod.TRANSLATION_LIMITED:
        return True
    if failure_kind == FailureKind.TRANSLATION_FAILURE:
        return True
    if re.search(r":\s*(true|false)\s*(?::=|$)", lean_low):
        return True
    return any(any(marker in f for marker in _DIAGNOSTIC_MARKERS) for f in [lean_low, *flags])


def _confidence(
    *,
    alignment: StatementAlignmentClass,
    paper_coverage: float,
    lean_coverage: float,
    fidelity: float,
    status_alignment: float,
    independent: bool,
    missing_assumptions: list[str],
    diagnostic: bool,
) -> float:
    if diagnostic:
        return 0.9
    base = 0.20 + 0.25 * paper_coverage + 0.20 * lean_coverage + 0.20 * fidelity + 0.15 * status_alignment
    if independent:
        base += 0.15
    if missing_assumptions:
        base -= min(0.25, 0.07 * len(missing_assumptions))
    if alignment == StatementAlignmentClass.UNRELATED:
        base = max(base, 0.75 if paper_coverage < 0.08 and lean_coverage < 0.08 else 0.55)
    return round(max(0.0, min(1.0, base)), 4)
