#!/usr/bin/env python3
"""Shared paper-local symbol inventory for generated PaperTheory modules."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


_LEAN_NAME_RE = r"[A-Za-z_ξΨΓΘΩαβγδℓ][A-Za-z0-9_ξΨΓΘΩαβγδℓ']*"


@dataclass(frozen=True)
class PaperSymbolDecl:
    latex: str
    lean: str
    kind: str
    declaration: str
    grounding: str
    reason: str
    source: str = "heuristic"
    grounding_kind: str = ""
    grounding_source: str = ""
    grounding_trust: str = ""
    paper_agnostic_rule_id: str = ""
    proof_countable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _grounding_metadata_for_symbol(
    *,
    lean: str,
    kind: str,
    grounding: str,
    source: str,
) -> dict[str, Any]:
    if grounding == "definition_stub":
        if kind == "scalar":
            rule_id = "definition_stub.scalar_parameter"
            grounding_kind = "transparent_scalar_stub"
        elif kind in {"operator", "operator_family", "solution", "solution_family", "data_norm", "norm"}:
            rule_id = "definition_stub.transparent_operator_or_distribution"
            grounding_kind = "transparent_operator_or_distribution_stub"
        elif kind in {"function_space", "type_family", "type_predicate"}:
            rule_id = "definition_stub.typed_carrier"
            grounding_kind = "transparent_typed_carrier_stub"
        else:
            rule_id = "definition_stub.syntax_only"
            grounding_kind = "transparent_definition_stub"
        if lean in {"HSobolev", "L2Space"}:
            rule_id = "definition_stub.mathlib_close_match"
            grounding_kind = "mathlib_close_definition_stub"
        return {
            "grounding_kind": grounding_kind,
            "grounding_source": source,
            "grounding_trust": "syntax_only_not_semantic_proof",
            "paper_agnostic_rule_id": rule_id,
            "proof_countable": False,
        }
    if grounding == "local_lemma_axiom":
        return {
            "grounding_kind": "paper_local_lemma_obligation",
            "grounding_source": source,
            "grounding_trust": "unproved_local_theory_obligation",
            "paper_agnostic_rule_id": "local_lemma.requires_verified_replacement",
            "proof_countable": False,
        }
    return {
        "grounding_kind": "domain_axiom_obligation",
        "grounding_source": source,
        "grounding_trust": "unproved_domain_assumption",
        "paper_agnostic_rule_id": "domain_axiom.requires_verified_replacement",
        "proof_countable": False,
    }


def _has_ident(text: str, ident: str) -> bool:
    return bool(re.search(rf"(?<![A-Za-z0-9_']){re.escape(ident)}(?![A-Za-z0-9_'])", text or ""))


def _decl_name(declaration: str) -> str:
    m = re.match(
        rf"\s*(?:axiom|constant|def|abbrev|opaque|lemma|theorem)\s+({_LEAN_NAME_RE})\b",
        declaration,
    )
    return m.group(1) if m else ""


def _definition_declaration(lean: str, kind: str) -> tuple[str, str]:
    """Return a conservative elaboration scaffold plus grounding class."""
    if kind == "statement_predicate":
        return f"def {lean} : Prop := True", "definition_stub"
    if kind == "type_family":
        return f"def {lean} (_s : Set ℝ) : Type := ℝ", "definition_stub"
    if kind == "type_predicate":
        return f"def {lean} (_α : Type*) : Type := PUnit", "definition_stub"
    if kind == "function_space":
        if lean == "HSobolev":
            return "def HSobolev (_s : ℝ) : Set (ℝ → ℝ) := Set.univ", "definition_stub"
        return f"def {lean} : Set (ℝ → ℝ) := Set.univ", "definition_stub"
    if kind == "operator":
        if lean == "MixedOperator":
            return "def MixedOperator (_N : ℕ) (w : ℝ → ℝ) : ℝ → ℝ := w", "definition_stub"
        return f"def {lean} (x : ℝ) : ℝ := x", "definition_stub"
    if kind == "operator_family":
        if lean == "B_N":
            return "def B_N (_N _i _j _k : ℕ) : ℝ := 0", "definition_stub"
        if lean == "D_N":
            return "def D_N (_N _i _k : ℕ) : ℝ := 0", "definition_stub"
    if kind == "predicate":
        return f"def {lean} (_l _N : ℕ) : Prop := True", "definition_stub"
    if kind == "frequency":
        return f"def {lean} (_i _k : ℕ) : ℝ := 0", "definition_stub"
    if kind == "solution_family":
        return f"def {lean} (_N : ℕ) (_t : ℝ) : ℝ := 0", "definition_stub"
    if kind == "solution":
        return f"def {lean} (_t : ℝ) : ℝ := 0", "definition_stub"
    if kind == "data_norm":
        return f"def {lean} (_N : ℕ) : ℝ := 0", "definition_stub"
    if kind == "norm":
        if lean == "CTHEnvelope":
            return "def CTHEnvelope (_w : ℝ → ℝ) (_T : ℝ) : ℝ := 0", "definition_stub"
        return f"def {lean} (_w : ℝ → ℝ) (_s : ℝ) : ℝ := 0", "definition_stub"
    if kind in {"estimate_lhs", "lemma"}:
        if lean == "VolterraOscillation":
            return "axiom VolterraOscillation : (ℝ → ℝ) → ℝ → ℝ → ℝ", "local_lemma_axiom"
        if lean == "DyadicBlockBound":
            return "axiom DyadicBlockBound : ℕ → ℕ → ℝ → ℝ", "local_lemma_axiom"
        return f"axiom {lean} : Prop", "local_lemma_axiom"
    if kind == "measure":
        return f"axiom {lean} : Measure ℝ", "domain_axiom"
    return f"def {lean} : ℝ := 0", "definition_stub"


_THEOREM_FAMILY_PREDICATES: dict[str, tuple[str, str]] = {
    "baseline lift": ("BaselineLiftStatement", "baseline_lift_statement_predicate"),
    "cubic and quartic": ("CubicQuarticBaselineStatement", "cubic_quartic_baseline_statement_predicate"),
    "mixed random operators": ("MixedRandomOperatorConvergence", "operator_convergence_statement_predicate"),
    "conditional deterministic closure": ("ConditionalDeterministicClosure", "conditional_closure_statement_predicate"),
    "centered covariance": ("CenteredCovarianceBound", "dyadic_covariance_statement_predicate"),
    "pathwise centered fluctuations": ("PathwiseFluctuationBound", "pathwise_fluctuation_statement_predicate"),
    "speed gap": ("SpeedGapStatement", "speed_gap_statement_predicate"),
    "volterra": ("VolterraEstimateStatement", "volterra_estimate_statement_predicate"),
    "strichartz": ("StrichartzAssumptionStatement", "strichartz_assumption_statement_predicate"),
    "safe bookkeeping range": ("SafeRangeStatement", "safe_range_statement_predicate"),
}


def _symbol(
    latex: str,
    lean: str,
    kind: str,
    reason: str,
    source: str,
    declaration: str = "",
    grounding: str = "",
) -> PaperSymbolDecl:
    decl, default_grounding = _definition_declaration(lean, kind)
    effective_grounding = grounding or default_grounding
    metadata = _grounding_metadata_for_symbol(
        lean=lean,
        kind=kind,
        grounding=effective_grounding,
        source=source,
    )
    return PaperSymbolDecl(
        latex=latex,
        lean=lean,
        kind=kind,
        declaration=declaration or decl,
        grounding=effective_grounding,
        reason=reason,
        source=source,
        **metadata,
    )


def infer_symbols_from_text(text: str, *, source: str = "seed_text") -> list[PaperSymbolDecl]:
    """Infer paper-local symbols needed to make translated statements elaborate."""
    s = text or ""
    symbols: list[PaperSymbolDecl] = []

    def add(latex: str, lean: str, kind: str, reason: str) -> None:
        symbols.append(_symbol(latex, lean, kind, reason, source))

    if any(tok in s for tok in ("HSobolev", "C_T HSobolev", "C_T_H", "H^s", "Sobolev")):
        add("H^s / HSobolev", "HSobolev", "function_space", "sobolev_space_reference")
    if "C_T" in s or "C([0,T]" in s or "C([0, T]" in s:
        add("C_T", "C_T", "function_space", "time_continuity_space")
    if "L2Space" in s or "L^2" in s or "L²" in s:
        add("L^2", "L2Space", "function_space", "l2_space_reference")
    if re.search(r"\bI_i\b", s):
        add("I_i", "I_i", "operator", "paper_duhamel_operator")
    if re.search(r"\|[^|]+\|\s*~\s*[A-Za-z0-9_']+", s):
        add("|ell| ~ N", "DyadicScale", "predicate", "dyadic_scale_relation")
    if "omega" in s or "ω" in s:
        add("ω_i(k)", "omega", "frequency", "frequency_function")
    if "cutoff_solution" in s:
        add("cutoff solution", "cutoff_solution", "solution_family", "cutoff_solution_reference")
    if "paracontrolled_solution" in s:
        add("paracontrolled solution", "paracontrolled_solution", "solution", "limit_solution_reference")
    if "cutoff_enhanced_data" in s:
        add("cutoff enhanced data", "cutoff_enhanced_data", "data_norm", "enhanced_data_reference")
    if re.search(r"\bB_N\b", s):
        add("B_N", "B_N", "operator_family", "dyadic_random_operator")
    if re.search(r"\bD_N\b", s):
        add("D_N", "D_N", "operator_family", "dyadic_deterministic_operator")
    if "d_dts" in s:
        add("d s / d t", "d_dts", "measure", "latex_differential_artifact")
    if "∥" in s and ("_C_T_H" in s or "C_T H" in s):
        add("||.||_{C_T H^s}", "CTHNorm", "norm", "function_space_norm")
    if "MixedOperator" in s or ("D_N" in s and "B_N" in s and "Finset.range" in s):
        add("mixed random/deterministic operator sum", "MixedOperator", "operator", "paper_mixed_operator_sum")
    if "CTHEnvelope" in s or "⨆ t ∈ Set.Icc" in s:
        add("C_T envelope norm", "CTHEnvelope", "norm", "paper_supremum_envelope")
    if "Complex.abs" in s or "VolterraOscillation" in s:
        add("Volterra oscillatory integral", "VolterraOscillation", "estimate_lhs", "paper_volterra_oscillation_summary")
    if "DyadicBlockBound" in s or "B_N^{" in s:
        add("dyadic block random operator bound", "DyadicBlockBound", "estimate_lhs", "paper_dyadic_block_bound_summary")
    if "H1_D_f" in s:
        add("H1_D_f", "H1_D_f", "type_family", "paper_weighted_hilbert_type_family")
    if "IsHilbertSpace" in s:
        add("IsHilbertSpace", "IsHilbertSpace", "type_predicate", "paper_hilbert_space_predicate")
    if "d_dtvolume" in s or "d_dt volume" in s:
        add("d_dt volume", "d_dtvolume", "measure", "generated_differential_measure")
    if re.search(r"(?<![A-Za-z0-9_'])infty(?![A-Za-z0-9_'])", s):
        add("infty", "infty", "scalar", "infinity_symbol_placeholder")

    low = s.lower()
    for marker, (lean, reason) in _THEOREM_FAMILY_PREDICATES.items():
        if marker in low:
            add(marker, lean, "statement_predicate", reason)

    generic_symbols = set(
        re.findall(
            r"(?<![A-Za-z0-9_'])"
            r"(?:[ξΨΓΘ][A-Za-z0-9_']*|[A-Za-z]+_[A-Za-z0-9_']+|[A-Za-z]+[0-9]+)"
            r"(?![A-Za-z0-9_'])",
            s,
        )
    )
    generic_symbols.update(
        {
            "ξ1",
            "ξ2",
            "Ψ1",
            "Ψ2",
            "Γ1",
            "Γ2",
            "Θ",
            "theta",
            "s1",
            "s2",
            "C",
            "a",
            "C_omega",
            "rho_V",
            "naive_low_high_estimate",
        }
    )
    reserved = {
        "theorem", "lemma", "def", "Prop", "True", "False", "Type", "Set", "Measure", "Filter",
        "Tendsto", "fun", "by", "sorry", "Mathlib", "Nat", "Real", "Complex", "For", "Then",
        "Assume", "Let", "If", "There", "exists", "forall", "and", "or",
        "HSobolev", "C_T", "I_i", "B_N", "D_N", "DyadicBlockBound", "VolterraOscillation",
        "MixedOperator", "CTHEnvelope", "H1_D_f", "IsHilbertSpace",
    }

    def looks_like_math_symbol(ident: str) -> bool:
        if ident in {"rho_V", "C_omega", "H1_D_f", "d_dtvolume", "naive_low_high_estimate"}:
            return True
        if re.search(r"[ξΨΓΘ]", ident):
            return True
        if re.search(r"\d", ident):
            return True
        if re.search(r"[A-Z]", ident) and "_" in ident:
            return True
        if ident in {"C", "T", "N", "a", "theta"}:
            return True
        return False

    for ident in sorted(generic_symbols):
        if ident in reserved or len(ident) <= 1 and ident not in {"C", "a"}:
            continue
        if ident.startswith(("thm_", "prop_", "lem_", "rem_", "cor_", "def_", "ass_", "test_")):
            continue
        if not looks_like_math_symbol(ident):
            continue
        if ident[0].isupper() and ident not in {"C", "T", "N", "C_omega"} and not ident.startswith(("Γ", "Ψ", "Θ")):
            continue
        if _has_ident(s, ident):
            add(ident, ident, "scalar", "paper_scalar_or_distribution")

    return dedupe_symbols(symbols)


def infer_symbols_from_schema(schema: dict[str, Any] | None, *, source: str = "translation_schema") -> list[PaperSymbolDecl]:
    if not isinstance(schema, dict):
        return []
    chunks: list[str] = []
    for key in (
        "objects",
        "quantifiers",
        "assumptions",
        "claim",
        "symbols",
        "constraints",
        "theorem_intent",
        "notation_dependencies",
        "required_definitions",
        "conclusion",
        "hypotheses",
        "variables",
    ):
        value = schema.get(key)
        if isinstance(value, list):
            chunks.extend(str(x) for x in value)
        elif value is not None:
            chunks.append(str(value))
    return infer_symbols_from_text("\n".join(chunks), source=source)


def infer_symbols_from_glossary(glossary: dict[str, str] | None) -> list[PaperSymbolDecl]:
    if not glossary:
        return []
    chunks = [f"{k}: {v}" for k, v in glossary.items()]
    return infer_symbols_from_text("\n".join(chunks), source="latex_glossary")


def build_symbol_inventory(
    *,
    seed_text: str = "",
    glossary: dict[str, str] | None = None,
    schemas: list[dict[str, Any]] | None = None,
    entries: list[Any] | None = None,
) -> list[PaperSymbolDecl]:
    symbols: list[PaperSymbolDecl] = []
    if seed_text:
        symbols.extend(infer_symbols_from_text(seed_text, source="seed_text"))
    if glossary:
        symbols.extend(infer_symbols_from_glossary(glossary))
    for schema in schemas or []:
        symbols.extend(infer_symbols_from_schema(schema))
    if entries:
        parts: list[str] = []
        for entry in entries:
            parts.append(str(getattr(entry, "statement", "") or ""))
            parts.append(str(getattr(entry, "proof", "") or ""))
            parts.append(str(getattr(entry, "name", "") or ""))
            parts.append(str(getattr(entry, "kind", "") or ""))
        symbols.extend(infer_symbols_from_text("\n".join(parts), source="extracted_statement"))
    return dedupe_symbols(symbols)


def dedupe_symbols(symbols: list[PaperSymbolDecl]) -> list[PaperSymbolDecl]:
    seen: set[str] = set()
    out: list[PaperSymbolDecl] = []
    for sym in symbols:
        if not sym.lean or sym.lean in seen:
            continue
        seen.add(sym.lean)
        out.append(sym)
    return out


def declaration_name(declaration: str) -> str:
    return _decl_name(declaration)


def symbols_to_manifest(*, paper_id: str, module_name: str, symbols: list[PaperSymbolDecl]) -> dict[str, Any]:
    return {
        "schema_version": "1.1.0",
        "paper_id": paper_id,
        "module_name": module_name,
        "grounding_policy": {
            "proof_countable": False,
            "claim_scope": (
                "Paper-local symbol declarations ground notation for elaboration; "
                "they are not proofs of paper claims unless separately audited and Lean-verified."
            ),
        },
        "symbols": [sym.to_dict() for sym in symbols],
    }


def load_inventory_json(path: str | Path) -> list[PaperSymbolDecl]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = raw.get("symbols", raw) if isinstance(raw, dict) else raw
    out: list[PaperSymbolDecl] = []
    if not isinstance(rows, list):
        return []
    for row in rows:
        if not isinstance(row, dict):
            continue
        lean = str(row.get("lean", "") or "").strip()
        declaration = str(row.get("declaration", "") or "").strip()
        if not lean or not declaration:
            continue
        out.append(
            PaperSymbolDecl(
                latex=str(row.get("latex", "") or ""),
                lean=lean,
                kind=str(row.get("kind", "") or "symbol"),
                declaration=declaration,
                grounding=str(row.get("grounding", "") or "domain_axiom"),
                reason=str(row.get("reason", "") or "loaded_inventory"),
                source=str(row.get("source", "") or "seed_json"),
                grounding_kind=str(row.get("grounding_kind", "") or ""),
                grounding_source=str(row.get("grounding_source", "") or ""),
                grounding_trust=str(row.get("grounding_trust", "") or ""),
                paper_agnostic_rule_id=str(row.get("paper_agnostic_rule_id", "") or ""),
                proof_countable=bool(row.get("proof_countable", False)),
            )
        )
    return dedupe_symbols(out)
