#!/usr/bin/env python3
"""Build a large paper-agnostic suite from arXiv categories.

Outputs a JSON suite consumable by paper_agnostic_consistency_gate.py
and run_paper_agnostic_suite.py.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


ARXIV_API = "https://export.arxiv.org/api/query"


DOMAIN_CATEGORY_QUERIES: dict[str, list[str]] = {
    "probability_statistics": ["math.PR", "stat.TH"],
    "analysis_pde": ["math.AP", "math.CA"],
    "optimization": ["math.OC", "cs.LG"],
    "algebra_number_theory": ["math.AG", "math.NT", "math.RA"],
    "remaining_cs_math": ["cs.LO", "cs.DS", "cs.CC", "math.CO"],
}


def _fetch_ids_for_category(category: str, *, per_category: int) -> list[str]:
    q = f"cat:{category}"
    params = {
        "search_query": q,
        "start": "0",
        "max_results": str(max(1, int(per_category))),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        raw = resp.read()
    root = ET.fromstring(raw)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out: list[str] = []
    for entry in root.findall("a:entry", ns):
        id_el = entry.find("a:id", ns)
        if id_el is None or not id_el.text:
            continue
        m = re.search(r"/abs/(\d{4}\.\d{4,5})(v\d+)?$", id_el.text.strip())
        if not m:
            continue
        out.append(m.group(1))
    # Dedup while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for pid in out:
        if pid in seen:
            continue
        seen.add(pid)
        deduped.append(pid)
    return deduped


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build paper-agnostic arXiv suite by domain")
    p.add_argument("--per-domain", type=int, default=20, help="Target papers per domain")
    p.add_argument("--per-category", type=int, default=30, help="Max fetched papers per category")
    p.add_argument(
        "--out-json",
        default="reproducibility/paper_agnostic_suite_20x5.json",
        help="Output suite JSON",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()
    per_domain = max(1, int(args.per_domain))
    per_category = max(per_domain, int(args.per_category))

    papers: list[dict[str, Any]] = []
    domain_counts: dict[str, int] = {}
    notes: list[str] = []
    for domain, cats in DOMAIN_CATEGORY_QUERIES.items():
        candidates: list[str] = []
        for cat in cats:
            try:
                ids = _fetch_ids_for_category(cat, per_category=per_category)
                candidates.extend(ids)
            except Exception as exc:
                notes.append(f"fetch_failed:{domain}:{cat}:{exc}")
        # Dedup across categories.
        seen: set[str] = set()
        uniq: list[str] = []
        for pid in candidates:
            if pid in seen:
                continue
            seen.add(pid)
            uniq.append(pid)
        picked = uniq[:per_domain]
        domain_counts[domain] = len(picked)
        for pid in picked:
            papers.append({"paper_id": pid, "domain": domain})
        if len(picked) < per_domain:
            notes.append(f"domain_shortfall:{domain}:{len(picked)}/{per_domain}")

    payload = {
        "suite_name": f"paper_agnostic_{per_domain}x{len(DOMAIN_CATEGORY_QUERIES)}",
        "source": "arXiv API",
        "per_domain_target": per_domain,
        "domain_category_queries": DOMAIN_CATEGORY_QUERIES,
        "domain_counts": domain_counts,
        "notes": notes,
        "papers": papers,
    }
    out = Path(args.out_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "out_json": str(out),
                "papers_total": len(papers),
                "domain_counts": domain_counts,
                "notes_count": len(notes),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

