#!/usr/bin/env python3
"""Build a queue for rows that need statement repair before exact review."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from build_statement_fidelity_queue import fidelity_review_reasons
from build_statement_review_batch import review_batch_exclusion_reasons
from export_corpus import DEFAULT_EVIDENCE_DIR, DEFAULT_LEDGER_DIR, DEFAULT_REPORT_DIR, build_corpus_rows
from statement_validity import classify_statement, statement_fidelity_gate


DEFAULT_OUT_JSONL = Path("output/corpus/statement_repair_queue.jsonl")
DEFAULT_OUT_SUMMARY = Path("output/corpus/statement_repair_queue_summary.json")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


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


def _primary_repair_kind(reasons: list[str]) -> str:
    if "placeholder_or_trivial_lean_statement" in reasons:
        return "replace_placeholder_statement"
    if any(reason.startswith("status_needs_statement_repair:FLAWED") for reason in reasons):
        return "regenerate_flawed_statement"
    if any(reason.startswith("status_needs_statement_repair:TRANSLATION_LIMITED") for reason in reasons):
        return "recover_translation_limited_statement"
    if "source_latex_missing" in reasons:
        return "recover_source_latex"
    if "source_match_not_unique" in reasons:
        return "adjudicate_source_match"
    if "source_span_not_review_grade" in reasons:
        return "repair_source_span_provenance"
    if "statement_fidelity:repair_candidate" in reasons:
        return "repair_candidate_from_fidelity_gate"
    return "statement_repair_review"


def _priority_score(row: dict[str, Any], reasons: list[str]) -> int:
    score = 0
    if "placeholder_or_trivial_lean_statement" in reasons:
        score += 40
    if any(reason.startswith("status_needs_statement_repair:FLAWED") for reason in reasons):
        score += 30
    if str(row.get("source_span_quality", "")) == "extractor_native":
        score += 10
    if str(row.get("status", "")) in {"UNRESOLVED", "FLAWED"}:
        score += 10
    score += max(0, 10 - len(str(row.get("lean_statement", ""))) // 200)
    return score


def _repair_route(row: dict[str, Any], repair_kind: str) -> str:
    if repair_kind == "repair_candidate_from_fidelity_gate":
        return "statement_regeneration"
    if repair_kind == "replace_placeholder_statement":
        return "statement_regeneration"
    if repair_kind == "regenerate_flawed_statement":
        return "statement_regeneration"
    if repair_kind == "recover_translation_limited_statement":
        return "source_translation_recovery"
    if repair_kind == "recover_source_latex":
        return "source_alignment_recovery"
    if repair_kind == "adjudicate_source_match":
        return "source_alignment_review"
    if repair_kind == "repair_source_span_provenance":
        return "source_span_repair"
    validity = classify_statement(row)
    if validity.primary_blocker == "paper_theory_debt":
        return "definition_or_theory_grounding"
    if validity.primary_blocker == "proof_search_failure":
        return "proof_search_after_review"
    return "manual_statement_repair"


def build_statement_repair_queue(rows: list[dict[str, Any]], *, limit: int = 500) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    repair_kind_counts: Counter[str] = Counter()
    for row in rows:
        fidelity_reasons = fidelity_review_reasons(row)
        if not fidelity_reasons:
            continue
        gate = statement_fidelity_gate(row)
        gate_reasons = []
        if gate.statement_fidelity_verdict == "repair_candidate":
            gate_reasons.append("statement_fidelity:repair_candidate")
            gate_reasons.extend(f"statement_fidelity_blocker:{b}" for b in gate.statement_fidelity_blockers)
        reasons = list(dict.fromkeys([*review_batch_exclusion_reasons(row), *gate_reasons]))
        if not reasons:
            continue
        reason_counts.update(reasons)
        repair_kind = _primary_repair_kind(reasons)
        validity = classify_statement(row)
        repair_route = _repair_route(row, repair_kind)
        repair_kind_counts[repair_kind] += 1
        queue.append(
            {
                "schema_version": "statement_repair_queue.v1",
                "row_id": row.get("row_id", ""),
                "arxiv_id": row.get("arxiv_id", ""),
                "theorem_id": row.get("theorem_id", ""),
                "canonical_theorem_id": row.get("canonical_theorem_id", ""),
                "priority_score": _priority_score(row, reasons),
                "repair_kind": repair_kind,
                "repair_route": repair_route,
                "repair_reasons": reasons,
                "validity_primary_blocker": validity.primary_blocker,
                "validity_reasons": validity.reasons,
                "validity_next_action": validity.next_action,
                "debt_tier": validity.debt_tier,
                "proof_value": validity.proof_value,
                "status": row.get("status", ""),
                "statement_alignment_class": row.get("statement_alignment_class", ""),
                "alignment_confidence": row.get("alignment_confidence", 0.0),
                "alignment_evidence": row.get("alignment_evidence", {}),
                "source_span_quality": row.get("source_span_quality", ""),
                "source_span": row.get("source_span", {}),
                "source_latex": row.get("source_latex", ""),
                "normalized_text": row.get("normalized_text", ""),
                "lean_statement": row.get("lean_statement", ""),
                "validation_gates": row.get("validation_gates", {}),
                "gate_failures": row.get("gate_failures", []),
                "axiom_debt": row.get("axiom_debt", []),
                "artifact_paths": row.get("artifact_paths", {}),
                "suggested_action": "repair_statement_then_rebuild_statement_review_batch",
            }
        )
    queue.sort(key=lambda item: (-int(item["priority_score"]), str(item["arxiv_id"]), str(item["theorem_id"])))
    queue = queue[:limit]
    summary = {
        "schema_version": "statement_repair_queue_summary.v1",
        "rows": len(queue),
        "reason_counts": dict(reason_counts.most_common()),
        "repair_kind_counts": dict(repair_kind_counts.most_common()),
        "validity_blocker_counts": dict(Counter(str(item.get("validity_primary_blocker", "")) for item in queue).most_common()),
        "repair_route_counts": dict(Counter(str(item.get("repair_route", "")) for item in queue).most_common()),
        "honest_scope": "Rows here need statement/source repair before they can be reviewed exact or used for proof growth.",
    }
    return queue, summary


def export_statement_repair_queue(
    *,
    project_root: Path,
    ledger_paths: list[Path],
    report_roots: list[Path],
    evidence_roots: list[Path],
    input_jsonl: Path | None,
    out_jsonl: Path,
    out_summary: Path,
    limit: int = 500,
) -> dict[str, Any]:
    if input_jsonl is not None:
        rows = _read_jsonl(input_jsonl)
        source_rows = len(rows)
    else:
        rows, corpus_summary = build_corpus_rows(
            ledger_paths=ledger_paths,
            project_root=project_root,
            report_roots=report_roots,
            evidence_roots=evidence_roots,
        )
        source_rows = int(corpus_summary.get("rows", len(rows)))
    queue, summary = build_statement_repair_queue(rows, limit=limit)
    result = {**summary, "source_corpus_rows": source_rows, "out_jsonl": str(out_jsonl)}
    _write_jsonl(out_jsonl, queue)
    _write_json(out_summary, result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build statement repair queue")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--ledger-path", action="append", type=Path, default=[])
    parser.add_argument("--report-root", action="append", type=Path, default=[])
    parser.add_argument("--evidence-root", action="append", type=Path, default=[])
    parser.add_argument("--input-jsonl", type=Path, default=None)
    parser.add_argument("--out-jsonl", type=Path, default=DEFAULT_OUT_JSONL)
    parser.add_argument("--out-summary", type=Path, default=DEFAULT_OUT_SUMMARY)
    parser.add_argument("--limit", type=int, default=500)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = export_statement_repair_queue(
        project_root=args.project_root,
        ledger_paths=args.ledger_path or [DEFAULT_LEDGER_DIR],
        report_roots=args.report_root or [DEFAULT_REPORT_DIR],
        evidence_roots=args.evidence_root or [DEFAULT_EVIDENCE_DIR],
        input_jsonl=args.input_jsonl,
        out_jsonl=args.out_jsonl,
        out_summary=args.out_summary,
        limit=args.limit,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
