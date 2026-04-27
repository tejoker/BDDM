from __future__ import annotations

import json
from pathlib import Path

from pipeline_status import build_ledger_entry, infer_claim_equivalence
from pipeline_status_models import ClaimEquivalenceVerdict, ProvenanceLink, VerificationStatus


def test_claim_equivalence_requires_independent_semantic_evidence(monkeypatch) -> None:
    monkeypatch.delenv("DESOL_ALLOW_HEURISTIC_EQUIVALENCE", raising=False)

    verdict, notes = infer_claim_equivalence(
        translation_validated=True,
        translation_fidelity_score=0.95,
        status_alignment_score=0.95,
        uncertainty_flags=[],
        adversarial_flags=[],
        roundtrip_flags=[],
    )

    assert verdict == ClaimEquivalenceVerdict.UNCLEAR
    assert "insufficient_semantic_evidence" in notes


def test_claim_equivalence_accepts_explicit_independent_marker(monkeypatch) -> None:
    monkeypatch.delenv("DESOL_ALLOW_HEURISTIC_EQUIVALENCE", raising=False)

    verdict, notes = infer_claim_equivalence(
        translation_validated=True,
        translation_fidelity_score=0.90,
        status_alignment_score=0.90,
        uncertainty_flags=["semantic_equivalence:verified"],
        adversarial_flags=[],
        roundtrip_flags=[],
    )

    assert verdict == ClaimEquivalenceVerdict.EQUIVALENT
    assert "equivalent_independent_semantic_evidence" in notes


def test_fully_proven_requires_independent_semantic_artifact_evidence(monkeypatch) -> None:
    monkeypatch.setenv("DESOL_INDEPENDENT_VERIFY", "0")
    monkeypatch.setenv("DESOL_ALLOW_HEURISTIC_EQUIVALENCE", "1")

    entry = build_ledger_entry(
        theorem_name="t",
        lean_file="T.lean",
        lean_statement="theorem t (n : ℕ) : n = n",
        proved=True,
        step_records=[{"result": "proof-finished"}],
        translation_validated=True,
        translation_fidelity_score=0.95,
        status_alignment_score=0.95,
        dependency_trust_complete=True,
        reproducible_env=True,
        provenance=ProvenanceLink(paper_id="paper/1", section="1"),
        original_latex_theorem=r"For all $n$, $n=n$.",
        extracted_assumptions=[],
        extracted_conclusion="n = n",
    )

    assert entry.claim_equivalence_verdict == ClaimEquivalenceVerdict.EQUIVALENT
    assert entry.status == VerificationStatus.INTERMEDIARY_PROVEN
    assert "independent_semantic_equivalence_evidence" in entry.gate_failures
    artifact = entry.to_dict()["semantic_equivalence_artifact"]
    assert artifact["original_latex_theorem"] == r"For all $n$, $n=n$."
    assert artifact["lean_statement"] == "theorem t (n : ℕ) : n = n"
    assert artifact["equivalence_verdict"] == "equivalent"
    assert artifact["independent_semantic_evidence"] is False


def test_paper_theory_import_alone_is_not_axiom_debt(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DESOL_INDEPENDENT_VERIFY", "0")
    monkeypatch.setenv("DESOL_ALLOW_HEURISTIC_EQUIVALENCE", "1")
    lean_file = tmp_path / "Paper.lean"
    lean_file.write_text(
        "import Desol.PaperTheory.Paper_2604_21884\n\n"
        "theorem t : True := by\n  trivial\n",
        encoding="utf-8",
    )

    entry = build_ledger_entry(
        theorem_name="t",
        lean_file=str(lean_file),
        lean_statement="theorem t : True",
        proof_text="trivial",
        proved=True,
        step_records=[{"result": "proof-finished"}],
        error_message="",
        project_root=tmp_path,
        translation_validated=True,
        translation_fidelity_score=0.95,
        status_alignment_score=0.95,
        translation_uncertainty_flags=["semantic_equivalence:verified"],
        dependency_trust_complete=True,
        reproducible_env=True,
        provenance=ProvenanceLink(paper_id="2604.21884", section="1"),
    )

    assert entry.status == VerificationStatus.FULLY_PROVEN
    assert entry.validation_gates["no_paper_axiom_debt"] is True
    assert entry.axiom_debt == []
    assert entry.closure_claim == "lean_verified_without_paper_local_axioms"


def test_paper_axiom_symbol_downgrades_fully_proven_to_axiom_backed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DESOL_INDEPENDENT_VERIFY", "0")
    monkeypatch.setenv("DESOL_ALLOW_HEURISTIC_EQUIVALENCE", "1")
    lean_file = tmp_path / "Paper.lean"
    lean_file.write_text(
        "import Desol.PaperTheory.Paper_2604_21884\n\n"
        "theorem t : HSobolev 0 = HSobolev 0 := by\n  rfl\n",
        encoding="utf-8",
    )

    entry = build_ledger_entry(
        theorem_name="t",
        lean_file=str(lean_file),
        lean_statement="theorem t : HSobolev 0 = HSobolev 0",
        proof_text="rfl",
        proved=True,
        step_records=[{"result": "proof-finished"}],
        error_message="",
        project_root=tmp_path,
        translation_validated=True,
        translation_fidelity_score=0.95,
        status_alignment_score=0.95,
        translation_uncertainty_flags=["semantic_equivalence:verified"],
        dependency_trust_complete=True,
        reproducible_env=True,
        provenance=ProvenanceLink(paper_id="2604.21884", section="1"),
    )

    assert entry.status == VerificationStatus.AXIOM_BACKED
    assert entry.validation_gates["no_paper_axiom_debt"] is False
    assert "paper_definition_stub:HSobolev" in entry.axiom_debt
    assert entry.closure_claim == "proved_modulo_paper_local_axioms"


def test_paper_theory_manifest_distinguishes_definition_stub_debt(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DESOL_INDEPENDENT_VERIFY", "0")
    monkeypatch.setenv("DESOL_ALLOW_HEURISTIC_EQUIVALENCE", "1")
    theory_dir = tmp_path / "Desol" / "PaperTheory"
    theory_dir.mkdir(parents=True)
    (theory_dir / "Paper_2604_21884.manifest.json").write_text(
        json.dumps(
            {
                "symbols": [
                    {
                        "lean": "HSobolev",
                        "grounding": "definition_stub",
                        "declaration": "def HSobolev (_s : ℝ) : Set (ℝ → ℝ) := Set.univ",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    lean_file = tmp_path / "Paper.lean"
    lean_file.write_text("theorem t : HSobolev 0 = HSobolev 0 := by\n  rfl\n", encoding="utf-8")

    entry = build_ledger_entry(
        theorem_name="t",
        lean_file=str(lean_file),
        lean_statement="theorem t : HSobolev 0 = HSobolev 0",
        proof_text="rfl",
        proved=True,
        step_records=[{"result": "proof-finished"}],
        project_root=tmp_path,
        translation_validated=True,
        translation_fidelity_score=0.95,
        status_alignment_score=0.95,
        translation_uncertainty_flags=["semantic_equivalence:verified"],
        dependency_trust_complete=True,
        reproducible_env=True,
        provenance=ProvenanceLink(paper_id="2604.21884", section="1"),
    )

    assert "paper_definition_stub:HSobolev" in entry.axiom_debt
