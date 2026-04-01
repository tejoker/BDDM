#!/usr/bin/env python3
"""Compute quality gates and generate attribution audit sheets from ledgers.

Produces:
- JSON summary with pass/fail against configurable gates
- CSV manual audit sheet for attribution review

Example:
  python3 scripts/quality_gates_report.py
  python3 scripts/quality_gates_report.py --paper 2304.09598 --audit-sample-size 25
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Gates:
    min_translation_rate: float = 0.90
    min_proof_closure_rate: float = 0.60
    min_attribution_precision: float = 0.85
    min_schema_v2_ratio: float = 1.00


def _iter_ledger_files(ledger_dir: Path, paper: str) -> list[Path]:
    if paper:
        safe = paper.replace("/", "_").replace(":", "_")
        p = ledger_dir / f"{safe}.json"
        return [p] if p.exists() else []
    return sorted(ledger_dir.glob("*.json"))


def _load_ledger(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}, []

    if isinstance(raw, list):
        return {"schema_version": "legacy"}, [r for r in raw if isinstance(r, dict)]

    if isinstance(raw, dict):
        entries = raw.get("entries", [])
        meta = {k: v for k, v in raw.items() if k != "entries"}
        if isinstance(entries, list):
            return meta, [r for r in entries if isinstance(r, dict)]

    return {}, []


def _assumptions_fully_grounded(row: dict[str, Any]) -> bool:
    assumptions = row.get("assumptions", [])
    if not assumptions:
        return False
    for a in assumptions:
        if not isinstance(a, dict):
            return False
        g = str(a.get("grounding", "UNKNOWN"))
        if g in {"UNKNOWN", "UNGROUNDED", ""}:
            return False
    return True


def _translation_validated_heuristic(row: dict[str, Any]) -> bool:
    # Ledger rows do not always include explicit translation block; use conservative heuristic.
    stmt = str(row.get("lean_statement", ""))
    err = str(row.get("error_message", "")).lower()
    if not stmt.strip():
        return False
    if "translation failed" in stmt.lower():
        return False
    if any(tok in err for tok in ["unexpected token", "unknown identifier", "type mismatch", "elaborate"]):
        return False
    return True


def _safe_rate(num: int, den: int) -> float:
    return (num / den) if den else 0.0


def _build_audit_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for r in rows:
        status = str(r.get("status", "UNRESOLVED"))
        origin = str(r.get("failure_origin", "UNKNOWN"))
        # Highest priority for manual review: ambiguous attribution and possible-false claims.
        if origin in {"UNKNOWN", "POSSIBLY_FALSE_STATEMENT", "FORMALIZATION_ERROR", "PROOF_SEARCH_ERROR"}:
            candidates.append(
                {
                    "theorem_name": r.get("theorem_name", ""),
                    "status": status,
                    "failure_origin": origin,
                    "step_verdict": r.get("step_verdict", "INCOMPLETE"),
                    "first_failing_step": r.get("first_failing_step", -1),
                    "error_message": str(r.get("error_message", ""))[:300],
                    "proof_mode": r.get("proof_mode", ""),
                }
            )
    return candidates


def main() -> int:
    p = argparse.ArgumentParser(description="Quality gates and attribution audit report from ledgers")
    p.add_argument("--ledger-dir", default="output/verification_ledgers", help="Ledger directory")
    p.add_argument("--paper", default="", help="Optional paper id")
    p.add_argument("--out-dir", default="output/audit", help="Output audit directory")
    p.add_argument("--seed", type=int, default=42, help="Random seed for audit sampling")
    p.add_argument("--audit-sample-size", type=int, default=25, help="Rows in manual attribution audit sheet")

    p.add_argument("--min-translation-rate", type=float, default=0.90)
    p.add_argument("--min-proof-closure-rate", type=float, default=0.60)
    p.add_argument("--min-attribution-precision", type=float, default=0.85)
    p.add_argument("--min-schema-v2-ratio", type=float, default=1.00)

    args = p.parse_args()

    gates = Gates(
        min_translation_rate=args.min_translation_rate,
        min_proof_closure_rate=args.min_proof_closure_rate,
        min_attribution_precision=args.min_attribution_precision,
        min_schema_v2_ratio=args.min_schema_v2_ratio,
    )

    ledger_dir = Path(args.ledger_dir)
    if not ledger_dir.exists():
        print(f"[fail] ledger directory not found: {ledger_dir}")
        return 1

    files = _iter_ledger_files(ledger_dir, args.paper)
    if not files:
        print("[fail] no ledger files matched")
        return 1

    total_rows = 0
    translated_rows = 0
    proof_closed_rows = 0
    fully_grounded_rows = 0
    schema_v2_files = 0
    attribution_known_rows = 0
    audit_candidates: list[dict[str, Any]] = []

    for f in files:
        meta, rows = _load_ledger(f)
        if not rows:
            continue

        if str(meta.get("schema_version", "legacy")) != "legacy":
            schema_v2_files += 1

        for r in rows:
            total_rows += 1

            if _translation_validated_heuristic(r):
                translated_rows += 1

            status = str(r.get("status", "UNRESOLVED"))
            if status in {"FULLY_PROVEN", "INTERMEDIARY_PROVEN"}:
                proof_closed_rows += 1

            if _assumptions_fully_grounded(r):
                fully_grounded_rows += 1

            origin = str(r.get("failure_origin", "UNKNOWN"))
            if origin not in {"", "UNKNOWN"}:
                attribution_known_rows += 1

        audit_candidates.extend(_build_audit_candidates(rows))

    schema_v2_ratio = _safe_rate(schema_v2_files, len(files))
    translation_rate = _safe_rate(translated_rows, total_rows)
    proof_closure_rate = _safe_rate(proof_closed_rows, total_rows)
    fully_grounded_rate = _safe_rate(fully_grounded_rows, total_rows)
    attribution_precision_proxy = _safe_rate(attribution_known_rows, total_rows)

    gate_status = {
        "translation_rate": translation_rate >= gates.min_translation_rate,
        "proof_closure_rate": proof_closure_rate >= gates.min_proof_closure_rate,
        "attribution_precision_proxy": attribution_precision_proxy >= gates.min_attribution_precision,
        "schema_v2_ratio": schema_v2_ratio >= gates.min_schema_v2_ratio,
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ledger_dir": str(ledger_dir),
        "paper_filter": args.paper,
        "files": len(files),
        "rows": total_rows,
        "metrics": {
            "translation_rate_heuristic": translation_rate,
            "proof_closure_rate": proof_closure_rate,
            "fully_grounded_rate": fully_grounded_rate,
            "attribution_precision_proxy": attribution_precision_proxy,
            "schema_v2_ratio": schema_v2_ratio,
        },
        "gates": {
            "thresholds": {
                "min_translation_rate": gates.min_translation_rate,
                "min_proof_closure_rate": gates.min_proof_closure_rate,
                "min_attribution_precision": gates.min_attribution_precision,
                "min_schema_v2_ratio": gates.min_schema_v2_ratio,
            },
            "pass": gate_status,
            "all_pass": all(gate_status.values()),
        },
        "notes": [
            "translation_rate_heuristic is conservative because ledger rows may omit explicit translation metadata",
            "attribution_precision_proxy approximates labeling coverage, not human-validated precision",
        ],
    }

    summary_path = out_dir / "quality_gates_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    random.seed(args.seed)
    random.shuffle(audit_candidates)
    sample = audit_candidates[: max(0, args.audit_sample_size)]

    sheet_path = out_dir / "attribution_review_sheet.csv"
    with sheet_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "theorem_name",
                "status",
                "failure_origin",
                "step_verdict",
                "first_failing_step",
                "proof_mode",
                "error_message",
                "human_label",
                "human_notes",
                "is_correct",
            ],
        )
        writer.writeheader()
        for row in sample:
            writer.writerow(
                {
                    **row,
                    "human_label": "",
                    "human_notes": "",
                    "is_correct": "",
                }
            )

    print("[ok] Quality gates report generated")
    print(f"[info] rows={total_rows} files={len(files)}")
    print(
        "[info] metrics="
        f"translation={translation_rate:.3f} "
        f"proof_closure={proof_closure_rate:.3f} "
        f"fully_grounded={fully_grounded_rate:.3f} "
        f"attribution_proxy={attribution_precision_proxy:.3f} "
        f"schema_v2_ratio={schema_v2_ratio:.3f}"
    )
    print(f"[info] gates_all_pass={all(gate_status.values())}")
    print(f"[info] wrote {summary_path}")
    print(f"[info] wrote {sheet_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
