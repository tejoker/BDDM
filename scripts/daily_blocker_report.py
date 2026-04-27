#!/usr/bin/env python3
"""Daily theorem-closure blocker report (gold fidelity + closure + review queue)."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any


def _run_json(cmd: list[str], cwd: Path, timeout_s: int = 1200) -> dict[str, Any]:
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=max(30, int(timeout_s)),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout_after_{int(timeout_s)}s", "cmd": cmd[:6]}
    if p.returncode != 0:
        return {"ok": False, "error": (p.stderr or p.stdout or "")[:600], "returncode": int(p.returncode)}
    try:
        raw = json.loads(p.stdout)
    except Exception:
        return {"ok": False, "error": "non_json_output", "stdout": (p.stdout or "")[:600]}
    if isinstance(raw, dict):
        raw["ok"] = True
        return raw
    return {"ok": False, "error": "json_not_object"}


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _top_review_items(payload: dict[str, Any], n: int = 10) -> list[dict[str, Any]]:
    q = payload.get("review_queue", [])
    if not isinstance(q, list):
        return []
    out: list[dict[str, Any]] = []
    for row in q[: max(1, n)]:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "theorem_name": str(row.get("theorem_name", "")),
                "status": str(row.get("status", "")),
                "claim_equivalence_verdict": str(row.get("claim_equivalence_verdict", "")),
                "failed_checks": list(row.get("failed_checks", [])),
            }
        )
    return out


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate daily blocker report for theorem closure")
    p.add_argument("--project-root", default=".")
    p.add_argument("--paper-id", required=True)
    p.add_argument("--gold-jsonl", default="reproducibility/gold_translation_tiny_hard20.jsonl")
    p.add_argument("--kg-db", action="append", default=[])
    p.add_argument("--closure-json", default="")
    p.add_argument("--review-queue-json", default="")
    p.add_argument("--out-json", default="output/reports/daily/daily_blocker_report.json")
    p.add_argument("--out-md", default="output/reports/daily/daily_blocker_report.md")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    root = Path(args.project_root).resolve()
    safe = args.paper_id.replace("/", "_").replace(":", "_")
    closure_json = Path(args.closure_json) if args.closure_json else Path(f"output/reports/full_paper/{safe}_closure_checklist.json")
    review_json = Path(args.review_queue_json) if args.review_queue_json else Path(f"output/reports/review_queue/{safe}_review_queue.json")

    cmd = [
        "python3",
        "scripts/eval_translation_fidelity.py",
        "--gold",
        str(Path(args.gold_jsonl)),
        "--min-score",
        "0.0",
    ]
    for db in list(args.kg_db or []):
        if str(db).strip():
            cmd.extend(["--kg-db", str(Path(db))])
    fidelity = _run_json(cmd, cwd=root, timeout_s=1200)
    closure = _load(closure_json)
    review = _load(review_json)

    payload = {
        "generated_at_unix": int(time.time()),
        "paper_id": args.paper_id,
        "gold_fidelity": {
            "ok": bool(fidelity.get("ok", False)),
            "gold_count": int(fidelity.get("gold_count", 0) or 0),
            "avg_fidelity_f1": float(fidelity.get("avg_fidelity_f1", 0.0) or 0.0),
        },
        "closure": {
            "total_theorems": int(closure.get("total_theorems", 0) or 0),
            "fully_proven": int(closure.get("fully_proven", 0) or 0),
            "closure_rate": float(closure.get("closure_rate", 0.0) or 0.0),
            "top_blockers": list(closure.get("top_blockers", []))[:10],
        },
        "review_queue": {
            "count": int(review.get("review_queue_count", 0) or 0),
            "top_items": _top_review_items(review, n=10),
        },
        "next_actions": [
            "Patch highest-frequency translation_fidelity failure pattern on tiny gold set.",
            "Adjudicate claim_equivalence for top review queue items and relabel stronger/weaker/unclear.",
            "Run focused proof closure retries only on equivalent theorems.",
        ],
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    md_lines = [
        f"# Daily Blocker Report ({args.paper_id})",
        "",
        "## Gold Fidelity",
        f"- gold_count: `{payload['gold_fidelity']['gold_count']}`",
        f"- avg_fidelity_f1: `{payload['gold_fidelity']['avg_fidelity_f1']}`",
        "",
        "## Closure",
        f"- total_theorems: `{payload['closure']['total_theorems']}`",
        f"- fully_proven: `{payload['closure']['fully_proven']}`",
        f"- closure_rate: `{payload['closure']['closure_rate']}`",
        "",
        "## Top Blockers",
    ]
    for row in payload["closure"]["top_blockers"]:
        if isinstance(row, dict):
            md_lines.append(f"- {row.get('check', '')}: {row.get('count', 0)}")
    md_lines.extend(["", "## Next Actions"])
    for a in payload["next_actions"]:
        md_lines.append(f"- {a}")
    out_md = Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(json.dumps({"out_json": str(out_json), "out_md": str(out_md)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
