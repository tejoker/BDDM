#!/usr/bin/env python3
"""Run stratified bottleneck testing and emit go/no-go decision report.

Design goals:
- Balance test slice across heuristic domains and difficulty bands.
- Compare multiple bridge configs under equal theorem targets.
- Report bottlenecks by domain + difficulty.
- Provide explicit go/no-go decision thresholds.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from world_model_bridge import compare_against_baseline


_DOMAIN_ORDER = [
    "probability_statistics",
    "analysis_pde",
    "optimization",
    "algebra_number_theory",
    "remaining_cs_math",
]

_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "probability_statistics": (
        "prob",
        "measure",
        "density",
        "variance",
        "expect",
        "martingale",
        "stochastic",
        "entropy",
        "brenier",
        "schro",
    ),
    "analysis_pde": (
        "laplace",
        "derivative",
        "gradient",
        "concave",
        "convex",
        "sobolev",
        "fourier",
        "pde",
        "analytic",
    ),
    "optimization": (
        "opt",
        "min",
        "max",
        "lagrang",
        "dual",
        "primal",
        "converg",
        "descent",
        "saddle",
    ),
    "algebra_number_theory": (
        "ring",
        "field",
        "group",
        "ideal",
        "prime",
        "galois",
        "algebra",
        "number",
    ),
    "remaining_cs_math": (
        "algorithm",
        "graph",
        "complex",
        "type",
        "program",
        "semantics",
        "mcts",
        "automata",
        "cs",
    ),
}


@dataclass(frozen=True)
class TargetMeta:
    theorem_name: str
    paper_id: str
    assumptions_total: int
    domain: str
    difficulty: str


def _iter_entries(ledger_root: Path):
    for path in sorted(ledger_root.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = raw.get("entries", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                yield path.stem, row


def _difficulty(n_assumptions: int) -> str:
    if n_assumptions <= 2:
        return "easy"
    if n_assumptions <= 5:
        return "medium"
    return "hard"


def _infer_domain(text: str) -> str:
    t = text.lower()
    for d in _DOMAIN_ORDER:
        keys = _DOMAIN_KEYWORDS.get(d, ())
        if any(k in t for k in keys):
            return d
    return "remaining_cs_math"


def build_target_pool(ledger_root: Path) -> list[TargetMeta]:
    out: list[TargetMeta] = []
    for paper_id, row in _iter_entries(ledger_root):
        thm = str(row.get("theorem_name", "")).strip()
        if not thm:
            continue
        assumptions = row.get("assumptions", [])
        if not isinstance(assumptions, list):
            continue
        ungrounded = [
            a
            for a in assumptions
            if isinstance(a, dict) and str(a.get("grounding", "")).upper() in {"UNGROUNDED", "UNKNOWN", ""}
        ]
        if not ungrounded:
            continue
        text = " ".join(
            [
                thm,
                str(row.get("lean_statement", "")),
                str(row.get("status", "")),
                paper_id,
            ]
        )
        out.append(
            TargetMeta(
                theorem_name=thm,
                paper_id=paper_id,
                assumptions_total=len(ungrounded),
                domain=_infer_domain(text),
                difficulty=_difficulty(len(ungrounded)),
            )
        )
    return out


def stratified_sample(
    pool: list[TargetMeta],
    *,
    per_domain: int,
) -> list[TargetMeta]:
    by_domain: dict[str, list[TargetMeta]] = defaultdict(list)
    for t in pool:
        by_domain[t.domain].append(t)
    for d in by_domain:
        by_domain[d].sort(key=lambda x: (x.difficulty, x.paper_id, x.theorem_name))

    selected: list[TargetMeta] = []
    for d in _DOMAIN_ORDER:
        group = by_domain.get(d, [])
        if not group:
            continue
        # Balance by difficulty inside each domain.
        by_diff = {
            "easy": [g for g in group if g.difficulty == "easy"],
            "medium": [g for g in group if g.difficulty == "medium"],
            "hard": [g for g in group if g.difficulty == "hard"],
        }
        picks: list[TargetMeta] = []
        k = 0
        order = ["hard", "medium", "easy"]
        while len(picks) < min(per_domain, len(group)):
            bucket = by_diff[order[k % len(order)]]
            if bucket:
                picks.append(bucket.pop(0))
            if not any(by_diff.values()):
                break
            k += 1
        selected.extend(picks)
    return selected


def _sign_test_pvalue(wins: int, losses: int) -> float:
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    tail = 0.0
    for i in range(0, k + 1):
        tail += math.comb(n, i) * (0.5 ** n)
    return min(1.0, 2.0 * tail)


def _aggregate_bottlenecks(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_domain: dict[str, Counter[str]] = defaultdict(Counter)
    by_difficulty: dict[str, Counter[str]] = defaultdict(Counter)
    for r in rows:
        domain = str(r.get("domain", "unknown"))
        diff = str(r.get("difficulty", "unknown"))
        wm_fr = (r.get("world_model", {}) or {}).get("failure_reasons", {}) or {}
        bl_fr = (r.get("baseline_text_bridge", {}) or {}).get("failure_reasons", {}) or {}
        for k, v in wm_fr.items():
            by_domain[domain][f"wm:{k}"] += int(v)
            by_difficulty[diff][f"wm:{k}"] += int(v)
        for k, v in bl_fr.items():
            by_domain[domain][f"bl:{k}"] += int(v)
            by_difficulty[diff][f"bl:{k}"] += int(v)

    def _top(c: Counter[str], n: int = 5) -> list[dict[str, Any]]:
        return [{"reason": k, "count": int(v)} for k, v in c.most_common(n)]

    return {
        "by_domain": {d: _top(c) for d, c in by_domain.items()},
        "by_difficulty": {d: _top(c) for d, c in by_difficulty.items()},
    }


def run_config(
    *,
    targets: list[TargetMeta],
    ledger_root: Path,
    budget: int,
    max_depth: int,
    max_candidates_per_assumption: int,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    wm_grounded = 0
    bl_grounded = 0
    wins = losses = ties = 0
    per_domain = Counter()
    for t in targets:
        comp = compare_against_baseline(
            target_theorem=t.theorem_name,
            ledger_root=ledger_root,
            budget=budget,
            max_depth=max_depth,
            max_candidates_per_assumption=max_candidates_per_assumption,
        )
        comp["domain"] = t.domain
        comp["difficulty"] = t.difficulty
        comp["paper_id"] = t.paper_id
        rows.append(comp)

        wm = int(comp["world_model"]["grounded_count"])
        bl = int(comp["baseline_text_bridge"]["grounded_count"])
        wm_grounded += wm
        bl_grounded += bl
        per_domain[t.domain] += 1
        if wm > bl:
            wins += 1
        elif wm < bl:
            losses += 1
        else:
            ties += 1

    return {
        "count": len(targets),
        "targets_per_domain": dict(per_domain),
        "world_model_grounded_total": wm_grounded,
        "baseline_grounded_total": bl_grounded,
        "delta_grounded": wm_grounded - bl_grounded,
        "wins_world_model": wins,
        "losses_world_model": losses,
        "ties": ties,
        "sign_test_pvalue": round(_sign_test_pvalue(wins, losses), 6),
        "bottlenecks": _aggregate_bottlenecks(rows),
        "rows": rows,
    }


def _variance(vals: list[float]) -> float:
    if not vals:
        return 0.0
    m = sum(vals) / len(vals)
    if m == 0:
        return 0.0
    return sum((x - m) ** 2 for x in vals) / len(vals) / (m * m)


def gate_decision(
    *,
    cfg_result: dict[str, Any],
    run_repeats: list[dict[str, Any]],
    domain_regression_tol: float,
) -> dict[str, Any]:
    count = max(1, int(cfg_result.get("count", 0)))
    delta = float(cfg_result.get("delta_grounded", 0))
    wins = int(cfg_result.get("wins_world_model", 0))
    losses = int(cfg_result.get("losses_world_model", 0))
    pval = float(cfg_result.get("sign_test_pvalue", 1.0))
    required_delta = 0.10 * max(1.0, float(cfg_result.get("baseline_grounded_total", 0)))
    pass_delta = delta >= required_delta
    pass_significance = wins > losses and pval < 0.05

    # Domain regression guard (wm should not regress by more than tolerance).
    domain_ok = True
    regressions: list[dict[str, Any]] = []
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in cfg_result.get("rows", []):
        by_domain[str(r.get("domain", "unknown"))].append(r)
    for d, rows in by_domain.items():
        wm = sum(int((r.get("world_model", {}) or {}).get("grounded_count", 0)) for r in rows)
        bl = sum(int((r.get("baseline_text_bridge", {}) or {}).get("grounded_count", 0)) for r in rows)
        n = max(1, len(rows))
        reg = (bl - wm) / n
        if reg > domain_regression_tol:
            domain_ok = False
            regressions.append({"domain": d, "avg_regression_per_target": round(reg, 4)})

    # Repro variance across repeats.
    deltas = [float(r.get("delta_grounded", 0)) for r in run_repeats]
    pvals = [float(r.get("sign_test_pvalue", 1.0)) for r in run_repeats]
    variance_ok = _variance(deltas) < 0.05 and _variance(pvals) < 0.05

    go = pass_delta and pass_significance and domain_ok and variance_ok
    return {
        "go_for_world_model_superiority_claim": go,
        "checks": {
            "delta_threshold_pass": pass_delta,
            "significance_pass": pass_significance,
            "domain_regression_pass": domain_ok,
            "repro_variance_pass": variance_ok,
        },
        "metrics": {
            "count": count,
            "delta_grounded": delta,
            "required_delta_grounded": round(required_delta, 4),
            "wins": wins,
            "losses": losses,
            "p_value": pval,
            "delta_variance_norm": round(_variance(deltas), 6),
            "pvalue_variance_norm": round(_variance(pvals), 6),
        },
        "domain_regressions": regressions,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Run stratified bottleneck suite with hard go/no-go decision")
    p.add_argument("--ledger-root", default="output/verification_ledgers")
    p.add_argument("--per-domain", type=int, default=10)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--domain-regression-tol", type=float, default=0.05)
    p.add_argument("--out", default="output/reports/stratified/stratified_bottleneck_suite.json")
    args = p.parse_args()

    ledger_root = Path(args.ledger_root)
    pool = build_target_pool(ledger_root)
    targets = stratified_sample(pool, per_domain=max(1, int(args.per_domain)))
    if not targets:
        payload = {"error": "no stratified targets available", "pool_size": len(pool)}
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 1

    configs = [
        {"name": "baseline_budget", "budget": 40, "max_depth": 4, "max_candidates_per_assumption": 3},
        {"name": "wm_lite_budget", "budget": 60, "max_depth": 5, "max_candidates_per_assumption": 4},
        {"name": "wm_high_budget", "budget": 80, "max_depth": 6, "max_candidates_per_assumption": 5},
    ]

    results: dict[str, Any] = {"targets": [t.__dict__ for t in targets], "configs": {}}
    for cfg in configs:
        repeats: list[dict[str, Any]] = []
        for _ in range(max(1, int(args.repeats))):
            repeats.append(
                run_config(
                    targets=targets,
                    ledger_root=ledger_root,
                    budget=int(cfg["budget"]),
                    max_depth=int(cfg["max_depth"]),
                    max_candidates_per_assumption=int(cfg["max_candidates_per_assumption"]),
                )
            )
        decision = gate_decision(
            cfg_result=repeats[0],
            run_repeats=repeats,
            domain_regression_tol=float(args.domain_regression_tol),
        )
        results["configs"][cfg["name"]] = {
            "config": cfg,
            "runs": repeats,
            "decision": decision,
        }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"out": str(out_path), "targets": len(targets), "pool_size": len(pool)}, indent=2))
    # Script succeeds even if decision is "no-go"; this is an evaluator.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

