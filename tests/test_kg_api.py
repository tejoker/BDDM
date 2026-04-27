"""Test suite for kg_api.py FastAPI endpoints."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")
TestClient = fastapi_testclient.TestClient


@pytest.fixture
def temp_kg_db():
    """Create a temporary KG database with test fixtures."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_kg.db"
        con = sqlite3.connect(str(db_path))
        
        # Create minimal schema matching KG expectations
        con.execute(
            """
            CREATE TABLE kg_nodes (
                id INTEGER PRIMARY KEY,
                paper_id TEXT NOT NULL,
                theorem_name TEXT NOT NULL,
                layer TEXT,
                status TEXT,
                payload_json TEXT
            )
            """
        )
        
        # Insert test data
        test_nodes = [
            (
                "2304.09598",
                "my_theorem",
                "trusted",
                "FULLY_PROVEN",
                json.dumps({"proof_steps": 5, "lines": 20}),
            ),
            (
                "2304.09598",
                "another_theorem",
                "conditional",
                "INTERMEDIARY_PROVEN",
                json.dumps({"proof_steps": 3, "lines": 15}),
            ),
            (
                "2305.12345",
                "third_theorem",
                "trusted",
                "FULLY_PROVEN",
                json.dumps({"proof_steps": 8, "lines": 30}),
            ),
        ]
        
        for paper_id, theorem_name, layer, status, payload in test_nodes:
            con.execute(
                """
                INSERT INTO kg_nodes (paper_id, theorem_name, layer, status, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (paper_id, theorem_name, layer, status, payload),
            )
        
        con.commit()
        con.close()
        yield db_path


@pytest.fixture
def kg_client(temp_kg_db):
    """Create FastAPI test client with mocked KG database path."""
    # Import here to avoid circular imports
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    
    def mock_query_fn(db_path, layer=None, paper_id=None, status=None, limit=100):
        con = sqlite3.connect(str(db_path))
        query = "SELECT paper_id, theorem_name, layer, status, payload_json FROM kg_nodes WHERE 1=1"
        params = []

        if paper_id:
            query += " AND paper_id = ?"
            params.append(paper_id)
        if layer:
            query += " AND layer = ?"
            params.append(layer)
        if status:
            query += " AND status = ?"
            params.append(status)

        query += f" LIMIT {int(limit)}"

        rows = con.execute(query, params).fetchall()
        con.close()

        return [
            {
                "paper_id": row[0],
                "theorem_name": row[1],
                "layer": row[2],
                "status": row[3],
                "payload": json.loads(row[4]),
            }
            for row in rows
        ]

    # Patch environment variables
    with mock.patch.dict(
        os.environ,
        {
            "DESOL_KG_DB": str(temp_kg_db),
            "DESOL_PROJECT_ROOT": "/tmp",
        },
    ):
        # Reload the module to pick up patched environment
        import kg_api
        import importlib
        importlib.reload(kg_api)

        # Patch query function after reload so fixture always controls result shape.
        with mock.patch.object(kg_api, "query_kg", side_effect=mock_query_fn):
            yield TestClient(kg_api.app)


class TestKGAPIHealth:
    """Test health check endpoint."""

    def test_health_check(self, kg_client):
        """GET /health should return ok status."""
        response = kg_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "kg_db" in data
        assert "kg_db_exists" in data


class TestKGAPIQuery:
    """Test KG query endpoints."""

    def test_query_all_nodes(self, kg_client):
        """GET /kg/query should return all nodes when no filter."""
        response = kg_client.get("/kg/query?limit=100")
        assert response.status_code == 200
        nodes = response.json()
        assert len(nodes) == 3

    def test_query_by_paper_id(self, kg_client):
        """GET /kg/query?paper_id=X should filter by paper."""
        response = kg_client.get("/kg/query?paper_id=2304.09598&limit=100")
        assert response.status_code == 200
        nodes = response.json()
        assert len(nodes) == 2
        assert all(n["paper_id"] == "2304.09598" for n in nodes)

    def test_query_by_layer(self, kg_client):
        """GET /kg/query?layer=X should filter by layer."""
        response = kg_client.get("/kg/query?layer=trusted&limit=100")
        assert response.status_code == 200
        nodes = response.json()
        assert len(nodes) == 2
        assert all(n["layer"] == "trusted" for n in nodes)

    def test_query_by_status(self, kg_client):
        """GET /kg/query?status=X should filter by status."""
        response = kg_client.get("/kg/query?status=FULLY_PROVEN&limit=100")
        assert response.status_code == 200
        nodes = response.json()
        assert len(nodes) == 2
        assert all(n["status"] == "FULLY_PROVEN" for n in nodes)

    def test_query_combined_filters(self, kg_client):
        """GET /kg/query with multiple filters should intersect."""
        response = kg_client.get(
            "/kg/query?paper_id=2304.09598&layer=trusted&limit=100"
        )
        assert response.status_code == 200
        nodes = response.json()
        assert len(nodes) == 1
        assert nodes[0]["theorem_name"] == "my_theorem"

    def test_query_limit_enforcement(self, kg_client):
        """GET /kg/query should respect limit parameter."""
        response = kg_client.get("/kg/query?limit=1")
        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_query_limit_bounds(self, kg_client):
        """GET /kg/query should reject invalid limit."""
        response = kg_client.get("/kg/query?limit=0")
        assert response.status_code == 422  # Validation error
        
        response = kg_client.get("/kg/query?limit=9999")
        assert response.status_code == 422  # Over max limit


class TestKGAPIPaper:
    """Test paper-specific endpoints."""

    def test_paper_found(self, kg_client):
        """GET /kg/paper/{paper_id} should return all nodes for paper."""
        response = kg_client.get("/kg/paper/2304.09598")
        assert response.status_code == 200
        nodes = response.json()
        assert len(nodes) == 2
        assert all(n["paper_id"] == "2304.09598" for n in nodes)

    def test_paper_not_found(self, kg_client):
        """GET /kg/paper/{paper_id} with unknown ID should return 404."""
        response = kg_client.get("/kg/paper/9999.99999")
        assert response.status_code == 404
        assert "No KG entries" in response.json()["detail"]

    def test_paper_endpoint_returns_all_nodes(self, kg_client):
        """GET /kg/paper/{paper_id} should not be limited by default limit."""
        response = kg_client.get("/kg/paper/2304.09598")
        assert response.status_code == 200
        assert len(response.json()) == 2


class TestKGAPIProof:
    """Test specific proof endpoints."""

    def test_proof_found(self, kg_client):
        """GET /kg/proof/{paper_id}/{theorem_name} should return node."""
        response = kg_client.get("/kg/proof/2304.09598/my_theorem")
        assert response.status_code == 200
        node = response.json()
        assert node["paper_id"] == "2304.09598"
        assert node["theorem_name"] == "my_theorem"
        assert node["status"] == "FULLY_PROVEN"

    def test_proof_not_found_wrong_theorem(self, kg_client):
        """GET /kg/proof with unknown theorem should return 404."""
        response = kg_client.get("/kg/proof/2304.09598/nonexistent_theorem")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_proof_not_found_wrong_paper(self, kg_client):
        """GET /kg/proof with unknown paper should return 404."""
        response = kg_client.get("/kg/proof/9999.99999/my_theorem")
        assert response.status_code == 404


class TestKGAPIVerifyEndpoint:
    """Test background pipeline trigger endpoint."""

    @mock.patch("subprocess.Popen")
    def test_verify_queues_job(self, mock_popen, kg_client):
        """POST /verify should spawn subprocess and return job info."""
        mock_popen.return_value = mock.Mock(pid=12345)
        
        response = kg_client.post("/verify?paper_id=2304.09598")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"
        assert data["paper_id"] == "2304.09598"
        assert data["pid"] == 12345
        
        # Verify subprocess was called
        mock_popen.assert_called_once()
        call_args = mock_popen.call_args[0][0]
        assert "2304.09598" in call_args

    @mock.patch("subprocess.Popen")
    def test_verify_invalid_paper_id_format(self, mock_popen, kg_client):
        """POST /verify with invalid paper_id format should return 400. (P0)"""
        # Valid format: YYYY.NNNNN
        invalid_ids = [
            "2304.9",  # Too short
            "23049598",  # Missing dot
            "230409598",  # Too long after dot
            "23/04/09598",  # Wrong separators
            "abc.defgh",  # Non-numeric
            "../../../etc/passwd",  # Path traversal
            "2304.09598; rm -rf /",  # Command injection
        ]
        for invalid_id in invalid_ids:
            response = kg_client.post(f"/verify?paper_id={invalid_id}")
            assert response.status_code == 400, f"Expected 400 for {invalid_id!r}, got {response.status_code}"
            assert "Invalid paper ID format" in response.json()["detail"]

    @mock.patch("subprocess.Popen")
    def test_verify_valid_paper_id_format(self, mock_popen, kg_client):
        """POST /verify should accept valid paper_id format. (P0)"""
        mock_popen.return_value = mock.Mock(pid=12345)
        
        valid_ids = ["2304.09598", "2305.12345", "2401.00001"]
        for valid_id in valid_ids:
            response = kg_client.post(f"/verify?paper_id={valid_id}")
            assert response.status_code == 200
            assert response.json()["paper_id"] == valid_id

    @mock.patch("subprocess.Popen")
    def test_verify_missing_script(self, mock_popen, kg_client):
        """POST /verify should return 500 if arxiv_to_lean.py missing."""
        from pathlib import Path as _Path

        with mock.patch("kg_api.SCRIPT_DIR", _Path("/definitely_missing_script_dir")):
            response = kg_client.post("/verify?paper_id=2304.09598")
            assert response.status_code == 500
