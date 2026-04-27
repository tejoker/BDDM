from __future__ import annotations

import json
from pathlib import Path

from bridge_proofs import (
    _build_proof_plan,
    _error_class_tactic_candidates,
    _synthesize_tactics_from_plan,
    _candidate_is_actionable,
    build_theorem_context_pack,
    compile_theorem_context_bundle,
    normalize_assumption_to_lean_statement,
    synthesize_actionable_goal,
    suggest_bridge_candidates,
    _goal_lane_allowed,
)


def test_normalize_assumption_to_lean_statement_from_expr() -> None:
    stmt = normalize_assumption_to_lean_statement(
        lean_expr="(h : x <= y)",
        lean_statement="",
        label="hxy",
    )
    assert stmt.startswith("theorem hxy :")
    assert "x <= y" in stmt


def test_normalize_assumption_to_lean_statement_rejects_non_prop() -> None:
    stmt = normalize_assumption_to_lean_statement(
        lean_expr="Real.exp",
        lean_statement="",
        label="exp",
    )
    assert stmt == ""


def test_candidate_is_actionable() -> None:
    assert _candidate_is_actionable("theorem t : 1 = 1") is True
    assert _candidate_is_actionable("lemma t : 1 = 1") is True
    assert _candidate_is_actionable("def f := 1") is False
    assert _candidate_is_actionable("theorem t := by sorry") is False


def test_suggest_bridge_candidates_filters_non_actionable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("bridge_proofs._HAS_RETRIEVAL", False)
    ledger_root = tmp_path / "ledgers"
    ledger_root.mkdir(parents=True)
    payload = {
        "entries": [
            {
                "theorem_name": "good_candidate",
                "status": "FULLY_PROVEN",
                "lean_statement": "theorem good_candidate : density_bound measure_x <= measure_y",
            },
            {
                "theorem_name": "bad_candidate",
                "status": "FULLY_PROVEN",
                "lean_statement": "Real.exp",
            },
        ]
    }
    (ledger_root / "p1.json").write_text(json.dumps(payload), encoding="utf-8")
    cands = suggest_bridge_candidates(
        assumption_expr="density_bound measure_x <= measure_y",
        ledger_root=ledger_root,
        max_candidates=5,
    )
    names = {c.theorem_name for c in cands}
    assert "good_candidate" in names
    assert "bad_candidate" not in names
    assert all(c.actionable for c in cands)


def test_suggest_bridge_candidates_template_fallback(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("bridge_proofs._HAS_RETRIEVAL", False)
    ledger_root = tmp_path / "ledgers"
    ledger_root.mkdir(parents=True)
    payload = {
        "entries": [
            {
                "theorem_name": "unusable_entry",
                "status": "FULLY_PROVEN",
                "lean_statement": "def foo := 1",
            },
        ]
    }
    (ledger_root / "p1.json").write_text(json.dumps(payload), encoding="utf-8")
    context_pack = build_theorem_context_pack(
        {
            "theorem_name": "target_t",
            "definitions": ["density_bound"],
            "notations": ["μ"],
            "lean_statement": "theorem target_t : p -> q := by sorry",
        }
    )
    cands = suggest_bridge_candidates(
        assumption_expr="x <= y",
        ledger_root=ledger_root,
        max_candidates=2,
        context_pack=context_pack,
        allow_template_fallback=True,
    )
    assert len(cands) == 2
    assert all(c.status == "TEMPLATE" for c in cands)
    assert all(c.actionable for c in cands)


def test_suggest_bridge_candidates_uses_retrieval_memory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("bridge_proofs._HAS_RETRIEVAL", False)
    ledger_root = tmp_path / "ledgers"
    ledger_root.mkdir(parents=True)
    payload = {
        "entries": [
            {
                "theorem_name": "weak",
                "status": "FULLY_PROVEN",
                "lean_statement": "theorem weak : density_bound_x <= density_bound_y",
            },
            {
                "theorem_name": "strong",
                "status": "FULLY_PROVEN",
                "lean_statement": "theorem strong : density_bound_x <= density_bound_y",
            },
        ]
    }
    (ledger_root / "p1.json").write_text(json.dumps(payload), encoding="utf-8")
    memory_path = tmp_path / "memory.json"
    memory_path.write_text(
        json.dumps({"theorems": {"strong": {"attempts": 4, "success": 4}}}),
        encoding="utf-8",
    )
    cands = suggest_bridge_candidates(
        assumption_expr="density_bound_x <= density_bound_y",
        ledger_root=ledger_root,
        max_candidates=2,
        retrieval_memory_path=memory_path,
    )
    assert len(cands) >= 1
    assert cands[0].theorem_name == "strong"


def test_synthesize_actionable_goal_builds_binders() -> None:
    stmt = synthesize_actionable_goal(
        lean_expr="(hxy : x + y <= z)",
        lean_statement="",
        label="hxy",
    )
    assert stmt.startswith("theorem hxy")
    assert ":" in stmt
    assert "<=" in stmt


def test_synthesize_actionable_goal_binds_uppercase_symbols() -> None:
    stmt = synthesize_actionable_goal(
        lean_expr="(hA : A > 0 ∧ B > 0)",
        lean_statement="",
        label="hA",
    )
    assert "(A : ℝ)" in stmt
    assert "(B : ℝ)" in stmt


def test_error_class_tactic_candidates_and_plan() -> None:
    tcs = _error_class_tactic_candidates("lean_typeclass", "theorem t : x = x")
    assert len(tcs) >= 1
    assert any("classical" in t for t in tcs)
    plan = _build_proof_plan(
        lean_statement="theorem t : ∀ x, x = x",
        error_class="lean_unsolved_goals",
        context_pack=None,
        decomposition={"objects": ["x"], "assumptions": ["x = x"]},
    )
    assert "derive_intermediate_lemma" in plan
    synth = _synthesize_tactics_from_plan(plan=plan, error_class="lean_unsolved_goals", lean_statement="theorem t : x = x")
    assert len(synth) >= 1


def test_compile_theorem_context_bundle_adds_free_symbol_vars() -> None:
    bundle = compile_theorem_context_bundle(
        lean_statement="theorem t (x : ℝ) : x + y <= z",
        context_pack=build_theorem_context_pack(
            {
                "theorem_name": "t",
                "definitions": ["def y := x + 1"],
                "notations": ["\\newcommand{\\R}{\\mathbb R}"],
            }
        ),
        decomposition={"objects": ["x", "y", "z"]},
    )
    prelude = str(bundle.get("prelude", ""))
    assert "variable (y : ℝ)" in prelude
    assert "variable (z : ℝ)" in prelude
    assert "variable (x : ℝ)" not in prelude


def test_goal_lane_rejects_malformed_schema_fragments() -> None:
    assert _goal_lane_allowed(
        lean_expr="[\\{ {ll -- schema_claim_1: \\\\ _1 _2, & . \\\\ . \\]",
        lean_statement="",
    ) is False
