#!/usr/bin/env python3
"""Manage multi-domain arXiv formalization rollout execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pipeline_orchestrator import PipelineOrchestrator


DOMAIN_ORDER = [
    "probability_statistics",
    "analysis_pde",
    "optimization",
    "algebra_number_theory",
    "remaining_cs_math",
]


def _domain_queue_file(root: Path, domain: str) -> Path:
    return root / f"{domain}.txt"


def _load_ids(path: Path) -> list[str]:
    if not path.exists():
        return []
    out: list[str] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        out.append(ln.replace("arxiv:", "").strip())
    return out


def enqueue_domain(
    *,
    orch: PipelineOrchestrator,
    queue_root: Path,
    domain: str,
    max_items: int,
) -> dict[str, Any]:
    qf = _domain_queue_file(queue_root, domain)
    ids = _load_ids(qf)
    if max_items > 0:
        ids = ids[:max_items]
    queued = dup = 0
    for pid in ids:
        r = orch.enqueue(
            paper_id=pid,
            config={"domain": domain, "rollout": True},
        )
        if r.get("status") == "queued":
            queued += 1
        else:
            dup += 1
    return {"domain": domain, "total": len(ids), "queued": queued, "duplicates": dup, "queue_file": str(qf)}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="arXiv multi-domain rollout manager")
    p.add_argument("--orch-root", default="output/orchestrator")
    p.add_argument("--queue-root", default="data/rollout_queues")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("enqueue-domain")
    e.add_argument("--domain", required=True, choices=DOMAIN_ORDER)
    e.add_argument("--max-items", type=int, default=0)

    ea = sub.add_parser("enqueue-all")
    ea.add_argument("--max-items-per-domain", type=int, default=0)

    sub.add_parser("status")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    orch = PipelineOrchestrator(Path(args.orch_root))
    queue_root = Path(args.queue_root)

    if args.cmd == "enqueue-domain":
        payload = enqueue_domain(
            orch=orch,
            queue_root=queue_root,
            domain=str(args.domain),
            max_items=max(0, int(args.max_items)),
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "enqueue-all":
        rows = []
        for d in DOMAIN_ORDER:
            rows.append(
                enqueue_domain(
                    orch=orch,
                    queue_root=queue_root,
                    domain=d,
                    max_items=max(0, int(args.max_items_per_domain)),
                )
            )
        print(json.dumps({"domains": rows, "queue": orch.queue_dashboard()}, indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "status":
        print(json.dumps({"domain_order": DOMAIN_ORDER, "queue": orch.queue_dashboard()}, indent=2, ensure_ascii=False))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

