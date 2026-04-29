#!/usr/bin/env python3
"""Build review queues for unresolved corpus semantic identity and novelty evidence."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_IN_JSONL = Path("output/corpus/stable_corpus.jsonl")
DEFAULT_OUT_JSONL = Path("output/corpus/identity_review_queue.jsonl")
DEFAULT_OUT_SUMMARY = Path("output/corpus/identity_review_queue_summary.json")


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except Exception:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def identity_review_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    identity_status = str(row.get("identity_status", "") or "unknown")
    if identity_status in {"near_duplicate", "unknown"}:
        reasons.append(f"identity_status:{identity_status}")
    evidence = row.get("identity_evidence") if isinstance(row.get("identity_evidence"), dict) else {}
    if bool(evidence.get("human_review_required")):
        reasons.append("human_review_required")
    if str(row.get("novelty_status", "")) == "semantic_near_duplicate":
        reasons.append("semantic_near_duplicate")
    if str(row.get("mathlib_novelty_status", "")) == "unknown":
        reasons.append("mathlib_novelty_unknown")
    if str(row.get("novelty_status", "")) == "new_candidate":
        checks = evidence.get("mathlib_checks_run", []) if isinstance(evidence, dict) else []
        if not checks:
            reasons.append("unsupported_new_candidate")
    return list(dict.fromkeys(reasons))


def build_identity_review_queue(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    for row in rows:
        reasons = identity_review_reasons(row)
        if not reasons:
            continue
        reason_counts.update(reasons)
        evidence = row.get("identity_evidence") if isinstance(row.get("identity_evidence"), dict) else {}
        queue.append(
            {
                "schema_version": "identity_review_queue.v1",
                "row_id": row.get("row_id", ""),
                "arxiv_id": row.get("arxiv_id", ""),
                "theorem_id": row.get("theorem_id", ""),
                "canonical_theorem_id": row.get("canonical_theorem_id", ""),
                "statement_fingerprint": row.get("statement_fingerprint", ""),
                "identity_status": row.get("identity_status", "unknown"),
                "novelty_status": row.get("novelty_status", "unknown"),
                "corpus_duplicate_status": row.get("corpus_duplicate_status", "unknown"),
                "mathlib_novelty_status": row.get("mathlib_novelty_status", "unknown"),
                "review_reasons": reasons,
                "identity_evidence": evidence,
                "novelty_evidence": row.get("novelty_evidence", {}),
                "source_latex": row.get("source_latex", ""),
                "lean_statement": row.get("lean_statement", ""),
                "artifact_paths": row.get("artifact_paths", {}),
            }
        )
    queue.sort(key=lambda item: (str(item.get("identity_status", "")), str(item.get("arxiv_id", "")), str(item.get("theorem_id", ""))))
    summary = {
        "schema_version": "identity_review_queue_summary.v1",
        "rows": len(queue),
        "reason_counts": dict(reason_counts.most_common()),
        "unknown_identity_rows": int(reason_counts.get("identity_status:unknown", 0)),
        "near_duplicate_rows": int(reason_counts.get("identity_status:near_duplicate", 0)),
        "unsupported_new_candidate_rows": int(reason_counts.get("unsupported_new_candidate", 0)),
    }
    return queue, summary


def export_identity_review_queue(*, in_jsonl: Path, out_jsonl: Path, out_summary: Path) -> dict[str, Any]:
    rows = _read_rows(in_jsonl)
    queue, summary = build_identity_review_queue(rows)
    result = {**summary, "source_rows": len(rows), "out_jsonl": str(out_jsonl)}
    _write_jsonl(out_jsonl, queue)
    _write_json(out_summary, result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build semantic identity review queues from corpus JSONL")
    parser.add_argument("--in-jsonl", type=Path, default=DEFAULT_IN_JSONL)
    parser.add_argument("--out-jsonl", type=Path, default=DEFAULT_OUT_JSONL)
    parser.add_argument("--out-summary", type=Path, default=DEFAULT_OUT_SUMMARY)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = export_identity_review_queue(
        in_jsonl=args.in_jsonl,
        out_jsonl=args.out_jsonl,
        out_summary=args.out_summary,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result["unsupported_new_candidate_rows"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
