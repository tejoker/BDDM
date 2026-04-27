#!/usr/bin/env python3
"""Full-paper formalization driver (from-scratch orchestration).

Runs:
1) arxiv_to_lean (ingest/extract/translate/prove bootstrap)
2) iterative prove_arxiv_batch passes with bridge loop
3) closure evaluation from verification ledger
4) unresolved theorem pack emission when not fully closed
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from axiom_debt_burndown import (
    build_deep_domain_obligations,
    build_axiom_debt_burndown,
    debt_tier_for_row,
    grounding_metadata_for_debt,
    grounding_metadata_for_row,
    proof_value_for_row,
    summarize_paper_theory_debt_tiers,
)
from claim_equivalence_review import (
    apply_adjudications_to_entries,
    build_review_queue,
    read_jsonl,
    summarize_review_queue,
    write_jsonl,
)
from pipeline_status import evaluate_promotion_gates, infer_claim_equivalence
from statement_validity import classify_statement, proof_repair_cohort, summarize_validity
from pipeline_status_models import (
    Assumption,
    ClaimEquivalenceVerdict,
    GroundingStatus,
    ProvenanceLink,
    ProofMethod,
    StepVerdict,
    TrustClass,
    VerificationStatus,
)

PAPER_LOCAL_AXIOM_RESULT_LABEL = "proved_modulo_paper_local_axioms"
PAPER_LOCAL_AXIOM_CLAIM_SCOPE = (
    "Lean checked this result only after accepting the listed paper-local "
    "axioms/declarations; do not count it as an unconditional verified theorem."
)
AXIOM_DEBT_CATEGORIES = (
    "easy_missing_definition",
    "local_paper_lemma",
    "missing_mathlib_theorem",
    "deep_domain_theory_gap",
    "bad_translation_artifact",
)
_DEBT_CATEGORY_PRIORITY = {
    "easy_missing_definition": 10,
    "bad_translation_artifact": 20,
    "local_paper_lemma": 30,
    "missing_mathlib_theorem": 50,
    "deep_domain_theory_gap": 90,
}

# ---------------------------------------------------------------------------
# Mathlib coverage pre-screening
# ---------------------------------------------------------------------------

# Core Mathlib namespaces available in Lean 4 Mathlib (as of 2025-2026).
# Domains not represented here are "library-limited": key types/theorems are absent.
_MATHLIB_NAMESPACE_COVERAGE: dict[str, list[str]] = {
    "algebra": [
        "Algebra", "LinearAlgebra", "RingTheory", "GroupTheory", "FieldTheory",
        "NumberTheory", "CategoryTheory", "Order", "Finset", "Multiset",
    ],
    "analysis": [
        "Analysis", "Topology", "MeasureTheory", "MeasurableSpace",
        "MetricSpace", "NormedSpace", "ContinuousLinearMap", "Asymptotics",
    ],
    "probability": [
        "ProbabilityTheory", "MeasureTheory", "Kernel", "MartingaleTheory",
        "StochasticProcess",
    ],
    "combinatorics": [
        "Combinatorics", "Finset", "Graph", "SimpleGraph", "Matroid",
    ],
    "geometry": [
        "Geometry", "EuclideanGeometry", "ConvexAnalysis", "Polytope",
    ],
    "logic": [
        "Logic", "Computability", "SetTheory",
    ],
}

# Domains where Mathlib coverage is sparse — trigger stub-and-check mode.
_LIBRARY_LIMITED_SIGNALS: list[tuple[str, str]] = [
    # (keyword in abstract/title, reason)
    ("schrödinger bridge", "optimal_transport_advanced"),
    ("schrodinger bridge", "optimal_transport_advanced"),
    ("wasserstein geodesic", "optimal_transport_advanced"),
    ("entropic optimal transport", "optimal_transport_advanced"),
    ("neural tangent kernel", "deep_learning_theory"),
    ("stochastic differential equation", "sde_ito_calculus"),
    ("itô integral", "sde_ito_calculus"),
    ("ito integral", "sde_ito_calculus"),
    ("stratonovich", "sde_ito_calculus"),
    ("kähler manifold", "complex_geometry"),
    ("kahler manifold", "complex_geometry"),
    ("ricci flow", "differential_geometry_advanced"),
    ("navier-stokes", "pde_advanced"),
    ("quantum channel", "quantum_information"),
    ("density matrix formalism", "quantum_information"),
    ("adversarial robustness", "ml_theory"),
]


def _score_mathlib_coverage(paper_text: str) -> dict[str, Any]:
    """Heuristic Mathlib coverage score for a paper's abstract or title text.

    Returns coverage fraction [0,1] and detected library-limited signals.
    """
    low = (paper_text or "").lower()
    signals: list[str] = []
    for kw, reason in _LIBRARY_LIMITED_SIGNALS:
        if kw in low:
            signals.append(reason)
    # Unique reasons.
    unique_signals = list(dict.fromkeys(signals))
    # Coverage degrades per distinct library-limited domain found.
    coverage = max(0.0, 1.0 - 0.35 * len(unique_signals))
    return {
        "coverage_score": round(coverage, 3),
        "library_limited": bool(unique_signals),
        "library_limited_reasons": unique_signals,
    }


def _safe_id(paper_id: str) -> str:
    return paper_id.replace("/", "_").replace(":", "_")


def _claim_equivalence_review_queue_summary(queue_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "review_queue_count": len(queue_rows),
        **summarize_review_queue(queue_rows),
    }


def _map_library_first_domain(domain: str) -> str:
    """Map user-friendly domain aliases to library_first_bootstrap choices."""
    d = (domain or "").strip().lower()
    if not d:
        return ""
    aliases = {
        # shorthand → library_first_bootstrap enum
        "probability": "probability_statistics",
        "prob": "probability_statistics",
        "probability_theory": "probability_statistics",
        "statistics": "probability_statistics",
        "analysis": "analysis_pde",
        "pde": "analysis_pde",
        "algebra": "algebra_number_theory",
        "number_theory": "algebra_number_theory",
        "nt": "algebra_number_theory",
        "combinatorics": "remaining_cs_math",
        "graph_theory": "remaining_cs_math",
        "graph": "remaining_cs_math",
        "discrete_math": "remaining_cs_math",
        "cs_math_logic": "remaining_cs_math",
        "logic": "remaining_cs_math",
        "custom_macros": "remaining_cs_math",
        "ugly_latex": "remaining_cs_math",
        "ml_statistics_theory": "probability_statistics",
        "ml_statistics": "probability_statistics",
        "optimization": "optimization",
        "cs_math": "remaining_cs_math",
        "remaining": "remaining_cs_math",
    }
    return aliases.get(d, domain)


def _ledger_path(project_root: Path, paper_id: str) -> Path:
    return project_root / "output" / "verification_ledgers" / f"{_safe_id(paper_id)}.json"


def _load_ledger_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
        return [r for r in raw.get("entries", []) if isinstance(r, dict)]
    return []


def _save_ledger_entries(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"entries": entries}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _publish_reproducibility_bundle(
    *,
    project_root: Path,
    paper_id: str,
    report_out: Path,
    ledger_path: Path,
    unresolved_out: Path,
    missing_lemma_out: Path | None = None,
    axiom_debt_burndown_out: Path | None = None,
    statement_validity_out: Path | None = None,
    proof_repair_cohort_out: Path | None = None,
    paper_theory_manifest: Path | None = None,
    claim_equivalence_review_out: Path | None = None,
    claim_equivalence_adjudications: Path | None = None,
) -> dict[str, str]:
    """Copy the report bundle into committed reproducibility/ evidence paths."""
    safe = _safe_id(paper_id)
    bundle_dir = project_root / "reproducibility" / "full_paper_reports" / safe
    bundle_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for label, src, dst_name in (
        ("report", report_out, "suite_report.json"),
        ("ledger", ledger_path, "verification_ledger.json"),
        ("unresolved", unresolved_out, "unresolved.json"),
        ("missing_lemmas", missing_lemma_out, "missing_lemmas.json"),
        ("axiom_debt_burndown", axiom_debt_burndown_out, "axiom_debt_burndown.json"),
        ("statement_validity", statement_validity_out, "statement_validity.json"),
        ("proof_repair_cohort", proof_repair_cohort_out, "proof_repair_cohort.json"),
        ("paper_theory_manifest", paper_theory_manifest, "paper_theory_manifest.json"),
        ("claim_equivalence_review_queue", claim_equivalence_review_out, "claim_equivalence_review_queue.jsonl"),
        ("claim_equivalence_adjudications", claim_equivalence_adjudications, "claim_equivalence_adjudications.jsonl"),
    ):
        if src is None:
            continue
        if not src.exists():
            continue
        dst = bundle_dir / dst_name
        shutil.copyfile(src, dst)
        paths[label] = str(dst)
    manifest = {
        "paper_id": paper_id,
        "schema_version": "1.0.0",
        "bundle_dir": str(bundle_dir),
        "files": paths,
        "regenerate_command": (
            "python3 scripts/formalize_paper_full.py "
            f"--paper-id {paper_id} --project-root ."
        ),
    }
    manifest_path = bundle_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    paths["manifest"] = str(manifest_path)
    return paths


def _decl_target(lean_statement: str) -> str:
    stmt = (lean_statement or "").strip()
    if not stmt:
        return ""
    stmt = re.sub(r":=\s*by\b.*$", "", stmt, flags=re.DOTALL).strip()
    depth_paren = depth_bracket = depth_brace = 0
    last_colon = -1
    for i, ch in enumerate(stmt):
        if ch == "(":
            depth_paren += 1
        elif ch == ")":
            depth_paren = max(0, depth_paren - 1)
        elif ch == "[":
            depth_bracket += 1
        elif ch == "]":
            depth_bracket = max(0, depth_bracket - 1)
        elif ch == "{":
            depth_brace += 1
        elif ch == "}":
            depth_brace = max(0, depth_brace - 1)
        elif ch == ":" and depth_paren == depth_bracket == depth_brace == 0:
            last_colon = i
    if last_colon < 0:
        return ""
    return stmt[last_colon + 1 :].strip()


def _translation_limited_reason(lean_statement: str) -> str:
    stmt = " ".join((lean_statement or "").split())
    if not stmt:
        return "empty_translation"
    target = _decl_target(lean_statement)
    if re.search(r"\(p_c\d+\s*:\s*Prop\)\s*\(h_c\d+\s*:\s*p_c\d+\)", stmt):
        return "schema_placeholder_identity"
    if re.search(r"\(p_c\d+\s*:\s*Prop\)", stmt):
        return "prop_slot_placeholder"
    if re.search(r"\b(?:schema_translation|schema_fallback|literal_schema_translation)\b", stmt):
        return "schema_translation_placeholder"
    if target:
        if re.fullmatch(r"True", target):
            return "trivial_true_target"
        if re.fullmatch(r"\(?\s*0\s*:\s*ℕ\s*\)?\s*=\s*0", target):
            return "trivial_nat0eq0_target"
        if re.fullmatch(
            r"(?:[A-Za-z_][A-Za-z0-9_']*\s*(?:∧\s*)?)+→\s*\(?\s*0\s*:\s*ℕ\s*\)?\s*=\s*0",
            target,
        ):
            prop_binders = set(re.findall(r"\((h\d+)\s*:\s*Prop\)", stmt))
            target_tokens = set(re.findall(r"\b(h\d+)\b", target.split("→", 1)[0]))
            if target_tokens and target_tokens <= prop_binders:
                return "relaxed_prop_trivial_nat_implication"
        if re.fullmatch(r"Nonempty\s*\(?Unit\)?", target):
            return "nonempty_unit_placeholder"
    return ""


def _semantic_hard_failure_reason(row: dict[str, Any]) -> str:
    err = str(row.get("error_message", "") or "").lower()
    flags: list[str] = []
    for key in (
        "translation_uncertainty_flags",
        "translation_adversarial_flags",
        "translation_roundtrip_flags",
        "gate_failures",
        "claim_equivalence_notes",
    ):
        val = row.get(key)
        if isinstance(val, list):
            flags.extend(str(x).lower() for x in val)
    joined = " ".join([err, *flags])
    hard_markers = (
        "semantic_policy_hard_block",
        "semantic_policy_violation",
        "trivialization_hard_violation",
        "claim_shape_mismatch",
        "schema_coverage_missing",
        "verdict:wrong",
        "roundtrip_semantic_mismatch",
    )
    for marker in hard_markers:
        if marker in joined:
            return marker
    return ""


def _definition_like_bad_statement_reason(lean_statement: str) -> str:
    target = _decl_target(lean_statement)
    if not target:
        return ""
    stmt = " ".join((lean_statement or "").split())
    if re.search(r"^\s*[a-z][A-Za-z0-9_']*\s*=\s*[\{\[]", target):
        if not re.search(r"\(h[_A-Za-z0-9']*\s*:", stmt):
            return "definition_like_unconstrained_equality"
    return ""


def _normalize_prop_for_statement_gate(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _weakened_by_target_hypothesis_reason(lean_statement: str) -> str:
    """Detect statements weakened to an explicit hypothesis of the same target."""
    target = _normalize_prop_for_statement_gate(_decl_target(lean_statement))
    if not target:
        return ""
    stmt = re.sub(r":=\s*by\b.*$", "", lean_statement or "", flags=re.DOTALL)
    for match in re.finditer(
        r"\(([hH][A-Za-z0-9_']*)\s*:\s*([^()]+?)\)",
        stmt,
        flags=re.DOTALL,
    ):
        name = match.group(1)
        typ = _normalize_prop_for_statement_gate(match.group(2))
        suspicious_name = re.search(r"(?:easy|claim|target|bound|conclusion|result)", name, re.IGNORECASE)
        relation_target = any(tok in target for tok in ("=", "≤", "≥", "<", ">", "↔", "∧", "∨", "∃", "∀"))
        if typ == target and (relation_target or suspicious_name):
            return f"claim_copied_into_hypothesis:{name}"
    return ""


def _ill_typed_translation_artifact_reason(lean_statement: str) -> str:
    stmt = " ".join((lean_statement or "").split())
    if not stmt:
        return ""
    artifact_patterns: list[tuple[str, str]] = [
        (r"\bI_i\s+[A-Za-z_][A-Za-z0-9_']*", "bare_paper_operator_application"),
        (r"\bC_T\s+HSobolev\b", "bare_function_space_application"),
        (r"\bB_N\^\{", "latex_superscript_artifact"),
        (r"\^\s*\([^)]*;", "semicolon_tuple_exponent_artifact"),
        (r"\|[^|]+\|\s*~\s*[A-Za-z0-9_']+", "latex_asymptotic_artifact"),
        (r"∥[^∥]+∥_[A-Za-z0-9_]+", "norm_suffix_artifact"),
        (r"\bd_dts\b", "latex_differential_artifact"),
        (r"\bComplex\.abs\b", "non_mathlib_complex_abs_artifact"),
        (r"\(([A-Z][A-Za-z0-9_']*)\s*:\s*\1\)", "type_name_used_as_term"),
    ]
    for pattern, reason in artifact_patterns:
        if re.search(pattern, stmt):
            return reason
    return ""


def _normalize_final_ledger_entries(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Apply final paper-level classifications before computing closure metrics."""
    counts = {
        "translation_limited_placeholders": 0,
        "semantic_hard_flawed": 0,
        "bad_statement_flawed": 0,
        "ill_typed_artifact_flawed": 0,
        "weakened_statement_flawed": 0,
        "domain_assumption_repair_flawed": 0,
    }
    normalized: list[dict[str, Any]] = []
    for row in entries:
        if not isinstance(row, dict):
            continue
        r = dict(row)
        status = str(r.get("status", "") or "")
        debt = _axiom_debt_list(r)
        repair = r.get("translation_repair") if isinstance(r.get("translation_repair"), dict) else {}
        repair_kind = str(r.get("repair_abstraction_kind", "") or repair.get("repair_abstraction_kind", "") or "")
        diagnostic_domain_repair = (
            "translation_repair_domain_assumption" in debt
            and (repair_kind == "paper_claim_diagnostic" or "PaperClaim" in str(r.get("lean_statement", "") or ""))
        )
        if status != "FULLY_PROVEN" and diagnostic_domain_repair:
            r["status"] = "FLAWED"
            r["proved"] = False
            r["proof_method"] = "translation_repair_diagnostic"
            r["failure_origin"] = "FORMALIZATION_ERROR"
            r["error_message"] = "final_bad_statement:translation_repair_domain_assumption"
            failures = [str(x) for x in (r.get("gate_failures") or [])]
            for failure in (
                "translation_repair_domain_assumption",
                "translation_fidelity_ok",
                "claim_equivalent",
            ):
                if failure not in failures:
                    failures.append(failure)
            r["gate_failures"] = failures
            vg = r.get("validation_gates")
            if not isinstance(vg, dict):
                vg = {}
            vg.update(
                {
                    "lean_proof_closed": False,
                    "translation_fidelity_ok": False,
                    "domain_assumption_repair": False,
                    "claim_equivalent": False,
                }
            )
            r["validation_gates"] = vg
            counts["domain_assumption_repair_flawed"] += 1
            normalized.append(_with_result_label(r))
            continue
        if status != "FULLY_PROVEN":
            reason = _translation_limited_reason(str(r.get("lean_statement", "") or ""))
            if reason:
                r["status"] = "TRANSLATION_LIMITED"
                r["proved"] = False
                r["proof_method"] = "translation_limited"
                r["error_message"] = f"final_translation_gate:{reason}"
                r["gate_failures"] = ["translation_limited_statement"]
                vg = r.get("validation_gates")
                if not isinstance(vg, dict):
                    vg = {}
                vg.update(
                    {
                        "lean_proof_closed": False,
                        "step_verdict_verified": False,
                        "translation_fidelity_ok": False,
                        "claim_equivalent": False,
                    }
                )
                r["validation_gates"] = vg
                counts["translation_limited_placeholders"] += 1
            else:
                hard_reason = _semantic_hard_failure_reason(r)
                bad_stmt_reason = _definition_like_bad_statement_reason(str(r.get("lean_statement", "") or ""))
                artifact_reason = _ill_typed_translation_artifact_reason(str(r.get("lean_statement", "") or ""))
                weakened_reason = _weakened_by_target_hypothesis_reason(str(r.get("lean_statement", "") or ""))
                if hard_reason or bad_stmt_reason or artifact_reason or weakened_reason:
                    r["status"] = "FLAWED"
                    r["failure_origin"] = "FORMALIZATION_ERROR"
                    final_reason = hard_reason or bad_stmt_reason or artifact_reason or weakened_reason
                    r["error_message"] = (
                        f"final_semantic_hard_block:{final_reason}"
                        if hard_reason
                        else (
                            f"final_bad_statement:{final_reason}"
                            if bad_stmt_reason
                            else (
                                f"final_ill_typed_translation_artifact:{final_reason}"
                                if artifact_reason
                                else f"final_weakened_statement:{final_reason}"
                            )
                        )
                    )
                    failures = [str(x) for x in (r.get("gate_failures") or [])]
                    if "translation_fidelity_ok" not in failures:
                        failures.append("translation_fidelity_ok")
                    if "claim_equivalent" not in failures:
                        failures.append("claim_equivalent")
                    r["gate_failures"] = failures
                    if hard_reason:
                        counts["semantic_hard_flawed"] += 1
                    elif bad_stmt_reason:
                        counts["bad_statement_flawed"] += 1
                    elif artifact_reason:
                        counts["ill_typed_artifact_flawed"] += 1
                    else:
                        counts["weakened_statement_flawed"] += 1
        normalized.append(_with_result_label(r))
    return normalized, counts


def _axiom_debt_list(row: dict[str, Any]) -> list[str]:
    debt = row.get("axiom_debt", [])
    if isinstance(debt, list):
        return [str(x) for x in debt if str(x).strip()]
    if isinstance(debt, str) and debt.strip():
        return [debt]
    return []


def _result_label_for_row(row: dict[str, Any]) -> tuple[str, str, bool]:
    if bool(row.get("superseded_by_audited_core")):
        return (
            "superseded_generated_diagnostic",
            "Generated row is retained for traceability only; the audited-core replacement row carries any verified claim.",
            False,
        )
    status = str(row.get("status", "") or "").strip()
    proof_method = str(row.get("proof_method", "") or "").strip().lower()
    debt = _axiom_debt_list(row)
    has_axiom_debt = bool(debt)
    if "translation_repair_domain_assumption" in debt:
        return (
            "not_verified_translation_repair_domain_assumption",
            "No closure claim is made because the proof depends on a translation-repair domain assumption.",
            False,
        )
    if status == "AXIOM_BACKED" or proof_method == "domain_axiom":
        return PAPER_LOCAL_AXIOM_RESULT_LABEL, PAPER_LOCAL_AXIOM_CLAIM_SCOPE, True
    if has_axiom_debt:
        return (
            "not_verified_with_paper_local_axiom_debt",
            "No verified closure claim is made, and the statement still references paper-local axiom debt.",
            True,
        )
    if status == "FULLY_PROVEN":
        return (
            "lean_verified_without_paper_local_axioms",
            "Lake-verified Lean proof with no paper-local axiom debt recorded.",
            False,
        )
    if status == "TRANSLATION_LIMITED":
        return (
            "not_formalized_translation_limited",
            "Excluded from closure because the generated statement is translation- or library-limited.",
            False,
        )
    return (
        "not_verified",
        "No verified closure claim is made for this result.",
        False,
    )


def _with_result_label(row: dict[str, Any]) -> dict[str, Any]:
    result_label, claim_scope, modulo_paper_local_axioms = _result_label_for_row(row)
    out = dict(row)
    out["debt_tier"] = debt_tier_for_row(row)
    out["proof_value"] = proof_value_for_row(row)
    out["grounding_metadata"] = grounding_metadata_for_row(row)
    out["result_label"] = result_label
    out["claim_scope"] = claim_scope
    out["modulo_paper_local_axioms"] = modulo_paper_local_axioms
    if result_label == PAPER_LOCAL_AXIOM_RESULT_LABEL:
        out["closure_claim"] = PAPER_LOCAL_AXIOM_RESULT_LABEL
    elif result_label == "lean_verified_without_paper_local_axioms":
        out["closure_claim"] = result_label
    else:
        out["closure_claim"] = "not_closed"
    if modulo_paper_local_axioms:
        out["paper_local_axiom_debt"] = _axiom_debt_list(row)
    else:
        out.pop("paper_local_axiom_debt", None)
    return out


def _label_final_ledger_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_with_result_label(row) for row in entries if isinstance(row, dict)]


def _safe_lemma_id(raw: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]", "_", raw or "missing")
    safe = re.sub(r"_+", "_", safe).strip("_") or "missing"
    if not re.match(r"[A-Za-z_]", safe):
        safe = "missing_" + safe
    return safe


def _debt_symbol(debt_item: str) -> str:
    for prefix in ("paper_symbol:", "paper_definition_stub:", "paper_local_lemma:"):
        if debt_item.startswith(prefix):
            return debt_item[len(prefix):]
    return ""


def _classify_axiom_debt_item(row: dict[str, Any], debt_item: str) -> str:
    debt = str(debt_item or "").strip()
    err = str(row.get("error_message", "") or "").lower()
    stmt = str(row.get("lean_statement", "") or "")
    repair = row.get("translation_repair") if isinstance(row.get("translation_repair"), dict) else {}
    repair_kind = str(row.get("repair_abstraction_kind", "") or repair.get("repair_abstraction_kind", "") or "")
    if (
        "final_ill_typed_translation_artifact" in err
        or "final_bad_statement" in err
        or "abstract_schema_placeholder" in err
        or "abstract_type_name_operator_claim" in err
        or repair_kind == "paper_claim_diagnostic"
        or "PaperClaim" in stmt
        or _ill_typed_translation_artifact_reason(stmt)
        or _definition_like_bad_statement_reason(stmt)
    ):
        return "bad_translation_artifact"

    symbol = _debt_symbol(debt)
    if symbol:
        if debt.startswith("paper_local_lemma:"):
            return "local_paper_lemma"
        if symbol in {"HSobolev", "L2Space", "C_T"}:
            return "easy_missing_definition"
        if symbol in {"I_i", "cutoff_solution", "paracontrolled_solution", "cutoff_enhanced_data"}:
            return "deep_domain_theory_gap"
        return "local_paper_lemma"

    if debt == "translation_repair_domain_assumption":
        return "local_paper_lemma"
    if debt == "paper_theory_reference":
        return "local_paper_lemma"
    if "unknown identifier" in err or "missing_mathlib" in err or "mathlib" in err:
        return "missing_mathlib_theorem"
    return "deep_domain_theory_gap"


def _suggested_action_for_debt(category: str, debt_item: str) -> str:
    symbol = _debt_symbol(debt_item)
    if category == "easy_missing_definition":
        return (
            f"Replace paper-local symbol `{symbol}` with a conservative Lean definition "
            "or a domain-pack definition, then re-run the theorem without axiom debt."
        )
    if category == "bad_translation_artifact":
        return "Repair the generated Lean statement before proof search; do not prove the artifact."
    if category == "local_paper_lemma":
        return "Extract the paper lemma/data assumption as a named Lean lemma and prove it before the theorem."
    if category == "missing_mathlib_theorem":
        return "Search Mathlib for an equivalent theorem or add a small bridge lemma in the domain pack."
    return "Requires a domain-pack extension or imported formal library before this theorem can be unconditional."


def _missing_lemma_name(theorem_name: str, debt_item: str) -> str:
    base = _safe_lemma_id(theorem_name.rsplit(".", 1)[-1] if theorem_name else "theorem")
    symbol = _safe_lemma_id(_debt_symbol(debt_item) or debt_item)
    digest = hashlib.sha256(f"{theorem_name}\n{debt_item}".encode("utf-8")).hexdigest()[:8]
    return f"missing_{base}_{symbol}_{digest}"


def _build_missing_lemma_subledger(entries: list[dict[str, Any]]) -> dict[str, Any]:
    obligations: list[dict[str, Any]] = []
    definition_grounding: dict[str, dict[str, Any]] = {}
    seen: set[tuple[str, str]] = set()
    for row in entries:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status", "") or "")
        theorem_name = str(row.get("theorem_name", "") or "")
        debt_items = _axiom_debt_list(row)
        if status == "AXIOM_BACKED" and not debt_items:
            debt_items = ["unclassified_axiom_backed"]
        for debt_item in debt_items:
            if debt_item.startswith("paper_definition_stub:"):
                symbol = _debt_symbol(debt_item) or debt_item
                grounding = grounding_metadata_for_debt(debt_item)
                item = definition_grounding.setdefault(
                    symbol,
                    {
                        "symbol": symbol,
                        "debt_item": debt_item,
                        "needed_by": [],
                        "needed_by_count": 0,
                        "mathlib_close_match": symbol in {"L2Space", "HSobolev"},
                        "proof_value": "definition_grounding",
                        "proof_countable": False,
                        "grounding_metadata": grounding,
                        "suggested_action": _suggested_action_for_debt("easy_missing_definition", debt_item),
                    },
                )
                if theorem_name and theorem_name not in item["needed_by"]:
                    item["needed_by"].append(theorem_name)
                    item["needed_by_count"] = len(item["needed_by"])
                continue
            key = (theorem_name, debt_item)
            if key in seen:
                continue
            seen.add(key)
            category = _classify_axiom_debt_item(row, debt_item)
            priority = _DEBT_CATEGORY_PRIORITY.get(category, 100)
            grounding = grounding_metadata_for_debt(debt_item)
            obligations.append(
                {
                    "obligation_id": hashlib.sha256(
                        f"{theorem_name}\n{debt_item}".encode("utf-8")
                    ).hexdigest()[:16],
                    "theorem_name": theorem_name,
                    "status": status,
                    "proof_method": str(row.get("proof_method", "") or ""),
                    "debt_item": debt_item,
                    "debt_category": category,
                    "grounding_metadata": grounding,
                    "proof_countable": bool(grounding.get("proof_countable")),
                    "requires_verified_replacement": bool(grounding.get("requires_verified_replacement")),
                    "attack_priority": priority,
                    "missing_lemma_name": _missing_lemma_name(theorem_name, debt_item),
                    "suggested_action": _suggested_action_for_debt(category, debt_item),
                    "axiom_debt_hash": str(row.get("axiom_debt_hash", "") or ""),
                    "result_label": str(row.get("result_label", "") or _result_label_for_row(row)[0]),
                }
            )

    obligations.sort(key=lambda item: (int(item["attack_priority"]), str(item["theorem_name"]), str(item["debt_item"])))
    counts = {category: 0 for category in AXIOM_DEBT_CATEGORIES}
    for item in obligations:
        category = str(item["debt_category"])
        counts[category] = counts.get(category, 0) + 1
    definition_items = sorted(
        definition_grounding.values(),
        key=lambda item: (int(item["needed_by_count"]), str(item["symbol"])),
    )
    return {
        "schema_version": "1.1.0",
        "purpose": "Queue for replacing paper-local axioms with unconditional Lean lemmas/definitions.",
        "total_obligations": len(obligations),
        "counts_by_category": counts,
        "definition_stub_grounding": {
            "count": len(definition_items),
            "total_references": sum(int(item["needed_by_count"]) for item in definition_items),
            "items": definition_items,
            "claim_scope": "Definition stubs are reported separately from theorem/lemma obligations; they do not count as proved paper claims.",
        },
        "attack_queue": obligations,
    }


def _repair_candidate_summary(payload: dict[str, Any]) -> dict[str, Any]:
    candidates = payload.get("repair_candidates", []) if isinstance(payload, dict) else []
    if not isinstance(candidates, list):
        candidates = []
    counts = payload.get("candidate_counts", {}) if isinstance(payload, dict) else {}
    if not isinstance(counts, dict):
        counts = {}
    changed_ok = [
        {
            "theorem_name": str(c.get("theorem_name", "")),
            "changes": list(c.get("changes", []) or []),
            "repair_abstraction_kind": str(c.get("repair_abstraction_kind", "") or ""),
            "statement_repair_kind": str(c.get("statement_repair_kind", "") or ""),
            "direct_tactic": str(c.get("direct_tactic", "") or ""),
            "domain_assumption_backed": bool(c.get("domain_assumption_backed")),
            "direct_proof_without_repair": bool(c.get("direct_proof_without_repair")),
        }
        for c in candidates
        if isinstance(c, dict)
        and (c.get("changes") or c.get("direct_proof_without_repair"))
        and (c.get("lean_validation") or {}).get("ok") is True
    ]
    return {
        "available": bool(payload),
        "summary_json": str(payload.get("summary_json", "")) if isinstance(payload, dict) else "",
        "repair_theory": str(payload.get("repair_theory", "") or payload.get("paper_theory", "")) if isinstance(payload, dict) else "",
        "retry_lean_file": str(payload.get("retry_lean_file", "")) if isinstance(payload, dict) else "",
        "retry_queue_json": str(payload.get("retry_queue_json", "")) if isinstance(payload, dict) else "",
        "retry_candidate_count": int(payload.get("retry_candidate_count", 0) or 0) if isinstance(payload, dict) else 0,
        "candidate_counts": counts,
        "changed_elaborating_theorems": changed_ok,
        "direct_proof_count": sum(1 for c in changed_ok if c.get("direct_tactic")),
        "domain_assumption_backed_count": sum(1 for c in changed_ok if c.get("domain_assumption_backed")),
        "faithful_statement_regeneration_count": sum(
            1 for c in changed_ok if c.get("statement_repair_kind") == "faithful_statement_regeneration"
        ),
    }


def _apply_validated_translation_repairs(
    entries: list[dict[str, Any]],
    repair_payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Replace bad ledger statements with changed, elaborating repair candidates."""
    candidates = repair_payload.get("repair_candidates", []) if isinstance(repair_payload, dict) else []
    if not isinstance(candidates, list):
        candidates = []
    by_name: dict[str, dict[str, Any]] = {}
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        name = str(cand.get("theorem_name", "") or "").rsplit(".", 1)[-1]
        if not name or not cand.get("changes"):
            if not cand.get("direct_proof_without_repair"):
                continue
        if (cand.get("lean_validation") or {}).get("ok") is not True:
            continue
        repaired = str(cand.get("repaired_decl", "") or "").strip()
        if not repaired:
            continue
        by_name[name] = cand

    updated: list[str] = []
    out: list[dict[str, Any]] = []
    for row in entries:
        r = dict(row)
        base = str(r.get("theorem_name", "") or "").rsplit(".", 1)[-1]
        cand = by_name.get(base)
        if cand and str(r.get("status", "") or "") != "FULLY_PROVEN":
            changes = list(cand.get("changes", []) or [])
            direct_tactic = str(cand.get("direct_tactic", "") or "")
            domain_backed = bool(cand.get("domain_assumption_backed"))
            direct_without_repair = bool(cand.get("direct_proof_without_repair"))
            repair_kind = str(cand.get("repair_abstraction_kind", "") or "")
            statement_repair_kind = str(cand.get("statement_repair_kind", "") or "")
            is_paper_claim_diagnostic = repair_kind == "paper_claim_diagnostic"
            r["lean_statement"] = str(cand.get("repaired_decl", "") or "").strip()
            if is_paper_claim_diagnostic:
                r["status"] = "FLAWED"
                r["proved"] = False
                r["proof_text"] = ""
                r["proof_method"] = "translation_repair_diagnostic"
                r["error_message"] = "translation_repair_diagnostic_paper_claim_abstraction:" + ",".join(str(x) for x in changes)
                r["gate_failures"] = ["no_paper_axiom_debt", "translation_fidelity_ok", "claim_equivalent"]
                debt = [str(x) for x in (r.get("axiom_debt") or []) if str(x).strip()]
                debt.append("translation_repair_domain_assumption")
                r["axiom_debt"] = list(dict.fromkeys(debt))
            elif direct_tactic and domain_backed and not direct_without_repair:
                r["status"] = "FLAWED"
                r["proved"] = False
                r["proof_text"] = ""
                r["proof_method"] = "translation_repair_diagnostic"
                r["error_message"] = "translation_repair_domain_assumption_inserted:" + ",".join(str(x) for x in changes)
                r["gate_failures"] = [
                    "translation_repair_domain_assumption",
                    "translation_fidelity_ok",
                    "claim_equivalent",
                ]
                debt = [str(x) for x in (r.get("axiom_debt") or []) if str(x).strip()]
                debt.append("translation_repair_domain_assumption")
                r["axiom_debt"] = list(dict.fromkeys(debt))
            elif direct_tactic and direct_without_repair:
                r["status"] = "AXIOM_BACKED"
                r["proved"] = True
                r["proof_text"] = direct_tactic
                r["proof_method"] = "domain_axiom"
                r["error_message"] = "direct_paper_statement_proved_modulo_paper_local_axioms"
                r["gate_failures"] = ["no_paper_axiom_debt"]
            else:
                r["status"] = "UNRESOLVED"
                r["proved"] = False
                r["proof_method"] = "translation_repaired_pending_proof"
                r["error_message"] = "translation_repaired_pending_proof:" + ",".join(str(x) for x in changes)
                cand_debt = [str(x) for x in (cand.get("paper_theory_debt") or []) if str(x).strip()]
                r["gate_failures"] = (
                    ["lean_proof_closed", "no_paper_axiom_debt"]
                    if statement_repair_kind == "faithful_statement_regeneration" and cand_debt
                    else ["lean_proof_closed"]
                )
                if statement_repair_kind == "faithful_statement_regeneration":
                    debt = [
                        str(x)
                        for x in (r.get("axiom_debt") or [])
                        if str(x).strip() and str(x).strip() != "translation_repair_domain_assumption"
                    ]
                    debt.extend(cand_debt)
                    r["axiom_debt"] = list(dict.fromkeys(debt))
            vg = r.get("validation_gates")
            if not isinstance(vg, dict):
                vg = {}
            repair_outcome = "direct_existing_hypothesis"
            if domain_backed and not direct_without_repair:
                repair_outcome = "domain_assumption_inserted"
            elif statement_repair_kind == "faithful_statement_regeneration":
                repair_outcome = "faithful_statement_regeneration"
            elif changes:
                repair_outcome = "statement_repaired_and_elaborates"
            vg.update(
                {
                    "translation_repair_applied": True,
                    "lean_statement_elaborates_after_repair": True,
                    "lean_proof_closed": bool(not is_paper_claim_diagnostic and direct_tactic and direct_without_repair),
                    "domain_assumption_backed": bool(domain_backed),
                    "direct_proof_without_repair": bool(direct_without_repair),
                    "paper_claim_diagnostic": is_paper_claim_diagnostic,
                    "statement_repair_kind": statement_repair_kind,
                    "repair_outcome": repair_outcome,
                }
            )
            r["validation_gates"] = vg
            if repair_kind:
                r["repair_abstraction_kind"] = repair_kind
            r["translation_repair"] = {
                "changes": changes,
                "repair_abstraction_kind": repair_kind,
                "statement_repair_kind": statement_repair_kind,
                "repair_theory": str(repair_payload.get("repair_theory", "") or repair_payload.get("paper_theory", "")),
            }
            updated.append(str(r.get("theorem_name", "") or base))
        out.append(_with_result_label(r))

    return out, {
        "updated_count": len(updated),
        "updated_theorems": updated,
        "eligible_repair_count": len(by_name),
    }


def _enum_value(enum_cls: Any, value: Any, default: Any) -> Any:
    try:
        return enum_cls(str(value))
    except Exception:
        return default


def _float_field(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_field_or_gate(row: dict[str, Any], field: str, gate: str) -> bool | None:
    if row.get(field) is not None:
        return bool(row.get(field))
    gates = row.get("validation_gates")
    if isinstance(gates, dict) and gates.get(gate) is not None:
        return bool(gates.get(gate))
    return None


def _assumptions_from_row(row: dict[str, Any]) -> list[Assumption]:
    assumptions: list[Assumption] = []
    for item in row.get("assumptions") or []:
        if not isinstance(item, dict):
            continue
        assumptions.append(
            Assumption(
                label=str(item.get("label", "") or ""),
                lean_expr=str(item.get("lean_expr", "") or ""),
                grounding=_enum_value(GroundingStatus, item.get("grounding"), GroundingStatus.UNKNOWN),
                grounding_source=str(item.get("grounding_source", "") or ""),
                trust_class=_enum_value(TrustClass, item.get("trust_class"), TrustClass.TRUST_PLACEHOLDER),
                trust_reference=str(item.get("trust_reference", "") or ""),
            )
        )
    return assumptions


def _provenance_from_row(row: dict[str, Any]) -> ProvenanceLink | None:
    raw = row.get("provenance")
    if not isinstance(raw, dict):
        return None
    return ProvenanceLink(
        paper_id=str(raw.get("paper_id", "") or ""),
        section=str(raw.get("section", "") or ""),
        label=str(raw.get("label", "") or ""),
        cited_refs=[str(x) for x in (raw.get("cited_refs") or [])],
    )


def _claim_equivalence_from_row(row: dict[str, Any]) -> tuple[ClaimEquivalenceVerdict, list[str]]:
    notes = [str(x) for x in (row.get("claim_equivalence_notes") or [])]
    has_source_signals = any(
        key in row
        for key in (
            "translation_validated",
            "translation_fidelity_score",
            "status_alignment_score",
            "translation_uncertainty_flags",
            "translation_adversarial_flags",
            "translation_roundtrip_flags",
        )
    )
    if has_source_signals:
        verdict, inferred_notes = infer_claim_equivalence(
            translation_validated=(
                bool(row.get("translation_validated"))
                if row.get("translation_validated") is not None
                else None
            ),
            translation_fidelity_score=_float_field(row, "translation_fidelity_score"),
            status_alignment_score=_float_field(row, "status_alignment_score"),
            uncertainty_flags=[str(x) for x in (row.get("translation_uncertainty_flags") or [])],
            adversarial_flags=[str(x) for x in (row.get("translation_adversarial_flags") or [])],
            roundtrip_flags=[str(x) for x in (row.get("translation_roundtrip_flags") or [])],
        )
        artifact = row.get("semantic_equivalence_artifact")
        if (
            verdict == ClaimEquivalenceVerdict.UNCLEAR
            and isinstance(artifact, dict)
            and bool(artifact.get("independent_semantic_evidence"))
            and str(artifact.get("equivalence_verdict", "")).lower() == "equivalent"
        ):
            return ClaimEquivalenceVerdict.EQUIVALENT, list(
                dict.fromkeys([*inferred_notes, "equivalent_independent_semantic_evidence"])
            )
        return verdict, inferred_notes
    return (
        _enum_value(
            ClaimEquivalenceVerdict,
            row.get("claim_equivalence_verdict"),
            ClaimEquivalenceVerdict.UNCLEAR,
        ),
        notes,
    )


_INDEPENDENT_SEMANTIC_MARKERS = (
    "human_equivalent",
    "claim_equivalent:human",
    "semantic_equivalence:verified",
    "roundtrip_equivalent",
    "adversarial_passed",
    "equivalent_independent_semantic_evidence",
)


def _auto_core_semantic_evidence_present(
    row: dict[str, Any],
    item: dict[str, Any],
    reliable_payload: dict[str, Any],
) -> bool:
    values: list[str] = []
    keys = (
        "translation_uncertainty_flags",
        "translation_adversarial_flags",
        "translation_roundtrip_flags",
        "claim_equivalence_notes",
        "semantic_equivalence_evidence",
        "semantic_evidence",
        "independent_semantic_equivalence_evidence",
    )
    for source in (row, item, reliable_payload):
        for key in keys:
            raw = source.get(key)
            if isinstance(raw, list):
                values.extend(str(x) for x in raw)
            elif isinstance(raw, dict):
                values.extend(f"{k}:{v}" for k, v in raw.items())
            elif raw is not None:
                values.append(str(raw))
        equiv = source.get("semantic_equivalence")
        if isinstance(equiv, dict):
            verdict = str(equiv.get("verdict", "") or equiv.get("claim_equivalence_verdict", "")).lower()
            if bool(equiv.get("independent")) and verdict == "equivalent":
                return True
        if bool(source.get("semantic_equivalence_verified")):
            verdict = str(source.get("claim_equivalence_verdict", "") or "").lower()
            if verdict in {"", "equivalent"}:
                return True
        artifact = source.get("semantic_equivalence_artifact")
        if isinstance(artifact, dict):
            verdict = str(
                artifact.get("equivalence_verdict", "")
                or artifact.get("claim_equivalence_verdict", "")
            ).lower()
            if bool(artifact.get("independent_semantic_evidence")) and verdict == "equivalent":
                return True
    lowered = [v.lower() for v in values]
    return any(any(marker in value for marker in _INDEPENDENT_SEMANTIC_MARKERS) for value in lowered)


def _auto_core_fresh_lean_verification(
    item: dict[str, Any],
    reliable_payload: dict[str, Any],
    *,
    core_path: Path,
    core_text: str,
) -> dict[str, Any] | None:
    raw = item.get("lean_verification")
    if not isinstance(raw, dict):
        raw = reliable_payload.get("lean_verification")
    if not isinstance(raw, dict):
        return None
    if not bool(raw.get("ok", raw.get("verified", False))):
        return None

    actual_hash = hashlib.sha256(core_text.encode("utf-8")).hexdigest()
    expected_hash = str(raw.get("core_sha256", "") or raw.get("output_sha256", "") or raw.get("file_sha256", ""))
    if expected_hash != actual_hash:
        return None

    if raw.get("verified_at", raw.get("checked_at")) in ("", None):
        return None

    raw_path = str(raw.get("core_file", "") or raw.get("out", "") or raw.get("path", ""))
    if raw_path and Path(raw_path) != core_path:
        return None

    return {
        "ok": True,
        "verified_at": raw.get("verified_at", raw.get("checked_at")),
        "core_sha256": actual_hash,
        "method": str(raw.get("method", "") or "lake env lean"),
    }


def _auto_core_closure_claim_compatible(row: dict[str, Any]) -> bool:
    claim = str(row.get("closure_claim", "") or "").strip()
    compatible_claims = {"", "unverified", "not_closed", "lean_verified_without_paper_local_axioms"}
    return (
        claim in compatible_claims
        and not bool(row.get("modulo_paper_local_axioms", False))
        and not _axiom_debt_list(row)
    )


def _audited_auto_core_equivalence(item: dict[str, Any], lean_verification: dict[str, Any] | None) -> bool:
    if not lean_verification:
        return False
    verdict = str(item.get("claim_equivalence_verdict", "") or "").strip().lower()
    semantic = item.get("semantic_equivalence")
    semantic_verdict = ""
    if isinstance(semantic, dict):
        semantic_verdict = str(semantic.get("verdict", "") or semantic.get("claim_equivalence_verdict", "")).lower()
    return bool(
        item.get("semantic_equivalence_verified")
        and item.get("supersedes_paper_axiom_debt")
        and verdict == "equivalent"
        and (not semantic_verdict or semantic_verdict == "equivalent")
        and (not isinstance(semantic, dict) or bool(semantic.get("independent", True)))
    )


def _decl_without_proof(decl: str) -> str:
    out = re.sub(r":=\s*by\b.*$", "", decl or "", flags=re.DOTALL).strip()
    return re.sub(r":=\s*$", "", out).strip()


def _proof_body_from_decl(decl: str) -> str:
    match = re.search(r":=\s*by\s*(.*)$", decl or "", flags=re.DOTALL)
    return match.group(1).strip() if match else ""


def _audited_replacement_theorem_name(source_theorem: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_']", "_", source_theorem.rsplit(".", 1)[-1]).strip("_")
    if not base or not re.match(r"[A-Za-z_]", base):
        base = "theorem"
    return f"{base}__audited_core"


def _audited_replacement_row(
    *,
    generated_row: dict[str, Any],
    item: dict[str, Any],
    core_path: Path,
    lean_verification: dict[str, Any],
    semantic_evidence_ok: bool,
) -> dict[str, Any]:
    source_theorem = str(generated_row.get("theorem_name", "") or item.get("source_theorem", "") or "").rsplit(".", 1)[-1]
    core_theorem = str(item.get("theorem_name", "") or "")
    core_decl = str(item.get("core_declaration", "") or item.get("decl", "") or "")
    lean_statement = str(item.get("lean_statement", "") or "").strip() or _decl_without_proof(core_decl)
    proof_text = str(item.get("proof_text", "") or "").strip() or _proof_body_from_decl(core_decl) or str(item.get("tactic", "") or "")
    replacement_name = _audited_replacement_theorem_name(source_theorem or core_theorem)
    core_sha = str(lean_verification.get("core_sha256", "") or "")
    artifact = generated_row.get("semantic_equivalence_artifact")
    if not isinstance(artifact, dict):
        artifact = {}
    replacement_artifact = dict(artifact)
    replacement_artifact.update(
        {
            "lean_statement": lean_statement,
            "equivalence_verdict": "equivalent",
            "independent_semantic_evidence": True,
            "audited_core_source_theorem": source_theorem,
            "audited_core_theorem_name": core_theorem,
            "schema_version": str(replacement_artifact.get("schema_version", "1.0") or "1.0"),
        }
    )
    evidence = replacement_artifact.get("reviewer_evaluator_evidence")
    if not isinstance(evidence, list):
        evidence = []
    for marker in (
        "audited_auto_reliable_core_equivalence",
        "equivalent_independent_semantic_evidence",
        str(item.get("equivalence_note", "") or ""),
    ):
        if marker and marker not in evidence:
            evidence.append(marker)
    replacement_artifact["reviewer_evaluator_evidence"] = evidence
    audited_core = {
        "source_theorem": source_theorem,
        "core_theorem_name": core_theorem,
        "core_file": str(core_path),
        "core_sha256": core_sha,
        "verification_method": str(lean_verification.get("method", "") or "lake env lean"),
        "verified_at": lean_verification.get("verified_at"),
        "lean_statement": lean_statement,
        "proof_text": proof_text,
        "core_declaration": core_decl,
        "semantic_equivalence_verified": bool(item.get("semantic_equivalence_verified")),
        "semantic_equivalence": item.get("semantic_equivalence") if isinstance(item.get("semantic_equivalence"), dict) else {},
        "semantic_equivalence_evidence": "independent" if semantic_evidence_ok else "",
        "equivalence_note": str(item.get("equivalence_note", "") or ""),
        "proof_countable": True,
        "replacement_gate": {
            "exact_recorded_statement_required": True,
            "exact_recorded_proof_required": True,
            "fresh_lean_verification_required": True,
            "no_paper_axiom_debt_required": True,
            "audited_provenance_required": True,
        },
    }
    return _with_result_label(
        {
            "theorem_name": replacement_name,
            "ledger_role": "audited_core_replacement",
            "replaces_generated_theorem": source_theorem,
            "source_theorem": source_theorem,
            "supersedes_generated_row": str(generated_row.get("theorem_name", "") or source_theorem),
            "status": VerificationStatus.FULLY_PROVEN.value,
            "proved": True,
            "lean_file": str(core_path),
            "lean_statement": lean_statement,
            "proof_text": proof_text,
            "proof_mode": "audited-core-replacement",
            "proof_method": ProofMethod.LEAN_VERIFIED.value,
            "proof_countable": True,
            "step_verdict": StepVerdict.VERIFIED.value,
            "failure_origin": "NOT_FAILED",
            "failure_kind": "unknown",
            "trust_class": "TRUST_INTERNAL_PROVED",
            "trust_reference": (
                f"audited_core_replacement:{core_theorem};"
                f"source_theorem:{source_theorem};"
                f"core_sha256:{core_sha};"
                "semantic_equivalence:verified;lean_verification:fresh"
            ),
            "promotion_gate_passed": True,
            "validation_gates": {
                "lean_proof_closed": True,
                "step_verdict_verified": True,
                "claim_equivalent": True,
                "independent_semantic_equivalence_evidence": True,
                "semantic_adversarial_checks_passed": True,
                "no_paper_axiom_debt": True,
                "fresh_lean_verification_evidence": True,
                "ledger_records_audited_statement_proof_pair": True,
                "generated_row_not_mutated": True,
            },
            "gate_failures": [],
            "claim_equivalence_verdict": ClaimEquivalenceVerdict.EQUIVALENT.value,
            "claim_equivalence_notes": [
                "audited_auto_reliable_core_equivalence",
                "equivalent_independent_semantic_evidence",
            ],
            "semantic_equivalence_artifact": replacement_artifact,
            "review_required": False,
            "review_queue_id": "",
            "axiom_debt": [],
            "axiom_debt_hash": "",
            "closure_claim": "lean_verified_without_paper_local_axioms",
            "modulo_paper_local_axioms": False,
            "auto_reliable_core": {
                "theorem_name": core_theorem,
                "core_file": str(core_path),
                "strict_gate_passed": True,
                "strict_gate_failures": [],
                "lean_verification": lean_verification,
                "semantic_equivalence_evidence": "independent",
                "audited_equivalence_applied": True,
                "ledger_statement_verified_by_core": True,
            },
            "audited_core_replacement": audited_core,
            "rounds_used": 0,
            "time_s": 0.0,
            "error_message": "",
        }
    )


def _diagnostic_repair_row(row: dict[str, Any]) -> bool:
    repair = row.get("translation_repair") if isinstance(row.get("translation_repair"), dict) else {}
    repair_kind = str(row.get("repair_abstraction_kind", "") or repair.get("repair_abstraction_kind", "") or "")
    return repair_kind == "paper_claim_diagnostic" or "PaperClaim" in str(row.get("lean_statement", "") or "")


def _apply_auto_reliable_core_promotions(
    entries: list[dict[str, Any]],
    reliable_payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply no-sorry/no-axiom core evidence through the normal strict gates."""
    if not isinstance(reliable_payload, dict) or not reliable_payload.get("ok"):
        return entries, {"applied_count": 0, "promoted_count": 0, "promoted_theorems": [], "strict_gate_blocked_theorems": []}
    if int(reliable_payload.get("theorem_count", 0) or 0) <= 0:
        return entries, {"applied_count": 0, "promoted_count": 0, "promoted_theorems": [], "strict_gate_blocked_theorems": []}
    core_path = Path(str(reliable_payload.get("out", "") or ""))
    if not core_path.exists():
        return entries, {"applied_count": 0, "promoted_count": 0, "promoted_theorems": [], "strict_gate_blocked_theorems": []}
    try:
        core_text = core_path.read_text(encoding="utf-8")
    except Exception:
        return entries, {"applied_count": 0, "promoted_count": 0, "promoted_theorems": [], "strict_gate_blocked_theorems": []}
    if re.search(r"\b(?:axiom|sorry)\b", core_text):
        return entries, {
            "applied_count": 0,
            "promoted_count": 0,
            "promoted_theorems": [],
            "strict_gate_blocked_theorems": [],
            "blocked_reason": "core_contains_axiom_or_sorry",
        }

    by_source: dict[str, dict[str, Any]] = {}
    for item in reliable_payload.get("theorems", []) or []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source_theorem", "") or "").rsplit(".", 1)[-1]
        if source:
            by_source[source] = item

    promoted: list[str] = []
    strict_gate_blocked: list[dict[str, Any]] = []
    audited_evidence_only: list[dict[str, Any]] = []
    audited_replacements: list[dict[str, Any]] = []
    existing_replacements: list[dict[str, Any]] = []
    refreshed_replacement_sources: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in entries:
        if isinstance(row, dict) and str(row.get("ledger_role", "") or "") == "audited_core_replacement":
            existing_replacements.append(dict(row))
            continue
        r = dict(row)
        original_status = str(r.get("status", "") or "")
        base = str(r.get("theorem_name", "") or "").rsplit(".", 1)[-1]
        item = by_source.get(base)
        was_auto_core = bool(r.get("auto_reliable_core")) or str(r.get("proof_text", "")).startswith("auto_reliable_core:")
        if item and (str(r.get("status", "") or "") != "FULLY_PROVEN" or was_auto_core):
            theorem_name = str(item.get("theorem_name", "") or "")
            tactic = str(item.get("tactic", "") or "")
            equiv_verdict, equiv_notes = _claim_equivalence_from_row(r)
            semantic_evidence_ok = _auto_core_semantic_evidence_present(r, item, reliable_payload)
            lean_verification = _auto_core_fresh_lean_verification(
                item,
                reliable_payload,
                core_path=core_path,
                core_text=core_text,
            )
            audited_equivalence = _audited_auto_core_equivalence(item, lean_verification)
            if audited_equivalence:
                semantic_evidence_ok = True
                equiv_verdict = ClaimEquivalenceVerdict.EQUIVALENT
                equiv_notes = list(
                    dict.fromkeys(
                        [
                            *equiv_notes,
                            "audited_auto_reliable_core_equivalence",
                            "equivalent_independent_semantic_evidence",
                        ]
                    )
                )
            elif semantic_evidence_ok and equiv_verdict == ClaimEquivalenceVerdict.UNCLEAR:
                equiv_verdict = ClaimEquivalenceVerdict.EQUIVALENT
            closure_claim_ok = _auto_core_closure_claim_compatible(r) or bool(
                item.get("supersedes_paper_axiom_debt")
            )
            translation_fidelity_score = _float_field(r, "translation_fidelity_score")
            status_alignment_score = _float_field(r, "status_alignment_score")
            if bool(item.get("supersedes_paper_axiom_debt")):
                translation_fidelity_score = float(
                    item.get("translation_fidelity_score", translation_fidelity_score or 0.0)
                )
                status_alignment_score = float(
                    item.get("status_alignment_score", status_alignment_score or 0.0)
                )
            strict_status, strict_gates, strict_failures = evaluate_promotion_gates(
                status=VerificationStatus.FULLY_PROVEN,
                proved=True,
                step_verdict=StepVerdict.VERIFIED,
                assumptions=_assumptions_from_row(r),
                provenance=_provenance_from_row(r),
                project_root=None,
                translation_fidelity_score=translation_fidelity_score,
                status_alignment_score=status_alignment_score,
                dependency_trust_complete=_bool_field_or_gate(
                    r,
                    "dependency_trust_complete",
                    "dependency_trust_complete",
                ),
                reproducible_env=_bool_field_or_gate(r, "reproducible_env", "reproducible_env"),
                lean_statement=str(r.get("lean_statement", "") or ""),
                proof_text=tactic,
                run_independent_verify=False,
                claim_equivalence_verdict=equiv_verdict,
                independent_semantic_evidence=semantic_evidence_ok,
                semantic_adversarial_checks_passed=True,
                axiom_debt=[],
            )
            strict_gates["auto_reliable_core_verified"] = bool(lean_verification)
            strict_gates["independent_semantic_equivalence_evidence"] = semantic_evidence_ok
            strict_gates["fresh_lean_verification_evidence"] = bool(lean_verification)
            strict_gates["consistent_closure_claim"] = closure_claim_ok
            extra_failures: list[str] = []
            if not semantic_evidence_ok:
                extra_failures.append("independent_semantic_equivalence_evidence")
            if not lean_verification:
                extra_failures.append("fresh_lean_verification_evidence")
            if not closure_claim_ok:
                extra_failures.append("consistent_closure_claim")
            all_failures = list(dict.fromkeys([*strict_failures, *extra_failures]))
            diagnostic_repair = _diagnostic_repair_row(r)
            # Audited core equivalence is independent evidence about a separate
            # checked theorem. It is not, by itself, a proof of this ledger row's
            # recorded statement/proof pair.
            generated_axiom_debt = _axiom_debt_list(r)
            generated_axiom_backed_or_domain_repair = bool(
                generated_axiom_debt
                or original_status == "AXIOM_BACKED"
                or str(r.get("proof_method", "") or "").lower() == ProofMethod.DOMAIN_AXIOM.value
            )
            ledger_statement_directly_supported = (
                not audited_equivalence
                and not diagnostic_repair
                and not generated_axiom_backed_or_domain_repair
            )
            if not ledger_statement_directly_supported:
                support_failure = (
                    "generated_row_has_axiom_or_domain_assumption_debt"
                    if generated_axiom_backed_or_domain_repair
                    else "ledger_statement_not_verified_by_core"
                )
                all_failures = list(dict.fromkeys([*all_failures, support_failure]))
                strict_gates["ledger_statement_verified_by_core"] = False
            else:
                strict_gates["ledger_statement_verified_by_core"] = True
            full_allowed = (
                strict_status == VerificationStatus.FULLY_PROVEN
                and not all_failures
                and ledger_statement_directly_supported
            )
            final_status = (
                VerificationStatus.FULLY_PROVEN
                if full_allowed
                else (
                    _enum_value(VerificationStatus, original_status, VerificationStatus.UNRESOLVED)
                    if diagnostic_repair or original_status in {"AXIOM_BACKED", "FLAWED", "TRANSLATION_LIMITED"}
                    else VerificationStatus.INTERMEDIARY_PROVEN
                )
            )
            r["status"] = final_status.value
            r["proved"] = bool(full_allowed)
            r["proof_method"] = (
                ProofMethod.LEAN_VERIFIED.value if full_allowed else "auto_reliable_core_evidence"
            )
            r["proof_text"] = f"auto_reliable_core:{theorem_name}"
            r["error_message"] = (
                ""
                if full_allowed
                else "auto_reliable_core_strict_gates_failed:" + ",".join(all_failures)
            )
            r["gate_failures"] = all_failures
            r["validation_gates"] = strict_gates
            r["claim_equivalence_verdict"] = equiv_verdict.value
            if semantic_evidence_ok and "equivalent_independent_semantic_evidence" not in equiv_notes:
                equiv_notes = [*equiv_notes, "equivalent_independent_semantic_evidence"]
            r["claim_equivalence_notes"] = equiv_notes
            artifact = r.get("semantic_equivalence_artifact")
            if not isinstance(artifact, dict):
                artifact = {
                    "original_latex_theorem": "",
                    "normalized_natural_language_theorem": "",
                    "extracted_assumptions": [],
                    "extracted_conclusion": "",
                    "reviewer_evaluator_evidence": [],
                    "adversarial_checks": {},
                    "schema_version": "1.0",
                }
            artifact["lean_statement"] = str(r.get("lean_statement", "") or "")
            artifact["equivalence_verdict"] = equiv_verdict.value
            artifact["independent_semantic_evidence"] = semantic_evidence_ok
            evidence = artifact.get("reviewer_evaluator_evidence")
            if not isinstance(evidence, list):
                evidence = []
            if semantic_evidence_ok and "equivalent_independent_semantic_evidence" not in evidence:
                evidence.append("equivalent_independent_semantic_evidence")
            artifact["reviewer_evaluator_evidence"] = evidence
            r["semantic_equivalence_artifact"] = artifact
            r["promotion_gate_passed"] = full_allowed
            if full_allowed:
                r["step_verdict"] = StepVerdict.VERIFIED.value
                r["failure_origin"] = "NOT_FAILED"
                r["failure_kind"] = "unknown"
                r["trust_class"] = "TRUST_INTERNAL_PROVED"
                r["axiom_debt"] = []
                r["axiom_debt_hash"] = ""
                r["closure_claim"] = "lean_verified_without_paper_local_axioms"
                r["modulo_paper_local_axioms"] = False
                r.pop("paper_local_axiom_debt", None)
            r["review_required"] = bool(
                equiv_verdict != ClaimEquivalenceVerdict.EQUIVALENT
                or "claim_equivalent" in set(all_failures)
            )
            r["review_queue_id"] = (
                f"review::{str(r.get('theorem_name', '') or base)}" if r["review_required"] else ""
            )
            r["trust_reference"] = (
                (
                    f"auto_reliable_core:{theorem_name};"
                    f"core_sha256:{lean_verification['core_sha256']};"
                    "semantic_equivalence:verified;lean_verification:fresh"
                )
                if full_allowed
                else "auto_reliable_core_evidence_only;gate_failures=" + ",".join(all_failures)
            )
            r["auto_reliable_core"] = {
                "theorem_name": theorem_name,
                "core_file": str(core_path),
                "tactic": tactic,
                "strict_gate_passed": full_allowed,
                "strict_gate_failures": all_failures,
                "lean_verification": lean_verification,
                "semantic_equivalence_evidence": "independent" if semantic_evidence_ok else "",
                "audited_equivalence_applied": audited_equivalence,
                "ledger_statement_verified_by_core": ledger_statement_directly_supported,
            }
            if audited_equivalence and lean_verification:
                replacement_name = _audited_replacement_theorem_name(base or theorem_name)
                r["ledger_role"] = str(r.get("ledger_role", "") or "generated_diagnostic")
                r["superseded_by_audited_core"] = True
                r["superseded_by_row_id"] = replacement_name
                r["replacement_reason"] = "audited_core_replacement_records_verified_statement_proof_pair"
                r["replacement_policy"] = "generated_row_remains_diagnostic_not_counted"
                replacement_row = _audited_replacement_row(
                    generated_row=r,
                    item=item,
                    core_path=core_path,
                    lean_verification=lean_verification,
                    semantic_evidence_ok=semantic_evidence_ok,
                )
                audited_replacements.append(replacement_row)
                refreshed_replacement_sources.add(str(replacement_row.get("source_theorem", "") or base))
            if full_allowed:
                promoted.append(str(r.get("theorem_name", "") or base))
            else:
                if audited_equivalence:
                    audited_evidence_only.append(
                        {
                            "theorem_name": str(r.get("theorem_name", "") or base),
                            "core_theorem_name": theorem_name,
                            "core_file": str(core_path),
                            "core_sha256": str((lean_verification or {}).get("core_sha256", "")),
                            "reason": "audited_core_does_not_verify_recorded_ledger_statement",
                            "ledger_statement_verified_by_core": ledger_statement_directly_supported,
                            "diagnostic_repair": diagnostic_repair,
                        }
                    )
                strict_gate_blocked.append(
                    {
                        "theorem_name": str(r.get("theorem_name", "") or base),
                        "gate_failures": all_failures,
                        "claim_equivalence_verdict": equiv_verdict.value,
                    }
                )
        out.append(_with_result_label(r))

    for replacement in existing_replacements:
        source = str(replacement.get("source_theorem", "") or replacement.get("replaces_generated_theorem", "") or "")
        if source not in refreshed_replacement_sources:
            out.append(_with_result_label(replacement))
    out.extend(audited_replacements)

    return out, {
        "applied_count": len(promoted) + len(strict_gate_blocked),
        "promoted_count": len(promoted),
        "promoted_theorems": promoted,
        "audited_core_replacement_count": len(audited_replacements),
        "audited_core_replacements": [
            {
                "theorem_name": str(row.get("theorem_name", "") or ""),
                "source_theorem": str(row.get("source_theorem", "") or ""),
                "core_theorem_name": str((row.get("audited_core_replacement") or {}).get("core_theorem_name", "")),
                "core_file": str((row.get("audited_core_replacement") or {}).get("core_file", "")),
                "core_sha256": str((row.get("audited_core_replacement") or {}).get("core_sha256", "")),
                "status": str(row.get("status", "") or ""),
                "proof_method": str(row.get("proof_method", "") or ""),
            }
            for row in audited_replacements
            if isinstance(row.get("audited_core_replacement"), dict)
        ],
        "strict_gate_blocked_count": len(strict_gate_blocked),
        "strict_gate_blocked_theorems": strict_gate_blocked,
        "eligible_reliable_core_count": len(by_source),
        "audited_reliable_core_evidence_only_count": len(audited_evidence_only),
        "audited_reliable_core_evidence_only": audited_evidence_only,
    }


def _dedupe_final_ledger_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse base/namespaced aliases so reports count paper theorems once."""
    status_rank = {
        "FULLY_PROVEN": 5,
        "TRANSLATION_LIMITED": 4,
        "FLAWED": 3,
        "INTERMEDIARY_PROVEN": 2,
        "UNRESOLVED": 1,
    }

    def key_for(row: dict[str, Any]) -> str:
        if str(row.get("ledger_role", "") or "") == "audited_core_replacement":
            name = str(row.get("theorem_name", "") or "").strip()
            source = str(row.get("source_theorem", "") or row.get("replaces_generated_theorem", "") or "").strip()
            return f"audited_core_replacement::{source or name}"
        name = str(row.get("theorem_name", "") or "").strip()
        return name.rsplit(".", 1)[-1] if name else ""

    def rank(row: dict[str, Any]) -> tuple[int, int, int]:
        status = str(row.get("status", "") or "")
        method = str(row.get("proof_method", "") or "").lower()
        return (
            status_rank.get(status, 0),
            1 if method == "lean_verified" else 0,
            1 if row.get("promotion_gate_passed") else 0,
        )

    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in entries:
        if not isinstance(row, dict):
            continue
        key = key_for(row)
        if not key:
            key = f"__row_{len(order)}"
        if key not in best:
            best[key] = row
            order.append(key)
            continue
        if rank(row) > rank(best[key]):
            best[key] = row
    return [best[k] for k in order if k in best]


def _blocker_clusters(entries: list[dict[str, Any]]) -> dict[str, Any]:
    clusters: dict[str, list[str]] = {}
    for row in entries:
        if not isinstance(row, dict):
            continue
        if bool(row.get("superseded_by_audited_core")):
            continue
        status = str(row.get("status", "") or "").strip()
        failures = {str(x) for x in (row.get("gate_failures") or []) if str(x).strip()}
        err = str(row.get("error_message", "") or "")
        if status == "TRANSLATION_LIMITED":
            key = "translation_limited_placeholder_or_schema"
        elif status == "AXIOM_BACKED" or str(row.get("proof_method", "") or "").strip().lower() == "domain_axiom":
            key = "closed_modulo_paper_local_axioms"
        elif "semantic_hard" in err or "semantic_policy_hard_block" in err:
            key = "semantic_fidelity_hard_block"
        elif _axiom_debt_list(row) or "no_paper_axiom_debt" in failures:
            tier = debt_tier_for_row(row)
            key = f"paper_theory_debt_{tier}" if tier and tier != "none" else "paper_theory_debt"
        elif "lean_proof_closed" in failures:
            key = "proof_search_gap"
        else:
            classified = classify_statement(row)
            if classified.primary_blocker == "release_ready":
                continue
            key = classified.primary_blocker
        if key == "release_ready":
            continue
        name = str(row.get("theorem_name", "") or "")
        if name:
            clusters.setdefault(key, []).append(name)
    return {
        key: {"count": len(names), "theorems": names}
        for key, names in sorted(clusters.items())
    }


def _audited_replacement_verified(row: dict[str, Any]) -> bool:
    if str(row.get("ledger_role", "") or "") != "audited_core_replacement":
        return True
    if str(row.get("status", "") or "") != "FULLY_PROVEN":
        return False
    if str(row.get("proof_method", "") or "").lower() != ProofMethod.LEAN_VERIFIED.value:
        return False
    core = row.get("audited_core_replacement")
    if not isinstance(core, dict):
        return False
    required = ("source_theorem", "core_theorem_name", "core_file", "core_sha256", "verification_method", "lean_statement", "proof_text")
    if any(not str(core.get(key, "") or "").strip() for key in required):
        return False
    recorded_statement = str(row.get("lean_statement", "") or "").strip()
    recorded_proof = str(row.get("proof_text", "") or "").strip()
    audited_statement = str(core.get("lean_statement", "") or "").strip()
    audited_proof = str(core.get("proof_text", "") or "").strip()
    if recorded_statement != audited_statement or recorded_proof != audited_proof:
        return False
    trust_reference = str(row.get("trust_reference", "") or "")
    if (
        f"audited_core_replacement:{core.get('core_theorem_name')}" not in trust_reference
        or f"source_theorem:{core.get('source_theorem')}" not in trust_reference
        or f"core_sha256:{core.get('core_sha256')}" not in trust_reference
    ):
        return False
    auto_core = row.get("auto_reliable_core") if isinstance(row.get("auto_reliable_core"), dict) else {}
    if auto_core and not bool(auto_core.get("ledger_statement_verified_by_core")):
        return False
    gates = row.get("validation_gates") if isinstance(row.get("validation_gates"), dict) else {}
    return bool(
        gates.get("ledger_records_audited_statement_proof_pair")
        and gates.get("fresh_lean_verification_evidence")
        and gates.get("claim_equivalent")
        and not _axiom_debt_list(row)
    )


def _closure_metrics(entries: list[dict[str, Any]]) -> dict[str, Any]:
    # TRANSLATION_LIMITED theorems are excluded from the proving-rate denominator:
    # their statements cannot be expressed in Lean because Mathlib lacks the required types.
    # They are tracked separately so the rate reflects what the prover can actually attempt.
    #
    # proof_method tiers (from ProofMethod enum):
    #   lean_verified     — lake build confirmed; counts toward verified_proven rate
    #   auto_closed       — trivial tactic, no lake check; excluded from verified rate
    #   reconcile_promoted — post-hoc file-scan promotion; excluded from verified rate
    #   translation_limited / unknown — excluded or unclassified
    _EXCLUDED_FROM_DENOMINATOR = {"TRANSLATION_LIMITED"}
    _LEAN_VERIFIED_METHODS = {"lean_verified"}
    _AUTO_CLOSED_METHODS = {"auto_closed", "reconcile_promoted"}

    status_counts: dict[str, int] = {}
    unresolved: list[dict[str, Any]] = []
    translation_limited: list[dict[str, Any]] = []
    lean_verified_proven: list[dict[str, Any]] = []
    auto_closed_proven: list[dict[str, Any]] = []
    axiom_backed: list[dict[str, Any]] = []
    audited_core_replacements: list[dict[str, Any]] = []
    superseded_diagnostics: list[dict[str, Any]] = []
    axiom_debt_items: list[str] = []

    for row in entries:
        st = str(row.get("status", "UNRESOLVED")).strip() or "UNRESOLVED"
        pm = str(row.get("proof_method", "unknown")).strip().lower()
        result_label, claim_scope, modulo_paper_local_axioms = _result_label_for_row(row)
        if bool(row.get("superseded_by_audited_core")):
            superseded_diagnostics.append(
                {
                    "theorem_name": str(row.get("theorem_name", "")),
                    "status": st,
                    "proof_method": pm,
                    "superseded_by_row_id": str(row.get("superseded_by_row_id", "")),
                    "replacement_reason": str(row.get("replacement_reason", "")),
                    "debt_tier": debt_tier_for_row(row),
                    "proof_value": proof_value_for_row(row),
                    "grounding_metadata": grounding_metadata_for_row(row),
                    "result_label": result_label,
                    "claim_scope": "Superseded diagnostic generated row; excluded from headline closure metrics.",
                }
            )
            continue
        status_counts[st] = status_counts.get(st, 0) + 1
        row_debt = row.get("axiom_debt", [])
        if isinstance(row_debt, list):
            axiom_debt_items.extend(str(x) for x in row_debt if str(x).strip())
        elif isinstance(row_debt, str) and row_debt.strip():
            axiom_debt_items.append(row_debt)
        if st in _EXCLUDED_FROM_DENOMINATOR:
            translation_limited.append(
                {
                    "theorem_name": str(row.get("theorem_name", "")),
                    "status": st,
                    "result_label": result_label,
                    "claim_scope": claim_scope,
                    "modulo_paper_local_axioms": modulo_paper_local_axioms,
                    "debt_tier": debt_tier_for_row(row),
                    "proof_value": proof_value_for_row(row),
                    "grounding_metadata": grounding_metadata_for_row(row),
                }
            )
        elif st == "AXIOM_BACKED":
            axiom_backed.append(
                {
                    "theorem_name": str(row.get("theorem_name", "")),
                    "proof_method": pm,
                    "axiom_debt_hash": str(row.get("axiom_debt_hash", "")),
                    "result_label": result_label,
                    "claim_scope": claim_scope,
                    "modulo_paper_local_axioms": modulo_paper_local_axioms,
                    "paper_local_axiom_debt": _axiom_debt_list(row),
                    "debt_tier": debt_tier_for_row(row),
                    "proof_value": proof_value_for_row(row),
                    "grounding_metadata": grounding_metadata_for_row(row),
                }
            )
        elif st == "FULLY_PROVEN":
            if pm in _LEAN_VERIFIED_METHODS:
                if _audited_replacement_verified(row):
                    item = {
                        "theorem_name": str(row.get("theorem_name", "")),
                        "proof_method": pm,
                        "result_label": result_label,
                        "claim_scope": claim_scope,
                        "modulo_paper_local_axioms": modulo_paper_local_axioms,
                        "debt_tier": debt_tier_for_row(row),
                        "proof_value": proof_value_for_row(row),
                        "grounding_metadata": grounding_metadata_for_row(row),
                    }
                    lean_verified_proven.append(item)
                    if str(row.get("ledger_role", "") or "") == "audited_core_replacement":
                        audited_core_replacements.append(
                            {
                                **item,
                                "source_theorem": str(row.get("source_theorem", "") or ""),
                                "core_theorem_name": str((row.get("audited_core_replacement") or {}).get("core_theorem_name", "")),
                                "core_file": str((row.get("audited_core_replacement") or {}).get("core_file", "")),
                                "core_sha256": str((row.get("audited_core_replacement") or {}).get("core_sha256", "")),
                                "proof_countable": bool(row.get("proof_countable", True)),
                                "replacement_gate": (row.get("audited_core_replacement") or {}).get("replacement_gate", {}),
                            }
                        )
                else:
                    unresolved.append(
                        {
                            "theorem_name": str(row.get("theorem_name", "")),
                            "status": st,
                            "grounding_status": str(row.get("grounding_status", "")),
                            "gate_failures": ["invalid_audited_core_replacement_record"],
                            "result_label": "not_verified",
                            "claim_scope": "Audited-core replacement row is missing required verified provenance.",
                            "modulo_paper_local_axioms": False,
                            "debt_tier": debt_tier_for_row(row),
                            "proof_value": proof_value_for_row(row),
                            "grounding_metadata": grounding_metadata_for_row(row),
                        }
                    )
            elif pm in _AUTO_CLOSED_METHODS:
                auto_closed_proven.append(
                    {
                        "theorem_name": str(row.get("theorem_name", "")),
                        "proof_method": pm,
                        "result_label": result_label,
                        "claim_scope": claim_scope,
                        "modulo_paper_local_axioms": modulo_paper_local_axioms,
                        "debt_tier": debt_tier_for_row(row),
                        "proof_value": proof_value_for_row(row),
                        "grounding_metadata": grounding_metadata_for_row(row),
                    }
                )
            else:
                # Legacy entries without proof_method field — count conservatively as auto_closed.
                auto_closed_proven.append(
                    {
                        "theorem_name": str(row.get("theorem_name", "")),
                        "proof_method": "unknown",
                        "result_label": result_label,
                        "claim_scope": claim_scope,
                        "modulo_paper_local_axioms": modulo_paper_local_axioms,
                        "debt_tier": debt_tier_for_row(row),
                        "proof_value": proof_value_for_row(row),
                        "grounding_metadata": grounding_metadata_for_row(row),
                    }
                )
        else:
            unresolved.append(
                {
                    "theorem_name": str(row.get("theorem_name", "")),
                    "status": st,
                    "grounding_status": str(row.get("grounding_status", "")),
                    "gate_failures": row.get("gate_failures", []),
                    "result_label": result_label,
                    "claim_scope": claim_scope,
                    "modulo_paper_local_axioms": modulo_paper_local_axioms,
                    "debt_tier": debt_tier_for_row(row),
                    "proof_value": proof_value_for_row(row),
                    "grounding_metadata": grounding_metadata_for_row(row),
                }
            )

    total_all = len(entries)
    total_headline = total_all - len(superseded_diagnostics)
    total_provable = total_headline - len(translation_limited)
    fully = status_counts.get("FULLY_PROVEN", 0)
    lean_verified_count = len(lean_verified_proven)
    auto_closed_count = len(auto_closed_proven)
    unique_axiom_debt = list(dict.fromkeys(axiom_debt_items))
    paper_local_axiom_debt = [
        debt for debt in unique_axiom_debt if debt != "translation_repair_domain_assumption"
    ]
    axiom_backed_theorems = [item["theorem_name"] for item in axiom_backed if item.get("theorem_name")]
    headline_entries = [row for row in entries if isinstance(row, dict) and not bool(row.get("superseded_by_audited_core"))]
    missing_lemma_subledger = _build_missing_lemma_subledger(headline_entries)
    axiom_debt_burndown = build_axiom_debt_burndown(headline_entries)
    paper_theory_debt_dashboard = summarize_paper_theory_debt_tiers(entries)
    deep_domain_obligations = build_deep_domain_obligations(headline_entries)
    statement_validity = summarize_validity(headline_entries)
    return {
        "total_theorems": total_all,
        "total_headline_theorems": total_headline,
        "total_provable": total_provable,
        "translation_limited_count": len(translation_limited),
        "fully_proven": fully,
        "fully_proven_status_count": fully,
        # verified_proven: only lake-confirmed proofs; the authoritative quality signal.
        "verified_proven": lean_verified_count,
        "real_fully_proven": lean_verified_count,
        "auto_closed_count": auto_closed_count,
        "audited_core_replacement_count": len(audited_core_replacements),
        "superseded_diagnostic_count": len(superseded_diagnostics),
        "axiom_backed_count": len(axiom_backed),
        "axiom_debt_count": len(unique_axiom_debt),
        "axiom_debt": unique_axiom_debt,
        "paper_local_axiom_disclosure": {
            "required": bool(axiom_backed or paper_local_axiom_debt),
            "result_label": PAPER_LOCAL_AXIOM_RESULT_LABEL,
            "claim_scope": PAPER_LOCAL_AXIOM_CLAIM_SCOPE,
            "theorem_count": len(axiom_backed),
            "theorems": axiom_backed_theorems,
            "axiom_debt": paper_local_axiom_debt,
        },
        "statement_validity": statement_validity,
        "missing_lemma_subledger": missing_lemma_subledger,
        "axiom_debt_burndown": axiom_debt_burndown,
        "paper_theory_debt_dashboard": paper_theory_debt_dashboard,
        "deep_domain_obligations": deep_domain_obligations,
        "closure_by_method": {
            "lean_verified": lean_verified_count,
            "auto_closed_or_reconciled": auto_closed_count,
            "axiom_backed": len(axiom_backed),
            "translation_limited": len(translation_limited),
            "unresolved_or_flawed": len(unresolved),
        },
        "full_closure": bool(total_provable > 0 and lean_verified_count == total_provable),
        # proving_rate uses verified_proven, not fully_proven, to avoid inflating with
        # auto-closed schema placeholders or reconcile-promoted trivial entries.
        "proving_rate": round(lean_verified_count / total_provable, 4) if total_provable > 0 else 0.0,
        "status_counts": status_counts,
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
        "translation_limited": translation_limited,
        "axiom_backed": axiom_backed,
        "audited_core_replacements": audited_core_replacements,
        "superseded_diagnostics": superseded_diagnostics,
        "lean_verified_proven": lean_verified_proven,
        "auto_closed_proven": auto_closed_proven,
    }


def _lean_namespace_id(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", raw or "Paper")
    cleaned = re.sub(r"_+", "_", cleaned).strip("_") or "Paper"
    if not re.match(r"[A-Za-z_]", cleaned):
        cleaned = "Paper_" + cleaned
    return cleaned


def _axiomize_decl_for_paper_local(lean_statement: str, fallback_name: str) -> str:
    """Convert a theorem/lemma declaration into a paper-local axiom declaration."""
    stmt = (lean_statement or "").strip()
    if not stmt:
        return ""
    stmt = re.sub(r":=\s*by\b.*$", "", stmt, flags=re.DOTALL).strip()
    stmt = re.sub(r":=\s*$", "", stmt).strip()
    m = re.match(r"^(?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma)\s+([^\s]+)(.*)$", stmt, re.DOTALL)
    if not m:
        return ""
    raw_name = fallback_name.rsplit(".", 1)[-1] if fallback_name else m.group(1).rsplit(".", 1)[-1]
    name = _lean_namespace_id(raw_name)
    rest = m.group(2).strip()
    if not rest or ":" not in rest:
        return ""
    return f"axiom {name} {rest}"


def _write_paper_local_theory_file(
    *,
    project_root: Path,
    paper_id: str,
    entries: list[dict[str, Any]],
) -> Path | None:
    """Emit a reusable Lean support surface for statements accepted by this paper run."""
    safe = _safe_id(paper_id)
    namespace = "PaperLocal_" + _lean_namespace_id(safe)
    decls: list[str] = []
    seen: set[str] = set()
    for row in entries:
        if not isinstance(row, dict):
            continue
        if str(row.get("status", "")).strip() not in {"FULLY_PROVEN", "INTERMEDIARY_PROVEN", "AXIOM_BACKED"}:
            continue
        if str(row.get("proof_method", "")).strip().lower() in {"auto_closed", "reconcile_promoted", "translation_limited"}:
            continue
        decl = _axiomize_decl_for_paper_local(
            str(row.get("lean_statement", "") or ""),
            str(row.get("theorem_name", "") or ""),
        )
        if not decl or decl in seen:
            continue
        seen.add(decl)
        decls.append(decl)
    if not decls:
        return None
    out = project_root / "output" / "paper_local_theory" / f"{safe}.lean"
    out.parent.mkdir(parents=True, exist_ok=True)
    body = "\n\n".join(decls)
    out.write_text(
        "import Mathlib\nimport Aesop\n\n"
        "open MeasureTheory ProbabilityTheory Filter Set\n\n"
        f"namespace {namespace}\n\n"
        f"{body}\n\n"
        f"end {namespace}\n",
        encoding="utf-8",
    )
    return out


def _detect_curated_paper_package(project_root: Path, paper_id: str) -> dict[str, Any]:
    """Return metadata for a hand-curated proof package, if this paper has one."""
    safe = _safe_id(paper_id)
    module_safe = re.sub(r"[^A-Za-z0-9_]", "_", str(paper_id).strip())
    candidates = [
        project_root / f"paper_{safe}" / "proofs.lean",
        project_root / f"poc_{safe}" / "proofs.lean",
        project_root / "Desol" / "PaperProofs" / f"Paper_{module_safe}.lean",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        theorem_names = re.findall(r"(?m)^\s*theorem\s+([A-Za-z_][A-Za-z0-9_']*)\b", text)
        axiom_names = re.findall(r"(?m)^\s*axiom\s+([A-Za-z_][A-Za-z0-9_']*)\b", text)
        sorry_count = len(re.findall(r"\bsorry\b", text))
        return {
            "available": True,
            "path": str(path),
            "theorem_count": len(theorem_names),
            "axiom_count": len(axiom_names),
            "sorry_count": sorry_count,
            "theorem_names": theorem_names,
            "note": (
                "Curated package is a separate hand-built Lean formalization. "
                "It may close every theorem relative to local/domain axioms, but it is not "
                "the same evidence as the automatic paper pipeline proving generated statements."
            ),
        }
    return {"available": False}


def _detect_auto_reliable_core(project_root: Path, paper_id: str) -> dict[str, Any]:
    """Return metadata for the automatically generated no-sorry reliable core."""
    module_safe = re.sub(r"[^A-Za-z0-9_]", "_", str(paper_id).strip())
    path = project_root / "Desol" / "PaperProofs" / "Auto" / f"Paper_{module_safe}.lean"
    if not path.exists():
        return {"available": False}
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {"available": False}
    theorem_names = re.findall(r"(?m)^\s*theorem\s+([A-Za-z_][A-Za-z0-9_']*)\b", text)
    axiom_names = re.findall(r"(?m)^\s*axiom\s+([A-Za-z_][A-Za-z0-9_']*)\b", text)
    sorry_count = len(re.findall(r"\bsorry\b", text))
    return {
        "available": bool(theorem_names),
        "path": str(path),
        "theorem_count": len(theorem_names),
        "axiom_count": len(axiom_names),
        "sorry_count": sorry_count,
        "theorem_names": theorem_names,
        "note": (
            "Automatically generated reliable core. Statements are copied only "
            "when they avoid known bad-translation/paper-axiom patterns and close "
            "with deterministic Lean-checked tactics."
        ),
    }


def _detect_paper_theory_artifacts(
    *,
    project_root: Path,
    paper_id: str,
    ledger_path: Path,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    module_safe = re.sub(r"[^A-Za-z0-9_]", "_", str(paper_id).strip())
    theory_path = project_root / "Desol" / "PaperTheory" / f"Paper_{module_safe}.lean"
    manifest_path = theory_path.with_suffix(".manifest.json")
    builder_step = next((s for s in reversed(steps) if s.get("stage") == "paper_theory_builder"), {})
    manifest_payload: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest_payload = loaded
        except Exception:
            manifest_payload = {}
    ledger_stale = False
    stale_reason = ""
    if ledger_path.exists() and manifest_path.exists():
        try:
            ledger_stale = ledger_path.stat().st_mtime < manifest_path.stat().st_mtime
            if ledger_stale:
                stale_reason = "ledger_generated_before_latest_paper_theory_manifest"
        except Exception:
            ledger_stale = False
    symbols = manifest_payload.get("symbols", []) if isinstance(manifest_payload.get("symbols"), list) else []
    counts_by_grounding: dict[str, int] = {}
    for sym in symbols:
        if not isinstance(sym, dict):
            continue
        grounding = str(sym.get("grounding", "") or "unknown")
        counts_by_grounding[grounding] = counts_by_grounding.get(grounding, 0) + 1
    return {
        "module": f"Desol.PaperTheory.Paper_{module_safe}",
        "paper_local_theory_file": str(theory_path) if theory_path.exists() else "",
        "paper_theory_manifest": str(manifest_path) if manifest_path.exists() else "",
        "paper_theory_manifest_exists": manifest_path.exists(),
        "paper_theory_build_gate": builder_step.get("build", {}),
        "paper_theory_builder_ok": bool(builder_step.get("ok", False)),
        "manifest": manifest_payload,
        "manifest_symbol_count": len(symbols),
        "manifest_counts_by_grounding": dict(sorted(counts_by_grounding.items())),
        "ledger_stale_against_manifest": ledger_stale,
        "stale_reason": stale_reason,
    }


def _run(cmd: list[str], cwd: Path) -> dict[str, Any]:
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    return {
        "cmd": cmd,
        "returncode": int(proc.returncode),
        "elapsed_s": round(time.time() - t0, 3),
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Formalize a full paper with iterative closure passes")
    p.add_argument("paper_id", help="arXiv id, e.g. 2304.09598")
    p.add_argument("--project-root", default=".")
    p.add_argument("--out-lean", default="", help="Output Lean file path")
    p.add_argument("--model", default="")
    p.add_argument("--max-theorems", type=int, default=0)
    p.add_argument("--initial-repair-rounds", type=int, default=5)
    p.add_argument("--initial-translation-candidates", type=int, default=1)
    p.add_argument("--prove-repair-rounds", type=int, default=5)
    p.add_argument("--prove-mode", default="full-draft")
    p.add_argument("--mcts-iterations", type=int, default=24)
    p.add_argument("--mcts-repair-variants", type=int, default=3)
    p.add_argument("--mcts-max-depth", type=int, default=5)
    p.add_argument("--bridge-rounds", type=int, default=3)
    p.add_argument("--bridge-depth", type=int, default=3)
    p.add_argument("--bridge-max-candidates", type=int, default=5)
    p.add_argument("--strict-context-pack", action="store_true")
    p.add_argument("--strict-assumption-slots", action="store_true")
    p.add_argument("--mandatory-retry-rounds", type=int, default=0)
    p.add_argument("--max-passes", type=int, default=4)
    p.add_argument("--results-file", default="", help="prove_arxiv_batch results JSON path")
    p.add_argument("--report-out", default="", help="Final orchestration report JSON path")
    p.add_argument("--write-kg", action="store_true")
    p.add_argument(
        "--focus-no-world-model",
        action="store_true",
        help="Apply blocker-first preset: no world-model lanes, stable full-draft proving, strict context/slot checks.",
    )
    p.add_argument(
        "--skip-coverage-screen",
        action="store_true",
        help="Disable Mathlib coverage pre-screening (run full prove pipeline even for library-limited papers).",
    )
    p.add_argument(
        "--coverage-threshold",
        type=float,
        default=0.30,
        help="Coverage score below which library-limited stub-and-check mode is used (default 0.30). "
             "Score=0.65 means 1 library-limited signal; score=0.30 means 2+ signals.",
    )
    p.add_argument(
        "--library-first-domain",
        default="",
        help="Optional domain bootstrap before pipeline run (e.g. probability_statistics)",
    )
    p.add_argument(
        "--library-first-extra-import",
        action="append",
        default=[],
        help="Extra Mathlib import for library-first bootstrap (repeatable)",
    )
    p.add_argument(
        "--no-reset-paper-ledger",
        action="store_true",
        help="Disable per-run ledger reset (default resets paper ledger before bootstrap).",
    )
    p.add_argument(
        "--skip-translation-repair-pack",
        action="store_true",
        help="Do not build/apply the post-run bad-translation repair pack.",
    )
    p.add_argument("--claim-equivalence-adjudications", default="", help="JSONL adjudications to merge before final report.")
    p.add_argument("--write-claim-equivalence-review-queue", action="store_true", help="Write JSONL queue for claim-equivalence blockers.")
    p.add_argument("--claim-equivalence-review-out", default="", help="Output path for claim-equivalence review queue JSONL.")
    p.add_argument(
        "--strict-statement-validity",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require generated statements to pass the statement-validity gate for release-oriented runs.",
    )
    p.add_argument(
        "--include-diagnostic-claim-reviews",
        action="store_true",
        help="Include diagnostic-only claim-equivalence rows in the review queue.",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()
    if args.focus_no_world_model:
        # Stable lane for blocker attacks: avoid brittle state-MCTS backend failures.
        args.prove_mode = "full-draft"
        args.strict_context_pack = True
        args.strict_assumption_slots = True
        args.bridge_rounds = max(1, int(args.bridge_rounds))
        args.bridge_depth = max(1, int(args.bridge_depth))
        args.bridge_max_candidates = max(1, int(args.bridge_max_candidates))
        args.mandatory_retry_rounds = max(1, int(args.mandatory_retry_rounds))
    project_root = Path(args.project_root).resolve()
    safe = _safe_id(args.paper_id)
    out_lean = Path(args.out_lean) if args.out_lean else project_root / "output" / f"{safe}.lean"
    results_file = (
        Path(args.results_file)
        if args.results_file
        else project_root / "logs" / f"full_paper_{safe}_prove_results.json"
    )
    report_out = (
        Path(args.report_out)
        if args.report_out
        else project_root / "output" / "reports" / "full_paper" / f"{safe}_report.json"
    )
    unresolved_out = report_out.with_suffix(".unresolved.json")
    missing_lemma_out = report_out.with_suffix(".missing_lemmas.json")
    axiom_debt_burndown_out = report_out.with_suffix(".axiom_debt_burndown.json")
    statement_validity_out = report_out.with_suffix(".statement_validity.json")
    proof_repair_cohort_out = report_out.with_suffix(".proof_repair_cohort.json")
    claim_equivalence_review_out = (
        Path(args.claim_equivalence_review_out)
        if args.claim_equivalence_review_out
        else project_root / "output" / "claim_equivalence" / "review_queue" / f"{safe}.jsonl"
    )
    claim_equivalence_adjudications = (
        Path(args.claim_equivalence_adjudications)
        if args.claim_equivalence_adjudications
        else project_root / "output" / "claim_equivalence" / "adjudications" / f"{safe}.jsonl"
    )

    steps: list[dict[str, Any]] = []
    pass_history: list[dict[str, Any]] = []
    ledger_path = _ledger_path(project_root, args.paper_id)

    # Hard-reset ledger scope by default so full-paper closure metrics reflect
    # this run's extracted/proved cohort rather than stale historical entries.
    reset_info: dict[str, Any] = {
        "stage": "reset_paper_ledger",
        "ledger_path": str(ledger_path),
        "removed": False,
        "skipped": bool(args.no_reset_paper_ledger),
    }
    if not bool(args.no_reset_paper_ledger):
        try:
            if ledger_path.exists():
                ledger_path.unlink()
                reset_info["removed"] = True
        except Exception as exc:
            reset_info["error"] = str(exc)
    steps.append(reset_info)

    # Mathlib coverage pre-screening: score the paper before committing compute.
    coverage_info: dict[str, Any] = {"stage": "mathlib_coverage_screen", "skipped": bool(args.skip_coverage_screen)}
    if not args.skip_coverage_screen:
        # Try to get paper abstract/title from the local cache or arxiv metadata.
        _abstract_text = ""
        _meta_candidates = [
            project_root / "data" / "arxiv_cache" / f"{safe}.json",
            project_root / "data" / "papers" / f"{safe}.json",
            project_root / "output" / f"{safe}_meta.json",
        ]
        for _mc in _meta_candidates:
            if _mc.exists():
                try:
                    _mdata = json.loads(_mc.read_text(encoding="utf-8"))
                    _abstract_text = str(_mdata.get("abstract", "") or _mdata.get("summary", "") or "")
                    if not _abstract_text:
                        _abstract_text = str(_mdata.get("title", "") or "")
                except Exception:
                    pass
                break
        coverage_result = _score_mathlib_coverage(_abstract_text or args.paper_id)
        coverage_info.update(coverage_result)
        coverage_info["abstract_chars"] = len(_abstract_text)
        steps.append(coverage_info)
        if coverage_result["library_limited"] and coverage_result["coverage_score"] < float(args.coverage_threshold):
            # Library-limited: reduce max passes and annotate report — don't abort.
            # Stub-and-check: shorten proof search, focus on what Mathlib can handle.
            args.max_passes = min(int(args.max_passes), 2)
            print(
                f"[coverage-screen] library-limited paper detected "
                f"(score={coverage_result['coverage_score']:.2f}, "
                f"reasons={coverage_result['library_limited_reasons']}). "
                f"Reducing to {args.max_passes} prove passes.",
                flush=True,
            )
    else:
        steps.append(coverage_info)

    if args.library_first_domain:
        args.library_first_domain = _map_library_first_domain(str(args.library_first_domain))
        cmd_bootstrap_lib = [
            "python3",
            "scripts/library_first_bootstrap.py",
            "--project-root",
            str(project_root),
            "--domain",
            str(args.library_first_domain),
        ]
        for imp in (args.library_first_extra_import or []):
            if str(imp).strip():
                cmd_bootstrap_lib.extend(["--extra-import", str(imp).strip()])
        lib_boot = _run(cmd_bootstrap_lib, cwd=project_root)
        steps.append({"stage": "library_first_bootstrap", **lib_boot})
        if lib_boot["returncode"] != 0:
            report = {
                "paper_id": args.paper_id,
                "ok": False,
                "reason": "library_first_bootstrap_failed",
                "steps": steps,
            }
            report_out.parent.mkdir(parents=True, exist_ok=True)
            report_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            print(json.dumps({"ok": False, "report": str(report_out)}, indent=2))
            return 1

    # Stage 1: bootstrap from raw paper.
    # Paper Theory Builder: generate paper-local module before any translation/proof.
    paper_theory_preflight_ok = True
    try:
        from paper_theory_builder import build_paper_theory, plan_paper_theory, write_paper_theory
        # Seed with existing out_lean text if present (helps pick up obvious symbols).
        seed_text = ""
        if out_lean.exists():
            try:
                seed_text = out_lean.read_text(encoding="utf-8")
            except Exception:
                seed_text = ""
        plan = plan_paper_theory(paper_id=args.paper_id, domain=str(args.library_first_domain or ""), seed_text=seed_text)
        theory_path = write_paper_theory(project_root=project_root, plan=plan)
        theory_build = build_paper_theory(project_root=project_root, module_name=plan.module_name)
        steps.append({
            "stage": "paper_theory_builder",
            "ok": bool(theory_build.get("ok", False)),
            "paper_theory_file": str(theory_path),
            "manifest": str(theory_path.with_suffix(".manifest.json")),
            "module": f"Desol.PaperTheory.{plan.module_name}",
            "build": theory_build,
        })
        paper_theory_preflight_ok = bool(theory_build.get("ok", False))
    except Exception as exc:
        steps.append({"stage": "paper_theory_builder", "ok": False, "error": str(exc)})
        paper_theory_preflight_ok = False
    if not paper_theory_preflight_ok:
        report = {
            "paper_id": args.paper_id,
            "ok": False,
            "reason": "paper_theory_build_gate_failed",
            "steps": steps,
        }
        report_out.parent.mkdir(parents=True, exist_ok=True)
        report_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"ok": False, "report": str(report_out)}, indent=2))
        return 1

    cmd_bootstrap = [
        "python3",
        "scripts/arxiv_to_lean.py",
        args.paper_id,
        "--project-root",
        str(project_root),
        "--out",
        str(out_lean),
        "--repair-rounds",
        str(max(1, int(args.initial_repair_rounds))),
        "--translation-candidates",
        str(max(1, int(args.initial_translation_candidates))),
        "--prove-mode",
        str(args.prove_mode),
        "--mcts-iterations",
        str(max(1, int(args.mcts_iterations))),
        "--mcts-repair-variants",
        str(max(1, int(args.mcts_repair_variants))),
        "--mcts-max-depth",
        str(max(1, int(args.mcts_max_depth))),
    ]
    if args.library_first_domain:
        cmd_bootstrap.extend(["--domain", str(args.library_first_domain)])
    cmd_bootstrap.extend(["--paper-theory-grounding-mode", "hybrid", "--paper-theory-build-gate"])
    if args.model:
        cmd_bootstrap.extend(["--model", str(args.model)])
    if int(args.max_theorems) > 0:
        cmd_bootstrap.extend(["--max-theorems", str(int(args.max_theorems))])
    if args.write_kg:
        cmd_bootstrap.append("--write-kg")

    boot = _run(cmd_bootstrap, cwd=project_root)
    steps.append({"stage": "bootstrap_arxiv_to_lean", **boot})
    if boot["returncode"] != 0:
        report = {
            "paper_id": args.paper_id,
            "ok": False,
            "reason": "bootstrap_failed",
            "steps": steps,
        }
        report_out.parent.mkdir(parents=True, exist_ok=True)
        report_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"ok": False, "report": str(report_out)}, indent=2))
        return 1

    strict_statement_cohort = project_root / "output" / "statement_validity" / f"{safe}_proof_repair_cohort.json"
    if bool(args.strict_statement_validity):
        bootstrap_entries = _load_ledger_entries(ledger_path)
        bootstrap_cohort = proof_repair_cohort(bootstrap_entries)
        strict_statement_cohort.parent.mkdir(parents=True, exist_ok=True)
        strict_statement_cohort.write_text(
            json.dumps({"theorems": bootstrap_cohort}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        steps.append(
            {
                "stage": "strict_statement_validity",
                "enabled": True,
                "cohort_path": str(strict_statement_cohort),
                "proof_repair_cohort_count": len(bootstrap_cohort),
            }
        )
    else:
        steps.append({"stage": "strict_statement_validity", "enabled": False})

    prev_fully = -1
    stagnation = 0

    for pass_idx in range(1, max(1, int(args.max_passes)) + 1):
        cmd_pass = [
            "python3",
            "scripts/prove_arxiv_batch.py",
            "--lean-file",
            str(out_lean),
            "--project-root",
            str(project_root),
            "--paper-id",
            args.paper_id,
            "--repair-rounds",
            str(max(1, int(args.prove_repair_rounds))),
            "--mode",
            str(args.prove_mode),
            "--mcts-iterations",
            str(max(1, int(args.mcts_iterations))),
            "--mcts-repair-variants",
            str(max(1, int(args.mcts_repair_variants))),
            "--mcts-max-depth",
            str(max(1, int(args.mcts_max_depth))),
            "--bridge-loop",
            "--bridge-rounds",
            str(max(1, int(args.bridge_rounds))),
            "--bridge-depth",
            str(max(1, int(args.bridge_depth))),
            "--bridge-max-candidates",
            str(max(1, int(args.bridge_max_candidates))),
            "--mandatory-retry-rounds",
            str(max(0, int(args.mandatory_retry_rounds))),
            "--results-file",
            str(results_file),
        ]
        if args.strict_context_pack:
            cmd_pass.append("--strict-context-pack")
        if args.strict_assumption_slots:
            cmd_pass.append("--strict-assumption-slots")
        if bool(args.strict_statement_validity) and strict_statement_cohort.exists():
            cmd_pass.extend(["--proof-cohort-json", str(strict_statement_cohort)])
        if args.model:
            cmd_pass.extend(["--model", str(args.model)])
        if args.write_kg:
            cmd_pass.append("--write-kg")

        res = _run(cmd_pass, cwd=project_root)
        steps.append({"stage": f"prove_pass_{pass_idx}", **res})

        entries = _load_ledger_entries(ledger_path)
        metrics = _closure_metrics(entries)
        pass_history.append({"pass_idx": pass_idx, "metrics": metrics, "returncode": res["returncode"]})

        fully = int(metrics["fully_proven"])
        if metrics["full_closure"]:
            break
        if fully <= prev_fully:
            stagnation += 1
        else:
            stagnation = 0
        prev_fully = fully
        if stagnation >= 2:
            break

    final_entries_raw = _load_ledger_entries(ledger_path)
    final_entries, final_normalization = _normalize_final_ledger_entries(final_entries_raw)
    final_entries = _dedupe_final_ledger_entries(final_entries)
    if final_entries != final_entries_raw:
        _save_ledger_entries(ledger_path, final_entries)
    final_metrics = _closure_metrics(final_entries)
    blocker_clusters = _blocker_clusters(final_entries)
    paper_local_theory = _write_paper_local_theory_file(
        project_root=project_root,
        paper_id=args.paper_id,
        entries=final_entries,
    )
    reliable_core_json = project_root / "output" / "reports" / "full_paper" / f"{safe}_reliable_core.json"
    cmd_reliable_core = [
        "python3",
        "scripts/build_reliable_paper_core.py",
        args.paper_id,
        "--project-root",
        str(project_root),
        "--lean-file",
        str(out_lean),
        "--timeout-s",
        "8",
        "--max-theorems",
        "40",
        "--out-json",
        str(reliable_core_json),
    ]
    reliable_core_step = _run(cmd_reliable_core, cwd=project_root)
    steps.append({"stage": "auto_reliable_core", **reliable_core_step})
    reliable_core_payload = _read_json_file(reliable_core_json)
    claim_equivalence_review: dict[str, Any] = {
        "review_queue_path": str(claim_equivalence_review_out),
        "review_queue_policy": (
            "release_eligible_only"
            if not bool(args.include_diagnostic_claim_reviews)
            else "include_diagnostic_reviews"
        ),
        "adjudications_path": str(claim_equivalence_adjudications) if args.claim_equivalence_adjudications else "",
        "review_queue_count": 0,
        "pending_review_count": 0,
        "high_potential_review_count": 0,
        "would_promote_if_equivalent_count": 0,
        "top_review_targets": [],
        "claim_equivalence_applied_count": 0,
        "claim_equivalence_promoted_count": 0,
        "claim_equivalence_rejected_count": 0,
        "claim_equivalence_llm_only_triage_count": 0,
        "claim_equivalence_requires_human_count": 0,
        "claim_equivalence_conflict_count": 0,
        "claim_equivalence_hard_blocked_count": 0,
        "claim_equivalence_human_approved_count": 0,
        "claim_equivalence_hybrid_approved_count": 0,
    }
    if args.claim_equivalence_adjudications:
        adjudication_rows = read_jsonl(claim_equivalence_adjudications)
        final_entries, adjudication_summary = apply_adjudications_to_entries(
            final_entries,
            adjudication_rows,
            paper_id=args.paper_id,
        )
        claim_equivalence_review.update(adjudication_summary)
        if adjudication_summary.get("claim_equivalence_applied_count"):
            _save_ledger_entries(ledger_path, final_entries)
            final_metrics = _closure_metrics(final_entries)
            blocker_clusters = _blocker_clusters(final_entries)
    final_entries, auto_reliable_core_promotion = _apply_auto_reliable_core_promotions(
        final_entries,
        reliable_core_payload if isinstance(reliable_core_payload, dict) else {},
    )
    if args.write_claim_equivalence_review_queue:
        queue_payload = {
            "paper_id": args.paper_id,
            "entries": final_entries,
        }
        queue_rows = build_review_queue(
            ledger_payload=queue_payload,
            paper_id=args.paper_id,
            source_ledger=str(ledger_path),
            report_payload={},
            release_eligible_only=not bool(args.include_diagnostic_claim_reviews),
        )
        write_jsonl(claim_equivalence_review_out, queue_rows)
        claim_equivalence_review.update(_claim_equivalence_review_queue_summary(queue_rows))
    if auto_reliable_core_promotion.get("applied_count"):
        _save_ledger_entries(ledger_path, final_entries)
        final_metrics = _closure_metrics(final_entries)
        blocker_clusters = _blocker_clusters(final_entries)
        final_normalization["auto_reliable_core_promoted"] = int(
            auto_reliable_core_promotion.get("promoted_count", 0) or 0
        )
        final_normalization["auto_reliable_core_evidence_only"] = int(
            auto_reliable_core_promotion.get("strict_gate_blocked_count", 0) or 0
        )
        final_normalization["audited_core_replacements"] = int(
            auto_reliable_core_promotion.get("audited_core_replacement_count", 0) or 0
        )
    curated_package = _detect_curated_paper_package(project_root, args.paper_id)
    auto_reliable_core = _detect_auto_reliable_core(project_root, args.paper_id)
    unresolved_out.parent.mkdir(parents=True, exist_ok=True)
    unresolved_out.write_text(
        json.dumps(final_metrics.get("unresolved", []), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    missing_lemma_out.write_text(
        json.dumps(final_metrics.get("missing_lemma_subledger", {}), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    axiom_debt_burndown_out.write_text(
        json.dumps(final_metrics.get("axiom_debt_burndown", {}), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    statement_validity_summary = summarize_validity(final_entries)
    statement_validity_out.write_text(
        json.dumps(statement_validity_summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    final_proof_repair_cohort = proof_repair_cohort(final_entries)
    proof_repair_cohort_out.write_text(
        json.dumps({"theorems": final_proof_repair_cohort}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    paper_theory_artifacts = _detect_paper_theory_artifacts(
        project_root=project_root,
        paper_id=args.paper_id,
        ledger_path=ledger_path,
        steps=steps,
    )

    report = {
        "paper_id": args.paper_id,
        "ok": bool(final_metrics.get("full_closure", False)),
        "evidence_label": (
            "full_verified_closure"
            if final_metrics.get("full_closure", False)
            else "partial_diagnostic_evidence"
        ),
        "primary_metric": "verified_proven",
        "claim_scope": (
            "All provable statements are lake-verified without paper-local axiom debt."
            if final_metrics.get("full_closure", False) and not final_metrics.get("axiom_debt")
            else (
                "Partial diagnostic evidence: AXIOM_BACKED results are proved modulo paper-local axioms; "
                "use verified_proven for unconditional Lean-verified claims."
                if final_metrics.get("axiom_backed_count", 0)
                else "Not a full paper closure claim; use blocker taxonomy and verified_proven only."
            )
        ),
        "out_lean": str(out_lean),
        "ledger_path": str(ledger_path),
        "results_file": str(results_file),
        "unresolved_pack": str(unresolved_out),
        "missing_lemma_subledger_path": str(missing_lemma_out),
        "missing_lemma_subledger": final_metrics.get("missing_lemma_subledger", {}),
        "axiom_debt_burndown_path": str(axiom_debt_burndown_out),
        "axiom_debt_burndown": final_metrics.get("axiom_debt_burndown", {}),
        "paper_theory_debt_dashboard": final_metrics.get("paper_theory_debt_dashboard", {}),
        "deep_domain_obligations": final_metrics.get("deep_domain_obligations", {}),
        "statement_validity_report_path": str(statement_validity_out),
        "statement_validity": statement_validity_summary,
        "primary_blocker_dashboard": statement_validity_summary.get("counts", {}),
        "next_best_actions": [
            {
                "theorem_name": item.get("theorem_name", ""),
                "primary_blocker": item.get("primary_blocker", ""),
                "debt_tier": item.get("debt_tier", ""),
                "proof_value": item.get("proof_value", ""),
                "next_action": item.get("next_action", ""),
                "reasons": item.get("reasons", []),
            }
            for item in statement_validity_summary.get("items", [])
            if item.get("primary_blocker") != "release_ready"
        ],
        "proof_repair_cohort_path": str(proof_repair_cohort_out),
        "proof_repair_cohort_count": len(final_proof_repair_cohort),
        "paper_local_theory_file": str(paper_local_theory) if paper_local_theory else "",
        "paper_theory": paper_theory_artifacts,
        "generated_paper_theory_file": paper_theory_artifacts.get("paper_local_theory_file", ""),
        "paper_theory_manifest": paper_theory_artifacts.get("paper_theory_manifest", ""),
        "paper_theory_build_gate": paper_theory_artifacts.get("paper_theory_build_gate", {}),
        "ledger_stale_against_paper_theory_manifest": paper_theory_artifacts.get("ledger_stale_against_manifest", False),
        "curated_paper_package": curated_package,
        "auto_reliable_core": auto_reliable_core,
        "auto_reliable_core_promotion": auto_reliable_core_promotion,
        "claim_equivalence_review": claim_equivalence_review,
        "paper_local_axiom_disclosure": final_metrics.get("paper_local_axiom_disclosure", {}),
        "final_metrics": final_metrics,
        "final_normalization": final_normalization,
        "blocker_clusters": blocker_clusters,
        "pass_history": pass_history,
        "steps": steps,
    }
    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    translation_repair_pack: dict[str, Any] = {"available": False}
    translation_repair_application: dict[str, Any] = {"updated_count": 0, "updated_theorems": []}
    if not args.skip_translation_repair_pack:
        repair_dir = project_root / "output" / "translation_repairs" / safe
        cmd_repair_pack = [
            "python3",
            "scripts/repair_bad_translations.py",
            "--paper-id",
            args.paper_id,
            "--project-root",
            str(project_root),
            "--report",
            str(report_out),
            "--lean-file",
            str(out_lean),
            "--out-dir",
            str(repair_dir),
        ]
        repair_step = _run(cmd_repair_pack, cwd=project_root)
        steps.append({"stage": "translation_repair_pack", **repair_step})
        summary_path = repair_dir / "summary.json"
        repair_payload = _read_json_file(summary_path)
        if isinstance(repair_payload, dict) and repair_payload:
            repair_payload["summary_json"] = str(summary_path)
            final_entries, translation_repair_application = _apply_validated_translation_repairs(
                final_entries,
                repair_payload,
            )
            if translation_repair_application.get("updated_count"):
                final_normalization["translation_repaired_elaborating"] = int(
                    translation_repair_application.get("updated_count", 0) or 0
                )
                _save_ledger_entries(ledger_path, final_entries)
                final_metrics = _closure_metrics(final_entries)
                blocker_clusters = _blocker_clusters(final_entries)
                unresolved_out.write_text(
                    json.dumps(final_metrics.get("unresolved", []), indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                missing_lemma_out.write_text(
                    json.dumps(final_metrics.get("missing_lemma_subledger", {}), indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                axiom_debt_burndown_out.write_text(
                    json.dumps(final_metrics.get("axiom_debt_burndown", {}), indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                statement_validity_summary = summarize_validity(final_entries)
                statement_validity_out.write_text(
                    json.dumps(statement_validity_summary, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                final_proof_repair_cohort = proof_repair_cohort(final_entries)
                proof_repair_cohort_out.write_text(
                    json.dumps({"theorems": final_proof_repair_cohort}, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                report.update(
                    {
                        "ok": bool(final_metrics.get("full_closure", False)),
                        "evidence_label": (
                            "full_verified_closure"
                            if final_metrics.get("full_closure", False)
                            else "partial_diagnostic_evidence"
                        ),
                        "final_metrics": final_metrics,
                        "missing_lemma_subledger": final_metrics.get("missing_lemma_subledger", {}),
                        "axiom_debt_burndown": final_metrics.get("axiom_debt_burndown", {}),
                        "paper_theory_debt_dashboard": final_metrics.get("paper_theory_debt_dashboard", {}),
                        "deep_domain_obligations": final_metrics.get("deep_domain_obligations", {}),
                        "paper_local_axiom_disclosure": final_metrics.get("paper_local_axiom_disclosure", {}),
                        "claim_scope": (
                            "All provable statements are lake-verified without paper-local axiom debt."
                            if final_metrics.get("full_closure", False) and not final_metrics.get("axiom_debt")
                            else (
                                "Partial diagnostic evidence: AXIOM_BACKED results are proved modulo paper-local axioms; "
                                "use verified_proven for unconditional Lean-verified claims."
                                if final_metrics.get("axiom_backed_count", 0)
                                else "Not a full paper closure claim; use blocker taxonomy and verified_proven only."
                            )
                        ),
                        "final_normalization": final_normalization,
                        "blocker_clusters": blocker_clusters,
                        "statement_validity": statement_validity_summary,
                        "primary_blocker_dashboard": statement_validity_summary.get("counts", {}),
                        "next_best_actions": [
                            {
                                "theorem_name": item.get("theorem_name", ""),
                                "primary_blocker": item.get("primary_blocker", ""),
                                "debt_tier": item.get("debt_tier", ""),
                                "proof_value": item.get("proof_value", ""),
                                "next_action": item.get("next_action", ""),
                                "reasons": item.get("reasons", []),
                            }
                            for item in statement_validity_summary.get("items", [])
                            if item.get("primary_blocker") != "release_ready"
                        ],
                        "proof_repair_cohort_count": len(final_proof_repair_cohort),
                        "claim_equivalence_review": claim_equivalence_review,
                    }
                )
            translation_repair_pack = _repair_candidate_summary(repair_payload)

    report["translation_repair_pack"] = translation_repair_pack
    report["translation_repair_application"] = translation_repair_application
    report["steps"] = steps
    report_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    repro_bundle = _publish_reproducibility_bundle(
        project_root=project_root,
        paper_id=args.paper_id,
        report_out=report_out,
        ledger_path=ledger_path,
        unresolved_out=unresolved_out,
        missing_lemma_out=missing_lemma_out,
        axiom_debt_burndown_out=axiom_debt_burndown_out,
        statement_validity_out=statement_validity_out,
        proof_repair_cohort_out=proof_repair_cohort_out,
        paper_theory_manifest=(
            Path(str(paper_theory_artifacts.get("paper_theory_manifest", "")))
            if str(paper_theory_artifacts.get("paper_theory_manifest", ""))
            else None
        ),
        claim_equivalence_review_out=claim_equivalence_review_out if claim_equivalence_review_out.exists() else None,
        claim_equivalence_adjudications=claim_equivalence_adjudications if args.claim_equivalence_adjudications and claim_equivalence_adjudications.exists() else None,
    )
    report["reproducibility_bundle"] = repro_bundle
    report_out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if repro_bundle.get("report"):
        shutil.copyfile(report_out, repro_bundle["report"])
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "evidence_label": report["evidence_label"],
                "full_closure": final_metrics["full_closure"],
                "fully_proven": final_metrics["fully_proven"],
                "verified_proven": final_metrics["verified_proven"],
                "axiom_backed_count": final_metrics["axiom_backed_count"],
                "axiom_backed_result_label": PAPER_LOCAL_AXIOM_RESULT_LABEL,
                "auto_closed_count": final_metrics["auto_closed_count"],
                "translation_limited_count": final_metrics["translation_limited_count"],
                "total_theorems": final_metrics["total_theorems"],
                "report": str(report_out),
                "reproducibility_bundle": repro_bundle,
                "unresolved_pack": str(unresolved_out),
                "missing_lemma_subledger": str(missing_lemma_out),
                "axiom_debt_burndown": str(axiom_debt_burndown_out),
            },
            indent=2,
        )
    )
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
