#!/usr/bin/env python3
"""Build curated corpus surfaces from schema-valid DESol corpus rows."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from export_corpus import (
    DEFAULT_EVIDENCE_DIR,
    DEFAULT_LEDGER_DIR,
    DEFAULT_REPORT_DIR,
    SCHEMA_VERSION,
    build_corpus_rows,
    validate_against_schema,
)


CURATED_SCHEMA_VERSION = "curated_corpus.v1"
DEFAULT_OUT_DIR = Path("output/corpus/curated")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def _schema_errors(row: dict[str, Any]) -> list[str]:
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / "corpus_row.v1.schema.json"
    return validate_against_schema(row, schema_path)


def _source_match_status(row: dict[str, Any]) -> str:
    evidence = row.get("alignment_evidence") if isinstance(row.get("alignment_evidence"), dict) else {}
    source_match = evidence.get("source_match") if isinstance(evidence.get("source_match"), dict) else {}
    return str(source_match.get("match_status", "") or "")


def _gold_exclusion_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if str(row.get("schema_version", "")) != SCHEMA_VERSION:
        reasons.append("schema_version_mismatch")
    reasons.extend(f"schema:{error}" for error in _schema_errors(row))
    if str(row.get("dataset_tier", "")) != "gold_proof":
        reasons.append("not_gold_proof_tier")
    if str(row.get("status", "")) != "FULLY_PROVEN":
        reasons.append("status_not_fully_proven")
    if str(row.get("proof_method", "")) != "lean_verified":
        reasons.append("proof_method_not_lean_verified")
    tier_evidence = row.get("tier_evidence") if isinstance(row.get("tier_evidence"), dict) else {}
    for blocker in tier_evidence.get("gold_blockers", []) if isinstance(tier_evidence.get("gold_blockers"), list) else []:
        reasons.append(f"gold_blocker:{blocker}")
    text = "\n".join(str(row.get(key, "") or "") for key in ("lean_statement", "proof_text", "trust_reference"))
    if "PaperClaim" in text or "paper_claim" in text.lower():
        reasons.append("paper_claim_artifact")
    if row.get("axiom_debt"):
        reasons.append("axiom_or_paper_theory_debt")
    if row.get("gate_failures"):
        reasons.append("gate_failures_present")
    return list(dict.fromkeys(reason for reason in reasons if reason))


def _alignment_exclusion_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if str(row.get("alignment_tier", "")) != "alignment_gold":
        reasons.append("not_alignment_gold")
    if str(row.get("statement_alignment_class", "")) != "exact":
        reasons.append("alignment_not_exact")
    if str(row.get("source_span_quality", "")) not in {"extractor_native", "reviewed"}:
        reasons.append(f"source_span_quality:{row.get('source_span_quality', '') or 'missing'}")
    if _source_match_status(row) != "matched":
        reasons.append(f"source_match:{_source_match_status(row) or 'missing'}")
    if bool(row.get("alignment_review_required")):
        reasons.append("alignment_review_required")
    return list(dict.fromkeys(reason for reason in reasons if reason))


def _row_context(row: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    out = dict(row)
    out["curation"] = {
        "schema_version": CURATED_SCHEMA_VERSION,
        "exclusion_reasons": reasons,
        "source_surface": "stable_corpus",
    }
    return out


def _silver_process_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("dataset_tier", "")) == "gold_proof":
            continue
        if str(row.get("training_tier", "")) in {"diagnostic", "blocker", "silver_repair"} or str(
            row.get("failure_kind", "")
        ):
            enriched = dict(row)
            enriched["curation"] = {
                "schema_version": CURATED_SCHEMA_VERSION,
                "training_scope": "silver_process_only_not_proof_training",
                "source_surface": "stable_corpus",
            }
            selected.append(enriched)
    return selected


def curate_rows(rows: list[dict[str, Any]], mixed_summary: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    gold_rows: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, Any]] = []
    alignment_candidates: list[dict[str, Any]] = []
    exclusion_counts: Counter[str] = Counter()

    for row in rows:
        gold_reasons = _gold_exclusion_reasons(row)
        if gold_reasons:
            excluded = _row_context(row, gold_reasons)
            excluded_rows.append(excluded)
            exclusion_counts.update(gold_reasons)
        else:
            gold_rows.append(_row_context(row, []))

        if not _alignment_exclusion_reasons(row):
            alignment_candidates.append(_row_context(row, []))

    surfaces = {
        "gold_proofs": gold_rows,
        "alignment_gold_candidates": alignment_candidates,
        "silver_process": _silver_process_rows(rows),
        "excluded_rows": excluded_rows,
    }
    verified_proven = int(mixed_summary.get("verified_proven_count", 0) or 0)
    summary = {
        "schema_version": CURATED_SCHEMA_VERSION,
        "source_schema_version": SCHEMA_VERSION,
        "source_rows": len(rows),
        "gold_rows": len(gold_rows),
        "verified_proven": verified_proven,
        "gold_not_greater_than_verified_proven": len(gold_rows) <= verified_proven,
        "alignment_gold_rows": len(alignment_candidates),
        "silver_process_rows": len(surfaces["silver_process"]),
        "excluded_rows": len(excluded_rows),
        "exclusion_reason_counts": dict(exclusion_counts.most_common()),
        "alignment_review_needed": sum(1 for row in rows if bool(row.get("alignment_review_required"))),
        "novelty_unknown_count": sum(1 for row in rows if str(row.get("mathlib_novelty_status", "")) == "unknown"),
        "first_formalization_claims": sum(
            1
            for row in gold_rows
            if str(row.get("mathlib_novelty_status", "")) == "new_candidate"
            and isinstance(row.get("identity_evidence"), dict)
            and bool(row["identity_evidence"].get("mathlib_fingerprint_check"))
        ),
        "unsupported_first_formalization_claims": sum(
            1
            for row in gold_rows
            if str(row.get("mathlib_novelty_status", "")) == "new_candidate"
            and not (
                isinstance(row.get("identity_evidence"), dict)
                and bool(row["identity_evidence"].get("mathlib_fingerprint_check"))
            )
        ),
        "source_summary": mixed_summary,
    }
    return surfaces, summary


def export_curated_corpus(
    *,
    project_root: Path,
    ledger_paths: list[Path],
    report_roots: list[Path],
    evidence_roots: list[Path],
    out_dir: Path,
) -> dict[str, Any]:
    rows, mixed_summary = build_corpus_rows(
        ledger_paths=ledger_paths,
        project_root=project_root,
        report_roots=report_roots,
        evidence_roots=evidence_roots,
    )
    surfaces, summary = curate_rows(rows, mixed_summary)
    outputs = {
        "gold_proofs": out_dir / "gold_proofs.jsonl",
        "alignment_gold_candidates": out_dir / "alignment_gold_candidates.jsonl",
        "silver_process": out_dir / "silver_process.jsonl",
        "excluded_rows": out_dir / "excluded_rows.jsonl",
    }
    for name, path in outputs.items():
        _write_jsonl(path, surfaces[name])
    summary_path = out_dir / "curated_summary.json"
    result = {
        **summary,
        "outputs": {name: str(path) for name, path in outputs.items()},
        "summary_path": str(summary_path),
    }
    _write_json(summary_path, result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export curated DESol corpus surfaces")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--ledger-path", action="append", type=Path, default=[])
    parser.add_argument("--report-root", action="append", type=Path, default=[])
    parser.add_argument("--evidence-root", action="append", type=Path, default=[])
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = export_curated_corpus(
        project_root=args.project_root,
        ledger_paths=args.ledger_path or [DEFAULT_LEDGER_DIR],
        report_roots=args.report_root or [DEFAULT_REPORT_DIR],
        evidence_roots=args.evidence_root or [DEFAULT_EVIDENCE_DIR],
        out_dir=args.out_dir,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["gold_not_greater_than_verified_proven"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
