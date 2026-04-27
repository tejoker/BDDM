from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any


AXIOM_DEBT_BURNDOWN_SCHEMA_VERSION = "1.1.0"
GROUNDING_METADATA_SCHEMA_VERSION = "1.0.0"

_DIFFICULTY_RANK = {
    "easy": 10,
    "medium": 30,
    "hard": 70,
    "deep": 90,
}

_BUCKET_LABELS = {
    "missing_definitions_only": "depend only on missing definitions",
    "local_lemmas": "depend on local lemmas",
    "missing_mathlib": "depend on missing Mathlib bridge theorems",
    "deep_domain_theory": "depend on deep domain theory",
    "translation_artifacts": "depend on translation artifacts",
    "unclassified": "are unclassified axiom debt",
}

_MATHLIB_CLOSE: dict[str, list[str]] = {
    "L2Space": ["MeasureTheory.Lp", "MeasureTheory.MemLp"],
    "HSobolev": ["SobolevSpace", "MeasureTheory.MemLp"],
}

_DOMAIN_SYMBOLS = {
    "I_i",
    "Γ1",
    "Γ2",
    "Ψ1",
    "Ψ2",
    "ξ1",
    "ξ2",
    "Θ",
    "cutoff_solution",
    "paracontrolled_solution",
    "cutoff_enhanced_data",
}

_SCALAR_STUB_SYMBOLS = {
    "a",
    "C",
    "C_omega",
    "omega",
    "rho_V",
    "s1",
    "s2",
    "theta",
    "naive_low_high_estimate",
}

_DEEP_MARKERS = {
    "DyadicBlockBound",
    "VolterraOscillation",
    "cutoff_enhanced_data",
    "cutoff_solution",
    "paracontrolled_solution",
    "I_i",
}

_DEFINITION_MATHLIB_CLOSE = {"HSobolev", "L2Space"}
_TRANSPARENT_OPERATOR_STUBS = {
    "I_i",
    "MixedOperator",
    "C_T",
    "Γ1",
    "Γ2",
    "Ψ1",
    "Ψ2",
    "Θ",
    "ξ1",
    "ξ2",
}


def axiom_debt_items(row: dict[str, Any]) -> list[str]:
    debt = row.get("axiom_debt", [])
    if isinstance(debt, list):
        return [str(x) for x in debt if str(x).strip()]
    if isinstance(debt, str) and debt.strip():
        return [debt]
    return []


def _row_statement(row: dict[str, Any]) -> str:
    return str(row.get("lean_statement", "") or "")


def _target_atom(lean_statement: str) -> str:
    stmt = re.sub(r":=\s*by\b.*$", "", lean_statement or "", flags=re.DOTALL)
    depth = 0
    last_colon = -1
    for idx, ch in enumerate(stmt):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0:
            last_colon = idx
    target = stmt[last_colon + 1 :].strip() if last_colon >= 0 else ""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_']*", target):
        return target
    return ""


def grounding_metadata_for_debt(debt_item: str) -> dict[str, Any]:
    """Paper-agnostic trust metadata for one axiom/debt item."""
    debt = str(debt_item or "").strip()
    symbol = _symbol(debt)
    base: dict[str, Any] = {
        "schema_version": GROUNDING_METADATA_SCHEMA_VERSION,
        "debt_item": debt,
        "symbol": symbol,
        "grounding_kind": "unclassified_debt",
        "grounding_source": "ledger_axiom_debt",
        "grounding_trust": "unclassified",
        "paper_agnostic_rule_id": "debt.unclassified",
        "proof_countable": False,
        "hidden_assumption": True,
        "requires_verified_replacement": True,
    }
    if debt.startswith("paper_definition_stub:"):
        rule = "definition_stub.syntax_only"
        kind = "transparent_definition_stub"
        trust = "syntax_only_not_semantic_proof"
        source = "paper_symbol_inventory"
        if symbol in _DEFINITION_MATHLIB_CLOSE:
            rule = "definition_stub.mathlib_close_match"
            kind = "mathlib_close_definition_stub"
            source = "paper_symbol_inventory.mathlib_close_match"
        elif symbol in _SCALAR_STUB_SYMBOLS:
            rule = "definition_stub.scalar_parameter"
            kind = "transparent_scalar_stub"
        elif symbol in _TRANSPARENT_OPERATOR_STUBS:
            rule = "definition_stub.transparent_operator_or_distribution"
            kind = "transparent_operator_or_distribution_stub"
        base.update(
            {
                "grounding_kind": kind,
                "grounding_source": source,
                "grounding_trust": trust,
                "paper_agnostic_rule_id": rule,
                "hidden_assumption": False,
                "requires_verified_replacement": False,
            }
        )
        return base
    if debt.startswith("paper_local_lemma:"):
        base.update(
            {
                "grounding_kind": "paper_local_lemma_obligation",
                "grounding_source": "paper_statement_or_domain_pack",
                "grounding_trust": "unproved_local_theory_obligation",
                "paper_agnostic_rule_id": "local_lemma.requires_verified_replacement",
                "hidden_assumption": True,
            }
        )
        return base
    if debt.startswith("paper_symbol:"):
        is_deep = symbol in _DOMAIN_SYMBOLS or symbol in _DEEP_MARKERS
        base.update(
            {
                "grounding_kind": "domain_symbol_obligation" if is_deep else "paper_symbol_obligation",
                "grounding_source": "paper_symbol_reference",
                "grounding_trust": "unproved_domain_symbol" if is_deep else "unproved_paper_symbol",
                "paper_agnostic_rule_id": (
                    "domain_symbol.requires_domain_pack_or_verified_replacement"
                    if is_deep
                    else "paper_symbol.requires_definition_or_verified_replacement"
                ),
                "hidden_assumption": True,
            }
        )
        return base
    if debt == "paper_theory_reference":
        base.update(
            {
                "grounding_kind": "paper_theory_reference",
                "grounding_source": "regenerated_statement_or_paper_theory",
                "grounding_trust": "unproved_paper_theory_reference",
                "paper_agnostic_rule_id": "paper_theory_reference.requires_audited_statement",
                "hidden_assumption": True,
            }
        )
        return base
    if debt == "translation_repair_domain_assumption":
        base.update(
            {
                "grounding_kind": "translation_repair_domain_assumption",
                "grounding_source": "translation_repair",
                "grounding_trust": "diagnostic_only",
                "paper_agnostic_rule_id": "translation_repair.domain_assumption_not_countable",
                "hidden_assumption": True,
            }
        )
        return base
    return base


def grounding_metadata_for_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [grounding_metadata_for_debt(debt) for debt in axiom_debt_items(row)]


def _deep_obligation_debts(row: dict[str, Any]) -> list[str]:
    debts = axiom_debt_items(row)
    if proof_value_for_row(row) != "deep_domain_gap":
        return []
    out: list[str] = []
    statement = _row_statement(row)
    for debt in debts:
        meta = grounding_metadata_for_debt(debt)
        symbol = str(meta.get("symbol", "") or "")
        if (
            str(meta.get("grounding_kind", "")).endswith("_obligation")
            or symbol in _DEEP_MARKERS
            or any(marker in statement for marker in _DEEP_MARKERS)
        ):
            out.append(debt)
    return out


def build_deep_domain_obligations(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Group deep-domain debt by canonical missing local theorem/definition."""
    grouped: dict[str, dict[str, Any]] = {}
    for row in entries:
        if not isinstance(row, dict):
            continue
        theorem_name = str(row.get("theorem_name", "") or "")
        for debt in _deep_obligation_debts(row):
            meta = grounding_metadata_for_debt(debt)
            symbol = str(meta.get("symbol", "") or debt)
            key = symbol or debt
            item = grouped.setdefault(
                key,
                {
                    "obligation_id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:16],
                    "canonical_symbol": key,
                    "debt_items": [],
                    "needed_by": [],
                    "needed_by_details": [],
                    "grounding_metadata": meta,
                    "proof_countable": False,
                    "replacement_gate": {
                        "exact_recorded_statement_required": True,
                        "fresh_lean_verification_required": True,
                        "no_paper_axiom_debt_required": True,
                        "audited_provenance_required": True,
                    },
                    "suggested_action": (
                        "Prove this canonical local theorem in a reusable domain pack, "
                        "or replace dependent rows with exact audited verified statements."
                    ),
                },
            )
            if debt not in item["debt_items"]:
                item["debt_items"].append(debt)
            if theorem_name and theorem_name not in item["needed_by"]:
                item["needed_by"].append(theorem_name)
                item["needed_by_details"].append(
                    {
                        "theorem_name": theorem_name,
                        "status": str(row.get("status", "") or ""),
                        "proof_method": str(row.get("proof_method", "") or ""),
                        "proof_value": proof_value_for_row(row),
                        "debt_tier": debt_tier_for_row(row),
                    }
                )
    obligations = list(grouped.values())
    for item in obligations:
        item["needed_by_count"] = len(item["needed_by"])
    obligations.sort(key=lambda item: (-int(item["needed_by_count"]), str(item["canonical_symbol"])))
    return {
        "schema_version": "1.0.0",
        "purpose": "Group deep-domain paper-theory debt into reusable local-theory obligations.",
        "total_obligations": len(obligations),
        "total_dependent_rows": sum(int(item["needed_by_count"]) for item in obligations),
        "obligations": obligations,
    }


def proof_value_for_row(row: dict[str, Any]) -> str:
    """Classify whether a row is real proof value or only bookkeeping debt."""
    if str(row.get("ledger_role", "") or "") == "audited_core_replacement":
        return "audited_real_claim"
    if bool(row.get("superseded_by_audited_core")):
        return "superseded_generated_diagnostic"
    statement = _row_statement(row)
    repair = row.get("translation_repair") if isinstance(row.get("translation_repair"), dict) else {}
    if (
        _target_atom(statement).endswith("RegeneratedStatement")
        or (
            str(repair.get("statement_repair_kind", "") or "") == "faithful_statement_regeneration"
            and "paper_theory_reference" in axiom_debt_items(row)
        )
    ):
        return "tautological_regenerated_statement"
    debts = axiom_debt_items(row)
    if any(debt.startswith("paper_definition_stub:") for debt in debts):
        return "definition_grounding"
    if any(marker in statement for marker in _DEEP_MARKERS) or any(
        _symbol(debt) in _DOMAIN_SYMBOLS or _symbol(debt) in _DEEP_MARKERS
        for debt in debts
    ):
        return "deep_domain_gap"
    if "paper_theory_reference" in debts:
        return "local_paper_theory_obligation"
    if debts:
        return "paper_local_debt"
    return "none"


def debt_tier_for_row(row: dict[str, Any]) -> str:
    """Tier rows by the next useful burn-down action."""
    proof_value = proof_value_for_row(row)
    if proof_value in {"audited_real_claim", "superseded_generated_diagnostic"}:
        return "audited_core_alignment"
    if proof_value == "tautological_regenerated_statement":
        return "regenerated_statement_tautology"
    if proof_value == "deep_domain_gap":
        return "deep_domain_theory"
    debts = axiom_debt_items(row)
    if any(debt.startswith("paper_definition_stub:") for debt in debts):
        return "definition_stub_grounding"
    if "paper_theory_reference" in debts:
        return "local_paper_theory"
    if "translation_repair_domain_assumption" in debts:
        return "regenerated_statement_tautology"
    if debts:
        return "local_paper_theory"
    return "none"


def summarize_paper_theory_debt_tiers(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize paper-theory debt without pretending all debt has equal proof value."""
    items: list[dict[str, Any]] = []
    for row in entries:
        if not isinstance(row, dict):
            continue
        tier = debt_tier_for_row(row)
        proof_value = proof_value_for_row(row)
        debts = axiom_debt_items(row)
        if tier == "none" and proof_value == "none":
            continue
        items.append(
            {
                "theorem_name": str(row.get("theorem_name", "") or ""),
                "status": str(row.get("status", "") or ""),
                "proof_method": str(row.get("proof_method", "") or ""),
                "debt_tier": tier,
                "proof_value": proof_value,
                "axiom_debt": debts,
                "grounding_metadata": [grounding_metadata_for_debt(debt) for debt in debts],
                "target_atom": _target_atom(_row_statement(row)),
                "ledger_role": str(row.get("ledger_role", "") or ""),
                "superseded_by_audited_core": bool(row.get("superseded_by_audited_core")),
            }
        )
    tier_counts = Counter(str(item["debt_tier"]) for item in items)
    proof_value_counts = Counter(str(item["proof_value"]) for item in items)
    actionable = [item for item in items if item["debt_tier"] not in {"none", "audited_core_alignment"}]
    return {
        "schema_version": "1.0.0",
        "purpose": "Separate paper-theory debt by proof value and next burn-down action.",
        "total_rows_with_debt_or_alignment": len(items),
        "actionable_debt_count": len(actionable),
        "counts_by_tier": dict(sorted(tier_counts.items())),
        "counts_by_proof_value": dict(sorted(proof_value_counts.items())),
        "deep_domain_obligations": build_deep_domain_obligations(entries),
        "items": items,
        "top_actionable_items": actionable[:20],
    }


def _symbol(debt_item: str) -> str:
    for prefix in ("paper_symbol:", "paper_definition_stub:", "paper_local_lemma:"):
        if debt_item.startswith(prefix):
            return debt_item[len(prefix) :]
    return ""


def _theorem_kind(theorem_name: str) -> str:
    base = theorem_name.rsplit(".", 1)[-1].lower()
    if base.startswith("def_"):
        return "definition"
    if base.startswith(("lem_", "lemma_")):
        return "lemma"
    if base.startswith(("ass_", "assumption_")):
        return "domain_assumption"
    return "theorem"


def _axiom_kind(debt_item: str, theorem_names: list[str]) -> str:
    debt = str(debt_item or "").strip()
    symbol = _symbol(debt)
    if debt in {"translation_repair_domain_assumption", "unclassified_axiom_backed"}:
        return "domain_assumption"
    if debt == "paper_theory_reference":
        return "lemma"
    if debt.startswith("paper_definition_stub:"):
        return "definition"
    if debt.startswith("paper_local_lemma:"):
        return "lemma"
    if symbol:
        if "estimate" in symbol or symbol.startswith(("lem_", "lemma_")):
            return "lemma"
        return "definition"
    if theorem_names:
        return _theorem_kind(theorem_names[0])
    return "domain_assumption"


def _dependency_bucket(debt_item: str, axiom_kind: str) -> str:
    debt = str(debt_item or "").strip()
    symbol = _symbol(debt)
    if debt == "translation_repair_domain_assumption":
        return "local_lemmas"
    if debt == "paper_theory_reference":
        return "local_lemmas"
    if debt.startswith("paper_definition_stub:"):
        return "missing_definitions_only"
    if debt.startswith("paper_local_lemma:"):
        return "local_lemmas"
    if symbol:
        if symbol in _DOMAIN_SYMBOLS:
            return "deep_domain_theory"
        if "estimate" in symbol or axiom_kind == "lemma":
            return "local_lemmas"
        return "missing_definitions_only"
    if axiom_kind == "definition":
        return "missing_definitions_only"
    if axiom_kind == "lemma":
        return "local_lemmas"
    return "unclassified"


def _difficulty(bucket: str, mathlib_candidates: list[str]) -> str:
    if bucket == "missing_definitions_only":
        return "easy" if mathlib_candidates else "medium"
    if bucket in {"local_lemmas", "missing_mathlib", "translation_artifacts"}:
        return "medium"
    if bucket == "deep_domain_theory":
        return "deep"
    return "hard"


def _explicit_in_paper(debt_item: str) -> bool | str:
    debt = str(debt_item or "").strip()
    if debt.startswith(("paper_symbol:", "paper_definition_stub:", "paper_local_lemma:")) or debt == "paper_theory_reference":
        return True
    if debt == "translation_repair_domain_assumption":
        return "unknown"
    return "unknown"


def _mathlib_candidates(debt_item: str) -> list[str]:
    symbol = _symbol(debt_item)
    return list(_MATHLIB_CLOSE.get(symbol, []))


def _norm_tokens(text: str) -> set[str]:
    return {tok.lower() for tok in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", text.replace("_", " "))}


def _earlier_claim_candidates(
    *,
    debt_item: str,
    first_needed_index: int,
    entries: list[dict[str, Any]],
) -> list[str]:
    symbol = _symbol(debt_item)
    if not symbol:
        return []
    needles = _norm_tokens(symbol.replace("_", " "))
    if not needles:
        return []
    candidates: list[str] = []
    for idx, row in enumerate(entries):
        if idx >= first_needed_index:
            break
        haystack = " ".join(
            [
                str(row.get("theorem_name", "") or ""),
                str(row.get("lean_statement", "") or ""),
                str(row.get("original_statement", "") or ""),
            ]
        )
        if needles & _norm_tokens(haystack):
            name = str(row.get("theorem_name", "") or "").strip()
            if name:
                candidates.append(name)
    return list(dict.fromkeys(candidates))


def _result_bucket_for_debts(debts: list[str]) -> str:
    if not debts:
        return "unclassified"
    kinds = [_axiom_kind(debt, []) for debt in debts]
    buckets = [_dependency_bucket(debt, kind) for debt, kind in zip(debts, kinds)]
    if "deep_domain_theory" in buckets:
        return "deep_domain_theory"
    if "translation_artifacts" in buckets:
        return "translation_artifacts"
    if "missing_mathlib" in buckets:
        return "missing_mathlib"
    if "local_lemmas" in buckets:
        return "local_lemmas"
    if all(bucket == "missing_definitions_only" for bucket in buckets):
        return "missing_definitions_only"
    return "unclassified"


def _summary_sentence(axiom_backed_count: int, counts: dict[str, int]) -> str:
    pieces = [
        f"{axiom_backed_count} axiom-backed result" + ("" if axiom_backed_count == 1 else "s")
    ]
    for bucket in (
        "missing_definitions_only",
        "local_lemmas",
        "missing_mathlib",
        "deep_domain_theory",
        "translation_artifacts",
        "unclassified",
    ):
        count = int(counts.get(bucket, 0))
        if count:
            label = _BUCKET_LABELS[bucket]
            if count == 1 and label.startswith("depend "):
                label = "depends " + label[len("depend ") :]
            if count == 1 and label.startswith("are "):
                label = "is " + label[len("are ") :]
            pieces.append(f"{count} {label}")
    return ", ".join(pieces) + "."


def build_axiom_debt_burndown(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a ranked report for retiring AXIOM_BACKED paper-local debt."""
    axiom_backed_rows = [
        (idx, row)
        for idx, row in enumerate(entries)
        if isinstance(row, dict) and str(row.get("status", "")).strip() == "AXIOM_BACKED"
    ]
    debt_rows = [
        (idx, row)
        for idx, row in enumerate(entries)
        if isinstance(row, dict) and (axiom_debt_items(row) or str(row.get("status", "")).strip() == "AXIOM_BACKED")
    ]

    grouped: dict[str, dict[str, Any]] = {}
    for idx, row in debt_rows:
        theorem_name = str(row.get("theorem_name", "") or "").strip()
        debts = axiom_debt_items(row) or ["unclassified_axiom_backed"]
        for debt in debts:
            item = grouped.setdefault(
                debt,
                {
                    "paper_local_axiom": debt,
                    "needed_by": [],
                    "needed_by_details": [],
                    "_first_needed_index": idx,
                },
            )
            if theorem_name and theorem_name not in item["needed_by"]:
                item["needed_by"].append(theorem_name)
                item["needed_by_details"].append(
                    {
                        "theorem_name": theorem_name,
                        "status": str(row.get("status", "") or ""),
                        "proof_method": str(row.get("proof_method", "") or ""),
                    }
                )
            item["_first_needed_index"] = min(int(item["_first_needed_index"]), idx)

    axiom_items: list[dict[str, Any]] = []
    for debt, item in grouped.items():
        needed_by = [str(x) for x in item["needed_by"]]
        kind = _axiom_kind(debt, needed_by)
        bucket = _dependency_bucket(debt, kind)
        mathlib_candidates = _mathlib_candidates(debt)
        difficulty = _difficulty(bucket, mathlib_candidates)
        earlier = _earlier_claim_candidates(
            debt_item=debt,
            first_needed_index=int(item["_first_needed_index"]),
            entries=entries,
        )
        can_prove_from_earlier: bool | str = "candidate" if earlier else False
        axiom_items.append(
            {
                "burn_down_id": hashlib.sha256(debt.encode("utf-8")).hexdigest()[:16],
                "paper_local_axiom": debt,
                "needed_by": needed_by,
                "needed_by_details": item["needed_by_details"],
                "needed_by_count": len(needed_by),
                "axiom_kind": kind,
                "appears_explicitly_in_paper": _explicit_in_paper(debt),
                "mathlib_has_close_match": bool(mathlib_candidates),
                "mathlib_candidates": mathlib_candidates,
                "can_be_proved_from_earlier_extracted_claims": can_prove_from_earlier,
                "earlier_extracted_claim_candidates": earlier,
                "dependency_bucket": bucket,
                "estimated_difficulty": difficulty,
                "difficulty_rank": _DIFFICULTY_RANK[difficulty],
            }
        )

    axiom_items.sort(
        key=lambda item: (
            int(item["difficulty_rank"]),
            int(item["needed_by_count"]),
            str(item["paper_local_axiom"]),
        )
    )

    result_buckets = Counter()
    for _, row in axiom_backed_rows:
        result_buckets[_result_bucket_for_debts(axiom_debt_items(row) or ["unclassified_axiom_backed"])] += 1
    result_counts = {bucket: int(result_buckets.get(bucket, 0)) for bucket in _BUCKET_LABELS}

    axiom_kind_counts = Counter(str(item["axiom_kind"]) for item in axiom_items)
    difficulty_counts = Counter(str(item["estimated_difficulty"]) for item in axiom_items)
    bucket_counts = Counter(str(item["dependency_bucket"]) for item in axiom_items)

    return {
        "schema_version": AXIOM_DEBT_BURNDOWN_SCHEMA_VERSION,
        "purpose": "Rank paper-local axioms by cheapest path to remove AXIOM_BACKED evidence.",
        "axiom_backed_result_count": len(axiom_backed_rows),
        "paper_local_axiom_count": len(axiom_items),
        "result_buckets": result_counts,
        "summary_sentence": _summary_sentence(len(axiom_backed_rows), result_counts),
        "axiom_kind_counts": dict(sorted(axiom_kind_counts.items())),
        "difficulty_counts": dict(sorted(difficulty_counts.items())),
        "dependency_bucket_counts": dict(sorted(bucket_counts.items())),
        "ranked_axioms": axiom_items,
    }
