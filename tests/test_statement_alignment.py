from __future__ import annotations

from pipeline_status import build_ledger_entry
from pipeline_status_models import ClaimEquivalenceVerdict, ProvenanceLink, StatementAlignmentClass
from statement_alignment import classify_statement_alignment, normalize_latex_statement


def test_normalize_latex_statement_prefers_schema() -> None:
    normalized = normalize_latex_statement(
        r"\label{thm:x} If $x>0$, then $x^2>0$.",
        {"assumptions": [r"$x>0$"], "claim": r"$x^2>0$"},
    )

    assert "Assumptions:" in normalized
    assert "Conclusion:" in normalized
    assert "label" not in normalized


def test_alignment_exact_requires_independent_evidence() -> None:
    decision = classify_statement_alignment(
        paper_id="2401.00001",
        theorem_name="t",
        original_latex_theorem=r"For all $n$, $n=n$.",
        normalized_paper_text="Conclusion: n = n",
        extracted_assumptions=[],
        extracted_conclusion="n = n",
        lean_statement="theorem t (n : Nat) : n = n",
        equivalence_verdict=ClaimEquivalenceVerdict.EQUIVALENT,
        claim_equivalence_notes=["equivalent_independent_semantic_evidence"],
        uncertainty_flags=["semantic_equivalence:verified"],
        adversarial_flags=[],
        roundtrip_flags=[],
        context_pack={"source_span_id": "srcspan_abc", "source_char_range": [0, 30]},
        translation_fidelity_score=0.95,
        status_alignment_score=0.95,
        translation_validated=True,
        independent_semantic_evidence=True,
        canonical_theorem_id="cth_test",
    )

    assert decision.alignment_class == StatementAlignmentClass.EXACT
    assert decision.paper_statement_id.startswith("pstmt_")
    assert decision.alignment_pair_id.startswith("align_")


def test_alignment_diagnostic_for_trivial_target() -> None:
    decision = classify_statement_alignment(
        paper_id="2401.00001",
        theorem_name="bad",
        original_latex_theorem="The operator is bounded.",
        normalized_paper_text="The operator is bounded.",
        extracted_assumptions=[],
        extracted_conclusion="operator is bounded",
        lean_statement="theorem bad : False",
        equivalence_verdict=ClaimEquivalenceVerdict.UNCLEAR,
        claim_equivalence_notes=["insufficient_semantic_evidence"],
        uncertainty_flags=["trivial_target"],
        adversarial_flags=[],
        roundtrip_flags=[],
        context_pack={},
        translation_fidelity_score=0.2,
        status_alignment_score=0.2,
        translation_validated=False,
        independent_semantic_evidence=False,
    )

    assert decision.alignment_class == StatementAlignmentClass.DIAGNOSTIC
    assert decision.diagnostic is True


def test_build_ledger_entry_emits_alignment_metadata(monkeypatch) -> None:
    monkeypatch.setenv("DESOL_INDEPENDENT_VERIFY", "0")
    entry = build_ledger_entry(
        theorem_name="t",
        lean_file="T.lean",
        lean_statement="theorem t (n : Nat) : n = n",
        proved=True,
        step_records=[{"result": "proof-finished"}],
        translation_validated=True,
        translation_fidelity_score=0.95,
        status_alignment_score=0.95,
        translation_uncertainty_flags=["semantic_equivalence:verified"],
        dependency_trust_complete=True,
        reproducible_env=True,
        provenance=ProvenanceLink(paper_id="2401.00001", section="1", label="thm:t"),
        context_pack={"source_span_id": "srcspan_abc", "source_char_range": [0, 30]},
        original_latex_theorem=r"For all $n$, $n=n$.",
        extracted_assumptions=[],
        extracted_conclusion="n = n",
    )
    row = entry.to_dict()

    assert row["statement_alignment_class"] == "exact"
    assert row["paper_statement_id"].startswith("pstmt_")
    assert row["canonical_theorem_id"].startswith("cth_")
    assert row["alignment_pair_id"].startswith("align_")
    assert row["semantic_equivalence_artifact"]["alignment_decision"]["alignment_class"] == "exact"
