#!/usr/bin/env python3
"""Build review queues for theorem claim-equivalence blockers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from claim_equivalence_review import build_review_queue, read_json, write_jsonl


def build_queue_file(*, ledger: Path, report: Path | None, out_jsonl: Path, paper_id: str = "") -> dict:
    ledger_payload = read_json(ledger)
    if ledger_payload is None:
        raise FileNotFoundError(f"could not read ledger: {ledger}")
    report_payload = read_json(report) if report is not None and report.exists() else None
    queue = build_review_queue(
        ledger_payload=ledger_payload,
        paper_id=paper_id,
        source_ledger=str(ledger),
        report_payload=report_payload,
    )
    write_jsonl(out_jsonl, queue)
    return {
        "out_jsonl": str(out_jsonl),
        "source_ledger": str(ledger),
        "report": str(report) if report else "",
        "rows": len(queue),
        "paper_id": paper_id or (ledger_payload.get("paper_id", "") if isinstance(ledger_payload, dict) else ""),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build claim-equivalence review queue JSONL")
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--out-jsonl", required=True, type=Path)
    parser.add_argument("--paper-id", default="")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = build_queue_file(
        ledger=args.ledger,
        report=args.report,
        out_jsonl=args.out_jsonl,
        paper_id=args.paper_id,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
