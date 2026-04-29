#!/usr/bin/env python3
"""Attach extractor-native source spans to legacy extracted_theorems artifacts."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from export_corpus import _normalize_ws, _normalized_key
from source_evidence_resolver import resolve_evidence_row
from theorem_extractor import TheoremEntry, extract_theorems


DEFAULT_EVIDENCE_ROOT = Path("reproducibility/paper_agnostic_golden10_results")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _entry_payload(entry: TheoremEntry) -> dict[str, Any]:
    payload = asdict(entry)
    if isinstance(payload.get("source_span"), dict):
        payload["source_span"]["span_confidence"] = "exact_extractor"
    return payload


def _source_path(row: dict[str, Any], project_root: Path) -> Path | None:
    source_file = str(row.get("source_file", "") or "").strip()
    if not source_file:
        return None
    path = Path(source_file)
    return path if path.is_absolute() else project_root / path


def _matching_entries(row: dict[str, Any], extracted: list[TheoremEntry]) -> list[TheoremEntry]:
    want_name = _normalized_key(str(row.get("name", "") or "").strip())
    want_statement = _normalize_ws(str(row.get("statement", "") or "").strip())
    matches: list[TheoremEntry] = []
    for entry in extracted:
        name_match = bool(want_name and _normalized_key(entry.name) == want_name)
        statement_match = bool(want_statement and _normalize_ws(entry.statement) == want_statement)
        if name_match or statement_match:
            matches.append(entry)
    return matches


def _resolve_entry(row: dict[str, Any], extracted: list[TheoremEntry]) -> tuple[TheoremEntry | None, dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for idx, entry in enumerate(extracted):
        candidates.append(
            {
                "resolver_index": idx,
                "kind": entry.kind,
                "name": entry.name,
                "label": entry.label,
                "statement": entry.statement,
                "source_file": entry.source_file,
                "env_name": entry.env_name,
                "source_span_id": entry.source_span_id,
            }
        )
    selected, evidence = resolve_evidence_row(
        paper_id="",
        ledger_row=row,
        source_latex=str(row.get("statement", "") or ""),
        evidence_rows=candidates,
    )
    if evidence.get("match_status") != "matched":
        return None, evidence
    return extracted[int(selected["resolver_index"])], evidence


def repair_payload(payload: dict[str, Any], *, project_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = payload.get("entries", [])
    if not isinstance(rows, list):
        return payload, {"rows": 0, "repaired_rows": 0, "unmatched_rows": 0, "ambiguous_rows": 0}

    by_source: dict[Path, list[TheoremEntry]] = {}
    repaired_rows = 0
    unmatched_rows = 0
    ambiguous_rows = 0
    match_status_counts: dict[str, int] = {}
    match_reason_counts: dict[str, int] = {}
    match_examples: list[dict[str, Any]] = []
    out_rows: list[Any] = []
    for raw in rows:
        if not isinstance(raw, dict):
            out_rows.append(raw)
            continue
        row = dict(raw)
        if isinstance(row.get("source_span"), dict) and row.get("source_span"):
            out_rows.append(row)
            continue
        source_path = _source_path(row, project_root)
        if source_path is None or not source_path.exists():
            unmatched_rows += 1
            out_rows.append(row)
            continue
        if source_path not in by_source:
            by_source[source_path] = extract_theorems(source_path)
        entry, evidence = _resolve_entry(row, by_source[source_path])
        status = str(evidence.get("match_status", "missing") or "missing")
        reason = str(evidence.get("reason", status) or status)
        match_status_counts[status] = match_status_counts.get(status, 0) + 1
        match_reason_counts[reason] = match_reason_counts.get(reason, 0) + 1
        if entry is None:
            if status == "ambiguous":
                ambiguous_rows += 1
            else:
                unmatched_rows += 1
            if len(match_examples) < 20:
                match_examples.append(
                    {
                        "name": str(row.get("name", "")),
                        "status": status,
                        "reason": reason,
                        "candidate_scores": evidence.get("candidate_scores", []),
                    }
                )
            out_rows.append(row)
            continue
        repaired = {**row, **_entry_payload(entry)}
        repaired["source_match_repair"] = evidence
        repaired_rows += 1
        out_rows.append(repaired)

    out = dict(payload)
    out["entries"] = out_rows
    out["span_repair"] = {
        "schema_version": "extracted_theorem_span_repair.v1",
        "method": "extractor_unique_name_or_statement_match",
        "resolver": "scored_evidence_resolver",
        "rows": len(rows),
        "repaired_rows": repaired_rows,
        "unmatched_rows": unmatched_rows,
        "ambiguous_rows": ambiguous_rows,
        "match_status_counts": match_status_counts,
        "match_reason_counts": match_reason_counts,
        "match_examples": match_examples,
    }
    return out, out["span_repair"]


def repair_file(path: Path, *, project_root: Path, write: bool) -> dict[str, Any]:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        return {"path": str(path), "ok": False, "reason": "invalid_json"}
    repaired, summary = repair_payload(payload, project_root=project_root)
    if write:
        _write_json(path, repaired)
    return {"path": str(path), "ok": True, **summary}


def _evidence_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            files.append(path)
        elif path.exists():
            files.extend(sorted(path.glob("*/extracted_theorems.json")))
    return sorted(dict.fromkeys(files), key=lambda p: str(p))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Attach extractor-native spans to extracted_theorems.json artifacts")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--evidence-root", action="append", type=Path, default=[])
    parser.add_argument("--write", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    roots = args.evidence_root or [DEFAULT_EVIDENCE_ROOT]
    results = [repair_file(path, project_root=args.project_root, write=bool(args.write)) for path in _evidence_files(roots)]
    total = {
        "ok": True,
        "files": len(results),
        "write": bool(args.write),
        "rows": sum(int(row.get("rows", 0)) for row in results),
        "repaired_rows": sum(int(row.get("repaired_rows", 0)) for row in results),
        "unmatched_rows": sum(int(row.get("unmatched_rows", 0)) for row in results),
        "ambiguous_rows": sum(int(row.get("ambiguous_rows", 0)) for row in results),
        "results": results,
    }
    print(json.dumps(total, indent=2, ensure_ascii=False))
    return 0 if all(row.get("ok") for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
