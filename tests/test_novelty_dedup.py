from __future__ import annotations

import json
from pathlib import Path

from novelty_dedup import _ledger_paper_id, annotate_entries, record_from_row


def test_fingerprint_ignores_theorem_name_and_proof() -> None:
    row_a = {
        "theorem_name": "paper_a",
        "lean_statement": "theorem paper_a (n : Nat) : n = n := by rfl",
    }
    row_b = {
        "theorem_name": "paper_b",
        "lean_statement": "theorem paper_b (m : Nat) : m = m := by simp",
    }

    rec_a = record_from_row(row_a, paper_id="p1", source_index=0)
    rec_b = record_from_row(row_b, paper_id="p2", source_index=0)

    assert rec_a is not None
    assert rec_b is not None
    assert rec_a.canonical_statement == rec_b.canonical_statement
    assert rec_a.statement_fingerprint == rec_b.statement_fingerprint


def test_annotate_detects_exact_duplicate_in_corpus() -> None:
    existing = record_from_row(
        {
            "theorem_name": "prior_reflexive",
            "lean_statement": "theorem prior_reflexive (n : Nat) : n = n := by rfl",
        },
        paper_id="2401.00001",
        source_index=0,
    )
    assert existing is not None

    annotated, summary = annotate_entries(
        [
            {
                "theorem_name": "new_reflexive",
                "lean_statement": "theorem new_reflexive (m : Nat) : m = m := by simp",
            }
        ],
        paper_id="2401.00002",
        corpus_records=[existing],
        encoder_name="hash",
    )

    assert annotated[0]["novelty_status"] == "duplicate_in_corpus"
    assert annotated[0]["corpus_duplicate_status"] == "exact_duplicate"
    assert annotated[0]["mathlib_novelty_status"] == "unknown"
    assert annotated[0]["identity_status"] == "same_statement"
    assert annotated[0]["identity_evidence"]["canonical_fingerprint_match"] is True
    assert annotated[0]["novelty_evidence"]["matches"][0]["paper_id"] == "2401.00001"
    assert summary["counts"]["duplicate_in_corpus"] == 1
    assert summary["corpus_duplicate_status_counts"]["exact_duplicate"] == 1
    assert summary["identity_status_counts"]["same_statement"] == 1


def test_annotate_prefers_mathlib_overlap_from_seed(tmp_path: Path) -> None:
    seed = tmp_path / "mathlib_seed.jsonl"
    seed.write_text(
        json.dumps(
            {
                "theorem_name": "Nat.self_eq",
                "lean_statement": "theorem Nat.self_eq (n : Nat) : n = n := by rfl",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    annotated, summary = annotate_entries(
        [
            {
                "theorem_name": "paper_self_eq",
                "lean_statement": "theorem paper_self_eq (m : Nat) : m = m := by simp",
            }
        ],
        paper_id="2401.00003",
        mathlib_seed=seed,
        encoder_name="hash",
    )

    assert annotated[0]["novelty_status"] == "mathlib_overlap"
    assert annotated[0]["mathlib_novelty_status"] == "mathlib_overlap"
    assert annotated[0]["identity_status"] == "same_statement"
    assert annotated[0]["identity_evidence"]["mathlib_fingerprint_check"] is True
    assert annotated[0]["novelty_evidence"]["method"] == "mathlib_fingerprint"
    assert summary["counts"]["mathlib_overlap"] == 1


def test_annotate_keeps_new_statement_unknown_when_mathlib_checks_unavailable() -> None:
    annotated, summary = annotate_entries(
        [
            {
                "theorem_name": "paper_new",
                "lean_statement": "theorem paper_new (n : Nat) : n + 0 = n := by simp",
            }
        ],
        paper_id="2401.00007",
        encoder_name="hash",
    )

    assert annotated[0]["novelty_status"] == "unknown"
    assert annotated[0]["mathlib_novelty_status"] == "unknown"
    assert annotated[0]["identity_status"] == "unknown"
    assert annotated[0]["identity_evidence"]["human_review_required"] is True
    assert annotated[0]["novelty_evidence"]["method"] == "mathlib_checks_unavailable"
    assert annotated[0]["novelty_evidence"]["mathlib"]["checks_run"] == []
    assert "lean_exact_check_disabled" in annotated[0]["novelty_evidence"]["mathlib"]["unavailable_checks"]
    assert summary["counts"]["unknown"] == 1


def test_annotate_allows_new_candidate_after_empty_mathlib_seed_check(tmp_path: Path) -> None:
    seed = tmp_path / "mathlib_seed.jsonl"
    seed.write_text("", encoding="utf-8")

    annotated, summary = annotate_entries(
        [
            {
                "theorem_name": "paper_new",
                "lean_statement": "theorem paper_new (n : Nat) : n + 0 = n := by simp",
            }
        ],
        paper_id="2401.00008",
        mathlib_seed=seed,
        encoder_name="hash",
    )

    assert annotated[0]["novelty_status"] == "new_candidate"
    assert annotated[0]["mathlib_novelty_status"] == "new_candidate"
    assert annotated[0]["identity_status"] == "distinct_candidate"
    assert annotated[0]["identity_evidence"]["mathlib_fingerprint_check"] is True
    assert annotated[0]["novelty_evidence"]["mathlib"]["checks_run"] == ["mathlib_fingerprint"]
    assert summary["counts"]["new_candidate"] == 1


def test_annotate_detects_semantic_near_duplicate() -> None:
    existing = record_from_row(
        {
            "theorem_name": "prior_gaussian_integrable",
            "lean_statement": "theorem prior_gaussian_integrable : GaussianIntegrable X ∧ True := by sorry",
            "semantic_equivalence_artifact": {
                "normalized_natural_language_theorem": "Gaussian random variables are integrable.",
                "extracted_conclusion": "X is integrable",
            },
        },
        paper_id="2401.00004",
        source_index=0,
    )
    assert existing is not None

    annotated, summary = annotate_entries(
        [
            {
                "theorem_name": "gaussian_integrable",
                "lean_statement": "theorem gaussian_integrable : GaussianIntegrable X := by sorry",
                "semantic_equivalence_artifact": {
                    "normalized_natural_language_theorem": "Every Gaussian variable is integrable.",
                    "extracted_conclusion": "X is integrable",
                },
            }
        ],
        paper_id="2401.00005",
        corpus_records=[existing],
        semantic_threshold=0.25,
        encoder_name="hash",
    )

    assert annotated[0]["novelty_status"] == "semantic_near_duplicate"
    assert annotated[0]["corpus_duplicate_status"] == "semantic_near_duplicate"
    assert annotated[0]["identity_status"] == "near_duplicate"
    assert annotated[0]["identity_evidence"]["human_review_required"] is True
    assert annotated[0]["novelty_evidence"]["matches"][0]["score"] >= 0.25
    assert summary["counts"]["semantic_near_duplicate"] == 1


def test_annotate_unknown_when_statement_unavailable() -> None:
    annotated, summary = annotate_entries(
        [{"theorem_name": "missing_statement"}],
        paper_id="2401.00006",
        encoder_name="hash",
    )

    assert annotated[0]["novelty_status"] == "unknown"
    assert annotated[0]["identity_status"] == "unknown"
    assert annotated[0]["novelty_evidence"]["reason"] == "statement_unavailable"
    assert summary["counts"]["unknown"] == 1


def test_ledger_paper_id_recovers_nested_reproducibility_bundle_id(tmp_path: Path) -> None:
    ledger = tmp_path / "reproducibility" / "full_paper_reports" / "2604.21884" / "verification_ledger.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps({"entries": []}), encoding="utf-8")

    assert _ledger_paper_id(ledger, {"entries": []}) == "2604.21884"
