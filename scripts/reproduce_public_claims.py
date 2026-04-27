#!/usr/bin/env python3
"""One-command public-claims reproduction harness.

The full mode orchestrates the existing suite runners. The smoke mode is
CI-friendly: it re-indexes checked-in reproducibility evidence without arXiv,
Mistral, or Lean calls, then writes the same top-level artifacts and manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


PUBLIC_ARTIFACTS = (
    "fetched_paper_metadata",
    "extracted_theorems",
    "translation",
    "lean_validation",
    "proof_attempts",
    "final_ledger",
    "full_report",
    "compiler_feedback_repair_dataset",
    "compiler_feedback_repair_dataset_summary",
    "manifest",
)


def _safe_id(text: str) -> str:
    return str(text).replace("/", "_").replace(":", "_")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _relativize(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_suite(suite_path: Path) -> list[dict[str, Any]]:
    raw = _read_json(suite_path)
    papers = raw.get("papers", []) if isinstance(raw, dict) else []
    return [paper for paper in papers if isinstance(paper, dict) and str(paper.get("paper_id", "")).strip()]


def _selected_papers(suite_path: Path, max_papers: int) -> list[dict[str, Any]]:
    papers = _load_suite(suite_path)
    if max_papers > 0:
        return papers[:max_papers]
    return papers


def _run(cmd: list[str], *, cwd: Path, timeout_s: int = 0) -> dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=(None if timeout_s <= 0 else timeout_s),
        )
        return {
            "cmd": cmd,
            "returncode": int(proc.returncode),
            "elapsed_s": round(time.time() - started, 3),
            "stdout_tail": (proc.stdout or "")[-5000:],
            "stderr_tail": (proc.stderr or "")[-5000:],
        }
    except subprocess.TimeoutExpired:
        return {
            "cmd": cmd,
            "returncode": 124,
            "elapsed_s": round(time.time() - started, 3),
            "stdout_tail": "",
            "stderr_tail": f"timeout_after_{timeout_s}s",
        }


def run_full_pipeline(
    *,
    project_root: Path,
    suite_path: Path,
    out_root: Path,
    max_papers: int,
    max_theorems: int,
    paper_timeout_s: int,
    model: str,
    api_rate: float,
    focus_no_world_model: bool,
    skip_translation: bool,
    skip_full_formalization: bool,
) -> list[dict[str, Any]]:
    stage_rows: list[dict[str, Any]] = []
    stage_rows.append(
        {
            "stage": "ingestion",
            **_run(
                [
                    sys.executable,
                    "scripts/paper_ingestion_evidence.py",
                    "--suite-json",
                    str(suite_path),
                    "--source-root",
                    str(out_root / "paper_sources"),
                    "--evidence-root",
                    str(out_root / "ingestion"),
                    "--max-papers",
                    str(max(0, max_papers)),
                    "--max-theorems-output",
                    "0",
                ],
                cwd=project_root,
            ),
        }
    )

    if not skip_translation:
        cmd = [
            sys.executable,
            "scripts/run_golden10_translation.py",
            "--suite-json",
            str(suite_path),
            "--project-root",
            str(project_root),
            "--out-root",
            str(out_root / "translations" / "lean"),
            "--work-root",
            str(out_root / "translation_work"),
            "--evidence-root",
            str(out_root / "translation"),
            "--max-papers",
            str(max(0, max_papers)),
            "--max-theorems",
            str(max(0, max_theorems)),
            "--api-rate",
            str(max(0.0, api_rate)),
        ]
        stage_rows.append({"stage": "translation", **_run(cmd, cwd=project_root)})
    else:
        stage_rows.append({"stage": "translation", "skipped": True, "returncode": 0})

    if not skip_full_formalization:
        cmd = [
            sys.executable,
            "scripts/run_paper_agnostic_suite.py",
            "--suite-json",
            str(suite_path),
            "--project-root",
            str(project_root),
            "--out-progress",
            str(out_root / "full_paper_progress.json"),
            "--max-papers",
            str(max(0, max_papers)),
            "--paper-timeout-s",
            str(max(0, paper_timeout_s)),
            "--max-theorems",
            str(max(0, max_theorems)),
            "--max-passes",
            "1",
            "--prove-repair-rounds",
            "1",
            "--mandatory-retry-rounds",
            "0",
            "--bridge-rounds",
            "1",
            "--bridge-depth",
            "1",
            "--bridge-max-candidates",
            "1",
        ]
        if model.strip():
            cmd.extend(["--model", model.strip()])
        if focus_no_world_model:
            cmd.append("--focus-no-world-model")
        stage_rows.append({"stage": "full_formalization", **_run(cmd, cwd=project_root)})
    else:
        stage_rows.append({"stage": "full_formalization", "skipped": True, "returncode": 0})

    return stage_rows


def _artifact_entry(root: Path, role: str, path: Path, paper_id: str = "") -> dict[str, Any]:
    exists = path.exists()
    row: dict[str, Any] = {
        "role": role,
        "path": _relativize(root, path),
        "exists": exists,
    }
    if paper_id:
        row["paper_id"] = paper_id
    if exists and path.is_file():
        row["size_bytes"] = path.stat().st_size
        row["sha256"] = _sha256(path)
    return row


def _ledger_entries(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
        return [row for row in raw["entries"] if isinstance(row, dict)]
    return []


def _ledger_path(project_root: Path, full_reports_root: Path, paper_id: str) -> Path:
    safe = _safe_id(paper_id)
    committed = full_reports_root / safe / "verification_ledger.json"
    if committed.exists():
        return committed
    return project_root / "output" / "verification_ledgers" / f"{safe}.json"


def build_public_claim_artifacts(
    *,
    project_root: Path,
    suite_path: Path,
    out_root: Path,
    papers: list[dict[str, Any]],
    stage_rows: list[dict[str, Any]],
    ingestion_root: Path,
    translation_root: Path,
    full_reports_root: Path,
    generated_at: str,
    command: list[str],
    mode: str,
) -> dict[str, Path]:
    out_root.mkdir(parents=True, exist_ok=True)

    fetch_rows: list[dict[str, Any]] = []
    theorem_rows: list[dict[str, Any]] = []
    translation_rows: list[dict[str, Any]] = []
    validation_rows: list[dict[str, Any]] = []
    proof_rows: list[dict[str, Any]] = []
    final_ledgers: list[dict[str, Any]] = []
    artifact_rows: list[dict[str, Any]] = []

    for paper in papers:
        paper_id = str(paper.get("paper_id", "")).strip()
        safe = _safe_id(paper_id)

        fetch_path = ingestion_root / safe / "fetch.json"
        extracted_path = ingestion_root / safe / "extracted_theorems.json"
        translation_path = translation_root / safe / "translation_run.json"
        translation_ledger_path = translation_root / safe / "ledger.json"
        translation_checkpoint_path = translation_root / safe / "pipeline_checkpoint.json"
        full_report_path = full_reports_root / safe / "suite_report.json"
        full_manifest_path = full_reports_root / safe / "manifest.json"
        proof_results_path = project_root / "logs" / f"full_paper_{safe}_suite_results.json"
        ledger_path = _ledger_path(project_root, full_reports_root, paper_id)

        artifact_rows.extend(
            [
                _artifact_entry(project_root, "fetched_paper_metadata", fetch_path, paper_id),
                _artifact_entry(project_root, "extracted_theorems", extracted_path, paper_id),
                _artifact_entry(project_root, "translation", translation_path, paper_id),
                _artifact_entry(project_root, "translation_ledger", translation_ledger_path, paper_id),
                _artifact_entry(project_root, "translation_checkpoint", translation_checkpoint_path, paper_id),
                _artifact_entry(project_root, "lean_validation_source_ledger", ledger_path, paper_id),
                _artifact_entry(project_root, "proof_attempts", proof_results_path, paper_id),
                _artifact_entry(project_root, "paper_full_report", full_report_path, paper_id),
                _artifact_entry(project_root, "paper_manifest", full_manifest_path, paper_id),
            ]
        )

        fetch_payload = _read_json(fetch_path)
        fetch_rows.append({"paper_id": paper_id, "domain": paper.get("domain", ""), "artifact": str(fetch_path), "payload": fetch_payload})

        extracted_payload = _read_json(extracted_path)
        theorem_rows.append(
            {
                "paper_id": paper_id,
                "artifact": str(extracted_path),
                "theorem_count": int((extracted_payload or {}).get("theorem_count", 0) or 0) if isinstance(extracted_payload, dict) else 0,
                "entries": (extracted_payload or {}).get("entries", []) if isinstance(extracted_payload, dict) else [],
            }
        )

        translation_payload = _read_json(translation_path)
        translation_rows.append(
            {
                "paper_id": paper_id,
                "artifact": str(translation_path),
                "ledger": str(translation_ledger_path),
                "checkpoint": str(translation_checkpoint_path),
                "payload": translation_payload,
            }
        )

        ledger_payload = _read_json(ledger_path)
        entries = _ledger_entries(ledger_payload)
        final_ledgers.append(
            {
                "paper_id": paper_id,
                "ledger": str(ledger_path),
                "entry_count": len(entries),
                "payload": ledger_payload,
            }
        )
        validation_rows.append(
            {
                "paper_id": paper_id,
                "ledger": str(ledger_path),
                "entries": [
                    {
                        "theorem_name": row.get("theorem_name", ""),
                        "status": row.get("status", ""),
                        "lean_statement": row.get("lean_statement", ""),
                        "validation_gates": row.get("validation_gates", {}),
                        "step_obligations": row.get("step_obligations", []),
                        "error_message": row.get("error_message", ""),
                    }
                    for row in entries
                ],
            }
        )

        proof_payload = _read_json(proof_results_path)
        report_payload = _read_json(full_report_path)
        proof_rows.append(
            {
                "paper_id": paper_id,
                "proof_results": str(proof_results_path),
                "proof_results_payload": proof_payload,
                "full_report": str(full_report_path),
                "pass_history": report_payload.get("pass_history", []) if isinstance(report_payload, dict) else [],
                "steps": report_payload.get("steps", []) if isinstance(report_payload, dict) else [],
            }
        )

    aggregate_report_path = out_root / "full_report.json"
    report_cmd = [
        sys.executable,
        "scripts/paper_agnostic_report.py",
        "--ledger-dir",
        str(project_root / "output" / "verification_ledgers"),
        "--suite-json",
        str(suite_path),
        "--toolchain-file",
        str(project_root / "lean-toolchain"),
        "--out-json",
        str(aggregate_report_path),
    ]
    report_stage = _run(report_cmd, cwd=project_root)
    stage_rows.append({"stage": "aggregate_report", **report_stage})
    aggregate_payload = _read_json(aggregate_report_path)
    aggregate_has_rows = (
        isinstance(aggregate_payload, dict)
        and int(aggregate_payload.get("papers_evaluated", 0) or 0) > 0
    )
    if not aggregate_report_path.exists() or (not aggregate_has_rows and any(int(row.get("entry_count", 0) or 0) for row in final_ledgers)):
        fallback_report = {
            "schema_version": "1.0.0",
            "suite_json": str(suite_path),
            "source": "reproduce_public_claims_fallback_from_collected_ledgers",
            "papers_evaluated": len(final_ledgers),
            "theorems_evaluated": sum(int(row.get("entry_count", 0) or 0) for row in final_ledgers),
            "papers": [{"paper_id": row["paper_id"], "ledger": row["ledger"], "theorems": row["entry_count"]} for row in final_ledgers],
        }
        _write_json(aggregate_report_path, fallback_report)

    repair_dataset_path = out_root / "compiler_feedback_repair_dataset.jsonl"
    repair_dataset_summary_path = out_root / "compiler_feedback_repair_dataset_summary.json"
    repair_started = time.time()
    try:
        from export_april_repair_dataset import export_dataset

        repair_result = export_dataset(
            input_paths=[project_root / "output" / "verification_ledgers", full_reports_root, project_root / "logs"],
            run_roots=[project_root / "output" / "flywheel" / "runs"],
            out_jsonl=repair_dataset_path,
            out_summary=repair_dataset_summary_path,
        )
        stage_rows.append(
            {
                "stage": "compiler_feedback_repair_dataset",
                "returncode": 0,
                "elapsed_s": round(time.time() - repair_started, 3),
                "stdout_tail": json.dumps(repair_result, ensure_ascii=False)[-5000:],
                "stderr_tail": "",
            }
        )
    except Exception as exc:
        stage_rows.append(
            {
                "stage": "compiler_feedback_repair_dataset",
                "returncode": 1,
                "elapsed_s": round(time.time() - repair_started, 3),
                "stdout_tail": "",
                "stderr_tail": str(exc)[-5000:],
            }
        )

    claim_review_queue_path = out_root / "claim_equivalence_review_queue.jsonl"
    claim_adjudications_path = out_root / "claim_equivalence_adjudications.jsonl"
    claim_review_lines: list[str] = []
    claim_adjudication_lines: list[str] = []
    for paper in papers:
        paper_id = str(paper.get("paper_id", "")).strip()
        safe = _safe_id(paper_id)
        for candidate in (
            project_root / "output" / "claim_equivalence" / "review_queue" / f"{safe}.jsonl",
            full_reports_root / safe / "claim_equivalence_review_queue.jsonl",
        ):
            if candidate.exists():
                claim_review_lines.extend(candidate.read_text(encoding="utf-8", errors="replace").splitlines())
        for candidate in (
            project_root / "output" / "claim_equivalence" / "adjudications" / f"{safe}.jsonl",
            full_reports_root / safe / "claim_equivalence_adjudications.jsonl",
        ):
            if candidate.exists():
                claim_adjudication_lines.extend(candidate.read_text(encoding="utf-8", errors="replace").splitlines())
    claim_review_queue_path.write_text("\n".join(line for line in claim_review_lines if line.strip()) + ("\n" if claim_review_lines else ""), encoding="utf-8")
    claim_adjudications_path.write_text("\n".join(line for line in claim_adjudication_lines if line.strip()) + ("\n" if claim_adjudication_lines else ""), encoding="utf-8")

    output_paths = {
        "fetched_paper_metadata": out_root / "fetched_paper_metadata.json",
        "extracted_theorems": out_root / "extracted_theorems.json",
        "translation": out_root / "translation.json",
        "lean_validation": out_root / "lean_validation.json",
        "proof_attempts": out_root / "proof_attempts.json",
        "final_ledger": out_root / "final_ledger.json",
        "full_report": aggregate_report_path,
        "compiler_feedback_repair_dataset": repair_dataset_path,
        "compiler_feedback_repair_dataset_summary": repair_dataset_summary_path,
        "claim_equivalence_review_queue": claim_review_queue_path,
        "claim_equivalence_adjudications": claim_adjudications_path,
        "manifest": out_root / "manifest.json",
    }

    common_meta = {
        "schema_version": "1.0.0",
        "generated_at": generated_at,
        "mode": mode,
        "suite_json": str(suite_path),
        "papers": [str(paper.get("paper_id", "")).strip() for paper in papers],
    }
    _write_json(output_paths["fetched_paper_metadata"], {**common_meta, "rows": fetch_rows})
    _write_json(output_paths["extracted_theorems"], {**common_meta, "rows": theorem_rows})
    _write_json(output_paths["translation"], {**common_meta, "rows": translation_rows})
    _write_json(output_paths["lean_validation"], {**common_meta, "rows": validation_rows})
    _write_json(output_paths["proof_attempts"], {**common_meta, "rows": proof_rows})
    _write_json(output_paths["final_ledger"], {**common_meta, "rows": final_ledgers})

    artifact_rows.extend(_artifact_entry(project_root, role, path) for role, path in output_paths.items() if role != "manifest")
    missing = [row for row in artifact_rows if not row.get("exists")]
    manifest_payload = {
        **common_meta,
        "command": command,
        "public_artifacts": {role: str(path) for role, path in output_paths.items()},
        "stages": stage_rows,
        "artifacts": artifact_rows,
        "missing_artifacts": missing,
        "all_required_artifacts_present": all(output_paths[role].exists() for role in PUBLIC_ARTIFACTS if role != "manifest"),
    }
    _write_json(output_paths["manifest"], manifest_payload)
    return output_paths


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reproduce public paper-agnostic claim artifacts with one command.")
    parser.add_argument("--suite", "--suite-json", dest="suite", default="reproducibility/paper_agnostic_golden10.json")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--out-root", default="output/reproducibility/public_claims")
    parser.add_argument("--max-papers", type=int, default=0, help="0 = all papers in suite")
    parser.add_argument("--max-theorems", type=int, default=0, help="0 = runner default/all")
    parser.add_argument("--paper-timeout-s", type=int, default=0, help="0 = no timeout")
    parser.add_argument("--model", default="")
    parser.add_argument("--api-rate", type=float, default=0.2)
    parser.add_argument("--focus-no-world-model", action="store_true")
    parser.add_argument("--smoke", action="store_true", help="CI-friendly no-network/no-API evidence indexing smoke run")
    parser.add_argument("--skip-translation", action="store_true", help="Skip translation runner in full mode")
    parser.add_argument("--skip-full-formalization", action="store_true", help="Skip full-paper runner in full mode")
    parser.add_argument("--ingestion-root", default="", help="Override fetched/extracted evidence root")
    parser.add_argument("--translation-root", default="", help="Override translation evidence root")
    parser.add_argument("--full-reports-root", default="reproducibility/full_paper_reports")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    project_root = Path(args.project_root).resolve()
    suite_path = _resolve(project_root, args.suite)
    out_root = _resolve(project_root, args.out_root)
    max_papers = int(args.max_papers)
    if args.smoke and max_papers <= 0:
        max_papers = 1

    papers = _selected_papers(suite_path, max_papers)
    if not papers:
        print(json.dumps({"ok": False, "reason": "empty_suite", "suite": str(suite_path)}, indent=2))
        return 1

    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    stage_rows: list[dict[str, Any]] = []

    if args.smoke:
        stage_rows.append(
            {
                "stage": "smoke_reindex_existing_evidence",
                "returncode": 0,
                "note": "No arXiv, API, or Lean calls were made.",
            }
        )
        ingestion_root = _resolve(project_root, args.ingestion_root or "reproducibility/paper_agnostic_golden10_results")
        translation_root = _resolve(project_root, args.translation_root or "reproducibility/paper_agnostic_golden10_translation")
    else:
        ingestion_root = _resolve(project_root, args.ingestion_root or out_root / "ingestion")
        translation_root = _resolve(project_root, args.translation_root or out_root / "translation")
        stage_rows.extend(
            run_full_pipeline(
                project_root=project_root,
                suite_path=suite_path,
                out_root=out_root,
                max_papers=max_papers,
                max_theorems=max(0, int(args.max_theorems)),
                paper_timeout_s=max(0, int(args.paper_timeout_s)),
                model=str(args.model),
                api_rate=float(args.api_rate),
                focus_no_world_model=bool(args.focus_no_world_model),
                skip_translation=bool(args.skip_translation),
                skip_full_formalization=bool(args.skip_full_formalization),
            )
        )

    paths = build_public_claim_artifacts(
        project_root=project_root,
        suite_path=suite_path,
        out_root=out_root,
        papers=papers,
        stage_rows=stage_rows,
        ingestion_root=ingestion_root,
        translation_root=translation_root,
        full_reports_root=_resolve(project_root, args.full_reports_root),
        generated_at=generated_at,
        command=[sys.executable, "scripts/reproduce_public_claims.py", *sys.argv[1:]],
        mode="smoke" if args.smoke else "full",
    )

    manifest = _read_json(paths["manifest"])
    missing_count = len(manifest.get("missing_artifacts", [])) if isinstance(manifest, dict) else 0
    print(
        json.dumps(
            {
                "ok": True,
                "mode": "smoke" if args.smoke else "full",
                "papers": len(papers),
                "out_root": str(out_root),
                "manifest": str(paths["manifest"]),
                "missing_artifacts": missing_count,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
