from __future__ import annotations

import sqlite3
from pathlib import Path

from ci_bootstrap_gates import bootstrap


def test_ci_bootstrap_creates_db(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.jsonl"
    fixture.write_text(
        '{"paper_id":"p1","theorem_name":"t1","payload":{"lean_statement":"theorem t1 : True"},"edges":[{"src":"p1|t1","dst":"p2|t2","edge_type":"implies"}]}\n',
        encoding="utf-8",
    )
    db = tmp_path / "kg.db"
    res = bootstrap(fixture_jsonl=fixture, kg_db=db)
    assert res["nodes"] == 1
    con = sqlite3.connect(str(db))
    n = con.execute("SELECT COUNT(*) FROM kg_nodes").fetchone()[0]
    e = con.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0]
    con.close()
    assert n == 1
    assert e == 1
