from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from kg_writer import query_kg_edges, query_math_kg


def _seed_db(db_path: Path) -> None:
    con = sqlite3.connect(str(db_path))
    con.execute(
        """
        CREATE TABLE kg_nodes (
            paper_id TEXT NOT NULL,
            theorem_name TEXT NOT NULL,
            layer TEXT NOT NULL,
            status TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (paper_id, theorem_name)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE kg_edges (
            src_theorem TEXT NOT NULL,
            dst_theorem TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            PRIMARY KEY (src_theorem, dst_theorem, edge_type)
        )
        """
    )
    payload = {
        "paper_id": "2304.09598",
        "theorem_name": "my_thm",
        "canonical_theorem_id": "cth_abc",
        "claim_shape": "equality",
        "status": "FULLY_PROVEN",
        "layer": "trusted",
        "trust_class": "TRUSTED",
        "promotion_gate_passed": True,
        "proof_mode": "auto",
        "time_s": 1.2,
        "lean_statement": "theorem my_thm : True := by trivial",
        "provenance": {"paper_id": "2304.09598"},
    }
    con.execute(
        "INSERT INTO kg_nodes(paper_id, theorem_name, layer, status, payload_json) VALUES (?,?,?,?,?)",
        ("2304.09598", "my_thm", "trusted", "FULLY_PROVEN", json.dumps(payload)),
    )
    con.execute(
        "INSERT INTO kg_edges(src_theorem, dst_theorem, edge_type) VALUES (?,?,?)",
        ("2304.09598|my_thm", "2301.12345", "cites_arxiv"),
    )
    con.commit()
    con.close()


def test_query_math_kg_strips_evidence_fields() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "kg.db"
        _seed_db(db_path)
        rows = query_math_kg(db_path, limit=10)
        assert len(rows) == 1
        row = rows[0]
        assert row["theorem_name"] == "my_thm"
        assert row["canonical_theorem_id"] == "cth_abc"
        assert "lean_statement" not in row
        assert "provenance" not in row


def test_query_kg_edges_returns_rows() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "kg.db"
        _seed_db(db_path)
        edges = query_kg_edges(db_path, edge_type="cites_arxiv", limit=10)
        assert len(edges) == 1
        assert edges[0]["edge_type"] == "cites_arxiv"
        assert "evidence_ids" in edges[0]
        assert "canonical_relation_id" in edges[0]
