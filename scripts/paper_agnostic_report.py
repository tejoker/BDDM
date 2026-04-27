#!/usr/bin/env python3
"""Summarize paper-agnostic ledger behavior without rerunning proof search."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from axiom_debt_burndown import build_axiom_debt_burndown


PAPER_STATUSES = (
    "FULLY_PROVEN",
    "AXIOM_BACKED",
    "VALID_STATEMENT_UNPROVEN",
    "TRANSLATION_UNCERTAIN",
    "EXTRACTION_FAILED",
    "OUT_OF_SCOPE_DOMAIN",
)

BLOCKERS = (
    "missing_latex_source",
    "latex_preprocessing",
    "theorem_extraction",
    "statement_translation",
    "lean_elaboration",
    "missing_mathlib_definition",
    "missing_domain_library",
    "proof_search_exhausted",
    "api_or_runtime_failure",
    "manual_review_required",
)

PAPER_LOCAL_AXIOM_RESULT_LABEL = "proved_modulo_paper_local_axioms"
PAPER_LOCAL_AXIOM_CLAIM_SCOPE = (
    "AXIOM_BACKED results are Lean-checked only after accepting listed "
    "paper-local axioms/declarations; they are not unconditional verified theorems."
)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _ledger_entries(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw = _read_json(path)
    if isinstance(raw, list):
        return {"schema_version": "legacy", "paper_id": path.stem}, [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict):
        entries = raw.get("entries", [])
        meta = {k: v for k, v in raw.items() if k != "entries"}
        if isinstance(entries, list):
            return meta, [r for r in entries if isinstance(r, dict)]
    return {"schema_version": "unreadable", "paper_id": path.stem}, []


def _paper_id(meta: dict[str, Any], path: Path) -> str:
    for key in ("paper_id", "arxiv_id", "source_paper"):
        value = str(meta.get(key, "")).strip()
        if value:
            return value
    return path.stem


def _has_valid_statement(row: dict[str, Any]) -> bool:
    stmt = str(row.get("lean_statement", "")).strip()
    if not stmt:
        return False
    lowered = stmt.lower()
    return "translation failed" not in lowered and ("theorem" in lowered or "lemma" in lowered)


def _paper_status(row: dict[str, Any]) -> str:
    status = str(row.get("status", "UNRESOLVED")).strip().upper()
    if status == "FULLY_PROVEN":
        return "FULLY_PROVEN"
    if status in {"AXIOM_BACKED", "INTERMEDIARY_PROVEN"}:
        return "AXIOM_BACKED"
    if status == "TRANSLATION_LIMITED":
        return "OUT_OF_SCOPE_DOMAIN"
    if status == "FLAWED":
        return "TRANSLATION_UNCERTAIN"
    if _has_valid_statement(row):
        return "VALID_STATEMENT_UNPROVEN"
    return "TRANSLATION_UNCERTAIN"


def _blocker(row: dict[str, Any]) -> str:
    explicit = str(row.get("blocker", "") or row.get("failure_stage", "")).strip()
    if explicit in BLOCKERS:
        return explicit

    status = _paper_status(row)
    origin = str(row.get("failure_origin", "")).lower()
    err = str(row.get("error_message", "") or row.get("error", "")).lower()

    if status == "FULLY_PROVEN":
        return "none"
    if status == "AXIOM_BACKED":
        return "missing_domain_library"
    if status == "OUT_OF_SCOPE_DOMAIN":
        return "missing_domain_library"
    if "api" in err or "rate" in err or "timeout" in err:
        return "api_or_runtime_failure"
    if "unknown identifier" in err or "missing" in err or "mathlib" in err:
        return "missing_mathlib_definition"
    if "unexpected token" in err or "type mismatch" in err or "elaborat" in err:
        return "lean_elaboration"
    if "formalization" in origin or "translation" in origin:
        return "statement_translation"
    if "proof_search" in origin or "search" in err:
        return "proof_search_exhausted"
    if status == "TRANSLATION_UNCERTAIN":
        return "statement_translation"
    return "manual_review_required"


def _schema_completeness(row: dict[str, Any]) -> float:
    required = ("theorem_name", "lean_statement", "status")
    present = sum(1 for key in required if str(row.get(key, "")).strip())
    optional = ("proof_text", "step_obligations", "assumptions", "provenance")
    present += sum(1 for key in optional if row.get(key) not in (None, "", [], {}))
    return present / float(len(required) + len(optional))


def _axiom_debt(row: dict[str, Any]) -> list[str]:
    debt = row.get("axiom_debt", [])
    if isinstance(debt, list):
        return [str(x) for x in debt if str(x).strip()]
    if isinstance(debt, str) and debt.strip():
        return [debt]
    return []


def _paper_local_axiom_disclosure(entries: list[dict[str, Any]]) -> dict[str, Any]:
    axiom_backed = [
        str(row.get("theorem_name", ""))
        for row in entries
        if _paper_status(row) == "AXIOM_BACKED" and str(row.get("theorem_name", "")).strip()
    ]
    debt = list(dict.fromkeys(item for row in entries for item in _axiom_debt(row)))
    return {
        "required": bool(axiom_backed or debt),
        "result_label": PAPER_LOCAL_AXIOM_RESULT_LABEL,
        "claim_scope": PAPER_LOCAL_AXIOM_CLAIM_SCOPE,
        "theorem_count": len(axiom_backed),
        "theorems": axiom_backed,
        "axiom_debt": debt,
    }


def build_report(*, ledger_dir: Path, suite_json: Path | None, toolchain_file: Path) -> dict[str, Any]:
    suite_papers: set[str] = set()
    if suite_json is not None:
        suite = _read_json(suite_json)
        papers = suite.get("papers", []) if isinstance(suite, dict) else []
        for item in papers:
            if isinstance(item, dict) and str(item.get("paper_id", "")).strip():
                suite_papers.add(str(item["paper_id"]).strip())

    ledger_files = sorted(ledger_dir.glob("*.json")) if ledger_dir.exists() else []
    paper_rows: list[dict[str, Any]] = []
    aggregate_status = Counter()
    aggregate_blockers = Counter()
    aggregate_axiom_backed_theorems: list[str] = []
    aggregate_axiom_debt: list[str] = []
    aggregate_entries: list[dict[str, Any]] = []

    for path in ledger_files:
        meta, entries = _ledger_entries(path)
        pid = _paper_id(meta, path)
        if suite_papers and pid not in suite_papers:
            continue

        statuses = Counter(_paper_status(row) for row in entries)
        blockers = Counter(_blocker(row) for row in entries)
        completeness = [_schema_completeness(row) for row in entries]
        aggregate_status.update(statuses)
        aggregate_blockers.update(blockers)
        aggregate_entries.extend(entries)
        disclosure = _paper_local_axiom_disclosure(entries)
        aggregate_axiom_backed_theorems.extend(str(x) for x in disclosure["theorems"])
        aggregate_axiom_debt.extend(str(x) for x in disclosure["axiom_debt"])
        paper_rows.append(
            {
                "paper_id": pid,
                "ledger": str(path),
                "schema_version": str(meta.get("schema_version", "legacy")),
                "evidence_label": (
                    "full_verified_closure"
                    if entries and statuses.get("FULLY_PROVEN", 0) == len(entries) and not disclosure["required"]
                    else "partial_diagnostic_evidence"
                ),
                "primary_metric": "statuses.FULLY_PROVEN",
                "claim_scope": (
                    "All ledger entries for this paper are FULLY_PROVEN without paper-local axiom disclosure."
                    if entries and statuses.get("FULLY_PROVEN", 0) == len(entries) and not disclosure["required"]
                    else "Partial diagnostic evidence: use statuses, blockers, and paper_local_axiom_disclosure; do not read this as full paper closure."
                ),
                "theorems": len(entries),
                "statuses": {key: int(statuses.get(key, 0)) for key in PAPER_STATUSES},
                "blockers": {key: int(blockers.get(key, 0)) for key in ("none", *BLOCKERS)},
                "paper_local_axiom_disclosure": disclosure,
                "axiom_debt_burndown": build_axiom_debt_burndown(entries),
                "mean_schema_completeness": round(sum(completeness) / len(completeness), 4) if completeness else 0.0,
            }
        )

    try:
        toolchain = toolchain_file.read_text(encoding="utf-8").strip()
    except OSError:
        toolchain = ""

    aggregate_disclosure = {
        "required": bool(aggregate_axiom_backed_theorems or aggregate_axiom_debt),
        "result_label": PAPER_LOCAL_AXIOM_RESULT_LABEL,
        "claim_scope": PAPER_LOCAL_AXIOM_CLAIM_SCOPE,
        "theorem_count": len(aggregate_axiom_backed_theorems),
        "theorems": aggregate_axiom_backed_theorems,
        "axiom_debt": list(dict.fromkeys(aggregate_axiom_debt)),
    }
    full_verified = bool(
        paper_rows
        and aggregate_status.get("FULLY_PROVEN", 0) == sum(int(row["theorems"]) for row in paper_rows)
        and not aggregate_disclosure["required"]
    )

    return {
        "schema_version": "1.0.0",
        "toolchain": toolchain,
        "ledger_dir": str(ledger_dir),
        "suite_json": str(suite_json) if suite_json else "",
        "evidence_label": "full_verified_closure" if full_verified else "partial_diagnostic_evidence",
        "primary_metric": "aggregate_statuses.FULLY_PROVEN",
        "claim_scope": (
            "Every evaluated ledger entry is FULLY_PROVEN without paper-local axiom disclosure."
            if full_verified
            else "Partial diagnostic evidence: use aggregate_statuses, aggregate_blockers, and paper_local_axiom_disclosure; do not read this as full suite closure."
        ),
        "papers_evaluated": len(paper_rows),
        "theorems_evaluated": sum(int(row["theorems"]) for row in paper_rows),
        "aggregate_statuses": {key: int(aggregate_status.get(key, 0)) for key in PAPER_STATUSES},
        "aggregate_blockers": {key: int(aggregate_blockers.get(key, 0)) for key in ("none", *BLOCKERS)},
        "paper_local_axiom_disclosure": aggregate_disclosure,
        "axiom_debt_burndown": build_axiom_debt_burndown(aggregate_entries),
        "papers": paper_rows,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize paper-agnostic verification ledgers")
    parser.add_argument("--ledger-dir", default="output/verification_ledgers")
    parser.add_argument("--suite-json", default="")
    parser.add_argument("--toolchain-file", default="lean-toolchain")
    parser.add_argument("--out-json", default="output/reports/paper_agnostic_report.json")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    report = build_report(
        ledger_dir=Path(args.ledger_dir),
        suite_json=Path(args.suite_json) if args.suite_json else None,
        toolchain_file=Path(args.toolchain_file),
    )
    out_path = Path(args.out_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"ok": True, "out_json": str(out_path), "papers_evaluated": report["papers_evaluated"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
