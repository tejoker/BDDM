#!/usr/bin/env python3
"""Build a statement-fidelity triage queue for non-proof-ready corpus rows."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from build_gold_proof_queue import MIN_PROOF_ALIGNMENT_CONFIDENCE, proof_candidate_blockers
from export_corpus import DEFAULT_EVIDENCE_DIR, DEFAULT_LEDGER_DIR, DEFAULT_REPORT_DIR, build_corpus_rows
from statement_validity import statement_fidelity_gate


DEFAULT_OUT_JSONL = Path("output/corpus/statement_fidelity_queue.jsonl")
DEFAULT_OUT_SUMMARY = Path("output/corpus/statement_fidelity_queue_summary.json")


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


def _placeholder_statement(row: dict[str, Any]) -> bool:
    statement = " ".join(str(row.get("lean_statement", "") or "").split())
    patterns = (
        "∃ x : ℝ, x = x",
        "x = x",
        ": True",
        "let Claim : Prop :=",
        "PaperClaim",
    )
    return any(pattern in statement for pattern in patterns)


def fidelity_review_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    gate = statement_fidelity_gate(row)
    if gate.proof_eligible:
        return []
    if gate.statement_fidelity_verdict:
        reasons.append(f"statement_fidelity:{gate.statement_fidelity_verdict}")
    reasons.extend(f"statement_fidelity_blocker:{b}" for b in gate.statement_fidelity_blockers)
    alignment_class = str(row.get("statement_alignment_class", "") or "unknown")
    if alignment_class != "exact":
        reasons.append(f"statement_alignment:{alignment_class}")
    if float(row.get("alignment_confidence", 0.0) or 0.0) < MIN_PROOF_ALIGNMENT_CONFIDENCE:
        reasons.append("alignment_confidence_below_proof_threshold")
    if str(row.get("claim_equivalence_verdict", "") or "").lower() not in {"equivalent", "exact"}:
        reasons.append("claim_equivalence_not_established")
    if str(row.get("identity_status", "") or "unknown") == "unknown":
        reasons.append("identity_unknown")
    if _placeholder_statement(row):
        reasons.append("placeholder_or_trivial_lean_statement")
    match_status = str(_source_match(row).get("match_status", "") or "missing")
    if match_status != "matched":
        reasons.append(f"source_match:{match_status}")
    if str(row.get("source_span_quality", "") or "") not in {"extractor_native", "reviewed"}:
        reasons.append(f"source_span_quality:{row.get('source_span_quality', '') or 'missing'}")
    for blocker in proof_candidate_blockers(row):
        if blocker in {
            "alignment_not_exact_or_reviewed_exact",
            "statement_alignment_not_exact",
            "alignment_confidence_below_proof_threshold",
            "placeholder_or_trivial_lean_statement",
        }:
            reasons.append(f"proof_queue_blocker:{blocker}")
    return list(dict.fromkeys(reasons))


def build_statement_fidelity_queue(rows: list[dict[str, Any]], *, limit: int = 500) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    for row in rows:
        reasons = fidelity_review_reasons(row)
        if not reasons:
            continue
        reason_counts.update(reasons)
        queue.append(
            {
                "schema_version": "statement_fidelity_queue.v1",
                "row_id": row.get("row_id", ""),
                "arxiv_id": row.get("arxiv_id", ""),
                "theorem_id": row.get("theorem_id", ""),
                "canonical_theorem_id": row.get("canonical_theorem_id", ""),
                "review_reasons": reasons,
                "statement_alignment_class": row.get("statement_alignment_class", ""),
                "alignment_confidence": row.get("alignment_confidence", 0.0),
                "alignment_tier": row.get("alignment_tier", ""),
                "alignment_gold_eligible": bool(row.get("alignment_gold_eligible")),
                "claim_equivalence_verdict": row.get("claim_equivalence_verdict", ""),
                "identity_status": row.get("identity_status", ""),
                "source_span_quality": row.get("source_span_quality", ""),
                "source_span": row.get("source_span", {}),
                "source_match": _source_match(row),
                "source_latex": row.get("source_latex", ""),
                "normalized_text": row.get("normalized_text", ""),
                "lean_statement": row.get("lean_statement", ""),
                "validation_gates": row.get("validation_gates", {}),
                "gate_failures": row.get("gate_failures", []),
                "alignment_evidence": row.get("alignment_evidence", {}),
                "artifact_paths": row.get("artifact_paths", {}),
                "suggested_action": "review_statement_equivalence_before_proof_search",
            }
        )
    queue.sort(
        key=lambda item: (
            0 if "placeholder_or_trivial_lean_statement" in item["review_reasons"] else 1,
            float(item.get("alignment_confidence", 0.0) or 0.0),
            str(item.get("arxiv_id", "")),
            str(item.get("theorem_id", "")),
        )
    )
    queue = queue[:limit]
    summary = {
        "schema_version": "statement_fidelity_queue_summary.v1",
        "rows": len(queue),
        "reason_counts": dict(reason_counts.most_common()),
        "partial_rows": int(reason_counts.get("statement_alignment:partial", 0)),
        "low_confidence_rows": int(reason_counts.get("alignment_confidence_below_proof_threshold", 0)),
        "placeholder_rows": int(reason_counts.get("placeholder_or_trivial_lean_statement", 0)),
        "honest_scope": "Statement-fidelity triage only; rows here are not proof-growth candidates until exact or reviewed-exact.",
    }
    return queue, summary


def export_statement_fidelity_queue(
    *,
    project_root: Path,
    ledger_paths: list[Path],
    report_roots: list[Path],
    evidence_roots: list[Path],
    out_jsonl: Path,
    out_summary: Path,
    limit: int = 500,
) -> dict[str, Any]:
    rows, corpus_summary = build_corpus_rows(
        ledger_paths=ledger_paths,
        project_root=project_root,
        report_roots=report_roots,
        evidence_roots=evidence_roots,
    )
    queue, summary = build_statement_fidelity_queue(rows, limit=limit)
    result = {**summary, "source_corpus_rows": corpus_summary.get("rows", 0), "out_jsonl": str(out_jsonl)}
    _write_jsonl(out_jsonl, queue)
    _write_json(out_summary, result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build statement-fidelity review queue")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--ledger-path", action="append", type=Path, default=[])
    parser.add_argument("--report-root", action="append", type=Path, default=[])
    parser.add_argument("--evidence-root", action="append", type=Path, default=[])
    parser.add_argument("--out-jsonl", type=Path, default=DEFAULT_OUT_JSONL)
    parser.add_argument("--out-summary", type=Path, default=DEFAULT_OUT_SUMMARY)
    parser.add_argument("--limit", type=int, default=500)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = export_statement_fidelity_queue(
        project_root=args.project_root,
        ledger_paths=args.ledger_path or [DEFAULT_LEDGER_DIR],
        report_roots=args.report_root or [DEFAULT_REPORT_DIR],
        evidence_roots=args.evidence_root or [DEFAULT_EVIDENCE_DIR],
        out_jsonl=args.out_jsonl,
        out_summary=args.out_summary,
        limit=args.limit,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
