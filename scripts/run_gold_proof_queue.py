#!/usr/bin/env python3
"""Run or dry-run proof search for strict gold-proof queue rows."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from build_gold_proof_queue import DEFAULT_OUT_JSONL, proof_candidate_blockers


DEFAULT_OUT_SUMMARY = Path("output/corpus/gold_proof_queue_run_summary.json")


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


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _lean_name(statement: str, fallback: str) -> str:
    match = re.search(
        r"^\s*(?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma|def)\s+([A-Za-z_][A-Za-z0-9_'.]*)\b",
        statement or "",
        flags=re.MULTILINE,
    )
    if match:
        return match.group(1).rsplit(".", 1)[-1]
    return str(fallback or "").rsplit(".", 1)[-1]


def _artifact_path(row: dict[str, Any], project_root: Path) -> str:
    artifacts = row.get("artifact_paths") if isinstance(row.get("artifact_paths"), dict) else {}
    for key in ("lean_file", "out_lean"):
        raw = str(artifacts.get(key, "") or "").strip()
        if raw:
            p = Path(raw)
            return str(p if p.is_absolute() else project_root / p)
    return ""


def _sorry_declarations(lean_file: Path) -> dict[str, str]:
    if not lean_file.exists():
        return {}
    text = lean_file.read_text(encoding="utf-8", errors="replace")
    declarations: dict[str, str] = {}
    pattern = re.compile(
        r"^\s*((?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_'.]*)\b[\s\S]*?:=\s*by(?:\s+sorry\b|\s*\n\s*sorry\b))",
        flags=re.MULTILINE,
    )
    for match in pattern.finditer(text):
        decl = match.group(1)
        raw = match.group(2)
        declarations[raw] = decl
        declarations[raw.rsplit(".", 1)[-1]] = decl
    return declarations


def _sorry_names(lean_file: Path) -> set[str]:
    return set(_sorry_declarations(lean_file))


def build_gold_proof_runs(
    rows: list[dict[str, Any]],
    *,
    project_root: Path,
    queue_jsonl: Path = DEFAULT_OUT_JSONL,
    mode: str = "full-draft",
    repair_rounds: int = 5,
    max_theorems: int = 0,
    extra_args: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    rejected_counts: Counter[str] = Counter()
    for row in rows:
        blockers = proof_candidate_blockers(row)
        if blockers:
            rejected_counts.update(blockers)
            continue
        paper_id = str(row.get("arxiv_id", "") or row.get("paper_id", "") or "").strip()
        lean_file = _artifact_path(row, project_root)
        theorem = _lean_name(str(row.get("lean_statement", "") or ""), str(row.get("theorem_id", "") or ""))
        if not paper_id:
            rejected_counts["paper_id_missing"] += 1
            continue
        if not lean_file:
            rejected_counts["lean_file_missing"] += 1
            continue
        if not theorem:
            rejected_counts["theorem_name_missing"] += 1
            continue
        lean_path = Path(lean_file)
        if lean_path.exists():
            current_sorries = _sorry_declarations(lean_path)
            current_decl = current_sorries.get(theorem)
            if not current_decl:
                rejected_counts["target_not_in_current_sorry_set"] += 1
                continue
            current_row = {**row, "lean_statement": current_decl}
            current_blockers = proof_candidate_blockers(current_row)
            if current_blockers:
                rejected_counts.update(f"current_lean_statement_blocked:{blocker}" for blocker in current_blockers)
                continue
        accepted.append({**row, "_paper_id": paper_id, "_lean_file": lean_file, "_theorem_name": theorem})

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in accepted[: max_theorems or None]:
        groups[(str(row["_paper_id"]), str(row["_lean_file"]))].append(row)

    commands: list[dict[str, Any]] = []
    for (paper_id, lean_file), group_rows in sorted(groups.items()):
        cmd = [
            sys.executable,
            "scripts/prove_arxiv_batch.py",
            "--project-root",
            str(project_root),
            "--lean-file",
            lean_file,
            "--paper-id",
            paper_id,
            "--mode",
            mode,
            "--repair-rounds",
            str(repair_rounds),
            "--gold-proof-queue-jsonl",
            str(queue_jsonl if queue_jsonl.is_absolute() else project_root / queue_jsonl),
        ]
        for row in group_rows:
            cmd.extend(["--target-theorem", str(row["_theorem_name"])])
        cmd.extend(extra_args or [])
        commands.append(
            {
                "paper_id": paper_id,
                "lean_file": lean_file,
                "rows": len(group_rows),
                "theorems": [str(row["_theorem_name"]) for row in group_rows],
                "command": cmd,
            }
        )

    summary = {
        "schema_version": "gold_proof_queue_run_summary.v1",
        "input_rows": len(rows),
        "accepted_rows": len(accepted),
        "rejected_rows": len(rows) - len(accepted),
        "rejection_reason_counts": dict(rejected_counts.most_common()),
        "command_groups": len(commands),
        "dry_run_safe": True,
        "honest_scope": "Consumes strict gold-proof queue candidates only; proof closure still requires Lean-verified results.",
    }
    return commands, summary


def export_gold_proof_run(
    *,
    queue_jsonl: Path,
    project_root: Path,
    out_summary: Path,
    execute: bool,
    mode: str,
    repair_rounds: int,
    max_theorems: int,
    extra_args: list[str],
) -> dict[str, Any]:
    rows = _read_jsonl(queue_jsonl)
    commands, summary = build_gold_proof_runs(
        rows,
        project_root=project_root,
        queue_jsonl=queue_jsonl,
        mode=mode,
        repair_rounds=repair_rounds,
        max_theorems=max_theorems,
        extra_args=extra_args,
    )
    executions: list[dict[str, Any]] = []
    if execute:
        for group in commands:
            proc = subprocess.run(
                group["command"],
                cwd=project_root,
                text=True,
                capture_output=True,
            )
            executions.append(
                {
                    "paper_id": group["paper_id"],
                    "lean_file": group["lean_file"],
                    "returncode": proc.returncode,
                    "stdout_tail": (proc.stdout or "")[-2000:],
                    "stderr_tail": (proc.stderr or "")[-2000:],
                }
            )
    result = {
        **summary,
        "execute": bool(execute),
        "commands": commands,
        "executions": executions,
        "out_summary": str(out_summary),
    }
    _write_json(out_summary, result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run strict gold-proof queue proof search")
    parser.add_argument("--queue-jsonl", type=Path, default=DEFAULT_OUT_JSONL)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--out-summary", type=Path, default=DEFAULT_OUT_SUMMARY)
    parser.add_argument("--mode", default="full-draft")
    parser.add_argument("--repair-rounds", type=int, default=5)
    parser.add_argument("--max-theorems", type=int, default=0)
    parser.add_argument("--execute", action="store_true", help="Actually invoke prove_arxiv_batch.py; default only writes commands.")
    parser.add_argument("extra_args", nargs=argparse.REMAINDER, help="Optional args passed after -- to prove_arxiv_batch.py")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    extra = list(args.extra_args or [])
    if extra and extra[0] == "--":
        extra = extra[1:]
    result = export_gold_proof_run(
        queue_jsonl=args.queue_jsonl,
        project_root=args.project_root.resolve(),
        out_summary=args.out_summary,
        execute=bool(args.execute),
        mode=args.mode,
        repair_rounds=args.repair_rounds,
        max_theorems=args.max_theorems,
        extra_args=extra,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
