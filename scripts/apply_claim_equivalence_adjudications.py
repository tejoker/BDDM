#!/usr/bin/env python3
"""Apply claim-equivalence adjudications to a verification ledger."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from claim_equivalence_review import (
    apply_adjudications_to_entries,
    ledger_document_like,
    ledger_entries,
    read_json,
    read_jsonl,
    write_json,
)


def apply_adjudication_file(
    *,
    ledger: Path,
    adjudications: Path,
    out_json: Path,
    paper_id: str = "",
    min_confidence: float = 0.80,
) -> dict:
    ledger_payload = read_json(ledger)
    if ledger_payload is None:
        raise FileNotFoundError(f"could not read ledger: {ledger}")
    rows = ledger_entries(ledger_payload)
    adjudication_rows = read_jsonl(adjudications)
    updated, summary = apply_adjudications_to_entries(
        rows,
        adjudication_rows,
        paper_id=paper_id or (ledger_payload.get("paper_id", "") if isinstance(ledger_payload, dict) else ""),
        min_confidence=min_confidence,
    )
    out_doc = ledger_document_like(ledger_payload, updated)
    write_json(out_json, out_doc)
    return {
        "out_json": str(out_json),
        "source_ledger": str(ledger),
        "adjudications": str(adjudications),
        "rows": len(updated),
        **summary,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply claim-equivalence adjudication JSONL to a ledger")
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--adjudications", required=True, type=Path)
    parser.add_argument("--out-json", required=True, type=Path)
    parser.add_argument("--paper-id", default="")
    parser.add_argument("--min-confidence", type=float, default=0.80)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = apply_adjudication_file(
        ledger=args.ledger,
        adjudications=args.adjudications,
        out_json=args.out_json,
        paper_id=args.paper_id,
        min_confidence=float(args.min_confidence),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
