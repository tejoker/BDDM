#!/usr/bin/env python3
"""Deterministically bootstrap CI artifacts for quality gates."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def _read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
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
        if isinstance(raw, dict):
            out.append(raw)
    return out


def bootstrap(*, fixture_jsonl: Path, kg_db: Path) -> dict:
    rows = _read_jsonl(fixture_jsonl)
    kg_db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(kg_db))
    with con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS kg_nodes (
                paper_id TEXT NOT NULL,
                theorem_name TEXT NOT NULL,
                layer TEXT NOT NULL DEFAULT 'trusted',
                status TEXT NOT NULL DEFAULT 'FULLY_PROVEN',
                payload_json TEXT NOT NULL,
                PRIMARY KEY (paper_id, theorem_name)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS kg_edges (
                src_theorem TEXT NOT NULL,
                dst_theorem TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                PRIMARY KEY (src_theorem, dst_theorem, edge_type)
            )
            """
        )
        con.execute("DELETE FROM kg_nodes")
        con.execute("DELETE FROM kg_edges")
        inserted_nodes = 0
        inserted_edges = 0
        for r in rows:
            paper_id = str(r.get("paper_id", "")).strip()
            theorem_name = str(r.get("theorem_name", "")).strip()
            payload = r.get("payload", {})
            if not paper_id or not theorem_name:
                continue
            con.execute(
                "INSERT OR REPLACE INTO kg_nodes(paper_id, theorem_name, layer, status, payload_json) VALUES (?,?,?,?,?)",
                (
                    paper_id,
                    theorem_name,
                    str(r.get("layer", "trusted")),
                    str(r.get("status", "FULLY_PROVEN")),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            inserted_nodes += 1
            for e in r.get("edges", []) if isinstance(r.get("edges"), list) else []:
                if not isinstance(e, dict):
                    continue
                src = str(e.get("src", "")).strip()
                dst = str(e.get("dst", "")).strip()
                et = str(e.get("edge_type", "")).strip()
                if src and dst and et:
                    con.execute(
                        "INSERT OR REPLACE INTO kg_edges(src_theorem,dst_theorem,edge_type) VALUES(?,?,?)",
                        (src, dst, et),
                    )
                    inserted_edges += 1
    con.close()
    return {"nodes": inserted_nodes, "edges": inserted_edges, "kg_db": str(kg_db)}


def main() -> int:
    p = argparse.ArgumentParser(description="Bootstrap deterministic KG fixtures for CI quality gates")
    p.add_argument("--fixture-jsonl", default="reproducibility/ci_fixture_kg_nodes.jsonl")
    p.add_argument("--kg-db", default="output/kg/kg_index.db")
    args = p.parse_args()
    payload = bootstrap(fixture_jsonl=Path(args.fixture_jsonl), kg_db=Path(args.kg_db))
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

