#!/usr/bin/env python3
"""Build repair flywheel dataset from ledgers + bridge failure artifacts."""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any


def _iter_ledger_rows(ledger_root: Path):
    for p in sorted(ledger_root.glob("*.json")):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = raw.get("entries", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
        if not isinstance(rows, list):
            continue
        for r in rows:
            if isinstance(r, dict):
                yield p.stem, r


def _normalize_error(msg: str) -> str:
    txt = (msg or "").strip().lower()
    txt = re.sub(r"\s+", " ", txt)
    txt = re.sub(r"\b\d+\b", "<n>", txt)
    return txt[:320]


def _failure_pattern(row: dict[str, Any]) -> str:
    status = str(row.get("status", "")).upper()
    if status == "FULLY_PROVEN":
        return "success"
    eq = str(row.get("claim_equivalence_verdict", "unclear")).lower()
    if eq in {"stronger", "weaker"}:
        return f"semantic_{eq}"
    gates = row.get("gate_failures", [])
    if isinstance(gates, list) and gates:
        if "claim_equivalent" in gates:
            return "semantic_unclear"
        if "assumptions_grounded" in gates:
            return "grounding_missing"
        if "lean_proof_closed" in gates:
            return "proof_not_closed"
    err = _normalize_error(str(row.get("error_message", "")))
    if "unknown identifier" in err:
        return "lean_unknown_identifier"
    if "type mismatch" in err:
        return "lean_type_mismatch"
    if "unsolved goals" in err:
        return "lean_unsolved_goals"
    if "timeout" in err:
        return "timeout"
    return "other_failure"


def _iter_bridge_failure_artifacts(root: Path):
    if not root.exists():
        return
    for p in sorted(root.glob("*.jsonl")):
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                raw = json.loads(ln)
            except Exception:
                continue
            if isinstance(raw, dict):
                yield raw


def build_dataset(
    *,
    ledger_root: Path,
    bridge_failures_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pattern_counter: Counter[str] = Counter()
    by_paper: Counter[str] = Counter()

    for paper_id, row in _iter_ledger_rows(ledger_root):
        theorem = str(row.get("theorem_name", "")).strip()
        if not theorem:
            continue
        pattern = _failure_pattern(row)
        item = {
            "source": "ledger",
            "paper_id": paper_id,
            "theorem_name": theorem,
            "status": str(row.get("status", "")),
            "failure_pattern": pattern,
            "claim_equivalence_verdict": str(row.get("claim_equivalence_verdict", "unclear")),
            "gate_failures": row.get("gate_failures", []),
            "error_message": _normalize_error(str(row.get("error_message", ""))),
            "rounds_used": int(row.get("rounds_used", 0) or 0),
            "proof_mode": str(row.get("proof_mode", "")),
            "timestamp_unix": int(time.time()),
        }
        rows.append(item)
        pattern_counter[pattern] += 1
        by_paper[paper_id] += 1

    for art in _iter_bridge_failure_artifacts(bridge_failures_root) or []:
        diag = art.get("diagnostic", {}) if isinstance(art.get("diagnostic", {}), dict) else {}
        target_theorem = str(art.get("target_theorem", "")).strip()
        if not target_theorem:
            continue
        taxonomy = str(art.get("taxonomy", "")).strip() or "bridge_failure"
        item = {
            "source": "bridge_failure_artifact",
            "paper_id": str(art.get("paper_id", "")),
            "theorem_name": target_theorem,
            "status": "UNRESOLVED",
            "failure_pattern": taxonomy,
            "claim_equivalence_verdict": "unclear",
            "gate_failures": [],
            "error_message": _normalize_error(str(diag.get("error", ""))),
            "rounds_used": int(diag.get("max_repair_rounds", 0) or 0),
            "proof_mode": str(diag.get("proof_mode", "bridge")),
            "timestamp_unix": int(art.get("timestamp_unix", int(time.time()))),
        }
        rows.append(item)
        pattern_counter[taxonomy] += 1

    rows.sort(key=lambda r: (str(r.get("paper_id", "")), str(r.get("theorem_name", "")), str(r.get("source", ""))))
    summary = {
        "generated_at_unix": int(time.time()),
        "rows": len(rows),
        "patterns": {k: int(v) for k, v in pattern_counter.most_common()},
        "papers": len(by_paper),
    }
    return rows, summary


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build repair-flywheel dataset artifacts")
    p.add_argument("--ledger-root", default="output/verification_ledgers")
    p.add_argument("--bridge-failures-root", default="output/reports/bridge_failures")
    p.add_argument("--out-jsonl", default="output/flywheel/repair_dataset.jsonl")
    p.add_argument("--out-summary", default="output/flywheel/repair_dataset_summary.json")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    rows, summary = build_dataset(
        ledger_root=Path(args.ledger_root),
        bridge_failures_root=Path(args.bridge_failures_root),
    )

    out_jsonl = Path(args.out_jsonl)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    out_summary = Path(args.out_summary)
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    out_summary.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"out_jsonl": str(out_jsonl), "out_summary": str(out_summary), **summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

