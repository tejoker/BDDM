#!/usr/bin/env python3
"""Benchmark world-model bridge scaffold vs current text bridge pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from world_model_bridge import compare_against_baseline


def _iter_target_theorems(ledger_root: Path, limit: int) -> list[str]:
    out: list[str] = []
    for path in sorted(ledger_root.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = raw.get("entries", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            assumptions = row.get("assumptions", [])
            if not isinstance(assumptions, list):
                continue
            has_ungrounded = any(
                isinstance(a, dict) and str(a.get("grounding", "")).upper() in {"UNGROUNDED", "UNKNOWN", ""}
                for a in assumptions
            )
            if not has_ungrounded:
                continue
            name = str(row.get("theorem_name", "")).strip()
            if name:
                out.append(name)
            if len(out) >= limit:
                return out
    return out


def run_benchmark(
    *,
    ledger_root: Path,
    limit: int,
    budget: int,
    max_depth: int,
    max_candidates_per_assumption: int,
    baseline_lean_timeout_s: int,
    baseline_max_repair_rounds: int,
    retrieval_memory_path: Path | None = None,
) -> dict[str, Any]:
    targets = _iter_target_theorems(ledger_root, limit=limit)
    rows: list[dict[str, Any]] = []
    wm_grounded = 0
    baseline_grounded = 0
    wm_failure_reasons: dict[str, int] = {}
    baseline_failure_reasons: dict[str, int] = {}
    slot_coverage_total = 0
    slot_coverage_ok = 0
    candidate_empty = 0
    non_actionable = 0
    repair_attempts_total = 0
    repair_success_total = 0

    for t in targets:
        comp = compare_against_baseline(
            target_theorem=t,
            ledger_root=ledger_root,
            budget=budget,
            max_depth=max_depth,
            max_candidates_per_assumption=max_candidates_per_assumption,
            baseline_lean_timeout_s=baseline_lean_timeout_s,
            baseline_max_repair_rounds=baseline_max_repair_rounds,
            retrieval_memory_path=retrieval_memory_path,
        )
        rows.append(comp)
        wm_grounded += int(comp["world_model"]["grounded_count"])
        baseline_grounded += int(comp["baseline_text_bridge"]["grounded_count"])
        b = comp.get("baseline_text_bridge", {}) or {}
        repair_attempts_total += int(b.get("repair_attempts_total", 0))
        repair_success_total += int(b.get("repair_success_count", 0))
        for d in (b.get("assumption_diagnostics", []) or []):
            if not isinstance(d, dict):
                continue
            if str(d.get("lane", "goal")) != "goal":
                continue
            slot_coverage_total += 1
            if str(d.get("slot_name", "")).strip():
                slot_coverage_ok += 1
            if int(d.get("candidate_count", 0)) == 0:
                candidate_empty += 1
            if int(d.get("candidate_count", 0)) > 0 and int(d.get("candidate_template_count", 0)) == 0 and str(d.get("lean_statement", "")).strip() == "":
                non_actionable += 1
        for k, v in (comp.get("world_model", {}).get("failure_reasons", {}) or {}).items():
            wm_failure_reasons[str(k)] = wm_failure_reasons.get(str(k), 0) + int(v)
        for k, v in (comp.get("baseline_text_bridge", {}).get("failure_reasons", {}) or {}).items():
            baseline_failure_reasons[str(k)] = baseline_failure_reasons.get(str(k), 0) + int(v)

    safe_denom = max(1, slot_coverage_total)
    kpis = {
        "hard_safe_yield": float(baseline_grounded) / max(1, len(targets)),
        "slot_coverage_pass_rate": float(slot_coverage_ok) / safe_denom,
        "candidate_empty_rate": float(candidate_empty) / safe_denom,
        "non_actionable_candidate_rate": float(non_actionable) / safe_denom,
        "avg_retries_per_grounded": float(repair_attempts_total) / max(1, baseline_grounded),
        "repair_success_rate": float(repair_success_total) / max(1, repair_attempts_total),
    }

    return {
        "targets": targets,
        "count": len(targets),
        "world_model_grounded_total": wm_grounded,
        "baseline_grounded_total": baseline_grounded,
        "delta_grounded": wm_grounded - baseline_grounded,
        "world_model_failure_reasons": wm_failure_reasons,
        "baseline_failure_reasons": baseline_failure_reasons,
        "kpis": kpis,
        "rows": rows,
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Compare world-model bridge scaffold against baseline")
    p.add_argument("--ledger-root", default="output/verification_ledgers")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--budget", type=int, default=40)
    p.add_argument("--max-depth", type=int, default=4)
    p.add_argument("--max-candidates-per-assumption", type=int, default=3)
    p.add_argument("--baseline-lean-timeout-s", type=int, default=60)
    p.add_argument("--baseline-max-repair-rounds", type=int, default=2)
    p.add_argument("--retrieval-memory-path", default="", help="Optional retrieval memory JSON path")
    p.add_argument("--out", default="", help="Optional JSON output path")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    payload = run_benchmark(
        ledger_root=Path(args.ledger_root),
        limit=max(1, args.limit),
        budget=max(1, args.budget),
        max_depth=max(1, args.max_depth),
        max_candidates_per_assumption=max(1, args.max_candidates_per_assumption),
        baseline_lean_timeout_s=max(5, args.baseline_lean_timeout_s),
        baseline_max_repair_rounds=max(0, args.baseline_max_repair_rounds),
        retrieval_memory_path=Path(args.retrieval_memory_path) if args.retrieval_memory_path else None,
    )
    txt = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(txt, encoding="utf-8")
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
