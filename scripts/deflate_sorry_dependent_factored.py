#!/usr/bin/env python3
"""Post-sweep deflation pass: revert any ledger row whose proof body
references a `__factored_aux`-suffixed lemma that is STILL sorry-bodied
in the source file.

Lean does not propagate the `declaration uses 'sorry'` warning across
`apply foo` calls — so a proof body that depends on a sorry-bodied aux
elaborates cleanly even though it's not a real closure. The integrity
audit (which checks `body == sorry` only) also misses this. This pass
adds a transitive sorry check at the application level.

For each canonical paper:
  1. Read `output/<paper>.lean` and find every `theorem <name>__factored_aux ... := by sorry` declaration. Collect the sorry-bodied aux names.
  2. Read `output/verification_ledgers/<paper>.json`. For each FP/AB/IP row whose `proof_text` references one of those names, revert it: set status='UNRESOLVED', drop the lean_proof_closed flag, restore the body in the file to `:= by sorry`.
  3. Also remove ALL `__factored_aux` lemmas from the file (they're now orphans).

Returns 0 always. Prints per-paper deflation counts.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

CANONICAL = [
    "2012.09271",
    "2304.09598",
    "2401.04567",
    "2604.21314",
    "2604.21583",
    "2604.21616",
    "2604.21821",
    "2604.21884",
]


_FACTORED_NAME_RX = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*__factored_aux)\b")


def _scan_sorry_bodied_factored_aux(lean_text: str) -> set[str]:
    """Return the set of `theorem <name>__factored_aux ... := by sorry` names
    in `lean_text`. A theorem is sorry-bodied if its body (after `:= by`) is
    purely `sorry` (with surrounding whitespace).

    The body check requires the FIRST non-whitespace token after `:= by`
    to be exactly `sorry` (a real proof body starting with `exact`,
    `intro`, `apply` etc. is NOT sorry-bodied even if it textually
    contains `sorry` deeper, e.g. in a comment).
    """
    out: set[str] = set()
    # First, find every theorem head with the suffix.
    head_pat = re.compile(
        r"(?:noncomputable\s+|private\s+)?(?:theorem|lemma)\s+"
        r"([A-Za-z_][A-Za-z0-9_']*__factored_aux)\b",
    )
    matches = list(head_pat.finditer(lean_text))
    for i, m in enumerate(matches):
        name = m.group(1)
        start = m.start()
        # Walk to the next top-level head (or EOF).
        end = matches[i + 1].start() if i + 1 < len(matches) else len(lean_text)
        block = lean_text[start:end]
        # Strip everything up to (and including) `:= by`.
        body_m = re.search(r":=\s*by\b([\s\S]*)$", block)
        if not body_m:
            continue
        body = body_m.group(1).lstrip()
        # `sorry` must be the FIRST tactic (followed by whitespace / newline
        # / end / next decl boundary). Equivalently, the body starts with
        # `sorry` as a whole token.
        if re.match(r"sorry(?![A-Za-z0-9_'])", body):
            out.add(name)
    return out


def _references_sorry_aux(proof_text: str, sorry_aux: set[str]) -> set[str]:
    """Return the subset of `sorry_aux` referenced by `proof_text`."""
    if not proof_text or not sorry_aux:
        return set()
    found: set[str] = set()
    for nm in _FACTORED_NAME_RX.findall(proof_text):
        if nm in sorry_aux:
            found.add(nm)
    return found


def _revert_parent_in_file(lean_text: str, parent_name: str) -> tuple[str, bool]:
    """Replace the parent's proof body with `:= by sorry`. Returns
    (new_text, modified)."""
    short = parent_name.rsplit(".", 1)[-1]
    pat = re.compile(
        r"((?:noncomputable\s+|private\s+)?(?:theorem|lemma)\s+"
        + re.escape(short)
        + r"\b[\s\S]*?:=\s*by[ \t]*\n)([\s\S]*?)(?=\n(?:noncomputable\s+|private\s+)?(?:theorem|lemma|def|abbrev|axiom|end|namespace)\b|\Z)",
    )
    m = pat.search(lean_text)
    if not m:
        # Try single-line form.
        pat2 = re.compile(
            r"((?:noncomputable\s+|private\s+)?(?:theorem|lemma)\s+"
            + re.escape(short)
            + r"\b[\s\S]*?:=\s*by\s+)([^\n]+)",
        )
        m2 = pat2.search(lean_text)
        if not m2:
            return lean_text, False
        new = lean_text[:m2.start()] + m2.group(1) + "sorry" + lean_text[m2.end():]
        return new, True
    new = lean_text[: m.start()] + m.group(1) + "  sorry\n" + lean_text[m.end():]
    return new, True


def _strip_all_factored_aux(lean_text: str) -> tuple[str, int]:
    """Remove every `theorem <name>__factored_aux ... ...` block from the
    file. Returns (new_text, count_removed)."""
    pat = re.compile(
        r"(?:noncomputable\s+|private\s+)?(?:theorem|lemma)\s+"
        r"[A-Za-z_][A-Za-z0-9_']*__factored_aux\b"
        r"[\s\S]*?(?=\n(?:noncomputable\s+|private\s+)?(?:theorem|lemma|def|abbrev|axiom|end|namespace)\b|\Z)",
    )
    new = lean_text
    count = 0
    while True:
        m = pat.search(new)
        if not m:
            break
        new = new[: m.start()] + new[m.end():]
        count += 1
    # Tidy multiple blank lines.
    new = re.sub(r"\n{3,}", "\n\n", new)
    return new, count


def _revert_ledger_entry(entry: dict) -> None:
    """Mutate an entry to UNRESOLVED state, drop closure markers."""
    entry["status"] = "UNRESOLVED"
    entry["proof_text"] = ""
    entry["proof_method"] = ""
    entry["step_verdict"] = "PENDING"
    entry["promotion_gate_passed"] = False
    gates = entry.get("validation_gates") or {}
    if isinstance(gates, dict):
        gates["lean_proof_closed"] = False
        gates["step_verdict_verified"] = False
        entry["validation_gates"] = gates
    fails = list(entry.get("gate_failures") or [])
    if "lean_proof_closed" not in fails:
        fails.append("lean_proof_closed")
    entry["gate_failures"] = fails
    # Strip the closure markers we added.
    entry.pop("leanstral_whole_proof", None)
    entry.pop("factored_aux", None)
    notes = [str(x) for x in (entry.get("claim_equivalence_notes") or [])]
    notes.append("deflated_sorry_dependent_factored_aux")
    entry["claim_equivalence_notes"] = list(dict.fromkeys(notes))


def deflate_paper(paper_id: str, *, write: bool = True, project_root: Path = PROJECT_ROOT) -> dict:
    """Identify and (optionally) deflate ledger rows whose proof_text
    references a sorry-bodied `__factored_aux` lemma in the file.

    Also reverts any parent that references a `__factored_aux` aux that
    will be REMOVED (i.e. would become a dangling reference). The file
    cleanup strips:
      - every sorry-bodied `__factored_aux` declaration,
      - every `__factored_aux` whose only known dependents (in the file)
        are now reverted to sorry — they're orphans.

    For simplicity and safety we strip ALL `__factored_aux` declarations
    in this pass (whether closed or sorry), and revert any parent that
    references any of them. This is conservative but ensures the file is
    self-consistent after deflation. The rationale: aux lemmas were
    pipeline-generated content with no curated review, and the parent's
    "closure" claim was made WITHOUT a transitive sorry-check; in the
    absence of that check, neither aux closure nor parent closure is
    trustworthy.
    """
    lean_file = project_root / "output" / f"{paper_id}.lean"
    led_path = project_root / "output" / "verification_ledgers" / f"{paper_id}.json"
    summary = {
        "paper_id": paper_id,
        "sorry_factored_aux": 0,
        "deflated_rows": 0,
        "removed_aux_decls": 0,
        "deflated_names": [],
    }
    if not lean_file.exists() or not led_path.exists():
        summary["error"] = "missing_file"
        return summary
    lean_text = lean_file.read_text(encoding="utf-8")
    sorry_aux = _scan_sorry_bodied_factored_aux(lean_text)
    summary["sorry_factored_aux"] = len(sorry_aux)
    # Identify ALL `__factored_aux` names in the file (closed or sorry) so
    # we can deflate any parent that references any of them.
    all_aux_names = set(_FACTORED_NAME_RX.findall(lean_text))
    data = json.loads(led_path.read_text(encoding="utf-8"))
    entries = data if isinstance(data, list) else data.get("entries", [])
    for e in entries:
        status = e.get("status")
        if status not in ("FULLY_PROVEN", "AXIOM_BACKED", "INTERMEDIARY_PROVEN"):
            continue
        proof_text = str(e.get("proof_text") or "")
        # A parent is deflated iff it references ANY `__factored_aux`
        # (since the aux will be stripped — its closure is unverified).
        refs = _references_sorry_aux(proof_text, all_aux_names)
        if not refs:
            continue
        summary["deflated_rows"] += 1
        summary["deflated_names"].append(str(e.get("theorem_name") or ""))
        if write:
            _revert_ledger_entry(e)
            new_text, modified = _revert_parent_in_file(lean_text, e.get("theorem_name") or "")
            if modified:
                lean_text = new_text
    # Strip all factored_aux declarations.
    new_text, removed = _strip_all_factored_aux(lean_text)
    summary["removed_aux_decls"] = removed
    if write and (new_text != lean_text or summary["deflated_rows"] > 0 or removed > 0):
        lean_file.write_text(new_text, encoding="utf-8")
        payload = data if isinstance(data, list) else {**data, "entries": entries}
        led_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--paper", action="append", default=[])
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()
    papers = args.paper or CANONICAL
    total_deflated = 0
    total_aux = 0
    for pid in papers:
        s = deflate_paper(pid, write=not args.no_write)
        print(f"[{pid}] sorry_factored_aux={s['sorry_factored_aux']} "
              f"deflated_rows={s['deflated_rows']} "
              f"removed_aux_decls={s['removed_aux_decls']} "
              f"deflated={s['deflated_names']}")
        total_deflated += s["deflated_rows"]
        total_aux += s["removed_aux_decls"]
    print(f"\n=== TOTAL: deflated={total_deflated} removed_aux={total_aux} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
