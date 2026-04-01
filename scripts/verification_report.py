#!/usr/bin/env python3
"""Summarize verification ledgers produced by arxiv_to_lean.

Examples:
  python3 scripts/verification_report.py
  python3 scripts/verification_report.py --paper 2304.09598
  python3 scripts/verification_report.py --dir output/verification_ledgers --show-theorems
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def _load_doc(path: Path) -> tuple[dict, list[dict]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}, []

    if isinstance(raw, list):
        return {}, [r for r in raw if isinstance(r, dict)]

    if isinstance(raw, dict):
        rows = raw.get("entries", [])
        if isinstance(rows, list):
            meta = {k: v for k, v in raw.items() if k != "entries"}
            return meta, [r for r in rows if isinstance(r, dict)]

    return {}, []


def _iter_ledger_files(ledger_dir: Path, paper: str) -> list[Path]:
    if paper:
        safe = paper.replace("/", "_").replace(":", "_")
        p = ledger_dir / f"{safe}.json"
        return [p] if p.exists() else []
    return sorted(ledger_dir.glob("*.json"))


def _print_file_summary(path: Path, meta: dict, rows: list[dict], show_theorems: bool) -> None:
    status_counts = Counter(r.get("status", "") for r in rows)
    step_counts = Counter(r.get("step_verdict", "INCOMPLETE") for r in rows)
    origin_counts = Counter(r.get("failure_origin", "UNKNOWN") for r in rows)
    trust_counts = Counter(r.get("trust_class", "TRUST_PLACEHOLDER") for r in rows)
    promotion_ready = sum(1 for r in rows if bool(r.get("promotion_gate_passed", False)))
    grounding_counts = Counter(
        a.get("grounding", "UNKNOWN")
        for r in rows
        for a in r.get("assumptions", [])
        if isinstance(a, dict)
    )
    assumption_trust_counts = Counter(
        a.get("trust_class", "TRUST_PLACEHOLDER")
        for r in rows
        for a in r.get("assumptions", [])
        if isinstance(a, dict)
    )

    print(f"\n{path}")
    if meta:
        schema = meta.get("schema_version", "legacy")
        generated = meta.get("generated_at", "?")
        commit = str(meta.get("pipeline_commit", "?"))[:12]
        print(f"  schema={schema} generated_at={generated} commit={commit}")
    print(f"  entries={len(rows)}")
    print(f"  status={dict(status_counts)}")
    print(f"  step_verdict={dict(step_counts)}")
    print(f"  failure_origin={dict(origin_counts)}")
    print(f"  trust_class={dict(trust_counts)}")
    print(f"  promotion_ready={promotion_ready}/{len(rows)}")
    print(f"  assumption_grounding={dict(grounding_counts)}")
    print(f"  assumption_trust={dict(assumption_trust_counts)}")

    if show_theorems:
        for r in rows:
            print(
                "  - "
                f"{r.get('theorem_name','?')}: "
                f"status={r.get('status','?')} "
                f"step={r.get('step_verdict','INCOMPLETE')} "
                f"origin={r.get('failure_origin','UNKNOWN')} "
                f"trust={r.get('trust_class','TRUST_PLACEHOLDER')} "
                f"promotion={bool(r.get('promotion_gate_passed', False))} "
                f"assumptions={len(r.get('assumptions', []))} "
                f"first_failing_step={r.get('first_failing_step', -1)}"
            )


def main() -> int:
    p = argparse.ArgumentParser(description="Summarize arXiv verification ledgers")
    p.add_argument("--dir", default="output/verification_ledgers", help="Ledger directory")
    p.add_argument("--paper", default="", help="Single paper id to inspect (e.g. 2304.09598)")
    p.add_argument("--show-theorems", action="store_true", help="Print theorem-level rows")
    args = p.parse_args()

    ledger_dir = Path(args.dir)
    if not ledger_dir.exists():
        print(f"[fail] ledger directory not found: {ledger_dir}")
        return 1

    files = _iter_ledger_files(ledger_dir, args.paper)
    if not files:
        print("[fail] no ledger files matched")
        return 1

    total_status = Counter()
    total_steps = Counter()
    total_origins = Counter()
    total_trust = Counter()
    total_grounding = Counter()
    total_assumption_trust = Counter()
    total_promotion_ready = 0
    total_rows = 0

    for f in files:
        meta, rows = _load_doc(f)
        _print_file_summary(f, meta, rows, args.show_theorems)

        total_rows += len(rows)
        total_status.update(r.get("status", "") for r in rows)
        total_steps.update(r.get("step_verdict", "INCOMPLETE") for r in rows)
        total_origins.update(r.get("failure_origin", "UNKNOWN") for r in rows)
        total_trust.update(r.get("trust_class", "TRUST_PLACEHOLDER") for r in rows)
        total_promotion_ready += sum(1 for r in rows if bool(r.get("promotion_gate_passed", False)))
        total_grounding.update(
            a.get("grounding", "UNKNOWN")
            for r in rows
            for a in r.get("assumptions", [])
            if isinstance(a, dict)
        )
        total_assumption_trust.update(
            a.get("trust_class", "TRUST_PLACEHOLDER")
            for r in rows
            for a in r.get("assumptions", [])
            if isinstance(a, dict)
        )

    if len(files) > 1:
        print("\nTOTAL")
        print(f"  files={len(files)}")
        print(f"  entries={total_rows}")
        print(f"  status={dict(total_status)}")
        print(f"  step_verdict={dict(total_steps)}")
        print(f"  failure_origin={dict(total_origins)}")
        print(f"  trust_class={dict(total_trust)}")
        print(f"  promotion_ready={total_promotion_ready}/{total_rows}")
        print(f"  assumption_grounding={dict(total_grounding)}")
        print(f"  assumption_trust={dict(total_assumption_trust)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
