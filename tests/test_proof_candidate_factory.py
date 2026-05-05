"""Tests for run_proof_candidate_factory.py — proof-candidate factory."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import run_proof_candidate_factory as factory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _repair_row(**kw) -> dict:
    base = {
        "row_id": "r1",
        "arxiv_id": "2604.21314",
        "theorem_id": "thm:test",
        "repair_route": "statement_regeneration",
        "repair_kind": "replace_placeholder_statement",
        "priority_score": 50,
        "status": "FLAWED",
        "lean_statement": "theorem bad : True",
        "source_latex": "A nontrivial mathematical theorem.",
        "source_span_quality": "extractor_native",
        "artifact_paths": {},
    }
    base.update(kw)
    return base


def _corpus_row(**kw) -> dict:
    base = {
        "row_id": "r1",
        "arxiv_id": "2604.21314",
        "theorem_id": "thm:test",
        "lean_statement": "theorem bad : True",
        "status": "FLAWED",
        "statement_alignment_class": "partial",
        "source_span_quality": "extractor_native",
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# _count_reviewed_exact
# ---------------------------------------------------------------------------

def test_count_reviewed_exact_from_reviews_empty() -> None:
    assert factory._count_reviewed_exact_from_reviews([]) == 0


def test_count_reviewed_exact_from_reviews_counts_exact() -> None:
    reviews = [
        {"reviewed_statement_alignment_class": "exact"},
        {"reviewed_statement_alignment_class": "partial"},
        {"reviewed_statement_alignment_class": "exact"},
    ]
    assert factory._count_reviewed_exact_from_reviews(reviews) == 2


def test_count_reviewed_exact_ignores_non_exact() -> None:
    reviews = [
        {"reviewed_statement_alignment_class": "weaker"},
        {"reviewed_equivalence_verdict": "equivalent"},  # not alignment_class
    ]
    assert factory._count_reviewed_exact_from_reviews(reviews) == 0


# ---------------------------------------------------------------------------
# run_factory — Phase 1 only (--repair-only, no LLM)
# ---------------------------------------------------------------------------

def test_factory_repair_only_dry_run(tmp_path: Path) -> None:
    """Factory dry-run should not mutate files but still produce a summary."""
    repair_queue = tmp_path / "output" / "corpus" / "statement_repair_queue.jsonl"
    _write_jsonl(repair_queue, [_repair_row()])

    # Patch run_worker to return a canned dry-run result
    def mock_run_worker(rows, *, project_root, write, **kwargs):
        assert write is False
        return [], {
            "net_graduated_rows": 0,
            "review_batch_rows_after": 0,
            "gold_proof_queue_rows_after": 0,
            "graduated_rows_before_action": 0,
            "mutated_groups": 0,
            "write": False,
        }

    with patch("run_proof_candidate_factory.run_worker", side_effect=mock_run_worker):
        result = factory.run_factory(
            project_root=tmp_path,
            repair_queue_jsonl=repair_queue,
            dry_run=True,
            repair_only=True,
        )

    assert result["dry_run"] is True
    assert result["before"]["repair_queue_rows"] == 1
    assert result["phases"]["repair"]["status"] == "dry_run"


def test_factory_repair_only_write(tmp_path: Path) -> None:
    """Factory write mode should call run_worker with write=True."""
    repair_queue = tmp_path / "output" / "corpus" / "statement_repair_queue.jsonl"
    _write_jsonl(repair_queue, [_repair_row(row_id="r1"), _repair_row(row_id="r2")])

    called_with_write = []

    def mock_run_worker(rows, *, project_root, write, **kwargs):
        called_with_write.append(write)
        return [], {
            "net_graduated_rows": 2,
            "review_batch_rows_after": 2,
            "gold_proof_queue_rows_after": 0,
            "graduated_rows_before_action": 0,
            "mutated_groups": 1,
            "rollback": False,
            "write": True,
        }

    with patch("run_proof_candidate_factory.run_worker", side_effect=mock_run_worker):
        result = factory.run_factory(
            project_root=tmp_path,
            repair_queue_jsonl=repair_queue,
            dry_run=False,
            repair_only=True,
        )

    assert called_with_write == [True]
    assert result["phases"]["repair"]["status"] == "completed"
    assert result["phases"]["repair"]["net_graduated_rows"] == 2


def test_factory_repair_skipped_when_queue_empty(tmp_path: Path) -> None:
    empty_queue = tmp_path / "output" / "corpus" / "statement_repair_queue.jsonl"
    empty_queue.parent.mkdir(parents=True, exist_ok=True)
    empty_queue.write_text("")

    with patch("run_proof_candidate_factory.run_worker") as mock_worker:
        result = factory.run_factory(
            project_root=tmp_path,
            repair_queue_jsonl=empty_queue,
            dry_run=False,
            repair_only=True,
        )

    mock_worker.assert_not_called()
    assert result["phases"]["repair"]["status"] == "skipped_empty_queue"


# ---------------------------------------------------------------------------
# run_factory — Phase 2 skipped when no MISTRAL_API_KEY
# ---------------------------------------------------------------------------

def test_factory_review_skipped_without_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    repair_queue = tmp_path / "output" / "corpus" / "statement_repair_queue.jsonl"
    _write_jsonl(repair_queue, [_repair_row()])

    corpus_path = tmp_path / "output" / "corpus" / "stable_corpus.jsonl"
    _write_jsonl(corpus_path, [_corpus_row()])
    review_batch = tmp_path / "output" / "corpus" / "statement_review_batch.jsonl"
    _write_jsonl(review_batch, [_corpus_row()])

    def mock_run_worker(rows, *, project_root, write, **kwargs):
        return [], {
            "net_graduated_rows": 0, "review_batch_rows_after": 1,
            "gold_proof_queue_rows_after": 0, "graduated_rows_before_action": 0,
            "mutated_groups": 0, "write": write,
        }

    def mock_bridge(**kwargs):
        return [], [], [], {"assisted_reviewed_exact_rows": 0, "promoted_alignment_gold": 0, "gold_proof_queue_rows": 0}

    with (
        patch("run_proof_candidate_factory.run_worker", side_effect=mock_run_worker),
        patch("run_proof_candidate_factory.run_review_to_gold_bridge", side_effect=mock_bridge),
    ):
        result = factory.run_factory(
            project_root=tmp_path,
            repair_queue_jsonl=repair_queue,
            dry_run=False,
            repair_only=False,
        )

    assert result["phases"]["auto_review"]["status"] == "skipped_no_api_key"


# ---------------------------------------------------------------------------
# run_factory — Phase 3 (bridge) produces gold queue
# ---------------------------------------------------------------------------

def test_factory_bridge_populates_gold_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    repair_queue = tmp_path / "output" / "corpus" / "statement_repair_queue.jsonl"
    _write_jsonl(repair_queue, [_repair_row()])

    corpus_path = tmp_path / "output" / "corpus" / "stable_corpus.jsonl"
    _write_jsonl(corpus_path, [_corpus_row()])
    review_batch = tmp_path / "output" / "corpus" / "statement_review_batch.jsonl"
    _write_jsonl(review_batch, [_corpus_row()])

    gold_candidate = {
        "row_id": "r1",
        "lean_statement": "theorem good (n : ℕ) : n + 0 = n := by simp",
        "status": "UNRESOLVED",
    }

    def mock_run_worker(rows, *, project_root, write, **kwargs):
        return [], {
            "net_graduated_rows": 1, "review_batch_rows_after": 1,
            "gold_proof_queue_rows_after": 0, "graduated_rows_before_action": 0,
            "mutated_groups": 1, "write": write,
        }

    def mock_bridge(*, batch_rows, corpus_rows, additional_reviews, **kwargs):
        return (
            [{"reviewed_statement_alignment_class": "exact"}],  # assisted
            [_corpus_row()],  # reviewed corpus
            [gold_candidate],  # gold queue — 1 candidate
            {"assisted_reviewed_exact_rows": 1, "promoted_alignment_gold": 1, "gold_proof_queue_rows": 1},
        )

    with (
        patch("run_proof_candidate_factory.run_worker", side_effect=mock_run_worker),
        patch("run_proof_candidate_factory.run_review_to_gold_bridge", side_effect=mock_bridge),
    ):
        result = factory.run_factory(
            project_root=tmp_path,
            repair_queue_jsonl=repair_queue,
            dry_run=False,
            repair_only=False,
        )

    assert result["phases"]["bridge"]["gold_proof_queue_rows"] == 1
    assert result["phases"]["bridge"]["assisted_reviewed_exact"] == 1
    # Gold queue file should be written
    gold_path = tmp_path / "output" / "corpus" / "gold_proof_growth_queue.jsonl"
    assert gold_path.exists()
    written = [json.loads(l) for l in gold_path.read_text().splitlines() if l.strip()]
    assert len(written) == 1
    assert written[0]["row_id"] == "r1"


# ---------------------------------------------------------------------------
# _suggest_next_actions — smoke test (no assertion on output, just no crash)
# ---------------------------------------------------------------------------

def test_suggest_next_actions_no_crash() -> None:
    result = {
        "before": {"repair_queue_rows": 10, "gold_proof_queue_rows": 0, "auto_reviewed_exact": 0},
        "phases": {
            "repair": {"rollback": True},
            "auto_review": {"status": "skipped_no_api_key"},
            "bridge": {"gold_proof_queue_rows": 0, "assisted_reviewed_exact": 0, "promoted_alignment_gold": 0},
        },
    }
    # Should not raise
    factory._suggest_next_actions(result)


def test_factory_cli_dry_run_does_not_write_summary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repair_queue = tmp_path / "output" / "corpus" / "statement_repair_queue.jsonl"
    _write_jsonl(repair_queue, [])
    out_summary = tmp_path / "summary.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_proof_candidate_factory.py",
            "--project-root",
            str(tmp_path),
            "--repair-queue-jsonl",
            str(repair_queue),
            "--dry-run",
            "--out-summary",
            str(out_summary),
        ],
    )

    assert factory.main() == 0
    assert not out_summary.exists()
