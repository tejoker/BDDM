from __future__ import annotations

from paper_symbol_inventory import build_symbol_inventory, infer_symbols_from_schema, infer_symbols_from_text


def test_inventory_mines_latex_glossary_and_greek_symbols() -> None:
    symbols = build_symbol_inventory(
        glossary={"$H^s$": "Sobolev space", "$\\Gamma_1$": "renormalization constant"},
        seed_text="Ψ1 Γ1 C_T",
    )
    names = {sym.lean: sym for sym in symbols}

    assert names["HSobolev"].grounding == "definition_stub"
    assert names["HSobolev"].grounding_kind == "mathlib_close_definition_stub"
    assert names["HSobolev"].proof_countable is False
    assert names["C_T"].kind == "function_space"
    assert names["C_T"].paper_agnostic_rule_id == "definition_stub.typed_carrier"
    assert names["Γ1"].declaration == "def Γ1 : ℝ := 0"
    assert names["Ψ1"].source == "seed_text"


def test_inventory_mines_required_definitions_and_notation_dependencies() -> None:
    schema_symbols = infer_symbols_from_schema(
        {
            "required_definitions": ["Define DyadicBlockBound for B_N^{i,j}."],
            "notation_dependencies": ["C_T H^s norm and I_i operator"],
        }
    )
    names = {sym.lean for sym in schema_symbols}

    assert {"DyadicBlockBound", "B_N", "HSobolev", "C_T", "I_i"} <= names
    dyadic = {sym.lean: sym for sym in schema_symbols}["DyadicBlockBound"]
    assert dyadic.grounding_kind == "paper_local_lemma_obligation"
    assert dyadic.paper_agnostic_rule_id == "local_lemma.requires_verified_replacement"


def test_repair_heuristic_inventory_uses_same_symbol_rules() -> None:
    symbols = infer_symbols_from_text("HSobolev C_T I_i cutoff_solution paracontrolled_solution", source="repair_heuristic")
    names = {sym.lean: sym.declaration for sym in symbols}

    assert names["HSobolev"].startswith("def HSobolev")
    assert names["I_i"] == "def I_i (x : ℝ) : ℝ := x"
    assert names["cutoff_solution"].startswith("def cutoff_solution")


def test_inventory_declares_current_paper_statement_family_predicates() -> None:
    symbols = infer_symbols_from_text(
        "baseline lift, cubic and quartic, mixed random operators, conditional deterministic closure, "
        "centered covariance, pathwise centered fluctuations, speed gap, Volterra estimate, "
        "Strichartz assumption, safe bookkeeping range",
        source="seed_text",
    )
    names = {sym.lean: sym for sym in symbols}

    assert names["BaselineLiftStatement"].declaration == "def BaselineLiftStatement : Prop := True"
    assert names["MixedRandomOperatorConvergence"].kind == "statement_predicate"
    assert names["SafeRangeStatement"].grounding == "definition_stub"


def test_inventory_mines_second_paper_generated_symbols() -> None:
    symbols = infer_symbols_from_text(
        "There exists inst : IsHilbertSpace (H1_D_f (Set.Ioo 0 z_F)). "
        "The bound is finite below infty with d_dtvolume.",
        source="translation_schema",
    )
    names = {sym.lean: sym for sym in symbols}

    assert names["H1_D_f"].kind == "type_family"
    assert names["IsHilbertSpace"].declaration.startswith("def IsHilbertSpace")
    assert names["d_dtvolume"].grounding == "domain_axiom"
    assert names["infty"].declaration == "def infty : ℝ := 0"
