#!/usr/bin/env python3
"""Deterministic source-evidence matching for corpus rows.

This module intentionally stays small and dependency-light so corpus export,
source-span repair, and queue workers can share the same conservative matching
policy.  It promotes only clear winners; close or duplicated candidates remain
review work.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


DEFAULT_MIN_SCORE = 90
DEFAULT_MIN_MARGIN = 25


def safe_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def normalize_ws(text: str, *, limit: int = 20000) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())[:limit]


def _basename_key(value: str) -> str:
    if not value:
        return ""
    return normalized_key(Path(value).name)


def _statementish_sources(ledger_row: dict[str, Any], source_latex: str) -> list[str]:
    artifact = ledger_row.get("semantic_equivalence_artifact")
    context = ledger_row.get("context_pack")
    values = [
        source_latex,
        safe_text(ledger_row.get("source_latex")),
        safe_text(ledger_row.get("normalized_text")),
        safe_text(ledger_row.get("statement")),
        safe_text(ledger_row.get("lean_statement")),
    ]
    if isinstance(artifact, dict):
        values.extend(
            [
                safe_text(artifact.get("original_latex_theorem")),
                safe_text(artifact.get("normalized_natural_language_theorem")),
                safe_text(artifact.get("extracted_conclusion")),
            ]
        )
    if isinstance(context, dict):
        values.append(safe_text(context.get("original_latex_theorem")))
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        normalized = normalize_ws(value)
        if normalized and normalized not in seen:
            out.append(normalized)
            seen.add(normalized)
    return out


def _name_sources(ledger_row: dict[str, Any]) -> set[str]:
    provenance = ledger_row.get("provenance") if isinstance(ledger_row.get("provenance"), dict) else {}
    values = [
        safe_text(provenance.get("label")),
        safe_text(ledger_row.get("theorem_name")),
        safe_text(ledger_row.get("theorem_id")),
        safe_text(ledger_row.get("name")),
        safe_text(ledger_row.get("canonical_theorem_id")),
    ]
    names: set[str] = set()
    for value in values:
        value = value.strip()
        if not value:
            continue
        names.add(normalized_key(value))
        names.add(normalized_key(value.rsplit(".", 1)[-1]))
    return {name for name in names if name}


def _file_sources(ledger_row: dict[str, Any]) -> set[str]:
    values = [safe_text(ledger_row.get("source_file"))]
    artifacts = ledger_row.get("artifact_paths") if isinstance(ledger_row.get("artifact_paths"), dict) else {}
    for key in ("source_file", "tex_file", "source_path"):
        values.append(safe_text(artifacts.get(key)))
    files: set[str] = set()
    for value in values:
        value = value.strip()
        if value:
            files.add(str(Path(value)))
            files.add(Path(value).name)
    return {item for item in files if item}


def _kind_sources(ledger_row: dict[str, Any]) -> set[str]:
    values = [
        safe_text(ledger_row.get("kind")),
        safe_text(ledger_row.get("theorem_kind")),
        safe_text(ledger_row.get("statement_kind")),
    ]
    return {value.strip().lower() for value in values if value.strip()}


def _candidate_name_keys(row: dict[str, Any]) -> set[str]:
    values = [
        safe_text(row.get("name")),
        safe_text(row.get("label")),
        safe_text(row.get("env_name")),
        safe_text(row.get("source_span_id")),
    ]
    keys: set[str] = set()
    for value in values:
        value = value.strip()
        if not value:
            continue
        keys.add(normalized_key(value))
        keys.add(normalized_key(value.rsplit(".", 1)[-1]))
    return {key for key in keys if key}


def _score_candidate(
    *,
    row: dict[str, Any],
    wanted_names: set[str],
    wanted_statements: list[str],
    wanted_files: set[str],
    wanted_kinds: set[str],
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    candidate_names = _candidate_name_keys(row)
    if wanted_names and candidate_names.intersection(wanted_names):
        score += 100
        reasons.append("name_or_label_exact")

    candidate_statement = normalize_ws(safe_text(row.get("statement")))
    if candidate_statement:
        for wanted in wanted_statements:
            if candidate_statement == wanted:
                score += 95
                reasons.append("source_statement_exact")
                break
        else:
            for wanted in wanted_statements:
                if len(wanted) >= 32 and (wanted in candidate_statement or candidate_statement in wanted):
                    score += 45
                    reasons.append("source_statement_containment")
                    break

    candidate_file = safe_text(row.get("source_file")).strip()
    if candidate_file and wanted_files:
        if candidate_file in wanted_files or str(Path(candidate_file)) in wanted_files:
            score += 15
            reasons.append("source_file_exact")
        elif Path(candidate_file).name in wanted_files:
            score += 8
            reasons.append("source_file_basename")

    theorem_kind = safe_text(row.get("kind")).strip().lower()
    if theorem_kind and wanted_kinds and theorem_kind in wanted_kinds:
        score += 5
        reasons.append("kind_exact")

    return score, reasons


def resolve_evidence_row(
    *,
    paper_id: str,
    ledger_row: dict[str, Any],
    source_latex: str,
    evidence_rows: list[dict[str, Any]],
    min_score: int = DEFAULT_MIN_SCORE,
    min_margin: int = DEFAULT_MIN_MARGIN,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a conservative evidence-row match plus machine-readable evidence."""
    if not evidence_rows:
        return {}, {"match_status": "missing", "reason": "no_extracted_theorems_for_paper"}

    wanted_names = _name_sources(ledger_row)
    wanted_statements = _statementish_sources(ledger_row, source_latex)
    wanted_files = _file_sources(ledger_row)
    wanted_kinds = _kind_sources(ledger_row)

    scored: list[dict[str, Any]] = []
    for idx, row in enumerate(evidence_rows):
        score, reasons = _score_candidate(
            row=row,
            wanted_names=wanted_names,
            wanted_statements=wanted_statements,
            wanted_files=wanted_files,
            wanted_kinds=wanted_kinds,
        )
        scored.append(
            {
                "index": idx,
                "score": score,
                "name": safe_text(row.get("name")),
                "label": safe_text(row.get("label")),
                "source_file": safe_text(row.get("source_file")),
                "reasons": reasons,
            }
        )

    scored.sort(key=lambda item: (-int(item["score"]), str(item["name"]), int(item["index"])))
    top = scored[0]
    runner_up_score = int(scored[1]["score"]) if len(scored) > 1 else 0
    margin = int(top["score"]) - runner_up_score
    tied_top = [item for item in scored if int(item["score"]) == int(top["score"])]
    evidence = {
        "match_method": "scored_evidence_resolver",
        "candidate_count": len(evidence_rows),
        "top_score": int(top["score"]),
        "runner_up_score": runner_up_score,
        "score_margin": margin,
        "min_score": min_score,
        "min_margin": min_margin,
        "candidate_scores": scored[:10],
        "diagnostics": {
            "wanted_name_keys": sorted(wanted_names)[:20],
            "wanted_kinds": sorted(wanted_kinds),
            "tied_top_count": len(tied_top),
            "tied_top_candidates": [
                {
                    "name": safe_text(item.get("name")),
                    "label": safe_text(item.get("label")),
                    "source_file": safe_text(item.get("source_file")),
                    "score": int(item.get("score", 0) or 0),
                    "reasons": item.get("reasons", []),
                }
                for item in tied_top[:10]
            ],
        },
    }

    if int(top["score"]) <= 0:
        return {}, {**evidence, "match_status": "missing", "reason": "no_positive_candidate"}
    if int(top["score"]) < min_score:
        return {}, {**evidence, "match_status": "ambiguous", "reason": "top_score_below_threshold"}
    if len(scored) > 1 and margin < min_margin:
        return {}, {**evidence, "match_status": "ambiguous", "reason": "top_candidate_margin_too_small"}

    selected = evidence_rows[int(top["index"])]
    return selected, {
        **evidence,
        "match_status": "matched",
        "selected_candidate": {
            "index": int(top["index"]),
            "name": safe_text(selected.get("name")),
            "label": safe_text(selected.get("label")),
            "source_file": safe_text(selected.get("source_file")),
        },
    }
