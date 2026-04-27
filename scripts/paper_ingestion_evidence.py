#!/usr/bin/env python3
"""Fetch a paper suite and persist extraction evidence artifacts."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from arxiv_fetcher import fetch_source, find_main_tex
from latex_preprocessor import (
    collect_definitions,
    collect_root_tex_paths,
    expand_include_tree,
    register_env_aliases,
    write_expanded_roots,
)
from theorem_extractor import extract_from_files


def _safe_id(text: str) -> str:
    return str(text).replace("/", "_").replace(":", "_")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _paper_status(row: dict[str, Any]) -> str:
    if not row.get("fetch_ok"):
        return "EXTRACTION_FAILED"
    if int(row.get("theorem_count", 0) or 0) <= 0:
        return "EXTRACTION_FAILED"
    return "VALID_STATEMENT_UNPROVEN"


def _dominant_blocker(row: dict[str, Any]) -> str:
    if not row.get("fetch_ok"):
        return "missing_latex_source"
    if int(row.get("theorem_count", 0) or 0) <= 0:
        return "theorem_extraction"
    if not row.get("mistral_api_key_set"):
        return "api_or_runtime_failure"
    return "proof_search_exhausted"


def run_paper(
    paper: dict[str, Any],
    *,
    source_root: Path,
    evidence_root: Path,
    max_theorems_output: int,
) -> dict[str, Any]:
    paper_id = str(paper.get("paper_id", "")).strip()
    safe = _safe_id(paper_id)
    source_dir = source_root / safe
    expanded_dir = source_root / f"{safe}_expanded"
    evidence_dir = evidence_root / safe

    started = time.time()
    row: dict[str, Any] = {
        "paper_id": paper_id,
        "domain": paper.get("domain", "unknown"),
        "role": paper.get("role", ""),
        "fetch_ok": False,
        "tex_file_count": 0,
        "root_tex_count": 0,
        "expanded_root_count": 0,
        "macro_count": 0,
        "environment_alias_count": 0,
        "theorem_count": 0,
        "mistral_api_key_set": bool(os.getenv("MISTRAL_API_KEY", "").strip()),
        "translation_status": "not_run_missing_mistral_api_key",
        "proof_status": "not_run_missing_mistral_api_key",
        "error": "",
    }

    try:
        tex_paths = fetch_source(paper_id, source_dir)
        row["fetch_ok"] = True
        row["tex_file_count"] = len(tex_paths)
        main_tex = find_main_tex(tex_paths)
        row["main_tex"] = str(main_tex)

        roots = collect_root_tex_paths(tex_paths, main_tex=main_tex)
        macros, aliases = collect_definitions(tex_paths)
        register_env_aliases(aliases)
        expanded_paths = write_expanded_roots(
            root_tex_paths=roots,
            source_root=main_tex.parent,
            output_root=expanded_dir,
            macro_defs=macros,
        )
        entries = extract_from_files(expanded_paths)

        row["root_tex_count"] = len(roots)
        row["expanded_root_count"] = len(expanded_paths)
        row["macro_count"] = len(macros)
        row["environment_alias_count"] = len(aliases)
        row["theorem_count"] = len(entries)

        theorem_rows = [asdict(entry) for entry in entries]
        if max_theorems_output > 0:
            theorem_rows = theorem_rows[:max_theorems_output]
            row["theorem_output_truncated"] = len(entries) > len(theorem_rows)
        else:
            row["theorem_output_truncated"] = False

        _write_json(
            evidence_dir / "fetch.json",
            {
                "paper_id": paper_id,
                "fetch_ok": True,
                "tex_files": [str(p) for p in tex_paths],
                "main_tex": str(main_tex),
                "root_tex_paths": [str(p) for p in roots],
                "expanded_paths": [str(p) for p in expanded_paths],
            },
        )
        _write_json(
            evidence_dir / "extracted_theorems.json",
            {
                "paper_id": paper_id,
                "theorem_count": len(entries),
                "entries": theorem_rows,
            },
        )
    except Exception as exc:
        row["error"] = str(exc)
        _write_json(evidence_dir / "fetch.json", {"paper_id": paper_id, "fetch_ok": False, "error": str(exc)})
        _write_json(evidence_dir / "extracted_theorems.json", {"paper_id": paper_id, "theorem_count": 0, "entries": []})

    row["paper_status"] = _paper_status(row)
    row["dominant_blocker"] = _dominant_blocker(row)
    row["elapsed_s"] = round(time.time() - started, 3)
    _write_json(evidence_dir / "summary.json", row)
    return row


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch suite papers and persist ingestion evidence")
    parser.add_argument("--suite-json", default="reproducibility/paper_agnostic_golden10.json")
    parser.add_argument("--source-root", default="output/paper_sources/golden10")
    parser.add_argument("--evidence-root", default="reproducibility/paper_agnostic_golden10_results")
    parser.add_argument("--max-papers", type=int, default=0)
    parser.add_argument("--max-theorems-output", type=int, default=0, help="0 = include all extracted theorem rows")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    suite = _read_json(Path(args.suite_json))
    papers = suite.get("papers", []) if isinstance(suite, dict) else []
    if not isinstance(papers, list) or not papers:
        print(json.dumps({"ok": False, "reason": "empty_suite", "suite_json": args.suite_json}, indent=2))
        return 1
    if int(args.max_papers) > 0:
        papers = papers[: int(args.max_papers)]

    source_root = Path(args.source_root)
    evidence_root = Path(args.evidence_root)
    rows = [
        run_paper(
            paper,
            source_root=source_root,
            evidence_root=evidence_root,
            max_theorems_output=max(0, int(args.max_theorems_output)),
        )
        for paper in papers
        if isinstance(paper, dict)
    ]

    summary = {
        "schema_version": "1.0.0",
        "suite_json": args.suite_json,
        "source_root": str(source_root),
        "evidence_root": str(evidence_root),
        "papers_attempted": len(rows),
        "papers_fetched": sum(1 for row in rows if row.get("fetch_ok")),
        "papers_with_theorems": sum(1 for row in rows if int(row.get("theorem_count", 0) or 0) > 0),
        "theorems_extracted": sum(int(row.get("theorem_count", 0) or 0) for row in rows),
        "mistral_api_key_set": bool(os.getenv("MISTRAL_API_KEY", "").strip()),
        "rows": rows,
    }
    _write_json(evidence_root / "summary.json", summary)
    print(json.dumps({"ok": True, **{k: summary[k] for k in ("papers_attempted", "papers_fetched", "papers_with_theorems", "theorems_extracted")}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
