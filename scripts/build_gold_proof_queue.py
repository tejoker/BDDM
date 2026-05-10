#!/usr/bin/env python3
"""Rank strict proof-production candidates without changing proof metrics."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from export_corpus import DEFAULT_EVIDENCE_DIR, DEFAULT_LEDGER_DIR, DEFAULT_REPORT_DIR, build_corpus_rows
from statement_validity import false_target_reason, statement_fidelity_gate


DEFAULT_OUT_JSONL = Path("output/corpus/gold_proof_growth_queue.jsonl")
DEFAULT_OUT_SUMMARY = Path("output/corpus/gold_proof_growth_summary.json")
MIN_PROOF_ALIGNMENT_CONFIDENCE = 0.75


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


def proof_candidate_blockers(row: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    fidelity = statement_fidelity_gate(row)
    if not fidelity.proof_eligible:
        blockers.append("statement_fidelity_not_proof_eligible")
        blockers.extend(f"statement_fidelity:{item}" for item in fidelity.statement_fidelity_blockers)
    text = "\n".join(str(row.get(key, "") or "") for key in ("lean_statement", "proof_text", "trust_reference", "failure_kind"))
    if "PaperClaim" in text or "paper_claim" in text.lower():
        blockers.append("paper_claim_artifact")
    # paper_definition_stub:* entries are transparent stubs (def X := 0 / Set.univ).
    # classify_statement() already exempts them from its paper_theory_debt blocker, so
    # blocking here too is inconsistent with what the fidelity gate allows.  Only block
    # on non-stub debt (paper_symbol:*, paper_local_lemma:*, bare entries, etc.).
    non_stub_debt = [d for d in (row.get("axiom_debt") or []) if not str(d).startswith("paper_definition_stub:")]
    if non_stub_debt:
        blockers.append("axiom_or_paper_theory_debt")
    gate_failures = [str(item) for item in row.get("gate_failures", [])] if isinstance(row.get("gate_failures"), list) else []
    if any("domain_assumption" in item or "paper_local" in item for item in gate_failures):
        blockers.append("domain_or_paper_local_gate_failure")
    status = str(row.get("status", ""))
    if status in {"FULLY_PROVEN", "AXIOM_BACKED", "FLAWED", "INTERMEDIARY_PROVEN", "TRANSLATION_LIMITED"}:
        blockers.append(f"status_not_queueable:{row.get('status')}")
    if not _proof_alignment_ready(row):
        blockers.append("alignment_not_exact_or_reviewed_exact")
    if str(row.get("statement_alignment_class", "")) != "exact" and str(row.get("reviewed_statement_alignment_class", "")) != "exact":
        blockers.append("statement_alignment_not_exact")
    if float(row.get("alignment_confidence", 0.0) or 0.0) < MIN_PROOF_ALIGNMENT_CONFIDENCE and float(
        row.get("reviewed_alignment_confidence", 0.0) or 0.0
    ) < MIN_PROOF_ALIGNMENT_CONFIDENCE:
        blockers.append("alignment_confidence_below_proof_threshold")
    if bool(row.get("alignment_review_required")):
        blockers.append("alignment_review_required")
    if str(row.get("source_span_quality", "")) not in {"extractor_native", "reviewed"}:
        blockers.append("source_span_not_gold_quality")
    if not str(row.get("lean_statement", "")).strip():
        blockers.append("lean_statement_missing")
    false_target = false_target_reason(row)
    if false_target:
        blockers.append(false_target)
    if _placeholder_statement(row):
        blockers.append("placeholder_or_trivial_lean_statement")
    return list(dict.fromkeys(blockers))


def _reviewed_alignment_ready(row: dict[str, Any]) -> bool:
    provenance = row.get("review_provenance") if isinstance(row.get("review_provenance"), dict) else {}
    return (
        str(row.get("reviewed_statement_alignment_class", "")) == "exact"
        and str(row.get("reviewed_equivalence_verdict", "")) in {"equivalent", "exact"}
        and float(row.get("reviewed_alignment_confidence", 0.0) or 0.0) >= MIN_PROOF_ALIGNMENT_CONFIDENCE
        and bool(provenance.get("reviewed_by"))
        and str(row.get("source_span_quality", "")) in {"extractor_native", "reviewed"}
    )


def _proof_alignment_ready(row: dict[str, Any]) -> bool:
    return bool(row.get("alignment_gold_eligible")) or _reviewed_alignment_ready(row)


def proof_closure_blockers(row: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if str(row.get("status", "")) != "FULLY_PROVEN":
        blockers.append(f"status_not_fully_proven:{row.get('status', '')}")
    if str(row.get("proof_method", "")) != "lean_verified":
        blockers.append(f"proof_method_not_lean_verified:{row.get('proof_method', '')}")
    if not str(row.get("proof_text", "") or "").strip():
        blockers.append("proof_text_missing")
    gate_failures = row.get("gate_failures") if isinstance(row.get("gate_failures"), list) else []
    if gate_failures:
        blockers.append("gate_failures_present")
    if row.get("axiom_debt"):
        blockers.append("axiom_debt_present")
    return list(dict.fromkeys(blockers))


def _placeholder_statement(row: dict[str, Any]) -> bool:
    statement = str(row.get("lean_statement", "") or "")
    compact = " ".join(statement.split())
    if not compact:
        return True
    placeholder_patterns = (
        "∃ x : ℝ, x = x",
        "exists x :",
        "x = x",
        "True",
        "let Claim : Prop :=",
        "PaperClaim",
    )
    return any(pattern in compact for pattern in placeholder_patterns)


def _score(row: dict[str, Any]) -> int:
    score = 0
    if str(row.get("alignment_tier", "")) == "alignment_gold":
        score += 40
    if _reviewed_alignment_ready(row):
        score += 35
    if str(row.get("statement_alignment_class", "")) == "exact":
        score += 25
    elif str(row.get("statement_alignment_class", "")) in {"weaker", "stronger", "partial"}:
        score += 10
    score += int(float(row.get("alignment_confidence", 0.0) or 0.0) * 20)
    if str(row.get("mathlib_novelty_status", "")) == "mathlib_overlap":
        score += 15
    if str(row.get("identity_status", "")) == "same_statement":
        score += 5
    if str(row.get("status", "")) in {"UNRESOLVED", "TRANSLATION_LIMITED"}:
        score += 5
    return score


def build_gold_proof_queue(rows: list[dict[str, Any]], *, limit: int = 200) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    rejected_counts: Counter[str] = Counter()
    rejected_row_count = 0
    per_paper: dict[str, dict[str, Any]] = defaultdict(lambda: {"candidate_rows": 0, "rejected_rows": 0, "top_score": 0})
    for row in rows:
        paper_id = str(row.get("arxiv_id", ""))
        blockers = proof_candidate_blockers(row)
        if blockers:
            rejected_row_count += 1
            rejected_counts.update(blockers)
            per_paper[paper_id]["rejected_rows"] += 1
            continue
        score = _score(row)
        per_paper[paper_id]["candidate_rows"] += 1
        per_paper[paper_id]["top_score"] = max(int(per_paper[paper_id]["top_score"]), score)
        queue.append(
            {
                "schema_version": "gold_proof_growth_queue.v1",
                "row_id": row.get("row_id", ""),
                "arxiv_id": paper_id,
                "theorem_id": row.get("theorem_id", ""),
                "canonical_theorem_id": row.get("canonical_theorem_id", ""),
                "priority_score": score,
                "alignment_tier": row.get("alignment_tier", ""),
                "statement_alignment_class": row.get("statement_alignment_class", ""),
                "alignment_confidence": row.get("alignment_confidence", 0.0),
                "reviewed_statement_alignment_class": row.get("reviewed_statement_alignment_class", ""),
                "reviewed_equivalence_verdict": row.get("reviewed_equivalence_verdict", ""),
                "reviewed_alignment_confidence": row.get("reviewed_alignment_confidence", 0.0),
                "review_provenance": row.get("review_provenance", {}),
                "claim_equivalence_verdict": row.get("claim_equivalence_verdict", ""),
                "independent_semantic_equivalence_evidence": row.get("independent_semantic_equivalence_evidence", False),
                "source_span_quality": row.get("source_span_quality", ""),
                "identity_status": row.get("identity_status", ""),
                "mathlib_novelty_status": row.get("mathlib_novelty_status", ""),
                "status": row.get("status", ""),
                "proof_method": row.get("proof_method", ""),
                "proof_closure_blockers": proof_closure_blockers(row),
                "lean_statement": row.get("lean_statement", ""),
                "source_latex": row.get("source_latex", ""),
                "artifact_paths": row.get("artifact_paths", {}),
                "proof_target": "lean_checked_exact_statement_or_audited_replacement_only",
            }
        )
    queue.sort(key=lambda item: (-int(item["priority_score"]), str(item["arxiv_id"]), str(item["theorem_id"])))
    queue = queue[:limit]
    summary = {
        "schema_version": "gold_proof_growth_summary.v1",
        "candidate_rows": len(queue),
        "rejected_rows": rejected_row_count,
        "rejection_reason_counts": dict(rejected_counts.most_common()),
        "per_paper": dict(sorted(per_paper.items())),
        "attempted_rows": 0,
        "newly_verified_rows": 0,
        "honest_scope": "Strict proof queue only; candidates require exact or reviewed-exact statement alignment before Lean proof work.",
    }
    return queue, summary


def export_gold_proof_queue(
    *,
    project_root: Path,
    ledger_paths: list[Path],
    report_roots: list[Path],
    evidence_roots: list[Path],
    out_jsonl: Path,
    out_summary: Path,
    limit: int = 200,
) -> dict[str, Any]:
    rows, corpus_summary = build_corpus_rows(
        ledger_paths=ledger_paths,
        project_root=project_root,
        report_roots=report_roots,
        evidence_roots=evidence_roots,
    )
    queue, summary = build_gold_proof_queue(rows, limit=limit)
    result = {**summary, "source_corpus_rows": corpus_summary.get("rows", 0), "out_jsonl": str(out_jsonl)}
    _write_jsonl(out_jsonl, queue)
    _write_json(out_summary, result)
    return result


def export_gold_proof_queue_from_rows(
    *,
    rows: list[dict[str, Any]],
    out_jsonl: Path,
    out_summary: Path,
    limit: int = 200,
) -> dict[str, Any]:
    queue, summary = build_gold_proof_queue(rows, limit=limit)
    result = {**summary, "source_corpus_rows": len(rows), "out_jsonl": str(out_jsonl)}
    _write_jsonl(out_jsonl, queue)
    _write_json(out_summary, result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build strict gold-proof production queue")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--ledger-path", action="append", type=Path, default=[])
    parser.add_argument("--report-root", action="append", type=Path, default=[])
    parser.add_argument("--evidence-root", action="append", type=Path, default=[])
    parser.add_argument("--out-jsonl", type=Path, default=DEFAULT_OUT_JSONL)
    parser.add_argument("--out-summary", type=Path, default=DEFAULT_OUT_SUMMARY)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--input-jsonl", type=Path, default=None, help="Optional corpus JSONL, e.g. after applying reviews")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.input_jsonl is not None:
        result = export_gold_proof_queue_from_rows(
            rows=_read_jsonl(args.input_jsonl),
            out_jsonl=args.out_jsonl,
            out_summary=args.out_summary,
            limit=args.limit,
        )
    else:
        result = export_gold_proof_queue(
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
