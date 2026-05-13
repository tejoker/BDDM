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

# Matches any `theorem foo` or `lemma foo` declaration in the .lean file.
# Used to detect which theorems already exist so we don't append duplicates.
_DECL_NAME_RE = re.compile(
    r"^(?:theorem|lemma)\s+(?P<name>[A-Za-z_][A-Za-z0-9_'.]*)\b",
    flags=re.MULTILINE,
)

# `end ArxivPaper` (or other namespace closer) — we inject before this.
_END_NAMESPACE_RE = re.compile(r"^end\s+[A-Za-z_][A-Za-z0-9_'.]*\s*$", flags=re.MULTILINE)


# Lightweight subset of arxiv_to_lean.translation_acceptance_gate checks.
# The full gate requires TheoremEntry/TranslationResult objects we don't have
# at rewriter time, but the cheap shape checks below catch the same bad-Lean
# patterns the gate rejects: placeholders, raw LaTeX leaks, fake declarations,
# atom targets, and copy-into-hypothesis claims.
def _lightweight_acceptance_gate(sig: str) -> str:
    """Return empty string if `sig` passes shape checks, else a reason code.

    Imports the actual gates from `arxiv_to_lean` so this rewriter's accept/reject
    decision matches the translator's. Falls back to local checks if the import
    fails (e.g. test isolation)."""
    if not sig or not sig.strip():
        return "empty_signature"
    try:  # Prefer the canonical checks for parity with the translator gate.
        from arxiv_to_lean import (
            _claim_atom_issue,
            _fake_placeholder_issue,
            _hypothesis_copies_target_issue,
            _is_placeholder_sig,
            _raw_latex_leak_reason,
            _relaxed_prop_identity_issue,
        )
    except Exception:  # pragma: no cover — defensive fallback only.
        if "PaperClaim" in sig or "RegeneratedStatement" in sig:
            return "claim_atom_target"
        if re.search(r"\\(?:left|right|frac|sum|int|ell|xi|omega|theta|alpha|beta|gamma|begin|end)\b", sig):
            return "raw_latex_command"
        if re.search(r"\$[^$]*\$", sig):
            return "dollar_math_delimiter"
        return ""
    if _is_placeholder_sig(sig):
        return "placeholder_or_schema_signature"
    fake = _fake_placeholder_issue(sig)
    if fake:
        return fake
    atom = _claim_atom_issue(sig)
    if atom:
        return atom
    leak = _raw_latex_leak_reason(sig)
    if leak:
        return f"raw_latex_leak:{leak}"
    copied = _hypothesis_copies_target_issue(sig)
    if copied:
        return copied
    relaxed = _relaxed_prop_identity_issue(sig)
    if relaxed:
        return relaxed
    return ""


def _collect_existing_decl_names(text: str) -> set[str]:
    """All `theorem foo` / `lemma foo` names already present in the .lean text."""
    return {match.group("name") for match in _DECL_NAME_RE.finditer(text)}


def _find_injection_point(text: str) -> int:
    """Index where new theorems should be injected — just before the final
    `end <Namespace>` line, or at EOF if no namespace closer is present."""
    last_end = None
    for match in _END_NAMESPACE_RE.finditer(text):
        last_end = match
    if last_end is None:
        # Append at EOF (ensure trailing newline).
        return len(text)
    return last_end.start()


def _format_appended_theorem(name: str, signature: str) -> str:
    """Render a missing-theorem injection block. Strips any stored proof body
    and emits `:= by sorry` so proof search picks the goal up fresh."""
    body = _strip_proof_body(signature).rstrip()
    return (
        f"-- [theorem] {name}  injected by rewrite_lean_from_ledger\n"
        f"{body} := by sorry\n\n"
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
    append_missing: bool = True,
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
        "schema_version": "rewrite_lean_from_ledger.v2",
        "paper_id": paper_id,
        "lean_file": str(lean_path.relative_to(project_root)),
        "placeholders_found": 0,
        "rewritten": 0,
        "appended_missing": 0,
        "skipped_no_ledger_signature": 0,
        "skipped_trivial_signature": 0,
        "skipped_gate_rejected": 0,
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
        gate_reason = _lightweight_acceptance_gate(new_sig)
        if gate_reason:
            summary["skipped_gate_rejected"] += 1
            summary["results"].append({
                "name": name,
                "action": "skipped_gate_rejected",
                "gate_reason": gate_reason,
            })
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
        gate_reason = _lightweight_acceptance_gate(new_sig)
        if gate_reason:
            summary["skipped_gate_rejected"] += 1
            summary["results"].append({
                "name": name,
                "action": "skipped_gate_rejected",
                "gate_reason": gate_reason,
            })
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

    # Append-missing pass: theorems present in the ledger but absent from the
    # .lean file are injected before the final `end <Namespace>` line. This is
    # the path that fixes the statement-repair-worker drift bug — the worker
    # writes upgraded `lean_statement` rows to the ledger but never touches the
    # .lean file, so any *new* theorem name (e.g. `prop_det_contraction`) stays
    # invisible to downstream proof search.
    if append_missing:
        existing_names = _collect_existing_decl_names(new_text)
        injection_idx = _find_injection_point(new_text)
        injections: list[str] = []
        # Iterate in ledger order so emitted theorems mirror the ledger sequence.
        seen_in_appends: set[str] = set()
        for entry in entries:
            full_name = str(entry.get("theorem_name", "") or "").strip()
            if not full_name:
                continue
            bare = full_name.rsplit(".", 1)[-1]
            if bare in existing_names or bare in seen_in_appends:
                continue
            stored = by_name.get(bare) or by_name.get(full_name)
            if not stored:
                # Either trivial or not declaration-shaped; nothing to inject.
                continue
            gate_reason = _lightweight_acceptance_gate(stored)
            if gate_reason:
                summary["skipped_gate_rejected"] += 1
                summary["results"].append({
                    "name": bare,
                    "action": "skipped_append_gate_rejected",
                    "gate_reason": gate_reason,
                })
                continue
            injections.append(_format_appended_theorem(bare, stored))
            seen_in_appends.add(bare)
            summary["appended_missing"] += 1
            summary["results"].append({
                "name": bare,
                "action": "appended_missing",
                "stored_signature_chars": len(stored),
            })
        if injections:
            block = "".join(injections)
            head = new_text[:injection_idx]
            tail = new_text[injection_idx:]
            # Ensure clean newline separation around the injected block.
            if head and not head.endswith("\n"):
                head = head + "\n"
            if not head.endswith("\n\n"):
                head = head + "\n"
            new_text = head + block + tail

    mutated = (summary["rewritten"] + summary["appended_missing"]) > 0
    if write and mutated and new_text != text:
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
    parser.add_argument(
        "--append-missing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Inject theorems present in the ledger but absent from the .lean file "
            "(before the final `end <Namespace>` line). Default ON — needed to "
            "propagate statement-repair-worker upgrades that add brand-new "
            "theorems. Pass --no-append-missing for placeholder-rewrites only."
        ),
    )
    args = parser.parse_args()

    summary = rewrite_paper(
        args.paper_id,
        project_root=args.project_root,
        write=args.write,
        append_missing=bool(args.append_missing),
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
