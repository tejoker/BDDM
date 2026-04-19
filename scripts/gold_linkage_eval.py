#!/usr/bin/env python3
"""Gold linkage set evaluation utilities.

Gold set format (JSONL):
{"src_theorem":"paper|thmA","dst_theorem":"paper|thmB","edge_type":"implies"}
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


def load_gold(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not path.exists():
        return rows
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
        src = str(raw.get("src_theorem", "")).strip()
        dst = str(raw.get("dst_theorem", "")).strip()
        et = str(raw.get("edge_type", "")).strip()
        if src and dst and et:
            rows.append({"src_theorem": src, "dst_theorem": dst, "edge_type": et})
    return rows


def load_pred_edges(db_path: Path) -> set[tuple[str, str, str]]:
    if not db_path.exists():
        return set()
    con = sqlite3.connect(str(db_path))
    rows = con.execute("SELECT src_theorem, dst_theorem, edge_type FROM kg_edges").fetchall()
    con.close()
    return {(str(a), str(b), str(c)) for a, b, c in rows}


def evaluate(gold: list[dict[str, str]], pred: set[tuple[str, str, str]]) -> dict[str, Any]:
    gold_set = {(g["src_theorem"], g["dst_theorem"], g["edge_type"]) for g in gold}
    tp = len(gold_set & pred)
    fp = len(pred - gold_set)
    fn = len(gold_set - pred)
    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)
    f1 = (2 * precision * recall) / max(1e-12, precision + recall)
    return {
        "gold_total": len(gold_set),
        "pred_total": len(pred),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Evaluate KG linkage predictions against gold set")
    p.add_argument("--gold", required=True, help="Path to gold linkage JSONL")
    p.add_argument("--kg-db", default="output/kg/kg_index.db")
    p.add_argument("--out", default="", help="Optional output json")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    gold = load_gold(Path(args.gold))
    pred = load_pred_edges(Path(args.kg_db))
    payload = evaluate(gold, pred)
    txt = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(txt, encoding="utf-8")
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

