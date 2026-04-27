#!/usr/bin/env python3
"""Paper-agnostic theorem-level semantic fidelity audit.

Computes two closure views from the verification ledger:
1) operational_closure: status == FULLY_PROVEN
2) faithful_closure: FULLY_PROVEN + claim_equivalence + theorem_signature_stable
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from paper_closure_checklist import run_checklist


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Audit theorem-level semantic fidelity for one paper")
    p.add_argument("--paper-id", required=True, help="arXiv paper ID, e.g. 2304.09598")
    p.add_argument("--ledger-root", default="output/verification_ledgers")
    p.add_argument("--out-json", default="")
    return p


def _safe_id(paper_id: str) -> str:
    return paper_id.replace("/", "_").replace(":", "_")


def main() -> int:
    args = _build_parser().parse_args()
    payload = run_checklist(paper_id=args.paper_id, ledger_root=Path(args.ledger_root))
    reports = payload.get("theorem_reports", [])
    total = int(payload.get("total_theorems", 0) or 0)
    operational = int(payload.get("fully_proven", 0) or 0)

    faithful_count = 0
    changed_signature: list[dict[str, Any]] = []
    not_equiv: list[dict[str, Any]] = []
    for t in reports:
        checks = t.get("checklist", {}) if isinstance(t, dict) else {}
        status = str(t.get("status", "")).upper() if isinstance(t, dict) else ""
        stable = bool(checks.get("theorem_signature_stable", False))
        equiv = bool(checks.get("claim_equivalence", False))
        if status == "FULLY_PROVEN" and stable and equiv:
            faithful_count += 1
        if not stable:
            changed_signature.append(
                {
                    "theorem_name": str(t.get("theorem_name", "")),
                    "status": status,
                    "reason": str((t.get("reasons", {}) or {}).get("theorem_signature_stable", "")),
                }
            )
        if not equiv:
            not_equiv.append(
                {
                    "theorem_name": str(t.get("theorem_name", "")),
                    "status": status,
                    "reason": str((t.get("reasons", {}) or {}).get("claim_equivalence", "")),
                }
            )

    out = {
        "paper_id": args.paper_id,
        "total_theorems": total,
        "operational_fully_proven": operational,
        "operational_closure_rate": round(float(operational) / max(1, total), 4),
        "faithful_fully_proven": faithful_count,
        "faithful_closure_rate": round(float(faithful_count) / max(1, total), 4),
        "signature_changed_count": len(changed_signature),
        "claim_not_equivalent_count": len(not_equiv),
        "signature_changed": changed_signature,
        "claim_not_equivalent": not_equiv,
    }

    out_path = (
        Path(args.out_json)
        if args.out_json
        else Path("output/reports/full_paper") / f"{_safe_id(args.paper_id)}_semantic_fidelity_audit.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"ok": True, "out_json": str(out_path), **{k: out[k] for k in (
        "total_theorems",
        "operational_fully_proven",
        "operational_closure_rate",
        "faithful_fully_proven",
        "faithful_closure_rate",
    )}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

