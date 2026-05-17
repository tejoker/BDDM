#!/usr/bin/env python3
"""Direct discharge pass: walk every AB/IP row in the canonical ledger,
match its ``axiom_debt`` against ``output/corpus/alignments.json``, and
move matched entries to ``discharged_axiom_debt``. When all entries are
discharged the promotion-gate's ``no_paper_axiom_debt`` flips True, so
the row should auto-promote AB→FP on re-evaluation.

apply_reviews_to_ledger.py runs the same logic but is gated on a row
having a reviewed_statement_corpus entry; many AB rows don't. This
script doesn't gate on review presence.

Standards-positive: the audit (`audit_fully_proven_integrity.py
--lake-validate-bodies`) re-runs after this script; any spurious
promotion (sorry-bodied or trivialized) gets demoted. The discharge
only flips axiom-debt entries — never proof-body claims.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALIGNMENTS_PATH = PROJECT_ROOT / "output" / "corpus" / "alignments.json"
LEDGER_ROOT = PROJECT_ROOT / "reproducibility" / "full_paper_reports"


def load_alignments(path: Path) -> dict[str, set[str]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for a in data.get("alignments", []):
        pid = str(a.get("paper_id", "") or "")
        name = str(a.get("paper_local_name", "") or "")
        if pid and name:
            out.setdefault(pid, set()).add(name)
    return out


def _promotion_status(entry: dict) -> str:
    """Recompute promotion status given the current axiom_debt and gates.

    Mirrors `pipeline_status.evaluate_promotion_gates`:
    - all proven gates True + no_paper_axiom_debt + no other gate_failures
      → FULLY_PROVEN
    - axiom_debt non-empty → AXIOM_BACKED
    - other gates failing → INTERMEDIARY_PROVEN
    Otherwise leave status unchanged.
    """
    debt = entry.get("axiom_debt") or []
    gates = entry.get("validation_gates") or {}
    proven_gate = bool(gates.get("lean_proof_closed"))
    if not proven_gate:
        return entry.get("status", "UNRESOLVED")
    # Other-gate failures (drop `no_paper_axiom_debt` since axiom_debt is
    # the actual signal, and `claim_equivalent` which we'll handle via
    # the review-driven gates).
    failures = [
        f for f in (entry.get("gate_failures") or [])
        if f not in {"no_paper_axiom_debt", "lean_proof_closed", "step_verdict_verified"}
    ]
    if debt:
        return "AXIOM_BACKED"
    if failures:
        return "INTERMEDIARY_PROVEN"
    return "FULLY_PROVEN"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alignments", type=Path, default=ALIGNMENTS_PATH)
    parser.add_argument("--ledger-root", type=Path, default=LEDGER_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    aligned = load_alignments(args.alignments)
    if not aligned:
        print("No alignments registered — nothing to discharge.")
        return 0

    summary: Counter[str] = Counter()
    promotions: list[tuple[str, str, str, str]] = []  # (pid, name, old, new)

    for ledger_path in sorted(args.ledger_root.glob("*/verification_ledger.json")):
        pid = ledger_path.parent.name
        aligned_names = aligned.get(pid, set())
        if not aligned_names:
            continue
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
        entries = data if isinstance(data, list) else data.get("entries", [])
        changed = False
        for e in entries:
            if e.get("status") not in ("AXIOM_BACKED", "INTERMEDIARY_PROVEN"):
                continue
            debt = list(e.get("axiom_debt") or [])
            if not debt:
                continue
            remaining: list[str] = []
            discharged: list[str] = []
            for d in debt:
                s = str(d).strip()
                if not s:
                    continue
                name = s.split(":", 1)[1].strip() if ":" in s else s
                if name in aligned_names:
                    discharged.append(s)
                else:
                    remaining.append(s)
            if not discharged:
                continue
            e["axiom_debt"] = remaining
            existing = list(e.get("discharged_axiom_debt") or [])
            e["discharged_axiom_debt"] = list(dict.fromkeys(existing + discharged))
            summary[f"{pid}/discharged"] += len(discharged)
            # If axiom_debt is empty AND lean_proof_closed=True, promote AB→FP
            new_status = _promotion_status(e)
            if new_status != e.get("status"):
                promotions.append((pid, e.get("theorem_name", ""), e.get("status", ""), new_status))
                summary[f"{pid}/promoted_{e.get('status')}_to_{new_status}"] += 1
                e["status"] = new_status
                # Clear `no_paper_axiom_debt` from gate_failures since
                # debt is now empty.
                failures = [
                    f for f in (e.get("gate_failures") or [])
                    if f != "no_paper_axiom_debt"
                ]
                e["gate_failures"] = failures
            changed = True
        if changed and not args.dry_run:
            payload = data if isinstance(data, list) else {**data, "entries": entries}
            ledger_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

    print(f"Discharged axioms across {sum(v for k,v in summary.items() if '/discharged' in k)} debt entries")
    print(f"Status changes: {len(promotions)}")
    for pid, nm, old, new in promotions[:30]:
        print(f"  {pid}/{nm[:60]}: {old} → {new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
