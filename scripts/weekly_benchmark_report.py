#!/usr/bin/env python3
"""Generate weekly benchmark report artifacts (JSON + Markdown)."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any


def _run_json_cmd(cmd: list[str], *, timeout_s: int = 14400) -> dict[str, Any]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=max(1, int(timeout_s)))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout_after_{int(timeout_s)}s", "cmd": cmd[:6]}
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
    retrieval_memory_path: Path | None,
    failure_artifacts_root: Path,
    hard_slice_size: int,
    min_translation_fidelity: float,
    min_semantic_safe_yield: float,
    min_slot_coverage_pass_rate: float,
    subrun_timeout_s: int,
    bridge_subrun_timeout_s: int,
    stratified_subrun_timeout_s: int,
    flywheel_out_jsonl: Path,
    flywheel_out_summary: Path,
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
            "--retrieval-memory-path",
            str(retrieval_memory_path) if retrieval_memory_path else "",
        ],
        timeout_s=bridge_subrun_timeout_s,
    )
    ab = _run_json_cmd(
        [
            "python3",
            "scripts/ab_world_model_vs_baseline.py",
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
            "--baseline-lean-timeout-s",
            "60",
            "--baseline-max-repair-rounds",
            "2",
            "--retrieval-memory-path",
            str(retrieval_memory_path) if retrieval_memory_path else "",
        ],
        timeout_s=bridge_subrun_timeout_s,
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
            ],
            timeout_s=subrun_timeout_s,
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
        ],
        timeout_s=subrun_timeout_s,
    )
    replay = _run_json_cmd(
        [
            "python3",
            "scripts/replay_hard_failures.py",
            "--artifacts-root",
            str(failure_artifacts_root),
            "--ledger-root",
            str(ledger_root),
            "--max-items",
            str(limit),
            "--budget",
            str(budget),
            "--max-depth",
            str(max_depth),
            "--max-candidates-per-assumption",
            str(max_candidates),
            "--retrieval-memory-path",
            str(retrieval_memory_path) if retrieval_memory_path else "",
        ],
        timeout_s=bridge_subrun_timeout_s,
    )
    flywheel = _run_json_cmd(
        [
            "python3",
            "scripts/build_repair_flywheel.py",
            "--ledger-root",
            str(ledger_root),
            "--bridge-failures-root",
            str(failure_artifacts_root),
            "--out-jsonl",
            str(flywheel_out_jsonl),
            "--out-summary",
            str(flywheel_out_summary),
        ],
        timeout_s=subrun_timeout_s,
    )
    per_domain = max(1, int(hard_slice_size) // 5)
    stratified = _run_json_cmd(
        [
            "python3",
            "scripts/run_stratified_bottleneck_suite.py",
            "--ledger-root",
            str(ledger_root),
            "--per-domain",
            str(per_domain),
            "--repeats",
            "3",
            "--out",
            str(out_dir / "stratified_bottleneck_suite.json"),
        ],
        timeout_s=stratified_subrun_timeout_s,
    )
    tf_avg = float(fidelity.get("avg_fidelity_f1", 0.0) or 0.0) if isinstance(fidelity, dict) else 0.0
    wm_kpis = (wm.get("kpis", {}) or {}) if isinstance(wm, dict) else {}
    safe_yield = float(wm_kpis.get("hard_safe_yield", 0.0) or 0.0)
    slot_cov = float(wm_kpis.get("slot_coverage_pass_rate", 0.0) or 0.0)
    strat_go = False
    if isinstance(stratified, dict) and stratified.get("ok"):
        try:
            raw = json.loads((out_dir / "stratified_bottleneck_suite.json").read_text(encoding="utf-8"))
            cfgs = (raw.get("configs", {}) or {})
            for cfg in cfgs.values():
                dec = ((cfg or {}).get("decision") or {})
                if bool(dec.get("go_for_world_model_superiority_claim", False)):
                    strat_go = True
                    break
        except Exception:
            strat_go = False
    release_gate = {
        "hard_slice_size": int(hard_slice_size),
        "hard_slice_in_range_50_100": 50 <= int(hard_slice_size) <= 100,
        "translation_fidelity_pass": tf_avg >= float(min_translation_fidelity),
        "semantic_safe_yield_pass": safe_yield >= float(min_semantic_safe_yield),
        "slot_coverage_pass": slot_cov >= float(min_slot_coverage_pass_rate),
        "min_translation_fidelity": float(min_translation_fidelity),
        "min_semantic_safe_yield": float(min_semantic_safe_yield),
        "min_slot_coverage_pass_rate": float(min_slot_coverage_pass_rate),
        "observed_translation_fidelity": round(tf_avg, 4),
        "hard_safe_yield": round(safe_yield, 4),
        "slot_coverage_pass_rate": round(slot_cov, 4),
        "stratified_go": bool(strat_go),
        "go_for_controlled_release": bool(
            (tf_avg >= float(min_translation_fidelity))
            and (safe_yield >= float(min_semantic_safe_yield))
            and (slot_cov >= float(min_slot_coverage_pass_rate))
            and strat_go
            and (50 <= int(hard_slice_size) <= 100)
        ),
    }
    # Programme-level dual-metric summary (statements formalized vs proofs closed)
    dual_metric: dict[str, Any] = {}
    try:
        import sqlite3 as _sq
        from collections import defaultdict as _dd
        if kg_db.exists():
            _con = _sq.connect(str(kg_db))
            _sc: dict[str, int] = _dd(int)
            _papers: set[str] = set()
            for _stat, _pid, _cnt in _con.execute(
                "SELECT status, paper_id, COUNT(*) FROM kg_nodes GROUP BY status, paper_id"
            ):
                _papers.add(_pid)
                _sc[_stat] += _cnt
            _stmts = sum(_sc[s] for s in ("FULLY_PROVEN", "AXIOM_BACKED", "INTERMEDIARY_PROVEN"))
            _closed = _sc.get("FULLY_PROVEN", 0)
            _backed = _sc.get("AXIOM_BACKED", 0)
            _missing: dict[str, int] = _dd(int)
            for (_pj,) in _con.execute(
                "SELECT payload_json FROM kg_entities WHERE entity_type='paper'"
            ):
                try:
                    _p = json.loads(_pj or "{}")
                    for _m in (_p.get("missing_mathlib_modules") or []):
                        _missing[_m] += 1
                except Exception:
                    pass
            dual_metric = {
                "papers": len(_papers),
                "theorems_total": sum(_sc.values()),
                "statements_formalized": _stmts,
                "proofs_closed": _closed,
                "axiom_backed": _backed,
                "proof_closure_rate": round(_closed / max(1, _stmts), 4),
                "top_missing_mathlib_modules": dict(
                    sorted(_missing.items(), key=lambda x: -x[1])[:10]
                ),
            }
    except Exception as _e:
        dual_metric = {"error": str(_e)}

    payload = {
        "generated_at_unix": int(time.time()),
        "benchmark_world_model": wm,
        "ab_world_model_vs_baseline": ab,
        "gold_linkage": gold,
        "translation_fidelity": fidelity,
        "replay_hard_failures": replay,
        "repair_flywheel": flywheel,
        "stratified_hard_slice": stratified,
        "release_gate": release_gate,
        "programme_dual_metric": dual_metric,
    }
    return payload


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    wm = payload.get("benchmark_world_model", {}) or {}
    ab = payload.get("ab_world_model_vs_baseline", {}) or {}
    gl = payload.get("gold_linkage", {}) or {}
    tf = payload.get("translation_fidelity", {}) or {}
    replay = payload.get("replay_hard_failures", {}) or {}
    flywheel = payload.get("repair_flywheel", {}) or {}
    gate = payload.get("release_gate", {}) or {}
    dm = payload.get("programme_dual_metric", {}) or {}
    kpis = (wm.get("kpis", {}) or {})
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
        f"- hard_safe_yield: `{kpis.get('hard_safe_yield', 'n/a')}`",
        f"- slot_coverage_pass_rate: `{kpis.get('slot_coverage_pass_rate', 'n/a')}`",
        f"- candidate_empty_rate: `{kpis.get('candidate_empty_rate', 'n/a')}`",
        f"- non_actionable_candidate_rate: `{kpis.get('non_actionable_candidate_rate', 'n/a')}`",
        f"- avg_retries_per_grounded: `{kpis.get('avg_retries_per_grounded', 'n/a')}`",
        "",
        "## A/B World-Model vs Baseline",
        f"- ok: `{ab.get('ok', False)}`",
        f"- wins_world_model: `{((ab.get('summary') or {}).get('wins_world_model', 'n/a'))}`",
        f"- losses_world_model: `{((ab.get('summary') or {}).get('losses_world_model', 'n/a'))}`",
        f"- sign_test_pvalue: `{((ab.get('summary') or {}).get('sign_test_pvalue', 'n/a'))}`",
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
        "",
        "## Hard Failure Replay",
        f"- ok: `{replay.get('ok', False)}`",
        f"- queue_size: `{replay.get('queue_size', 'n/a')}`",
        f"- delta_grounded: `{replay.get('delta_grounded', 'n/a')}`",
        "",
        "## Repair Flywheel",
        f"- ok: `{flywheel.get('ok', False)}`",
        f"- rows: `{flywheel.get('rows', 'n/a')}`",
        f"- out_jsonl: `{flywheel.get('out_jsonl', 'n/a')}`",
        f"- out_summary: `{flywheel.get('out_summary', 'n/a')}`",
        "",
        "## Release Gate",
        f"- hard_slice_size: `{gate.get('hard_slice_size', 'n/a')}`",
        f"- hard_slice_in_range_50_100: `{gate.get('hard_slice_in_range_50_100', False)}`",
        f"- translation_fidelity_pass: `{gate.get('translation_fidelity_pass', False)}`",
        f"- semantic_safe_yield_pass: `{gate.get('semantic_safe_yield_pass', False)}`",
        f"- slot_coverage_pass: `{gate.get('slot_coverage_pass', False)}`",
        f"- observed_translation_fidelity: `{gate.get('observed_translation_fidelity', 'n/a')}`",
        f"- hard_safe_yield: `{gate.get('hard_safe_yield', 'n/a')}`",
        f"- slot_coverage_pass_rate: `{gate.get('slot_coverage_pass_rate', 'n/a')}`",
        f"- stratified_go: `{gate.get('stratified_go', False)}`",
        f"- go_for_controlled_release: `{gate.get('go_for_controlled_release', False)}`",
        "",
        "## Programme: Statements Formalized vs Proofs Closed",
        f"- papers: `{dm.get('papers', 'n/a')}`",
        f"- theorems_total: `{dm.get('theorems_total', 'n/a')}`",
        f"- statements_formalized: `{dm.get('statements_formalized', 'n/a')}`",
        f"- proofs_closed: `{dm.get('proofs_closed', 'n/a')}`",
        f"- axiom_backed: `{dm.get('axiom_backed', 'n/a')}`",
        f"- proof_closure_rate: `{dm.get('proof_closure_rate', 'n/a')}`",
        f"- top_missing_mathlib_modules: `{dm.get('top_missing_mathlib_modules', {})}`",
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
    p.add_argument("--retrieval-memory-path", default="output/bridge_memory/candidate_stats.json")
    p.add_argument("--failure-artifacts-root", default="output/reports/bridge_failures")
    p.add_argument("--flywheel-out-jsonl", default="output/flywheel/repair_dataset.jsonl")
    p.add_argument("--flywheel-out-summary", default="output/flywheel/repair_dataset_summary.json")
    p.add_argument("--hard-slice-size", type=int, default=50)
    p.add_argument("--min-translation-fidelity", type=float, default=0.40)
    p.add_argument("--min-semantic-safe-yield", type=float, default=0.10)
    p.add_argument("--min-slot-coverage-pass-rate", type=float, default=0.45)
    p.add_argument(
        "--subrun-timeout-s",
        type=int,
        default=7200,
        help="General timeout per sub-benchmark command (seconds)",
    )
    p.add_argument(
        "--bridge-subrun-timeout-s",
        type=int,
        default=14400,
        help="Timeout for bridge-heavy subruns (benchmark/replay/A-B) in seconds",
    )
    p.add_argument(
        "--stratified-subrun-timeout-s",
        type=int,
        default=21600,
        help="Timeout for stratified suite subrun in seconds",
    )
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
        retrieval_memory_path=Path(args.retrieval_memory_path) if args.retrieval_memory_path else None,
        failure_artifacts_root=Path(args.failure_artifacts_root),
        flywheel_out_jsonl=Path(args.flywheel_out_jsonl),
        flywheel_out_summary=Path(args.flywheel_out_summary),
        hard_slice_size=max(10, int(args.hard_slice_size)),
        min_translation_fidelity=float(args.min_translation_fidelity),
        min_semantic_safe_yield=float(args.min_semantic_safe_yield),
        min_slot_coverage_pass_rate=float(args.min_slot_coverage_pass_rate),
        subrun_timeout_s=max(30, int(args.subrun_timeout_s)),
        bridge_subrun_timeout_s=max(60, int(args.bridge_subrun_timeout_s)),
        stratified_subrun_timeout_s=max(60, int(args.stratified_subrun_timeout_s)),
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
