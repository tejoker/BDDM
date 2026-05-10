#!/usr/bin/env python3
"""Retroactive upgrader for existing Desol/PaperTheory/Paper_*.lean stubs.

Scans each paper-theory module that was generated BEFORE
`paper_theory_builder._auto_instance_lines` / `_aesop_attribute_lines` landed,
and appends the missing standard typeclass instances (LE/LT/Preorder/
PartialOrder/DecidableEq) and `attribute [aesop safe apply]` tags. Idempotent:
re-running on an already-upgraded file is a no-op.

Mirrors the reference layout of `Paper_2304_09598.lean`, which had these lines
hand-curated last round, so all other papers get the same generality.

Usage:
    python3 scripts/upgrade_existing_paper_theory_stubs.py
        [--dry-run]              # print what would change without writing
        [--paper-theory-dir DIR] # default Desol/PaperTheory
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Reuse the emission helpers from paper_theory_builder so the retroactive
# behaviour cannot drift from new-stub generation.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_theory_builder import (  # noqa: E402
    _ABBREV_TYPE_RE,
    _AUTO_INSTANCE_CLASSES,
    _aesop_attribute_lines,
    _auto_instance_lines,
)


END_NAMESPACE_RE = re.compile(r"^\s*end\s+(Paper_[A-Za-z0-9_]+)\s*$", re.MULTILINE)


def _extract_decls_block(text: str, start_marker: str, end_marker: str) -> tuple[int, int, str]:
    """Return (start, end, block_text) of the section between two header markers."""
    s = text.find(start_marker)
    if s < 0:
        return -1, -1, ""
    s_end = text.find("\n", s) + 1
    e = text.find(end_marker, s_end) if end_marker else len(text)
    if e < 0:
        e = len(text)
    return s_end, e, text[s_end:e]


def _split_decls(block: str) -> list[str]:
    """Split a decl block into individual top-level declarations."""
    return [chunk.strip() for chunk in re.split(r"\n\s*\n", block) if chunk.strip()]


def _already_has_instance(text: str, type_name: str, cls: str) -> bool:
    pattern = rf"^\s*instance\s*:\s*{re.escape(cls)}\s+{re.escape(type_name)}\s*:="
    return bool(re.search(pattern, text, flags=re.MULTILINE))


def _already_has_aesop_attr(text: str, axiom_name: str) -> bool:
    pattern = rf"^\s*attribute\s*\[aesop\s+[^\]]*\]\s+{re.escape(axiom_name)}\b"
    return bool(re.search(pattern, text, flags=re.MULTILINE))


def upgrade_file(path: Path, *, dry_run: bool = False) -> dict:
    """Append missing instance/attribute lines to a single Paper_*.lean file.

    Returns a summary dict with `path`, `instances_added`, `axioms_tagged`, and `changed`.
    """
    text = path.read_text(encoding="utf-8")

    # 1. Compute what auto-emission WOULD produce for this file's decls.
    # Find every definition (an "abbrev" line) anywhere in the file, treat each as a one-line decl.
    abbrev_decls = [
        f"abbrev {m.group(1)} : Type := {m.group(2)}"
        for m in _ABBREV_TYPE_RE.finditer(text)
    ]
    candidate_instance_lines = _auto_instance_lines(abbrev_decls)

    # Find every paper-local `axiom Name ...` declaration (single-line or multi-line).
    axiom_decls: list[str] = []
    for match in re.finditer(
        r"^\s*axiom\s+([A-Za-z_][A-Za-z0-9_']*)\b[^\n]*(?:\n[^\n]*)*?(?=\n\s*\n|\nend\s+|\Z)",
        text,
        flags=re.MULTILINE,
    ):
        axiom_decls.append(match.group(0))
    candidate_aesop_lines = _aesop_attribute_lines(axiom_decls)

    # 2. Filter out lines already present in the file.
    instances_to_add: list[str] = []
    for line in candidate_instance_lines:
        m = re.match(r"^instance\s*:\s*([A-Za-z_]+)\s+([A-Za-z_][A-Za-z0-9_']*)", line)
        if not m:
            continue
        cls, type_name = m.group(1), m.group(2)
        if not _already_has_instance(text, type_name, cls):
            instances_to_add.append(line)

    aesop_to_add: list[str] = []
    for line in candidate_aesop_lines:
        m = re.match(r"^attribute\s*\[aesop\s+[^\]]*\]\s+([A-Za-z_][A-Za-z0-9_']*)", line)
        if not m:
            continue
        axiom_name = m.group(1)
        if not _already_has_aesop_attr(text, axiom_name):
            aesop_to_add.append(line)

    summary = {
        "path": str(path),
        "instances_added": len(instances_to_add),
        "axioms_tagged": len(aesop_to_add),
        "changed": False,
    }
    if not instances_to_add and not aesop_to_add:
        return summary

    # 3. Splice the new lines in just before `end Paper_<id>`. If the namespace
    # close line isn't found we conservatively decline to modify the file.
    end_match = END_NAMESPACE_RE.search(text)
    if end_match is None:
        return summary
    insert_at = end_match.start()
    new_block = ""
    if instances_to_add:
        new_block += (
            "-- Standard typeclass instances inherited from the underlying Mathlib type.\n"
            + "\n".join(instances_to_add)
            + "\n\n"
        )
    if aesop_to_add:
        new_block += (
            "-- Aesop tactic registration for paper-local axioms.\n"
            + "\n".join(aesop_to_add)
            + "\n\n"
        )
    upgraded = text[:insert_at] + new_block + text[insert_at:]

    summary["changed"] = True
    if dry_run:
        return summary

    path.write_text(upgraded, encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Retroactively upgrade Paper_*.lean stubs")
    parser.add_argument("--paper-theory-dir", type=Path, default=Path("Desol/PaperTheory"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    base = args.paper_theory_dir
    if not base.exists():
        print(f"No such directory: {base}", file=sys.stderr)
        return 2

    summaries: list[dict] = []
    for path in sorted(base.rglob("Paper_*.lean")):
        summaries.append(upgrade_file(path, dry_run=args.dry_run))

    total_inst = sum(s["instances_added"] for s in summaries)
    total_aesop = sum(s["axioms_tagged"] for s in summaries)
    changed = sum(1 for s in summaries if s["changed"])
    for s in summaries:
        if s["instances_added"] or s["axioms_tagged"]:
            print(
                f"{s['path']}: +{s['instances_added']} instance(s), "
                f"+{s['axioms_tagged']} aesop tag(s){' (dry-run)' if args.dry_run else ''}"
            )
    print()
    print(
        f"Summary: {changed} file(s) {'would change' if args.dry_run else 'changed'}, "
        f"+{total_inst} instances, +{total_aesop} aesop tags."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
