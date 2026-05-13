"""Regression tests for the silent-state-corruption bug where a prove-loop
ledger write-back wipes review-evidence fields (reviewed_*, review_provenance,
reviewer_type, review_policy, claim_equivalence_verdict='equivalent', and the
review-derived validation_gates sub-flags).

Failure mode (observed in Round-III closure): a validation_gate_elaboration
failure routes through build_ledger_entry → upsert_ledger_entry, which
rebuilds the row from scratch — dropping every reviewed_* field the CoT
bridge / apply_reviews_to_ledger.py wrote earlier. Net effect: the next
unattended run silently destroys honestly-collected review evidence.

Fix: pipeline_status._preserve_review_evidence(old, new) copies the
review-evidence subset from the old entry into the new entry whenever
upsert_ledger_entry replaces an existing row (or _sync_base_alias_entry
overwrites one in prove_arxiv_batch.py).

These tests are hermetic — they use tmp_path-rooted ledgers, no Lean/REPL,
no Mistral, no HTTP. Safe to run in the fast suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline_status import (
    _preserve_review_evidence,
    build_ledger_entry,
    load_ledger,
    save_ledger,
    upsert_ledger_entry,
)
from pipeline_status_models import FailureKind, FailureOrigin


PAPER_ID = "9999.99999"
THM_NAME = "ArxivPaper.lem_test"


# ---------------------------------------------------------------------------
# Unit tests for _preserve_review_evidence
# ---------------------------------------------------------------------------


def test_preserve_review_evidence_copies_top_level_review_fields():
    old = {
        "theorem_name": THM_NAME,
        "status": "FULLY_PROVEN",
        "reviewed_equivalence_verdict": "equivalent",
        "reviewed_statement_alignment_class": "EXACT",
        "reviewed_alignment_confidence": 0.92,
        "reviewer_type": "hybrid",
        "review_policy": "release_eligible",
        "review_provenance": {"reviewed_by": "hybrid:cot-bridge", "reviewed_at": "2026-05-13T00:00:00Z"},
        "reviewed_by": "hybrid:cot-bridge",
        "reviewed_at": "2026-05-13T00:00:00Z",
    }
    new = {
        "theorem_name": THM_NAME,
        "status": "FLAWED",
        "error_message": "validation_gate_elaboration_failed:Lean error",
    }

    merged = _preserve_review_evidence(old, new)

    # Every top-level review field must survive.
    assert merged["reviewed_equivalence_verdict"] == "equivalent"
    assert merged["reviewed_statement_alignment_class"] == "EXACT"
    assert merged["reviewed_alignment_confidence"] == pytest.approx(0.92)
    assert merged["reviewer_type"] == "hybrid"
    assert merged["review_policy"] == "release_eligible"
    assert merged["review_provenance"] == {
        "reviewed_by": "hybrid:cot-bridge",
        "reviewed_at": "2026-05-13T00:00:00Z",
    }
    assert merged["reviewed_by"] == "hybrid:cot-bridge"
    assert merged["reviewed_at"] == "2026-05-13T00:00:00Z"
    # Proof-search fields from the new entry must still win.
    assert merged["status"] == "FLAWED"
    assert "validation_gate_elaboration_failed" in merged["error_message"]


def test_preserve_review_evidence_does_not_invent_fields():
    """A row with NO prior review evidence stays empty (no spurious keys)."""
    old = {
        "theorem_name": THM_NAME,
        "status": "UNRESOLVED",
    }
    new = {
        "theorem_name": THM_NAME,
        "status": "FLAWED",
        "error_message": "validation_gate_elaboration_failed",
    }

    merged = _preserve_review_evidence(old, new)

    for field in (
        "reviewed_equivalence_verdict",
        "reviewed_statement_alignment_class",
        "reviewed_alignment_confidence",
        "reviewer_type",
        "review_policy",
        "review_provenance",
        "reviewed_by",
        "reviewed_at",
        "claim_equivalence_verdict",
    ):
        assert field not in merged, f"unexpected synthesis of {field}"
    assert "validation_gates" not in merged


def test_preserve_review_evidence_does_not_overwrite_new_values():
    """When the new entry has its own review fields, do not overwrite."""
    old = {
        "reviewed_equivalence_verdict": "equivalent",
        "reviewer_type": "hybrid",
    }
    new = {
        "reviewed_equivalence_verdict": "not_equivalent",  # fresh ground truth
        "reviewer_type": "human",
    }

    merged = _preserve_review_evidence(old, new)

    assert merged["reviewed_equivalence_verdict"] == "not_equivalent"
    assert merged["reviewer_type"] == "human"


def test_preserve_review_evidence_carries_equivalent_claim_verdict():
    """An old 'equivalent' claim_equivalence_verdict survives; weaker
    verdicts (unclear/not_equivalent) on the old row do NOT propagate over
    a new value."""
    old = {"claim_equivalence_verdict": "equivalent"}
    new = {"claim_equivalence_verdict": "unclear"}
    merged = _preserve_review_evidence(old, new)
    assert merged["claim_equivalence_verdict"] == "equivalent"

    # Opposite direction: old=unclear, new=unclear → leave new alone
    old2 = {"claim_equivalence_verdict": "unclear"}
    new2 = {"claim_equivalence_verdict": "unclear"}
    merged2 = _preserve_review_evidence(old2, new2)
    assert merged2["claim_equivalence_verdict"] == "unclear"

    # If the new entry already says equivalent, leave it alone.
    old3 = {"claim_equivalence_verdict": "equivalent"}
    new3 = {"claim_equivalence_verdict": "equivalent"}
    merged3 = _preserve_review_evidence(old3, new3)
    assert merged3["claim_equivalence_verdict"] == "equivalent"


def test_preserve_review_evidence_preserves_review_validation_gates():
    """The review round-trip flips claim_equivalent /
    independent_semantic_equivalence_evidence / statement_alignment_exact
    in validation_gates. A fresh proof-search entry must not knock those
    True flips back to False."""
    old = {
        "validation_gates": {
            "claim_equivalent": True,
            "independent_semantic_equivalence_evidence": True,
            "statement_alignment_exact": True,
            "statement_alignment_not_unrelated": True,
            "translation_fidelity_ok": False,
        }
    }
    new = {
        "validation_gates": {
            "translation_fidelity_ok": False,
            "status_alignment_ok": False,
        }
    }

    merged = _preserve_review_evidence(old, new)

    assert merged["validation_gates"]["claim_equivalent"] is True
    assert merged["validation_gates"]["independent_semantic_equivalence_evidence"] is True
    assert merged["validation_gates"]["statement_alignment_exact"] is True
    assert merged["validation_gates"]["statement_alignment_not_unrelated"] is True
    # Proof-search gates win.
    assert merged["validation_gates"]["translation_fidelity_ok"] is False
    assert merged["validation_gates"]["status_alignment_ok"] is False


def test_preserve_review_evidence_handles_none_or_empty_old():
    """No-op when old_entry is None / missing / wrong type."""
    new = {"status": "FLAWED"}
    assert _preserve_review_evidence(None, new) is new
    assert _preserve_review_evidence({}, new)["status"] == "FLAWED"


def test_preserve_review_evidence_ignores_empty_review_strings():
    """Old fields that are empty strings / empty dicts should not overwrite
    a missing field — they carry no evidence."""
    old = {
        "reviewed_equivalence_verdict": "",
        "review_provenance": {},
        "reviewed_alignment_confidence": None,
    }
    new = {"status": "FLAWED"}
    merged = _preserve_review_evidence(old, new)
    assert "reviewed_equivalence_verdict" not in merged
    assert "review_provenance" not in merged
    assert "reviewed_alignment_confidence" not in merged


# ---------------------------------------------------------------------------
# Integration tests against upsert_ledger_entry (the real call site)
# ---------------------------------------------------------------------------


def _seed_reviewed_ledger(output_root: Path) -> dict:
    """Write a ledger row that already carries hybrid-review evidence
    (mirroring what apply_reviews_to_ledger.py / the CoT bridge produces)."""
    row = {
        "theorem_name": THM_NAME,
        "lean_file": "Desol/PaperTheory/Paper_9999_99999.lean",
        "lean_statement": "theorem ArxivPaper.lem_test : True := by trivial",
        "status": "UNRESOLVED",
        "proved": False,
        "reviewed_equivalence_verdict": "equivalent",
        "reviewed_statement_alignment_class": "EXACT",
        "reviewed_alignment_confidence": 0.88,
        "reviewer_type": "hybrid",
        "review_policy": "release_eligible",
        "review_provenance": {
            "reviewed_by": "hybrid:cot-bridge",
            "reviewed_at": "2026-05-13T00:00:00Z",
            "artifact_id": "cot-bridge:auto_alignment",
        },
        "reviewed_by": "hybrid:cot-bridge",
        "reviewed_at": "2026-05-13T00:00:00Z",
        "claim_equivalence_verdict": "equivalent",
        "validation_gates": {
            "claim_equivalent": True,
            "independent_semantic_equivalence_evidence": True,
            "statement_alignment_exact": True,
        },
    }
    save_ledger(PAPER_ID, [row], output_root=output_root)
    return row


def _build_validation_gate_failure_entry():
    """Mirror prove_arxiv_batch.py's validation_gate_elaboration_failed path."""
    return build_ledger_entry(
        theorem_name=THM_NAME,
        lean_file="Desol/PaperTheory/Paper_9999_99999.lean",
        lean_statement="theorem ArxivPaper.lem_test : True := by trivial",
        proved=False,
        step_records=[],
        proof_text="",
        error_message="validation_gate_elaboration_failed:Lean: unknown identifier 'foo'",
        proof_mode="validation-gate",
        rounds_used=0,
        time_s=0.0,
        had_exception=False,
        failure_kind=FailureKind.ELABORATION_FAILURE,
    )


def test_upsert_preserves_review_fields_on_validation_gate_failure(tmp_path):
    """The bug: prove_arxiv_batch.py's validation-gate-failure writeback
    used to destroy reviewed_* fields. After the fix, all review evidence
    survives the FLAWED/UNRESOLVED writeback."""
    output_root = tmp_path / "verification_ledgers"
    seed = _seed_reviewed_ledger(output_root)
    new_entry = _build_validation_gate_failure_entry()
    new_entry.failure_origin = FailureOrigin.FORMALIZATION_ERROR

    upsert_ledger_entry(PAPER_ID, new_entry, output_root=output_root)

    rows = load_ledger(PAPER_ID, output_root=output_root)
    assert len(rows) == 1
    row = rows[0]

    # Proof-search outputs win.
    assert row["status"] in ("FLAWED", "UNRESOLVED")
    assert "validation_gate_elaboration_failed" in str(row.get("error_message", ""))
    # Review evidence MUST be preserved.
    assert row["reviewed_equivalence_verdict"] == "equivalent"
    assert row["reviewed_statement_alignment_class"] == "EXACT"
    assert row["reviewed_alignment_confidence"] == pytest.approx(0.88)
    assert row["reviewer_type"] == "hybrid"
    assert row["review_policy"] == "release_eligible"
    assert row["review_provenance"]["reviewed_by"] == "hybrid:cot-bridge"
    assert row["claim_equivalence_verdict"] == "equivalent"
    assert row["validation_gates"]["claim_equivalent"] is True
    assert row["validation_gates"]["independent_semantic_equivalence_evidence"] is True
    assert row["validation_gates"]["statement_alignment_exact"] is True
    # Sanity: seed and post-write share the persistent fields.
    for k in (
        "reviewed_equivalence_verdict",
        "reviewer_type",
        "review_policy",
    ):
        assert row[k] == seed[k]


def test_upsert_preserves_review_fields_when_proof_succeeds(tmp_path):
    """Edge case: when proof succeeds and the row legitimately promotes,
    the review fields STILL stay (they are orthogonal to proof success)."""
    output_root = tmp_path / "verification_ledgers"
    _seed_reviewed_ledger(output_root)

    new_entry = build_ledger_entry(
        theorem_name=THM_NAME,
        lean_file="Desol/PaperTheory/Paper_9999_99999.lean",
        lean_statement="theorem ArxivPaper.lem_test : True := by trivial",
        proved=True,
        step_records=[{"step": 1, "attempt": 1, "result": "proof-finished", "tactic": "trivial", "detail": ""}],
        proof_text="by trivial",
        error_message="",
        proof_mode="full-draft",
        rounds_used=1,
        time_s=0.1,
        had_exception=False,
    )

    upsert_ledger_entry(PAPER_ID, new_entry, output_root=output_root)

    rows = load_ledger(PAPER_ID, output_root=output_root)
    assert len(rows) == 1
    row = rows[0]

    # The row promoted past UNRESOLVED.
    assert row["status"] in ("FULLY_PROVEN", "INTERMEDIARY_PROVEN", "AXIOM_BACKED")
    # Review evidence preserved across the promotion.
    assert row["reviewed_equivalence_verdict"] == "equivalent"
    assert row["reviewer_type"] == "hybrid"
    assert row["review_policy"] == "release_eligible"
    assert row["review_provenance"]["reviewed_by"] == "hybrid:cot-bridge"


def test_upsert_no_review_fields_stays_clean(tmp_path):
    """A ledger row with no prior review evidence must not gain fabricated
    reviewed_* keys after a prove-loop writeback."""
    output_root = tmp_path / "verification_ledgers"
    seed_row = {
        "theorem_name": THM_NAME,
        "lean_file": "Desol/PaperTheory/Paper_9999_99999.lean",
        "lean_statement": "theorem ArxivPaper.lem_test : True := by trivial",
        "status": "UNRESOLVED",
        "proved": False,
    }
    save_ledger(PAPER_ID, [seed_row], output_root=output_root)

    new_entry = _build_validation_gate_failure_entry()
    upsert_ledger_entry(PAPER_ID, new_entry, output_root=output_root)

    rows = load_ledger(PAPER_ID, output_root=output_root)
    assert len(rows) == 1
    row = rows[0]
    for field in (
        "reviewed_equivalence_verdict",
        "reviewed_statement_alignment_class",
        "reviewed_alignment_confidence",
        "reviewer_type",
        "review_policy",
        "review_provenance",
    ):
        assert field not in row, f"unexpected synthesis of {field} on un-reviewed row"


def test_upsert_round_iii_replay_preserves_19_of_19(tmp_path):
    """Targeted regression: simulate the exact Round-III closure failure
    (19 rows with reviewed_equivalence_verdict='equivalent' all hit
    validation_gate_elaboration_failed). After the fix, all 19 keep their
    review evidence; previously they would all lose it."""
    output_root = tmp_path / "verification_ledgers"
    seeded_rows = []
    for i in range(19):
        seeded_rows.append({
            "theorem_name": f"ArxivPaper.lem_{i:02d}",
            "lean_file": "Desol/PaperTheory/Paper_9999_99999.lean",
            "lean_statement": f"theorem ArxivPaper.lem_{i:02d} : True := by trivial",
            "status": "UNRESOLVED",
            "proved": False,
            "reviewed_equivalence_verdict": "equivalent",
            "reviewed_statement_alignment_class": "EXACT",
            "reviewed_alignment_confidence": 0.9,
            "reviewer_type": "hybrid",
            "review_policy": "release_eligible",
            "review_provenance": {"reviewed_by": "hybrid:cot-bridge"},
        })
    save_ledger(PAPER_ID, seeded_rows, output_root=output_root)

    for i in range(19):
        new_entry = build_ledger_entry(
            theorem_name=f"ArxivPaper.lem_{i:02d}",
            lean_file="Desol/PaperTheory/Paper_9999_99999.lean",
            lean_statement=f"theorem ArxivPaper.lem_{i:02d} : True := by trivial",
            proved=False,
            step_records=[],
            error_message="validation_gate_elaboration_failed:Lean error",
            proof_mode="validation-gate",
            failure_kind=FailureKind.ELABORATION_FAILURE,
        )
        upsert_ledger_entry(PAPER_ID, new_entry, output_root=output_root)

    rows = load_ledger(PAPER_ID, output_root=output_root)
    assert len(rows) == 19
    preserved = sum(
        1 for r in rows
        if r.get("reviewed_equivalence_verdict") == "equivalent"
        and r.get("reviewer_type") == "hybrid"
        and r.get("review_policy") == "release_eligible"
    )
    assert preserved == 19, f"expected all 19 rows preserved, got {preserved}"
