#!/usr/bin/env python3
"""Benchmark tier-aware retrieval across multiple papers.

Compares baseline retrieval vs tier-preferred retrieval on theorem statements
from ledger entries and writes a JSON report.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from pathlib import Path

from premise_retrieval import PremiseRetriever, load_kg_tier_names


def _load_entries(ledger_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for p in sorted(ledger_dir.glob("*.json")):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
            entries = raw["entries"]
        elif isinstance(raw, list):
            entries = raw
        else:
            continue
        for e in entries:
            if not isinstance(e, dict):
                continue
            stmt = str(e.get("lean_statement", "")).strip()
            if stmt:
                rows.append({
                    "paper_id": p.stem,
                    "theorem_name": str(e.get("theorem_name", "")),
                    "lean_statement": stmt,
                })
    return rows


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    idx = int((len(values) - 1) * p)
    return sorted(values)[idx]


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark tier-aware premise retrieval")
    parser.add_argument("--index", default="data/mathlib_embeddings", help="Retrieval index path")
    parser.add_argument("--ledger-dir", default="output/verification_ledgers", help="Ledger directory")
    parser.add_argument("--kg-root", default="output/kg", help="KG root directory")
    parser.add_argument("--top-k", type=int, default=12)
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", default="output/mcts_bench/tier_retrieval_benchmark.json")
    args = parser.parse_args()

    index_path = Path(args.index)
    ledger_dir = Path(args.ledger_dir)
    kg_root = Path(args.kg_root)

    if not index_path.exists():
        raise SystemExit(f"index not found: {index_path}")
    if not ledger_dir.exists():
        raise SystemExit(f"ledger dir not found: {ledger_dir}")

    retriever = PremiseRetriever.load(index_path)
    trusted, conditional = load_kg_tier_names(kg_root)

    rows = _load_entries(ledger_dir)
    if not rows:
        raise SystemExit("no ledger theorem statements available")

    random.seed(args.seed)
    random.shuffle(rows)
    sample_rows = rows[: max(1, min(args.samples, len(rows)))]

    baseline_latency: list[float] = []
    tier_latency: list[float] = []
    tier_hit_count = 0

    per_query: list[dict] = []
    for row in sample_rows:
        goal = row["lean_statement"]

        t0 = time.perf_counter()
        base_hits = retriever.query(goal, top_k=args.top_k)
        baseline_latency.append((time.perf_counter() - t0) * 1000.0)

        t1 = time.perf_counter()
        tier_hits = retriever.query_with_tier_preference(
            goal,
            kg_trusted_names=trusted if trusted else None,
            kg_conditional_names=conditional if conditional else None,
            top_k=args.top_k,
        )
        tier_latency.append((time.perf_counter() - t1) * 1000.0)

        tiered = sum(1 for h in tier_hits if h.trust_tier in {"trusted", "conditional"})
        if tiered > 0:
            tier_hit_count += 1

        per_query.append(
            {
                "paper_id": row["paper_id"],
                "theorem_name": row["theorem_name"],
                "baseline_top1": base_hits[0].name if base_hits else "",
                "tier_top1": tier_hits[0].name if tier_hits else "",
                "tier_hits_in_topk": tiered,
            }
        )

    report = {
        "samples": len(sample_rows),
        "top_k": args.top_k,
        "kg_counts": {
            "trusted": len(trusted),
            "conditional": len(conditional),
        },
        "latency_ms": {
            "baseline_mean": statistics.fmean(baseline_latency) if baseline_latency else 0.0,
            "baseline_p95": _pct(baseline_latency, 0.95),
            "tier_mean": statistics.fmean(tier_latency) if tier_latency else 0.0,
            "tier_p95": _pct(tier_latency, 0.95),
        },
        "tier_relevance": {
            "queries_with_tier_hit": tier_hit_count,
            "query_hit_ratio": tier_hit_count / max(1, len(sample_rows)),
        },
        "queries": per_query,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps({
        "out": str(out_path),
        "samples": report["samples"],
        "tier_hit_ratio": round(report["tier_relevance"]["query_hit_ratio"], 4),
        "tier_mean_ms": round(report["latency_ms"]["tier_mean"], 3),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
