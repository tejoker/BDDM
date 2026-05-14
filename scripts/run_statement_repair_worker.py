#!/usr/bin/env python3
"""Queue-driven statement repair worker.

The worker is dry-run by default. It groups statement-repair rows into
route-specific actions and only mutates ledgers/evidence when --write is set.
"""

from __future__ import annotations

import argparse
import re
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from build_gold_proof_queue import (
    DEFAULT_OUT_JSONL as DEFAULT_GOLD_PROOF_OUT,
    DEFAULT_OUT_SUMMARY as DEFAULT_GOLD_PROOF_SUMMARY,
    build_gold_proof_queue,
)
from build_statement_fidelity_queue import (
    DEFAULT_OUT_JSONL as DEFAULT_FIDELITY_OUT,
    DEFAULT_OUT_SUMMARY as DEFAULT_FIDELITY_SUMMARY,
    build_statement_fidelity_queue,
)
from build_statement_repair_queue import build_statement_repair_queue
from build_statement_review_batch import (
    DEFAULT_OUT_JSONL as DEFAULT_REVIEW_BATCH_OUT,
    DEFAULT_OUT_SUMMARY as DEFAULT_REVIEW_BATCH_SUMMARY,
    DEFAULT_OUT_TEMPLATE as DEFAULT_REVIEW_TEMPLATE_OUT,
    build_statement_review_batch,
    review_batch_exclusion_reasons,
)
from export_corpus import (
    DEFAULT_EVIDENCE_DIR,
    DEFAULT_LEDGER_DIR,
    DEFAULT_OUT_JSONL as DEFAULT_CORPUS_OUT,
    DEFAULT_OUT_SUMMARY as DEFAULT_CORPUS_SUMMARY,
    DEFAULT_REPORT_DIR,
    build_corpus_rows,
)
from statement_validity import classify_statement


DEFAULT_OUT_ACTIONS = Path("output/corpus/statement_repair_worker_actions.jsonl")
DEFAULT_OUT_SUMMARY = Path("output/corpus/statement_repair_worker_summary.json")
DEFAULT_REPAIR_QUEUE_OUT = Path("output/corpus/statement_repair_queue.jsonl")
DEFAULT_REPAIR_QUEUE_SUMMARY = Path("output/corpus/statement_repair_queue_summary.json")
BLOCKING_VALIDITY = {"translation_limited", "bad_translation_artifact"}
WRITE_CAPABLE_ROUTES = {"statement_regeneration", "source_span_repair", "source_translation_recovery"}
DEFAULT_LLM_REPAIR_MODEL = "labs-leanstral-2603"

# Cache for the elaboration gate keyed by
# (paper_id, theorem_name, sha256(candidate_decl)) -> {"ok": bool, "error": str}.
# Identical candidates (e.g. re-validated on retry) are not re-elaborated. The
# cache lives for the worker process lifetime; tests reset it via the public
# `_reset_elaboration_gate_cache` helper below.
_ELABORATION_GATE_CACHE: dict[tuple[str, str, str], dict[str, Any]] = {}


def _signature_hash(decl: str) -> str:
    import hashlib

    return hashlib.sha256((decl or "").strip().encode("utf-8", errors="replace")).hexdigest()[:16]


def _reset_elaboration_gate_cache() -> None:
    """Test hook: clear the elaboration-gate cache between cases."""
    _ELABORATION_GATE_CACHE.clear()


def _run_elaboration_gate(
    *,
    project_root: Path,
    paper_id: str,
    theorem_name: str,
    candidate_decl: str,
    source_file_hint: str = "",
    timeout_s: int = 45,
) -> dict[str, Any]:
    """Elaboration-validity gate for repair candidates. Re-uses the isolated
    `lake env lean` probe from `prove_arxiv_batch._run_isolated_file_check`
    (the same gate the proof-search loop applies before entering MCTS). Results
    are cached by (paper_id, theorem_name, signature_hash) to avoid re-shelling
    the same candidate on retry.

    Returns `{"ok": bool, "error": str, "cache_hit": bool, "signature_hash": str}`.

    The gate is STRICT: a candidate that does not elaborate is rejected,
    regardless of semantic-equivalence verdicts (CoT-judge "reviewed_exact"
    etc.). The semantic check remains a precondition, not a substitute.
    """
    decl = (candidate_decl or "").strip()
    if not decl:
        return {"ok": False, "error": "elaboration_gate_empty_decl", "cache_hit": False, "signature_hash": ""}
    sig = _signature_hash(decl)
    key = (str(paper_id or ""), str(theorem_name or ""), sig)
    cached = _ELABORATION_GATE_CACHE.get(key)
    if cached is not None:
        return {**cached, "cache_hit": True, "signature_hash": sig}

    source_file = Path(source_file_hint) if source_file_hint else (project_root / "output" / f"{paper_id}.lean")
    try:
        from prove_arxiv_batch import _run_isolated_file_check  # type: ignore[import-not-found]
    except Exception as exc:
        # If the helper can't be imported, fall open with a diagnostic so we
        # don't silently block all repair candidates in environments where the
        # prover dependency tree isn't available (e.g. unit-test runs without
        # lake). Callers can still inspect the diagnostic.
        result = {"ok": None, "error": f"elaboration_gate_import_failed:{type(exc).__name__}:{exc}"[:200]}
        _ELABORATION_GATE_CACHE[key] = result
        return {**result, "cache_hit": False, "signature_hash": sig}

    try:
        ok, detail = _run_isolated_file_check(
            project_root=project_root,
            source_file=source_file,
            theorem_decl=decl,
            timeout_s=timeout_s,
        )
    except Exception as exc:
        result = {"ok": False, "error": f"elaboration_gate_exception:{type(exc).__name__}:{exc}"[:200]}
        _ELABORATION_GATE_CACHE[key] = result
        return {**result, "cache_hit": False, "signature_hash": sig}

    result = {"ok": bool(ok), "error": (detail or "")[-600:]}
    _ELABORATION_GATE_CACHE[key] = result
    return {**result, "cache_hit": False, "signature_hash": sig}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


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


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def _path_from_artifacts(row: dict[str, Any], *keys: str) -> str:
    artifacts = row.get("artifact_paths") if isinstance(row.get("artifact_paths"), dict) else {}
    current: Any = artifacts
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    if isinstance(current, str):
        return current
    return ""


def _resolve_path(project_root: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else project_root / path


def _project_path(project_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else project_root / path


def graduation_blockers(row: dict[str, Any]) -> list[str]:
    blockers = [f"review_batch_exclusion:{reason}" for reason in review_batch_exclusion_reasons(row)]
    validity = classify_statement(row)
    if validity.primary_blocker in BLOCKING_VALIDITY:
        blockers.append(f"statement_validity:{validity.primary_blocker}")
    return list(dict.fromkeys(blockers))


def is_graduated_to_review(row: dict[str, Any]) -> bool:
    return not graduation_blockers(row)


def _selected_rows(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            -int(row.get("priority_score", 0) or 0),
            str(row.get("arxiv_id", "")),
            str(row.get("theorem_id", "")),
        ),
    )[:limit]


def _row_id(row: dict[str, Any]) -> str:
    return str(row.get("row_id", "") or "").strip()


def _index_by_row_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {_row_id(row): row for row in rows if _row_id(row)}


def _action_type(route: str) -> str:
    return {
        "statement_regeneration": "translation_repair_pack",
        "source_span_repair": "extracted_theorem_span_repair",
        "source_alignment_review": "source_match_adjudication_queue",
        "source_translation_recovery": "source_retranslation_required",
        "source_alignment_recovery": "source_alignment_recovery",
    }.get(route, "manual_statement_repair")


def _lean_theorem_name_from_statement(statement: str) -> str:
    match = re.search(r"^\s*(?:theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_'.]*)\b", statement or "", flags=re.MULTILINE)
    return match.group(1).rsplit(".", 1)[-1] if match else ""


def _source_match_for_row(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("alignment_evidence") if isinstance(row.get("alignment_evidence"), dict) else {}
    source_match = evidence.get("source_match") if isinstance(evidence.get("source_match"), dict) else {}
    return source_match


def _source_context_for_row(row: dict[str, Any]) -> dict[str, Any]:
    lean_statement = str(row.get("lean_statement", "") or "")
    theorem_name = (
        str(row.get("theorem_name", "") or "").rsplit(".", 1)[-1]
        or _lean_theorem_name_from_statement(lean_statement)
        or str(row.get("theorem_id", "") or "").rsplit(".", 1)[-1]
    )
    return {
        "row_id": str(row.get("row_id", "") or ""),
        "paper_id": str(row.get("arxiv_id", "") or ""),
        "theorem_id": str(row.get("theorem_id", "") or theorem_name),
        "theorem_name": theorem_name,
        "ledger_theorem_name": theorem_name,
        "source_latex": str(row.get("source_latex", "") or ""),
        "normalized_text": str(row.get("normalized_text", "") or ""),
        "lean_statement": lean_statement,
        "status": str(row.get("status", "") or ""),
        "source_span": row.get("source_span", {}) if isinstance(row.get("source_span"), dict) else {},
        "source_span_quality": str(row.get("source_span_quality", "") or ""),
        "source_match": _source_match_for_row(row),
        "context_pack": row.get("context_pack", {}) if isinstance(row.get("context_pack"), dict) else {},
        "artifact_paths": row.get("artifact_paths", {}) if isinstance(row.get("artifact_paths"), dict) else {},
    }


def build_worker_actions(rows: list[dict[str, Any]], *, limit: int = 500) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected = _selected_rows(rows, limit=limit)
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        key = (
            str(row.get("arxiv_id", "")),
            str(row.get("repair_route", "") or "manual_statement_repair"),
            str(row.get("repair_kind", "") or "statement_repair_review"),
        )
        groups[key].append(row)

    actions: list[dict[str, Any]] = []
    for (paper_id, route, repair_kind), group_rows in sorted(groups.items()):
        first = group_rows[0]
        report = _path_from_artifacts(first, "report")
        lean_file = _path_from_artifacts(first, "lean_file") or _path_from_artifacts(first, "out_lean")
        ledger = _path_from_artifacts(first, "ledger") or _path_from_artifacts(first, "reproducibility_bundle", "ledger")
        evidence = _path_from_artifacts(first, "extracted_theorems")
        current_blockers = Counter(reason for row in group_rows for reason in graduation_blockers(row))
        actions.append(
            {
                "schema_version": "statement_repair_worker_action.v1",
                "paper_id": paper_id,
                "repair_route": route,
                "repair_kind": repair_kind,
                "action_type": _action_type(route),
                "write_capable": route in WRITE_CAPABLE_ROUTES,
                "input_rows": len(group_rows),
                "row_ids": [str(row.get("row_id", "")) for row in group_rows],
                "theorem_ids": [str(row.get("theorem_id", "")) for row in group_rows],
                "source_contexts": [_source_context_for_row(row) for row in group_rows],
                "priority_score_max": max(int(row.get("priority_score", 0) or 0) for row in group_rows),
                "artifacts": {
                    "report": report,
                    "lean_file": lean_file,
                    "ledger": ledger,
                    "extracted_theorems": evidence,
                },
                "current_graduation_blockers": dict(current_blockers.most_common()),
                "status": "planned",
            }
        )

    summary = _summarize_actions(actions, rows=selected, write=False)
    return actions, summary


def _summarize_actions(actions: list[dict[str, Any]], *, rows: list[dict[str, Any]], write: bool) -> dict[str, Any]:
    action_counts = Counter(str(action.get("action_type", "")) for action in actions)
    route_counts = Counter(str(action.get("repair_route", "")) for action in actions)
    paper_counts = Counter(str(action.get("paper_id", "")) for action in actions)
    status_counts = Counter(str(action.get("status", "")) for action in actions)
    blocker_counts = Counter(reason for row in rows for reason in graduation_blockers(row))
    graduated = sum(1 for row in rows if is_graduated_to_review(row))
    written_rows = sum(int(action.get("mutated_rows", 0) or 0) for action in actions)
    mutated_groups = sum(1 for action in actions if bool(action.get("mutated")))
    return {
        "schema_version": "statement_repair_worker_summary.v1",
        "write": write,
        "dry_run": not write,
        "input_rows": len(rows),
        "action_groups": len(actions),
        "attempted_rows": sum(int(action.get("input_rows", 0) or 0) for action in actions),
        "written_actions": sum(1 for action in actions if bool(action.get("wrote"))),
        "written_rows": written_rows,
        "mutated_groups": mutated_groups,
        "graduated_rows_before_action": graduated,
        "graduated_rows_before": graduated,
        "still_blocked_rows_before_action": max(0, len(rows) - graduated),
        "still_blocked_rows_before": max(0, len(rows) - graduated),
        "action_type_counts": dict(action_counts.most_common()),
        "repair_route_counts": dict(route_counts.most_common()),
        "per_paper_action_counts": dict(paper_counts.most_common()),
        "per_route": _aggregate_actions(actions, "repair_route"),
        "per_paper": _aggregate_actions(actions, "paper_id"),
        "action_status_counts": dict(status_counts.most_common()),
        "graduation_blocker_counts": dict(blocker_counts.most_common()),
        "still_blocked_reason_counts_before": dict(blocker_counts.most_common()),
        "honest_scope": "Worker orchestration only; repaired/reviewable rows are not proof closure.",
    }


def _aggregate_actions(actions: list[dict[str, Any]], key: str) -> dict[str, dict[str, int]]:
    grouped: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "action_groups": 0,
            "attempted_rows": 0,
            "written_rows": 0,
            "mutated_groups": 0,
        }
    )
    for action in actions:
        name = str(action.get(key, "") or "unknown")
        grouped[name]["action_groups"] += 1
        grouped[name]["attempted_rows"] += int(action.get("input_rows", 0) or 0)
        grouped[name]["written_rows"] += int(action.get("mutated_rows", 0) or 0)
        grouped[name]["mutated_groups"] += int(bool(action.get("mutated")))
    return dict(sorted(grouped.items()))


def _non_mutating_action(action: dict[str, Any], status: str, **extra: Any) -> dict[str, Any]:
    return {
        **action,
        "status": status,
        "wrote": False,
        "mutated": False,
        "mutated_rows": 0,
        **extra,
    }


def _base_name(value: Any) -> str:
    return str(value or "").strip().rsplit(".", 1)[-1]


def _action_target_names(action: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for value in action.get("theorem_ids", []) if isinstance(action.get("theorem_ids"), list) else []:
        base = _base_name(value)
        if base:
            names.add(base)
    contexts = action.get("source_contexts") if isinstance(action.get("source_contexts"), list) else []
    for context in contexts:
        if not isinstance(context, dict):
            continue
        for key in ("ledger_theorem_name", "theorem_name", "theorem_id"):
            base = _base_name(context.get(key))
            if base:
                names.add(base)
        lean_name = _lean_theorem_name_from_statement(str(context.get("lean_statement", "") or ""))
        if lean_name:
            names.add(_base_name(lean_name))
    return names


def _candidate_counts(candidates: list[dict[str, Any]]) -> dict[str, int]:
    def ok(candidate: dict[str, Any]) -> bool:
        return (
            (candidate.get("repair_quality") or {}).get("ok", True) is True
            and (candidate.get("lean_validation") or {}).get("ok") is True
        )

    return {
        "total": len(candidates),
        "changed": sum(1 for c in candidates if c.get("changes")),
        "changed_elaborating": sum(1 for c in candidates if c.get("changes") and ok(c)),
        "paper_claim_abstractions": sum(
            1 for c in candidates if "abstract_schema_placeholder_to_paper_claim" in (c.get("changes") or [])
        ),
        "diagnostic_repair_abstractions": sum(
            1 for c in candidates if c.get("repair_abstraction_kind") == "paper_claim_diagnostic"
        ),
        "faithful_statement_regenerations": sum(
            1 for c in candidates if c.get("statement_repair_kind") == "faithful_statement_regeneration"
        ),
        "failed_validation": sum(1 for c in candidates if (c.get("lean_validation") or {}).get("ok") is False),
        "quality_blocked": sum(1 for c in candidates if (c.get("repair_quality") or {}).get("ok", True) is not True),
        "needs_llm_repair": sum(1 for c in candidates if c.get("needs_llm_repair")),
    }


def _repair_candidate_blocker_counts(candidates: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        quality = candidate.get("repair_quality")
        if isinstance(quality, dict):
            for blocker in quality.get("blockers") or []:
                if str(blocker).strip():
                    counts[f"repair_quality:{blocker}"] += 1
        validation = candidate.get("lean_validation")
        if isinstance(validation, dict) and validation.get("ok") is False:
            error = str(validation.get("error", "") or "")
            if error.startswith("repair_quality_blocked:"):
                continue
            reason = error.splitlines()[0][:160] if error else "lean_validation_failed"
            counts[f"lean_validation:{reason}"] += 1
        if candidate.get("needs_llm_repair"):
            counts["needs_llm_repair"] += 1
    return dict(counts.most_common())


def _candidate_graduation_previews(candidates: list[dict[str, Any]], *, limit: int = 12) -> list[dict[str, Any]]:
    previews: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        quality = candidate.get("repair_quality") if isinstance(candidate.get("repair_quality"), dict) else {}
        validation = candidate.get("lean_validation") if isinstance(candidate.get("lean_validation"), dict) else {}
        review_preview = (
            candidate.get("review_batch_eligibility_preview")
            if isinstance(candidate.get("review_batch_eligibility_preview"), dict)
            else {}
        )
        blockers: list[str] = []
        for blocker in quality.get("blockers") or []:
            if str(blocker).strip():
                blockers.append(f"repair_quality:{blocker}")
        if validation.get("ok") is False:
            error = str(validation.get("error", "") or "lean_validation_failed").splitlines()[0][:160]
            blockers.append(f"lean_validation:{error}")
        for blocker in review_preview.get("blockers") or []:
            raw_blocker = str(blocker).strip()
            if raw_blocker:
                clean_blocker = raw_blocker
                if clean_blocker.startswith("review_batch_exclusion:"):
                    clean_blocker = clean_blocker.split(":", 1)[1]
                blockers.append(f"review_batch:{clean_blocker}")
        previews.append(
            {
                "theorem_name": str(candidate.get("theorem_name", "") or ""),
                "reviewable": bool(review_preview.get("eligible"))
                and quality.get("ok", True) is True
                and validation.get("ok") is True,
                "repair_quality_ok": quality.get("ok", None),
                "lean_validation_ok": validation.get("ok", None),
                "review_batch_eligible": review_preview.get("eligible", None),
                "blockers": list(dict.fromkeys(blockers)),
                "regeneration_protocol": str(candidate.get("regeneration_protocol", "") or ""),
                "statement_repair_kind": str(candidate.get("statement_repair_kind", "") or ""),
            }
        )
    previews.sort(
        key=lambda item: (
            not bool(item.get("reviewable")),
            len(item.get("blockers", [])) if isinstance(item.get("blockers"), list) else 999,
            str(item.get("theorem_name", "")),
        )
    )
    return previews[:limit]


def _candidate_is_write_eligible(candidate: dict[str, Any]) -> bool:
    quality = candidate.get("repair_quality") if isinstance(candidate.get("repair_quality"), dict) else {}
    validation = candidate.get("lean_validation") if isinstance(candidate.get("lean_validation"), dict) else {}
    preview = (
        candidate.get("review_batch_eligibility_preview")
        if isinstance(candidate.get("review_batch_eligibility_preview"), dict)
        else {}
    )
    return (
        bool(candidate.get("changes"))
        and quality.get("ok", True) is True
        and validation.get("ok") is True
        and preview.get("eligible") is True
    )


def _write_eligible_repair_payload(repair_payload: dict[str, Any]) -> dict[str, Any]:
    candidates = repair_payload.get("repair_candidates", [])
    if not isinstance(candidates, list):
        return repair_payload
    eligible = [candidate for candidate in candidates if isinstance(candidate, dict) and _candidate_is_write_eligible(candidate)]
    out = dict(repair_payload)
    out["repair_candidates"] = eligible
    out["candidate_counts"] = _candidate_counts(eligible)
    out["write_eligibility_filter"] = {
        "input_candidate_count": len(candidates),
        "eligible_candidate_count": len(eligible),
        "policy": "repair_quality_ok_and_lean_validation_ok_and_review_batch_eligible",
    }
    return out


def _repair_payload_for_action(repair_payload: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    targets = _action_target_names(action)
    if not targets:
        return repair_payload
    candidates = repair_payload.get("repair_candidates", [])
    if not isinstance(candidates, list):
        return repair_payload
    filtered = [
        c
        for c in candidates
        if isinstance(c, dict) and _base_name(c.get("theorem_name")) in targets
    ]
    out = dict(repair_payload)
    out["repair_candidates"] = filtered
    out["candidate_counts"] = _candidate_counts(filtered)
    out["worker_candidate_filter"] = {
        "target_names": sorted(targets),
        "input_candidate_count": len(candidates),
        "filtered_candidate_count": len(filtered),
    }
    return out


def apply_validated_repair_pack_to_ledger(
    *,
    ledger_path: Path,
    repair_payload: dict[str, Any],
    write: bool,
) -> dict[str, Any]:
    payload = _read_json(ledger_path)
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "invalid_ledger_json", "ledger": str(ledger_path)}
    entries = payload.get("entries", [])
    if not isinstance(entries, list):
        return {"ok": False, "reason": "ledger_entries_missing", "ledger": str(ledger_path)}
    from formalize_paper_full import _apply_validated_translation_repairs

    updated_entries, summary = _apply_validated_translation_repairs(entries, repair_payload)
    if write:
        out = dict(payload)
        out["entries"] = updated_entries
        _write_json(ledger_path, out)
    return {"ok": True, "ledger": str(ledger_path), "wrote": write, **summary}


def _context_by_theorem(action: dict[str, Any]) -> dict[str, dict[str, Any]]:
    contexts = action.get("source_contexts") if isinstance(action.get("source_contexts"), list) else []
    out: dict[str, dict[str, Any]] = {}
    for context in contexts:
        if not isinstance(context, dict):
            continue
        for key in (
            context.get("ledger_theorem_name"),
            context.get("theorem_name"),
            context.get("theorem_id"),
        ):
            base = _base_name(key)
            if base:
                out.setdefault(base, context)
    return out


def _preview_row_for_candidate(context: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    paper_theory_debt = candidate.get("paper_theory_debt") if isinstance(candidate.get("paper_theory_debt"), list) else []
    row = {
        "row_id": context.get("row_id", ""),
        "arxiv_id": context.get("paper_id", ""),
        "theorem_id": context.get("theorem_id", ""),
        "canonical_theorem_id": "",
        "status": "UNRESOLVED",
        "lean_statement": candidate.get("repaired_decl", ""),
        "source_latex": context.get("source_latex", ""),
        "normalized_text": context.get("normalized_text", ""),
        "source_span": context.get("source_span", {}),
        "source_span_quality": context.get("source_span_quality", ""),
        "alignment_evidence": {"source_match": context.get("source_match", {})},
        "statement_alignment_class": "partial",
        "alignment_confidence": 0.5,
        "alignment_gold_eligible": False,
        "claim_equivalence_verdict": "unclear",
        "identity_status": "unknown",
        "gate_failures": ["lean_proof_closed"],
        "axiom_debt": [str(item) for item in paper_theory_debt if str(item).strip()],
        "translation_repair": {
            "statement_repair_kind": candidate.get("statement_repair_kind", ""),
            "regeneration_protocol": candidate.get("regeneration_protocol", ""),
        },
    }
    return row


def _attach_review_batch_previews(repair_payload: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    contexts = _context_by_theorem(action)
    candidates = repair_payload.get("repair_candidates", [])
    if not isinstance(candidates, list):
        return repair_payload
    out_candidates: list[dict[str, Any]] = []
    for raw in candidates:
        candidate = dict(raw) if isinstance(raw, dict) else {}
        context = contexts.get(_base_name(candidate.get("theorem_name")), {})
        if not context:
            out_candidates.append(candidate)
            continue
        preview_row = _preview_row_for_candidate(context, candidate)
        blockers = graduation_blockers(preview_row)
        preview = {
            "eligible": not blockers,
            "blockers": blockers,
            "status": "reviewable" if not blockers else "blocked",
        }
        candidate["review_batch_eligibility_preview"] = preview
        if blockers:
            quality = candidate.get("repair_quality") if isinstance(candidate.get("repair_quality"), dict) else {}
            quality_blockers = list(quality.get("blockers") or [])
            quality_blockers.extend(f"review_batch_preview:{blocker}" for blocker in blockers)
            candidate["repair_quality"] = {
                **quality,
                "ok": False,
                "blockers": list(dict.fromkeys(quality_blockers)),
            }
            validation = candidate.get("lean_validation") if isinstance(candidate.get("lean_validation"), dict) else {}
            if validation.get("ok") is True or validation.get("ok") is None:
                candidate["lean_validation"] = {
                    "ok": False,
                    "error": "review_batch_preview_blocked:" + ",".join(blockers),
                    "review_batch_eligibility_preview": preview,
                    "lean_validation_without_review_preview": validation,
                }
        out_candidates.append(candidate)
    out = dict(repair_payload)
    out["repair_candidates"] = out_candidates
    out["candidate_counts"] = _candidate_counts(out_candidates)
    return out


def _build_source_backed_payload_for_action(
    action: dict[str, Any],
    *,
    project_root: Path,
    repair_output_root: Path,
    validate_candidates: bool,
    use_llm_repair: bool = False,
    llm_repair_client: Any = None,
    llm_repair_model: str = DEFAULT_LLM_REPAIR_MODEL,
    llm_repair_max_rounds: int = 3,
) -> dict[str, Any]:
    from repair_bad_translations import build_source_backed_repair_payload

    paper_id = str(action.get("paper_id", ""))
    source_contexts = action.get("source_contexts") if isinstance(action.get("source_contexts"), list) else []
    out_dir = repair_output_root / paper_id.replace(".", "_") / str(action.get("repair_kind", "source_backed_v2"))
    payload = build_source_backed_repair_payload(
        paper_id=paper_id,
        project_root=project_root,
        source_contexts=[ctx for ctx in source_contexts if isinstance(ctx, dict)],
        out_dir=out_dir,
        validate_candidates=validate_candidates,
    )
    if use_llm_repair:
        payload = _overlay_llm_repair_candidates(
            payload,
            action=action,
            project_root=project_root,
            client=llm_repair_client,
            model=llm_repair_model,
            validate_candidates=validate_candidates,
            max_repair_rounds=llm_repair_max_rounds,
        )
    payload = _repair_payload_for_action(payload, action)
    return _attach_review_batch_previews(payload, action)


def _summarize_llm_repair_per_row(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-row LLM-repair retry telemetry suitable for surfacing in the action
    output. Each entry includes the theorem name, retry-round count, whether
    the final candidate elaborated, and a short summary of each round.

    Only candidates with an `llm_repair` block (i.e. the LLM overlay was
    attempted) are included; rule-based-only rows return nothing.
    """
    rows: list[dict[str, Any]] = []
    for cand in candidates or []:
        if not isinstance(cand, dict):
            continue
        llm = cand.get("llm_repair")
        if not isinstance(llm, dict):
            continue
        history = llm.get("retry_history") if isinstance(llm.get("retry_history"), list) else []
        compact_history = [
            {
                "round": h.get("round"),
                "elaboration_ok": h.get("elaboration_ok"),
                "lean_error_head": (str(h.get("lean_error_tail") or "")[-200:]).replace("\n", " "),
            }
            for h in history
            if isinstance(h, dict)
        ]
        rows.append(
            {
                "theorem_name": cand.get("theorem_name", ""),
                "llm_ok": bool(llm.get("ok")),
                "retry_rounds": int(llm.get("retry_rounds", 1) or 1),
                "final_elaboration_ok": bool((llm.get("elaboration_gate") or {}).get("ok")) if llm.get("ok") else False,
                "retry_history": compact_history,
                "reason": llm.get("reason", ""),
            }
        )
    return rows


def _overlay_llm_repair_candidates(
    payload: dict[str, Any],
    *,
    action: dict[str, Any],
    project_root: Path,
    client: Any,
    model: str,
    validate_candidates: bool,
    max_repair_rounds: int = 3,
) -> dict[str, Any]:
    """Replace each candidate's `repaired_decl` with an LLM-generated signature
    when (a) the source_latex is non-empty and (b) the LLM output passes the
    trivialization / placeholder gate. The legacy rule-based candidate is
    preserved under `rule_based_candidate_before_llm` for diagnostics.

    LLM repair is HIGHER priority than the rule-based path: when both succeed,
    the LLM output wins. When the LLM fails (refusal, rejection, transport
    error) the rule-based candidate is kept unmodified.
    """
    if client is None:
        return payload
    try:
        from llm_statement_repair import (  # type: ignore[import-not-found]
            extract_paper_theory_hint,
            generate_llm_repair_candidate,
        )
        from repair_bad_translations import validate_repair_candidate  # type: ignore[import-not-found]
    except Exception:
        return payload

    paper_id = str(action.get("paper_id", ""))
    contexts = _context_by_theorem(action)
    candidates = payload.get("repair_candidates", [])
    if not isinstance(candidates, list):
        return payload

    safe_id = paper_id.replace(".", "_")
    theory_path = project_root / "Desol" / "PaperTheory" / f"Paper_{safe_id}.lean"
    paper_theory_hint = extract_paper_theory_hint(theory_path) if theory_path.exists() else ""

    artifacts = action.get("artifacts") if isinstance(action.get("artifacts"), dict) else {}
    lean_file_hint = str(artifacts.get("lean_file", "") or "")

    out_candidates: list[dict[str, Any]] = []
    llm_counters = {
        "attempted": 0,
        "accepted": 0,
        "rejected": 0,
        "skipped_no_source": 0,
        "validated_ok": 0,
        "elaboration_gate_rejected": 0,
        "retry_rounds_total": 0,
        "retry_rounds_max_observed": 0,
        "retry_recovered": 0,
    }
    for raw in candidates:
        candidate = dict(raw) if isinstance(raw, dict) else {}
        theorem_name = str(candidate.get("theorem_name", "") or "")
        context = contexts.get(_base_name(theorem_name), {})
        source_latex = str(context.get("source_latex", "") or candidate.get("source_statement_excerpt", "") or "")
        if not source_latex.strip():
            llm_counters["skipped_no_source"] += 1
            out_candidates.append(candidate)
            continue
        llm_counters["attempted"] += 1

        # Build an elaboration-validator closure for the retry loop. Re-uses
        # the same isolated `lake env lean` probe as the post-call gate, so the
        # gate's cache absorbs the duplicate lookup. When `validate_candidates`
        # is False we pass None — disabling retry — because there's nothing
        # informative to feed back to the LLM.
        _validate_elab: Optional[Any] = None
        if validate_candidates and max_repair_rounds > 1:
            def _validate_elab_closure(
                cand_decl: str,
                _paper_id: str = paper_id,
                _theorem_name: str = theorem_name,
                _lean_file_hint: str = lean_file_hint,
            ) -> tuple[bool, str]:
                gate = _run_elaboration_gate(
                    project_root=project_root,
                    paper_id=_paper_id,
                    theorem_name=_theorem_name,
                    candidate_decl=cand_decl,
                    source_file_hint=_lean_file_hint,
                )
                ok_val = bool(gate.get("ok"))
                err_tail = str(gate.get("error") or "")
                return ok_val, err_tail
            _validate_elab = _validate_elab_closure

        try:
            llm_result = generate_llm_repair_candidate(
                source_latex=source_latex,
                paper_id=paper_id,
                theorem_name=theorem_name,
                paper_theory_hint=paper_theory_hint,
                client=client,
                model=model,
                max_repair_rounds=max_repair_rounds,
                validate_elaboration=_validate_elab,
            )
        except Exception as exc:
            candidate["llm_repair"] = {
                "ok": False,
                "error": f"{type(exc).__name__}:{exc}"[:200],
            }
            out_candidates.append(candidate)
            continue

        # Aggregate retry telemetry (also for rows that ultimately fail).
        rounds_used = int((llm_result or {}).get("retry_rounds", 1) or 1)
        llm_counters["retry_rounds_total"] += rounds_used
        if rounds_used > llm_counters["retry_rounds_max_observed"]:
            llm_counters["retry_rounds_max_observed"] = rounds_used

        if not llm_result or not llm_result.get("repaired_decl"):
            candidate["llm_repair"] = {
                "ok": False,
                "result": llm_result or {"error": "empty_or_none"},
                "retry_rounds": rounds_used,
                "retry_history": (llm_result or {}).get("retry_history", []),
            }
            llm_counters["rejected"] += 1
            if "elaboration_gate_after_retry" in ((llm_result or {}).get("rejected") or []):
                llm_counters["elaboration_gate_rejected"] += 1
            out_candidates.append(candidate)
            continue

        new_decl = str(llm_result.get("repaired_decl") or "")
        validation: dict[str, Any] = {"ok": None, "error": "validation_skipped"}
        if validate_candidates:
            try:
                validation = validate_repair_candidate(
                    project_root=project_root,
                    paper_id=paper_id,
                    decl=new_decl,
                )
            except Exception as exc:
                validation = {"ok": False, "error": f"validation_exception:{type(exc).__name__}:{exc}"[:200]}

        if validate_candidates and not (validation or {}).get("ok"):
            candidate["llm_repair"] = {
                "ok": False,
                "result": llm_result,
                "lean_validation_for_llm_candidate": validation,
                "reason": "llm_candidate_failed_lean_validation",
            }
            llm_counters["rejected"] += 1
            out_candidates.append(candidate)
            continue

        # Elaboration-validity gate (Round-III regression fix).
        #
        # The repair worker historically accepted any candidate that passed the
        # semantic CoT judge + the `validate_repair_candidate` paper-theory
        # probe. But the paper-theory probe runs against `Paper_<id>_Repair`,
        # which auto-synthesizes missing symbols — masking failures the actual
        # proof-search gate (`_run_isolated_file_check` against the canonical
        # paper prelude) will then re-raise as `validation_gate_elaboration_failed`.
        #
        # Run the SAME isolated probe the prover uses, so a repair candidate
        # that won't elaborate downstream is rejected here before we mutate the
        # ledger. The semantic check stays as a precondition; this is additive.
        elaboration_gate: dict[str, Any] = {"ok": None, "error": "elaboration_gate_skipped"}
        if validate_candidates:
            elaboration_gate = _run_elaboration_gate(
                project_root=project_root,
                paper_id=paper_id,
                theorem_name=theorem_name,
                candidate_decl=new_decl,
                source_file_hint=lean_file_hint,
            )
        if validate_candidates and elaboration_gate.get("ok") is False:
            candidate["llm_repair"] = {
                "ok": False,
                "result": llm_result,
                "lean_validation_for_llm_candidate": validation,
                "elaboration_gate": elaboration_gate,
                "reason": "llm_candidate_failed_elaboration_gate",
                "elaboration_gate_failed": (elaboration_gate.get("error") or "")[-300:],
            }
            llm_counters["rejected"] += 1
            llm_counters["elaboration_gate_rejected"] = (
                llm_counters.get("elaboration_gate_rejected", 0) + 1
            )
            out_candidates.append(candidate)
            continue

        # Promote LLM candidate.
        candidate["rule_based_candidate_before_llm"] = {
            "repaired_decl": candidate.get("repaired_decl"),
            "changes": candidate.get("changes"),
            "structured_translation": candidate.get("structured_translation"),
            "lean_validation": candidate.get("lean_validation"),
            "repair_quality": candidate.get("repair_quality"),
        }
        candidate["repaired_decl"] = new_decl
        candidate["statement_repair_kind"] = "llm_statement_repair"
        candidate["regeneration_protocol"] = "llm_statement_repair_v1"
        new_changes = list(candidate.get("changes") or [])
        if "llm_statement_repair_v1" not in new_changes:
            new_changes.append("llm_statement_repair_v1")
        candidate["changes"] = new_changes
        if validate_candidates:
            candidate["lean_validation"] = validation
            llm_counters["validated_ok"] += 1
        else:
            candidate["lean_validation"] = {"ok": None, "error": "validation_skipped"}
        candidate["repair_quality"] = {
            "ok": True,
            "blockers": [],
            "protocol": "llm_statement_repair_v1",
            "source_backed": True,
        }
        candidate["elaboration_gate"] = elaboration_gate
        candidate["llm_repair"] = {
            "ok": True,
            "result": llm_result,
            "lean_validation_for_llm_candidate": validation,
            "elaboration_gate": elaboration_gate,
            "retry_rounds": rounds_used,
            "retry_history": (llm_result or {}).get("retry_history", []),
        }
        llm_counters["accepted"] += 1
        if rounds_used > 1:
            llm_counters["retry_recovered"] += 1
        out_candidates.append(candidate)

    out = dict(payload)
    out["repair_candidates"] = out_candidates
    out["candidate_counts"] = _candidate_counts(out_candidates)
    out["llm_repair_overlay"] = {
        "protocol": "llm_statement_repair_v1",
        "model": model,
        **llm_counters,
    }
    return out


def _apply_source_backed_payload(
    action: dict[str, Any],
    *,
    project_root: Path,
    write: bool,
    repair_output_root: Path,
    validate_candidates: bool,
    no_change_status: str,
    written_status: str,
    use_llm_repair: bool = False,
    llm_repair_client: Any = None,
    llm_repair_model: str = DEFAULT_LLM_REPAIR_MODEL,
    llm_repair_max_rounds: int = 3,
) -> dict[str, Any]:
    artifacts = action.get("artifacts") if isinstance(action.get("artifacts"), dict) else {}
    ledger = str(artifacts.get("ledger", "") or "")
    if not ledger:
        return _non_mutating_action(action, "skipped_missing_repair_artifacts")
    source_contexts = action.get("source_contexts") if isinstance(action.get("source_contexts"), list) else []
    if not source_contexts:
        return _non_mutating_action(action, "skipped_missing_source_contexts")

    repair_payload = _build_source_backed_payload_for_action(
        action,
        project_root=project_root,
        repair_output_root=repair_output_root,
        validate_candidates=validate_candidates,
        use_llm_repair=use_llm_repair,
        llm_repair_client=llm_repair_client,
        llm_repair_model=llm_repair_model,
        llm_repair_max_rounds=llm_repair_max_rounds,
    )
    candidates = repair_payload.get("repair_candidates", [])
    if not isinstance(candidates, list):
        candidates = []
    candidate_previews = _candidate_graduation_previews(candidates)
    repair_blockers = _repair_candidate_blocker_counts(candidates)
    write_payload = _write_eligible_repair_payload(repair_payload)
    eligible_count = int((write_payload.get("write_eligibility_filter") or {}).get("eligible_candidate_count", 0) or 0)

    # Surface LLM-repair retry telemetry (per-row + aggregate) into the action
    # so smoke / audit / regression runs can inspect retry behavior without
    # having to re-derive it from the on-disk repair_pack json (which is
    # written by `build_source_backed_repair_payload` BEFORE the LLM overlay).
    llm_repair_overlay = repair_payload.get("llm_repair_overlay") or {}
    llm_repair_per_row = _summarize_llm_repair_per_row(candidates)

    if not write:
        return _non_mutating_action(
            action,
            "dry_run_source_backed_preview",
            repair_summary=repair_payload.get("candidate_counts", {}),
            repair_blocker_counts=repair_blockers,
            candidate_graduation_preview=candidate_previews,
            write_eligibility_filter=write_payload.get("write_eligibility_filter", {}),
            llm_repair_overlay=llm_repair_overlay,
            llm_repair_per_row=llm_repair_per_row,
            wrote_repair_pack=True,
            regeneration_protocol="source_backed_v2",
        )
    if eligible_count <= 0:
        return _non_mutating_action(
            action,
            no_change_status,
            repair_summary=repair_payload.get("candidate_counts", {}),
            repair_blocker_counts=repair_blockers,
            candidate_graduation_preview=candidate_previews,
            write_eligibility_filter=write_payload.get("write_eligibility_filter", {}),
            llm_repair_overlay=llm_repair_overlay,
            llm_repair_per_row=llm_repair_per_row,
            wrote_repair_pack=True,
            regeneration_protocol="source_backed_v2",
        )
    dry_apply = apply_validated_repair_pack_to_ledger(
        ledger_path=_resolve_path(project_root, ledger),
        repair_payload=write_payload,
        write=False,
    )
    updated_count = int(dry_apply.get("updated_count", 0) or 0)
    if updated_count <= 0:
        return _non_mutating_action(
            action,
            no_change_status,
            repair_summary=repair_payload.get("candidate_counts", {}),
            repair_blocker_counts=repair_blockers,
            candidate_graduation_preview=candidate_previews,
            write_eligibility_filter=write_payload.get("write_eligibility_filter", {}),
            llm_repair_overlay=llm_repair_overlay,
            llm_repair_per_row=llm_repair_per_row,
            ledger_application=dry_apply,
            wrote_repair_pack=True,
            regeneration_protocol="source_backed_v2",
        )

    ledger_result = apply_validated_repair_pack_to_ledger(
        ledger_path=_resolve_path(project_root, ledger),
        repair_payload=write_payload,
        write=True,
    )
    final_updated_count = int(ledger_result.get("updated_count", 0) or 0)
    return {
        **action,
        "status": written_status,
        "wrote": True,
        "mutated": final_updated_count > 0,
        "mutated_rows": final_updated_count,
        "repair_summary": repair_payload.get("candidate_counts", {}),
        "repair_blocker_counts": repair_blockers,
        "candidate_graduation_preview": candidate_previews,
        "write_eligibility_filter": write_payload.get("write_eligibility_filter", {}),
        "llm_repair_overlay": llm_repair_overlay,
        "llm_repair_per_row": llm_repair_per_row,
        "ledger_application": ledger_result,
        "regeneration_protocol": "source_backed_v2",
    }


def _execute_statement_regeneration(
    action: dict[str, Any],
    *,
    project_root: Path,
    write: bool,
    repair_output_root: Path,
    validate_candidates: bool,
    use_llm_repair: bool = False,
    llm_repair_client: Any = None,
    llm_repair_model: str = DEFAULT_LLM_REPAIR_MODEL,
    llm_repair_max_rounds: int = 3,
) -> dict[str, Any]:
    artifacts = action.get("artifacts") if isinstance(action.get("artifacts"), dict) else {}
    report = str(artifacts.get("report", "") or "")
    lean_file = str(artifacts.get("lean_file", "") or "")
    ledger = str(artifacts.get("ledger", "") or "")
    if not (report and lean_file and ledger):
        return _non_mutating_action(action, "skipped_missing_repair_artifacts")
    if action.get("source_contexts"):
        return _apply_source_backed_payload(
            action,
            project_root=project_root,
            write=write,
            repair_output_root=repair_output_root,
            validate_candidates=validate_candidates,
            no_change_status="source_backed_regeneration_no_ledger_change",
            written_status="written_source_backed_regeneration",
            use_llm_repair=use_llm_repair,
            llm_repair_client=llm_repair_client,
            llm_repair_model=llm_repair_model,
            llm_repair_max_rounds=llm_repair_max_rounds,
        )
    if not write:
        return _non_mutating_action(action, "dry_run_write_required")
    from repair_bad_translations import build_repair_pack

    paper_id = str(action.get("paper_id", ""))
    out_dir = repair_output_root / paper_id.replace(".", "_") / str(action.get("repair_kind", "statement_regeneration"))
    repair_payload = build_repair_pack(
        paper_id=paper_id,
        report_path=_resolve_path(project_root, report),
        lean_file=_resolve_path(project_root, lean_file),
        project_root=project_root,
        out_dir=out_dir,
        validate_candidates=validate_candidates,
    )
    repair_payload = _repair_payload_for_action(repair_payload, action)
    dry_apply = apply_validated_repair_pack_to_ledger(
        ledger_path=_resolve_path(project_root, ledger),
        repair_payload=repair_payload,
        write=False,
    )
    updated_count = int(dry_apply.get("updated_count", 0) or 0)
    if updated_count <= 0:
        candidates = repair_payload.get("repair_candidates", [])
        if not isinstance(candidates, list):
            candidates = []
        return _non_mutating_action(
            action,
            "generated_repair_pack_no_ledger_change",
            repair_summary=repair_payload.get("candidate_counts", {}),
            repair_blocker_counts=_repair_candidate_blocker_counts(candidates),
            ledger_application=dry_apply,
            wrote_repair_pack=True,
        )

    ledger_result = apply_validated_repair_pack_to_ledger(
        ledger_path=_resolve_path(project_root, ledger),
        repair_payload=repair_payload,
        write=True,
    )
    final_updated_count = int(ledger_result.get("updated_count", 0) or 0)
    return {
        **action,
        "status": "written_translation_repair_pack",
        "wrote": True,
        "mutated": final_updated_count > 0,
        "mutated_rows": final_updated_count,
        "repair_summary": repair_payload.get("candidate_counts", {}),
        "ledger_application": ledger_result,
    }


def _execute_source_span_repair(action: dict[str, Any], *, project_root: Path, write: bool) -> dict[str, Any]:
    artifacts = action.get("artifacts") if isinstance(action.get("artifacts"), dict) else {}
    evidence = str(artifacts.get("extracted_theorems", "") or "")
    if not evidence:
        return _non_mutating_action(action, "skipped_missing_extracted_theorems")
    if not write:
        return _non_mutating_action(action, "dry_run_write_required")
    from repair_extracted_theorem_spans import repair_file

    path = _resolve_path(project_root, evidence)
    dry_result = repair_file(path, project_root=project_root, write=False)
    repaired_rows = int(dry_result.get("repaired_rows", 0) or 0)
    if repaired_rows <= 0:
        return _non_mutating_action(action, "checked_source_span_no_change", span_repair=dry_result)
    result = repair_file(path, project_root=project_root, write=True)
    final_repaired_rows = int(result.get("repaired_rows", 0) or 0)
    return {
        **action,
        "status": "written_source_span_repair",
        "wrote": True,
        "mutated": final_repaired_rows > 0,
        "mutated_rows": final_repaired_rows,
        "span_repair": result,
    }


def _execute_source_translation_recovery(
    action: dict[str, Any],
    *,
    project_root: Path,
    write: bool,
    repair_output_root: Path,
    validate_candidates: bool,
    use_llm_repair: bool = False,
    llm_repair_client: Any = None,
    llm_repair_model: str = DEFAULT_LLM_REPAIR_MODEL,
    llm_repair_max_rounds: int = 3,
) -> dict[str, Any]:
    if not write and not action.get("source_contexts"):
        return _non_mutating_action(
            action,
            "dry_run_source_retranslation_required",
            translation_recovery_policy={
                "write_capable": True,
                "allowed_auto_write": "only_after_validation_and_review_batch_preview",
                "required_exit_gate": "validator_passing_and_review_batch_eligible",
                "proof_promotion": False,
            },
        )
    result = _apply_source_backed_payload(
        action,
        project_root=project_root,
        write=write,
        repair_output_root=repair_output_root,
        validate_candidates=validate_candidates,
        no_change_status="source_retranslation_no_ledger_change",
        written_status="written_source_retranslation_candidate",
        use_llm_repair=use_llm_repair,
        llm_repair_client=llm_repair_client,
        llm_repair_model=llm_repair_model,
        llm_repair_max_rounds=llm_repair_max_rounds,
    )
    if not write:
        result["translation_recovery_policy"] = {
            "write_capable": True,
            "allowed_auto_write": "only_after_validation_and_review_batch_preview",
            "required_exit_gate": "validator_passing_and_review_batch_eligible",
            "proof_promotion": False,
        }
    return result


def execute_worker_actions(
    actions: list[dict[str, Any]],
    *,
    project_root: Path,
    write: bool,
    max_write_groups: int,
    repair_output_root: Path,
    validate_candidates: bool,
    use_llm_repair: bool = False,
    llm_repair_client: Any = None,
    llm_repair_model: str = DEFAULT_LLM_REPAIR_MODEL,
    llm_repair_max_rounds: int = 3,
    rewrite_lean_files: bool = True,
) -> list[dict[str, Any]]:
    executed: list[dict[str, Any]] = []
    write_groups = 0
    # Track which papers had ledger mutations so we rewrite each .lean file
    # exactly once (multiple actions per paper share a single rewrite).
    rewritten_paper_ids: set[str] = set()
    for action in actions:
        route = str(action.get("repair_route", ""))
        if write and action.get("write_capable") and write_groups >= max_write_groups:
            executed.append(_non_mutating_action(action, "skipped_write_limit"))
            continue
        if route == "statement_regeneration":
            result = _execute_statement_regeneration(
                action,
                project_root=project_root,
                write=write,
                repair_output_root=repair_output_root,
                validate_candidates=validate_candidates,
                use_llm_repair=use_llm_repair,
                llm_repair_client=llm_repair_client,
                llm_repair_model=llm_repair_model,
                llm_repair_max_rounds=llm_repair_max_rounds,
            )
        elif route == "source_span_repair":
            result = _execute_source_span_repair(action, project_root=project_root, write=write)
        elif route == "source_alignment_review":
            result = _non_mutating_action(action, "queued_source_match_adjudication")
        elif route == "source_translation_recovery":
            result = _execute_source_translation_recovery(
                action,
                project_root=project_root,
                write=write,
                repair_output_root=repair_output_root,
                validate_candidates=validate_candidates,
                use_llm_repair=use_llm_repair,
                llm_repair_client=llm_repair_client,
                llm_repair_model=llm_repair_model,
                llm_repair_max_rounds=llm_repair_max_rounds,
            )
        else:
            result = _non_mutating_action(action, "queued_manual_repair")
        if write and bool(result.get("mutated")):
            write_groups += 1
            paper_id = str(result.get("paper_id", "") or action.get("paper_id", "") or "").strip()
            if rewrite_lean_files and paper_id and paper_id not in rewritten_paper_ids:
                rewrite_summary = _rewrite_lean_file_for_paper(paper_id, project_root=project_root)
                if rewrite_summary is not None:
                    result = {**result, "lean_file_rewrite": rewrite_summary}
                    rewritten_paper_ids.add(paper_id)
        executed.append(result)
    return executed


def _rewrite_lean_file_for_paper(paper_id: str, *, project_root: Path) -> dict[str, Any] | None:
    """Regenerate `output/<paper_id>.lean` from the (just-mutated) ledger.

    Ledger upgrades from the repair worker can both (a) replace an existing
    placeholder theorem with a richer signature and (b) introduce brand-new
    theorem names that the original translator never emitted. Without this
    pass, the per-paper .lean file is stale w.r.t. the ledger and downstream
    proof search reads outdated content.

    Errors are non-fatal — the ledger write already succeeded, so a rewrite
    failure must not roll back the action. We surface the error in the
    returned summary instead.
    """
    try:
        from rewrite_lean_from_ledger import rewrite_paper
    except ImportError as exc:
        return {"ok": False, "paper_id": paper_id, "error": f"import_failed:{exc}"}
    try:
        summary = rewrite_paper(
            paper_id,
            project_root=project_root,
            write=True,
            append_missing=True,
        )
    except Exception as exc:  # pragma: no cover — defensive shield around the rewrite.
        return {"ok": False, "paper_id": paper_id, "error": f"{type(exc).__name__}:{exc}"}
    return {"ok": True, **summary}


def _action_write_paths(action: dict[str, Any], project_root: Path) -> list[Path]:
    artifacts = action.get("artifacts") if isinstance(action.get("artifacts"), dict) else {}
    paths: list[Path] = []
    for key in ("ledger", "extracted_theorems"):
        raw = str(artifacts.get(key, "") or "")
        if raw:
            paths.append(_resolve_path(project_root, raw))
    # Include the per-paper `output/<paper>.lean` file so rollback can also
    # restore the lean-file rewrite triggered after a ledger mutation. We
    # snapshot it unconditionally for write-capable actions (the path may not
    # exist for some papers — `_snapshot_write_artifacts` handles that).
    paper_id = str(action.get("paper_id", "") or "").strip()
    if paper_id:
        paths.append(project_root / "output" / f"{paper_id}.lean")
    return paths


def _snapshot_write_artifacts(actions: list[dict[str, Any]], project_root: Path) -> dict[Path, str | None]:
    snapshots: dict[Path, str | None] = {}
    for action in actions:
        if not action.get("write_capable"):
            continue
        for path in _action_write_paths(action, project_root):
            if path in snapshots:
                continue
            try:
                snapshots[path] = path.read_text(encoding="utf-8")
            except OSError:
                snapshots[path] = None
    return snapshots


def _mutated_snapshots(
    snapshots: dict[Path, str | None],
    actions: list[dict[str, Any]],
    project_root: Path,
) -> dict[Path, str | None]:
    paths: set[Path] = set()
    for action in actions:
        if action.get("mutated"):
            paths.update(_action_write_paths(action, project_root))
    return {path: snapshots[path] for path in paths if path in snapshots}


def _restore_write_artifacts(snapshots: dict[Path, str | None]) -> list[str]:
    restored: list[str] = []
    for path, text in snapshots.items():
        if text is None:
            if path.exists():
                path.unlink()
                restored.append(str(path))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        restored.append(str(path))
    return restored


def _mark_rolled_back(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for action in actions:
        if action.get("mutated"):
            out.append(
                {
                    **action,
                    "status": "rolled_back_no_post_rebuild_graduation",
                    "wrote": False,
                    "mutated": False,
                    "mutated_rows_before_rollback": int(action.get("mutated_rows", 0) or 0),
                    "mutated_rows": 0,
                }
            )
        else:
            out.append(action)
    return out


def _still_blocked_reasons_after_rebuild(
    *,
    before_row: dict[str, Any],
    after_row: dict[str, Any] | None,
    repair_row: dict[str, Any] | None,
) -> list[str]:
    if after_row is None:
        return ["post_rebuild_row_missing"]
    reasons: list[str] = []
    if repair_row is not None:
        repair_reasons = repair_row.get("repair_reasons")
        if isinstance(repair_reasons, list) and repair_reasons:
            reasons.extend(f"repair_queue:{reason}" for reason in repair_reasons)
        else:
            reasons.append("repair_queue:still_present")
    reasons.extend(f"review_batch_exclusion:{reason}" for reason in review_batch_exclusion_reasons(after_row))
    validity = classify_statement(after_row)
    if validity.primary_blocker in BLOCKING_VALIDITY:
        reasons.append(f"statement_validity:{validity.primary_blocker}")
    return list(dict.fromkeys(reasons))


def post_rebuild_graduation_report(
    *,
    before_rows: list[dict[str, Any]],
    corpus_rows_after: list[dict[str, Any]],
    repair_queue_after: list[dict[str, Any]],
    review_batch_after: list[dict[str, Any]],
    gold_queue_after: list[dict[str, Any]],
) -> dict[str, Any]:
    after_by_id = _index_by_row_id(corpus_rows_after)
    repair_by_id = _index_by_row_id(repair_queue_after)
    graduated: list[str] = []
    still_blocked: dict[str, list[str]] = {}
    for before in before_rows:
        rid = _row_id(before)
        if not rid:
            continue
        reasons = _still_blocked_reasons_after_rebuild(
            before_row=before,
            after_row=after_by_id.get(rid),
            repair_row=repair_by_id.get(rid),
        )
        if reasons:
            still_blocked[rid] = reasons
        else:
            graduated.append(rid)

    reason_counts = Counter(reason for reasons in still_blocked.values() for reason in reasons)
    return {
        "input_row_ids": [_row_id(row) for row in before_rows if _row_id(row)],
        "graduated_row_ids": graduated,
        "graduated_rows_after": len(graduated),
        "still_blocked_rows_after": len(still_blocked),
        "still_blocked_reason_counts_after": dict(reason_counts.most_common()),
        "still_blocked_by_row_id": still_blocked,
        "repair_queue_rows_after": len(repair_queue_after),
        "review_batch_rows_after": len(review_batch_after),
        "gold_proof_queue_rows_after": len(gold_queue_after),
    }


def rebuild_downstream_artifacts(
    *,
    project_root: Path,
    ledger_paths: list[Path],
    report_roots: list[Path],
    evidence_roots: list[Path],
    selected_rows_before: list[dict[str, Any]],
    limit: int,
) -> dict[str, Any]:
    corpus_out = _project_path(project_root, DEFAULT_CORPUS_OUT)
    corpus_summary_out = _project_path(project_root, DEFAULT_CORPUS_SUMMARY)
    fidelity_out = _project_path(project_root, DEFAULT_FIDELITY_OUT)
    fidelity_summary_out = _project_path(project_root, DEFAULT_FIDELITY_SUMMARY)
    repair_out = _project_path(project_root, DEFAULT_REPAIR_QUEUE_OUT)
    repair_summary_out = _project_path(project_root, DEFAULT_REPAIR_QUEUE_SUMMARY)
    review_out = _project_path(project_root, DEFAULT_REVIEW_BATCH_OUT)
    review_template_out = _project_path(project_root, DEFAULT_REVIEW_TEMPLATE_OUT)
    review_summary_out = _project_path(project_root, DEFAULT_REVIEW_BATCH_SUMMARY)
    gold_out = _project_path(project_root, DEFAULT_GOLD_PROOF_OUT)
    gold_summary_out = _project_path(project_root, DEFAULT_GOLD_PROOF_SUMMARY)
    resolved_ledger_paths = [_project_path(project_root, path) for path in (ledger_paths or [DEFAULT_LEDGER_DIR])]
    resolved_report_roots = [_project_path(project_root, path) for path in (report_roots or [DEFAULT_REPORT_DIR])]
    resolved_evidence_roots = [_project_path(project_root, path) for path in (evidence_roots or [DEFAULT_EVIDENCE_DIR])]

    rows_after, corpus_summary = build_corpus_rows(
        ledger_paths=resolved_ledger_paths,
        project_root=project_root,
        report_roots=resolved_report_roots,
        evidence_roots=resolved_evidence_roots,
    )
    _write_jsonl(corpus_out, rows_after)
    _write_json(corpus_summary_out, {**corpus_summary, "out_jsonl": str(corpus_out), "out_summary": str(corpus_summary_out)})

    fidelity_queue, fidelity_summary = build_statement_fidelity_queue(rows_after, limit=limit)
    _write_jsonl(fidelity_out, fidelity_queue)
    _write_json(fidelity_summary_out, {**fidelity_summary, "source_corpus_rows": len(rows_after), "out_jsonl": str(fidelity_out)})

    repair_queue, repair_summary = build_statement_repair_queue(rows_after, limit=limit)
    _write_jsonl(repair_out, repair_queue)
    _write_json(repair_summary_out, {**repair_summary, "source_corpus_rows": len(rows_after), "out_jsonl": str(repair_out)})

    review_batch, review_templates, review_summary = build_statement_review_batch(rows_after, limit=limit)
    _write_jsonl(review_out, review_batch)
    _write_jsonl(review_template_out, review_templates)
    _write_json(
        review_summary_out,
        {
            **review_summary,
            "source_corpus_rows": len(rows_after),
            "out_jsonl": str(review_out),
            "out_template": str(review_template_out),
        },
    )

    gold_queue, gold_summary = build_gold_proof_queue(rows_after, limit=limit)
    _write_jsonl(gold_out, gold_queue)
    _write_json(gold_summary_out, {**gold_summary, "source_corpus_rows": len(rows_after), "out_jsonl": str(gold_out)})

    graduation = post_rebuild_graduation_report(
        before_rows=selected_rows_before,
        corpus_rows_after=rows_after,
        repair_queue_after=repair_queue,
        review_batch_after=review_batch,
        gold_queue_after=gold_queue,
    )
    return {
        "status": "completed",
        "corpus_rows_after": len(rows_after),
        "corpus_summary": corpus_summary,
        "statement_fidelity_queue_rows_after": len(fidelity_queue),
        "statement_repair_queue_rows_after": len(repair_queue),
        "statement_review_batch_rows_after": len(review_batch),
        "gold_proof_queue_rows_after": len(gold_queue),
        "artifacts": {
            "corpus_jsonl": str(corpus_out),
            "corpus_summary": str(corpus_summary_out),
            "statement_fidelity_queue": str(fidelity_out),
            "statement_repair_queue": str(repair_out),
            "statement_review_batch": str(review_out),
            "statement_review_template": str(review_template_out),
            "gold_proof_queue": str(gold_out),
        },
        **graduation,
    }


def run_worker(
    rows: list[dict[str, Any]],
    *,
    project_root: Path,
    write: bool = False,
    limit: int = 500,
    max_write_groups: int = 1,
    repair_output_root: Path | None = None,
    validate_candidates: bool = True,
    ledger_paths: list[Path] | None = None,
    report_roots: list[Path] | None = None,
    evidence_roots: list[Path] | None = None,
    use_llm_repair: bool = False,
    llm_repair_client: Any = None,
    llm_repair_model: str = DEFAULT_LLM_REPAIR_MODEL,
    llm_repair_max_rounds: int = 3,
    rewrite_lean_files: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    actions, _ = build_worker_actions(rows, limit=limit)
    selected_rows = _selected_rows(rows, limit=limit)
    snapshots = _snapshot_write_artifacts(actions, project_root) if write else {}
    executed = execute_worker_actions(
        actions,
        project_root=project_root,
        write=write,
        max_write_groups=max_write_groups,
        repair_output_root=repair_output_root or project_root / "output" / "statement_repair_worker",
        validate_candidates=validate_candidates,
        use_llm_repair=use_llm_repair,
        llm_repair_client=llm_repair_client,
        llm_repair_model=llm_repair_model,
        llm_repair_max_rounds=llm_repair_max_rounds,
        rewrite_lean_files=rewrite_lean_files,
    )
    summary = _summarize_actions(executed, rows=selected_rows, write=write)
    summary["validate_candidates"] = validate_candidates
    summary["experimental"] = not validate_candidates
    if write:
        try:
            post_rebuild = rebuild_downstream_artifacts(
                project_root=project_root,
                ledger_paths=ledger_paths or [DEFAULT_LEDGER_DIR],
                report_roots=report_roots or [DEFAULT_REPORT_DIR],
                evidence_roots=evidence_roots or [DEFAULT_EVIDENCE_DIR],
                selected_rows_before=selected_rows,
                limit=limit,
            )
        except Exception as exc:
            post_rebuild = {"status": "failed", "error": f"{type(exc).__name__}:{exc}"}
        summary["post_rebuild"] = post_rebuild
        summary["graduated_rows_after"] = int(post_rebuild.get("graduated_rows_after", 0) or 0)
        summary["net_graduated_rows"] = summary["graduated_rows_after"] - int(summary.get("graduated_rows_before_action", 0) or 0)
        summary["still_blocked_reason_counts_after"] = post_rebuild.get("still_blocked_reason_counts_after", {})
        summary["review_batch_rows_after"] = int(post_rebuild.get("review_batch_rows_after", 0) or 0)
        summary["gold_proof_queue_rows_after"] = int(post_rebuild.get("gold_proof_queue_rows_after", 0) or 0)
        if summary.get("mutated_groups", 0) and summary["net_graduated_rows"] <= 0:
            restored = _restore_write_artifacts(_mutated_snapshots(snapshots, executed, project_root))
            executed = _mark_rolled_back(executed)
            try:
                rollback_rebuild = rebuild_downstream_artifacts(
                    project_root=project_root,
                    ledger_paths=ledger_paths or [DEFAULT_LEDGER_DIR],
                    report_roots=report_roots or [DEFAULT_REPORT_DIR],
                    evidence_roots=evidence_roots or [DEFAULT_EVIDENCE_DIR],
                    selected_rows_before=selected_rows,
                    limit=limit,
                )
            except Exception as exc:
                rollback_rebuild = {"status": "failed", "error": f"{type(exc).__name__}:{exc}"}
            summary = _summarize_actions(executed, rows=selected_rows, write=write)
            summary["validate_candidates"] = validate_candidates
            summary["experimental"] = not validate_candidates
            summary["post_rebuild"] = rollback_rebuild
            summary["rollback"] = {
                "status": "completed",
                "reason": "no_post_rebuild_graduation",
                "restored_artifacts": restored,
            }
            summary["graduated_rows_after"] = int(rollback_rebuild.get("graduated_rows_after", 0) or 0)
            summary["net_graduated_rows"] = summary["graduated_rows_after"] - int(summary.get("graduated_rows_before_action", 0) or 0)
            summary["still_blocked_reason_counts_after"] = rollback_rebuild.get("still_blocked_reason_counts_after", {})
            summary["review_batch_rows_after"] = int(rollback_rebuild.get("review_batch_rows_after", 0) or 0)
            summary["gold_proof_queue_rows_after"] = int(rollback_rebuild.get("gold_proof_queue_rows_after", 0) or 0)
    else:
        summary["post_rebuild"] = {"status": "skipped_dry_run"}
        summary["graduated_rows_after"] = summary["graduated_rows_before_action"]
        summary["net_graduated_rows"] = 0
        summary["still_blocked_reason_counts_after"] = {}
        summary["review_batch_rows_after"] = 0
        summary["gold_proof_queue_rows_after"] = 0
    summary["non_promotable"] = (
        (not write)
        or (not validate_candidates)
        or str(summary.get("post_rebuild", {}).get("status", "")) != "completed"
        or bool(summary.get("rollback"))
        or int(summary.get("net_graduated_rows", 0) or 0) <= 0
    )
    return executed, summary


def _load_or_build_queue(args: argparse.Namespace) -> tuple[list[dict[str, Any]], int]:
    if args.repair_queue_jsonl is not None:
        rows = _read_jsonl(args.repair_queue_jsonl)
        return _filter_queue_rows(
            rows,
            paper_id=args.paper_id,
            repair_route=args.repair_route,
            repair_kind=args.repair_kind,
        ), len(rows)
    corpus_rows, corpus_summary = build_corpus_rows(
        ledger_paths=[_project_path(args.project_root, path) for path in (args.ledger_path or [DEFAULT_LEDGER_DIR])],
        project_root=args.project_root,
        report_roots=[_project_path(args.project_root, path) for path in (args.report_root or [DEFAULT_REPORT_DIR])],
        evidence_roots=[_project_path(args.project_root, path) for path in (args.evidence_root or [DEFAULT_EVIDENCE_DIR])],
    )
    queue, _summary = build_statement_repair_queue(corpus_rows, limit=args.limit)
    return _filter_queue_rows(
        queue,
        paper_id=args.paper_id,
        repair_route=args.repair_route,
        repair_kind=args.repair_kind,
    ), int(corpus_summary.get("rows", len(corpus_rows)))


def _filter_queue_rows(
    rows: list[dict[str, Any]],
    *,
    paper_id: str = "",
    repair_route: str = "",
    repair_kind: str = "",
) -> list[dict[str, Any]]:
    paper = str(paper_id or "").strip()
    route = str(repair_route or "").strip()
    kind = str(repair_kind or "").strip()
    if not paper and not route and not kind:
        return rows
    out: list[dict[str, Any]] = []
    for row in rows:
        if paper and str(row.get("arxiv_id", "") or row.get("paper_id", "") or "") != paper:
            continue
        if route and str(row.get("repair_route", "") or "") != route:
            continue
        if kind and str(row.get("repair_kind", "") or "") != kind:
            continue
        out.append(row)
    return out


def _build_llm_repair_client(args: argparse.Namespace) -> Any:
    """Instantiate a Mistral client when --use-llm-repair is set."""
    if not bool(getattr(args, "use_llm_repair", False)):
        return None
    try:
        from mistralai import Mistral  # type: ignore[import-not-found]
    except ImportError:
        try:
            from mistralai.client import Mistral  # type: ignore[no-redef,import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "mistralai package is not installed; cannot run --use-llm-repair"
            ) from exc
    import os

    try:
        from dotenv import load_dotenv  # type: ignore[import-not-found]

        load_dotenv()
    except Exception:
        pass
    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MISTRAL_API_KEY is not set; cannot run --use-llm-repair")
    return Mistral(api_key=api_key)


def export_worker_run(args: argparse.Namespace) -> dict[str, Any]:
    rows, source_rows = _load_or_build_queue(args)
    llm_client = _build_llm_repair_client(args)
    actions, summary = run_worker(
        rows,
        project_root=args.project_root,
        write=bool(args.write),
        limit=args.limit,
        max_write_groups=args.max_write_groups,
        repair_output_root=args.repair_output_root,
        validate_candidates=not args.skip_validate,
        use_llm_repair=bool(getattr(args, "use_llm_repair", False)),
        llm_repair_client=llm_client,
        llm_repair_model=str(getattr(args, "llm_repair_model", DEFAULT_LLM_REPAIR_MODEL) or DEFAULT_LLM_REPAIR_MODEL),
        llm_repair_max_rounds=int(getattr(args, "llm_repair_max_rounds", 3) or 3),
        rewrite_lean_files=bool(getattr(args, "rewrite_lean_files", True)),
    )
    result = {
        **summary,
        "source_rows": source_rows,
        "out_actions": str(args.out_actions),
        "out_summary": str(args.out_summary),
    }
    _write_jsonl(args.out_actions, actions)
    _write_json(args.out_summary, result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run dry-run-first statement repair worker")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--repair-queue-jsonl", type=Path, default=None)
    parser.add_argument("--ledger-path", action="append", type=Path, default=[])
    parser.add_argument("--report-root", action="append", type=Path, default=[])
    parser.add_argument("--evidence-root", action="append", type=Path, default=[])
    parser.add_argument("--out-actions", type=Path, default=DEFAULT_OUT_ACTIONS)
    parser.add_argument("--out-summary", type=Path, default=DEFAULT_OUT_SUMMARY)
    parser.add_argument("--repair-output-root", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--max-write-groups", type=int, default=1)
    parser.add_argument("--paper-id", default="", help="Optional arXiv paper-id filter for bounded repair runs")
    parser.add_argument("--repair-route", default="", help="Optional route filter for bounded repair runs")
    parser.add_argument("--repair-kind", default="", help="Optional kind filter for bounded repair runs")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--skip-validate", action="store_true")
    parser.add_argument(
        "--use-llm-repair",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Generate statement repair candidates via Leanstral (HIGHER priority "
            "than the rule-based path). Default ON as of Round-III smoke validation "
            "(4/5 Lean-validated on 2304.09598 sample). Pass --no-use-llm-repair to "
            "skip Mistral calls (rule-based path only). Requires MISTRAL_API_KEY."
        ),
    )
    parser.add_argument(
        "--llm-repair-model",
        default=DEFAULT_LLM_REPAIR_MODEL,
        help="Mistral model ID for --use-llm-repair (default: %(default)s)",
    )
    parser.add_argument(
        "--llm-repair-max-rounds",
        type=int,
        default=3,
        help=(
            "Max retry rounds for LLM statement repair (default: 3). When the "
            "elaboration gate rejects an LLM candidate, the Lean error tail is "
            "fed back to Leanstral with a directive to fix the specific issue. "
            "Set to 1 to disable retry (single-attempt semantics). Each "
            "additional round is one extra Mistral call per failing row, so "
            "cost scales linearly with this budget."
        ),
    )
    parser.add_argument(
        "--rewrite-lean-files",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "After each per-paper ledger mutation, regenerate `output/<paper>.lean` "
            "from the (now-updated) ledger via rewrite_lean_from_ledger. Default ON "
            "so brand-new theorems introduced by statement-repair upgrades become "
            "visible to downstream proof search. Pass --no-rewrite-lean-files for "
            "diagnostic runs that need to inspect the ledger update in isolation."
        ),
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    args.project_root = args.project_root.resolve()
    result = export_worker_run(args)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
