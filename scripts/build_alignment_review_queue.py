#!/usr/bin/env python3
"""Build a review queue for DESol rows with weak or ambiguous source alignment."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from export_corpus import DEFAULT_EVIDENCE_DIR, DEFAULT_LEDGER_DIR, DEFAULT_REPORT_DIR, build_corpus_rows


DEFAULT_OUT_JSONL = Path("output/corpus/alignment_review_queue.jsonl")
DEFAULT_OUT_SUMMARY = Path("output/corpus/alignment_review_queue_summary.json")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def _source_match(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("alignment_evidence") if isinstance(row.get("alignment_evidence"), dict) else {}
    source_match = evidence.get("source_match") if isinstance(evidence.get("source_match"), dict) else {}
    return source_match


def alignment_review_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    source_span_quality = str(row.get("source_span_quality", "") or "missing")
    if source_span_quality in {"string_recovered", "missing", "ambiguous", "unknown"}:
        reasons.append(f"source_span_quality:{source_span_quality}")
    match_status = str(_source_match(row).get("match_status", "") or "missing")
    if match_status in {"ambiguous", "missing"}:
        reasons.append(f"source_match:{match_status}")
    if bool(row.get("alignment_review_required")):
        reasons.append("alignment_review_required")
    if str(row.get("alignment_tier", "")) == "alignment_gold":
        return []
    if str(row.get("statement_alignment_class", "")) in {"exact", "weaker", "stronger"} and reasons:
        reasons.append("high_value_alignment_candidate")
    return list(dict.fromkeys(reasons))


def build_alignment_review_queue(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    for row in rows:
        reasons = alignment_review_reasons(row)
        if not reasons:
            continue
        reason_counts.update(reasons)
        queue.append(
            {
                "schema_version": "alignment_review_queue.v1",
                "row_id": row.get("row_id", ""),
                "arxiv_id": row.get("arxiv_id", ""),
                "theorem_id": row.get("theorem_id", ""),
                "canonical_theorem_id": row.get("canonical_theorem_id", ""),
                "review_reasons": reasons,
                "statement_alignment_class": row.get("statement_alignment_class", ""),
                "alignment_confidence": row.get("alignment_confidence", 0.0),
                "alignment_tier": row.get("alignment_tier", ""),
                "source_span_quality": row.get("source_span_quality", ""),
                "source_span": row.get("source_span", {}),
                "source_match": _source_match(row),
                "source_latex": row.get("source_latex", ""),
                "normalized_text": row.get("normalized_text", ""),
                "lean_statement": row.get("lean_statement", ""),
                "status": row.get("status", ""),
                "dataset_tier": row.get("dataset_tier", ""),
                "artifact_paths": row.get("artifact_paths", {}),
            }
        )
    queue.sort(
        key=lambda item: (
            0 if item["dataset_tier"] == "gold_proof" else 1,
            str(item["arxiv_id"]),
            str(item["theorem_id"]),
        )
    )
    summary = {
        "schema_version": "alignment_review_queue_summary.v1",
        "rows": len(queue),
        "reason_counts": dict(reason_counts.most_common()),
        "gold_proof_rows": sum(1 for row in queue if row.get("dataset_tier") == "gold_proof"),
        "string_recovered_rows": int(reason_counts.get("source_span_quality:string_recovered", 0)),
        "ambiguous_match_rows": int(reason_counts.get("source_match:ambiguous", 0)),
        "missing_match_rows": int(reason_counts.get("source_match:missing", 0)),
    }
    return queue, summary


def export_alignment_review_queue(
    *,
    project_root: Path,
    ledger_paths: list[Path],
    report_roots: list[Path],
    evidence_roots: list[Path],
    out_jsonl: Path,
    out_summary: Path,
) -> dict[str, Any]:
    rows, corpus_summary = build_corpus_rows(
        ledger_paths=ledger_paths,
        project_root=project_root,
        report_roots=report_roots,
        evidence_roots=evidence_roots,
    )
    queue, summary = build_alignment_review_queue(rows)
    result = {**summary, "source_corpus_rows": corpus_summary.get("rows", 0), "out_jsonl": str(out_jsonl)}
    _write_jsonl(out_jsonl, queue)
    _write_json(out_summary, result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a source-alignment review queue")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--ledger-path", action="append", type=Path, default=[])
    parser.add_argument("--report-root", action="append", type=Path, default=[])
    parser.add_argument("--evidence-root", action="append", type=Path, default=[])
    parser.add_argument("--out-jsonl", type=Path, default=DEFAULT_OUT_JSONL)
    parser.add_argument("--out-summary", type=Path, default=DEFAULT_OUT_SUMMARY)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = export_alignment_review_queue(
        project_root=args.project_root,
        ledger_paths=args.ledger_path or [DEFAULT_LEDGER_DIR],
        report_roots=args.report_root or [DEFAULT_REPORT_DIR],
        evidence_roots=args.evidence_root or [DEFAULT_EVIDENCE_DIR],
        out_jsonl=args.out_jsonl,
        out_summary=args.out_summary,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
