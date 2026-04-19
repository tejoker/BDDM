#!/usr/bin/env python3
"""Generate weekly benchmark report artifacts (JSON + Markdown)."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any


def _run_json_cmd(cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip()[:500], "stdout": proc.stdout.strip()[:500]}
    try:
        payload = json.loads(proc.stdout)
    except Exception:
        return {"ok": False, "error": "non_json_output", "stdout": proc.stdout.strip()[:500]}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "non_object_json"}
    payload["ok"] = True
    return payload


def generate_report(
    *,
    ledger_root: Path,
    kg_db: Path,
    out_dir: Path,
    limit: int,
    budget: int,
    max_depth: int,
    max_candidates: int,
    gold_path: Path | None,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    wm = _run_json_cmd(
        [
            "python3",
            "scripts/benchmark_bridge_world_model.py",
            "--ledger-root",
            str(ledger_root),
            "--limit",
            str(limit),
            "--budget",
            str(budget),
            "--max-depth",
            str(max_depth),
            "--max-candidates-per-assumption",
            str(max_candidates),
        ]
    )
    gold = {}
    if gold_path and gold_path.exists():
        gold = _run_json_cmd(
            [
                "python3",
                "scripts/gold_linkage_eval.py",
                "--gold",
                str(gold_path),
                "--kg-db",
                str(kg_db),
            ]
        )
    fidelity = _run_json_cmd(
        [
            "python3",
            "scripts/eval_translation_fidelity.py",
            "--gold",
            "reproducibility/gold_translation_fidelity.jsonl",
            "--kg-db",
            str(kg_db),
            "--min-score",
            "0.0",
        ]
    )
    payload = {
        "generated_at_unix": int(time.time()),
        "benchmark_world_model": wm,
        "gold_linkage": gold,
        "translation_fidelity": fidelity,
    }
    return payload


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    wm = payload.get("benchmark_world_model", {}) or {}
    gl = payload.get("gold_linkage", {}) or {}
    tf = payload.get("translation_fidelity", {}) or {}
    lines = [
        "# Weekly KG + Bridge Report",
        "",
        f"- generated_at_unix: `{payload.get('generated_at_unix', 0)}`",
        "",
        "## Bridge Benchmark",
        f"- ok: `{wm.get('ok', False)}`",
        f"- count: `{wm.get('count', 0)}`",
        f"- world_model_grounded_total: `{wm.get('world_model_grounded_total', 0)}`",
        f"- baseline_grounded_total: `{wm.get('baseline_grounded_total', 0)}`",
        f"- delta_grounded: `{wm.get('delta_grounded', 0)}`",
        "",
        "## Gold Linkage",
        f"- ok: `{gl.get('ok', False)}`",
        f"- precision: `{gl.get('precision', 'n/a')}`",
        f"- recall: `{gl.get('recall', 'n/a')}`",
        f"- f1: `{gl.get('f1', 'n/a')}`",
        "",
        "## Translation Fidelity",
        f"- ok: `{tf.get('ok', False)}`",
        f"- avg_fidelity_f1: `{tf.get('avg_fidelity_f1', 'n/a')}`",
        f"- gold_count: `{tf.get('gold_count', 'n/a')}`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate weekly benchmark report artifacts")
    p.add_argument("--ledger-root", default="output/verification_ledgers")
    p.add_argument("--kg-db", default="output/kg/kg_index.db")
    p.add_argument("--out-dir", default="output/reports/weekly")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--budget", type=int, default=40)
    p.add_argument("--max-depth", type=int, default=4)
    p.add_argument("--max-candidates", type=int, default=3)
    p.add_argument("--gold", default="", help="Optional gold linkage jsonl path")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    out_dir = Path(args.out_dir)
    payload = generate_report(
        ledger_root=Path(args.ledger_root),
        kg_db=Path(args.kg_db),
        out_dir=out_dir,
        limit=max(1, int(args.limit)),
        budget=max(1, int(args.budget)),
        max_depth=max(1, int(args.max_depth)),
        max_candidates=max(1, int(args.max_candidates)),
        gold_path=Path(args.gold) if args.gold else None,
    )
    ts = time.strftime("%Y%m%d", time.gmtime(payload["generated_at_unix"]))
    json_path = out_dir / f"weekly_report_{ts}.json"
    md_path = out_dir / f"weekly_report_{ts}.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    write_markdown(md_path, payload)
    print(json.dumps({"json": str(json_path), "markdown": str(md_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
