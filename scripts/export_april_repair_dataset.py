#!/usr/bin/env python3
"""Export APRIL-style compiler-feedback repair tuples from DESol artifacts.

The output is JSONL with a stable core schema:

{
  "failing_lean": "...",
  "error_message": "...",
  "local_context": "...",
  "previous_attempt": "...",
  "successful_repair": "..."
}

Rows are built from failed proof/translation attempts in verification ledgers
and proof logs. If the same `(paper_id, theorem_name)` also has a later
successful row, the successful proof text is attached as `successful_repair`.
Unpaired failures are still exported because they are valuable hard negatives
and future repair targets.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from repair_feedback_dataset import (
    DATASET_FAMILY,
    DEFAULT_DATASET_PATH,
    DEFAULT_RUN_ROOT,
    DEFAULT_SUMMARY_PATH,
    classify_error,
    make_repair_row,
    merge_deduped_rows,
    read_jsonl,
    summarize_rows,
)


SUCCESS_STATUSES = {"FULLY_PROVEN", "AXIOM_BACKED", "INTERMEDIARY_PROVEN"}
FAILURE_STATUSES = {"FLAWED", "UNRESOLVED", "VALID_STATEMENT_UNPROVEN", "TRANSLATION_UNCERTAIN"}


@dataclass(frozen=True)
class AttemptRow:
    paper_id: str
    theorem_name: str
    status: str
    source_artifact: str
    lean_file: str = ""
    lean_statement: str = ""
    proof_text: str = ""
    error_message: str = ""
    local_context: str = ""
    previous_attempt: str = ""
    failure_class: str = "unknown"
    timestamp_unix: int = 0


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _safe_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _safe_name(name: str) -> str:
    return (name or "").strip().rsplit(".", 1)[-1]


def _normalize_ws(text: str, *, limit: int = 8000) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text[:limit]


def _entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("entries", "rows", "results"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [x for x in rows if isinstance(x, dict)]
    return []


def _paper_id_from_path(path: Path, payload: Any) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("paper_id"), str):
        return payload["paper_id"]
    m = re.search(r"(\d{4}\.\d{5})", str(path))
    return m.group(1) if m else path.stem


def _theorem_name(row: dict[str, Any]) -> str:
    for key in ("theorem_name", "theorem", "name", "target_theorem"):
        value = _safe_text(row.get(key)).strip()
        if value:
            return value
    return ""


def _status(row: dict[str, Any]) -> str:
    if row.get("proved") is True:
        return "FULLY_PROVEN"
    if row.get("proved") is False:
        return "UNRESOLVED"
    return _safe_text(row.get("status")).upper()


def _error_message(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("error_message", "last_error", "error", "prove_summary", "stderr_tail"):
        value = _safe_text(row.get(key)).strip()
        if value and value not in parts:
            parts.append(value)
    if not parts:
        for obligation in row.get("step_obligations", []) if isinstance(row.get("step_obligations"), list) else []:
            if not isinstance(obligation, dict):
                continue
            if obligation.get("verified") is False:
                detail = _safe_text(obligation.get("detail")).strip()
                if detail:
                    parts.append(detail)
                    break
    return _normalize_ws(" | ".join(parts), limit=4000)


def _previous_attempt(row: dict[str, Any]) -> str:
    proof = _safe_text(row.get("proof_text")).strip()
    if proof:
        return proof
    tactics: list[str] = []
    for obligation in row.get("step_obligations", []) if isinstance(row.get("step_obligations"), list) else []:
        if not isinstance(obligation, dict):
            continue
        tactic = _safe_text(obligation.get("tactic")).strip()
        if tactic:
            tactics.append(tactic)
    if tactics:
        return "\n".join(tactics[:40])
    for key in ("attempted_proof", "previous_attempt", "candidate_proof"):
        value = _safe_text(row.get(key)).strip()
        if value:
            return value
    return ""


def _local_context(row: dict[str, Any]) -> str:
    chunks: list[str] = []
    provenance = row.get("provenance") if isinstance(row.get("provenance"), dict) else {}
    for key in ("section", "label", "context_excerpt", "source_statement", "normalized_statement"):
        value = _safe_text(provenance.get(key) if key in provenance else row.get(key)).strip()
        if value:
            chunks.append(f"{key}: {value}")
    assumptions = row.get("assumptions")
    if isinstance(assumptions, list) and assumptions:
        rendered: list[str] = []
        for assump in assumptions[:12]:
            if isinstance(assump, dict):
                expr = _safe_text(assump.get("lean_expr") or assump.get("label")).strip()
                grounding = _safe_text(assump.get("grounding")).strip()
                rendered.append(f"{expr} [{grounding}]" if grounding else expr)
        if rendered:
            chunks.append("assumptions: " + "; ".join(rendered))
    gate_failures = row.get("gate_failures")
    if isinstance(gate_failures, list) and gate_failures:
        chunks.append("gate_failures: " + ", ".join(str(x) for x in gate_failures[:20]))
    failed_steps: list[str] = []
    for obligation in row.get("step_obligations", []) if isinstance(row.get("step_obligations"), list) else []:
        if isinstance(obligation, dict) and obligation.get("verified") is False:
            failed_steps.append(_safe_text(obligation.get("detail")))
    if failed_steps:
        chunks.append("failed_steps: " + " | ".join(x for x in failed_steps[:5] if x))
    return _normalize_ws("\n".join(chunks), limit=8000)


def _extract_decl_from_file(lean_file: str, theorem_name: str) -> str:
    path = Path(lean_file)
    if not lean_file or not path.exists() or not theorem_name:
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    name = re.escape(_safe_name(theorem_name))
    pattern = re.compile(
        rf"(?ms)^\s*(?:theorem|lemma)\s+(?:[A-Za-z_][A-Za-z0-9_'.]*\.)?{name}\b.*?(?=^\s*(?:theorem|lemma|def|axiom|namespace|end)\b|\Z)"
    )
    match = pattern.search(text)
    return match.group(0).strip() if match else ""


def _failing_lean(row: dict[str, Any]) -> str:
    statement = _safe_text(row.get("lean_statement")).strip()
    if statement:
        return statement
    decl = _extract_decl_from_file(_safe_text(row.get("lean_file") or row.get("file")), _theorem_name(row))
    if decl:
        return decl
    return ""


def _attempt_from_row(path: Path, paper_id: str, row: dict[str, Any]) -> AttemptRow | None:
    theorem = _theorem_name(row)
    if not theorem:
        return None
    error = _error_message(row)
    status = _status(row)
    lean_file = _safe_text(row.get("lean_file") or row.get("file"))
    lean_statement = _safe_text(row.get("lean_statement")).strip() or _extract_decl_from_file(lean_file, theorem)
    return AttemptRow(
        paper_id=paper_id,
        theorem_name=theorem,
        status=status,
        source_artifact=str(path),
        lean_file=lean_file,
        lean_statement=lean_statement,
        proof_text=_safe_text(row.get("proof_text")),
        error_message=error,
        local_context=_local_context(row),
        previous_attempt=_previous_attempt(row),
        failure_class=classify_error(error),
        timestamp_unix=int(time.time()),
    )


def iter_attempts(paths: Iterable[Path]) -> list[AttemptRow]:
    attempts: list[AttemptRow] = []
    for root in paths:
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else sorted(root.rglob("*.json"))
        for path in candidates:
            payload = _read_json(path)
            if payload is None:
                continue
            paper_id = _paper_id_from_path(path, payload)
            for row in _entries(payload):
                attempt = _attempt_from_row(path, paper_id, row)
                if attempt is not None:
                    attempts.append(attempt)
    return attempts


def _key(attempt: AttemptRow) -> tuple[str, str]:
    return (attempt.paper_id, _safe_name(attempt.theorem_name))


def build_april_rows(attempts: list[AttemptRow]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    successes: dict[tuple[str, str], AttemptRow] = {}
    for attempt in attempts:
        if attempt.status in SUCCESS_STATUSES and (attempt.proof_text or attempt.previous_attempt):
            successes[_key(attempt)] = attempt

    rows: list[dict[str, Any]] = []
    classes: Counter[str] = Counter()
    paired = 0
    for attempt in attempts:
        is_failure_status = attempt.status in FAILURE_STATUSES or bool(attempt.error_message)
        if attempt.status in SUCCESS_STATUSES or not is_failure_status:
            continue
        success = successes.get(_key(attempt))
        successful_repair = ""
        if success is not None:
            successful_repair = success.proof_text or success.previous_attempt
        if successful_repair:
            paired += 1
        classes[attempt.failure_class] += 1
        failing_lean = attempt.lean_statement or _extract_decl_from_file(attempt.lean_file, attempt.theorem_name)
        rows.append(
            make_repair_row(
                paper_id=attempt.paper_id,
                theorem_name=attempt.theorem_name,
                source_artifact=attempt.source_artifact,
                failing_lean=failing_lean,
                error_message=attempt.error_message,
                local_context=attempt.local_context,
                previous_attempt=attempt.previous_attempt,
                successful_repair=successful_repair,
                repair_available=bool(successful_repair),
                repair_source="ledger_pair" if successful_repair else "ledger_unpaired",
                failure_class=attempt.failure_class,
                lean_file=attempt.lean_file,
                stage="ledger_export",
                run_id="ledger_export",
                source_artifacts=[attempt.source_artifact],
                extra={"legacy_dataset_family": "april_style_compiler_feedback_repair"},
            )
        )

    rows.sort(key=lambda r: (r["paper_id"], r["theorem_name"], r["source_artifact"]))
    summary = summarize_rows(rows)
    summary["dataset_family"] = DATASET_FAMILY
    summary["honest_scope"] = "DESol-local APRIL-style compiler-feedback tuples; not the APRIL 260k dataset"
    return rows, summary


def iter_run_rows(run_roots: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for root in run_roots:
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else sorted(root.rglob("compiler_feedback_repair_dataset.jsonl"))
        for path in candidates:
            for row in read_jsonl(path):
                source_artifacts = row.get("source_artifacts")
                if not isinstance(source_artifacts, list):
                    source_artifacts = []
                if str(path) not in [str(x) for x in source_artifacts]:
                    row = dict(row)
                    row["source_artifacts"] = [*source_artifacts, str(path)]
                    row["source_artifact"] = row.get("source_artifact") or str(path)
                rows.append(row)
    return rows


def export_dataset(
    *,
    input_paths: list[Path],
    out_jsonl: Path,
    out_summary: Path,
    run_roots: list[Path] | None = None,
) -> dict[str, Any]:
    attempts = iter_attempts(input_paths)
    ledger_rows, _ledger_summary = build_april_rows(attempts)
    run_rows = iter_run_rows(run_roots or [])
    rows = merge_deduped_rows([ledger_rows, run_rows])
    summary = summarize_rows(rows)
    summary["ledger_export_rows"] = len(ledger_rows)
    summary["run_local_rows"] = len(run_rows)
    summary["deduplicated_rows"] = len(rows)
    summary["honest_scope"] = "DESol-local APRIL-style compiler-feedback tuples; not the APRIL 260k dataset"
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    out_summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"out_jsonl": str(out_jsonl), "out_summary": str(out_summary), **summary}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export APRIL-style compiler-feedback repair tuples")
    parser.add_argument(
        "--input",
        action="append",
        type=Path,
        default=[],
        help="Ledger/log file or directory to scan; can be repeated",
    )
    parser.add_argument(
        "--run-root",
        action="append",
        type=Path,
        default=[],
        help="Run-local repair dataset root/file to merge; can be repeated",
    )
    parser.add_argument("--out-jsonl", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--out-summary", type=Path, default=DEFAULT_SUMMARY_PATH)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    inputs = args.input or [
        Path("output/verification_ledgers"),
        Path("reproducibility/full_paper_reports"),
        Path("logs"),
    ]
    run_roots = args.run_root or [DEFAULT_RUN_ROOT]
    result = export_dataset(input_paths=inputs, run_roots=run_roots, out_jsonl=args.out_jsonl, out_summary=args.out_summary)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
