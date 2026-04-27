#!/usr/bin/env python3
"""Paper-agnostic consistency gate over a benchmark suite.

Evaluates whether pipeline quality is stable across topics, using:
- per-paper operational closure
- per-paper faithful closure
- cross-paper mean and standard deviation

Default go/no-go target:
- mean faithful closure >= 0.80
- std faithful closure <= 0.10
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any

from paper_closure_checklist import run_checklist


def _load_suite(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    papers = raw.get("papers", []) if isinstance(raw, dict) else []
    if not isinstance(papers, list):
        return []
    return [p for p in papers if isinstance(p, dict)]


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _safe_id(text: str) -> str:
    return str(text).replace("/", "_").replace(":", "_")


def _per_domain(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    by_domain: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        d = str(r.get("domain", "unknown"))
        by_domain.setdefault(d, []).append(r)
    out: dict[str, dict[str, float]] = {}
    for d, rs in by_domain.items():
        faithful = [_f(r.get("faithful_closure_rate")) for r in rs]
        operational = [_f(r.get("operational_closure_rate")) for r in rs]
        out[d] = {
            "papers": float(len(rs)),
            "mean_faithful": round(statistics.fmean(faithful), 4) if faithful else 0.0,
            "std_faithful": round(statistics.pstdev(faithful), 4) if len(faithful) > 1 else 0.0,
            "mean_operational": round(statistics.fmean(operational), 4) if operational else 0.0,
            "std_operational": round(statistics.pstdev(operational), 4) if len(operational) > 1 else 0.0,
        }
    return out


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run paper-agnostic consistency gate")
    p.add_argument("--suite-json", default="reproducibility/paper_agnostic_suite_seed.json")
    p.add_argument("--ledger-root", default="output/verification_ledgers")
    p.add_argument("--out-json", default="")
    p.add_argument("--min-faithful-mean", type=float, default=0.80)
    p.add_argument("--max-faithful-std", type=float, default=0.10)
    p.add_argument("--min-operational-mean", type=float, default=0.90)
    p.add_argument("--max-operational-std", type=float, default=0.12)
    return p


def main() -> int:
    args = _build_parser().parse_args()
    suite_path = Path(args.suite_json)
    ledger_root = Path(args.ledger_root)
    rows = _load_suite(suite_path)
    if not rows:
        print(json.dumps({"ok": False, "reason": "empty_suite", "suite_json": str(suite_path)}, indent=2))
        return 1

    paper_rows: list[dict[str, Any]] = []
    for item in rows:
        pid = str(item.get("paper_id", "")).strip()
        if not pid:
            continue
        domain = str(item.get("domain", "unknown"))
        payload = run_checklist(paper_id=pid, ledger_root=ledger_root)
        operational = _f(payload.get("operational_closure_rate", payload.get("closure_rate", 0.0)))
        faithful = _f(payload.get("faithful_closure_rate", 0.0))
        paper_rows.append(
            {
                "paper_id": pid,
                "domain": domain,
                "total_theorems": int(payload.get("total_theorems", 0) or 0),
                "operational_closure_rate": round(operational, 4),
                "faithful_closure_rate": round(faithful, 4),
                "operational_fully_proven": int(payload.get("operational_fully_proven", payload.get("fully_proven", 0)) or 0),
                "faithful_fully_accepted": int(payload.get("faithful_fully_accepted", 0) or 0),
            }
        )

    if not paper_rows:
        print(json.dumps({"ok": False, "reason": "no_valid_papers"}, indent=2))
        return 1

    operational_rates = [_f(r["operational_closure_rate"]) for r in paper_rows]
    faithful_rates = [_f(r["faithful_closure_rate"]) for r in paper_rows]
    mean_operational = statistics.fmean(operational_rates)
    mean_faithful = statistics.fmean(faithful_rates)
    std_operational = statistics.pstdev(operational_rates) if len(operational_rates) > 1 else 0.0
    std_faithful = statistics.pstdev(faithful_rates) if len(faithful_rates) > 1 else 0.0

    gates = {
        "mean_faithful_pass": mean_faithful >= float(args.min_faithful_mean),
        "std_faithful_pass": std_faithful <= float(args.max_faithful_std),
        "mean_operational_pass": mean_operational >= float(args.min_operational_mean),
        "std_operational_pass": std_operational <= float(args.max_operational_std),
    }
    go = all(bool(v) for v in gates.values())

    payload = {
        "suite_json": str(suite_path),
        "papers_evaluated": len(paper_rows),
        "thresholds": {
            "min_faithful_mean": float(args.min_faithful_mean),
            "max_faithful_std": float(args.max_faithful_std),
            "min_operational_mean": float(args.min_operational_mean),
            "max_operational_std": float(args.max_operational_std),
        },
        "aggregate": {
            "mean_operational_closure_rate": round(mean_operational, 4),
            "std_operational_closure_rate": round(std_operational, 4),
            "mean_faithful_closure_rate": round(mean_faithful, 4),
            "std_faithful_closure_rate": round(std_faithful, 4),
        },
        "gates": gates,
        "go_for_paper_agnostic_claim": go,
        "by_domain": _per_domain(paper_rows),
        "papers": paper_rows,
    }

    out_path = (
        Path(args.out_json)
        if args.out_json
        else Path("output/reports/full_paper") / f"{_safe_id(suite_path.stem)}_consistency_gate.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "out_json": str(out_path),
                "papers_evaluated": payload["papers_evaluated"],
                "mean_operational_closure_rate": payload["aggregate"]["mean_operational_closure_rate"],
                "std_operational_closure_rate": payload["aggregate"]["std_operational_closure_rate"],
                "mean_faithful_closure_rate": payload["aggregate"]["mean_faithful_closure_rate"],
                "std_faithful_closure_rate": payload["aggregate"]["std_faithful_closure_rate"],
                "go_for_paper_agnostic_claim": payload["go_for_paper_agnostic_claim"],
            },
            indent=2,
        )
    )
    return 0 if go else 2


if __name__ == "__main__":
    raise SystemExit(main())

