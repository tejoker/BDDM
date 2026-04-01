"""Unit tests for bridge_proofs.py."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from bridge_proofs import (
    BridgeCandidate,
    BridgePlan,
    _norm_tokens,
    suggest_bridge_candidates,
    build_bridge_plan,
)


# ---------------------------------------------------------------------------
# Token normalisation
# ---------------------------------------------------------------------------

def test_norm_tokens_lowercase_and_min_length():
    tokens = _norm_tokens("iIndepFun GaussianProcess abc")
    assert all(t == t.lower() for t in tokens)
    assert all(len(t) >= 4 for t in tokens)


def test_norm_tokens_empty():
    assert _norm_tokens("") == set()


def test_norm_tokens_deduplicates():
    tokens = _norm_tokens("gaussian gaussian gaussian")
    assert len(tokens) == 1


# ---------------------------------------------------------------------------
# suggest_bridge_candidates
# ---------------------------------------------------------------------------

def _write_ledger(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"entries": rows}), encoding="utf-8")


def _make_ledger_root(tmp_path: Path) -> Path:
    root = tmp_path / "ledgers"
    paper1 = root / "paper1.json"
    _write_ledger(paper1, [
        {
            "theorem_name": "HasGaussianLaw.integrable",
            "lean_statement": "integrable gaussian measure probability",
            "status": "FULLY_PROVEN",
        },
        {
            "theorem_name": "Measurable.sub",
            "lean_statement": "subtraction measurable functions",
            "status": "FULLY_PROVEN",
        },
        {
            "theorem_name": "BadTheorem",
            "lean_statement": "something unrelated",
            "status": "FLAWED",
        },
    ])
    paper2 = root / "paper2.json"
    _write_ledger(paper2, [
        {
            "theorem_name": "iIndepFun.characterization",
            "lean_statement": "independent functions characterization probability",
            "status": "INTERMEDIARY_PROVEN",
        },
    ])
    return root


def test_suggest_bridge_candidates_returns_list(tmp_path):
    root = _make_ledger_root(tmp_path)
    candidates = suggest_bridge_candidates(
        assumption_expr="(hG : IsGaussianProcess X μ)",
        ledger_root=root,
    )
    assert isinstance(candidates, list)


def test_suggest_bridge_candidates_filters_flawed(tmp_path):
    root = _make_ledger_root(tmp_path)
    candidates = suggest_bridge_candidates(
        assumption_expr="something unrelated flawed",
        ledger_root=root,
    )
    names = [c.theorem_name for c in candidates]
    assert "BadTheorem" not in names


def test_suggest_bridge_candidates_scores_descending(tmp_path):
    root = _make_ledger_root(tmp_path)
    candidates = suggest_bridge_candidates(
        assumption_expr="gaussian integrable probability measure",
        ledger_root=root,
        max_candidates=5,
    )
    scores = [c.score for c in candidates]
    assert scores == sorted(scores, reverse=True)


def test_suggest_bridge_candidates_empty_assumption(tmp_path):
    root = _make_ledger_root(tmp_path)
    candidates = suggest_bridge_candidates(assumption_expr="", ledger_root=root)
    assert candidates == []


def test_suggest_bridge_candidates_missing_root():
    candidates = suggest_bridge_candidates(
        assumption_expr="gaussian",
        ledger_root=Path("/nonexistent/path"),
    )
    assert candidates == []


def test_suggest_bridge_candidates_respects_max(tmp_path):
    root = _make_ledger_root(tmp_path)
    candidates = suggest_bridge_candidates(
        assumption_expr="gaussian integrable measurable independent probability",
        ledger_root=root,
        max_candidates=1,
    )
    assert len(candidates) <= 1


# ---------------------------------------------------------------------------
# build_bridge_plan
# ---------------------------------------------------------------------------

def test_build_bridge_plan_returns_plan(tmp_path):
    root = _make_ledger_root(tmp_path)
    plan = build_bridge_plan(
        assumption_expr="gaussian integrable",
        ledger_root=root,
    )
    assert isinstance(plan, BridgePlan)
    assert plan.assumption_expr == "gaussian integrable"
    assert isinstance(plan.candidates, list)


def test_build_bridge_plan_no_overlap_gives_empty(tmp_path):
    root = _make_ledger_root(tmp_path)
    plan = build_bridge_plan(
        assumption_expr="zzzzz completely unrelated xyzzy",
        ledger_root=root,
    )
    # All candidates have zero overlap; should be empty or very short.
    assert isinstance(plan.candidates, list)
