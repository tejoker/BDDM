from __future__ import annotations

import json
from pathlib import Path

from export_curated_corpus import curate_rows, export_curated_corpus


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_project_pins(root: Path) -> None:
    (root / "lean-toolchain").write_text("leanprover/lean4:v4.29.0-rc7\n", encoding="utf-8")
    (root / "lakefile.toml").write_text(
        '\n'.join(
            [
                'name = "desol-test"',
                "",
                "[[require]]",
                'name = "mathlib"',
                'git = "https://github.com/leanprover-community/mathlib4.git"',
                'rev = "abc123"',
            ]
        ),
        encoding="utf-8",
    )


def _base_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_version": "corpus_row.v1",
        "dataset_family": "desol_stable_corpus",
        "dataset_tier": "gold_proof",
        "training_tier": "gold_proof",
        "row_id": "r1",
        "arxiv_id": "2300.00001",
        "theorem_id": "thm:demo",
        "canonical_theorem_id": "demo",
        "toolchain_hash": "tool",
        "source_latex": "For every n, n = n.",
        "normalized_text": "For every n, n = n.",
        "lean_statement": "theorem demo (n : Nat) : n = n",
        "status": "FULLY_PROVEN",
        "proof_method": "lean_verified",
        "proof_text": "rfl",
        "trust_tier": "TRUST_MATHLIB",
        "source_span": {"span_confidence": "exact_extractor"},
        "source_span_quality": "extractor_native",
        "artifact_paths": {},
        "provenance": {},
        "axiom_debt": [],
        "gate_failures": [],
        "tier_evidence": {"gold_blockers": []},
        "statement_alignment_class": "exact",
        "alignment_confidence": 0.95,
        "alignment_tier": "alignment_gold",
        "alignment_review_required": False,
        "alignment_evidence": {"source_match": {"match_status": "matched"}},
        "mathlib_novelty_status": "unknown",
        "identity_status": "unknown",
        "identity_evidence": {"human_review_required": True},
    }
    row.update(overrides)
    return row


def test_curate_rows_splits_gold_alignment_silver_and_excluded() -> None:
    gold = _base_row()
    diagnostic = _base_row(
        row_id="r2",
        dataset_tier="diagnostic",
        training_tier="diagnostic",
        status="UNRESOLVED",
        proof_method="",
        proof_text="",
        tier_evidence={"gold_blockers": ["status_not_fully_proven"]},
        alignment_tier="alignment_review_required",
        source_span_quality="string_recovered",
        alignment_review_required=True,
        source_span={"span_confidence": "string_recovered_exact"},
    )

    surfaces, summary = curate_rows([gold, diagnostic], {"verified_proven_count": 1})

    assert len(surfaces["gold_proofs"]) == 1
    assert len(surfaces["alignment_gold_candidates"]) == 1
    assert len(surfaces["silver_process"]) == 1
    assert len(surfaces["excluded_rows"]) == 1
    assert surfaces["excluded_rows"][0]["curation"]["exclusion_reasons"]
    assert summary["gold_rows"] == 1
    assert summary["gold_not_greater_than_verified_proven"] is True
    assert summary["exclusion_reason_counts"]["not_gold_proof_tier"] == 1
    assert summary["alignment_review_needed"] == 1


def test_export_curated_corpus_writes_surfaces(tmp_path: Path) -> None:
    _write_project_pins(tmp_path)
    paper_id = "2300.00002"
    ledger = tmp_path / "reproducibility" / "full_paper_reports" / paper_id / "verification_ledger.json"
    _write_json(
        ledger,
        {
            "paper_id": paper_id,
            "entries": [
                {
                    "theorem_name": "demo",
                    "lean_statement": "theorem demo (n : Nat) : n = n",
                    "proof_text": "rfl",
                    "status": "FULLY_PROVEN",
                    "proof_method": "lean_verified",
                    "trust_class": "TRUST_MATHLIB",
                    "provenance": {"label": "thm:demo"},
                    "semantic_equivalence_artifact": {
                        "original_latex_theorem": "For every natural number n, n equals n.",
                        "normalized_natural_language_theorem": "For every natural number n, n equals n.",
                        "lean_statement": "theorem demo (n : Nat) : n = n",
                    },
                }
            ],
        },
    )
    result = export_curated_corpus(
        project_root=tmp_path,
        ledger_paths=[ledger.parent.parent],
        report_roots=[ledger.parent.parent],
        evidence_roots=[],
        out_dir=tmp_path / "curated",
    )

    assert result["gold_rows"] == 1
    assert result["gold_rows"] <= result["verified_proven"]
    gold_lines = (tmp_path / "curated" / "gold_proofs.jsonl").read_text(encoding="utf-8").splitlines()
    excluded_lines = (tmp_path / "curated" / "excluded_rows.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(gold_lines) == 1
    assert excluded_lines == []
