#!/usr/bin/env python3
"""Audit cross-tier consistency between `validation_gates` and `gate_failures`.

A ledger row's `validation_gates` is a dict of gate_name -> bool. The
companion `gate_failures` list should contain EXACTLY the gate names whose
`validation_gates` value is False. Drift between the two is a data-integrity
defect — it can mask bypass-promotions from the standards-positive audits.

Surfaced patterns:
  - `validation_gates.lean_proof_closed=True` but `'lean_proof_closed' in gate_failures`
  - same for `claim_equivalent`, `independent_semantic_equivalence_evidence`, etc.

This audit walks each canonical ledger and ALIGNS `gate_failures` to
`validation_gates` (the gates dict is the source of truth — it's what
`evaluate_promotion_gates` produces). The fix is purely a list rebuild;
no status change.

Standards-positive: this audit only DROPS spurious entries from
`gate_failures`; it does not ADD entries (which could mask real failures).
If a gate is False but missing from gate_failures, that's a separate
class of defect we WARN about but don't auto-fix.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_LEDGER_DIR = Path("reproducibility/full_paper_reports")
DEFAULT_EPHEMERAL_DIR = Path("output/verification_ledgers")


@dataclass
class GateConsistencyResult:
    paper_id: str
    rows: int = 0
    rebuilt: int = 0          # rows whose gate_failures was rebuilt
    missing_entries: int = 0   # gates False but absent from gate_failures (WARN)
    drift_samples: list[str] = field(default_factory=list)


def reconcile_entry(entry: dict[str, Any]) -> tuple[bool, list[str]]:
    """Return (mutated, dropped_gate_names).

    Drops any gate name from `gate_failures` whose `validation_gates` value
    is True. Does NOT add missing entries — caller decides.
    """
    gates = entry.get("validation_gates")
    failures = entry.get("gate_failures")
    if not isinstance(gates, dict) or not isinstance(failures, list):
        return False, []
    dropped: list[str] = []
    cleaned: list[str] = []
    for name in failures:
        if not isinstance(name, str):
            cleaned.append(name)
            continue
        val = gates.get(name)
        if val is True:
            dropped.append(name)
            continue
        cleaned.append(name)
    if dropped:
        entry["gate_failures"] = cleaned
        return True, dropped
    return False, []


def check_missing_entries(entry: dict[str, Any]) -> list[str]:
    """Return gate names whose `validation_gates` value is False but the
    gate isn't listed in `gate_failures`. These are SUSPICIOUS but the
    audit doesn't auto-fix them — they could indicate the row genuinely
    didn't run that gate, in which case adding to failures would be wrong.
    """
    gates = entry.get("validation_gates")
    failures = entry.get("gate_failures")
    if not isinstance(gates, dict) or not isinstance(failures, list):
        return []
    failure_set = {f for f in failures if isinstance(f, str)}
    missing: list[str] = []
    for name, val in gates.items():
        if val is False and name not in failure_set:
            missing.append(str(name))
    return missing


def audit_ledger_file(ledger_path: Path, *, write: bool = False) -> GateConsistencyResult:
    if not ledger_path.exists():
        return GateConsistencyResult(paper_id=ledger_path.stem)
    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    entries = data if isinstance(data, list) else data.get("entries", [])
    paper_id = ledger_path.parent.name if ledger_path.parent.name != "verification_ledgers" else ledger_path.stem
    result = GateConsistencyResult(paper_id=paper_id)
    any_change = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        result.rows += 1
        mutated, dropped = reconcile_entry(entry)
        if mutated:
            result.rebuilt += 1
            any_change = True
            if len(result.drift_samples) < 4:
                result.drift_samples.append(
                    f"{entry.get('theorem_name', '?')}: dropped {dropped}"
                )
        missing = check_missing_entries(entry)
        if missing:
            result.missing_entries += 1
    if write and any_change:
        payload = data if isinstance(data, list) else {**data, "entries": entries}
        ledger_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repro-dir", type=Path, default=DEFAULT_LEDGER_DIR)
    parser.add_argument("--ephemeral-dir", type=Path, default=DEFAULT_EPHEMERAL_DIR)
    parser.add_argument("--write", action="store_true", help="Apply rebuilds; default is dry-run")
    parser.add_argument(
        "--fail-on-rebuild",
        action="store_true",
        help=(
            "Exit non-zero when any row would need its `gate_failures` list "
            "rebuilt. Intended for CI gates: ledger drift between "
            "validation_gates and gate_failures is a data-integrity defect "
            "that should block merges."
        ),
    )
    args = parser.parse_args()

    summary: dict[str, Any] = {
        "schema_version": "audit_gate_consistency.v1",
        "dry_run": not args.write,
        "papers": {},
        "totals": {"rows": 0, "rebuilt": 0, "missing_entries": 0},
    }

    # Canonical reproducibility ledgers
    if args.repro_dir.exists():
        for p in sorted(args.repro_dir.glob("*/verification_ledger.json")):
            r = audit_ledger_file(p, write=args.write)
            summary["papers"][f"canonical:{r.paper_id}"] = {
                "rows": r.rows,
                "rebuilt": r.rebuilt,
                "missing_entries": r.missing_entries,
                "drift_samples": r.drift_samples,
            }
            summary["totals"]["rows"] += r.rows
            summary["totals"]["rebuilt"] += r.rebuilt
            summary["totals"]["missing_entries"] += r.missing_entries

    # Ephemeral ledgers (skip the *_smoke/*_actionable/*_repair etc.)
    if args.ephemeral_dir.exists():
        for p in sorted(args.ephemeral_dir.glob("*.json")):
            if any(s in p.stem for s in ("_smoke", "_actionable", "_repair", "_reliable", "ab_repair", "_fdcheck", "_patchcheck", "_rflguard", "_fast")):
                continue
            r = audit_ledger_file(p, write=args.write)
            summary["papers"][f"ephemeral:{r.paper_id}"] = {
                "rows": r.rows,
                "rebuilt": r.rebuilt,
                "missing_entries": r.missing_entries,
                "drift_samples": r.drift_samples,
            }
            summary["totals"]["rows"] += r.rows
            summary["totals"]["rebuilt"] += r.rebuilt
            summary["totals"]["missing_entries"] += r.missing_entries

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.fail_on_rebuild and summary["totals"]["rebuilt"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
