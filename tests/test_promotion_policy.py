"""Tests for strict promotion gates in pipeline_status."""

from __future__ import annotations

from pipeline_status import (
    ProvenanceLink,
    VerificationStatus,
    build_ledger_entry,
)


def test_fully_proven_downgrades_without_independent_gate_evidence():
    entry = build_ledger_entry(
        theorem_name="demo_thm",
        lean_file="Desol/Basic.lean",
        lean_statement="theorem demo_thm : True := by trivial",
        proved=True,
        step_records=[{"step": 1, "attempt": 1, "result": "proof-finished", "tactic": "trivial", "detail": ""}],
        project_root=None,
        ledger_root=None,
    )
    assert entry.status == VerificationStatus.INTERMEDIARY_PROVEN
    assert entry.promotion_gate_passed is False
    assert "provenance_linked" in entry.gate_failures


def test_fully_proven_kept_when_all_gates_explicitly_passed():
    entry = build_ledger_entry(
        theorem_name="demo_thm_ok",
        lean_file="Desol/Basic.lean",
        lean_statement="theorem demo_thm_ok : True := by trivial",
        proved=True,
        step_records=[{"step": 1, "attempt": 1, "result": "proof-finished", "tactic": "trivial", "detail": ""}],
        provenance=ProvenanceLink(paper_id="paper/1", section="1", label="thmA", cited_refs=["ref1"]),
        translation_fidelity_score=0.95,
        status_alignment_score=0.96,
        dependency_trust_complete=True,
        reproducible_env=True,
        project_root=None,
        ledger_root=None,
    )
    assert entry.status == VerificationStatus.FULLY_PROVEN
    assert entry.promotion_gate_passed is True
    assert entry.gate_failures == []


def test_dependency_gate_failure_blocks_full_status():
    entry = build_ledger_entry(
        theorem_name="demo_dep_fail",
        lean_file="Desol/Basic.lean",
        lean_statement="theorem demo_dep_fail : True := by trivial",
        proved=True,
        step_records=[{"step": 1, "attempt": 1, "result": "proof-finished", "tactic": "trivial", "detail": ""}],
        provenance=ProvenanceLink(paper_id="paper/1", section="1", label="thmA", cited_refs=["ref1"]),
        translation_fidelity_score=0.95,
        status_alignment_score=0.96,
        dependency_trust_complete=False,
        reproducible_env=True,
        project_root=None,
        ledger_root=None,
    )
    assert entry.status == VerificationStatus.INTERMEDIARY_PROVEN
    assert entry.promotion_gate_passed is False
    assert "dependency_trust_complete" in entry.gate_failures
