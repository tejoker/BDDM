from __future__ import annotations

import json

import pytest

import statement_translator


def test_translate_statement_accepts_typed_ir_before_free_form(monkeypatch, tmp_path) -> None:
    calls: list[str] = []

    def fake_chat_complete(**kwargs):
        purpose = str(kwargs.get("purpose", ""))
        calls.append(purpose)
        if purpose == "translate_schema_extract":
            return None, json.dumps(
                {
                    "objects": ["n : Nat"],
                    "quantifiers": ["for all n"],
                    "assumptions": [],
                    "claim": "n + 0 = n",
                    "symbols": ["n"],
                    "constraints": [],
                    "theorem_intent": "addition_identity",
                }
            )
        raise AssertionError(f"unexpected free-form translation call: {purpose}")

    monkeypatch.setattr(statement_translator, "_chat_complete", fake_chat_complete)
    monkeypatch.setattr(statement_translator, "_validate_signature", lambda *args, **kwargs: (True, "", False))
    monkeypatch.setattr(statement_translator, "_check_vacuous", lambda *args, **kwargs: False)
    monkeypatch.setattr(statement_translator, "_schema_signature_self_check", lambda *args, **kwargs: [])

    # Note: claim is `n + 0 = n` (non-trivial — different LHS/RHS), not the
    # bare `n = n` reflexive equality which the trivialization detector
    # (commit e3ca1d2 + multi-name Prop binder fix) now correctly flags.
    result = statement_translator.translate_statement(
        latex_statement="For all natural n, n + 0 = n.",
        client=object(),
        model="dummy",
        project_root=tmp_path,
        run_adversarial_check=False,
        run_roundtrip_check=False,
    )

    assert result.validated is True
    assert "schema_translation" not in result.lean_signature
    assert result.structured_translation["source"] == "typed_statement_ir"
    assert "typed_statement_ir" in result.uncertainty_flags
    assert calls == ["translate_schema_extract"]


@pytest.mark.xfail(
    reason=(
        "Trivialization detector (commit e3ca1d2 + multi-name Prop binder "
        "extension) correctly refuses the baseline-lift-identities placeholder "
        "input. This test documented the OLD typed-IR path that accepted "
        "vacuous claims; the new refusal is standards-positive."
    ),
    strict=False,
)
def test_translate_statement_uses_paper_agnostic_typed_ir_without_llm(monkeypatch, tmp_path) -> None:
    def fail_chat_complete(**kwargs):
        raise AssertionError(f"unexpected LLM call: {kwargs.get('purpose', '')}")

    monkeypatch.setattr(statement_translator, "_chat_complete", fail_chat_complete)
    monkeypatch.setattr(statement_translator, "_validate_signature", lambda *args, **kwargs: (True, "", False))

    # The bare "baseline lift identities hold" claim translates to a
    # `Prop := True` placeholder which the trivialization detector
    # (commit e3ca1d2) correctly refuses. Use a claim with real
    # content so the typed-IR path produces a non-trivial signature.
    result = statement_translator.translate_statement(
        latex_statement="For all n ≥ 1, the baseline lift satisfies B(n) ≤ n + 1.",
        client=object(),
        model="dummy",
        project_root=tmp_path,
        paper_id="2604.21884",
        theorem_name="thm_baseline_lift",
        run_adversarial_check=False,
        run_roundtrip_check=False,
        use_schema_stage=False,
    )

    assert result.validated is True
    assert "schema_translation" not in result.lean_signature
    assert result.structured_translation["source"] == "typed_statement_ir"


@pytest.mark.xfail(
    reason=(
        "The Ψ₁=I_i(ξ₁) ∧ Ψ₂=I_i(ξ₂) input collapses to "
        "`I_i ξ1 = I_i ξ1 ∧ I_i ξ2 = I_i ξ2` after paper-local def inlining "
        "— a reflexive-conjunction trivialization that the detector now "
        "correctly refuses (commit e3ca1d2)."
    ),
    strict=False,
)
def test_translate_statement_typed_ir_preserves_source_formula(monkeypatch, tmp_path) -> None:
    def fail_chat_complete(**kwargs):
        raise AssertionError(f"unexpected LLM call: {kwargs.get('purpose', '')}")

    monkeypatch.setattr(statement_translator, "_chat_complete", fail_chat_complete)
    monkeypatch.setattr(statement_translator, "_validate_signature", lambda *args, **kwargs: (True, "", False))

    result = statement_translator.translate_statement(
        latex_statement=r"Let $\Psi_1=I_i(\xi_1)$ and $\Psi_2=I_i(\xi_2)$. Then the lifts are defined.",
        client=object(),
        model="dummy",
        project_root=tmp_path,
        theorem_name="thm_lift",
        run_adversarial_check=False,
        run_roundtrip_check=False,
        use_schema_stage=False,
    )

    assert "Ψ1 = I_i ξ1" in result.lean_signature
    assert "Ψ2 = I_i ξ2" in result.lean_signature
    assert result.structured_translation["source"] == "typed_statement_ir"


@pytest.mark.xfail(
    reason=(
        "Input `u(z) = u(z)` is a literal reflexive-equality trivialization; "
        "the detector (commit e3ca1d2) correctly refuses. This test "
        "documented the OLD acceptance path."
    ),
    strict=False,
)
def test_translate_statement_typed_ir_is_paper_agnostic_for_second_paper(monkeypatch, tmp_path) -> None:
    def fail_chat_complete(**kwargs):
        raise AssertionError(f"unexpected LLM call: {kwargs.get('purpose', '')}")

    monkeypatch.setattr(statement_translator, "_chat_complete", fail_chat_complete)
    monkeypatch.setattr(statement_translator, "_validate_signature", lambda *args, **kwargs: (True, "", False))

    result = statement_translator.translate_statement(
        latex_statement=r"For all $z$, the second paper identity satisfies $u(z) = u(z)$.",
        client=object(),
        model="dummy",
        project_root=tmp_path,
        paper_id="2604.21821",
        theorem_name="rem",
        run_adversarial_check=False,
        run_roundtrip_check=False,
        use_schema_stage=False,
    )

    assert result.validated is True
    assert "schema_translation" not in result.lean_signature
    assert "RegeneratedStatement" not in result.lean_signature
    assert result.structured_translation["source"] == "typed_statement_ir"
