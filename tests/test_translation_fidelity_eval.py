from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from eval_translation_fidelity import evaluate


def test_translation_fidelity_eval(tmp_path: Path) -> None:
    gold = tmp_path / "gold.jsonl"
    gold.write_text(
        '{"paper_id":"p1","theorem_name":"t1","expected_lean":"theorem t1 : x = x"}\n',
        encoding="utf-8",
    )
    db = tmp_path / "kg.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE kg_nodes(paper_id TEXT, theorem_name TEXT, payload_json TEXT, PRIMARY KEY(paper_id,theorem_name))"
    )
    payload = {"lean_statement": "theorem t1 : x = x := by rfl"}
    con.execute("INSERT INTO kg_nodes VALUES(?,?,?)", ("p1", "t1", json.dumps(payload)))
    con.commit()
    con.close()

    res = evaluate(gold_path=gold, kg_db=db)
    assert res["gold_count"] == 1
    assert res["avg_fidelity_f1"] > 0.6
