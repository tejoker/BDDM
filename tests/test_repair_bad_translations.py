from __future__ import annotations

import json
from pathlib import Path

from repair_bad_translations import (
    _extract_decl_blocks,
    _is_schema_placeholder_decl,
    _direct_tactic_for_decl,
    _validation_source,
    build_repair_pack,
    infer_symbol_table,
    repair_statement_with_symbols,
    write_retry_lean_file,
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
