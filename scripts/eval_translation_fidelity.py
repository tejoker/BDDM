#!/usr/bin/env python3
"""Evaluate translation fidelity against a gold theorem set.

Gold format (JSONL):
{"paper_id":"2604.15191","theorem_name":"t1","expected_lean":"theorem ..."}
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any


_TOK_RE = re.compile(r"[A-Za-z0-9_']+")


def _tokens(s: str) -> set[str]:
    return {t.lower() for t in _TOK_RE.findall(s or "") if len(t) >= 2}


def _f1(pred: set[str], gold: set[str]) -> float:
    if not pred and not gold:
        return 1.0
    if not pred or not gold:
        return 0.0
    tp = len(pred & gold)
    p = tp / max(1, len(pred))
    r = tp / max(1, len(gold))
    return (2 * p * r) / max(1e-12, p + r)


def _load_gold(path: Path) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not path.exists():
        return out
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            raw = json.loads(ln)
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        paper_id = str(raw.get("paper_id", "")).strip()
        theorem_name = str(raw.get("theorem_name", "")).strip()
        expected = str(raw.get("expected_lean", "")).strip()
        if paper_id and theorem_name and expected:
            out.append({"paper_id": paper_id, "theorem_name": theorem_name, "expected_lean": expected})
    return out


def _load_actual(db_path: Path, paper_id: str, theorem_name: str) -> str:
    if not db_path.exists():
        return ""
    con = sqlite3.connect(str(db_path))
    row = con.execute(
        "SELECT payload_json FROM kg_nodes WHERE paper_id=? AND theorem_name=? LIMIT 1",
        (paper_id, theorem_name),
    ).fetchone()
    con.close()
    if row is None:
        return ""
    try:
        payload = json.loads(str(row[0]))
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("lean_statement", "")).strip()


def evaluate(*, gold_path: Path, kg_db: Path) -> dict[str, Any]:
    gold = _load_gold(gold_path)
    rows: list[dict[str, Any]] = []
    total = 0.0
    for g in gold:
        actual = _load_actual(kg_db, g["paper_id"], g["theorem_name"])
        score = _f1(_tokens(actual), _tokens(g["expected_lean"]))
        rows.append(
            {
                "paper_id": g["paper_id"],
                "theorem_name": g["theorem_name"],
                "score_f1": round(score, 4),
                "found": bool(actual),
            }
        )
        total += score
    avg = total / max(1, len(rows))
    return {
        "gold_count": len(rows),
        "avg_fidelity_f1": round(avg, 4),
        "rows": rows,
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Evaluate translation fidelity against gold set")
    p.add_argument("--gold", default="reproducibility/gold_translation_fidelity.jsonl")
    p.add_argument("--kg-db", default="output/kg/kg_index.db")
    p.add_argument("--min-score", type=float, default=0.8)
    p.add_argument("--out", default="")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    payload = evaluate(gold_path=Path(args.gold), kg_db=Path(args.kg_db))
    txt = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(txt, encoding="utf-8")
    print(txt)
    if payload["gold_count"] <= 0:
        return 2
    return 0 if float(payload["avg_fidelity_f1"]) >= float(args.min_score) else 3


if __name__ == "__main__":
    sys.exit(main())

