#!/usr/bin/env python3
"""Rewrite placeholder theorems in `output/<paper>.lean` using the full
signatures stored in the verification ledger.

Many ledger rows record a real, plausible theorem signature in
`lean_statement` even though the translator's validation gate marked the
translation BLOCKED (e.g. `claim_shape_mismatch`, `false_target`) and emitted
a placeholder body (`theorem foo : False := by sorry`) into the .lean file.

The validation gate has false positives — `∃ C, P(C)` for a paper claim
"there exists C with bound" gets flagged as `claim_shape_mismatch:ineq->exists`
even though the existential IS the right form. This tool gives proof search
a chance to attempt the real signature: it walks the .lean file, finds each
placeholder, looks up the ledger row by theorem_name, and (if the ledger
has a non-trivial signature with parameters / hypotheses / a real conclusion)
writes that signature back in place of the placeholder.

Reversible — backs up the .lean file as `<file>.lean.bak.ledger_rewrite`.
Pipeline policy: no Mistral calls; this is purely local rewriting.

Usage:
    python3 scripts/rewrite_lean_from_ledger.py 2604.21616 --write
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


_PLACEHOLDER_RE = re.compile(
    r"^theorem\s+(?P<name>[A-Za-z_][A-Za-z0-9_'.]*)\s*:\s*"
    r"(?P<placeholder>False|True)\s*:=\s*by\s+sorry\b",
    flags=re.MULTILINE,
)
_TRIVIAL_RE = re.compile(
    r"^theorem\s+(?P<name>[A-Za-z_][A-Za-z0-9_'.]*)\s*:\s*True\s*:=\s*trivial\b",
    flags=re.MULTILINE,
)


def _is_trivial_signature(lean_statement: str) -> bool:
    """A signature is trivial if it contains no parameters/hypotheses and the
    conclusion is False/True/x=x. We don't rewrite for these — they wouldn't
    yield a more provable form than the existing placeholder."""
    s = " ".join((lean_statement or "").split())
    if not s:
        return True
    if ": False :=" in s or ": True :=" in s:
        return True
    # No parens means no parameters/hypotheses.
    if "(" not in s:
        return True
    # `∃ x : ℝ, x = x` and similar self-equality scaffolds.
    m = re.search(r"∃\s+(\w+)\s*:\s*[^,]+,\s*(\w+)\s*=\s*(\w+)", s)
    if m and m.group(1) == m.group(2) == m.group(3):
        return True
    return False


def _strip_proof_body(lean_statement: str) -> str:
    """Drop `:= by …` / `:= …` from the end of a stored signature so we can
    emit `:= by sorry` consistently."""
    s = lean_statement.rstrip()
    # Find the LAST `:= by` or trailing `:=` and strip it.
    m = re.search(r"\s*:=\s*by\b.*$", s, flags=re.DOTALL)
    if m:
        s = s[: m.start()].rstrip()
    elif s.endswith(":="):
        s = s[:-2].rstrip()
    return s


def rewrite_paper(
    paper_id: str,
    *,
    project_root: Path,
    write: bool = False,
) -> dict[str, Any]:
    lean_path = project_root / "output" / f"{paper_id}.lean"
    ledger_path = project_root / "output" / "verification_ledgers" / f"{paper_id}.json"
    if not lean_path.exists() or not ledger_path.exists():
        return {"paper_id": paper_id, "lean_or_ledger_missing": True, "rewritten": 0}

    text = lean_path.read_text(encoding="utf-8")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    entries = ledger if isinstance(ledger, list) else ledger.get("entries", [])

    # Build a name -> stored-signature index from the ledger.
    by_name: dict[str, str] = {}
    for entry in entries:
        name = str(entry.get("theorem_name", "") or "").strip()
        if not name:
            continue
        bare = name.rsplit(".", 1)[-1]
        ls = str(entry.get("lean_statement", "") or "").strip()
        if not ls or _is_trivial_signature(ls):
            continue
        # Verify the signature actually starts with `theorem` to avoid weird inputs.
        if not re.match(r"^\s*(theorem|lemma)\s+", ls):
            continue
        by_name[bare] = ls
        # Also store with the full namespaced name in case of duplicates.
        by_name.setdefault(name, ls)

    summary: dict[str, Any] = {
        "schema_version": "rewrite_lean_from_ledger.v1",
        "paper_id": paper_id,
        "lean_file": str(lean_path.relative_to(project_root)),
        "placeholders_found": 0,
        "rewritten": 0,
        "skipped_no_ledger_signature": 0,
        "skipped_trivial_signature": 0,
        "results": [],
        "dry_run": not write,
    }

    new_text_parts: list[str] = []
    cursor = 0
    for match in _PLACEHOLDER_RE.finditer(text):
        summary["placeholders_found"] += 1
        name = match.group("name")
        new_sig = by_name.get(name) or by_name.get(name.rsplit(".", 1)[-1])
        if not new_sig:
            summary["skipped_no_ledger_signature"] += 1
            summary["results"].append({"name": name, "action": "skipped_no_ledger_signature"})
            continue
        body = _strip_proof_body(new_sig)
        replacement = body + " := by sorry"
        # Append everything before the match, then the replacement.
        new_text_parts.append(text[cursor : match.start()])
        new_text_parts.append(replacement)
        cursor = match.end()
        summary["rewritten"] += 1
        summary["results"].append({
            "name": name,
            "action": "rewritten",
            "stored_signature_chars": len(new_sig),
        })
    new_text_parts.append(text[cursor:])

    # Also handle `theorem foo : True := trivial` placeholders.
    intermediate = "".join(new_text_parts)
    final_parts: list[str] = []
    cursor = 0
    for match in _TRIVIAL_RE.finditer(intermediate):
        summary["placeholders_found"] += 1
        name = match.group("name")
        new_sig = by_name.get(name) or by_name.get(name.rsplit(".", 1)[-1])
        if not new_sig:
            summary["skipped_no_ledger_signature"] += 1
            summary["results"].append({"name": name, "action": "skipped_no_ledger_signature"})
            continue
        body = _strip_proof_body(new_sig)
        replacement = body + " := by sorry"
        final_parts.append(intermediate[cursor : match.start()])
        final_parts.append(replacement)
        cursor = match.end()
        summary["rewritten"] += 1
        summary["results"].append({
            "name": name,
            "action": "rewritten_from_trivial_placeholder",
            "stored_signature_chars": len(new_sig),
        })
    final_parts.append(intermediate[cursor:])

    new_text = "".join(final_parts)
    if write and summary["rewritten"] > 0 and new_text != text:
        backup = lean_path.with_suffix(".lean.bak.ledger_rewrite")
        backup.write_text(text, encoding="utf-8")
        lean_path.write_text(new_text, encoding="utf-8")
        summary["backup"] = str(backup.relative_to(project_root))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paper_id", help="arxiv id, e.g. 2604.21616")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--write", action="store_true", help="Apply rewrites; default is dry-run")
    args = parser.parse_args()

    summary = rewrite_paper(args.paper_id, project_root=args.project_root, write=args.write)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
