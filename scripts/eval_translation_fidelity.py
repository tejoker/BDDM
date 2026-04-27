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


def _norm_name(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def _discover_default_dbs() -> list[Path]:
    roots = [Path("output/kg/kg_index.db")]
    for p in sorted(Path("output").glob("**/kg_index.db")):
        if p not in roots:
            roots.append(p)
    return roots


def _extract_lean(payload_json: Any) -> str:
    try:
        payload = json.loads(str(payload_json))
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("lean_statement", "")).strip()


def _load_actual_from_db(db_path: Path, paper_id: str, theorem_name: str) -> str:
    if not db_path.exists():
        return ""
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute(
            "SELECT payload_json FROM kg_nodes WHERE paper_id=? AND theorem_name=? LIMIT 1",
            (paper_id, theorem_name),
        ).fetchone()
        if row is not None:
            actual = _extract_lean(row[0])
            if actual:
                return actual

        # Fallback: normalized theorem-name matching to survive naming variants.
        target = _norm_name(theorem_name)
        if not target:
            return ""
        rows = con.execute(
            "SELECT theorem_name, payload_json FROM kg_nodes WHERE paper_id=?",
            (paper_id,),
        ).fetchall()
        for thm, payload_json in rows:
            if _norm_name(str(thm)) != target:
                continue
            actual = _extract_lean(payload_json)
            if actual:
                return actual
    finally:
        con.close()
    return ""


def _load_actual(db_paths: list[Path], paper_id: str, theorem_name: str) -> tuple[str, str]:
    for db_path in db_paths:
        actual = _load_actual_from_db(db_path, paper_id, theorem_name)
        if actual:
            return actual, str(db_path)
    return "", ""


def evaluate(*, gold_path: Path, kg_dbs: list[Path]) -> dict[str, Any]:
    gold = _load_gold(gold_path)
    rows: list[dict[str, Any]] = []
    total = 0.0
    for g in gold:
        actual, source_db = _load_actual(kg_dbs, g["paper_id"], g["theorem_name"])
        score = _f1(_tokens(actual), _tokens(g["expected_lean"]))
        rows.append(
            {
                "paper_id": g["paper_id"],
                "theorem_name": g["theorem_name"],
                "score_f1": round(score, 4),
                "found": bool(actual),
                "source_db": source_db,
            }
        )
        total += score
    avg = total / max(1, len(rows))
    return {
        "gold_count": len(rows),
        "avg_fidelity_f1": round(avg, 4),
        "db_paths": [str(p) for p in kg_dbs],
        "rows": rows,
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Evaluate translation fidelity against gold set")
    p.add_argument("--gold", default="reproducibility/gold_translation_fidelity.jsonl")
    p.add_argument("--kg-db", action="append", default=[])
    p.add_argument("--min-score", type=float, default=0.8)
    p.add_argument("--out", default="")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    kg_dbs = [Path(p) for p in args.kg_db] if args.kg_db else _discover_default_dbs()
    payload = evaluate(gold_path=Path(args.gold), kg_dbs=kg_dbs)
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
