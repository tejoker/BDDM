from __future__ import annotations

import sqlite3
from pathlib import Path

from gold_linkage_eval import evaluate, load_gold, load_pred_edges


def test_gold_linkage_eval_roundtrip(tmp_path: Path) -> None:
    gold_path = tmp_path / "gold.jsonl"
    gold_path.write_text(
        '{"src_theorem":"p1|a","dst_theorem":"p2|b","edge_type":"implies"}\n',
        encoding="utf-8",
    )
    db = tmp_path / "kg.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE kg_edges(src_theorem TEXT, dst_theorem TEXT, edge_type TEXT, PRIMARY KEY(src_theorem,dst_theorem,edge_type))"
    )
    con.execute("INSERT INTO kg_edges VALUES(?,?,?)", ("p1|a", "p2|b", "implies"))
    con.commit()
    con.close()

    gold = load_gold(gold_path)
    pred = load_pred_edges(db)
    res = evaluate(gold, pred)
    assert res["tp"] == 1
    assert res["f1"] == 1.0
