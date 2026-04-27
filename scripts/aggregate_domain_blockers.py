#!/usr/bin/env python3
"""Aggregate missing Mathlib modules across all papers in the KG.

Usage:
    python3 scripts/aggregate_domain_blockers.py
    python3 scripts/aggregate_domain_blockers.py --kg-db output/kg/kg_index.db --out output/reports/domain_blockers.json

Output tells you which Mathlib library gaps block the most theorems so the
100-paper programme can prioritise library-building work.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path


def aggregate(kg_db: Path) -> dict:
    if not kg_db.exists():
        return {"error": f"KG database not found: {kg_db}"}

    con = sqlite3.connect(str(kg_db))

    # Count per-status across all papers
    status_counts: dict[str, int] = defaultdict(int)
    paper_ids: set[str] = set()
    for status, paper_id, count in con.execute(
        "SELECT status, paper_id, COUNT(*) FROM kg_nodes GROUP BY status, paper_id"
    ):
        paper_ids.add(paper_id)
        status_counts[status] += count

    # Gather blocking info from paper entities
    missing_modules: dict[str, int] = defaultdict(int)        # module → papers blocked
    module_theorems: dict[str, int] = defaultdict(int)        # module → theorems blocked
    blocking_domains: dict[str, list[str]] = defaultdict(list)  # domain → paper_ids
    papers_needing_library: list[dict] = []

    for entity_id, payload_json in con.execute(
        "SELECT entity_id, payload_json FROM kg_entities WHERE entity_type='paper'"
    ):
        try:
            p = json.loads(payload_json or "{}")
        except Exception:
            continue
        if not p.get("domain_library_needed"):
            continue

        paper_id = p.get("paper_id", entity_id)
        domain = p.get("blocking_domain", "unknown")
        axiom_backed = p.get("axiom_backed", 0)
        modules = p.get("missing_mathlib_modules") or []

        papers_needing_library.append({
            "paper_id": paper_id,
            "blocking_domain": domain,
            "axiom_backed_theorems": axiom_backed,
            "missing_modules": modules,
        })
        blocking_domains[domain].append(paper_id)
        for mod in modules:
            missing_modules[mod] += 1
            module_theorems[mod] += axiom_backed

    total_theorems = sum(status_counts.values())
    statements_formalized = sum(
        status_counts[s] for s in ("FULLY_PROVEN", "AXIOM_BACKED", "INTERMEDIARY_PROVEN")
    )
    proofs_closed = status_counts.get("FULLY_PROVEN", 0)
    axiom_backed_total = status_counts.get("AXIOM_BACKED", 0)

    # Rank modules by theorems blocked (most impactful first)
    ranked_modules = sorted(
        [
            {
                "module": mod,
                "papers_blocked": missing_modules[mod],
                "theorems_blocked": module_theorems[mod],
            }
            for mod in missing_modules
        ],
        key=lambda x: (-x["theorems_blocked"], -x["papers_blocked"]),
    )

    return {
        "programme_summary": {
            "papers_total": len(paper_ids),
            "theorems_total": total_theorems,
            "statements_formalized": statements_formalized,
            "proofs_closed": proofs_closed,
            "axiom_backed": axiom_backed_total,
            "papers_needing_domain_library": len(papers_needing_library),
            "proof_closure_rate": round(proofs_closed / max(1, statements_formalized), 4),
        },
        "priority_library_work": ranked_modules,
        "blocking_domains": {
            domain: papers for domain, papers in
            sorted(blocking_domains.items(), key=lambda x: -len(x[1]))
        },
        "papers_needing_library": papers_needing_library,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kg-db", default="output/kg/kg_index.db", help="Path to kg_index.db")
    ap.add_argument("--out", default=None, help="Write JSON report to this path")
    args = ap.parse_args()

    result = aggregate(Path(args.kg_db))

    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nReport written to {out}", flush=True)


if __name__ == "__main__":
    main()
