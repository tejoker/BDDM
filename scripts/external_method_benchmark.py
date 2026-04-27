#!/usr/bin/env python3
"""Compare DESol evidence against three adjacent arXiv methods.

This is intentionally a method-level benchmark, not a leaderboard claim. It
absorbs concrete lessons from:

- arXiv:2602.02990 / APRIL: compiler-feedback proof repair supervision.
- arXiv:2602.05216 / theorem semantic search: theorem extraction + sloganized retrieval.
- arXiv:2603.17075 / CircuitBuilder: verifier-backed symbolic search.

The script reads committed/local DESol artifacts and reports what is already
measurable, what is partially implemented, and what remains not comparable.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExternalMethod:
    paper_id: str
    name: str
    method_family: str
    target_signal: str
    external_claim: str
    desol_mapping: str


EXTERNAL_METHODS: tuple[ExternalMethod, ...] = (
    ExternalMethod(
        paper_id="2602.02990",
        name="APRIL proof repair",
        method_family="compiler_feedback_repair",
        target_signal="failing Lean proof + compiler diagnostic + corrected proof",
        external_claim="260k proof-repair tuples; single-shot repair gains from compiler feedback",
        desol_mapping="proof ledgers and logs should become a repair-flywheel corpus",
    ),
    ExternalMethod(
        paper_id="2602.05216",
        name="Semantic theorem search",
        method_family="theorem_retrieval",
        target_signal="theorem inventory + natural-language slogan + embedding retrieval evidence",
        external_claim="9.2M theorem statements indexed; sloganized theorem retrieval improves search",
        desol_mapping="paper ingestion, theorem extraction, sloganization, KG/retrieval coverage",
    ),
    ExternalMethod(
        paper_id="2603.17075",
        name="CircuitBuilder symbolic search",
        method_family="verifier_backed_symbolic_search",
        target_signal="state/action/reward traces in a compact verifier-backed symbolic environment",
        external_claim="PPO+MCTS and SAC learn polynomial circuit construction in small verified games",
        desol_mapping="world-model/MCTS traces should expose verifier-backed actions and rewards",
    ),
)


_ERROR_HINTS = (
    "error",
    "failed",
    "unexpected",
    "typeclass instance problem",
    "invalid field",
    "unknown identifier",
    "tactic",
    "semantic_policy",
    "repair",
)


SLOGAN_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "with", "on", "by", "is", "are",
    "let", "where", "there", "exists", "forall", "all", "that", "this", "from", "as", "be",
}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _iter_json_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.json") if p.is_file())


def _iter_jsonl_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    for path in sorted(root.rglob("*.jsonl")):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
        except Exception:
            continue
    return rows


def _read_jsonl_file(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
    except Exception:
        return []
    return rows


def _repair_dataset_rows(project_root: Path) -> list[dict[str, Any]]:
    flywheel = project_root / "output" / "flywheel"
    canonical = flywheel / "compiler_feedback_repair_dataset.jsonl"
    if canonical.exists():
        return _read_jsonl_file(canonical)
    fallback = flywheel / "april_repair_dataset.jsonl"
    if fallback.exists():
        return _read_jsonl_file(fallback)
    return []


def _as_entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        entries = payload.get("entries") or payload.get("rows") or payload.get("theorems")
        return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []
    if isinstance(payload, list):
        return [e for e in payload if isinstance(e, dict)]
    return []


def _safe_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def theorem_slogan(statement: str, *, max_terms: int = 12) -> str:
    """Create a tiny deterministic theorem-search style slogan.

    This is not an LLM slogan. It is a cheap local baseline that lets us track
    whether every extracted theorem has a searchable semantic surrogate.
    """
    text = re.sub(r"\\(?:label|ref|cite|eqref)\{[^}]*\}", " ", statement or "")
    text = re.sub(r"\\[A-Za-z]+\*?(?:\[[^]]*\])?", " ", text)
    text = re.sub(r"[$^_{}\\]", " ", text)
    tokens = [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", text)]
    kept: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        if tok in SLOGAN_STOPWORDS or tok in seen:
            continue
        seen.add(tok)
        kept.append(tok)
        if len(kept) >= max_terms:
            break
    return " ".join(kept)


def _ledger_rows(ledger_dirs: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for root in ledger_dirs:
        for path in _iter_json_files(root):
            payload = _read_json(path)
            paper_id = _safe_text(payload.get("paper_id")) if isinstance(payload, dict) else ""
            for entry in _as_entries(payload):
                row = dict(entry)
                row.setdefault("paper_id", paper_id or path.stem)
                row.setdefault("artifact_path", str(path))
                rows.append(row)
    return rows


def _looks_like_feedback_row(row: dict[str, Any]) -> bool:
    blob = " ".join(
        _safe_text(row.get(key))
        for key in (
            "error",
            "error_message",
            "last_error",
            "prove_summary",
            "stderr_tail",
            "stdout_tail",
            "status",
        )
    ).lower()
    return any(hint in blob for hint in _ERROR_HINTS)


def build_repair_benchmark(
    ledger_rows: list[dict[str, Any]],
    *,
    repair_dataset_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    repair_dataset_rows = repair_dataset_rows or []
    feedback_rows = [row for row in ledger_rows if _looks_like_feedback_row(row)]
    successful_rows = [row for row in ledger_rows if _safe_text(row.get("status")) in {"FULLY_PROVEN", "AXIOM_BACKED"}]
    paired = [
        row
        for row in feedback_rows
        if _safe_text(row.get("proof_text")) or _safe_text(row.get("corrected_proof")) or _safe_text(row.get("final_proof"))
    ]
    dataset_paired = [row for row in repair_dataset_rows if row.get("repair_available") or _safe_text(row.get("successful_repair"))]
    errors = Counter()
    for row in feedback_rows:
        blob = " ".join(_safe_text(row.get(k)) for k in ("error_message", "last_error", "prove_summary", "status")).lower()
        if "typeclass" in blob:
            errors["typeclass"] += 1
        elif "unknown identifier" in blob or "invalid field" in blob:
            errors["name_resolution"] += 1
        elif "semantic_policy" in blob or "claim_shape" in blob:
            errors["semantic_policy"] += 1
        elif "tactic" in blob:
            errors["tactic_failure"] += 1
        elif "unexpected" in blob:
            errors["syntax_or_repl"] += 1
        else:
            errors["other"] += 1
    readiness = "not_comparable"
    if feedback_rows and paired:
        readiness = "partial"
    if feedback_rows and paired and len(paired) >= max(5, len(feedback_rows) // 4):
        readiness = "benchmarkable_small"
    if repair_dataset_rows:
        readiness = "april_style_export_ready"
    return {
        "method_family": "compiler_feedback_repair",
        "external_reference": "arXiv:2602.02990",
        "desol_signal": {
            "ledger_rows": len(ledger_rows),
            "feedback_rows": len(feedback_rows),
            "paired_repair_candidates": len(paired),
            "april_style_dataset_rows": len(repair_dataset_rows),
            "april_style_paired_repairs": len(dataset_paired),
            "successful_rows_available_as_targets": len(successful_rows),
            "error_class_counts": dict(errors),
        },
        "benchmark_status": readiness,
        "next_absorption_step": "export APRIL-style JSONL tuples: failing proof, diagnostic, local context, repaired proof when available",
    }


def _extraction_rows(ingestion_root: Path, theorem_jsons: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in theorem_jsons:
        payload = _read_json(path)
        entries = _as_entries(payload)
        paper_id = _safe_text(payload.get("paper_id")) if isinstance(payload, dict) else path.parent.name
        for entry in entries:
            row = dict(entry)
            row.setdefault("paper_id", paper_id)
            row.setdefault("artifact_path", str(path))
            rows.append(row)
    # Common committed ingestion layout: paper_dir/extracted_theorems.json.
    if ingestion_root.exists():
        for path in sorted(ingestion_root.rglob("extracted_theorems.json")):
            payload = _read_json(path)
            paper_id = _safe_text(payload.get("paper_id")) if isinstance(payload, dict) else path.parent.name
            for entry in _as_entries(payload):
                row = dict(entry)
                row.setdefault("paper_id", paper_id or path.parent.name)
                row.setdefault("artifact_path", str(path))
                rows.append(row)
    return rows


def build_theorem_search_benchmark(extraction_rows: list[dict[str, Any]]) -> dict[str, Any]:
    paper_ids = {str(row.get("paper_id", "")) for row in extraction_rows if row.get("paper_id")}
    statements = [_safe_text(row.get("statement") or row.get("normalized_statement") or row.get("latex")) for row in extraction_rows]
    slogans = [theorem_slogan(stmt) for stmt in statements]
    nonempty_slogans = [s for s in slogans if s]
    env_counts = Counter(str(row.get("kind", "unknown")) for row in extraction_rows)
    readiness = "not_comparable"
    if extraction_rows:
        readiness = "extraction_only"
    if extraction_rows and len(nonempty_slogans) / max(1, len(extraction_rows)) >= 0.8:
        readiness = "slogan_baseline_ready"
    return {
        "method_family": "theorem_retrieval",
        "external_reference": "arXiv:2602.05216",
        "desol_signal": {
            "papers_with_extraction_rows": len(paper_ids),
            "theorem_like_rows": len(extraction_rows),
            "rows_with_nonempty_slogan": len(nonempty_slogans),
            "slogan_coverage": round(len(nonempty_slogans) / max(1, len(extraction_rows)), 4),
            "kind_counts": dict(env_counts),
            "sample_slogans": nonempty_slogans[:5],
        },
        "benchmark_status": readiness,
        "next_absorption_step": "replace local slogans with LLM theorem descriptions, embed them, and evaluate top-k theorem retrieval on gold queries",
    }


def build_symbolic_search_benchmark(report_roots: list[Path]) -> dict[str, Any]:
    files: list[Path] = []
    for root in report_roots:
        files.extend(_iter_json_files(root))
    evidence = Counter()
    reward_rows = 0
    verifier_rows = 0
    for path in files:
        payload = _read_json(path)
        text = json.dumps(payload, ensure_ascii=False).lower() if payload is not None else ""
        if "world_model" in text or "mcts" in text:
            evidence["search_artifacts"] += 1
        if "reward" in text or "score" in text or "value" in text:
            reward_rows += 1
        if "lean" in text and ("verified" in text or "returncode" in text or "proof_closed" in text):
            verifier_rows += 1
    readiness = "not_comparable"
    if evidence["search_artifacts"]:
        readiness = "trace_artifacts_present"
    if evidence["search_artifacts"] and reward_rows and verifier_rows:
        readiness = "benchmarkable_small"
    return {
        "method_family": "verifier_backed_symbolic_search",
        "external_reference": "arXiv:2603.17075",
        "desol_signal": {
            "json_artifacts_scanned": len(files),
            "search_artifacts_detected": evidence["search_artifacts"],
            "artifacts_with_reward_or_value_signal": reward_rows,
            "artifacts_with_verifier_signal": verifier_rows,
        },
        "benchmark_status": readiness,
        "next_absorption_step": "run a fixed symbolic-search slice with explicit state/action/reward/verifier traces and compare against text-only proof search",
    }


def build_report(
    *,
    project_root: Path,
    ingestion_root: Path,
    ledger_dirs: list[Path],
    theorem_jsons: list[Path],
    report_roots: list[Path],
) -> dict[str, Any]:
    ledgers = _ledger_rows(ledger_dirs)
    extracted = _extraction_rows(ingestion_root, theorem_jsons)
    repair_dataset_rows = _repair_dataset_rows(project_root)
    sections = [
        build_repair_benchmark(ledgers, repair_dataset_rows=repair_dataset_rows),
        build_theorem_search_benchmark(extracted),
        build_symbolic_search_benchmark(report_roots),
    ]
    comparable = [s for s in sections if s["benchmark_status"] not in {"not_comparable"}]
    return {
        "schema_version": "1.0.0",
        "report_type": "external_method_absorption_benchmark",
        "project_root": str(project_root),
        "external_methods": [asdict(m) for m in EXTERNAL_METHODS],
        "summary": {
            "methods_tracked": len(EXTERNAL_METHODS),
            "methods_with_desol_evidence": len(comparable),
            "honest_scope": "method-level comparison; not a reproduction of external full-scale datasets",
        },
        "sections": sections,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark DESol evidence against adjacent external methods")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument(
        "--ingestion-root",
        type=Path,
        default=Path("reproducibility/paper_agnostic_golden10_results"),
        help="Root containing extracted_theorems.json bundles",
    )
    parser.add_argument(
        "--ledger-dir",
        action="append",
        type=Path,
        default=[],
        help="Ledger/report directory to scan; can be repeated",
    )
    parser.add_argument(
        "--theorem-json",
        action="append",
        type=Path,
        default=[],
        help="Additional theorem inventory JSON file to include; can be repeated",
    )
    parser.add_argument(
        "--report-root",
        action="append",
        type=Path,
        default=[],
        help="Report/log directory to scan for search traces; can be repeated",
    )
    parser.add_argument("--out-json", type=Path, default=Path("output/reports/external_method_benchmark.json"))
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    project_root = args.project_root.resolve()
    ledger_dirs = args.ledger_dir or [
        project_root / "output" / "verification_ledgers",
        project_root / "reproducibility" / "full_paper_reports",
    ]
    report_roots = args.report_root or [project_root / "output" / "reports", project_root / "logs", project_root / "reproducibility"]
    report = build_report(
        project_root=project_root,
        ingestion_root=args.ingestion_root if args.ingestion_root.is_absolute() else project_root / args.ingestion_root,
        ledger_dirs=[p if p.is_absolute() else project_root / p for p in ledger_dirs],
        theorem_jsons=[p if p.is_absolute() else project_root / p for p in args.theorem_json],
        report_roots=[p if p.is_absolute() else project_root / p for p in report_roots],
    )
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"out_json": str(args.out_json), **report["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
