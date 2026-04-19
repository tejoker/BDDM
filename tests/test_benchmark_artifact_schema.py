from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from benchmark_minif2f import BenchmarkResult, _validate_benchmark_artifact


def _base_result() -> BenchmarkResult:
    return BenchmarkResult(
        schema_version="1.0.0",
        split="test",
        n_problems=10,
        k=1,
        pass_at_1=0.1,
        pass_at_k=0.1,
        total_solved=1,
        total_attempts=10,
        elapsed_seconds=10.0,
        timestamp="20260101T000000",
        model="labs-leanstral-2603",
        retrieval_index="data/mathlib_embeddings",
        retrieval_top_k=12,
        lean_timeout_s=120,
        mode="ponder",
        git_commit="deadbeef",
        lean_version="Lean (version 4.x)",
        python_version="3.12.0",
    )


def test_benchmark_artifact_schema_accepts_complete() -> None:
    bench = _base_result()
    _validate_benchmark_artifact(bench)


@pytest.mark.parametrize(
    "field,value",
    [
        ("model", ""),
        ("retrieval_index", ""),
        ("retrieval_top_k", 0),
        ("lean_timeout_s", 0),
        ("git_commit", ""),
        ("lean_version", ""),
        ("python_version", ""),
    ],
)
def test_benchmark_artifact_schema_rejects_missing_required(field: str, value: object) -> None:
    bench = _base_result()
    setattr(bench, field, value)
    with pytest.raises(ValueError):
        _validate_benchmark_artifact(bench)

