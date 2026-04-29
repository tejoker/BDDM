#!/usr/bin/env python3
"""Export paper-agnostic silver repair data with explicit labels.

This exporter packages failed elaborations, compiler feedback, tactic attempts,
translation-repair queue rows, and statement-validity blockers into a silver
dataset.  Silver rows are useful for ML training/evaluation, but are never gold
proof evidence.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

from export_april_repair_dataset import build_april_rows, iter_attempts, iter_run_rows
from repair_feedback_dataset import (
    DEFAULT_RUN_ROOT,
    DEFAULT_SILVER_DATASET_PATH,
    DEFAULT_SILVER_SUMMARY_PATH,
    SILVER_DATASET_FAMILY,
    apply_silver_metadata,
    classify_error,
    make_repair_row,
    merge_deduped_rows,
    read_jsonl,
)


TRANSLATION_BLOCKERS = {"translation_limited", "bad_translation_artifact", "ill_typed_statement"}
DIAGNOSTIC_BLOCKERS = {"paper_theory_debt", "claim_review_pending", "release_ready"}
SUCCESS_STATUSES = {"FULLY_PROVEN", "AXIOM_BACKED", "INTERMEDIARY_PROVEN"}
AXIOM_SUCCESS_STATUSES = {"AXIOM_BACKED", "INTERMEDIARY_PROVEN"}


@dataclass(frozen=True)
class StatementInfo:
    primary_blocker: str = ""
    reasons: tuple[str, ...] = ()
    valid_for_proof: bool = False
    in_proof_repair_cohort: bool = False
    source_artifact: str = ""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _safe_name(name: str) -> str:
    return str(name or "").strip().rsplit(".", 1)[-1]


def _key(paper_id: str, theorem_name: str) -> tuple[str, str]:
    return (str(paper_id or "").strip(), _safe_name(theorem_name))


def _entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("entries", "items", "rows", "results"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [x for x in rows if isinstance(x, dict)]
    return []


def _paper_id_from_path(path: Path, payload: Any = None) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("paper_id"), str):
        return str(payload["paper_id"])
    match = re.search(r"(\d{4}\.\d{5})", str(path))
    return match.group(1) if match else path.stem


def _ledger_files(paths: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for root in paths:
        if not root.exists():
            continue
        if root.is_file() and root.suffix == ".json":
            files.append(root)
            continue
        files.extend(sorted(root.glob("*.json")))
        if root.name == "verification_ledgers":
            files.extend(sorted(root.glob("*.json")))
        files.extend(sorted(root.rglob("verification_ledger.json")))
    return list(dict.fromkeys(files))


def _load_statuses(ledger_files: Iterable[Path]) -> dict[tuple[str, str], set[str]]:
    statuses: dict[tuple[str, str], set[str]] = defaultdict(set)
    for path in ledger_files:
        payload = _read_json(path)
        if payload is None:
            continue
        paper_id = _paper_id_from_path(path, payload)
        for row in _entries(payload):
            theorem = str(row.get("theorem_name") or row.get("theorem") or row.get("name") or "")
            if not theorem:
                continue
            status = str(row.get("status") or ("FULLY_PROVEN" if row.get("proved") is True else "")).upper()
            if status:
                statuses[_key(paper_id, theorem)].add(status)
    return statuses


def _statement_validity_files(report_roots: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for root in report_roots:
        if not root.exists():
            continue
        if root.is_file() and root.name.endswith("statement_validity.json"):
            files.append(root)
        elif root.is_dir():
            files.extend(sorted(root.rglob("statement_validity.json")))
            files.extend(sorted(root.rglob("*.statement_validity.json")))
    return list(dict.fromkeys(files))


def _load_statement_info(report_roots: Iterable[Path]) -> dict[tuple[str, str], StatementInfo]:
    info: dict[tuple[str, str], StatementInfo] = {}
    for path in _statement_validity_files(report_roots):
        payload = _read_json(path)
        if not isinstance(payload, dict):
            continue
        paper_id = _paper_id_from_path(path, payload)
        for item in _entries(payload):
            theorem = str(item.get("theorem_name") or "")
            if not theorem:
                continue
            reasons = item.get("reasons", [])
            reason_tuple = tuple(str(x) for x in reasons) if isinstance(reasons, list) else ()
            info[_key(paper_id, theorem)] = StatementInfo(
                primary_blocker=str(item.get("primary_blocker") or ""),
                reasons=reason_tuple,
                valid_for_proof=bool(item.get("valid_for_proof")),
                source_artifact=str(path),
            )
    return info


def _load_proof_cohort(report_roots: Iterable[Path]) -> set[tuple[str, str]]:
    cohort: set[tuple[str, str]] = set()
    for root in report_roots:
        if not root.exists():
            continue
        paths = [root] if root.is_file() and "proof_repair_cohort" in root.name else sorted(root.rglob("proof_repair_cohort.json"))
        for path in paths:
            payload = _read_json(path)
            paper_id = _paper_id_from_path(path, payload)
            for item in _entries(payload):
                theorem = str(item.get("theorem_name") or item.get("theorem") or item.get("name") or "")
                if theorem:
                    cohort.add(_key(paper_id, theorem))
    return cohort


def _join_statement_info(
    info: dict[tuple[str, str], StatementInfo],
    cohort: set[tuple[str, str]],
) -> dict[tuple[str, str], StatementInfo]:
    out = dict(info)
    for key in cohort:
        current = out.get(key, StatementInfo())
        out[key] = StatementInfo(
            primary_blocker=current.primary_blocker,
            reasons=current.reasons,
            valid_for_proof=current.valid_for_proof,
            in_proof_repair_cohort=True,
            source_artifact=current.source_artifact,
        )
    return out


def _artifact_issue(text: str) -> str:
    s = str(text or "")
    if "PaperClaim" in s:
        return "paper_claim_atom"
    if "RegeneratedStatement" in s:
        return "regenerated_statement_atom"
    if re.search(r"\(p_c\d+\s*:\s*Prop\)", s):
        return "schema_prop_slot"
    if "schema_translation_placeholder" in s or "sorry_placeholder" in s:
        return "schema_placeholder"
    if any(tok in s for tok in ("\\frac", "\\begin", "\\end", "B_N^{", "C_TH ^", "Complex.abs")):
        return "raw_notation_artifact"
    return ""


def _paper_split(paper_id: str, *, seed: str = "silver-v1") -> str:
    value = int(sha256(f"{seed}:{paper_id}".encode("utf-8")).hexdigest()[:8], 16) % 100
    if value < 80:
        return "train"
    if value < 90:
        return "validation"
    return "test"


def _classify_silver_row(
    row: dict[str, Any],
    *,
    statement_info: StatementInfo,
    statuses: set[str],
) -> tuple[str, str, str, str]:
    blocker = statement_info.primary_blocker
    text = "\n".join(
        [
            str(row.get("failing_lean", "") or ""),
            str(row.get("successful_repair", "") or ""),
            str(row.get("error_message", "") or ""),
        ]
    )
    artifact = _artifact_issue(text)
    if blocker in TRANSLATION_BLOCKERS or artifact:
        return "negative_bad_translation", blocker or classify_error(str(row.get("error_message", ""))), artifact or blocker, "high"
    if statuses & AXIOM_SUCCESS_STATUSES:
        return "diagnostic_only", blocker or "axiom_backed_success", "axiom_backed_success_excluded", "high"
    if str(row.get("successful_repair", "") or "").strip():
        return "positive_repair", blocker or str(row.get("failure_class", "") or "compiler_feedback"), "", "medium"
    if blocker in DIAGNOSTIC_BLOCKERS:
        return "diagnostic_only", blocker, blocker, "high"
    reason = str(row.get("failure_class", "") or classify_error(str(row.get("error_message", ""))) or "failed_attempt")
    return "negative_failed_attempt", blocker or reason, reason, "medium"


def _translation_queue_files(paths: Iterable[Path], *, include_tmp: bool) -> list[Path]:
    files: list[Path] = []
    for root in paths:
        if not root.exists():
            continue
        if root.is_file():
            files.append(root)
        else:
            files.extend(sorted(root.rglob("translation_repair_queue.jsonl")))
    if include_tmp:
        tmp = Path("/tmp")
        if tmp.exists():
            files.extend(sorted(tmp.glob("arxiv_*/translation_repair_queue.jsonl")))
    return list(dict.fromkeys(files))


def _queue_rows(paths: Iterable[Path], *, include_tmp: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _translation_queue_files(paths, include_tmp=include_tmp):
        for raw in read_jsonl(path):
            paper_id = str(raw.get("paper_id") or _paper_id_from_path(path))
            theorem = str(raw.get("theorem_name") or "")
            gate_reason = str(raw.get("gate_reason") or raw.get("failure_kind") or "translation_repair_queue")
            local_context = "\n".join(
                part
                for part in (
                    f"source_statement: {raw.get('source_statement', '')}",
                    f"gate_reason: {gate_reason}",
                )
                if part.strip()
            )
            rows.append(
                make_repair_row(
                    paper_id=paper_id,
                    theorem_name=theorem,
                    failing_lean=str(raw.get("lean_signature") or ""),
                    error_message=f"translation_acceptance_gate:{gate_reason}",
                    local_context=local_context,
                    repair_source="translation_repair_queue",
                    stage="translation_repair_queue",
                    run_id="translation_repair_queue",
                    source_artifact=str(path),
                    source_artifacts=[str(path)],
                    extra={
                        "source_statement": str(raw.get("source_statement") or ""),
                        "gate_reason": gate_reason,
                        "validated": bool(raw.get("validated")),
                    },
                )
            )
    return rows


def build_silver_rows(
    *,
    input_paths: list[Path],
    run_roots: list[Path],
    report_roots: list[Path],
    repair_queue_paths: list[Path],
    include_tmp_repair_queues: bool = False,
    split_seed: str = "silver-v1",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ledger_files = _ledger_files(input_paths)
    attempts = iter_attempts(ledger_files)
    ledger_rows, _ = build_april_rows(attempts)
    run_rows = iter_run_rows(run_roots)
    queue_rows = _queue_rows(repair_queue_paths, include_tmp=include_tmp_repair_queues)
    base_rows = merge_deduped_rows([ledger_rows, run_rows, queue_rows])

    statuses = _load_statuses(ledger_files)
    statement_info = _join_statement_info(_load_statement_info(report_roots), _load_proof_cohort(report_roots))

    silver_rows: list[dict[str, Any]] = []
    for row in base_rows:
        key = _key(str(row.get("paper_id", "")), str(row.get("theorem_name", "")))
        info = statement_info.get(key, StatementInfo())
        row_statuses = statuses.get(key, set())
        label, blocker, negative_reason, confidence = _classify_silver_row(
            row,
            statement_info=info,
            statuses=row_statuses,
        )
        sources = list(row.get("source_artifacts") or [])
        if info.source_artifact and info.source_artifact not in sources:
            sources.append(info.source_artifact)
        enriched = apply_silver_metadata(
            row,
            label=label,
            blocker_label=blocker,
            statement_validity_blocker=info.primary_blocker,
            paper_split=_paper_split(str(row.get("paper_id", "")), seed=split_seed),
            negative_reason=negative_reason,
            provenance_confidence=confidence,
            gold_eligible=False,
            source_artifacts=sources,
        )
        enriched["statement_validity_reasons"] = list(info.reasons)
        enriched["valid_for_proof"] = bool(info.valid_for_proof)
        enriched["in_proof_repair_cohort"] = bool(info.in_proof_repair_cohort)
        enriched["ledger_statuses"] = sorted(row_statuses)
        silver_rows.append(enriched)

    silver_rows.sort(
        key=lambda r: (
            str(r.get("paper_split", "")),
            str(r.get("paper_id", "")),
            str(r.get("theorem_name", "")),
            str(r.get("silver_row_id", "")),
        )
    )
    return silver_rows, summarize_silver_rows(
        silver_rows,
        ledger_export_rows=len(ledger_rows),
        run_local_rows=len(run_rows),
        translation_queue_rows=len(queue_rows),
        split_seed=split_seed,
    )


def summarize_silver_rows(
    rows: list[dict[str, Any]],
    *,
    ledger_export_rows: int,
    run_local_rows: int,
    translation_queue_rows: int,
    split_seed: str,
) -> dict[str, Any]:
    labels = Counter(str(row.get("label", "")) for row in rows)
    polarities = Counter(str(row.get("label_polarity", "")) for row in rows)
    training_tiers = Counter(str(row.get("training_tier", "")) for row in rows)
    splits = Counter(str(row.get("paper_split", "")) for row in rows)
    papers = Counter(str(row.get("paper_id", "")) for row in rows)
    blockers = Counter(str(row.get("blocker_label", "")) for row in rows if str(row.get("blocker_label", "")))
    statement_blockers = Counter(
        str(row.get("statement_validity_blocker", ""))
        for row in rows
        if str(row.get("statement_validity_blocker", ""))
    )
    failure_classes = Counter(str(row.get("failure_class", "")) for row in rows if str(row.get("failure_class", "")))
    negative_reasons = Counter(str(row.get("negative_reason", "")) for row in rows if str(row.get("negative_reason", "")))
    source_artifacts: list[str] = []
    for row in rows:
        for source in row.get("source_artifacts", []) if isinstance(row.get("source_artifacts"), list) else []:
            source_text = str(source)
            if source_text and source_text not in source_artifacts:
                source_artifacts.append(source_text)
    return {
        "schema_version": "1.0.0",
        "dataset_family": SILVER_DATASET_FAMILY,
        "dataset_tier": "silver",
        "created_at_unix": int(time.time()),
        "rows": len(rows),
        "papers": len([p for p in papers if p]),
        "label_counts": dict(labels.most_common()),
        "label_polarity_counts": dict(polarities.most_common()),
        "training_tier_counts": dict(training_tiers.most_common()),
        "split_counts": dict(splits.most_common()),
        "paper_counts": dict(papers.most_common()),
        "blocker_label_counts": dict(blockers.most_common()),
        "statement_validity_blocker_counts": dict(statement_blockers.most_common()),
        "failure_class_counts": dict(failure_classes.most_common()),
        "negative_reason_counts": dict(negative_reasons.most_common()),
        "ledger_export_rows": int(ledger_export_rows),
        "run_local_rows": int(run_local_rows),
        "translation_queue_rows": int(translation_queue_rows),
        "deduplicated_rows": len(rows),
        "split_seed": split_seed,
        "gold_contamination_audit": {
            "gold_eligible_true_count": sum(1 for row in rows if bool(row.get("gold_eligible"))),
            "positive_axiom_backed_rows": sum(
                1
                for row in rows
                if row.get("label") == "positive_repair"
                and any(str(s) in AXIOM_SUCCESS_STATUSES for s in row.get("ledger_statuses", []))
            ),
            "paper_claim_rows": sum(1 for row in rows if _artifact_issue(str(row.get("failing_lean", ""))) == "paper_claim_atom"),
            "regenerated_statement_rows": sum(
                1 for row in rows if _artifact_issue(str(row.get("failing_lean", ""))) == "regenerated_statement_atom"
            ),
            "axiom_backed_success_excluded": int(negative_reasons.get("axiom_backed_success_excluded", 0)),
        },
        "source_artifacts": source_artifacts[:500],
        "honest_scope": "DESol-local silver repair data with explicit negatives; never gold proof evidence.",
    }


def export_silver_dataset(
    *,
    input_paths: list[Path],
    run_roots: list[Path],
    report_roots: list[Path],
    repair_queue_paths: list[Path],
    out_jsonl: Path,
    out_summary: Path,
    include_tmp_repair_queues: bool = False,
    split_seed: str = "silver-v1",
) -> dict[str, Any]:
    rows, summary = build_silver_rows(
        input_paths=input_paths,
        run_roots=run_roots,
        report_roots=report_roots,
        repair_queue_paths=repair_queue_paths,
        include_tmp_repair_queues=include_tmp_repair_queues,
        split_seed=split_seed,
    )
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    out_summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"out_jsonl": str(out_jsonl), "out_summary": str(out_summary), **summary}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export paper-agnostic silver repair dataset")
    parser.add_argument("--input", action="append", type=Path, default=[], help="Ledger file/directory; can repeat")
    parser.add_argument("--run-root", action="append", type=Path, default=[], help="Run-local compiler-feedback root/file")
    parser.add_argument("--report-root", action="append", type=Path, default=[], help="Full-paper report bundle root")
    parser.add_argument("--repair-queue", action="append", type=Path, default=[], help="Translation repair queue file/root")
    parser.add_argument("--out-jsonl", type=Path, default=DEFAULT_SILVER_DATASET_PATH)
    parser.add_argument("--out-summary", type=Path, default=DEFAULT_SILVER_SUMMARY_PATH)
    parser.add_argument("--split-seed", default="silver-v1")
    parser.add_argument("--include-tmp-repair-queues", action="store_true", help="Debug-only: include /tmp/arxiv_* repair queues")
    parser.add_argument("--no-tmp-repair-queues", action="store_true", help=argparse.SUPPRESS)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    inputs = args.input or [Path("output/verification_ledgers"), Path("reproducibility/full_paper_reports")]
    run_roots = args.run_root or [DEFAULT_RUN_ROOT, Path("output/flywheel/compiler_feedback_repair_dataset.jsonl")]
    report_roots = args.report_root or [Path("reproducibility/full_paper_reports"), Path("output/reports/full_paper")]
    result = export_silver_dataset(
        input_paths=inputs,
        run_roots=run_roots,
        report_roots=report_roots,
        repair_queue_paths=args.repair_queue or [],
        out_jsonl=args.out_jsonl,
        out_summary=args.out_summary,
        include_tmp_repair_queues=bool(args.include_tmp_repair_queues) and not bool(args.no_tmp_repair_queues),
        split_seed=args.split_seed,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
