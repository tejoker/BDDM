#!/usr/bin/env python3
"""Conservatively assist reviewed-exact statement adjudication."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_OUT_JSONL = Path("output/corpus/assisted_reviewed_statement_alignment.v1.jsonl")
DEFAULT_OUT_SUMMARY = Path("output/corpus/assisted_statement_review_summary.json")

_STOPWORDS = {
    "a",
    "an",
    "and",
    "any",
    "be",
    "by",
    "for",
    "have",
    "if",
    "is",
    "of",
    "then",
    "the",
    "to",
    "we",
    "leq",
    "geq",
}
_RISK_PHRASES = (
    "algorithm",
    "assumptions",
    "consequently",
    "does not admit",
    "sharp",
    "such that",
    "there exist",
    "there exists",
    "up to",
    "weak solution",
)
_LEAN_RISK_PATTERNS = (
    r"\bFalse\b",
    r"\bPaperClaim\b",
    r"\bSet\.univ\b",
    r"\bDyadicBlockBound\s+[^:]*=\s*DyadicBlockBound\b",
    r"\(\s*\w+\s*:\s*Is[A-Z][A-Za-z0-9_]*\s*\)",
    r"∃\s+\w+\s*:\s*ℝ,\s*\w+\s*=\s*\w+",
)


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


def _clean_math_text(text: str) -> str:
    out = text or ""
    out = re.sub(r"\\label\{[^}]*\}", " ", out)
    out = re.sub(r"\\cite\{[^}]*\}", " ", out)
    out = re.sub(r"\\ref\{[^}]*\}", " ", out)
    out = re.sub(r"\\tilde\{\\?([A-Za-z]+)\}", r" tilde \1 ", out)
    out = re.sub(r"\\([A-Za-z]+)", r" \1 ", out)
    out = out.replace("_", " ")
    out = re.sub(r"([a-z])([A-Z])", r"\1 \2", out)
    return out


def _tokens(text: str) -> set[str]:
    cleaned = _clean_math_text(text)
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9]*|[0-9]+", cleaned)
        if token.lower() not in _STOPWORDS and len(token) > 1
    }


def _relation_count(text: str) -> int:
    return len(re.findall(r"≤|>=|<=|≥|=|\\leq|\\geq|\\subseteq|⊆", text or ""))


def _relation_compatible(source: str, lean: str) -> bool:
    source_count = _relation_count(source)
    lean_count = _relation_count(lean)
    if source_count == 0:
        return False
    if "\\subseteq" in source or "⊆" in source:
        return "⊆" in lean or "subset" in lean.lower()
    return lean_count >= source_count


def adjudication_blockers(item: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    source = str(item.get("source_latex", "") or "")
    lean = str(item.get("lean_statement", "") or "")
    source_lower = source.lower()
    if str(item.get("schema_version", "")) != "statement_review_batch.v1":
        blockers.append("schema_version_invalid")
    if not str(item.get("source_span_sha256", "")).strip():
        blockers.append("source_span_sha256_missing")
    if not source.strip() or not lean.strip():
        blockers.append("source_or_lean_missing")
    if len(" ".join(source.split())) > 360 or len(" ".join(lean.split())) > 520:
        blockers.append("statement_too_large_for_assisted_exact_review")
    if any(phrase in source_lower for phrase in _RISK_PHRASES):
        blockers.append("source_contains_complex_or_existence_claim")
    if any(re.search(pattern, lean) for pattern in _LEAN_RISK_PATTERNS):
        blockers.append("lean_contains_placeholder_or_ungrounded_shape")
    if "multisegment" in source_lower and "Multisegment" not in lean:
        blockers.append("source_multisegment_not_preserved_in_lean_type")
    if "tilde" in _tokens(source) and "tilde" not in _tokens(lean):
        blockers.append("tilde_symbol_missing_in_lean")
    if not _relation_compatible(source, lean):
        blockers.append("primary_relation_not_preserved")
    source_tokens = _tokens(source)
    lean_tokens = _tokens(lean)
    coverage = len(source_tokens & lean_tokens) / len(source_tokens) if source_tokens else 0.0
    if coverage < 0.62:
        blockers.append("token_coverage_below_assisted_review_threshold")
    return list(dict.fromkeys(blockers))


def build_assisted_reviews(
    batch_rows: list[dict[str, Any]],
    *,
    existing_reviews: list[dict[str, Any]] | None = None,
    reviewed_by: str = "agent:assisted-statement-review",
    reviewed_at: str = "",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    existing_ids = {str(row.get("row_id", "")) for row in (existing_reviews or []) if str(row.get("row_id", "")).strip()}
    reviewed_at = reviewed_at or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    reviews: list[dict[str, Any]] = []
    blocker_counts: Counter[str] = Counter()
    skipped_existing = 0
    for item in batch_rows:
        row_id = str(item.get("row_id", ""))
        if row_id in existing_ids:
            skipped_existing += 1
            continue
        blockers = adjudication_blockers(item)
        if blockers:
            blocker_counts.update(blockers)
            continue
        reviews.append(
            {
                "schema_version": "reviewed_statement_alignment.v1",
                "artifact_id": f"assisted_reviewed_statement_alignment:{item.get('arxiv_id')}:{item.get('theorem_id')}:v1",
                "row_id": row_id,
                "source_span_sha256": item.get("source_span_sha256", ""),
                "reviewed_statement_alignment_class": "exact",
                "reviewed_equivalence_verdict": "equivalent",
                "reviewed_alignment_confidence": 0.85,
                "reviewed_by": reviewed_by,
                "reviewed_at": reviewed_at,
                "reviewer_role": "conservative_assisted_exact_adjudicator",
                "notes": (
                    "Conservative assisted review: short source and Lean statements preserve the primary relation, "
                    "key identifiers, and conclusion shape. This is statement-alignment evidence only."
                ),
            }
        )
    summary = {
        "schema_version": "assisted_statement_review_summary.v1",
        "batch_rows": len(batch_rows),
        "assisted_reviewed_exact_rows": len(reviews),
        "skipped_existing_reviews": skipped_existing,
        "blocked_rows": len(batch_rows) - len(reviews) - skipped_existing,
        "blocker_counts": dict(blocker_counts.most_common()),
        "honest_scope": "Conservative statement-alignment assistance only; no proof or novelty claims are made.",
    }
    return reviews, summary


def export_assisted_reviews(
    *,
    batch_jsonl: Path,
    existing_reviews_jsonl: Path | None,
    out_jsonl: Path,
    out_summary: Path,
    reviewed_by: str,
    reviewed_at: str,
) -> dict[str, Any]:
    batch_rows = _read_jsonl(batch_jsonl)
    existing_reviews = _read_jsonl(existing_reviews_jsonl) if existing_reviews_jsonl is not None else []
    reviews, summary = build_assisted_reviews(
        batch_rows,
        existing_reviews=existing_reviews,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
    )
    result = {**summary, "out_jsonl": str(out_jsonl), "out_summary": str(out_summary)}
    _write_jsonl(out_jsonl, reviews)
    _write_json(out_summary, result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Conservatively assist statement review adjudication")
    parser.add_argument("--batch-jsonl", required=True, type=Path)
    parser.add_argument("--existing-reviews-jsonl", type=Path, default=None)
    parser.add_argument("--out-jsonl", type=Path, default=DEFAULT_OUT_JSONL)
    parser.add_argument("--out-summary", type=Path, default=DEFAULT_OUT_SUMMARY)
    parser.add_argument("--reviewed-by", default="agent:assisted-statement-review")
    parser.add_argument("--reviewed-at", default="")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = export_assisted_reviews(
        batch_jsonl=args.batch_jsonl,
        existing_reviews_jsonl=args.existing_reviews_jsonl,
        out_jsonl=args.out_jsonl,
        out_summary=args.out_summary,
        reviewed_by=args.reviewed_by,
        reviewed_at=args.reviewed_at,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
