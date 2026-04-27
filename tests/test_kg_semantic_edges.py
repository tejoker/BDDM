from __future__ import annotations

import json
from pathlib import Path

from kg_writer import build_kg, query_kg_edges, query_kg
from statement_retrieval import build_statement_index


def _write_semantic_ledger(ledger_dir: Path) -> None:
    ledger_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "test",
        "entries": [
            {
                "theorem_name": "gaussian_integrable",
                "lean_file": "A.lean",
                "lean_statement": "theorem gaussian_integrable : Integrable X := by sorry",
                "status": "FULLY_PROVEN",
                "promotion_gate_passed": True,
                "semantic_equivalence_artifact": {
                    "original_latex_theorem": "Every Gaussian random variable is integrable.",
                    "normalized_natural_language_theorem": "Gaussian random variables are integrable.",
                    "extracted_conclusion": "Gaussian variables are integrable",
                },
            },
            {
                "theorem_name": "normal_law_integrable",
                "lean_file": "B.lean",
                "lean_statement": "theorem normal_law_integrable : Integrable Y := by sorry",
                "status": "FULLY_PROVEN",
                "promotion_gate_passed": True,
                "semantic_equivalence_artifact": {
                    "original_latex_theorem": "A random variable with normal law is integrable.",
                    "normalized_natural_language_theorem": "Normal law random variables are integrable.",
                    "extracted_conclusion": "Normal random variables are integrable",
                },
            },
            {
                "theorem_name": "independent_increments",
                "lean_file": "C.lean",
                "lean_statement": "theorem independent_increments : IndepFun increments := by sorry",
                "status": "UNRESOLVED",
                "semantic_equivalence_artifact": {
                    "original_latex_theorem": "The process has independent increments.",
                    "normalized_natural_language_theorem": "The increments of the process are independent.",
                    "extracted_conclusion": "increments are independent",
                },
            },
        ],
    }
    (ledger_dir / "2401.00002.json").write_text(json.dumps(payload), encoding="utf-8")


def test_build_kg_writes_statement_fields_and_semantic_edges(tmp_path: Path) -> None:
    ledger_dir = tmp_path / "ledgers"
    kg_root = tmp_path / "kg"
    statement_index = tmp_path / "statement_index"
    _write_semantic_ledger(ledger_dir)
    build_statement_index(
        ledger_dir=ledger_dir,
        out_dir=statement_index,
        encoder_name="hash",
        dims=128,
    )

    summary = build_kg(
        ledger_dir=ledger_dir,
        kg_root=kg_root,
        statement_index=statement_index,
        semantic_edge_threshold=0.05,
        semantic_top_k=2,
    )
    edges = query_kg_edges(kg_root / "kg_index.db", edge_type="semantically_similar_to", limit=20)
    nodes = query_kg(kg_root / "kg_index.db", paper_id="2401.00002", limit=10)

    assert summary.semantic_edges > 0
    assert edges
    assert all(edge["src_theorem"] != edge["dst_theorem"] for edge in edges)
    assert all(edge["edge_type"] == "semantically_similar_to" for edge in edges)
    assert any(
        edge["src_theorem"] == "2401.00002|gaussian_integrable"
        and edge["dst_theorem"] == "2401.00002|normal_law_integrable"
        for edge in edges
    )
    assert nodes[0]["semantic_statement"]["retrieval_text_hash"]
    assert nodes[0]["semantic_statement_text"]
