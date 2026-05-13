#!/usr/bin/env python3
"""Retroactively populate `source_latex` on ledger entries that lack it.

`scripts/run_statement_repair_worker.py` (`_source_context_for_row`, line ~175)
reads `source_latex` from each ledger row and forwards it to repair-candidate
generation. Rows whose `source_latex` is empty are filtered out as
`source_latex_missing`, which sinks the entire repair-worker pass.

This script backfills the missing field by looking up the matching entry in
`extracted_theorems.json` (written by the upstream theorem extractor) and
copying its `statement` into `entry["source_latex"]`. It does NOT fabricate
LaTeX: only existing extracted statements are copied. Rows whose name does
not match any extracted entry are left untouched and reported.

Search order for extracted_theorems.json (first hit wins):
    reproducibility/paper_agnostic_golden10_results/<paper_id>/extracted_theorems.json
    reproducibility/full_paper_reports/<paper_id>/extracted_theorems.json

Name normalization mirrors `arxiv_to_lean._lean_name` (non-alphanumeric ->
underscore, collapse runs, strip leading digits, prepend `thm_` if first char
is a digit). Disambiguation suffix `_2`, `_3`, ... is reapplied when the same
base lean-name occurs multiple times in the extracted list, matching how
`arxiv_to_lean.py` (line ~2781) assigns Lean declaration ids.

Idempotent: re-running over an already-backfilled ledger is a no-op. Existing
non-empty `source_latex` values are preserved.

Usage:
    python3 scripts/backfill_source_latex.py
        [--ledger-dir output/verification_ledgers]
        [--extracted-roots reproducibility/paper_agnostic_golden10_results
                           reproducibility/full_paper_reports]
        [--publish]            # also copy each updated ledger to
                               # reproducibility/full_paper_reports/<id>/verification_ledger.json
        [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

DEFAULT_LEDGER_DIR = Path("output/verification_ledgers")
DEFAULT_EXTRACTED_ROOTS = (
    Path("reproducibility/paper_agnostic_golden10_results"),
    Path("reproducibility/full_paper_reports"),
)
PUBLISH_ROOT = Path("reproducibility/full_paper_reports")
# Canonical ledger filename: <arxiv-id>.json where arxiv-id is YYDD.NNNNN[v#].
_CANONICAL_LEDGER_RE = re.compile(r"^\d{4}\.\d{4,6}(?:v\d+)?$")


def _lean_name(raw: str) -> str:
    """Mirror of `arxiv_to_lean._lean_name`.

    Convert a LaTeX label or index into a valid Lean identifier.
    """
    name = re.sub(r"[^A-Za-z0-9_]", "_", raw)
    name = re.sub(r"_+", "_", name).strip("_")
    if name and name[0].isdigit():
        name = "thm_" + name
    return name or "thm_unnamed"


def _load_extracted_entries(extracted_path: Path) -> list[dict[str, Any]]:
    """Read an extracted_theorems.json payload and return its entry list.

    Tolerates both list-shaped and dict-shaped roots, and both `entries`
    and `theorems` keys, mirroring upstream variance.
    """
    if not extracted_path.exists():
        return []
    try:
        data = json.loads(extracted_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    if isinstance(data, dict):
        for key in ("entries", "theorems"):
            value = data.get(key)
            if isinstance(value, list):
                return [e for e in value if isinstance(e, dict)]
    return []


def _build_name_lookup(extracted: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build a {lean-disambiguated-name -> extracted-entry} dict.

    Reapplies the `_2`, `_3`, ... suffix rule from
    `arxiv_to_lean.write_paper_module` (line ~2781) so that repeated
    extracted labels (e.g. two `lem:com1` blocks) map to the same
    lean ids the upstream pipeline produced.

    Also indexes by the raw extracted name as a fallback for ledgers that
    pre-date `_lean_name` normalization.
    """
    lookup: dict[str, dict[str, Any]] = {}
    seen: dict[str, int] = {}
    for entry in extracted:
        raw_name = str(entry.get("name", "") or entry.get("theorem_name", "") or "").strip()
        if not raw_name:
            continue
        base = _lean_name(raw_name)
        count = seen.get(base, 0)
        lean_id = base if count == 0 else f"{base}_{count + 1}"
        seen[base] = count + 1
        lookup.setdefault(lean_id, entry)
        lookup.setdefault(raw_name, entry)
        lookup.setdefault(base, entry)
    return lookup


def _resolve_extracted_path(paper_id: str, roots: list[Path]) -> Path | None:
    for root in roots:
        candidate = root / paper_id / "extracted_theorems.json"
        if candidate.exists():
            return candidate
    return None


def _candidate_names_for_row(entry: dict[str, Any]) -> list[str]:
    """Yield ordered candidate lookup keys for a ledger entry's theorem_name.

    `run_statement_repair_worker._source_context_for_row` already strips a
    leading namespace via `rsplit('.', 1)[-1]`, so a ledger row
    `ArxivPaper.lem_com1_2` should match the extracted `lem:com1` with seen=1.
    """
    raw = str(entry.get("theorem_name", "") or "").strip()
    if not raw:
        return []
    candidates = [raw]
    last = raw.rsplit(".", 1)[-1]
    if last and last != raw:
        candidates.append(last)
    return candidates


def backfill_ledger(
    path: Path,
    *,
    extracted_roots: list[Path],
    dry_run: bool = False,
    publish: bool = False,
    publish_root: Path = PUBLISH_ROOT,
) -> dict[str, Any]:
    """Backfill source_latex on a single ledger file.

    Returns a per-ledger summary dict with counts and the resolved extracted
    path (or `None` if no extracted_theorems.json was found for this paper).
    """
    summary: dict[str, Any] = {
        "backfilled": 0,
        "rows": 0,
        "already_filled": 0,
        "unmatched": 0,
        "extracted_path": None,
        "published": False,
    }
    if not path.exists():
        return summary
    paper_id = path.stem
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data if isinstance(data, list) else data.get("entries", [])
    summary["rows"] = len(entries)

    extracted_path = _resolve_extracted_path(paper_id, extracted_roots)
    if extracted_path is None:
        # No source-of-truth available for this paper. Count unmatched rows
        # but do not write.
        summary["unmatched"] = sum(
            1 for e in entries if isinstance(e, dict) and not str(e.get("source_latex", "") or "").strip()
        )
        summary["already_filled"] = summary["rows"] - summary["unmatched"]
        return summary

    summary["extracted_path"] = str(extracted_path)
    extracted = _load_extracted_entries(extracted_path)
    lookup = _build_name_lookup(extracted)

    backfilled = 0
    already_filled = 0
    unmatched = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        existing = str(entry.get("source_latex", "") or "").strip()
        if existing:
            already_filled += 1
            continue
        match: dict[str, Any] | None = None
        for name in _candidate_names_for_row(entry):
            if name in lookup:
                match = lookup[name]
                break
        if match is None:
            unmatched += 1
            continue
        statement = str(match.get("statement", "") or "").strip()
        if not statement:
            # Empty extracted statement: don't pollute the ledger with "".
            unmatched += 1
            continue
        entry["source_latex"] = statement
        backfilled += 1

    summary["backfilled"] = backfilled
    summary["already_filled"] = already_filled
    summary["unmatched"] = unmatched

    if backfilled and not dry_run:
        rewritten = data if isinstance(data, list) else {**data, "entries": entries}
        path.write_text(
            json.dumps(rewritten, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if publish:
            target = publish_root / paper_id / "verification_ledger.json"
            if target.parent.exists():
                target.write_text(
                    json.dumps(rewritten, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                summary["published"] = True
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill source_latex on ledger entries")
    parser.add_argument("--ledger-dir", type=Path, default=DEFAULT_LEDGER_DIR)
    parser.add_argument(
        "--extracted-roots",
        type=Path,
        nargs="+",
        default=list(DEFAULT_EXTRACTED_ROOTS),
        help="Directories scanned (in order) for <paper_id>/extracted_theorems.json",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Also copy each updated ledger to reproducibility/full_paper_reports/<id>/verification_ledger.json",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.ledger_dir.exists():
        print(f"No such directory: {args.ledger_dir}")
        return 2

    grand: dict[str, int] = {"backfilled": 0, "rows": 0, "already_filled": 0, "unmatched": 0, "published": 0, "papers_no_extracted": 0}
    per_paper: list[dict[str, Any]] = []
    for path in sorted(args.ledger_dir.glob("*.json")):
        if not _CANONICAL_LEDGER_RE.match(path.stem):
            continue
        summary = backfill_ledger(
            path,
            extracted_roots=list(args.extracted_roots),
            dry_run=args.dry_run,
            publish=args.publish,
        )
        per_paper.append({"paper": path.stem, **summary})
        for k in ("backfilled", "rows", "already_filled", "unmatched"):
            grand[k] += int(summary.get(k, 0) or 0)
        if summary.get("published"):
            grand["published"] += 1
        if summary.get("extracted_path") is None:
            grand["papers_no_extracted"] += 1
        if summary["backfilled"] or summary["unmatched"]:
            tag = " (dry-run)" if args.dry_run else ""
            ext_tag = " [no-extracted]" if summary.get("extracted_path") is None else ""
            print(
                f"{path.stem}: +{summary['backfilled']} backfilled / "
                f"{summary['rows']} rows / {summary['unmatched']} unmatched{tag}{ext_tag}"
            )

    print()
    print(json.dumps({"summary": grand, "per_paper": per_paper}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
