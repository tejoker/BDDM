from __future__ import annotations

import json
from pathlib import Path

import repair_bad_translations as rbt
from repair_bad_translations import (
    SymbolDecl,
    _extract_decl_blocks,
    _is_schema_placeholder_decl,
    _direct_tactic_for_decl,
    _repair_quality_blockers,
    _validation_source,
    build_repair_pack,
    build_source_backed_repair_payload,
    infer_symbol_table,
    repair_statement_with_symbols,
    write_retry_lean_file,
    write_symbol_theory,
)


def test_infer_symbol_table_detects_paper_local_symbols() -> None:
    text = "f ∈ C_T HSobolev s and |ℓ| ~ N and I_i ξ1 and ∥w∥_C_T_H ^ (s2 - alpha)"

    symbols = infer_symbol_table(text)
    names = {sym.lean for sym in symbols}

    assert {"HSobolev", "C_T", "I_i", "DyadicScale", "CTHNorm", "ξ1"} <= names


def test_repair_statement_with_symbols_rewrites_known_bad_patterns() -> None:
    decl = (
        "theorem t (f : ℝ → ℝ) (s : ℝ) (ℓ N : ℕ) : "
        "f ∈ C_T HSobolev s ∧ |ℓ| ~ N := by\n  sorry"
    )

    repaired, changes = repair_statement_with_symbols(decl)

    assert "f ∈ HSobolev (s)" in repaired
    assert "DyadicScale ℓ N" in repaired
    assert "replace_C_T_HSobolev_membership" in changes
    assert "replace_dyadic_asymptotic" in changes


def test_schema_placeholders_are_not_direct_proof_targets() -> None:
    assert _is_schema_placeholder_decl("theorem t (p_c1 : Prop) (h_c1 : p_c1) : p_c1 := by\n  sorry")
    assert _is_schema_placeholder_decl("theorem t : (0 : ℕ) = 0 := by\n  sorry")


def test_repair_statement_with_symbols_regenerates_explicit_structured_statement() -> None:
    repaired, changes = repair_statement_with_symbols(
        "theorem thm_baseline_lift (p_c1 : Prop) (h_c1 : p_c1) : p_c1 := by\n  sorry",
        source_statement=r"Let $\Psi_1=I_i(\xi_1)$ and $\Psi_2=I_i(\xi_2)$.",
    )

    assert "theorem thm_baseline_lift" in repaired
    assert "Ψ1 = I_i ξ1" in repaired
    assert "p_c1" not in repaired
    assert "Source claim excerpt" in repaired
    assert "regenerate_explicit_structured_statement" in changes
    assert "abstract_schema_placeholder_to_paper_claim" not in changes
    assert _direct_tactic_for_decl(repaired) == ""


def test_repair_statement_with_symbols_uses_paper_claim_only_as_last_resort() -> None:
    repaired, changes = repair_statement_with_symbols(
        "theorem unknown_claim (p_c1 : Prop) (h_c1 : p_c1) : p_c1 := by\n  sorry",
        source_statement="",
    )

    assert "UnknownClaimPaperClaim" in repaired
    assert "abstract_schema_placeholder_to_paper_claim" in changes


def test_repair_statement_with_symbols_rewrites_mixed_operator_sum() -> None:
    decl = (
        "theorem t (N : ℕ) (w : ℝ → ℝ) (s2 alpha T C : ℝ) :\n"
        "    CTHNorm (∑ i in Finset.range N, ∑ j in Finset.range N, "
        "(if i = j then D_N else B_N) w ) (s2 - alpha) ≤\n"
        "      C * CTHEnvelope w T * (⨆ t ∈ Set.Icc 0 T, |w t| + "
        "⨆ t ∈ Set.Icc 0 T, |(deriv w) t|) := by\n"
        "  sorry"
    )

    repaired, changes = repair_statement_with_symbols(decl)

    assert "CTHNorm (MixedOperator N w) (s2 - alpha)" in repaired
    assert "CTHEnvelope w 0" in repaired
    assert "replace_mixed_operator_sum" in changes
    assert "replace_C_T_supremum_envelope" in changes


def test_repair_statement_with_symbols_abstracts_type_name_operator_claim() -> None:
    decl = (
        "theorem remark_9\n"
        "  {alpha : ℝ} (V1 V2 U1 U2 : Type*) [NormedAddCommGroup V1] [NormedAddCommGroup V2]\n"
        "  [NormedAddCommGroup U1] [NormedAddCommGroup U2]\n"
        "  (halpha : alpha < 1)\n"
        "  (hV_regularity : Prop)\n"
        "  (h_random_operators : ∀ (T : Type*) [NormedAddCommGroup T],\n"
        "    ∃ (L : T → T), ∀ (u : T), L u = U1 ∨ L u = U2) :\n"
        "  ¬(∃ (L : V1 → V1), ∀ (v : V1), L v = U1 ∨ L v = U2) := by\n"
        "  sorry"
    )

    repaired, changes = repair_statement_with_symbols(decl)

    assert "(h_random_operators : Prop) (h_no_random_operators : ¬ h_random_operators)" in repaired
    assert ": ¬ h_random_operators := by" in repaired
    assert "abstract_type_name_operator_claim" in changes
    assert "insert_domain_lemma_assumption" in changes


def test_repair_statement_with_symbols_summarizes_dyadic_block_bound() -> None:
    decl = (
        "theorem remark_10 {alpha s2 theta : ℝ} (halpha : 0 < alpha) "
        "(htheta : 0 < theta) (ha : 6 - 8 * alpha + 2 * s2 + 3 * theta < 0) :\n"
        "  ∃ C : ℝ, ∀ N : ℕ, ∀ t : ℝ, t ∈ Set.Icc (0 : ℝ) T →\n"
        "    ∃ eps : ℕ → ℝ, eps =O[Filter.atTop] (fun _ => (1 : ℝ)) ∧\n"
        "    ∀ n : ℕ, n ≤ N →\n"
        "      |B_N^{i;j,k}(n, q, t, t)| ≤ C * (1 + eps N) * N^(3 - 6 * alpha) := by\n"
        "  sorry"
    )

    repaired, changes = repair_statement_with_symbols(decl)

    assert "theorem remark_10 {alpha s2 theta T : ℝ}" in repaired
    assert "h_dyadic_block_bound" in repaired
    assert "DyadicBlockBound N n t" in repaired
    assert "summarize_dyadic_block_bound" in changes
    assert "insert_domain_lemma_assumption" in changes


def test_repair_statement_with_symbols_summarizes_volterra() -> None:
    decl = (
        "theorem lem_volterra {a : ℝ → ℝ} {Φ : ℝ} {N : ℝ} {T : ℝ} {alpha : ℝ}\n"
        "  (ha : ContDiff ℝ 1 a) (hΦ : |Φ| ≥ N ^ alpha) :\n"
        "  ∃ C : ℝ, ∀ t : ℝ, 0 ≤ t → t ≤ T →\n"
        "    Complex.abs (∫ s in Set.Icc 0 t, a s * Complex.exp (Complex.I * (t - s) * Φ)) ≤\n"
        "      C * N ^ (-alpha) * (⨆ (t : ℝ) (H : 0 ≤ t ∧ t ≤ T), |a t| + ⨆ (t : ℝ) (H : 0 ≤ t ∧ t ≤ T), |deriv a t|) := by\n"
        "  sorry"
    )

    repaired, changes = repair_statement_with_symbols(decl)

    assert "VolterraOscillation a Φ t" in repaired
    assert "CTHEnvelope a T" in repaired
    assert "h_volterra_bound" in repaired
    assert "Complex.abs" not in repaired
    assert "summarize_volterra_oscillation" in changes
    assert "insert_domain_lemma_assumption" in changes


def test_repair_statement_with_symbols_inserts_mid_completion_domain_lemma() -> None:
    decl = (
        "theorem prop_mid_completion {N : ℕ} {s1 s2 alpha theta eps : ℝ} "
        "(hs1 : s1 > 0) (htheta : 0 < theta ∧ theta < 1) (hs2 : s2 > 0)\n"
        "    (hcond1 : s2 < 4 * alpha - 3 - (3 / 2) * theta - eps)\n"
        "    (hcond2 : 3 - 4 * alpha + theta * (s2 + eps) < 0) :\n"
        "    ∃ C : ℝ, ∀ N : ℕ, N ≠ 0 → N ^ (s2 + 3 - 4 * alpha - theta * s1 + eps) ≤ C := by\n"
        "  sorry"
    )

    repaired, changes = repair_statement_with_symbols(decl)

    assert "h_mid_completion" in repaired
    assert "insert_mid_completion_domain_lemma" in changes
    assert "insert_domain_lemma_assumption" in changes


def test_repair_statement_with_symbols_types_pathwise_bound_hypothesis() -> None:
    decl = (
        "theorem thm_pathwise_fluct\n"
        "  {alpha s2 theta : ℝ}\n"
        "  (hB_bound : Prop)\n"
        "  : ∃ C_omega : ℝ, C_omega > 0 ∧ ∀ N, B N ≤ C_omega * N ^ (a / 2 + κ) := by\n"
        "  sorry"
    )

    repaired, changes = repair_statement_with_symbols(decl)

    assert "(hB_bound : ∃ C_omega : ℝ" in repaired
    assert "type_pathwise_bound_hypothesis" in changes


def test_repair_statement_with_symbols_inserts_safe_range_domain_lemma() -> None:
    decl = (
        "theorem cor_safe_range {alpha : ℝ} (halpha : alpha > 12 / 13)\n"
        "    (hrho : rho_V = 3 * alpha - 3) (hX : ∀ x > 0, x ≤ naive_low_high_estimate) :\n"
        "    ∃ eps > 0, ∃ s1 s2 theta : ℝ, 0 < s1 ∧ s1 < s2 ∧ 0 < theta := by\n"
        "  sorry"
    )

    repaired, changes = repair_statement_with_symbols(decl)

    assert "h_safe_range" in repaired
    assert "insert_safe_range_domain_lemma" in changes


def test_build_repair_pack_writes_symbol_table_and_theory(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    report = tmp_path / "report.json"
    lean = tmp_path / "paper.lean"
    report.write_text(
        json.dumps(
            {
                "final_metrics": {
                    "unresolved": [{"theorem_name": "thm_bad"}],
                    "translation_limited": [],
                }
            }
        ),
        encoding="utf-8",
    )
    lean.write_text(
        "theorem thm_bad (f : ℝ → ℝ) (s : ℝ) : f ∈ C_T HSobolev s := by\n  sorry\n",
        encoding="utf-8",
    )

    payload = build_repair_pack(
        paper_id="2604.21884",
        report_path=report,
        lean_file=lean,
        project_root=project,
        out_dir=tmp_path / "repair",
        validate_candidates=False,
    )

    assert payload["symbols"]
    assert (tmp_path / "repair" / "symbol_table.json").exists()
    theory = project / "Desol" / "PaperTheory" / "Repair" / "Paper_2604_21884.lean"
    assert theory.exists()
    text = theory.read_text(encoding="utf-8")
    assert "def HSobolev" in text
    assert "namespace Paper_2604_21884_Repair" in text
    assert payload["repair_candidates"][0]["changes"] == ["replace_C_T_HSobolev_membership"]
    assert payload["repair_candidates"][0]["lean_validation"]["error"] == "validation_skipped"
    assert payload["candidate_counts"]["changed"] == 1
    assert Path(payload["retry_lean_file"]).exists()
    assert Path(payload["retry_queue_json"]).exists()


def test_build_repair_pack_regenerates_schema_placeholder_with_explicit_statement(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    report = tmp_path / "report.json"
    lean = tmp_path / "paper.lean"
    report.write_text(
        json.dumps(
            {
                "final_metrics": {
                    "unresolved": [],
                    "translation_limited": [{"theorem_name": "thm_baseline_lift"}],
                }
            }
        ),
        encoding="utf-8",
    )
    lean.write_text(
        "theorem thm_baseline_lift (p_c1 : Prop) (h_c1 : p_c1) : p_c1 := by\n  sorry\n",
        encoding="utf-8",
    )
    extracted_dir = project / "reproducibility" / "paper_agnostic_golden10_results" / "2604.21884"
    extracted_dir.mkdir(parents=True)
    (extracted_dir / "extracted_theorems.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "name": "thm:baseline-lift",
                            "statement": r"Let $\Psi_1=I_i(\xi_1)$ and $\Psi_2=I_i(\xi_2)$.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    payload = build_repair_pack(
        paper_id="2604.21884",
        report_path=report,
        lean_file=lean,
        project_root=project,
        out_dir=tmp_path / "repair_claim",
        validate_candidates=False,
    )

    theory = project / "Desol" / "PaperTheory" / "Repair" / "Paper_2604_21884.lean"
    text = theory.read_text(encoding="utf-8")
    assert "def ThmBaselineLiftRegeneratedStatement : Prop" not in text
    assert "axiom ThmBaselineLiftPaperClaim : Prop" not in text
    assert payload["candidate_counts"]["paper_claim_abstractions"] == 0
    assert payload["candidate_counts"]["diagnostic_repair_abstractions"] == 0
    assert payload["candidate_counts"]["faithful_statement_regenerations"] == 1
    cand = payload["repair_candidates"][0]
    assert cand["source_statement_available"] is True
    assert cand["repair_abstraction_kind"] == ""
    assert cand["statement_repair_kind"] == "faithful_statement_regeneration"
    assert cand["paper_theory_debt"] == []
    assert "Ψ1 = I_i ξ1" in cand["repaired_decl"]
    assert cand["repair_quality"]["ok"] is True


def test_repair_quality_blocks_vacuous_regenerated_statement() -> None:
    blockers = _repair_quality_blockers(
        repaired_decl="theorem weak : ∃ x : ℝ, x = x := by\n  sorry",
        source_statement="There exists a non-trivial construction whose length strictly increases.",
        changes=["regenerate_explicit_structured_statement", "source_grounded_statement_body"],
    )

    assert "vacuous_exists_self_equality_after_repair" in blockers


def test_build_repair_pack_rejects_weak_structured_regeneration(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "repair_bad_translations.build_typed_statement_translation",
        lambda **_kwargs: {"conclusion": "∃ x : ℝ, x = x"},
    )
    project = tmp_path / "proj"
    project.mkdir()
    report = tmp_path / "report.json"
    lean = tmp_path / "paper.lean"
    report.write_text(
        json.dumps(
            {
                "final_metrics": {
                    "unresolved": [],
                    "translation_limited": [{"theorem_name": "weak"}],
                }
            }
        ),
        encoding="utf-8",
    )
    lean.write_text(
        "theorem weak (p_c1 : Prop) (h_c1 : p_c1) : p_c1 := by\n  sorry\n",
        encoding="utf-8",
    )
    extracted_dir = project / "reproducibility" / "paper_agnostic_golden10_results" / "2604.21884"
    extracted_dir.mkdir(parents=True)
    (extracted_dir / "extracted_theorems.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "name": "weak",
                        "statement": "There exists a non-trivial construction whose length strictly increases.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    payload = build_repair_pack(
        paper_id="2604.21884",
        report_path=report,
        lean_file=lean,
        project_root=project,
        out_dir=tmp_path / "repair_weak",
        validate_candidates=False,
    )

    cand = payload["repair_candidates"][0]
    assert cand["repair_quality"]["ok"] is False
    assert "vacuous_exists_self_equality_after_repair" in cand["repair_quality"]["blockers"]
    assert cand["lean_validation"]["ok"] is False
    assert payload["candidate_counts"]["quality_blocked"] == 1
    assert payload["retry_candidate_count"] == 0


def test_source_backed_repair_payload_records_reviewable_context(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    payload = build_source_backed_repair_payload(
        paper_id="2604.21884",
        project_root=project,
        out_dir=tmp_path / "source_backed",
        validate_candidates=False,
        source_contexts=[
                {
                    "theorem_name": "alpha_le_beta",
                    "theorem_id": "alpha_le_beta",
                    "source_latex": r"Let $\alpha,\beta$ be real numbers with $\alpha \le \beta$.",
                    "context_pack": {
                        "translation_statement_schema": {
                            "objects": ["alpha", "beta"],
                            "assumptions": ["alpha and beta are real numbers"],
                            "claim": "alpha ≤ beta",
                            "constraints": [r"\alpha \le \beta"],
                        }
                    },
                    "source_span_quality": "extractor_native",
                    "source_match": {"match_status": "matched"},
                "source_span": {"source_file": "paper.tex", "start_byte": 0, "end_byte": 20},
                "lean_statement": "theorem alpha_le_beta : True := by\n  trivial",
            }
        ],
    )

    cand = payload["repair_candidates"][0]
    assert cand["regeneration_protocol"] == "source_backed_v2"
    assert cand["source_context_pack_id"].startswith("srcctx_")
    assert cand["source_context_pack"]["source_span_quality"] == "extractor_native"
    assert cand["source_coverage"]["score"] > 0
    assert cand["repair_quality"]["ok"] is True
    assert cand["lean_validation"]["error"] == "validation_skipped"


def test_source_backed_repair_payload_falls_back_after_malformed_lean_validation(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    validation_calls: list[str] = []

    monkeypatch.setattr(
        rbt,
        "build_typed_statement_translation",
        lambda **_kwargs: {
            "lean_declaration": "theorem hard_formula :\n  Delta commutator bound := by\n  sorry",
            "conclusion": "Delta commutator bound",
            "claim_shape": "raw_formula",
        },
    )
    monkeypatch.setattr(rbt, "build_repair_theory", lambda *_args, **_kwargs: {"ok": True})

    def fake_validate(**kwargs: object) -> dict[str, object]:
        decl = str(kwargs.get("decl", "") or "")
        validation_calls.append(decl)
        if "SourceStatement" in decl:
            return {"ok": True, "error": ""}
        return {"ok": False, "error": "unexpected token ':'; expected command"}

    monkeypatch.setattr(rbt, "validate_repair_candidate", fake_validate)

    payload = build_source_backed_repair_payload(
        paper_id="2604.21583",
        project_root=project,
        out_dir=tmp_path / "source_backed_fallback",
        validate_candidates=True,
        source_contexts=[
            {
                "theorem_name": "hard_formula",
                "theorem_id": "hard_formula",
                "source_latex": r"Let $\Delta_{p,q,k}$ be the commutator bound from equation (4.1).",
                "source_span_quality": "extractor_native",
                "source_match": {"match_status": "matched"},
                "source_span": {"source_file": "paper.tex", "start_byte": 0, "end_byte": 80},
                "lean_statement": "theorem hard_formula : True := by\n  trivial",
            }
        ],
    )

    cand = payload["repair_candidates"][0]
    assert cand["lean_validation"]["ok"] is True
    assert cand["repair_quality"]["ok"] is True
    assert "fallback_after_lean_validation_failure" in cand["changes"]
    assert "HardFormulaSourceStatement" in cand["repaired_decl"]
    assert cand["paper_theory_debt"] == ["paper_definition_stub:HardFormulaSourceStatement"]
    assert len(validation_calls) == 2


def test_write_symbol_theory_disables_unbuildable_base_import(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "proj"
    base = project / "Desol" / "PaperTheory" / "Paper_2604_21583.lean"
    base.parent.mkdir(parents=True)
    base.write_text("def Broken : Prop := True\nexport Paper_2604_21583 (Missing)\n", encoding="utf-8")

    class Proc:
        returncode = 1
        stdout = ""
        stderr = "Unknown constant `Paper_2604_21583.Missing`"

    monkeypatch.setattr(rbt.subprocess, "run", lambda *_args, **_kwargs: Proc())

    theory = write_symbol_theory(
        project_root=project,
        paper_id="2604.21583",
        symbols=[
            SymbolDecl(
                latex="source",
                lean="HardFormulaSourceStatement",
                kind="source_statement_review_stub",
                declaration="def HardFormulaSourceStatement : Prop := True",
                reason="test",
            )
        ],
    )

    text = theory.read_text(encoding="utf-8")
    assert "import Desol.PaperTheory.Paper_2604_21583" not in text
    assert "base paper theory import: disabled" in text
    assert "def HardFormulaSourceStatement : Prop := True" in text


def test_source_backed_repair_payload_blocks_ambiguous_source_context(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    payload = build_source_backed_repair_payload(
        paper_id="2604.21884",
        project_root=project,
        out_dir=tmp_path / "source_backed_bad",
        validate_candidates=False,
        source_contexts=[
            {
                "theorem_name": "ambiguous",
                "source_latex": r"Let $\alpha \le \beta$.",
                "source_span_quality": "ambiguous",
                "source_match": {"match_status": "ambiguous"},
                "lean_statement": "theorem ambiguous : True := by\n  trivial",
            }
        ],
    )

    cand = payload["repair_candidates"][0]
    assert cand["repair_quality"]["ok"] is False
    assert "source_span_not_review_grade" in cand["repair_quality"]["blockers"]
    assert "source_match_not_unique" in cand["repair_quality"]["blockers"]


def test_extract_decl_blocks_stops_before_next_comment_header() -> None:
    text = """-- [theorem] a
theorem first : True := by
  sorry

-- [theorem] b
theorem second : True := by
  trivial
"""

    blocks = _extract_decl_blocks(text)

    assert "-- [theorem] b" not in blocks["first"]
    assert "theorem second" not in blocks["first"]


def test_validation_source_imports_paper_theory_and_strips_end() -> None:
    source = _validation_source(
        paper_id="2604.21884",
        decl="theorem t : True := by\n  trivial\n\nend ArxivPaper",
    )

    assert "import Desol.PaperTheory.Repair.Paper_2604_21884" in source
    assert "open Paper_2604_21884_Repair" in source
    assert "end ArxivPaper" not in source


def test_write_retry_lean_file_keeps_only_validated_candidates(tmp_path: Path) -> None:
    lean, queue, count = write_retry_lean_file(
        project_root=tmp_path,
        paper_id="2604.21884",
        out_dir=tmp_path,
        candidates=[
            {
                "theorem_name": "good",
                "repaired_decl": "theorem good : True := by\n  sorry",
                "changes": ["rewrite"],
                "lean_validation": {"ok": True},
            },
            {
                "theorem_name": "bad",
                "repaired_decl": "theorem bad : True := by\n  sorry",
                "changes": ["rewrite"],
                "lean_validation": {"ok": False},
            },
            {
                "theorem_name": "unchanged",
                "repaired_decl": "theorem unchanged : True := by\n  sorry",
                "changes": [],
                "lean_validation": {"ok": True},
            },
        ],
    )

    text = lean.read_text(encoding="utf-8")
    assert count == 1
    assert "theorem good : True" in text
    assert "theorem bad : True" not in text
    assert "theorem unchanged : True" not in text
    assert '"good"' in queue.read_text(encoding="utf-8")


def test_write_symbol_theory_dedupes_abbrev_from_base(tmp_path: Path) -> None:
    """The repair-module generator must NOT redeclare a name that already
    exists in the imported base paper-theory. Pre-fix bug: the dedup regex
    matched only `axiom|constant|def|theorem|lemma` — `abbrev` was missed,
    so `abbrev Multisegment` got re-emitted in `Paper_X_Repair` even though
    `Paper_X` already declared it. With both namespaces opened via
    `PaperImportsAnchor`, any reference to `Multisegment` then failed with
    `Ambiguous term`. Surfaced by the LLM-statement-repair smoke run
    (Round II-4 / commit e6065ab)."""
    from repair_bad_translations import write_symbol_theory, SymbolDecl

    project = tmp_path / "proj"
    project.mkdir()
    base_dir = project / "Desol" / "PaperTheory"
    base_dir.mkdir(parents=True)
    # Write a base theory file containing `abbrev Multisegment : Type := ℕ`
    # and `noncomputable def TestNorm`. Both shapes the previous regex
    # missed.
    (base_dir / "Paper_9999_99999.lean").write_text(
        "namespace Paper_9999_99999\n\n"
        "abbrev Multisegment : Type := ℕ\n\n"
        "noncomputable def TestNorm : ℝ := 0\n\n"
        "def OtherSymbol : Prop := True\n\n"
        "end Paper_9999_99999\n",
        encoding="utf-8",
    )
    symbols = [
        SymbolDecl(
            latex="Multisegment",
            lean="Multisegment",
            kind="paper_local",
            declaration="abbrev Multisegment : Type := ℕ",
            reason="paper_multisegment_carrier",
        ),
        SymbolDecl(
            latex="TestNorm",
            lean="TestNorm",
            kind="paper_local",
            declaration="noncomputable def TestNorm : ℝ := 0",
            reason="paper_norm_carrier",
        ),
        SymbolDecl(
            latex="OtherSymbol",
            lean="OtherSymbol",
            kind="paper_local",
            declaration="def OtherSymbol : Prop := True",
            reason="other",
        ),
        SymbolDecl(
            latex="NewSymbol",
            lean="NewSymbol",
            kind="paper_local",
            declaration="def NewSymbol : Prop := True",
            reason="repair_new",
        ),
    ]
    out = write_symbol_theory(project_root=project, paper_id="9999.99999", symbols=symbols)
    text = out.read_text(encoding="utf-8")
    # Only `NewSymbol` should be re-emitted; the three already in the base
    # must be filtered out.
    assert "def NewSymbol" in text
    assert "abbrev Multisegment" not in text
    assert "TestNorm" not in text
    assert "OtherSymbol" not in text


def test_write_symbol_theory_dedupes_when_base_uses_only_def(tmp_path: Path) -> None:
    """Sanity guard against the pre-fix regex behaviour. A base that uses
    only `def` declarations must still produce dedup correctly."""
    from repair_bad_translations import write_symbol_theory, SymbolDecl

    project = tmp_path / "proj"
    project.mkdir()
    base_dir = project / "Desol" / "PaperTheory"
    base_dir.mkdir(parents=True)
    (base_dir / "Paper_9998_99998.lean").write_text(
        "namespace Paper_9998_99998\n\n"
        "def Foo : Prop := True\n\n"
        "end Paper_9998_99998\n",
        encoding="utf-8",
    )
    symbols = [
        SymbolDecl(
            latex="Foo", lean="Foo", kind="paper_local",
            declaration="def Foo : Prop := True", reason="dup",
        ),
        SymbolDecl(
            latex="Bar", lean="Bar", kind="paper_local",
            declaration="def Bar : Prop := True", reason="new",
        ),
    ]
    out = write_symbol_theory(project_root=project, paper_id="9998.99998", symbols=symbols)
    text = out.read_text(encoding="utf-8")
    assert "def Bar" in text
    # Foo is in base, must NOT be re-emitted.
    assert "def Foo" not in text
