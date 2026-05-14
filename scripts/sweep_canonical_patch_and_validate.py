#!/usr/bin/env python3
"""Post-sweep helper: patch each ledger's closed proofs back into the
corresponding `output/<paper>.lean`, then `lake env lean` validate the file.
Rows whose proof_text fails to compile are reverted to `sorry` in the .lean
AND demoted (status -> UNRESOLVED, validation_gates.lean_proof_closed=False)
in the ephemeral ledger. This is the honest counterpart to the audit:
the audit looks only at .lean body; this helper ensures the body actually
compiles before the audit runs.

Usage:
  python3 scripts/sweep_canonical_patch_and_validate.py [--paper PID] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
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


def _theorem_body_re(name: str) -> re.Pattern[str]:
    return re.compile(
        r"((?:theorem|lemma|noncomputable\s+theorem|noncomputable\s+lemma|private\s+theorem|private\s+lemma)\s+"
        + re.escape(name)
        + r"\b[\s\S]*?:=\s*by\s*\n)\s*sorry\b",
    )


def _body_is_sorry_for(lean_text: str, name: str) -> bool:
    pat = _theorem_body_re(name)
    return bool(pat.search(lean_text))


def _patch_in_place(lean_file: Path, name: str, proof_text: str) -> bool:
    text = lean_file.read_text(encoding="utf-8")
    pat = _theorem_body_re(name)
    new_text = pat.sub(lambda m: m.group(1) + (proof_text.rstrip() or "  sorry") + "\n", text, count=1)
    if new_text == text:
        return False
    lean_file.write_text(new_text, encoding="utf-8")
    return True


def _revert_to_sorry(lean_file: Path, name: str) -> bool:
    text = lean_file.read_text(encoding="utf-8")
    # Find the patched proof and revert. We can't easily know exactly what was
    # patched, so we replace the whole `:= by\n  <anything>` for this theorem
    # with `:= by\n  sorry`. This works because we just patched a moment ago.
    head_pat = re.compile(
        r"((?:theorem|lemma|noncomputable\s+theorem|noncomputable\s+lemma|private\s+theorem|private\s+lemma)\s+"
        + re.escape(name)
        + r"\b[\s\S]*?:=\s*by\s*\n)([^\n]*(?:\n[ \t][^\n]*)*)",
    )
    m = head_pat.search(text)
    if not m:
        return False
    new_text = text[: m.start()] + m.group(1) + "  sorry\n" + text[m.end() :]
    if new_text == text:
        return False
    lean_file.write_text(new_text, encoding="utf-8")
    return True


def _lake_validate(lean_file: Path, timeout_s: int = 240) -> tuple[bool, str]:
    """Run `lake env lean` on lean_file. Standards-positive: returncode==0
    AND no `declaration uses 'sorry'` warning on the patched theorem."""
    try:
        proc = subprocess.run(
            ["lake", "env", "lean", str(lean_file)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return False, f"lake_timeout:{timeout_s}s"
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0:
        return False, out[-1500:]
    return True, out  # caller inspects warnings


def _theorem_line_in_file(lean_file: Path, name: str) -> int | None:
    """Return the 1-indexed line number of `theorem <name>` in the file."""
    text = lean_file.read_text(encoding="utf-8")
    pat = re.compile(r"^\s*(?:noncomputable\s+|private\s+)?(?:theorem|lemma)\s+" + re.escape(name) + r"\b")
    for i, ln in enumerate(text.splitlines(), start=1):
        if pat.match(ln):
            return i
    return None


def _file_compiles_clean_for_theorem(lake_output: str, theorem_line: int) -> bool:
    """Inspect lake_output for a `declaration uses 'sorry'` warning at the
    given theorem_line. True if NO such warning is attached to this line."""
    if not theorem_line:
        return True
    needle = f":{theorem_line}:"
    for raw in lake_output.splitlines():
        if needle in raw and "declaration uses" in raw and "sorry" in raw:
            return False
    return True


def _load_ledger(pid: str) -> tuple[Path, list[dict]]:
    p = PROJECT_ROOT / "output" / "verification_ledgers" / f"{pid}.json"
    data = json.loads(p.read_text())
    entries = data if isinstance(data, list) else data.get("entries", [])
    return p, entries


def _save_ledger(p: Path, entries: list[dict]) -> None:
    p.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--paper", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--per-paper-timeout", type=int, default=240)
    args = parser.parse_args()

    papers = args.paper or CANONICAL
    summary: dict[str, dict] = {}
    for pid in papers:
        lean_file = PROJECT_ROOT / "output" / f"{pid}.lean"
        if not lean_file.exists():
            print(f"[skip] {pid}: no {lean_file}")
            continue
        led_path, entries = _load_ledger(pid)
        # Identify candidate rows: status in proof-claiming set, proof_text
        # non-empty/non-trivial, .lean body still sorry.
        candidates = []
        for e in entries:
            status = e.get("status")
            if status not in ("FULLY_PROVEN", "INTERMEDIARY_PROVEN", "AXIOM_BACKED"):
                continue
            proof_text = (e.get("proof_text") or "").strip()
            if not proof_text or proof_text == "sorry":
                continue
            name = e.get("theorem_name", "")
            short = name.rsplit(".", 1)[-1] if name else ""
            text = lean_file.read_text(encoding="utf-8")
            target_name = None
            if short and _body_is_sorry_for(text, short):
                target_name = short
            elif name and _body_is_sorry_for(text, name):
                target_name = name
            if target_name is None:
                continue
            candidates.append((target_name, proof_text, e))
        print(f"[{pid}] candidate patches: {len(candidates)}")
        if not candidates:
            summary[pid] = {"candidates": 0, "patched": 0, "validated": 0, "reverted": 0}
            continue
        if args.dry_run:
            for tn, pt, _ in candidates:
                print(f"  would patch {tn} <- {pt[:60]!r}")
            summary[pid] = {"candidates": len(candidates), "patched": 0, "validated": 0, "reverted": 0}
            continue

        # Strategy: do not require the whole file to compile clean (it
        # typically has many other sorry-bodied theorems). For each candidate
        # we patch, run `lake env lean` on the whole file, then verify that
        # the *patched theorem's line* does NOT emit a "declaration uses
        # `sorry`" warning. If so, keep; else revert.
        kept: list[tuple[str, str, dict]] = []
        reverted: list[str] = []
        for tn, pt, ent in candidates:
            if not _patch_in_place(lean_file, tn, pt):
                continue
            line_no = _theorem_line_in_file(lean_file, tn)
            ok_rc, lake_out = _lake_validate(lean_file, timeout_s=args.per_paper_timeout)
            if not ok_rc:
                # Hard elaboration failure — revert.
                _revert_to_sorry(lean_file, tn)
                reverted.append(tn)
                continue
            if not _file_compiles_clean_for_theorem(lake_out, line_no or 0):
                # Patched body emits sorry warning (e.g. `apply?` term).
                _revert_to_sorry(lean_file, tn)
                reverted.append(tn)
                continue
            kept.append((tn, pt, ent))
        patched_names = [k[0] for k in kept] + reverted
        # Demote reverted rows.
        reverted_set = set(reverted)
        for tn, _pt, ent in candidates:
            if tn in reverted_set:
                ent["status"] = "UNRESOLVED"
                vg = ent.get("validation_gates") or {}
                if isinstance(vg, dict):
                    vg["lean_proof_closed"] = False
                ent["proof_text"] = ""
                fails = list(ent.get("gate_failures") or [])
                if "lean_proof_closed" not in fails:
                    fails.append("lean_proof_closed")
                ent["gate_failures"] = fails
        if reverted:
            _save_ledger(led_path, entries)
        print(f"[{pid}] kept={len(kept)} reverted={len(reverted)}")
        summary[pid] = {"candidates": len(candidates), "patched": len(patched_names), "validated": len(kept), "reverted": len(reverted)}

    print("\n=== Summary ===")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
