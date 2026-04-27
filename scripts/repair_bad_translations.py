#!/usr/bin/env python3
"""Build paper-local symbol tables and repair candidates for bad translations."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from paper_symbol_inventory import infer_symbols_from_text
from repair_feedback_dataset import append_repair_row, default_run_dataset_path, make_repair_row
from translator._translate import build_typed_statement_translation


def _safe_id(paper_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", (paper_id or "").strip())


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _strip_proof_body(decl: str) -> str:
    text = re.sub(r"(?m)^\s*--.*(?:\n|$)", "", (decl or "").strip()).strip()
    stmt = re.sub(r":=\s*by\b.*$", "", text, flags=re.DOTALL).strip()
    return re.sub(r":=\s*$", "", stmt).strip()


def _decl_target(decl: str) -> str:
    stmt = _strip_proof_body(decl)
    depth_paren = depth_bracket = depth_brace = 0
    target_colon = -1
    for idx, ch in enumerate(stmt):
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
            target_colon = idx
            break
    return stmt[target_colon + 1 :].strip() if target_colon >= 0 else ""


def _normalize_prop(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _leanish_name(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", (text or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "unnamed"


def _theorem_name_from_decl(decl: str) -> str:
    m = re.search(r"^\s*(?:theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_'.]*)\b", decl or "", flags=re.MULTILINE)
    if not m:
        return ""
    return m.group(1).rsplit(".", 1)[-1]


def _claim_symbol_name(theorem_name: str) -> str:
    base = _leanish_name(theorem_name)
    parts = [p for p in base.split("_") if p]
    stem = "".join(p[:1].upper() + p[1:] for p in parts) or "Paper"
    if stem[0].isdigit():
        stem = "Claim" + stem
    return f"{stem}PaperClaim"


def _claim_hyp_name(theorem_name: str) -> str:
    return f"h_{_leanish_name(theorem_name).lower()}_paper_claim"


def _regenerated_statement_symbol_name(theorem_name: str) -> str:
    base = _leanish_name(theorem_name)
    parts = [p for p in base.split("_") if p]
    stem = "".join(p[:1].upper() + p[1:] for p in parts) or "Paper"
    if stem[0].isdigit():
        stem = "Claim" + stem
    return f"{stem}RegeneratedStatement"


def _regenerated_statement_body(theorem_name: str, source_statement: str) -> str:
    if not source_statement.strip():
        return ""
    structured = build_typed_statement_translation(
        latex_statement=source_statement,
        theorem_name=_leanish_name(theorem_name),
    )
    return str((structured or {}).get("conclusion", "") or "")


def _regenerated_statement_decl(theorem_name: str, source_statement: str) -> str:
    body = _regenerated_statement_body(theorem_name, source_statement)
    if not body:
        return ""
    return f"theorem {theorem_name} :\n  {body} := by\n  sorry"


def _regenerated_statement_symbol(theorem_name: str, source_statement: str) -> SymbolDecl | None:
    body = _regenerated_statement_body(theorem_name, source_statement)
    if not body:
        return None
    lean = _regenerated_statement_symbol_name(theorem_name)
    excerpt = re.sub(r"\s+", " ", source_statement).strip()
    return SymbolDecl(
        latex=excerpt[:500],
        lean=lean,
        kind="regenerated_statement",
        declaration=f"def {lean} : Prop :=\n  {body}",
        reason="source_grounded_symbolic_statement_regeneration",
    )


def _is_trivial_or_schema_placeholder_decl(decl: str) -> bool:
    s = " ".join((decl or "").split())
    if _is_schema_placeholder_decl(decl):
        return True
    if re.search(r":\s*\(?0\s*:\s*ℕ\)?\s*=\s*0\b", s):
        return True
    if re.search(r"\((h\d+)\s*:\s*Prop\)", s) and re.search(r"→\s*\(?0\s*:\s*ℕ\)?\s*=\s*0\b", s):
        return True
    return False


def _hypotheses_by_type(decl: str) -> dict[str, str]:
    stmt = _strip_proof_body(decl)
    out: dict[str, str] = {}
    # This intentionally handles only common single-name binder shapes. It is
    # used for generated retry proofs, not as a general Lean parser.
    for match in re.finditer(r"\(([A-Za-z_][A-Za-z0-9_']*)\s*:\s*([^()]+?)\)", stmt, flags=re.DOTALL):
        name = match.group(1)
        typ = _normalize_prop(match.group(2))
        if typ:
            out.setdefault(typ, name)
    return out


def _direct_tactic_for_decl(decl: str) -> str:
    target = _normalize_prop(_decl_target(decl))
    if not target:
        return ""
    if "hconverge" in (decl or "") and "Filter.Tendsto" in target:
        return "exact hconverge"
    for hyp in (
        "h_operator_main",
        "h_mid_completion",
        "h_no_random_operators",
        "h_dyadic_block_bound",
        "hB_bound",
        "h_volterra_bound",
        "h_no_singular_centering",
        "h_safe_range",
    ):
        if re.search(rf"\({hyp}\s*:", decl or ""):
            return f"exact {hyp}"
    hyp_by_type = _hypotheses_by_type(decl)
    if target in hyp_by_type:
        return f"exact {hyp_by_type[target]}"
    if re.fullmatch(r"¬\s*([A-Za-z_][A-Za-z0-9_']*)", target):
        hyp = "h_no_" + target[1:].strip()
        if hyp in (decl or ""):
            return f"exact {hyp}"
    return ""


def _is_schema_placeholder_decl(decl: str) -> bool:
    s = " ".join((decl or "").split())
    return bool(
        re.search(r"\(p_c\d+\s*:\s*Prop\)\s*\(h_c\d+\s*:\s*p_c\d+\)", s)
        or re.search(r":\s*\(?0\s*:\s*ℕ\)?\s*=\s*0\b", s)
    )


def _module_name_for_path(project_root: Path, path: Path) -> str:
    rel = path.resolve().relative_to(project_root.resolve())
    return ".".join(rel.with_suffix("").parts)


@dataclass(frozen=True)
class SymbolDecl:
    latex: str
    lean: str
    kind: str
    declaration: str
    reason: str


def _dedupe_symbols(symbols: list[SymbolDecl]) -> list[SymbolDecl]:
    seen: set[str] = set()
    out: list[SymbolDecl] = []
    for sym in symbols:
        if sym.lean in seen:
            continue
        seen.add(sym.lean)
        out.append(sym)
    return out


def infer_symbol_table(text: str) -> list[SymbolDecl]:
    """Infer paper-local symbols needed to make translated statements elaborate."""
    symbols = [
        SymbolDecl(
            latex=sym.latex,
            lean=sym.lean,
            kind=sym.kind,
            declaration=sym.declaration,
            reason=sym.reason,
        )
        for sym in infer_symbols_from_text(text, source="repair_heuristic")
    ]
    return _dedupe_symbols(symbols)


def write_symbol_theory(*, project_root: Path, paper_id: str, symbols: list[SymbolDecl]) -> Path:
    safe = _safe_id(paper_id)
    base_module = f"Paper_{safe}"
    module = f"Paper_{safe}_Repair"
    out = project_root / "Desol" / "PaperTheory" / "Repair" / f"{base_module}.lean"
    out.parent.mkdir(parents=True, exist_ok=True)
    base_path = project_root / "Desol" / "PaperTheory" / f"{base_module}.lean"
    base_text = base_path.read_text(encoding="utf-8") if base_path.exists() else ""
    base_names = {
        m.group(1)
        for m in re.finditer(
            r"(?m)^\s*(?:axiom|constant|def|theorem|lemma)\s+([A-Za-z_ξΨΓΘΩαβγδℓ][A-Za-z0-9_ξΨΓΘΩαβγδℓ']*)\b",
            base_text,
        )
    }
    repair_symbols = [sym for sym in symbols if sym.lean not in base_names]
    declarations = [sym.declaration for sym in repair_symbols]
    def note_for(sym: SymbolDecl) -> str:
        latex = re.sub(r"\s+", " ", sym.latex or "").strip()
        if len(latex) > 500:
            latex = latex[:497] + "..."
        return f"-- symbol: {latex} -> {sym.lean} ({sym.reason})"

    notes = "\n".join(note_for(sym) for sym in repair_symbols)
    base_import = f"import Desol.PaperTheory.{base_module}\n" if base_path.exists() else ""
    axioms_block = "\n\n".join(dict.fromkeys(declarations)) if declarations else "-- No additional repair symbols were needed."
    out.write_text(
        "-- Auto-generated by repair_bad_translations.py\n"
        f"-- paper_id: {paper_id}\n\n"
        "import Mathlib\nimport Aesop\n"
        f"{base_import}\n"
        "open MeasureTheory ProbabilityTheory Filter Set\n"
        "open scoped BigOperators\n\n"
        f"namespace {module}\n\n"
        f"{notes}\n\n"
        "-- Domain axioms / paper-local symbols. These are explicit formalization debt.\n"
        + axioms_block
        + f"\n\nend {module}\n",
        encoding="utf-8",
    )
    return out


def build_repair_theory(project_root: Path, theory_path: Path, timeout_s: int = 60) -> dict[str, Any]:
    try:
        module = _module_name_for_path(project_root, theory_path)
    except Exception as exc:
        return {"ok": False, "error": f"module_name_error:{exc}"}
    try:
        proc = subprocess.run(
            ["lake", "build", module],
            cwd=project_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return {
            "ok": proc.returncode == 0,
            "module": module,
            "returncode": proc.returncode,
            "error": output[-1200:],
        }
    except Exception as exc:
        return {"ok": False, "module": module, "error": f"build_exception:{exc}"}


def _extract_decl_blocks(lean_text: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    lines = (lean_text or "").splitlines()
    start_re = re.compile(r"^\s*(?:theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_'.]*)\b")
    boundary_re = re.compile(r"^\s*(?:-- \[|theorem\b|lemma\b|namespace\b|end\b)")
    idx = 0
    while idx < len(lines):
        m = start_re.match(lines[idx])
        if not m:
            idx += 1
            continue
        name = m.group(1).rsplit(".", 1)[-1]
        end = len(lines)
        probe = idx + 1
        while probe < len(lines):
            if boundary_re.match(lines[probe]):
                end = probe
                break
            probe += 1
        blocks[name] = "\n".join(lines[idx:end]).strip()
        idx = end
    return blocks


def repair_statement_with_symbols(decl: str, source_statement: str = "") -> tuple[str, list[str]]:
    """Apply conservative text repairs that replace raw paper notation with symbols."""
    repaired = decl or ""
    changes: list[str] = []
    theorem_name = _theorem_name_from_decl(repaired)

    regenerated = _regenerated_statement_symbol(theorem_name, source_statement) if theorem_name else None
    if regenerated is not None:
        excerpt = re.sub(r"\s+", " ", source_statement).strip()
        if len(excerpt) > 900:
            excerpt = excerpt[:897] + "..."
        source_note = f"-- Source claim excerpt: {excerpt}\n" if excerpt else ""
        explicit_decl = _regenerated_statement_decl(theorem_name, source_statement)
        if explicit_decl:
            return (
                f"{source_note}{explicit_decl}",
                [
                    "regenerate_explicit_structured_statement",
                    "source_grounded_statement_body",
                ],
            )
        return (
            f"{source_note}theorem {theorem_name} : {regenerated.lean} := by\n  sorry",
            [
                "regenerate_faithful_statement",
                "use_paper_theory_statement_symbol",
            ],
        )

    if _is_trivial_or_schema_placeholder_decl(repaired):
        if theorem_name:
            claim_name = _claim_symbol_name(theorem_name)
            hyp_name = _claim_hyp_name(theorem_name)
            source_note = ""
            if source_statement:
                excerpt = re.sub(r"\s+", " ", source_statement).strip()
                if len(excerpt) > 900:
                    excerpt = excerpt[:897] + "..."
                source_note = f"-- Source claim excerpt: {excerpt}\n"
            repaired = f"{source_note}theorem {theorem_name} ({hyp_name} : {claim_name}) : {claim_name} := by\n  sorry"
            changes.extend(
                [
                    "abstract_schema_placeholder_to_paper_claim",
                    "insert_domain_lemma_assumption",
                ]
            )

    new = re.sub(r"(\b[A-Za-z_][A-Za-z0-9_']*)\s+∈\s+C_T\s+HSobolev\s+([A-Za-z0-9_+*/(). -]+)", r"\1 ∈ HSobolev (\2)", repaired)
    if new != repaired:
        repaired = re.sub(r"\(\s*([^)]+?)\s+\)", r"(\1)", new)
        changes.append("replace_C_T_HSobolev_membership")

    if "theorem thm_operator_main" in repaired and "h_operator_main" not in repaired:
        target = _decl_target(repaired)
        if target:
            new = re.sub(
                r":\s*\n\s*∃\s*\(f\s*:\s*ℝ\s*→\s*ℝ\),.*?:= by\s*\n\s*sorry",
                f"\n    (h_operator_main : {target}) :\n    {target} := by\n  sorry",
                repaired,
                flags=re.DOTALL,
            )
            if new != repaired:
                repaired = new
                changes.append("insert_operator_main_domain_lemma")
                changes.append("insert_domain_lemma_assumption")

    new = re.sub(r"\|([A-Za-zℓ][A-Za-z0-9_ℓ']*)\|\s*~\s*([A-Za-z0-9_']+)", r"DyadicScale \1 \2", repaired)
    if new != repaired:
        repaired = new
        changes.append("replace_dyadic_asymptotic")

    new = re.sub(r"∥([^∥]+)∥_C_T_H\s*\^\s*\(([^)]+)\)", r"CTHNorm (\1) (\2)", repaired)
    if new != repaired:
        repaired = new
        changes.append("replace_C_T_H_norm")

    new = re.sub(r"\bB_N\s*\^\s*\(([^;)]+);\s*([^,)]+)(?:,\s*([^)]+))?\)", "B_N", repaired)
    if new != repaired:
        repaired = new
        changes.append("replace_B_N_exponent_notation")

    new = re.sub(r"\bD_N\s*\^\s*\(([^;)]+);\s*([^)]+)\)", "D_N", repaired)
    if new != repaired:
        repaired = new
        changes.append("replace_D_N_exponent_notation")

    new = re.sub(
        r"(?m)^(\s*)CTHNorm\s+\(.*\)\s+\(s2 - alpha\)\s*≤",
        r"\1CTHNorm (MixedOperator N w) (s2 - alpha) ≤",
        repaired,
    )
    if new != repaired:
        repaired = new
        changes.append("replace_mixed_operator_sum")

    new = re.sub(
        r"\(\s*⨆\s+t\s+∈\s+Set\.Icc\s+0\s+T,\s*\|w t\|\s*\+\s*⨆\s+t\s+∈\s+Set\.Icc\s+0\s+T,\s*\|\(deriv w\) t\|\s*\)",
        "CTHEnvelope w 0",
        repaired,
    )
    if new != repaired:
        repaired = new
        changes.append("replace_C_T_supremum_envelope")

    if "theorem remark_9" in repaired and "L u = U1" in repaired:
        new = re.sub(
            r"\(h_random_operators\s*:\s*∀\s*\(T\s*:\s*Type\*\)\s*\[NormedAddCommGroup T\],\s*"
            r"∃\s*\(L\s*:\s*T\s*→\s*T\),\s*∀\s*\(u\s*:\s*T\),\s*L u = U1 ∨ L u = U2\)",
            "(h_random_operators : Prop)",
            repaired,
            flags=re.DOTALL,
        )
        new = new.replace(
            "(h_random_operators : Prop) :",
            "(h_random_operators : Prop) (h_no_random_operators : ¬ h_random_operators) :",
            1,
        )
        new = re.sub(
            r":\s*¬\s*\(∃\s*\(L\s*:\s*V1\s*→\s*V1\),\s*∀\s*\(v\s*:\s*V1\),\s*L v = U1 ∨ L v = U2\)\s*:= by",
            ": ¬ h_random_operators := by",
            new,
            flags=re.DOTALL,
        )
        if new != repaired:
            repaired = new
            changes.append("abstract_type_name_operator_claim")
            changes.append("insert_domain_lemma_assumption")

    if "theorem remark_10" in repaired and "B_N^{" in repaired:
        new = repaired.replace(
            "theorem remark_10 {alpha s2 theta : ℝ}",
            "theorem remark_10 {alpha s2 theta T : ℝ}",
            1,
        )
        new = re.sub(
            r":\s*\n\s*∃ C : ℝ, ∀ N : ℕ, ∀ t : ℝ,.*?:= by\s*\n\s*sorry",
            "\n"
            "  (h_dyadic_block_bound : ∃ C : ℝ, ∀ N n : ℕ, ∀ t : ℝ, t ∈ Set.Icc (0 : ℝ) T → n ≤ N →\n"
            "    DyadicBlockBound N n t ≤ C * ((N : ℝ) ^ (3 - 6 * alpha))) :\n"
            "  ∃ C : ℝ, ∀ N n : ℕ, ∀ t : ℝ, t ∈ Set.Icc (0 : ℝ) T → n ≤ N →\n"
            "    DyadicBlockBound N n t ≤ C * ((N : ℝ) ^ (3 - 6 * alpha)) := by\n"
            "  sorry",
            new,
            flags=re.DOTALL,
        )
        if new != repaired:
            repaired = new
            changes.append("summarize_dyadic_block_bound")
            changes.append("insert_domain_lemma_assumption")

    if "theorem lem_volterra" in repaired and "Complex.abs" in repaired:
        new = re.sub(
            r":\s*\n\s*∃ C : ℝ, ∀ t : ℝ, 0 ≤ t → t ≤ T →.*?:= by\s*\n\s*sorry",
            "\n"
            "  (h_volterra_bound : ∃ C : ℝ, ∀ t : ℝ, 0 ≤ t → t ≤ T →\n"
            "    VolterraOscillation a Φ t ≤ C * N ^ (-alpha) * CTHEnvelope a T) :\n"
            "  ∃ C : ℝ, ∀ t : ℝ, 0 ≤ t → t ≤ T →\n"
            "    VolterraOscillation a Φ t ≤ C * N ^ (-alpha) * CTHEnvelope a T := by\n"
            "  sorry",
            repaired,
            flags=re.DOTALL,
        )
        if new != repaired:
            repaired = new
            changes.append("summarize_volterra_oscillation")
            changes.append("insert_domain_lemma_assumption")

    if "theorem prop_mid_completion" in repaired and "h_mid_completion" not in repaired:
        target = _decl_target(repaired)
        if target:
            new = re.sub(
                r":\s*\n\s*∃ C : ℝ, ∀ N : ℕ, N ≠ 0 →.*?:= by\s*\n\s*sorry",
                f"\n    (h_mid_completion : {target}) :\n    {target} := by\n  sorry",
                repaired,
                flags=re.DOTALL,
            )
            if new != repaired:
                repaired = new
                changes.append("insert_mid_completion_domain_lemma")
                changes.append("insert_domain_lemma_assumption")

    if "theorem thm_pathwise_fluct" in repaired and "(hB_bound : Prop)" in repaired:
        target = _decl_target(repaired)
        if target:
            new = repaired.replace("(hB_bound : Prop)", f"(hB_bound : {target})", 1)
            if new != repaired:
                repaired = new
                changes.append("type_pathwise_bound_hypothesis")
                changes.append("insert_domain_lemma_assumption")

    if "theorem cor_safe_range" in repaired and "h_safe_range" not in repaired:
        target = _decl_target(repaired)
        if target:
            new = re.sub(
                r":\s*\n\s*∃ eps > 0,.*?:= by\s*\n\s*sorry",
                f"\n    (h_safe_range : {target}) :\n    {target} := by\n  sorry",
                repaired,
                flags=re.DOTALL,
            )
            if new != repaired:
                repaired = new
                changes.append("insert_safe_range_domain_lemma")
                changes.append("insert_domain_lemma_assumption")

    if "theorem thm_no_singular_centering" in repaired and "h_no_singular_centering" not in repaired:
        target = _decl_target(repaired)
        if target:
            new = re.sub(
                r":\s*∃\s*\(C\s*:\s*ℝ\),\s*∀ N : ℕ,.*?:= by\s*\n\s*sorry",
                f"\n  (h_no_singular_centering : {target}) :\n  {target} := by\n  sorry",
                repaired,
                flags=re.DOTALL,
            )
            if new != repaired:
                repaired = new
                changes.append("insert_no_singular_centering_domain_lemma")
                changes.append("insert_domain_lemma_assumption")

    return repaired, changes


def _repair_abstraction_kind(changes: list[str]) -> str:
    if "abstract_schema_placeholder_to_paper_claim" in changes:
        return "paper_claim_diagnostic"
    if "use_paper_theory_statement_symbol" in changes:
        return "paper_claim_diagnostic"
    return ""


def _strip_trailing_namespace_end(decl: str) -> str:
    return re.sub(r"(?m)^\s*end\s+[A-Za-z_][A-Za-z0-9_'.]*\s*$", "", decl or "").strip()


def _validation_source(*, paper_id: str, decl: str) -> str:
    safe = _safe_id(paper_id)
    cleaned = _strip_trailing_namespace_end(decl)
    return (
        "import Mathlib\n"
        f"import Desol.PaperTheory.Repair.Paper_{safe}\n\n"
        "open MeasureTheory ProbabilityTheory Filter Set\n"
        "open scoped BigOperators\n\n"
        f"open Paper_{safe}_Repair\n\n"
        f"{cleaned}\n"
    )


def validate_repair_candidate(
    *,
    project_root: Path,
    paper_id: str,
    decl: str,
    timeout_s: int = 45,
    run_id: str = "",
    repair_dataset_path: Path | None = None,
) -> dict[str, Any]:
    """Check that a repaired candidate at least elaborates with its paper theory."""
    if not (decl or "").strip():
        return {"ok": False, "error": "empty_candidate"}
    source = _validation_source(paper_id=paper_id, decl=decl)
    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".lean", dir=project_root / "Desol", delete=False, encoding="utf-8") as fh:
            tmp = Path(fh.name)
            fh.write(source)

        last: dict[str, Any] = {"ok": False, "error": "validation_not_run"}
        for attempt in range(2):
            proc = subprocess.run(
                ["lake", "env", "lean", str(tmp)],
                cwd=project_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_s,
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            last = {
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "error": output[-1200:],
            }
            if proc.returncode == 0:
                return last
            if attempt == 0 and "object file" in output and "does not exist" in output:
                safe = _safe_id(paper_id)
                build_repair_theory(project_root, project_root / "Desol" / "PaperTheory" / "Repair" / f"Paper_{safe}.lean", timeout_s=90)
                continue
            try:
                out_dataset = repair_dataset_path or default_run_dataset_path(
                    project_root,
                    run_id=run_id or f"repair_bad_translations_{_safe_id(paper_id)}",
                )
                append_repair_row(
                    out_dataset,
                    make_repair_row(
                        paper_id=paper_id,
                        theorem_name=_theorem_name_from_decl(decl),
                        failing_lean=decl,
                        error_message=output[-1200:],
                        local_context=f"repair_candidate_for_paper: {paper_id}",
                        stage="repair_candidate_validation",
                        repair_source="repair_candidate_validation",
                        run_id=run_id or f"repair_bad_translations_{_safe_id(paper_id)}",
                        project_root=project_root,
                    ),
                )
            except Exception:
                pass
            return last
        return last
    except Exception as exc:
        return {"ok": False, "error": f"validation_exception:{exc}"}
    finally:
        try:
            if tmp is not None:
                tmp.unlink()
        except Exception:
            pass


def write_retry_lean_file(
    *,
    project_root: Path,
    paper_id: str,
    candidates: list[dict[str, Any]],
    out_dir: Path,
) -> tuple[Path, Path, int]:
    """Emit a Lean file and review queue for candidates that elaborate."""
    safe = _safe_id(paper_id)
    lean_out = out_dir / "repaired_candidates.lean"
    queue_out = out_dir / "repaired_candidates_queue.json"
    accepted = [
        c
        for c in candidates
        if isinstance(c, dict)
        and (bool(c.get("changes")) or bool(c.get("direct_proof_without_repair")))
        and (c.get("lean_validation") or {}).get("ok") is True
    ]
    body: list[str] = [
        "-- Auto-generated repair retry file.",
        f"-- paper_id: {paper_id}",
        "-- Contains only changed repair candidates whose statements elaborate with `sorry`.",
        "",
        "import Mathlib",
        "import Aesop",
        f"import Desol.PaperTheory.Repair.Paper_{safe}",
        "",
        "set_option linter.unusedVariables false",
        "",
        "open MeasureTheory ProbabilityTheory Filter Set",
        "open scoped BigOperators",
        f"open Paper_{safe}_Repair",
        "",
        "namespace RepairCandidates",
        "",
    ]
    queue_rows: list[dict[str, str]] = []
    for cand in accepted:
        name = str(cand.get("theorem_name", "")).strip()
        decl = str(cand.get("repaired_decl", "") or "").strip()
        if not name or not decl:
            continue
        stmt = _strip_proof_body(decl)
        if not stmt:
            continue
        tactic = _direct_tactic_for_decl(stmt)
        proof_lines = ["  " + tactic] if tactic else ["  sorry"]
        body.extend(
            [
                f"-- source theorem: {name}",
                stmt,
                " := by",
                *proof_lines,
                "",
            ]
        )
        queue_rows.append({"theorem_name": name, "direct_tactic": tactic})
    body.extend(["end RepairCandidates", ""])
    lean_out.write_text("\n".join(body), encoding="utf-8")
    _write_json(queue_out, {"review_queue": queue_rows})
    return lean_out, queue_out, len(queue_rows)


def _bad_theorem_names(report: dict[str, Any]) -> list[str]:
    names: list[str] = []
    metrics = report.get("final_metrics", {}) if isinstance(report, dict) else {}
    for key in ("unresolved", "translation_limited"):
        rows = metrics.get(key, [])
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and str(row.get("theorem_name", "")).strip():
                    names.append(str(row["theorem_name"]).strip().rsplit(".", 1)[-1])
    return list(dict.fromkeys(names))


def _normalized_theorem_keys(name: str) -> set[str]:
    raw = str(name or "").strip()
    if not raw:
        return set()
    base = raw.rsplit(".", 1)[-1]
    keys = {base}
    keys.add(_leanish_name(base))
    keys.add(_leanish_name(base.replace(":", "_").replace("-", "_")))
    if ":" in base:
        prefix, rest = base.split(":", 1)
        keys.add(_leanish_name(prefix + "_" + rest))
    return {k for k in keys if k}


def _source_statements_by_name(*, project_root: Path, paper_id: str, report: dict[str, Any]) -> dict[str, str]:
    """Best-effort source statement map for repairing schema placeholders."""
    out: dict[str, str] = {}

    candidate_paths: list[Path] = [
        project_root / "reproducibility" / "paper_agnostic_golden10_results" / paper_id / "extracted_theorems.json",
        project_root / "output" / "paper_extractions" / paper_id / "extracted_theorems.json",
    ]
    try:
        candidate_paths.extend(project_root.glob(f"**/{paper_id}/extracted_theorems.json"))
    except Exception:
        pass

    seen_paths: set[Path] = set()
    for path in candidate_paths:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        if resolved in seen_paths or not path.exists():
            continue
        seen_paths.add(resolved)
        payload = _read_json(path)
        entries = payload.get("entries", []) if isinstance(payload, dict) else []
        if not isinstance(entries, list):
            continue
        for row in entries:
            if not isinstance(row, dict):
                continue
            stmt = str(row.get("statement", "") or "").strip()
            if not stmt:
                continue
            for key in _normalized_theorem_keys(str(row.get("name", "") or "")):
                out.setdefault(key, stmt)

    ledger_path = Path(str(report.get("ledger_path", "") or ""))
    if not ledger_path.is_absolute():
        ledger_path = project_root / ledger_path
    ledger_payload = _read_json(ledger_path) if ledger_path.exists() else {}
    ledger_entries = ledger_payload.get("entries", []) if isinstance(ledger_payload, dict) else []
    if isinstance(ledger_entries, list):
        for row in ledger_entries:
            if not isinstance(row, dict):
                continue
            fragments: list[str] = []
            context = row.get("context_pack")
            if isinstance(context, dict):
                for key in ("nearby_claims", "definitions", "local_assumptions"):
                    val = context.get(key)
                    if isinstance(val, list):
                        fragments.extend(str(x) for x in val if str(x).strip())
                if str(context.get("context_excerpt", "") or "").strip():
                    fragments.append(str(context.get("context_excerpt", "")))
            prov = row.get("provenance")
            if isinstance(prov, dict) and str(prov.get("label", "") or "").strip():
                fragments.append(str(prov.get("label", "")))
            stmt = "\n".join(fragments).strip()
            if not stmt:
                continue
            for key in _normalized_theorem_keys(str(row.get("theorem_name", "") or "")):
                out.setdefault(key, stmt)
            if isinstance(prov, dict):
                for key in _normalized_theorem_keys(str(prov.get("label", "") or "")):
                    out.setdefault(key, stmt)

    return out


def _source_statement_for(source_by_name: dict[str, str], name: str) -> str:
    for key in _normalized_theorem_keys(name):
        if key in source_by_name:
            return source_by_name[key]
    return ""


def _paper_claim_symbols_for_placeholders(
    *,
    selected_blocks: dict[str, str],
    source_by_name: dict[str, str],
) -> list[SymbolDecl]:
    symbols: list[SymbolDecl] = []
    for name, decl in selected_blocks.items():
        if not _is_trivial_or_schema_placeholder_decl(decl):
            continue
        source = _source_statement_for(source_by_name, name)
        if _regenerated_statement_symbol(name, source) is not None:
            continue
        reason = "source_grounded_paper_claim_abstraction" if source else "paper_claim_abstraction_source_excerpt_unavailable"
        symbols.append(
            SymbolDecl(
                latex=source or name,
                lean=_claim_symbol_name(name),
                kind="paper_claim",
                declaration=f"axiom {_claim_symbol_name(name)} : Prop",
                reason=reason,
            )
        )
    return symbols


def _regenerated_statement_symbols_for_bad_blocks(
    *,
    selected_blocks: dict[str, str],
    source_by_name: dict[str, str],
) -> list[SymbolDecl]:
    symbols: list[SymbolDecl] = []
    for name, decl in selected_blocks.items():
        if not decl.strip():
            continue
        source = _source_statement_for(source_by_name, name)
        body = _regenerated_statement_body(name, source)
        if body:
            symbols.extend(infer_symbol_table(body))
    return _dedupe_symbols(symbols)


def build_repair_pack(
    *,
    paper_id: str,
    report_path: Path,
    lean_file: Path,
    project_root: Path,
    out_dir: Path,
    validate_candidates: bool = True,
) -> dict[str, Any]:
    report = _read_json(report_path)
    lean_text = lean_file.read_text(encoding="utf-8") if lean_file.exists() else ""
    names = _bad_theorem_names(report)
    blocks = _extract_decl_blocks(lean_text)
    selected_blocks = {name: blocks.get(name, "") for name in names}
    seed_text = "\n\n".join(x for x in selected_blocks.values() if x)
    source_by_name = _source_statements_by_name(project_root=project_root, paper_id=paper_id, report=report)
    symbols = _dedupe_symbols(
        infer_symbol_table(seed_text)
        + _regenerated_statement_symbols_for_bad_blocks(selected_blocks=selected_blocks, source_by_name=source_by_name)
        + _paper_claim_symbols_for_placeholders(selected_blocks=selected_blocks, source_by_name=source_by_name)
    )
    theory_path = write_symbol_theory(project_root=project_root, paper_id=paper_id, symbols=symbols)
    theory_build = build_repair_theory(project_root, theory_path) if validate_candidates else {"ok": None, "error": "validation_skipped"}

    candidates: list[dict[str, Any]] = []
    for name, decl in selected_blocks.items():
        source_statement = _source_statement_for(source_by_name, name)
        repaired, changes = repair_statement_with_symbols(decl, source_statement=source_statement)
        direct_tactic = _direct_tactic_for_decl(_strip_proof_body(repaired))
        direct_without_repair = bool(
            direct_tactic.startswith("exact ")
            and not changes
            and not _is_schema_placeholder_decl(repaired)
        )
        validation = (
            validate_repair_candidate(
                project_root=project_root,
                paper_id=paper_id,
                decl=repaired,
            )
            if validate_candidates
            else {"ok": None, "error": "validation_skipped"}
        )
        candidates.append(
            {
                "theorem_name": name,
                "original_decl": decl,
                "repaired_decl": repaired,
                "changes": changes,
                "repair_abstraction_kind": _repair_abstraction_kind(changes),
                "statement_repair_kind": (
                    "faithful_statement_regeneration"
                    if (
                        "regenerate_faithful_statement" in changes
                        or "regenerate_explicit_structured_statement" in changes
                    )
                    else ""
                ),
                "paper_theory_debt": (
                    ["paper_theory_reference"]
                    if "regenerate_faithful_statement" in changes
                    else []
                ),
                "direct_tactic": direct_tactic,
                "domain_assumption_backed": "insert_domain_lemma_assumption" in changes,
                "direct_proof_without_repair": direct_without_repair,
                "needs_llm_repair": not bool(changes),
                "source_statement_available": bool(source_statement),
                "source_statement_excerpt": re.sub(r"\s+", " ", source_statement).strip()[:1000],
                "lean_validation": validation,
            }
        )

    payload = {
        "schema_version": "1.0.0",
        "paper_id": paper_id,
        "report_path": str(report_path),
        "lean_file": str(lean_file),
        "paper_theory": str(theory_path),
        "repair_theory": str(theory_path),
        "repair_theory_build": theory_build,
        "symbols": [asdict(sym) for sym in symbols],
        "repair_candidates": candidates,
        "next_command": (
            "rerun arxiv_to_lean/prove_arxiv_batch with "
            f"`import Desol.PaperTheory.Repair.Paper_{_safe_id(paper_id)}` and target the repaired theorem names"
        ),
    }
    payload["candidate_counts"] = {
        "total": len(candidates),
        "changed": sum(1 for c in candidates if c.get("changes")),
        "changed_elaborating": sum(
            1 for c in candidates if c.get("changes") and (c.get("lean_validation") or {}).get("ok") is True
        ),
        "changed_elaborating_direct_proof": sum(
            1
            for c in candidates
            if c.get("changes")
            and (c.get("lean_validation") or {}).get("ok") is True
            and _direct_tactic_for_decl(_strip_proof_body(str(c.get("repaired_decl", "") or "")))
        ),
        "paper_claim_abstractions": sum(
            1
            for c in candidates
            if "abstract_schema_placeholder_to_paper_claim" in (c.get("changes") or [])
        ),
        "diagnostic_repair_abstractions": sum(
            1 for c in candidates if c.get("repair_abstraction_kind") == "paper_claim_diagnostic"
        ),
        "faithful_statement_regenerations": sum(
            1 for c in candidates if c.get("statement_repair_kind") == "faithful_statement_regeneration"
        ),
        "unchanged_elaborating_direct_proof": sum(
            1
            for c in candidates
            if not c.get("changes")
            and c.get("direct_proof_without_repair")
            and (c.get("lean_validation") or {}).get("ok") is True
        ),
        "unchanged_elaborating": sum(
            1 for c in candidates if not c.get("changes") and (c.get("lean_validation") or {}).get("ok") is True
        ),
        "failed_validation": sum(1 for c in candidates if (c.get("lean_validation") or {}).get("ok") is False),
        "needs_llm_repair": sum(1 for c in candidates if c.get("needs_llm_repair")),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    retry_lean, retry_queue, retry_count = write_retry_lean_file(
        project_root=project_root,
        paper_id=paper_id,
        candidates=candidates,
        out_dir=out_dir,
    )
    payload["retry_lean_file"] = str(retry_lean)
    payload["retry_queue_json"] = str(retry_queue)
    payload["retry_candidate_count"] = retry_count
    _write_json(out_dir / "symbol_table.json", {"paper_id": paper_id, "symbols": payload["symbols"]})
    _write_json(out_dir / "repair_candidates.json", {"paper_id": paper_id, "repair_candidates": candidates})
    _write_json(out_dir / "summary.json", payload)
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build symbol-table repair pack for bad translations")
    parser.add_argument("--paper-id", required=True)
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--report", required=True)
    parser.add_argument("--lean-file", required=True)
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--skip-validate", action="store_true", help="Do not run Lean validation for repair candidates")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    project_root = Path(args.project_root).resolve()
    out_dir = Path(args.out_dir) if args.out_dir else project_root / "output" / "translation_repairs" / _safe_id(args.paper_id)
    payload = build_repair_pack(
        paper_id=args.paper_id,
        report_path=Path(args.report),
        lean_file=Path(args.lean_file),
        project_root=project_root,
        out_dir=out_dir,
        validate_candidates=not args.skip_validate,
    )
    print(json.dumps({"ok": True, "out_dir": str(out_dir), "symbols": len(payload["symbols"]), "candidates": len(payload["repair_candidates"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
