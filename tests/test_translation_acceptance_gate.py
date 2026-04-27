from __future__ import annotations

from arxiv_to_lean import (
    _apply_translation_gate_to_ledger,
    _translation_repair_queue_rows,
    _write_lean_file,
    PipelineResult,
    TranslationAcceptanceGate,
    translation_acceptance_gate,
)
from pipeline_status import build_ledger_entry
from pipeline_status_models import FailureKind, ProofMethod, VerificationStatus
from statement_translator import TranslationResult
from theorem_extractor import TheoremEntry


def _entry(statement: str = "For all n, n = n.") -> TheoremEntry:
    return TheoremEntry(
        kind="theorem",
        name="thm:test",
        statement=statement,
        proof="",
        source_file="test.tex",
    )


def _tr(signature: str, *, validated: bool = True, last_error: str = "", **kwargs) -> TranslationResult:
    return TranslationResult(
        lean_signature=signature,
        validated=validated,
        rounds_used=1,
        last_error=last_error,
        confidence=0.9,
        **kwargs,
    )


def test_acceptance_gate_blocks_schema_placeholder() -> None:
    gate = translation_acceptance_gate(
        entry=_entry(),
        translation=_tr("theorem t (p_c1 : Prop) (h_c1 : p_c1) : p_c1 := by"),
    )

    assert gate.accepted is False
    assert gate.status == VerificationStatus.TRANSLATION_LIMITED
    assert gate.failure_kind == FailureKind.TRANSLATION_FAILURE
    assert gate.reason == "placeholder_or_schema_signature"


def test_acceptance_gate_blocks_regenerated_claim_atoms() -> None:
    gate = translation_acceptance_gate(
        entry=_entry(),
        translation=_tr("theorem thm_baseline_lift : ThmBaselineLiftRegeneratedStatement := by"),
    )

    assert gate.accepted is False
    assert gate.status == VerificationStatus.FLAWED
    assert gate.reason == "claim_atom_target"


def test_acceptance_gate_blocks_raw_latex_leakage() -> None:
    gate = translation_acceptance_gate(
        entry=_entry(),
        translation=_tr("theorem t : f ∈ C_T HSobolev s := by"),
    )

    assert gate.accepted is False
    assert gate.status == VerificationStatus.FLAWED
    assert gate.reason == "raw_latex_leak:bare_function_space_application"


def test_acceptance_gate_allows_typed_paper_operator_application() -> None:
    gate = translation_acceptance_gate(
        entry=_entry("The lifted stochastic objects satisfy the baseline identities."),
        translation=_tr("theorem t : Ψ1 = I_i ξ1 ∧ Ψ2 = I_i ξ2 := by"),
    )

    assert gate.accepted is True


def test_acceptance_gate_blocks_bare_paper_operator_self_identity() -> None:
    gate = translation_acceptance_gate(
        entry=_entry("Let Ψ_i = I_i(ξ_i) be the Duhamel lift."),
        translation=_tr("theorem t : I_i ξ1 = I_i ξ1 := by"),
    )

    assert gate.accepted is False
    assert gate.reason == "raw_latex_leak:bare_paper_operator_application"


def test_acceptance_gate_blocks_claim_shape_mismatch() -> None:
    gate = translation_acceptance_gate(
        entry=_entry("There exists a constant C such that P C."),
        translation=_tr("theorem t (n : ℕ) : n ≤ n := by"),
    )

    assert gate.accepted is False
    assert gate.status == VerificationStatus.FLAWED
    assert gate.failure_kind == FailureKind.FALSE_OR_AMBIGUOUS_STATEMENT
    assert gate.reason == "claim_shape_mismatch:exists->ineq"


def test_acceptance_gate_blocks_claim_copied_into_hypothesis() -> None:
    gate = translation_acceptance_gate(
        entry=_entry("The operator is bounded by C."),
        translation=_tr("theorem t (C : ℝ) (h_easy : C ≤ C) : C ≤ C := by"),
    )

    assert gate.accepted is False
    assert gate.status == VerificationStatus.FLAWED
    assert gate.failure_kind == FailureKind.FALSE_OR_AMBIGUOUS_STATEMENT
    assert gate.reason == "claim_copied_into_hypothesis:h_easy"


def test_acceptance_gate_blocks_fake_lean_placeholder() -> None:
    gate = translation_acceptance_gate(
        entry=_entry(),
        translation=_tr("theorem t : sorry_placeholder := by"),
    )

    assert gate.accepted is False
    assert gate.reason == "fake_lean_placeholder"


def test_acceptance_gate_blocks_relaxed_prop_identity() -> None:
    gate = translation_acceptance_gate(
        entry=_entry("Assume P. The substantive paper claim follows."),
        translation=_tr("theorem t (P : Prop) : P -> P := by"),
    )

    assert gate.accepted is False
    assert gate.reason == "relaxed_prop_identity"


def test_acceptance_gate_blocks_wrong_quantifier_erasure() -> None:
    gate = translation_acceptance_gate(
        entry=_entry("For all natural numbers n, the paper predicate P n holds."),
        translation=_tr("theorem t : P 0 := by"),
    )

    assert gate.accepted is False
    assert gate.reason == "wrong_quantifier:missing_forall"


def test_acceptance_gate_blocks_current_paper_notation_artifacts() -> None:
    for signature, expected in [
        ("theorem t : B_N^{i;j,k} = 0 := by", "raw_latex_leak:latex_superscript_artifact"),
        ("theorem t : Complex.abs x ≤ C := by", "raw_latex_leak:non_mathlib_complex_abs_notation"),
        ("theorem t (U : ℝ) : U ∈ C_TH ^ s1 := by", "raw_latex_leak:undefined_paper_function_space"),
    ]:
        gate = translation_acceptance_gate(entry=_entry(), translation=_tr(signature))

        assert gate.accepted is False
        assert gate.reason == expected


def test_acceptance_gate_marks_unknown_identifiers_translation_limited() -> None:
    gate = translation_acceptance_gate(
        entry=_entry(),
        translation=_tr(
            "theorem t : FooBar = FooBar := by",
            validated=False,
            last_error="unknown identifier 'FooBar'",
        ),
    )

    assert gate.accepted is False
    assert gate.status == VerificationStatus.TRANSLATION_LIMITED
    assert gate.failure_kind == FailureKind.MISSING_DEFINITION
    assert gate.reason == "unresolved_identifier_or_missing_paper_local_theory"


def test_acceptance_gate_allows_valid_statement() -> None:
    gate = translation_acceptance_gate(
        entry=_entry("For all n, n = n."),
        translation=_tr("theorem t (n : ℕ) : n = n := by"),
    )

    assert gate.accepted is True


def test_acceptance_gate_allows_non_current_paper_typed_ir_statement() -> None:
    gate = translation_acceptance_gate(
        entry=_entry("For all z, the second paper identity satisfies u z = u z."),
        translation=_tr("theorem rem (u : ℝ → ℝ) : ∀ z : ℝ, u z = u z := by"),
    )

    assert gate.accepted is True


def test_acceptance_gate_blocks_non_current_paper_claim_atoms() -> None:
    gate = translation_acceptance_gate(
        entry=_entry("The extracted 2304.09598 claim follows."),
        translation=_tr("theorem thm : PaperClaim230409598 := by"),
    )

    assert gate.accepted is False
    assert gate.reason == "paper_claim_atom"


def test_acceptance_gate_overrides_ledger_status() -> None:
    gate = translation_acceptance_gate(
        entry=_entry(),
        translation=_tr("theorem t : B_N^{i;j,k} = 0 := by"),
    )
    ledger = build_ledger_entry(
        theorem_name="t",
        lean_file="T.lean",
        lean_statement="theorem t : B_N^{i;j,k} = 0",
        proved=False,
        step_records=[],
        error_message=f"translation_acceptance_gate:{gate.reason}",
    )

    _apply_translation_gate_to_ledger(ledger, gate)

    assert ledger.status == VerificationStatus.FLAWED
    assert ledger.proof_method == ProofMethod.TRANSLATION_LIMITED
    assert "translation_acceptance_gate" in ledger.gate_failures


def test_write_lean_file_replaces_gate_rejected_validated_signature(tmp_path) -> None:
    out = tmp_path / "blocked.lean"
    gate = TranslationAcceptanceGate(
        accepted=False,
        reason="placeholder_or_schema_signature",
        status=VerificationStatus.TRANSLATION_LIMITED,
        failure_kind=FailureKind.TRANSLATION_FAILURE,
        gate_failures=("translation_acceptance_gate", "translation_limited_statement"),
    )
    result = PipelineResult(
        entry=_entry("For all n, n = n."),
        translation=_tr("theorem schema_translation (p_c1 : Prop) (h_c1 : p_c1) : p_c1 := by"),
        proved=False,
        proof_body="",
        skipped=True,
        prove_summary="translation_acceptance_gate:placeholder_or_schema_signature",
        translation_gate=gate,
    )

    _write_lean_file(out, source_label="2604.21884", results=[result], imports="import Mathlib")
    text = out.read_text(encoding="utf-8")

    assert "theorem thm_test : False := by sorry" in text
    assert "(p_c1 : Prop)" not in text


def test_rejected_translations_are_written_to_repair_queue_rows() -> None:
    gate = translation_acceptance_gate(
        entry=_entry("For all natural numbers n, the paper predicate P n holds."),
        translation=_tr("theorem t : P 0 := by"),
    )
    result = PipelineResult(
        entry=_entry("For all natural numbers n, the paper predicate P n holds."),
        translation=_tr("theorem t : P 0 := by"),
        proved=False,
        proof_body="",
        skipped=True,
        prove_summary=f"translation_acceptance_gate:{gate.reason}",
        translation_gate=gate,
    )

    rows = _translation_repair_queue_rows(paper_id="2604.21884", results=[result])

    assert len(rows) == 1
    assert rows[0]["gate_reason"] == "wrong_quantifier:missing_forall"
    assert rows[0]["lean_signature"] == "theorem t : P 0 := by"
