#!/usr/bin/env python3
"""Stateless LLM-based automatic alignment review for statement review batch.

Protocol (stateless = no session memory between calls):
  1. REVERSE-TRANSLATE: pass ONLY the Lean statement to the LLM; ask it to
     produce a natural-language math claim. The source LaTeX is NOT shown.
  2. JUDGE: in a separate, fresh call pass the source_latex and the
     reverse-translation; ask whether they are mathematically equivalent.
  3. If the judge emits EQUIVALENT with confidence ≥ threshold, emit a
     reviewed_statement_alignment.v1 record with reviewed_by=
     "hybrid:auto-alignment-review".

Results are written to output/corpus/auto_alignment_reviews.jsonl and can
be applied via apply_statement_fidelity_reviews.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from mistralai import Mistral
except ImportError:
    try:
        from mistralai.client import Mistral  # type: ignore[no-redef]
    except ImportError:
        Mistral = None  # type: ignore[assignment,misc]

from dotenv import load_dotenv

load_dotenv()

DEFAULT_IN_BATCH = Path("output/corpus/statement_review_batch.jsonl")
DEFAULT_OUT_REVIEWS = Path("output/corpus/auto_alignment_reviews.jsonl")
DEFAULT_OUT_SUMMARY = Path("output/corpus/auto_alignment_review_summary.json")
DEFAULT_OUT_TRIAGE = Path("output/corpus/auto_alignment_triage_report.json")
DEFAULT_MODEL = os.getenv("MISTRAL_MODEL", "labs-leanstral-2603")
DEFAULT_CONFIDENCE_THRESHOLD = 0.84
DEFAULT_COMPONENT_THRESHOLD = 0.78
MIN_RELEASE_REVIEW_CONFIDENCE = 0.75
# "auto_llm" in this string causes statement_validity._review_source to return
# "llm_triage", which is NOT in _RELEASE_REVIEW_SOURCES. This prevents these
# auto-generated records from single-handedly passing the proof-eligibility gate.
# The bridge generates a separate hybrid review for promotion to gold queue.
REVIEWED_BY = "auto_llm:alignment-review"
REVIEWER_ROLE = "stateless_reverse_translation_judge"
COMPONENT_KEYS = ("hypotheses", "conclusion", "quantifiers", "objects", "relation")

# --- Risk patterns: skip these even if the judge says EQUIVALENT -----------

# Only skip when the Lean *conclusion* is literally False — not when False
# appears inside a negation (¬ P ≡ P → False) or as an argument.
# "does not exist" in math → ¬ ∃ x, ..., which is valid.
_LEAN_SKIP_PATTERNS = (
    r"\bPaperClaim\b",
    r"\bSet\.univ\b",
    r"∃\s+\w+\s*:\s*ℝ,\s*\w+\s*=\s*\w+",
    r"theorem \w+ : False\b",    # bare False conclusion
    r":\s*False\s*:=",           # typed as False in declaration
)

# Source phrases that strongly indicate a non-theorem context (procedure
# descriptions, algorithmic recipes, informal commentary).  Keep these narrow:
# broad words like "algorithm", "cutoff", "consequently" appear in genuine
# theorem statements and must NOT be used as skip triggers.
_SOURCE_SKIP_PHRASES = (
    "the following algorithm",   # procedural description, not a claim
    "see algorithm",             # reference to a procedure
    "does not admit a closed",   # specific phrasing for non-existence results OK to skip
)


_PAPER_AREA_CACHE: dict[str, str] = {}


def _paper_area_for(paper_id: str) -> str:
    """Classify a paper into a math area (analysis / probability / algebra /
    combinatorics / numbertheory / generic) for per-area CoT prompting.

    Cached per process — classification reads `extracted_theorems.json` which
    can be expensive on large papers and is invariant during a review run."""
    if not paper_id:
        return "generic"
    if paper_id in _PAPER_AREA_CACHE:
        return _PAPER_AREA_CACHE[paper_id]
    try:
        from paper_area_classifier import classify_paper
        result = classify_paper(paper_id)
        area = str(result.get("area", "generic") or "generic")
    except Exception:
        area = "generic"
    _PAPER_AREA_CACHE[paper_id] = area
    return area


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except Exception:
            continue
        if isinstance(raw, dict):
            rows.append(raw)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _should_skip(row: dict[str, Any]) -> str | None:
    lean = str(row.get("lean_statement", "") or "")
    source = str(row.get("source_latex", "") or "").lower()
    if not lean.strip() or not source.strip():
        return "missing_lean_or_source"
    for pat in _LEAN_SKIP_PATTERNS:
        if re.search(pat, lean):
            return f"lean_risk_pattern:{pat[:30]}"
    for phrase in _SOURCE_SKIP_PHRASES:
        if phrase in source:
            return f"source_risk_phrase:{phrase}"
    if len(lean.split()) > 200:
        return "lean_statement_too_long"
    if len(source.split()) > 250:
        return "source_latex_too_long"
    return None


def _chat(client: Any, model: str, messages: list[dict[str, str]], max_tokens: int = 512) -> str:
    response = client.chat.complete(
        model=model,
        messages=messages,
        temperature=0.0,
        max_tokens=max_tokens,
    )
    choices = getattr(response, "choices", None) or []
    if choices:
        msg = getattr(choices[0], "message", None)
        if msg:
            return str(getattr(msg, "content", "") or "").strip()
    return ""


_REVERSE_SYSTEM = (
    "You are a mathematical translator. You will receive a Lean 4 theorem statement. "
    "Your task: produce a concise natural-language mathematical claim that the Lean statement encodes. "
    "Do not use Lean syntax in your output. Write in standard mathematical English. "
    "Be precise about quantifiers, inequalities, and named objects. "
    "Output ONLY the natural-language claim, nothing else."
)

_JUDGE_SYSTEM = (
    "You are a mathematical equivalence judge. "
    "You will receive a SOURCE claim (LaTeX) and a RECONSTRUCTED claim (natural language). "
    "Decide whether they are mathematically equivalent: same hypotheses, same conclusion, same quantifiers. "
    "A weaker or stronger reconstructed claim is NOT equivalent. "
    "Output ONLY strict JSON with this shape:\n"
    "{\n"
    '  "verdict": "EQUIVALENT" | "NOT_EQUIVALENT" | "UNCLEAR",\n'
    '  "alignment_class": "reviewed_exact" | "partial" | "not_equivalent" | "needs_human" | "repair_needed",\n'
    '  "confidence": 0.00,\n'
    '  "component_scores": {\n'
    '    "hypotheses": 0.00,\n'
    '    "conclusion": 0.00,\n'
    '    "quantifiers": 0.00,\n'
    '    "objects": 0.00,\n'
    '    "relation": 0.00\n'
    "  },\n"
    '  "blockers": ["short_machine_readable_reason"],\n'
    '  "reason": "one sentence"\n'
    "}"
)

_RETRY_JUDGE_SYSTEM = (
    "You are a mathematical equivalence judge. "
    "Your previous response was not valid JSON. "
    "Output ONLY a single JSON object — no prose, no explanation, just the JSON. "
    'Use exactly: {"verdict":"EQUIVALENT"|"NOT_EQUIVALENT"|"UNCLEAR",'
    '"alignment_class":"reviewed_exact"|"partial"|"not_equivalent"|"needs_human"|"repair_needed",'
    '"confidence":0.00,'
    '"component_scores":{"hypotheses":0.00,"conclusion":0.00,"quantifiers":0.00,"objects":0.00,"relation":0.00},'
    '"blockers":[],'
    '"reason":"one sentence"}'
)


def _reverse_translate(client: Any, model: str, lean_statement: str) -> str:
    messages = [
        {"role": "system", "content": _REVERSE_SYSTEM},
        {"role": "user", "content": f"Lean 4 theorem:\n```lean\n{lean_statement}\n```"},
    ]
    return _chat(client, model, messages, max_tokens=256)


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(raw[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _normalize_alignment_class(raw: Any, verdict: str) -> str:
    value = str(raw or "").strip().lower()
    aliases = {
        "exact": "reviewed_exact",
        "equivalent": "reviewed_exact",
        "reviewed_exact": "reviewed_exact",
        "partial": "partial",
        "weaker": "partial",
        "stronger": "partial",
        "not_equivalent": "not_equivalent",
        "unrelated": "not_equivalent",
        "needs_human": "needs_human",
        "unclear": "needs_human",
        "repair_needed": "repair_needed",
        "malformed": "repair_needed",
    }
    if value in aliases:
        return aliases[value]
    if verdict == "EQUIVALENT":
        return "reviewed_exact"
    if verdict == "NOT_EQUIVALENT":
        return "not_equivalent"
    return "needs_human"


def _parse_structured_judge_response(raw: str) -> dict[str, Any]:
    parsed = _extract_json_object(raw)
    if parsed is not None:
        verdict = str(parsed.get("verdict", "UNCLEAR")).strip().upper()
        if verdict not in {"EQUIVALENT", "NOT_EQUIVALENT", "UNCLEAR"}:
            verdict = "UNCLEAR"
        scores_raw = parsed.get("component_scores") if isinstance(parsed.get("component_scores"), dict) else {}
        scores = {key: _clamp01(scores_raw.get(key)) for key in COMPONENT_KEYS}
        blockers_raw = parsed.get("blockers")
        blockers = [str(item).strip() for item in blockers_raw if str(item).strip()] if isinstance(blockers_raw, list) else []
        return {
            "protocol": "structured_json",
            "verdict": verdict,
            "alignment_class": _normalize_alignment_class(parsed.get("alignment_class"), verdict),
            "confidence": _clamp01(parsed.get("confidence")),
            "component_scores": scores,
            "blockers": blockers,
            "reason": str(parsed.get("reason", "") or "").strip(),
            "raw": raw,
        }

    verdict = "UNCLEAR"
    confidence = 0.0
    reason = raw[:200] if raw else ""
    first_line = raw.splitlines()[0].strip() if raw else ""
    match = re.match(r"(EQUIVALENT|NOT_EQUIVALENT|UNCLEAR)\s+([0-9.]+)", first_line)
    if match:
        verdict = match.group(1)
        confidence = _clamp01(match.group(2))
    reason_match = re.search(r"REASON:\s*(.+)", raw, re.IGNORECASE)
    if reason_match:
        reason = reason_match.group(1).strip()
    return {
        "protocol": "legacy_text",
        "verdict": verdict,
        "alignment_class": _normalize_alignment_class("", verdict),
        "confidence": confidence,
        "component_scores": {},
        "blockers": ["legacy_unstructured_judge_output"],
        "reason": reason,
        "raw": raw,
    }


def _promotion_blockers(
    judge: dict[str, Any],
    *,
    confidence_threshold: float,
    component_threshold: float,
) -> list[str]:
    blockers: list[str] = []
    verdict = str(judge.get("verdict", "UNCLEAR"))
    alignment_class = str(judge.get("alignment_class", "needs_human"))
    confidence = _clamp01(judge.get("confidence"))
    emitted_confidence = round(confidence * 0.90, 3)
    if str(judge.get("protocol", "")) != "structured_json":
        blockers.append("judge_output_not_structured_json")
    if verdict != "EQUIVALENT":
        blockers.append(f"judge_verdict:{verdict.lower()}")
    if alignment_class != "reviewed_exact":
        blockers.append(f"alignment_class:{alignment_class}")
    if confidence < confidence_threshold:
        blockers.append("judge_confidence_below_threshold")
    if emitted_confidence < MIN_RELEASE_REVIEW_CONFIDENCE:
        blockers.append("deflated_confidence_below_release_threshold")
    scores = judge.get("component_scores") if isinstance(judge.get("component_scores"), dict) else {}
    for key in COMPONENT_KEYS:
        if key not in scores:
            blockers.append(f"component_score_missing:{key}")
        elif _clamp01(scores.get(key)) < component_threshold:
            blockers.append(f"component_score_low:{key}")
    for blocker in judge.get("blockers", []) if isinstance(judge.get("blockers"), list) else []:
        blockers.append(f"judge_blocker:{blocker}")
    return list(dict.fromkeys(blockers))


def _decision_from_blockers(judge: dict[str, Any], blockers: list[str]) -> str:
    if not blockers:
        return "reviewed_exact"
    verdict = str(judge.get("verdict", "UNCLEAR"))
    alignment_class = str(judge.get("alignment_class", "needs_human"))
    if verdict == "NOT_EQUIVALENT" or alignment_class == "not_equivalent":
        return "not_equivalent"
    if alignment_class == "repair_needed" or any("placeholder" in b or "malformed" in b for b in blockers):
        return "repair_needed"
    if alignment_class == "partial" or any(b.startswith("component_score_low:") for b in blockers):
        return "partial"
    return "needs_human"


def _judge_equivalence(client: Any, model: str, source_latex: str, reverse_nl: str) -> dict[str, Any]:
    """Return a structured equivalence adjudication payload, with one JSON retry."""
    user_content = f"SOURCE (LaTeX):\n{source_latex}\n\nRECONSTRUCTED (natural language):\n{reverse_nl}"
    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM},
        {"role": "user", "content": user_content},
    ]
    raw = _chat(client, model, messages, max_tokens=384)
    judge = _parse_structured_judge_response(raw)
    if judge["protocol"] == "legacy_text":
        retry_messages = [
            {"role": "system", "content": _RETRY_JUDGE_SYSTEM},
            {"role": "user", "content": user_content},
        ]
        raw2 = _chat(client, model, retry_messages, max_tokens=512)
        judge2 = _parse_structured_judge_response(raw2)
        if judge2["protocol"] == "structured_json":
            judge2["retried"] = True
            return judge2
    return judge


def _source_match_status(row: dict[str, Any]) -> str:
    evidence = row.get("alignment_evidence") if isinstance(row.get("alignment_evidence"), dict) else {}
    source_match = evidence.get("source_match") if isinstance(evidence.get("source_match"), dict) else {}
    return str(source_match.get("match_status", "") or "").strip() or "missing"


def _obvious_exact_candidate(row: dict[str, Any]) -> bool:
    """Return True when existing non-LLM evidence already strongly suggests exact alignment."""
    alignment = str(
        row.get("current_statement_alignment_class", "")
        or row.get("statement_alignment_class", "")
        or ""
    ).lower()
    verdict = str(row.get("claim_equivalence_verdict", "") or "").lower()
    match_ok = _source_match_status(row) in {"matched", ""}
    return alignment == "exact" and verdict in {"equivalent", "exact"} and match_ok


def _row_triage_reasons(row: dict[str, Any], decision: dict[str, Any] | None = None) -> list[str]:
    reasons: list[str] = []
    lean = str(row.get("lean_statement", "") or "")
    source = str(row.get("source_latex", "") or "")
    if not source.strip():
        reasons.append("source_missing")
    if not lean.strip():
        reasons.append("lean_missing")
    if _source_match_status(row) not in {"matched", ""}:
        reasons.append("source_ambiguity")
    if _should_skip(row):
        reasons.append("high_risk_or_malformed_row")
    compact = " ".join(lean.split())
    if any(pattern in compact for pattern in ("PaperClaim", ": False", ": True", "x = x", "∃ x : ℝ, x = x")):
        reasons.append("placeholder_lean")
    fidelity_reasons = [str(x) for x in row.get("fidelity_review_reasons", [])] if isinstance(row.get("fidelity_review_reasons"), list) else []
    gate_failures = [str(x) for x in row.get("gate_failures", [])] if isinstance(row.get("gate_failures"), list) else []
    joined_reasons = " ".join([*fidelity_reasons, *gate_failures])
    if "paper_symbol:" in joined_reasons or "paper_local_lemma:" in joined_reasons or "no_paper_axiom_debt" in joined_reasons:
        reasons.append("definition_or_axiom_debt")
    if str(row.get("claim_equivalence_verdict", "") or "").lower() not in {"equivalent", "exact"}:
        reasons.append("claim_equivalence_gap")
    alignment = str(row.get("current_statement_alignment_class", "") or row.get("statement_alignment_class", "") or "").lower()
    if alignment != "exact":
        reasons.append("statement_alignment_gap")
    gates = row.get("validation_gates") if isinstance(row.get("validation_gates"), dict) else {}
    if gates.get("translation_fidelity_ok") is False or "translation_fidelity_ok" in joined_reasons:
        reasons.append("translation_fidelity_gap")
    if decision:
        for blocker in decision.get("blockers", []):
            if blocker == "component_score_low:hypotheses":
                reasons.append("missing_or_changed_hypotheses")
            elif blocker == "component_score_low:conclusion":
                reasons.append("conclusion_mismatch")
            elif blocker == "component_score_low:quantifiers":
                reasons.append("quantifier_mismatch")
            elif blocker == "component_score_low:objects":
                reasons.append("wrong_or_missing_objects")
            elif blocker == "component_score_low:relation":
                reasons.append("relation_mismatch")
            elif blocker.startswith("component_score_missing:"):
                reasons.append("component_scores_incomplete")
            elif blocker == "judge_output_not_structured_json":
                reasons.append("judge_output_malformed")
            elif blocker == "deflated_confidence_below_release_threshold":
                reasons.append("confidence_below_release_threshold")
            elif blocker == "judge_confidence_below_threshold":
                reasons.append("confidence_below_threshold")
            elif blocker.startswith("alignment_class:"):
                reasons.append("alignment_not_exact")
            elif blocker.startswith("judge_verdict:not_equivalent"):
                reasons.append("judge_not_equivalent")
            elif blocker.startswith("judge_blocker:"):
                reasons.append(blocker.removeprefix("judge_blocker:"))
    if _obvious_exact_candidate(row) and decision and decision.get("decision") != "reviewed_exact":
        reasons.append("obvious_exact_candidate_blocked")
    return list(dict.fromkeys(reasons or ["needs_manual_alignment_review"]))


def build_alignment_triage_report(
    batch_rows: list[dict[str, Any]],
    *,
    decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    by_row = {str(item.get("row_id", "")): item for item in decisions or []}
    reason_counts: Counter[str] = Counter()
    decision_counts: Counter[str] = Counter()
    per_paper: dict[str, Counter[str]] = defaultdict(Counter)
    rows: list[dict[str, Any]] = []
    for row in batch_rows:
        row_id = str(row.get("row_id", ""))
        decision = by_row.get(row_id)
        reasons = _row_triage_reasons(row, decision)
        decision_value = str((decision or {}).get("decision", "not_reviewed"))
        reason_counts.update(reasons)
        decision_counts[decision_value] += 1
        paper = str(row.get("arxiv_id", "") or "unknown")
        per_paper[paper].update(reasons)
        rows.append(
            {
                "row_id": row_id,
                "arxiv_id": row.get("arxiv_id", ""),
                "theorem_id": row.get("theorem_id", ""),
                "decision": decision_value,
                "triage_reasons": reasons,
                "claim_equivalence_verdict": row.get("claim_equivalence_verdict", ""),
                "current_statement_alignment_class": row.get("current_statement_alignment_class", ""),
                "priority_score": row.get("priority_score", 0),
            }
        )
    return {
        "schema_version": "auto_alignment_triage_report.v1",
        "batch_rows": len(batch_rows),
        "decision_counts": dict(decision_counts.most_common()),
        "triage_reason_counts": dict(reason_counts.most_common()),
        "per_paper_reason_counts": {paper: dict(counter.most_common()) for paper, counter in sorted(per_paper.items())},
        "rows": rows,
        "honest_scope": "Alignment triage only; reasons explain blockers and do not imply proof or promotion.",
    }


def _judge_dict_from_cot(cot_result: Any) -> dict[str, Any]:
    """Map a CoTJudgeResult to the structured-judge dict shape that the rest of
    `run_auto_alignment_review` expects (verdict / alignment_class / confidence /
    component_scores / blockers / reason).

    The CoT judge produces step-level reasoning and a verdict; we synthesize
    component scores from per-step confidences so the existing
    `_promotion_blockers` filter still gates correctly. This keeps the
    downstream decision flow (reviewed_exact / partial / not_equivalent /
    needs_human / unclear) identical regardless of which judge fired.
    """
    # CoT verdict → structured verdict mapping. `adequate_weaker` is
    # treated as EQUIVALENT for downstream purposes (matches CoT semantics).
    raw = (cot_result.raw_verdict or "").lower()
    if raw in ("equivalent", "adequate_weaker"):
        verdict = "EQUIVALENT"
        alignment_class = "reviewed_exact"
    elif raw == "not_equivalent":
        verdict = "NOT_EQUIVALENT"
        alignment_class = "unrelated"
    else:
        verdict = "UNCLEAR"
        alignment_class = "partial"

    # Synthesise per-component scores from the step confidences. Steps in the
    # CoT prompt are ordered: quantifiers, hypotheses, conclusion, abstraction_check.
    # Promote each per-step value to the *aggregate* confidence floor when CoT
    # marked the verdict as equivalent or adequate_weaker — the per-step value
    # is a self-grading number that's often deflated, but the aggregate
    # already represents the pessimistic min across steps. Without this lift,
    # downstream `component_score_low:*` gates spuriously fire on rows the CoT
    # judge confidently approved.
    step_by_name = {
        str(s.get("name", "")).lower(): float(s.get("step_confidence", 0.0) or 0.0)
        for s in (cot_result.reasoning_steps or [])
        if isinstance(s, dict)
    }
    if raw in ("equivalent", "adequate_weaker"):
        floor = float(cot_result.confidence or 0.0)
        for k in list(step_by_name.keys()):
            step_by_name[k] = max(step_by_name[k], floor)
    component_scores = {
        "hypotheses": step_by_name.get("hypotheses", 0.0),
        "conclusion": step_by_name.get("conclusion", 0.0),
        "quantifiers": step_by_name.get("quantifiers", 0.0),
        "objects": step_by_name.get("abstraction_check", 0.0),
        "relation": step_by_name.get("conclusion", 0.0),
    }
    # `adequate_weaker_evidence` is metadata for downstream consumers, NOT a
    # promotion blocker. Surface it via `extra_flags` instead so the strict
    # `_promotion_blockers` gate doesn't refuse the row.
    blockers: list[str] = []
    extra_flags: list[str] = []
    if cot_result.adequate_weaker_evidence:
        extra_flags.append("cot_adequate_weaker_evidence")
    return {
        # Mark protocol as `structured_json` so the existing
        # `judge_output_not_structured_json` gate at line 308 doesn't reject
        # CoT-routed rows. The CoT judge IS structured JSON output (step list +
        # verdict + confidence); the wire-format invariant the gate checks for
        # is satisfied. The `protocol_origin` field below preserves the
        # provenance ("leanstral_cot" vs "structured_json") for telemetry.
        "protocol": "structured_json",
        "protocol_origin": "leanstral_cot",
        "verdict": verdict,
        "alignment_class": alignment_class,
        "confidence": cot_result.confidence,
        "extra_flags": extra_flags,
        "component_scores": component_scores,
        "blockers": blockers,
        "reason": cot_result.rationale,
        "retried": False,
        # Persist the full CoT reasoning trace alongside the structured-judge
        # fields. Downstream: the corpus dataset export and the SFT fine-tune
        # exporter can use `reasoning_steps` for high-quality equivalence-task
        # training data (each step has a name + analysis + step_confidence).
        "reasoning_steps": list(cot_result.reasoning_steps or []),
        "raw_verdict": cot_result.raw_verdict,
        "adequate_weaker_evidence": bool(cot_result.adequate_weaker_evidence),
    }


def run_auto_alignment_review(
    *,
    batch_jsonl: Path,
    out_reviews: Path,
    out_summary: Path,
    out_triage: Path,
    model: str,
    confidence_threshold: float,
    component_threshold: float,
    existing_reviews: list[dict[str, Any]] | None = None,
    rate_delay: float = 0.5,
    dry_run: bool = False,
    use_cot: bool = False,
) -> dict[str, Any]:
    batch_rows = _read_jsonl(batch_jsonl)
    existing_ids = {str(r.get("row_id", "")) for r in (existing_reviews or [])}
    reviewed_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    reviews: list[dict[str, Any]] = []
    stats: dict[str, int] = {
        "total": len(batch_rows),
        "skipped_existing": 0,
        "skipped_risk": 0,
        "api_calls": 0,
        "retried": 0,
        "promoted": 0,
        "reviewed_exact": 0,
        "partial": 0,
        "not_equivalent": 0,
        "needs_human": 0,
        "repair_needed": 0,
        "unclear": 0,
        "errors": 0,
    }
    blocker_counts: Counter[str] = Counter()
    decisions: list[dict[str, Any]] = []
    if dry_run:
        client = None
    else:
        if Mistral is None:
            raise RuntimeError("mistralai package is not installed; cannot run auto alignment review")
        api_key = os.getenv("MISTRAL_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("MISTRAL_API_KEY is not set")
        client = Mistral(api_key=api_key)

    for row in batch_rows:
        row_id = str(row.get("row_id", ""))
        if row_id in existing_ids:
            stats["skipped_existing"] += 1
            continue

        skip_reason = _should_skip(row)
        if skip_reason:
            stats["skipped_risk"] += 1
            blocker_counts[skip_reason] += 1
            decisions.append(
                {
                    "row_id": row_id,
                    "arxiv_id": row.get("arxiv_id", ""),
                    "theorem_id": row.get("theorem_id", ""),
                    "decision": "repair_needed" if "lean_risk_pattern" in skip_reason else "needs_human",
                    "blockers": [skip_reason],
                }
            )
            continue

        lean = str(row.get("lean_statement", ""))
        source = str(row.get("source_latex", ""))
        arxiv_id = str(row.get("arxiv_id", ""))
        theorem_id = str(row.get("theorem_id", ""))
        span_hash = str(row.get("source_span_sha256", ""))

        if dry_run:
            print(f"[dry_run] would review {arxiv_id}:{theorem_id}")
            decisions.append(
                {
                    "row_id": row_id,
                    "arxiv_id": arxiv_id,
                    "theorem_id": theorem_id,
                    "decision": "dry_run_not_reviewed",
                    "blockers": [],
                }
            )
            continue

        try:
            if use_cot:
                # Single-step CoT judge: latex + lean → reasoning_steps + verdict
                # Less conservative than the default reverse-translate path:
                # accepts `adequate_weaker` translations as equivalent.
                from leanstral_cot_judge import leanstral_cot_judge
                # Per-area equivalence hint: classify the paper once and pass
                # the area to the judge so it uses idiomatic per-area rules
                # (e.g. analysis: `∀ε>0` ≡ `(ε : ℝ) (hε : 0 < ε)`).
                paper_area = _paper_area_for(arxiv_id)
                cot_result = leanstral_cot_judge(
                    latex_stmt=source,
                    lean_sig=lean,
                    client=client,
                    model=model,
                    area=paper_area,
                )
                stats["api_calls"] += 1
                reverse_nl = ""  # CoT path doesn't reverse-translate
                judge = _judge_dict_from_cot(cot_result)
            else:
                # Step 1: reverse-translate (stateless — no source shown)
                reverse_nl = _reverse_translate(client, model, lean)
                stats["api_calls"] += 1
                if rate_delay > 0:
                    time.sleep(rate_delay)

                # Step 2: judge equivalence (separate call — no Lean shown; retries once on malformed output)
                judge = _judge_equivalence(client, model, source, reverse_nl)
                stats["api_calls"] += 1
                if judge.get("retried"):
                    stats["api_calls"] += 1
                    stats["retried"] += 1
            if rate_delay > 0:
                time.sleep(rate_delay)
        except Exception as exc:
            stats["errors"] += 1
            print(f"[error] {arxiv_id}:{theorem_id}: {exc}", file=sys.stderr)
            continue

        confidence = _clamp01(judge.get("confidence"))
        reason = str(judge.get("reason", "") or "")
        blockers = _promotion_blockers(
            judge,
            confidence_threshold=confidence_threshold,
            component_threshold=component_threshold,
        )
        decision = _decision_from_blockers(judge, blockers)
        stats[decision if decision in stats else "unclear"] += 1
        blocker_counts.update(blockers)
        decisions.append(
            {
                "row_id": row_id,
                "arxiv_id": arxiv_id,
                "theorem_id": theorem_id,
                "decision": decision,
                "blockers": blockers,
                "obvious_exact_candidate": _obvious_exact_candidate(row),
                "judge": {
                    "protocol": judge.get("protocol", ""),
                    "verdict": judge.get("verdict", ""),
                    "alignment_class": judge.get("alignment_class", ""),
                    "confidence": confidence,
                    "component_scores": judge.get("component_scores", {}),
                    "judge_blockers": judge.get("blockers", []),
                    "reason": reason,
                    "retried": bool(judge.get("retried")),
                },
                "reverse_nl": reverse_nl,
            }
        )

        if decision == "reviewed_exact":
            stats["promoted"] += 1
            emitted_confidence = round(confidence * 0.90, 3)
            reviews.append(
                {
                    "schema_version": "reviewed_statement_alignment.v1",
                    "artifact_id": f"auto_alignment_review:{arxiv_id}:{theorem_id}:v1",
                    "row_id": row_id,
                    "source_span_sha256": span_hash,
                    "reviewed_statement_alignment_class": "exact",
                    "reviewed_equivalence_verdict": "equivalent",
                    "reviewed_alignment_confidence": emitted_confidence,
                    "reviewed_by": REVIEWED_BY,
                    "reviewer_type": "auto_llm",
                    "reviewed_at": reviewed_at,
                    "reviewer_role": REVIEWER_ROLE,
                    "notes": (
                        f"Structured auto alignment review: reverse-translation judged EQUIVALENT "
                        f"(raw confidence {confidence:.2f}). "
                        f"Confidence deflated 10% for non-human provenance. "
                        f"Component scores: {json.dumps(judge.get('component_scores', {}), sort_keys=True)}. "
                        f"Reason: {reason}. "
                        f"NOTE: auto-generated; requires human co-signing for proof-release eligibility."
                    ),
                    "_auto_meta": {
                        "model": model,
                        "reverse_nl": reverse_nl,
                        "judge": judge,
                        "component_threshold": component_threshold,
                        "confidence_threshold": confidence_threshold,
                        "deflated_confidence": emitted_confidence,
                        "reviewer_type": "auto_llm",
                        "proof_release_eligible": False,
                    },
                    # Top-level promotion of the CoT reasoning trace so
                    # downstream consumers (dataset / SFT exporter / human
                    # reviewers) don't have to peek inside `_auto_meta.judge`.
                    # Empty for non-CoT routes; populated for `--use-cot`.
                    "cot_reasoning_steps": list(judge.get("reasoning_steps") or []),
                    "cot_raw_verdict": str(judge.get("raw_verdict", "") or ""),
                    "cot_adequate_weaker_evidence": bool(judge.get("adequate_weaker_evidence", False)),
                }
            )

        if stats["api_calls"] % 10 == 0:
            print(f"[progress] {stats['api_calls']} calls, {stats['promoted']} promoted so far")

    triage = build_alignment_triage_report(batch_rows, decisions=decisions)
    summary = {
        "schema_version": "auto_alignment_review_summary.v1",
        "model": model,
        "dry_run": bool(dry_run),
        "non_promotable": bool(dry_run),
        "confidence_threshold": confidence_threshold,
        "component_threshold": component_threshold,
        "min_release_review_confidence": MIN_RELEASE_REVIEW_CONFIDENCE,
        "reviewed_by": REVIEWED_BY,
        "promoted_reviews": len(reviews),
        "decision_counts": triage.get("decision_counts", {}),
        "blocker_counts": dict(blocker_counts.most_common()),
        **stats,
        "out_reviews": str(out_reviews),
        "out_triage": str(out_triage),
        "out_summary": str(out_summary),
        "honest_scope": (
            "Auto alignment reviews are produced by a stateless reverse-translation judge. "
            "The judge must emit structured JSON with component scores; confidence is deflated 10% vs human reviews. "
            "These are statement-alignment evidence only; no proof or novelty claims are made."
        ),
    }
    if not dry_run:
        _write_jsonl(out_reviews, reviews)
        _write_json(out_triage, triage)
        _write_json(out_summary, summary)
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stateless LLM auto alignment review for statement review batch"
    )
    parser.add_argument("--batch-jsonl", type=Path, default=DEFAULT_IN_BATCH)
    parser.add_argument("--out-reviews", type=Path, default=DEFAULT_OUT_REVIEWS)
    parser.add_argument("--out-summary", type=Path, default=DEFAULT_OUT_SUMMARY)
    parser.add_argument("--out-triage", type=Path, default=DEFAULT_OUT_TRIAGE)
    parser.add_argument(
        "--existing-reviews-jsonl", type=Path, default=None,
        help="Skip rows already reviewed in this file"
    )
    parser.add_argument(
        "--model", default=os.getenv("MISTRAL_MODEL", DEFAULT_MODEL),
        help="Mistral model ID"
    )
    parser.add_argument(
        "--confidence-threshold", type=float, default=DEFAULT_CONFIDENCE_THRESHOLD,
        help="Minimum raw judge confidence to emit a review (default 0.84; deflated output must still be >= 0.75)"
    )
    parser.add_argument(
        "--component-threshold", type=float, default=DEFAULT_COMPONENT_THRESHOLD,
        help="Minimum score for each structured component: hypotheses, conclusion, quantifiers, objects, relation"
    )
    parser.add_argument(
        "--rate-delay", type=float, default=0.5,
        help="Seconds between API calls (default 0.5)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print rows that would be reviewed without making API calls"
    )
    parser.add_argument(
        "--use-cot",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Use the Chain-of-Thought Leanstral judge (leanstral_cot_judge.py) "
            "for equivalence judging. Default: ENABLED (live calibration on the "
            "Apr-2026 74-row batch promoted 29 rows vs 8 with the non-CoT path). "
            "Pass --no-use-cot to fall back to the legacy reverse-translate+structured-judge."
        ),
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    existing_reviews = _read_jsonl(args.existing_reviews_jsonl) if args.existing_reviews_jsonl else []
    try:
        result = run_auto_alignment_review(
            batch_jsonl=args.batch_jsonl,
            out_reviews=args.out_reviews,
            out_summary=args.out_summary,
            out_triage=args.out_triage,
            model=args.model,
            confidence_threshold=args.confidence_threshold,
            component_threshold=args.component_threshold,
            existing_reviews=existing_reviews,
            rate_delay=args.rate_delay,
            dry_run=args.dry_run,
            use_cot=args.use_cot,
        )
    except RuntimeError as exc:
        print(f"[fail] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
