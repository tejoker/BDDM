#!/usr/bin/env python3
"""Build a tiny hard gold translation set (default: 20 theorems) from ledgers.

Output JSONL is compatible with `scripts/eval_translation_fidelity.py`:
{"paper_id":"2604.15191","theorem_name":"t1","expected_lean":"theorem ..."}
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any


_NOISE_RE = re.compile(r"schema_translation|schema_fallback|missing theorem statement", re.IGNORECASE)


def _iter_rows(ledger_root: Path):
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


def _score(row: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    status = str(row.get("status", "")).upper()
    gates = row.get("validation_gates", {}) if isinstance(row.get("validation_gates", {}), dict) else {}
    gate_failures = row.get("gate_failures", []) if isinstance(row.get("gate_failures", []), list) else []
    stmt = str(row.get("lean_statement", "") or "")
    err = str(row.get("error_message", "") or "")
    assumptions = row.get("assumptions", []) if isinstance(row.get("assumptions", []), list) else []
    score = 0
    # Hard-first ranking.
    if status != "FULLY_PROVEN":
        score += 100
    score += 8 * len(gate_failures)
    if not gates.get("translation_fidelity_ok", False):
        score += 30
    if not gates.get("claim_equivalent", False):
        score += 25
    if not gates.get("lean_proof_closed", False):
        score += 20
    if not gates.get("assumptions_grounded", False):
        score += 18
    if _NOISE_RE.search(stmt) or _NOISE_RE.search(err):
        score += 20
    score += min(15, max(0, len(assumptions) - 1) * 3)
    meta = {
        "status": status,
        "gate_failures": gate_failures,
        "assumptions_count": len(assumptions),
        "error_message": err[:240],
    }
    return score, meta


def _clean_stmt(stmt: str) -> str:
    s = (stmt or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def build_gold(
    *,
    ledger_root: Path,
    limit: int,
    include_fully_proven: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[tuple[int, dict[str, Any]]] = []
    for paper_id, row in _iter_rows(ledger_root):
        theorem_name = str(row.get("theorem_name", "")).strip()
        expected = _clean_stmt(str(row.get("lean_statement", "") or ""))
        if not paper_id or not theorem_name or not expected:
            continue
        status = str(row.get("status", "")).upper()
        if (not include_fully_proven) and status == "FULLY_PROVEN":
            continue
        sc, meta = _score(row)
        candidates.append(
            (
                sc,
                {
                    "paper_id": paper_id,
                    "theorem_name": theorem_name,
                    "expected_lean": expected,
                    "score": sc,
                    "meta": meta,
                },
            )
        )

    candidates.sort(key=lambda x: (-x[0], x[1]["paper_id"], x[1]["theorem_name"]))
    selected = [row for _, row in candidates[: max(1, int(limit))]]

    jsonl_rows = [
        {
            "paper_id": r["paper_id"],
            "theorem_name": r["theorem_name"],
            "expected_lean": r["expected_lean"],
        }
        for r in selected
    ]
    return jsonl_rows, selected


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build tiny hard gold translation set from local ledgers")
    p.add_argument("--ledger-root", default="output/verification_ledgers")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--include-fully-proven", action="store_true")
    p.add_argument("--out-jsonl", default="reproducibility/gold_translation_tiny_hard20.jsonl")
    p.add_argument("--out-meta", default="reproducibility/gold_translation_tiny_hard20_meta.json")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    rows, meta = build_gold(
        ledger_root=Path(args.ledger_root),
        limit=max(1, int(args.limit)),
        include_fully_proven=bool(args.include_fully_proven),
    )
    out_jsonl = Path(args.out_jsonl)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    out_meta = Path(args.out_meta)
    out_meta.parent.mkdir(parents=True, exist_ok=True)
    out_meta.write_text(
        json.dumps(
            {
                "generated_at_unix": int(time.time()),
                "count": len(rows),
                "ledger_root": str(Path(args.ledger_root)),
                "items": meta,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"count": len(rows), "out_jsonl": str(out_jsonl), "out_meta": str(out_meta)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

