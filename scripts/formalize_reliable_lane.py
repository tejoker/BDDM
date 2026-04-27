#!/usr/bin/env python3
"""Reliable-lane formalization runner for high-probability paper classes.

Pipeline:
1) readiness scoring
2) actionable theorem regeneration
3) strict proof run on actionable slice
4) strict closure checklist on reliable-lane ledger
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any


def _safe_id(paper_id: str) -> str:
    return paper_id.replace("/", "_").replace(":", "_")


def _run(cmd: list[str], cwd: Path) -> dict[str, Any]:
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    return {
        "cmd": cmd,
        "returncode": int(proc.returncode),
        "elapsed_s": round(time.time() - t0, 3),
        "stdout_tail": (proc.stdout or "")[-2500:],
        "stderr_tail": (proc.stderr or "")[-2500:],
    }


def run_reliable_lane(
    *,
    paper_id: str,
    project_root: Path,
    min_readiness_score: float,
    force: bool,
) -> dict[str, Any]:
    safe = _safe_id(paper_id)
    steps: list[dict[str, Any]] = []

    readiness_out = project_root / "output" / "reports" / "full_paper" / f"{safe}_readiness.json"
    actionable_lean = project_root / "output" / f"{safe}_actionable.lean"
    reliable_paper_id = f"{paper_id}_reliable"
    reliable_ledger = project_root / "output" / "verification_ledgers" / f"{_safe_id(reliable_paper_id)}.json"
    prove_results = project_root / "logs" / f"proof_batch_results_reliable_{safe}.json"
    checklist_json = project_root / "output" / "reports" / "full_paper" / f"{safe}_reliable_closure_checklist.json"
    checklist_md = project_root / "output" / "reports" / "full_paper" / f"{safe}_reliable_closure_checklist.md"

    cmd_score = [
        "python3",
        "scripts/paper_readiness_score.py",
        "--paper-id",
        paper_id,
        "--ledger-root",
        "output/verification_ledgers",
        "--out",
        str(readiness_out),
    ]
    s_score = _run(cmd_score, cwd=project_root)
    steps.append({"stage": "readiness_score", **s_score})
    readiness: dict[str, Any] = {}
    if readiness_out.exists():
        try:
            readiness = json.loads(readiness_out.read_text(encoding="utf-8"))
        except Exception:
            readiness = {}
    readiness_score = float(readiness.get("readiness_score", 0.0) or 0.0)
    readiness_class = str(readiness.get("readiness_class", "C"))
    if (not force) and readiness_score < float(min_readiness_score):
        return {
            "paper_id": paper_id,
            "ok": False,
            "skipped": True,
            "reason": f"readiness_score_below_threshold ({readiness_score:.4f} < {float(min_readiness_score):.4f})",
            "readiness_class": readiness_class,
            "readiness_score": readiness_score,
            "steps": steps,
        }

    cmd_regen = [
        "python3",
        "scripts/regenerate_actionable_theorems.py",
        "--paper-id",
        paper_id,
        "--ledger-root",
        "output/verification_ledgers",
        "--out",
        str(actionable_lean),
    ]
    s_regen = _run(cmd_regen, cwd=project_root)
    steps.append({"stage": "regenerate_actionable", **s_regen})

    # Hard reset reliable-lane ledger scope for this run so closure denominator
    # reflects only the current actionable cohort, not stale historical entries.
    reset_info = {"stage": "reset_reliable_ledger", "ledger_path": str(reliable_ledger), "removed": False}
    try:
        if reliable_ledger.exists():
            reliable_ledger.unlink()
            reset_info["removed"] = True
    except Exception as exc:
        reset_info["error"] = str(exc)
    steps.append(reset_info)

    cmd_prove = [
        "python3",
        "scripts/prove_arxiv_batch.py",
        "--lean-file",
        str(actionable_lean),
        "--project-root",
        str(project_root),
        "--paper-id",
        reliable_paper_id,
        "--mode",
        "full-draft",
        "--repair-rounds",
        "5",
        "--bridge-loop",
        "--bridge-rounds",
        "1",
        "--bridge-depth",
        "2",
        "--bridge-max-candidates",
        "2",
        "--strict-context-pack",
        "--strict-assumption-slots",
        "--mandatory-retry-rounds",
        "1",
        "--results-file",
        str(prove_results),
    ]
    s_prove = _run(cmd_prove, cwd=project_root)
    steps.append({"stage": "prove_actionable_strict", **s_prove})

    cmd_check = [
        "python3",
        "scripts/paper_closure_checklist.py",
        "--paper-id",
        reliable_paper_id,
        "--ledger-root",
        "output/verification_ledgers",
        "--out-json",
        str(checklist_json),
        "--out-md",
        str(checklist_md),
    ]
    s_check = _run(cmd_check, cwd=project_root)
    steps.append({"stage": "closure_checklist", **s_check})

    checklist: dict[str, Any] = {}
    if checklist_json.exists():
        try:
            checklist = json.loads(checklist_json.read_text(encoding="utf-8"))
        except Exception:
            checklist = {}

    return {
        "paper_id": paper_id,
        "reliable_paper_id": reliable_paper_id,
        "ok": bool(float(checklist.get("closure_rate", 0.0) or 0.0) > 0.0),
        "readiness_class": readiness_class,
        "readiness_score": readiness_score,
        "closure_rate": float(checklist.get("closure_rate", 0.0) or 0.0),
        "fully_proven": int(checklist.get("fully_proven", 0) or 0),
        "total_theorems": int(checklist.get("total_theorems", 0) or 0),
        "outputs": {
            "readiness": str(readiness_out),
            "actionable_lean": str(actionable_lean),
            "prove_results": str(prove_results),
            "checklist_json": str(checklist_json),
            "checklist_md": str(checklist_md),
        },
        "steps": steps,
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run reliable-lane formalization workflow")
    p.add_argument("--paper-id", required=True)
    p.add_argument("--project-root", default=".")
    p.add_argument("--min-readiness-score", type=float, default=0.45)
    p.add_argument("--force", action="store_true")
    p.add_argument("--out", default="")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    payload = run_reliable_lane(
        paper_id=args.paper_id,
        project_root=Path(args.project_root).resolve(),
        min_readiness_score=float(args.min_readiness_score),
        force=bool(args.force),
    )
    txt = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(txt, encoding="utf-8")
    print(txt)
    return 0 if payload.get("ok", False) else 2


if __name__ == "__main__":
    raise SystemExit(main())
