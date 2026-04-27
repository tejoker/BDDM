#!/usr/bin/env python3
"""Build a nontrivial translation-fidelity gold set (default: 50 items).

This targets hard, actionable theorem lanes by excluding trivialized targets and
ranking entries by unresolved semantic/proof blockers.
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


def _is_nontrivial(stmt: str) -> bool:
    s = " ".join((stmt or "").split())
    if not s:
        return False
    low = s.lower()
    if re.search(r":\s*true\s*(?::=|$)", low):
        return False
    if "schema_fallback" in low or "schema_translation" in low:
        return False
    if " by trivial" in low:
        return False
    if not any(tok in s for tok in ("→", "->", "↔", "=", "≤", "≥", "<", ">", "∃", "∀")):
        return False
    return True


def _score(row: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    status = str(row.get("status", "")).upper()
    gates = row.get("validation_gates", {}) if isinstance(row.get("validation_gates", {}), dict) else {}
    gate_failures = row.get("gate_failures", []) if isinstance(row.get("gate_failures", []), list) else []
    stmt = str(row.get("lean_statement", "") or "")
    err = str(row.get("error_message", "") or "")
    assumptions = row.get("assumptions", []) if isinstance(row.get("assumptions", []), list) else []
    score = 0
    if status != "FULLY_PROVEN":
        score += 120
    score += 10 * len(gate_failures)
    if not gates.get("translation_fidelity_ok", False):
        score += 36
    if not gates.get("claim_equivalent", False):
        score += 32
    if not gates.get("lean_proof_closed", False):
        score += 24
    if not gates.get("assumptions_grounded", False):
        score += 18
    if _NOISE_RE.search(stmt) or _NOISE_RE.search(err):
        score += 20
    score += min(20, max(0, len(assumptions) - 1) * 4)
    meta = {
        "status": status,
        "gate_failures": gate_failures,
        "assumptions_count": len(assumptions),
        "error_message": err[:240],
    }
    return score, meta


def _clean_stmt(stmt: str) -> str:
    return re.sub(r"\s+", " ", (stmt or "")).strip()


def build_gold(*, ledger_root: Path, limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[tuple[int, dict[str, Any]]] = []
    for paper_id, row in _iter_rows(ledger_root):
        theorem_name = str(row.get("theorem_name", "")).strip()
        expected = _clean_stmt(str(row.get("lean_statement", "") or ""))
        if not paper_id or not theorem_name or not expected:
            continue
        if not _is_nontrivial(expected):
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
    rows = [
        {"paper_id": r["paper_id"], "theorem_name": r["theorem_name"], "expected_lean": r["expected_lean"]}
        for r in selected
    ]
    return rows, selected


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build nontrivial translation-fidelity gold set from ledgers")
    p.add_argument("--ledger-root", default="output/verification_ledgers")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--out-jsonl", default="reproducibility/gold_translation_nontrivial50.jsonl")
    p.add_argument("--out-meta", default="reproducibility/gold_translation_nontrivial50_meta.json")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    rows, meta = build_gold(
        ledger_root=Path(args.ledger_root),
        limit=max(1, int(args.limit)),
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

