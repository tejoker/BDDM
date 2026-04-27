#!/usr/bin/env python3
"""Utilities for DESol-local compiler-feedback repair datasets."""

from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import sys
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "1.1.0"
DATASET_FAMILY = "desol_compiler_feedback_repair"
DEFAULT_DATASET_PATH = Path("output/flywheel/compiler_feedback_repair_dataset.jsonl")
DEFAULT_SUMMARY_PATH = Path("output/flywheel/compiler_feedback_repair_dataset_summary.json")
DEFAULT_RUN_ROOT = Path("output/flywheel/runs")
RUN_DATASET_FILENAME = "compiler_feedback_repair_dataset.jsonl"

_PROCESS_RUN_ID = ""
_PIPELINE_COMMIT_CACHE: dict[str, str] = {}

CORE_FIELDS = (
    "failing_lean",
    "error_message",
    "raw_error_message",
    "normalized_error_message",
    "lean_error_kind",
    "primary_identifier",
    "line_col",
    "failed_candidate",
    "local_context",
    "repair_prompt_context",
    "previous_attempt",
    "successful_repair",
    "repair_source",
)


def normalize_text(value: Any, *, limit: int = 8000) -> str:
    """Return whitespace-normalized text suitable for JSONL rows."""
    if not isinstance(value, str):
        return ""
    text = re.sub(r"\s+", " ", value.strip())
    return text[:limit]


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", (value or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("._-")
    return cleaned or "run"


def normalize_error_message(error_message: Any, *, limit: int = 4000) -> str:
    """Normalize compiler feedback for stable row IDs and grouping."""
    return normalize_text(error_message, limit=limit)


_LINE_COL_PATTERNS = (
    re.compile(r"\bline\s*=\s*(\d+)\s*;\s*(?:column|col)\s*=\s*(\d+)", re.IGNORECASE),
    re.compile(r"\bline\s*=\s*(\d+)\b", re.IGNORECASE),
    re.compile(r"(?:^|\s|:)(\d+):(\d+):\s*(?:error|warning|info)\b", re.IGNORECASE),
)

_IDENT_PATTERNS = (
    re.compile(r"unknown identifier\s+[`']([^`']+)[`']", re.IGNORECASE),
    re.compile(r"unknown constant\s+[`']([^`']+)[`']", re.IGNORECASE),
    re.compile(r"unknown namespace\s+[`']([^`']+)[`']", re.IGNORECASE),
    re.compile(r"identifier\s+[`']([^`']+)[`']\s+is unknown", re.IGNORECASE),
    re.compile(r"invalid field\s+[`']([^`']+)[`']", re.IGNORECASE),
    re.compile(r"invalid field notation.*?[`']([^`']+)[`']", re.IGNORECASE),
)

_KIND_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("timeout", ("timed out", "timeout", "maxheartbeats", "maximum recursion depth", "heartbeat")),
    ("vacuity_failure", ("vacuity", "trivially provable", "trivially true", "trivialized")),
    (
        "semantic_policy_violation",
        (
            "semantic_policy",
            "semantic policy",
            "claim_shape",
            "roundtrip_semantic",
            "final_semantic_hard_block",
            "semantic_repair",
            "translation_acceptance_gate",
        ),
    ),
    (
        "typeclass_synthesis_failed",
        (
            "typeclass",
            "failed to synthesize instance",
            "failed to synthesize",
            "synthesized type class instance",
        ),
    ),
    ("unknown_identifier", ("unknown identifier", "identifier `", "identifier '")),
    ("unknown_constant", ("unknown constant", "unknown namespace", "unknown declaration")),
    ("invalid_field", ("invalid field", "invalid projection")),
    (
        "type_mismatch",
        (
            "type mismatch",
            "application type mismatch",
            "has type",
            "but is expected to have type",
            "function expected at",
            "invalid argument",
        ),
    ),
    (
        "syntax_error",
        (
            "unexpected identifier",
            "unexpected token",
            "unexpected end",
            "expected command",
            "invalid binder annotation",
            "parser",
            "syntax error",
        ),
    ),
    ("unsolved_goals", ("unsolved goals", "goals unsolved", "declaration uses 'sorry'", "declaration uses sorry")),
    ("assumption_failed", ("tactic `assumption` failed", "assumption failed", "assumption")),
    ("reflexivity_failed", ("rfl", "reflexivity")),
    (
        "import_resolution_failed",
        (
            "object file",
            "does not exist",
            "unknown module prefix",
            "module not found",
            "no such file or directory",
        ),
    ),
    ("elaboration_failed", ("lean_elaboration", "elaboration failed", "failed to elaborate")),
    (
        "tactic_failure",
        (
            "tactic",
            "simp made no progress",
            "linarith failed",
            "omega",
            "aesop failed",
            "no goals to be solved",
        ),
    ),
    ("metavariable_unsolved", ("metavariable", "synthetic opaque", "?m.")),
)

_KIND_TO_FAILURE_CLASS = {
    "unknown_identifier": "name_resolution",
    "unknown_constant": "name_resolution",
    "invalid_field": "name_resolution",
    "import_resolution_failed": "name_resolution",
    "typeclass_synthesis_failed": "typeclass_stuck",
    "type_mismatch": "type_mismatch",
    "semantic_policy_violation": "semantic_fidelity",
    "vacuity_failure": "trivialization",
    "assumption_failed": "assumption_mismatch",
    "reflexivity_failed": "reflexivity_mismatch",
    "syntax_error": "syntax_or_repl_startup",
    "timeout": "timeout",
    "unsolved_goals": "tactic_failure",
    "tactic_failure": "tactic_failure",
    "metavariable_unsolved": "tactic_failure",
    "elaboration_failed": "translation_or_elaboration",
}


def _extract_line_col(text: str) -> str:
    for pattern in _LINE_COL_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        groups = [g for g in match.groups() if g]
        if len(groups) >= 2:
            return f"{groups[0]}:{groups[1]}"
        if groups:
            return groups[0]
    return ""


def _extract_primary_identifier(text: str) -> str:
    for pattern in _IDENT_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return ""


def lean_error_kind(error: str) -> str:
    """Return a fine-grained Lean/translation diagnostic kind."""
    text = (error or "").lower()
    for kind, needles in _KIND_RULES:
        if any(needle in text for needle in needles):
            return kind
    return "other"


def parse_lean_error(error: str) -> dict[str, str]:
    """Normalize a raw Lean/compiler-feedback message into structured fields."""
    raw = str(error or "").strip()
    normalized = normalize_error_message(raw)
    return {
        "raw_error_message": raw[:8000],
        "normalized_error_message": normalized,
        "lean_error_kind": lean_error_kind(normalized),
        "primary_identifier": _extract_primary_identifier(normalized),
        "line_col": _extract_line_col(normalized),
    }


def compute_row_id(
    *,
    paper_id: str,
    theorem_name: str,
    stage: str,
    failing_lean: str,
    normalized_error_message: str,
) -> str:
    payload = {
        "paper_id": str(paper_id or ""),
        "theorem_name": str(theorem_name or ""),
        "stage": str(stage or ""),
        "failing_lean": normalize_text(failing_lean, limit=12000),
        "normalized_error_message": normalize_error_message(normalized_error_message),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def default_run_id(prefix: str = "repair") -> str:
    """Return a process-stable run ID unless DESOL_REPAIR_RUN_ID is set."""
    global _PROCESS_RUN_ID
    env_run = os.environ.get("DESOL_REPAIR_RUN_ID", "").strip()
    if env_run:
        return _safe_component(env_run)
    if not _PROCESS_RUN_ID:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        _PROCESS_RUN_ID = f"{_safe_component(prefix)}_{stamp}_{os.getpid()}"
    return _PROCESS_RUN_ID


def default_run_dataset_path(project_root: Path, run_id: str = "") -> Path:
    rid = _safe_component(run_id or default_run_id())
    return project_root / DEFAULT_RUN_ROOT / rid / RUN_DATASET_FILENAME


def _pipeline_commit(project_root: Path | None = None) -> str:
    root = (project_root or Path(".")).resolve()
    key = str(root)
    if key in _PIPELINE_COMMIT_CACHE:
        return _PIPELINE_COMMIT_CACHE[key]
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        commit = (proc.stdout or "").strip() if proc.returncode == 0 else "unknown"
    except Exception:
        commit = "unknown"
    _PIPELINE_COMMIT_CACHE[key] = commit or "unknown"
    return _PIPELINE_COMMIT_CACHE[key]


def classify_error(error: str) -> str:
    """Classify a Lean/compiler-feedback string into a stable repair bucket."""
    return _KIND_TO_FAILURE_CLASS.get(lean_error_kind(error), "other")


def toolchain_metadata(project_root: Path | None = None) -> dict[str, str]:
    """Capture cheap, deterministic toolchain metadata without shelling out."""
    root = project_root or Path(".")
    lean_toolchain = ""
    try:
        lean_toolchain = (root / "lean-toolchain").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return {
        "lean_toolchain": lean_toolchain,
        "python": sys.version.split()[0],
    }


def make_repair_row(
    *,
    failing_lean: str,
    error_message: str,
    local_context: str = "",
    previous_attempt: str = "",
    successful_repair: str = "",
    paper_id: str = "",
    theorem_name: str = "",
    source_artifact: str = "",
    source_artifacts: list[str] | None = None,
    lean_file: str = "",
    stage: str = "",
    model: str = "",
    run_id: str = "",
    pipeline_commit: str = "",
    repair_source: str = "",
    repair_available: bool | None = None,
    failure_class: str | None = None,
    project_root: Path | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable JSONL row for compiler-feedback repair training/eval."""
    raw_error = str(error_message or "").strip()
    normalized_error = normalize_error_message(raw_error)
    stage_name = str(stage or "")
    paper = str(paper_id or "")
    theorem = str(theorem_name or "")
    failing = str(failing_lean or "").strip()
    previous = str(previous_attempt or "").strip()
    local = normalize_text(local_context, limit=8000)
    repair_context = normalize_text(
        "\n".join(
            part
            for part in (
                local,
                f"previous_attempt:\n{previous}" if previous else "",
            )
            if part
        ),
        limit=10000,
    )
    diagnostic = parse_lean_error(raw_error)
    sources = [str(x) for x in (source_artifacts or []) if str(x).strip()]
    if source_artifact and str(source_artifact) not in sources:
        sources.insert(0, str(source_artifact))
    rid = str(run_id or default_run_id(stage_name or "repair"))
    row: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "dataset_family": DATASET_FAMILY,
        "row_id": compute_row_id(
            paper_id=paper,
            theorem_name=theorem,
            stage=stage_name,
            failing_lean=failing,
            normalized_error_message=normalized_error,
        ),
        "run_id": rid,
        "pipeline_commit": pipeline_commit or _pipeline_commit(project_root),
        "paper_id": paper,
        "theorem_name": theorem,
        "source_artifact": sources[0] if sources else "",
        "source_artifacts": sources,
        "stage": stage_name,
        "failing_lean": failing,
        "failed_candidate": failing,
        "error_message": raw_error,
        "raw_error_message": diagnostic["raw_error_message"],
        "normalized_error_message": normalized_error,
        "lean_error_kind": diagnostic["lean_error_kind"],
        "primary_identifier": diagnostic["primary_identifier"],
        "line_col": diagnostic["line_col"],
        "local_context": local,
        "repair_prompt_context": repair_context,
        "previous_attempt": previous,
        "successful_repair": str(successful_repair or "").strip(),
        "repair_available": bool(successful_repair) if repair_available is None else bool(repair_available),
        "repair_source": str(repair_source or stage_name or "unknown"),
        "failure_class": failure_class or _KIND_TO_FAILURE_CLASS.get(diagnostic["lean_error_kind"], "other"),
        "lean_file": str(lean_file or ""),
        "model": str(model or ""),
        "timestamp_unix": int(time.time()),
        "toolchain": toolchain_metadata(project_root),
    }
    if extra:
        row["extra"] = extra
    return row


@contextmanager
def _jsonl_lock(path: Path):
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock_handle:
        try:
            import fcntl

            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass
        try:
            yield
        finally:
            try:
                import fcntl

                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


def append_repair_row(out_jsonl: Path, row: dict[str, Any]) -> None:
    """Append one row to the repair dataset, best-effort and side-effect small."""
    append_repair_rows(out_jsonl, [row])


def append_repair_rows(out_jsonl: Path, rows: Iterable[dict[str, Any]]) -> int:
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with _jsonl_lock(out_jsonl):
        with out_jsonl.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                count += 1
    return count


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict):
            rows.append(raw)
    return rows


def _row_source_count(row: dict[str, Any]) -> int:
    sources = row.get("source_artifacts")
    if isinstance(sources, list):
        return len([x for x in sources if str(x).strip()])
    return 1 if str(row.get("source_artifact", "")).strip() else 0


def _dedupe_rank(row: dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        1 if str(row.get("successful_repair", "") or "").strip() else 0,
        _row_source_count(row),
        len(str(row.get("local_context", "") or "")),
        int(row.get("timestamp_unix", 0) or 0),
    )


def _merge_source_artifacts(*rows: dict[str, Any]) -> list[str]:
    merged: list[str] = []
    for row in rows:
        candidates: list[Any] = []
        if isinstance(row.get("source_artifacts"), list):
            candidates.extend(row.get("source_artifacts") or [])
        if row.get("source_artifact"):
            candidates.append(row.get("source_artifact"))
        for item in candidates:
            value = str(item or "").strip()
            if value and value not in merged:
                merged.append(value)
    return merged


def ensure_row_id(row: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with row_id and normalized_error_message populated."""
    out = dict(row)
    raw_error = str(out.get("raw_error_message") or out.get("error_message", "") or "")
    diagnostic = parse_lean_error(raw_error)
    normalized_error = normalize_error_message(out.get("normalized_error_message") or diagnostic["normalized_error_message"])
    diagnostic["normalized_error_message"] = normalized_error
    if not out.get("raw_error_message"):
        out["raw_error_message"] = diagnostic["raw_error_message"]
    out["normalized_error_message"] = normalized_error
    out["lean_error_kind"] = str(out.get("lean_error_kind") or diagnostic["lean_error_kind"])
    out["primary_identifier"] = str(out.get("primary_identifier") or diagnostic["primary_identifier"])
    out["line_col"] = str(out.get("line_col") or diagnostic["line_col"])
    out["failed_candidate"] = str(out.get("failed_candidate") or out.get("failing_lean", "") or "")
    local = normalize_text(str(out.get("local_context", "") or ""), limit=8000)
    previous = str(out.get("previous_attempt", "") or "").strip()
    out["repair_prompt_context"] = str(
        out.get("repair_prompt_context")
        or normalize_text(
            "\n".join(part for part in (local, f"previous_attempt:\n{previous}" if previous else "") if part),
            limit=10000,
        )
    )
    out["repair_source"] = str(out.get("repair_source") or out.get("stage") or "unknown")
    out["failure_class"] = str(
        out.get("failure_class")
        or _KIND_TO_FAILURE_CLASS.get(str(out.get("lean_error_kind", "")), "other")
    )
    out["row_id"] = out.get("row_id") or compute_row_id(
        paper_id=str(out.get("paper_id", "") or ""),
        theorem_name=str(out.get("theorem_name", "") or ""),
        stage=str(out.get("stage", "") or ""),
        failing_lean=str(out.get("failing_lean", "") or ""),
        normalized_error_message=normalized_error,
    )
    sources = _merge_source_artifacts(out)
    out["source_artifacts"] = sources
    out["source_artifact"] = sources[0] if sources else str(out.get("source_artifact", "") or "")
    out["run_id"] = str(out.get("run_id", "") or "")
    out["pipeline_commit"] = str(out.get("pipeline_commit", "") or "unknown")
    return out


def merge_deduped_rows(row_groups: Iterable[Iterable[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Merge rows by row_id, preferring paired and richer rows."""
    by_id: dict[str, dict[str, Any]] = {}
    for group in row_groups:
        for raw in group:
            row = ensure_row_id(raw)
            row_id = str(row.get("row_id", ""))
            if not row_id:
                continue
            existing = by_id.get(row_id)
            if existing is None:
                by_id[row_id] = row
                continue
            best, other = (row, existing) if _dedupe_rank(row) >= _dedupe_rank(existing) else (existing, row)
            merged_sources = _merge_source_artifacts(best, other)
            best = dict(best)
            best["source_artifacts"] = merged_sources
            best["source_artifact"] = merged_sources[0] if merged_sources else str(best.get("source_artifact", "") or "")
            if not str(best.get("successful_repair", "") or "").strip():
                best["successful_repair"] = str(other.get("successful_repair", "") or "")
                best["repair_available"] = bool(best["successful_repair"])
            by_id[row_id] = best
    return sorted(by_id.values(), key=lambda r: (str(r.get("paper_id", "")), str(r.get("theorem_name", "")), str(r.get("row_id", ""))))


def _token_set(text: str) -> set[str]:
    return {
        tok.lower()
        for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_']+", text or "")
        if len(tok) >= 4
    }


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def retrieve_repair_examples(
    *,
    dataset_path: Path,
    error_message: str,
    lean_state: str = "",
    current_draft: str = "",
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Return compact successful repair examples similar to the current error.

    This is intentionally dependency-free. It ranks by failure class first and
    token overlap second so it is safe to use inside hot repair loops.
    """
    if limit <= 0 or not dataset_path.exists():
        return []
    target_class = classify_error(error_message)
    query_tokens = _token_set("\n".join([error_message, lean_state, current_draft]))
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in read_jsonl(dataset_path):
        repair = str(row.get("successful_repair", "") or "").strip()
        if not repair:
            continue
        row_error = str(row.get("normalized_error_message") or row.get("error_message", "") or "")
        row_class = str(row.get("failure_class", "") or classify_error(row_error))
        class_score = 2.0 if row_class == target_class else 0.0
        row_tokens = _token_set(
            "\n".join(
                [
                    row_error,
                    str(row.get("failed_candidate") or row.get("failing_lean", "") or ""),
                    str(row.get("previous_attempt", "") or ""),
                    str(row.get("repair_prompt_context", "") or ""),
                ]
            )
        )
        score = class_score + _jaccard(query_tokens, row_tokens)
        if score <= 0:
            continue
        scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    out: list[dict[str, Any]] = []
    seen_repairs: set[str] = set()
    for _score, row in scored:
        repair = str(row.get("successful_repair", "") or "").strip()
        if not repair or repair in seen_repairs:
            continue
        seen_repairs.add(repair)
        out.append(
            {
                "failure_class": str(row.get("failure_class", "") or classify_error(str(row.get("error_message", "")))),
                "error_message": normalize_text(row.get("normalized_error_message") or row.get("error_message", ""), limit=600),
                "lean_error_kind": str(row.get("lean_error_kind", "") or ""),
                "previous_attempt": str(row.get("previous_attempt", "") or "").strip()[:600],
                "successful_repair": repair[:800],
                "theorem_name": str(row.get("theorem_name", "") or ""),
            }
        )
        if len(out) >= limit:
            break
    return out


def format_repair_examples(examples: list[dict[str, Any]]) -> str:
    """Render examples for insertion into a model repair prompt."""
    if not examples:
        return ""
    blocks: list[str] = []
    for idx, ex in enumerate(examples, start=1):
        parts = [
            f"Example {idx} ({ex.get('failure_class', 'unknown')}):",
            f"Lean error: {ex.get('error_message', '')}",
        ]
        previous = str(ex.get("previous_attempt", "") or "").strip()
        if previous:
            parts.append(f"Previous failed attempt:\n{previous}")
        parts.append(f"Successful repair:\n{ex.get('successful_repair', '')}")
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks)


def summarize_rows(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    row_list = list(rows)
    classes: Counter[str] = Counter(str(row.get("failure_class", "unknown")) for row in row_list)
    kinds: Counter[str] = Counter(str(row.get("lean_error_kind", "unknown")) for row in row_list)
    stages: Counter[str] = Counter(str(row.get("stage", "unknown")) for row in row_list)
    run_ids = sorted({str(row.get("run_id", "")) for row in row_list if str(row.get("run_id", "")).strip()})
    commits = sorted({str(row.get("pipeline_commit", "")) for row in row_list if str(row.get("pipeline_commit", "")).strip()})
    models = sorted({str(row.get("model", "")) for row in row_list if str(row.get("model", "")).strip()})
    lean_toolchains = sorted(
        {
            str((row.get("toolchain") or {}).get("lean_toolchain", ""))
            for row in row_list
            if isinstance(row.get("toolchain"), dict) and str((row.get("toolchain") or {}).get("lean_toolchain", "")).strip()
        }
    )
    source_artifacts: list[str] = []
    for row in row_list:
        for source in _merge_source_artifacts(row):
            if source not in source_artifacts:
                source_artifacts.append(source)
    papers = {str(row.get("paper_id", "")) for row in row_list if str(row.get("paper_id", "")).strip()}
    paired = sum(1 for row in row_list if bool(row.get("successful_repair")))
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset_family": DATASET_FAMILY,
        "rows": len(row_list),
        "paired_repairs": paired,
        "unpaired_failures": len(row_list) - paired,
        "papers": len(papers),
        "run_ids": run_ids,
        "pipeline_commits": commits,
        "models": models,
        "lean_toolchains": lean_toolchains,
        "source_artifacts": source_artifacts[:500],
        "failure_class_counts": dict(classes.most_common()),
        "lean_error_kind_counts": dict(kinds.most_common()),
        "stage_counts": dict(stages.most_common()),
        "core_fields": list(CORE_FIELDS),
        "honest_scope": "DESol-local compiler-feedback repair tuples; not the external APRIL 260k dataset",
    }


def write_summary(rows: Iterable[dict[str, Any]], out_summary: Path) -> dict[str, Any]:
    summary = summarize_rows(rows)
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    out_summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary
