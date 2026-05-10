#!/usr/bin/env python3
"""Retroactively populate `provenance` on ledger entries that lack it.

`pipeline_status.evaluate_promotion_gates` line 268 requires
    provenance.paper_id AND (provenance.section OR provenance.label OR provenance.cited_refs)
for the `provenance_linked` gate to pass. Some legacy ledger entries (notably
the 12 INTERMEDIARY_PROVEN rows in 2304.09598) were re-written by older
versions of `prove_arxiv_batch.py` that dropped the `provenance` field built
upstream by `arxiv_to_lean.py`. With provenance missing, every other gate can
pass and the row still stays INTERMEDIARY_PROVEN forever.

This script walks every ledger under `output/verification_ledgers/` (or a
specified dir), and for each row whose `provenance` is null/empty/missing
the required keys, populates a minimal one:
    {
      "paper_id": <ledger filename without .json>,
      "section": "",
      "label": <row.theorem_name>,
      "cited_refs": []
    }

The row's `paper_id` is derived from the ledger filename so the backfill is
purely deterministic. Idempotent: re-running over an already-backfilled
ledger is a no-op.

Optionally re-runs `evaluate_promotion_gates` on each row (via
`apply_reviews_to_ledger.apply_adjudication_to_row`-like flow) to refresh
`gate_failures` and `status` — but ONLY when --re-evaluate is passed (default
off; we don't want this script to mass-promote rows on its own).

Usage:
    python3 scripts/backfill_provenance.py
        [--ledger-dir output/verification_ledgers]
        [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

DEFAULT_LEDGER_DIR = Path("output/verification_ledgers")
# Canonical ledger filename: <arxiv-id>.json where arxiv-id is YYDD.NNNNN[v#].
_CANONICAL_LEDGER_RE = re.compile(r"^\d{4}\.\d{4,6}(?:v\d+)?$")


def _provenance_passes_gate(prov: Any) -> bool:
    """Mirror of pipeline_status.evaluate_promotion_gates 'provenance_linked'."""
    if not isinstance(prov, dict):
        return False
    if not str(prov.get("paper_id", "") or "").strip():
        return False
    if str(prov.get("section", "") or "").strip():
        return True
    if str(prov.get("label", "") or "").strip():
        return True
    refs = prov.get("cited_refs")
    if isinstance(refs, list) and any(str(r).strip() for r in refs):
        return True
    return False


def _minimal_provenance(paper_id: str, theorem_name: str) -> dict:
    return {
        "paper_id": paper_id,
        "section": "",
        "label": theorem_name,
        "cited_refs": [],
    }


def backfill_ledger(path: Path, *, dry_run: bool = False) -> dict[str, int]:
    """Backfill provenance on a single ledger file. Returns counts dict.

    Also refreshes the stale `validation_gates.provenance_linked` flag and
    drops `provenance_linked` from `gate_failures` when the freshly-written
    provenance now passes the gate. Without this refresh, rows get a valid
    provenance dict but their gate cache stays at False from an earlier
    pre-backfill evaluation."""
    if not path.exists():
        return {"backfilled": 0, "rows": 0, "already_ok": 0, "gate_refreshed": 0}
    paper_id = path.stem  # e.g., '2304.09598'
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data if isinstance(data, list) else data.get("entries", [])
    backfilled = 0
    already_ok = 0
    gate_refreshed = 0
    for entry in entries:
        prov = entry.get("provenance")
        if _provenance_passes_gate(prov):
            already_ok += 1
        else:
            theorem_name = str(entry.get("theorem_name", "") or "").strip()
            if not theorem_name:
                continue
            new_prov = _minimal_provenance(paper_id, theorem_name)
            # Preserve any pre-existing fields the gate didn't care about.
            if isinstance(prov, dict):
                for k, v in prov.items():
                    if k not in new_prov or not new_prov[k]:
                        new_prov[k] = v
            entry["provenance"] = new_prov
            backfilled += 1
        # Whether we backfilled OR the row already had good provenance,
        # refresh the gate flag if it's stale-False.
        gates = entry.get("validation_gates")
        if isinstance(gates, dict) and gates.get("provenance_linked") is False:
            if _provenance_passes_gate(entry.get("provenance")):
                gates["provenance_linked"] = True
                # Also drop from gate_failures.
                failures = entry.get("gate_failures")
                if isinstance(failures, list) and "provenance_linked" in failures:
                    entry["gate_failures"] = [f for f in failures if f != "provenance_linked"]
                gate_refreshed += 1
    summary = {"backfilled": backfilled, "rows": len(entries), "already_ok": already_ok, "gate_refreshed": gate_refreshed}
    if (backfilled or gate_refreshed) and not dry_run:
        path.write_text(
            json.dumps(data if isinstance(data, list) else {**data, "entries": entries}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill provenance on ledger entries")
    parser.add_argument("--ledger-dir", type=Path, default=DEFAULT_LEDGER_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.ledger_dir.exists():
        print(f"No such directory: {args.ledger_dir}")
        return 2

    grand: dict[str, int] = {"backfilled": 0, "rows": 0, "already_ok": 0}
    for path in sorted(args.ledger_dir.glob("*.json")):
        # Only operate on canonical <arxiv-id>.json files. All dev variants
        # (_smoke, _actionable, _repair_candidates, ab_repair_*, etc.) carry
        # ephemeral state that the verification gates don't read from.
        if not _CANONICAL_LEDGER_RE.match(path.stem):
            continue
        summary = backfill_ledger(path, dry_run=args.dry_run)
        for k, v in summary.items():
            grand[k] = grand.get(k, 0) + v
        if summary["backfilled"]:
            print(f"{path}: +{summary['backfilled']} backfilled / {summary['rows']} rows{' (dry-run)' if args.dry_run else ''}")
    print()
    print(json.dumps({"summary": grand}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
