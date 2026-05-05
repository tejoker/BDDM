#!/usr/bin/env python3
"""Build span-bound reviewed-exact statement adjudication batches."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from build_statement_fidelity_queue import fidelity_review_reasons
from export_corpus import DEFAULT_EVIDENCE_DIR, DEFAULT_LEDGER_DIR, DEFAULT_REPORT_DIR, build_corpus_rows
from statement_validity import false_target_reason


DEFAULT_OUT_JSONL = Path("output/corpus/statement_review_batch.jsonl")
DEFAULT_OUT_TEMPLATE = Path("output/corpus/reviewed_statement_alignment_template.jsonl")
DEFAULT_OUT_SUMMARY = Path("output/corpus/statement_review_batch_summary.json")


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


def source_span_sha256(row: dict[str, Any]) -> str:
    span = row.get("source_span") if isinstance(row.get("source_span"), dict) else {}
    return hashlib.sha256(json.dumps(span, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()


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
    if "p_c1 : Prop" in statement and "h_c1 : p_c1" in statement:
        return True
    return any(pattern in statement for pattern in patterns)


def _raw_latex_statement(row: dict[str, Any]) -> bool:
    statement = str(row.get("lean_statement", "") or "")
    return bool(
        "\\" in statement
        or "$" in statement
        or any(token in statement for token in ("\\frac", "\\sum", "\\int", "\\mathbb", "\\operatorname"))
    )


def review_batch_exclusion_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if bool(row.get("alignment_gold_eligible")):
        reasons.append("already_alignment_gold")
    if str(row.get("source_span_quality", "")) not in {"extractor_native", "reviewed"}:
        reasons.append("source_span_not_review_grade")
    if str(_source_match(row).get("match_status", "") or "missing") != "matched":
        reasons.append("source_match_not_unique")
    if not str(row.get("source_latex", "")).strip():
        reasons.append("source_latex_missing")
    if not str(row.get("lean_statement", "")).strip():
        reasons.append("lean_statement_missing")
    false_target = false_target_reason(row)
    if false_target:
        reasons.append(false_target)
    if str(row.get("status", "")) in {"FLAWED", "TRANSLATION_LIMITED"}:
        reasons.append(f"status_needs_statement_repair:{row.get('status')}")
    if _placeholder_statement(row):
        reasons.append("placeholder_or_trivial_lean_statement")
    if _raw_latex_statement(row):
        reasons.append("raw_latex_lean_statement")
    return list(dict.fromkeys(reasons))


def _priority_score(row: dict[str, Any]) -> int:
    score = 0
    if str(row.get("statement_alignment_class", "")) == "partial":
        score += 30
    score += int(float(row.get("alignment_confidence", 0.0) or 0.0) * 20)
    if str(row.get("status", "")) in {"UNRESOLVED", "INTERMEDIARY_PROVEN"}:
        score += 20
    if str(row.get("identity_status", "")) in {"same_statement", "near_duplicate"}:
        score += 10
    if str(row.get("claim_equivalence_verdict", "")).lower() in {"equivalent", "exact"}:
        score += 20
    if str(row.get("source_span_quality", "")) == "reviewed":
        score += 5
    return score


def _review_checklist() -> list[str]:
    return [
        "Confirm source_span_sha256 still matches the corpus row.",
        "Compare source_latex/normalized_text against lean_statement, including hypotheses and conclusion.",
        "Use reviewed_statement_alignment_class=exact only when the Lean statement is mathematically equivalent to the source claim, not merely related or weaker.",
        "Use reviewed_equivalence_verdict=equivalent only when no paper assumption is dropped, strengthened, or silently replaced by a placeholder.",
        "Do not promote rows with placeholder/trivial Lean targets or missing source context.",
    ]


def build_statement_review_batch(rows: list[dict[str, Any]], *, limit: int = 100) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    batch: list[dict[str, Any]] = []
    templates: list[dict[str, Any]] = []
    exclusion_counts: Counter[str] = Counter()
    considered = 0
    for row in rows:
        if not fidelity_review_reasons(row):
            continue
        considered += 1
        exclusions = review_batch_exclusion_reasons(row)
        if exclusions:
            exclusion_counts.update(exclusions)
            continue
        span_hash = source_span_sha256(row)
        item = {
            "schema_version": "statement_review_batch.v1",
            "row_id": row.get("row_id", ""),
            "arxiv_id": row.get("arxiv_id", ""),
            "theorem_id": row.get("theorem_id", ""),
            "canonical_theorem_id": row.get("canonical_theorem_id", ""),
            "priority_score": _priority_score(row),
            "source_span_sha256": span_hash,
            "source_span": row.get("source_span", {}),
            "source_latex": row.get("source_latex", ""),
            "normalized_text": row.get("normalized_text", ""),
            "lean_statement": row.get("lean_statement", ""),
            "current_statement_alignment_class": row.get("statement_alignment_class", ""),
            "current_alignment_confidence": row.get("alignment_confidence", 0.0),
            "claim_equivalence_verdict": row.get("claim_equivalence_verdict", ""),
            "identity_status": row.get("identity_status", ""),
            "fidelity_review_reasons": fidelity_review_reasons(row),
            "alignment_evidence": row.get("alignment_evidence", {}),
            "validation_gates": row.get("validation_gates", {}),
            "artifact_paths": row.get("artifact_paths", {}),
            "review_checklist": _review_checklist(),
        }
        batch.append(item)
        templates.append(
            {
                "schema_version": "reviewed_statement_alignment.v1",
                "row_id": row.get("row_id", ""),
                "source_span_sha256": span_hash,
                "reviewed_statement_alignment_class": "",
                "reviewed_equivalence_verdict": "",
                "reviewed_alignment_confidence": 0.0,
                "reviewed_by": "",
                "reviewed_at": "",
                "notes": "",
            }
        )
    batch.sort(key=lambda item: (-int(item["priority_score"]), str(item["arxiv_id"]), str(item["theorem_id"])))
    batch = batch[:limit]
    kept_ids = {str(row["row_id"]) for row in batch}
    templates = [row for row in templates if str(row["row_id"]) in kept_ids]
    summary = {
        "schema_version": "statement_review_batch_summary.v1",
        "considered_fidelity_rows": considered,
        "review_batch_rows": len(batch),
        "excluded_rows": max(0, considered - len(batch)),
        "exclusion_reason_counts": dict(exclusion_counts.most_common()),
        "template_rows": len(templates),
        "honest_scope": "Human or trusted-review adjudication batch only; blank templates do not promote rows.",
    }
    return batch, templates, summary


def export_statement_review_batch(
    *,
    project_root: Path,
    ledger_paths: list[Path],
    report_roots: list[Path],
    evidence_roots: list[Path],
    input_jsonl: Path | None,
    out_jsonl: Path,
    out_template: Path,
    out_summary: Path,
    limit: int = 100,
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
    batch, templates, summary = build_statement_review_batch(rows, limit=limit)
    result = {**summary, "source_corpus_rows": source_rows, "out_jsonl": str(out_jsonl), "out_template": str(out_template)}
    _write_jsonl(out_jsonl, batch)
    _write_jsonl(out_template, templates)
    _write_json(out_summary, result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build reviewed-exact statement adjudication batch")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--ledger-path", action="append", type=Path, default=[])
    parser.add_argument("--report-root", action="append", type=Path, default=[])
    parser.add_argument("--evidence-root", action="append", type=Path, default=[])
    parser.add_argument("--input-jsonl", type=Path, default=None)
    parser.add_argument("--out-jsonl", type=Path, default=DEFAULT_OUT_JSONL)
    parser.add_argument("--out-template", type=Path, default=DEFAULT_OUT_TEMPLATE)
    parser.add_argument("--out-summary", type=Path, default=DEFAULT_OUT_SUMMARY)
    parser.add_argument("--limit", type=int, default=100)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = export_statement_review_batch(
        project_root=args.project_root,
        ledger_paths=args.ledger_path or [DEFAULT_LEDGER_DIR],
        report_roots=args.report_root or [DEFAULT_REPORT_DIR],
        evidence_roots=args.evidence_root or [DEFAULT_EVIDENCE_DIR],
        input_jsonl=args.input_jsonl,
        out_jsonl=args.out_jsonl,
        out_template=args.out_template,
        out_summary=args.out_summary,
        limit=args.limit,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
