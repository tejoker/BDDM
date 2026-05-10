#!/usr/bin/env python3
"""Mark stale UNRESOLVED ledger rows as TRANSLATION_LIMITED.

A "ghost" row is a ledger entry whose corresponding `output/<paper>.lean` file
is missing — typically because the translator failed during onboarding and the
.lean file was never produced (or was cleaned up post-failure), but the ledger
entry persisted with `error_message="translate-only mode"`.

These ghost rows pollute the UNRESOLVED count and the closure-rate denominator
without representing real proof attempts. Re-classifying them as
TRANSLATION_LIMITED is the honest move: the statement could not be formalized
in Lean, so it is excluded from the proving-rate scope (per
`pipeline_status_models.VerificationStatus.TRANSLATION_LIMITED`'s comment).

Re-onboarding via `onboard_arxiv_paper.py` is the path back from
TRANSLATION_LIMITED to UNRESOLVED/closed when translator improvements land.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_LEDGER_DIR = Path("output/verification_ledgers")
DEFAULT_LEAN_DIR = Path("output")
DEFAULT_REPRO_DIR = Path("reproducibility/full_paper_reports")


def is_ghost_row(entry: dict[str, Any]) -> bool:
    """A row is a ghost if it's UNRESOLVED, was produced by translate-only mode
    (or has empty proof_method), and shows no real proof attempt happened
    (rounds_used == 0 and proof_method ∈ {"", "unknown"})."""
    if str(entry.get("status", "") or "") != "UNRESOLVED":
        return False
    err = str(entry.get("error_message", "") or "")
    proof_method = str(entry.get("proof_method", "") or "")
    rounds = int(entry.get("rounds_used", 0) or 0)
    # Either explicit translate-only marker or no proof attempt info at all.
    if "translate-only mode" in err:
        return True
    if rounds == 0 and proof_method in {"", "unknown", "translation_limited"}:
        # No real proof was attempted; the row is purely translation-state.
        return True
    return False


def mark_ledger_file(
    ledger_path: Path,
    paper_id: str,
    lean_dir: Path,
    *,
    write: bool = False,
) -> dict[str, int]:
    """Mark ghosts in a single ledger file. Idempotent."""
    if not ledger_path.exists():
        return {"rows": 0, "marked": 0, "lean_file_missing": False}

    lean_file = lean_dir / f"{paper_id}.lean"
    lean_file_missing = not lean_file.exists()

    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    entries = data if isinstance(data, list) else data.get("entries", [])
    marked = 0
    for entry in entries:
        if not lean_file_missing:
            # Don't auto-flag papers whose .lean still exists; the row may be
            # legitimately UNRESOLVED for proof-search reasons, not translation.
            continue
        if not is_ghost_row(entry):
            continue
        # Reclassify.
        entry["status"] = "TRANSLATION_LIMITED"
        entry["proof_method"] = "translation_limited"
        entry["failure_origin"] = "FORMALIZATION_ERROR"
        entry["failure_kind"] = "lean_file_missing_post_translate"
        if not entry.get("error_message"):
            entry["error_message"] = "ghost row: lean file missing or never produced post-translate"
        marked += 1

    if marked and write:
        payload = data if isinstance(data, list) else {**data, "entries": entries}
        ledger_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return {"rows": len(entries), "marked": marked, "lean_file_missing": lean_file_missing}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger-dir", type=Path, default=DEFAULT_LEDGER_DIR)
    parser.add_argument("--lean-dir", type=Path, default=DEFAULT_LEAN_DIR)
    parser.add_argument("--write", action="store_true", help="Apply changes; default is dry-run")
    args = parser.parse_args()

    summary: dict[str, Any] = {
        "schema_version": "ghost_translation_marker.v1",
        "dry_run": not args.write,
        "papers": {},
    }
    total_marked = 0
    for ledger_path in sorted(args.ledger_dir.glob("*.json")):
        name = ledger_path.stem
        # Skip non-canonical variants (smoke / actionable / repair etc.)
        if any(s in name for s in ("_smoke", "_actionable", "_fdcheck", "_patchcheck", "_rflguard", "_repair", "_reliable", "ab_repair", "_fast")):
            continue
        result = mark_ledger_file(ledger_path, name, args.lean_dir, write=args.write)
        if result["marked"] > 0 or result["lean_file_missing"]:
            summary["papers"][name] = result
            total_marked += result["marked"]
    summary["total_marked"] = total_marked
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
