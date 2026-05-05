#!/usr/bin/env python3
"""Apply reviewed statement-fidelity adjudications to corpus rows."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from build_gold_proof_queue import MIN_PROOF_ALIGNMENT_CONFIDENCE


REVIEW_SCHEMA_VERSION = "reviewed_statement_alignment.v1"
ALIGNMENT_CLASSES = {"exact", "partial", "weaker", "stronger", "diagnostic", "unrelated", "unknown"}
EQUIVALENCE_VERDICTS = {"equivalent", "exact", "weaker", "stronger", "not_equivalent", "unclear", "unknown"}


def source_span_sha256(row: dict[str, Any]) -> str:
    span = row.get("source_span") if isinstance(row.get("source_span"), dict) else {}
    payload = json.dumps(span, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def _review_promotes_alignment(row: dict[str, Any], review: dict[str, Any]) -> bool:
    return (
        str(review.get("reviewed_statement_alignment_class", "")) == "exact"
        and str(review.get("reviewed_equivalence_verdict", "")) in {"equivalent", "exact"}
        and float(review.get("reviewed_alignment_confidence", 0.0) or 0.0) >= MIN_PROOF_ALIGNMENT_CONFIDENCE
        and str(row.get("source_span_quality", "")) in {"extractor_native", "reviewed"}
        and bool(str(review.get("reviewed_by", "")).strip())
        and bool(str(review.get("reviewed_at", "")).strip())
    )


def review_validation_errors(review: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if str(review.get("schema_version", "")) != REVIEW_SCHEMA_VERSION:
        errors.append("schema_version_invalid")
    if not str(review.get("row_id", "")).strip():
        errors.append("row_id_missing")
    if not str(review.get("source_span_sha256", "") or review.get("source_span_hash", "")).strip():
        errors.append("source_span_sha256_missing")
    alignment_class = str(review.get("reviewed_statement_alignment_class", "")).strip()
    if alignment_class not in ALIGNMENT_CLASSES:
        errors.append("reviewed_statement_alignment_class_invalid")
    verdict = str(review.get("reviewed_equivalence_verdict", "")).strip()
    if verdict not in EQUIVALENCE_VERDICTS:
        errors.append("reviewed_equivalence_verdict_invalid")
    try:
        confidence = float(review.get("reviewed_alignment_confidence", 0.0) or 0.0)
    except Exception:
        confidence = -1.0
    if not 0.0 <= confidence <= 1.0:
        errors.append("reviewed_alignment_confidence_invalid")
    if not str(review.get("reviewed_by", "")).strip():
        errors.append("reviewed_by_missing")
    if not str(review.get("reviewed_at", "")).strip():
        errors.append("reviewed_at_missing")
    if alignment_class == "exact" and verdict not in {"equivalent", "exact"}:
        errors.append("exact_review_requires_equivalent_verdict")
    return errors


def apply_review(row: dict[str, Any], review: dict[str, Any]) -> tuple[dict[str, Any], str]:
    errors = review_validation_errors(review)
    if errors:
        return row, "invalid_review:" + errors[0]
    expected_span = str(review.get("source_span_sha256", "") or review.get("source_span_hash", "")).strip()
    actual_span = source_span_sha256(row)
    if str(review.get("row_id", "")) != str(row.get("row_id", "")):
        return row, "row_id_mismatch"
    if expected_span and expected_span != actual_span:
        return row, "source_span_mismatch"

    reviewed = dict(row)
    reviewed["reviewed_statement_alignment_class"] = str(review.get("reviewed_statement_alignment_class", "")).strip()
    reviewed["reviewed_equivalence_verdict"] = str(review.get("reviewed_equivalence_verdict", "")).strip()
    reviewed["reviewed_alignment_confidence"] = float(review.get("reviewed_alignment_confidence", 0.0) or 0.0)
    reviewed["review_provenance"] = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "artifact_id": str(review.get("artifact_id", "")).strip(),
        "reviewed_by": str(review.get("reviewed_by", "")).strip(),
        "reviewed_at": str(review.get("reviewed_at", "")).strip(),
        "reviewer_role": str(review.get("reviewer_role", "")).strip(),
        "source_span_sha256": actual_span,
        "notes": str(review.get("notes", "")).strip(),
    }
    if _review_promotes_alignment(reviewed, review):
        reviewed["alignment_gold_eligible"] = True
        reviewed["alignment_tier"] = "alignment_gold"
        reviewed["alignment_review_required"] = False
        return reviewed, "applied_promoted_alignment_gold"
    return reviewed, "applied_review_only"


def _review_authority(review: dict[str, Any]) -> int:
    """Return review authority level: 3=human, 2=hybrid(non-auto), 1=auto-LLM/unknown."""
    rb = str(review.get("reviewed_by", "") or "").lower()
    if "human" in rb:
        return 3
    if "hybrid" in rb and "auto_llm" not in rb:
        return 2
    return 1


def apply_reviews(rows: list[dict[str, Any]], reviews: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped_reviews: dict[str, list[dict[str, Any]]] = {}
    for review in reviews:
        row_id = str(review.get("row_id", "")).strip()
        if row_id:
            grouped_reviews.setdefault(row_id, []).append(review)
    status_counts: Counter[str] = Counter()
    out: list[dict[str, Any]] = []
    corpus_row_ids = {str(row.get("row_id", "")) for row in rows}
    for row in rows:
        row_reviews = grouped_reviews.get(str(row.get("row_id", "")), [])
        if not row_reviews:
            out.append(row)
            continue
        unique_payloads = {json.dumps(review, sort_keys=True, ensure_ascii=True) for review in row_reviews}
        if len(row_reviews) > 1 and len(unique_payloads) > 1:
            # Conflict: if reviews differ in authority level, take the highest-authority one.
            # If they have equal authority, block (ambiguous human/hybrid conflict).
            authorities = [_review_authority(r) for r in row_reviews]
            if max(authorities) > min(authorities):
                review = max(row_reviews, key=_review_authority)
                applied, status = apply_review(row, review)
                status_counts[status] += 1
                out.append(applied)
            else:
                status_counts["duplicate_review_conflict"] += 1
                out.append(row)
            continue
        if len(row_reviews) > 1:
            status_counts["duplicate_review_identical"] += len(row_reviews) - 1
        review = row_reviews[0]
        applied, status = apply_review(row, review)
        status_counts[status] += 1
        out.append(applied)
    unused_reviews = len([review for review in reviews if str(review.get("row_id", "")) not in corpus_row_ids])
    if unused_reviews:
        status_counts["unused_review"] += unused_reviews
    summary = {
        "schema_version": "reviewed_statement_alignment_apply_summary.v1",
        "rows": len(rows),
        "reviews": len(reviews),
        "status_counts": dict(status_counts.most_common()),
        "promoted_alignment_gold": int(status_counts.get("applied_promoted_alignment_gold", 0)),
        "review_only": int(status_counts.get("applied_review_only", 0)),
        "source_span_mismatch": int(status_counts.get("source_span_mismatch", 0)),
        "invalid_reviews": sum(count for status, count in status_counts.items() if status.startswith("invalid_review:")),
        "duplicate_review_conflicts": int(status_counts.get("duplicate_review_conflict", 0)),
        "honest_scope": "Reviewed fields are parallel audit evidence; automatic fields are preserved.",
    }
    return out, summary


def export_reviewed_corpus(*, in_jsonl: Path, reviews_jsonl: Path, out_jsonl: Path, out_summary: Path) -> dict[str, Any]:
    rows = _read_jsonl(in_jsonl)
    reviews = _read_jsonl(reviews_jsonl)
    out, summary = apply_reviews(rows, reviews)
    result = {**summary, "out_jsonl": str(out_jsonl), "out_summary": str(out_summary)}
    _write_jsonl(out_jsonl, out)
    _write_json(out_summary, result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply reviewed statement-fidelity adjudications to corpus JSONL")
    parser.add_argument("--in-jsonl", required=True, type=Path)
    parser.add_argument("--reviews-jsonl", required=True, type=Path)
    parser.add_argument("--out-jsonl", required=True, type=Path)
    parser.add_argument("--out-summary", required=True, type=Path)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = export_reviewed_corpus(
        in_jsonl=args.in_jsonl,
        reviews_jsonl=args.reviews_jsonl,
        out_jsonl=args.out_jsonl,
        out_summary=args.out_summary,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result["source_span_mismatch"] or result["invalid_reviews"] or result["duplicate_review_conflicts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
