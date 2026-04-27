from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

from statement_retrieval import (
    build_statement_index,
    iter_statement_rows,
    load_statement_metadata,
    query_statement_index,
    statement_id,
)


def _write_ledger(ledger_dir: Path) -> Path:
    ledger_dir.mkdir(parents=True, exist_ok=True)
    path = ledger_dir / "2401.00001.json"
    payload = {
        "schema_version": "test",
        "entries": [
            {
                "theorem_name": "gaussian_integrable",
                "status": "FULLY_PROVEN",
                "promotion_gate_passed": True,
                "lean_statement": "theorem gaussian_integrable : Integrable X := by sorry",
                "semantic_equivalence_artifact": {
                    "original_latex_theorem": "Every Gaussian random variable is integrable.",
                    "normalized_natural_language_theorem": "Gaussian random variables are integrable.",
                    "extracted_assumptions": ["X has Gaussian law"],
                    "extracted_conclusion": "X is integrable",
                },
            },
            {
                "theorem_name": "independent_increments",
                "status": "INTERMEDIARY_PROVEN",
                "lean_statement": "theorem independent_increments : IndepFun increments := by sorry",
                "semantic_equivalence_artifact": {
                    "original_latex_theorem": "The process has independent increments.",
                    "normalized_natural_language_theorem": "The increments of the process are independent.",
                    "extracted_conclusion": "increments are independent",
                },
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_statement_id_is_stable() -> None:
    assert statement_id("2401.00001", "gaussian_integrable") == "2401.00001|gaussian_integrable"


def test_iter_statement_rows_extracts_semantic_artifact(tmp_path: Path) -> None:
    ledger_dir = tmp_path / "ledgers"
    _write_ledger(ledger_dir)

    rows = iter_statement_rows(ledger_dir)

    assert len(rows) == 2
    meta, text = rows[0]
    assert meta.statement_id == "2401.00001|gaussian_integrable"
    assert meta.layer == "trusted"
    assert "Gaussian random variables are integrable" in text
    assert "X has Gaussian law" in text


def test_build_and_query_statement_index_hash(tmp_path: Path) -> None:
    ledger_dir = tmp_path / "ledgers"
    index_dir = tmp_path / "statement_index"
    _write_ledger(ledger_dir)

    summary = build_statement_index(
        ledger_dir=ledger_dir,
        out_dir=index_dir,
        encoder_name="hash",
        dims=128,
    )
    metadata = load_statement_metadata(index_dir)
    hits = query_statement_index(index_dir, "gaussian variable integrable", top_k=1)

    assert summary["kind"] == "desol_statement_index"
    assert summary["count"] == 2
    assert "2401.00001|gaussian_integrable" in metadata
    assert hits[0]["statement_id"] == "2401.00001|gaussian_integrable"
    assert hits[0]["kg_ref"] == "2401.00001|gaussian_integrable"


def test_kg_api_semantic_search_endpoint(tmp_path: Path) -> None:
    fastapi = pytest.importorskip("fastapi.testclient")
    ledger_dir = tmp_path / "ledgers"
    index_dir = tmp_path / "statement_index"
    _write_ledger(ledger_dir)
    build_statement_index(
        ledger_dir=ledger_dir,
        out_dir=index_dir,
        encoder_name="hash",
        dims=128,
    )

    with mock.patch.dict(
        os.environ,
        {
            "DESOL_STATEMENT_INDEX": str(index_dir),
            "DESOL_KG_DB": str(tmp_path / "kg.db"),
            "DESOL_API_KEY": "",
        },
    ):
        if "kg_api" in sys.modules:
            del sys.modules["kg_api"]
        kg_api = importlib.import_module("kg_api")
        client = fastapi.TestClient(kg_api.app)

    response = client.get("/kg/semantic/search?q=gaussian%20integrable&top_k=1")
    assert response.status_code == 200
    data = response.json()
    assert data[0]["statement_id"] == "2401.00001|gaussian_integrable"
