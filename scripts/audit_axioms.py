#!/usr/bin/env python3
"""Audit paper-local axiom dependencies of every theorem in the corpus.

Companion to `Desol/AxiomBudget.lean`. Generates a release-readiness report
by walking each ledger row's `axiom_debt` field (already populated by
`pipeline_status.evaluate_promotion_gates`).

Output schema (matches what we'd later upstream as a Mathlib-blessed
artifact):

    {
      "schema_version": "axiom_budget_audit.v1",
      "papers": {
        "<arxiv-id>": {
          "rows": <int>,
          "release_eligible": <int>,           # zero paper-local debt
          "axiom_backed": <int>,                # only paper-local debt
          "intermediary": <int>,                # other gate failures too
          "by_axiom_kind": {
            "paper_definition_stub:*": <count>,
            "paper_symbol:*": <count>,
            "paper_local_lemma:*": <count>,
          }
        },
        ...
      },
      "totals": {...}
    }

Usage:
    python3 scripts/audit_axioms.py
        [--ledger-dir output/verification_ledgers]
        [--out output/corpus/axiom_budget_audit.json]
        [--paper-id <only this paper>]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

DEFAULT_LEDGER_DIR = Path("output/verification_ledgers")
DEFAULT_OUT = Path("output/corpus/axiom_budget_audit.json")
DEFAULT_ALIGNMENTS = Path("output/corpus/alignments.json")
_CANONICAL_LEDGER_RE = re.compile(r"^\d{4}\.\d{4,6}(?:v\d+)?$")


def _load_alignments(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Load registered paper-local↔Mathlib alignments. Returns map keyed by
    (paper_id, paper_local_name) for lookup against axiom_debt entries."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for a in data.get("alignments", []):
        if not isinstance(a, dict):
            continue
        pid = str(a.get("paper_id", "") or "")
        name = str(a.get("paper_local_name", "") or "")
        if pid and name:
            out[(pid, name)] = a
    return out


def _debt_aligned(paper_id: str, debt: str, alignments: dict[tuple[str, str], dict[str, Any]]) -> bool:
    """True iff this axiom-debt entry corresponds to a registered alignment.

    Debt entries look like `paper_definition_stub:Multisegment` or
    `paper_symbol:dual` or `paper_local_lemma:foo`. We extract the last
    name and check against the alignments map keyed by (paper_id, name)."""
    if ":" not in debt:
        return False
    _, name = debt.split(":", 1)
    name = name.strip()
    return (paper_id, name) in alignments


def _canonical_ledgers(ledger_dir: Path) -> list[Path]:
    return sorted(p for p in ledger_dir.glob("*.json") if _CANONICAL_LEDGER_RE.match(p.stem))


def _load_entries(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("entries", [])


def _classify_row(
    row: dict[str, Any],
    paper_id: str = "",
    alignments: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> str:
    """Return one of `release_eligible`, `axiom_backed`, `intermediary`,
    `unresolved`, `flawed`, `translation_limited`. The first three are all
    proof-closed but vary in axiom debt and gate status.

    When `alignments` is provided, axiom-debt entries that correspond to a
    registered alignment are treated as discharged (paper-local stub IS the
    Mathlib counterpart). A row whose debt is FULLY discharged and that
    has no other gate failures is upgraded to `release_eligible` even if
    its raw status is still AXIOM_BACKED — that's the alignment-aware demotion.
    """
    alignments = alignments or {}
    status = str(row.get("status", "") or "").upper()
    if status in ("UNRESOLVED", "FLAWED", "TRANSLATION_LIMITED"):
        return status.lower()
    debt = [str(x) for x in (row.get("axiom_debt") or []) if str(x).strip()]
    paper_local = [d for d in debt if d.startswith(("paper_definition_stub:", "paper_symbol:", "paper_local_lemma:"))]
    # Filter out aligned debt — these entries have a Mathlib counterpart proof.
    paper_local_unaligned = [d for d in paper_local if not _debt_aligned(paper_id, d, alignments)]
    other_debt = [d for d in debt if d not in paper_local]
    gate_failures = [str(x) for x in (row.get("gate_failures") or []) if str(x).strip()]
    nontrivial_gates = [g for g in gate_failures if g != "no_paper_axiom_debt"]
    # Promotion: zero remaining debt + no other gate failures + proof closed
    if not paper_local_unaligned and not other_debt and not nontrivial_gates and status in ("FULLY_PROVEN", "AXIOM_BACKED"):
        return "release_eligible"
    if not debt and not nontrivial_gates and status == "FULLY_PROVEN":
        return "release_eligible"
    if paper_local and not other_debt and not nontrivial_gates:
        return "axiom_backed"
    if status in ("FULLY_PROVEN", "AXIOM_BACKED", "INTERMEDIARY_PROVEN"):
        return "intermediary"
    return status.lower()


def _kind_of_debt(debt: str) -> str:
    """Categorise an axiom-debt entry by its prefix."""
    for prefix in ("paper_definition_stub:", "paper_symbol:", "paper_local_lemma:"):
        if debt.startswith(prefix):
            return prefix.rstrip(":")
    if ":" in debt:
        return debt.split(":", 1)[0]
    return debt or "bare"


def audit_corpus(
    ledger_dir: Path,
    paper_id_filter: str = "",
    alignments_path: Path = DEFAULT_ALIGNMENTS,
) -> dict[str, Any]:
    alignments = _load_alignments(alignments_path)
    papers: dict[str, dict[str, Any]] = {}
    grand_kinds: Counter[str] = Counter()
    grand_classes: Counter[str] = Counter()
    grand_rows = 0
    grand_aligned_demoted = 0  # count of rows promoted via alignment to release_eligible
    for path in _canonical_ledgers(ledger_dir):
        if paper_id_filter and path.stem != paper_id_filter:
            continue
        paper_id = path.stem
        entries = _load_entries(path)
        kinds: Counter[str] = Counter()
        classes: Counter[str] = Counter()
        aligned_demoted = 0
        for r in entries:
            cls_pre = _classify_row(r, paper_id=paper_id, alignments={})
            cls = _classify_row(r, paper_id=paper_id, alignments=alignments)
            if cls == "release_eligible" and cls_pre != "release_eligible":
                aligned_demoted += 1
            classes[cls] += 1
            for d in (r.get("axiom_debt") or []):
                if str(d).strip():
                    kinds[_kind_of_debt(str(d))] += 1
        papers[paper_id] = {
            "rows": len(entries),
            "classes": dict(classes),
            "by_axiom_kind": dict(kinds),
            "alignment_promotions": aligned_demoted,
            "ledger_path": str(path),
        }
        grand_classes += classes
        grand_kinds += kinds
        grand_rows += len(entries)
        grand_aligned_demoted += aligned_demoted
    return {
        "schema_version": "axiom_budget_audit.v2",
        "alignments_loaded": len(alignments),
        "papers": papers,
        "totals": {
            "rows": grand_rows,
            "classes": dict(grand_classes),
            "by_axiom_kind": dict(grand_kinds),
            "alignment_promotions": grand_aligned_demoted,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Paper-local axiom-budget audit across the corpus")
    parser.add_argument("--ledger-dir", type=Path, default=DEFAULT_LEDGER_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--paper-id", default="")
    args = parser.parse_args()

    audit = audit_corpus(args.ledger_dir, args.paper_id)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(audit, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
