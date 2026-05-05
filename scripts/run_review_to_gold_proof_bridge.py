#!/usr/bin/env python3
"""Conservative bridge from statement review batch to strict gold proof queue."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from apply_statement_fidelity_reviews import apply_reviews
from assist_statement_review_adjudication import build_assisted_reviews
from build_gold_proof_queue import build_gold_proof_queue


DEFAULT_BATCH_JSONL = Path("output/corpus/statement_review_batch.jsonl")
DEFAULT_CORPUS_JSONL = Path("output/corpus/stable_corpus.jsonl")
DEFAULT_EXISTING_REVIEWS_JSONL = Path("reproducibility/statement_reviews/reviewed_statement_alignment.jsonl")
DEFAULT_AUTO_REVIEWS_JSONL = Path("output/corpus/auto_alignment_reviews.jsonl")
DEFAULT_OUT_REVIEWS_JSONL = Path("output/corpus/assisted_reviewed_statement_alignment.v1.jsonl")
DEFAULT_OUT_REVIEWED_CORPUS_JSONL = Path("output/corpus/reviewed_statement_corpus.jsonl")
DEFAULT_OUT_GOLD_JSONL = Path("output/corpus/gold_proof_growth_queue.jsonl")
DEFAULT_OUT_SUMMARY = Path("output/corpus/review_to_gold_proof_bridge_summary.json")


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


def _is_release_eligible_review(review: dict[str, Any]) -> bool:
    """Return True for reviews from human or non-auto hybrid sources.

    Auto-LLM reviews (reviewed_by contains "auto_llm") are NOT release-eligible;
    they provide alignment evidence but require a separate bridge-generated review
    to pass the proof-eligibility gate. This prevents double-skipping: rows with
    only auto-LLM reviews still get a bridge-generated hybrid review.
    """
    rb = str(review.get("reviewed_by", "") or "").lower()
    return ("hybrid" in rb and "auto_llm" not in rb) or "human" in rb


def run_review_to_gold_bridge(
    *,
    batch_rows: list[dict[str, Any]],
    corpus_rows: list[dict[str, Any]],
    existing_reviews: list[dict[str, Any]] | None = None,
    additional_reviews: list[dict[str, Any]] | None = None,
    reviewed_by: str = "hybrid:conservative-assisted-review",
    reviewed_at: str = "",
    gold_limit: int = 200,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Return assisted reviews, reviewed corpus, gold queue, and honest summary."""
    trusted_input_reviews = [*(existing_reviews or []), *(additional_reviews or [])]
    # Only count release-eligible reviews as "already reviewed" when deciding whether
    # to generate a new assisted review. Auto-LLM-only rows still get a bridge review.
    release_reviews = [r for r in trusted_input_reviews if _is_release_eligible_review(r)]
    assisted_reviews, assisted_summary = build_assisted_reviews(
        batch_rows,
        existing_reviews=release_reviews,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
    )
    combined_reviews = [*trusted_input_reviews, *assisted_reviews]
    reviewed_rows, apply_summary = apply_reviews(corpus_rows, combined_reviews)
    gold_queue, gold_summary = build_gold_proof_queue(reviewed_rows, limit=gold_limit)
    summary = {
        "schema_version": "review_to_gold_proof_bridge_summary.v1",
        "batch_rows": len(batch_rows),
        "corpus_rows": len(corpus_rows),
        "existing_reviews": len(existing_reviews or []),
        "additional_reviews": len(additional_reviews or []),
        "combined_reviews_used": len(combined_reviews),
        "assisted_reviewed_exact_rows": len(assisted_reviews),
        "promoted_alignment_gold": int(apply_summary.get("promoted_alignment_gold", 0) or 0),
        "gold_proof_queue_rows": len(gold_queue),
        "assisted_summary": assisted_summary,
        "apply_summary": apply_summary,
        "gold_summary": gold_summary,
        "honest_scope": (
            "Conservative reviewed-exact bridge only. Gold queue rows are proof-search candidates, "
            "not proof closure."
        ),
    }
    return assisted_reviews, reviewed_rows, gold_queue, summary


def export_review_to_gold_bridge(
    *,
    batch_jsonl: Path,
    corpus_jsonl: Path,
    existing_reviews_jsonl: Path | None,
    additional_reviews_jsonl: list[Path],
    out_reviews_jsonl: Path,
    out_reviewed_corpus_jsonl: Path,
    out_gold_jsonl: Path,
    out_summary: Path,
    reviewed_by: str,
    reviewed_at: str,
    gold_limit: int,
) -> dict[str, Any]:
    batch_rows = _read_jsonl(batch_jsonl)
    corpus_rows = _read_jsonl(corpus_jsonl)
    existing_reviews = _read_jsonl(existing_reviews_jsonl) if existing_reviews_jsonl and existing_reviews_jsonl.exists() else []
    additional_reviews: list[dict[str, Any]] = []
    for path in additional_reviews_jsonl:
        if path.exists():
            additional_reviews.extend(_read_jsonl(path))
    reviews, reviewed_rows, gold_queue, summary = run_review_to_gold_bridge(
        batch_rows=batch_rows,
        corpus_rows=corpus_rows,
        existing_reviews=existing_reviews,
        additional_reviews=additional_reviews,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        gold_limit=gold_limit,
    )
    _write_jsonl(out_reviews_jsonl, reviews)
    _write_jsonl(out_reviewed_corpus_jsonl, reviewed_rows)
    _write_jsonl(out_gold_jsonl, gold_queue)
    result = {
        **summary,
        "out_reviews_jsonl": str(out_reviews_jsonl),
        "out_reviewed_corpus_jsonl": str(out_reviewed_corpus_jsonl),
        "out_gold_jsonl": str(out_gold_jsonl),
        "out_summary": str(out_summary),
    }
    _write_json(out_summary, result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bridge conservative statement reviews into strict gold proof queue")
    parser.add_argument("--batch-jsonl", type=Path, default=DEFAULT_BATCH_JSONL)
    parser.add_argument("--corpus-jsonl", type=Path, default=DEFAULT_CORPUS_JSONL)
    parser.add_argument("--existing-reviews-jsonl", type=Path, default=DEFAULT_EXISTING_REVIEWS_JSONL)
    parser.add_argument(
        "--additional-reviews-jsonl",
        action="append",
        type=Path,
        default=[DEFAULT_AUTO_REVIEWS_JSONL],
        help="Additional reviewed_statement_alignment.v1 files to apply before assisted review; defaults to auto alignment reviews.",
    )
    parser.add_argument("--out-reviews-jsonl", type=Path, default=DEFAULT_OUT_REVIEWS_JSONL)
    parser.add_argument("--out-reviewed-corpus-jsonl", type=Path, default=DEFAULT_OUT_REVIEWED_CORPUS_JSONL)
    parser.add_argument("--out-gold-jsonl", type=Path, default=DEFAULT_OUT_GOLD_JSONL)
    parser.add_argument("--out-summary", type=Path, default=DEFAULT_OUT_SUMMARY)
    parser.add_argument("--reviewed-by", default="hybrid:conservative-assisted-review")
    parser.add_argument("--reviewed-at", default="")
    parser.add_argument("--gold-limit", type=int, default=200)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = export_review_to_gold_bridge(
        batch_jsonl=args.batch_jsonl,
        corpus_jsonl=args.corpus_jsonl,
        existing_reviews_jsonl=args.existing_reviews_jsonl,
        additional_reviews_jsonl=args.additional_reviews_jsonl or [],
        out_reviews_jsonl=args.out_reviews_jsonl,
        out_reviewed_corpus_jsonl=args.out_reviewed_corpus_jsonl,
        out_gold_jsonl=args.out_gold_jsonl,
        out_summary=args.out_summary,
        reviewed_by=args.reviewed_by,
        reviewed_at=args.reviewed_at,
        gold_limit=args.gold_limit,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
