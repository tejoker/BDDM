"""Tests for the elaboration-validity gate added to the statement-repair worker.

Round-III regression: 42 LLM-repaired statements were promoted by the CoT
semantic judge but 40 of them failed `validation_gate_elaboration_failed`
inside the subsequent proof-search pass (0 closures on 21 retried rows). The
fix is an additive gate that re-uses
`prove_arxiv_batch._run_isolated_file_check` (the same probe the proof-search
loop applies) BEFORE the worker writes a candidate back to the ledger.

These tests are hermetic — they monkeypatch the isolated-file probe so they
never shell out to `lake env lean`.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

import run_statement_repair_worker as worker


# --- Fixtures ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_gate_cache() -> None:
    """Each test starts with a clean elaboration-gate cache."""
    worker._reset_elaboration_gate_cache()
    yield
    worker._reset_elaboration_gate_cache()


def _install_fake_modules(
    monkeypatch,
    *,
    isolated_check: Any,
    llm_repaired_decl: str = "theorem bad (n : Nat) : n = n",
    paper_theory_validation_ok: bool = True,
) -> dict[str, list]:
    """Wire up fake modules for llm_statement_repair, repair_bad_translations,
    and prove_arxiv_batch. Returns a dict of call recorders so tests can
    inspect invocation patterns."""
    isolated_calls: list[dict[str, Any]] = []

    def _wrapped_isolated_check(*, project_root, source_file, theorem_decl, timeout_s=45):
        isolated_calls.append(
            {
                "project_root": str(project_root),
                "source_file": str(source_file),
                "theorem_decl": theorem_decl,
            }
        )
        return isolated_check(theorem_decl)

    fake_prove_arxiv_batch = types.SimpleNamespace(
        _run_isolated_file_check=_wrapped_isolated_check,
    )
    monkeypatch.setitem(sys.modules, "prove_arxiv_batch", fake_prove_arxiv_batch)

    fake_llm_repair = types.SimpleNamespace(
        extract_paper_theory_hint=lambda *_args, **_kwargs: "",
        generate_llm_repair_candidate=lambda **_kwargs: {
            "repaired_decl": llm_repaired_decl,
            "ok": True,
        },
    )
    monkeypatch.setitem(sys.modules, "llm_statement_repair", fake_llm_repair)

    fake_repair = types.SimpleNamespace(
        validate_repair_candidate=lambda **_kwargs: {
            "ok": paper_theory_validation_ok,
            "error": "" if paper_theory_validation_ok else "paper_theory_validation_failed",
        },
    )
    monkeypatch.setitem(sys.modules, "repair_bad_translations", fake_repair)

    return {"isolated_calls": isolated_calls}


def _action_with_one_candidate() -> dict[str, Any]:
    return {
        "paper_id": "2304.09598",
        "theorem_ids": ["bad"],
        "row_ids": ["r1"],
        "artifacts": {"ledger": "ledger.json", "lean_file": "output/2304.09598.lean"},
        "source_contexts": [
            {
                "theorem_id": "bad",
                "theorem_name": "bad",
                "ledger_theorem_name": "bad",
                "lean_statement": "theorem bad : True",
                "source_latex": "A nontrivial source claim.",
                "normalized_text": "A nontrivial source claim.",
                "row_id": "r1",
            }
        ],
    }


def _payload_with_rule_based_candidate() -> dict[str, Any]:
    """Mimics the payload returned by `build_source_backed_repair_payload`,
    with one rule-based candidate already present."""
    return {
        "repair_candidates": [
            {
                "theorem_name": "bad",
                "repaired_decl": "theorem bad : True := by\n  sorry",
                "changes": ["source_backed_regeneration_v2"],
                "statement_repair_kind": "source_backed_statement_regeneration",
                "regeneration_protocol": "source_backed_v2",
                "lean_validation": {"ok": True, "error": ""},
                "repair_quality": {"ok": True, "blockers": [], "protocol": "source_backed_v2"},
                "source_statement_excerpt": "A nontrivial source claim.",
            }
        ],
        "candidate_counts": {"total": 1},
    }


# --- Direct gate tests ------------------------------------------------------


def test_elaboration_gate_passes_when_isolated_check_succeeds(monkeypatch, tmp_path) -> None:
    recorders = _install_fake_modules(monkeypatch, isolated_check=lambda decl: (True, ""))

    result = worker._run_elaboration_gate(
        project_root=tmp_path,
        paper_id="P1",
        theorem_name="bad",
        candidate_decl="theorem bad (n : Nat) : n = n",
    )

    assert result["ok"] is True
    assert result["error"] == ""
    assert result["cache_hit"] is False
    assert len(recorders["isolated_calls"]) == 1


def test_elaboration_gate_rejects_bracket_imbalance(monkeypatch, tmp_path) -> None:
    """Failing path: candidate has bracket imbalance -> rejected with diagnostics."""
    error_tail = "file_check_fail:error: unexpected end of input; expected ')'"
    _install_fake_modules(monkeypatch, isolated_check=lambda decl: (False, error_tail))

    result = worker._run_elaboration_gate(
        project_root=tmp_path,
        paper_id="P1",
        theorem_name="bad",
        candidate_decl="theorem bad : (n : Nat",
    )

    assert result["ok"] is False
    assert "file_check_fail" in result["error"]
    assert "unexpected end of input" in result["error"]


def test_elaboration_gate_rejects_unknown_identifier(monkeypatch, tmp_path) -> None:
    """Failing path: candidate references an unknown identifier -> rejected."""
    error_tail = "file_check_fail:error(lean.unknownIdentifier): Unknown constant `Frobnicate`"
    _install_fake_modules(monkeypatch, isolated_check=lambda decl: (False, error_tail))

    result = worker._run_elaboration_gate(
        project_root=tmp_path,
        paper_id="P1",
        theorem_name="bad",
        candidate_decl="theorem bad : Frobnicate = 0",
    )

    assert result["ok"] is False
    assert "unknownIdentifier" in result["error"] or "Unknown constant" in result["error"]


def test_elaboration_gate_caches_repeated_candidates(monkeypatch, tmp_path) -> None:
    recorders = _install_fake_modules(monkeypatch, isolated_check=lambda decl: (True, ""))

    first = worker._run_elaboration_gate(
        project_root=tmp_path,
        paper_id="P1",
        theorem_name="bad",
        candidate_decl="theorem bad : True",
    )
    second = worker._run_elaboration_gate(
        project_root=tmp_path,
        paper_id="P1",
        theorem_name="bad",
        candidate_decl="theorem bad : True",
    )

    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert second["ok"] is True
    # The underlying isolated probe must run only once for the same signature.
    assert len(recorders["isolated_calls"]) == 1


def test_elaboration_gate_empty_decl_is_rejected(monkeypatch, tmp_path) -> None:
    _install_fake_modules(monkeypatch, isolated_check=lambda decl: (True, ""))

    result = worker._run_elaboration_gate(
        project_root=tmp_path,
        paper_id="P1",
        theorem_name="bad",
        candidate_decl="   ",
    )

    assert result["ok"] is False
    assert "empty_decl" in result["error"]


# --- Wired into _overlay_llm_repair_candidates ------------------------------


def test_overlay_promotes_llm_candidate_when_elaboration_gate_passes(monkeypatch, tmp_path) -> None:
    _install_fake_modules(
        monkeypatch,
        isolated_check=lambda decl: (True, ""),
        llm_repaired_decl="theorem bad (n : Nat) : n = n := by\n  rfl",
    )

    payload = _payload_with_rule_based_candidate()
    action = _action_with_one_candidate()

    out = worker._overlay_llm_repair_candidates(
        payload,
        action=action,
        project_root=tmp_path,
        client=object(),  # truthy
        model="fake-model",
        validate_candidates=True,
    )

    cand = out["repair_candidates"][0]
    assert cand["statement_repair_kind"] == "llm_statement_repair"
    assert cand["repaired_decl"] == "theorem bad (n : Nat) : n = n := by\n  rfl"
    assert cand["elaboration_gate"]["ok"] is True
    assert out["llm_repair_overlay"]["accepted"] == 1
    assert out["llm_repair_overlay"]["elaboration_gate_rejected"] == 0


def test_overlay_rejects_candidate_when_elaboration_gate_fails(monkeypatch, tmp_path) -> None:
    """The KEY Round-III failure mode: CoT-judge / paper-theory validation
    accepts the candidate, but the elaboration gate against the canonical
    paper prelude rejects it. The worker must preserve the rule-based
    candidate as fallback and record `elaboration_gate_failed`."""
    error_tail = (
        "file_check_fail:error(lean.synthInstanceFailed): failed to synthesize instance"
        " of type class\n  LE ConjugacyClass"
    )
    _install_fake_modules(
        monkeypatch,
        isolated_check=lambda decl: (False, error_tail),
        llm_repaired_decl="theorem bad (alpha : ConjugacyClass) : alpha <= alpha",
        paper_theory_validation_ok=True,  # Paper-theory probe ACCEPTS it.
    )

    payload = _payload_with_rule_based_candidate()
    action = _action_with_one_candidate()

    out = worker._overlay_llm_repair_candidates(
        payload,
        action=action,
        project_root=tmp_path,
        client=object(),
        model="fake-model",
        validate_candidates=True,
    )

    cand = out["repair_candidates"][0]
    # LLM candidate REJECTED; rule-based candidate preserved untouched.
    assert cand["repaired_decl"] == "theorem bad : True := by\n  sorry"
    assert cand["statement_repair_kind"] == "source_backed_statement_regeneration"
    # Diagnostics recorded.
    llm_diag = cand["llm_repair"]
    assert llm_diag["ok"] is False
    assert llm_diag["reason"] == "llm_candidate_failed_elaboration_gate"
    assert "synthInstanceFailed" in llm_diag["elaboration_gate_failed"]
    assert llm_diag["elaboration_gate"]["ok"] is False
    # Counter incremented.
    assert out["llm_repair_overlay"]["elaboration_gate_rejected"] == 1
    assert out["llm_repair_overlay"]["accepted"] == 0
    assert out["llm_repair_overlay"]["rejected"] == 1


def test_overlay_skips_elaboration_gate_when_validate_candidates_false(monkeypatch, tmp_path) -> None:
    """The `validate_candidates=False` dry-run path must NOT shell out to lake.
    Cost-aware: the gate is gated by the existing flag."""
    recorders = _install_fake_modules(
        monkeypatch,
        isolated_check=lambda decl: (False, "file_check_fail:should_not_be_called"),
        llm_repaired_decl="theorem bad (n : Nat) : n = n := by\n  rfl",
    )

    payload = _payload_with_rule_based_candidate()
    action = _action_with_one_candidate()

    out = worker._overlay_llm_repair_candidates(
        payload,
        action=action,
        project_root=tmp_path,
        client=object(),
        model="fake-model",
        validate_candidates=False,
    )

    # Elaboration gate was NOT called (no isolated invocations).
    assert recorders["isolated_calls"] == []
    cand = out["repair_candidates"][0]
    # The LLM candidate is still promoted because the explicit dry-run mode
    # skips ALL validation (matching pre-existing semantics for
    # `validate_candidates=False`).
    assert cand["statement_repair_kind"] == "llm_statement_repair"


def test_overlay_skips_when_paper_theory_validation_already_failed(monkeypatch, tmp_path) -> None:
    """If the upstream `validate_repair_candidate` already rejected the
    candidate, the elaboration gate must not be reached — we want to spend the
    lake budget only on candidates that survive cheaper checks."""
    recorders = _install_fake_modules(
        monkeypatch,
        isolated_check=lambda decl: (True, ""),
        llm_repaired_decl="theorem bad : True := by trivial",
        paper_theory_validation_ok=False,
    )

    payload = _payload_with_rule_based_candidate()
    action = _action_with_one_candidate()

    out = worker._overlay_llm_repair_candidates(
        payload,
        action=action,
        project_root=tmp_path,
        client=object(),
        model="fake-model",
        validate_candidates=True,
    )

    # The earlier-stage paper-theory check rejected the candidate, so we
    # never reached the elaboration gate.
    assert recorders["isolated_calls"] == []
    cand = out["repair_candidates"][0]
    assert cand["llm_repair"]["reason"] == "llm_candidate_failed_lean_validation"
    # Rule-based candidate is preserved.
    assert cand["repaired_decl"] == "theorem bad : True := by\n  sorry"
