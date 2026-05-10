#!/usr/bin/env python3
"""Re-apply translation repairs to an existing ledger without re-running formalize.

`scripts/formalize_paper_full.py` already invokes `repair_bad_translations.py`
and applies validated repair candidates back into the ledger via
`_apply_validated_translation_repairs` (around line 2979). But running that
pass requires a full formalize cycle (translate → paper-theory → prove → ...),
which is expensive and not always available.

This script provides the lightweight alternative:
  1. Read the existing ledger at `output/verification_ledgers/<paper>.json`.
  2. Run `repair_bad_translations.build_repair_pack(...)` against the current
     `output/<paper>.lean` and the ledger-derived report.
  3. Apply the validated repair candidates back to the ledger entries
     (reuses `_apply_validated_translation_repairs`).
  4. Write the updated ledger.

Idempotent: re-running on an already-repaired ledger is a no-op (validated
candidates that already match the ledger's `lean_statement` produce no changes).

Usage:
    python3 scripts/apply_translation_repairs.py --paper-id 2604.21884
        [--project-root .]
        [--lean-file output/2604.21884.lean]
        [--ledger output/verification_ledgers/2604.21884.json]
        [--out-dir output/translation_repairs/2604_21884]
        [--skip-validate]   # skip lake-build of repair candidates (fast preview)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _safe_id(paper_id: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_]", "_", (paper_id or "").strip())


def _load_ledger(path: Path) -> tuple[Any, list[dict[str, Any]]]:
    if not path.exists():
        raise FileNotFoundError(f"ledger not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data if isinstance(data, list) else data.get("entries", [])
    return data, entries


def _save_ledger(path: Path, data: Any, entries: list[dict[str, Any]]) -> None:
    if isinstance(data, list):
        path.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        out = {**data, "entries": entries}
        path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _make_report_from_ledger(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a translation-report-shaped payload from ledger entries.

    `repair_bad_translations.build_repair_pack` reads a `report` JSON to find
    bad theorems (status==FLAWED, failure_kind==elaboration_failure, etc.).
    The ledger is sufficient — entries already carry the same status/error
    fields. We synthesize the report shape it expects."""
    return {
        "results": [
            {
                "theorem_name": str(r.get("theorem_name", "") or ""),
                "status": str(r.get("status", "") or ""),
                "failure_kind": str(r.get("failure_kind", "") or ""),
                "error_message": str(r.get("error_message", "") or ""),
                "lean_statement": str(r.get("lean_statement", "") or ""),
                "source_statement": str(r.get("source_statement", "") or r.get("source_latex", "") or ""),
                "paper_statement_id": str(r.get("paper_statement_id", "") or ""),
            }
            for r in entries
        ]
    }


def apply_translation_repairs(
    *,
    paper_id: str,
    project_root: Path,
    lean_file: Path,
    ledger_path: Path,
    out_dir: Path,
    validate_candidates: bool = True,
) -> dict[str, Any]:
    from repair_bad_translations import build_repair_pack
    from formalize_paper_full import _apply_validated_translation_repairs

    data, entries = _load_ledger(ledger_path)

    # Synthesize a report; persist so build_repair_pack can read it from disk.
    report_payload = _make_report_from_ledger(entries)
    report_path = out_dir / f"_report_{_safe_id(paper_id)}.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    repair_payload = build_repair_pack(
        paper_id=paper_id,
        report_path=report_path,
        lean_file=lean_file,
        project_root=project_root,
        out_dir=out_dir,
        validate_candidates=validate_candidates,
    )
    new_entries, application = _apply_validated_translation_repairs(entries, repair_payload)
    if application.get("updated_count", 0):
        _save_ledger(ledger_path, data, new_entries)
    return {
        "paper_id": paper_id,
        "ledger_path": str(ledger_path),
        "repair_pack_dir": str(out_dir),
        "candidates": len(repair_payload.get("repair_candidates", [])),
        "updated_count": application.get("updated_count", 0),
        "updated_theorems": application.get("updated_theorems", []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply translation repairs to an existing ledger")
    parser.add_argument("--paper-id", required=True)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--lean-file", type=Path, default=None,
                        help="Defaults to output/<paper-id>.lean")
    parser.add_argument("--ledger", type=Path, default=None,
                        help="Defaults to output/verification_ledgers/<paper-id>.json")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="Defaults to output/translation_repairs/<safe-paper-id>")
    parser.add_argument("--skip-validate", action="store_true",
                        help="Skip lake-build validation of repair candidates (fast preview)")
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    paper_id = args.paper_id
    safe = _safe_id(paper_id)
    lean_file = (args.lean_file or (project_root / "output" / f"{paper_id}.lean")).resolve()
    ledger_path = (args.ledger or (project_root / "output" / "verification_ledgers" / f"{paper_id}.json")).resolve()
    out_dir = (args.out_dir or (project_root / "output" / "translation_repairs" / safe)).resolve()

    result = apply_translation_repairs(
        paper_id=paper_id,
        project_root=project_root,
        lean_file=lean_file,
        ledger_path=ledger_path,
        out_dir=out_dir,
        validate_candidates=not args.skip_validate,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
