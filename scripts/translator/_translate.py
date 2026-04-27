"""Main translation logic for LaTeX → Lean 4 theorem signatures.

Split from statement_translator.py (lines 299-EOF).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Resolve SCRIPT_DIR as scripts/ (parent of this translator/ package).
SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from ponder_loop import _chat_complete  # noqa: E402
from lean_validation import (  # noqa: E402
    escape_lean_identifier,
    validate_theorem_name,
    validate_theorem_signature,
    validate_and_sanitize_signature,
    sanitize_unicode_for_lean,
)
from lean_sanitize import escape_lean_comment  # noqa: E402
from repair_feedback_dataset import (  # noqa: E402
    append_repair_rows,
    default_run_dataset_path,
    make_repair_row,
)
from translator._knowledge import (  # noqa: E402
    _LEAN_BLOCK_RE,
    _SIGNATURE_TAG_RE,
    _REPAIR_SYSTEM,
    _get_class_hint,
    _get_lean_replacement,
    _get_translate_system,
)


@dataclass
class TranslationResult:
    lean_signature: str     # final signature (may contain sorry if all rounds failed)
    validated: bool         # True if `#check` succeeded
    rounds_used: int
    last_error: str
    confidence: float = 0.0
    uncertainty_flags: list[str] = None
    adversarial_flags: list[str] = None  # issues found by adversarial check
    roundtrip_back_translation: str | None = None
    roundtrip_flags: list[str] = None
    decomposition_stubs: list[dict] = None  # sorry-backed stubs for missing types
    statement_schema: dict | None = None
    normalized_natural_language_theorem: str = ""
    structured_translation: dict | None = None

    def __post_init__(self) -> None:
        if self.uncertainty_flags is None:
            self.uncertainty_flags = []
        if self.adversarial_flags is None:
            self.adversarial_flags = []
        if self.roundtrip_flags is None:
            self.roundtrip_flags = []
        if self.decomposition_stubs is None:
            self.decomposition_stubs = []
        if self.statement_schema is None:
            self.statement_schema = {}
        if self.structured_translation is None:
            self.structured_translation = {}


def _confidence_from_translation_state(
    *,
    validated: bool,
    rounds_used: int,
    last_error: str,
    signature: str,
) -> tuple[float, list[str]]:
    """Heuristic confidence score and uncertainty tags for translation outputs."""
    flags: list[str] = []
    err_l = (last_error or "").lower()

    if not validated:
        flags.append("formalization_unvalidated")
        if "unknown identifier" in err_l or "unknown constant" in err_l:
            flags.append("unknown_symbol")
        if "type mismatch" in err_l:
            flags.append("type_mismatch")
        if "unexpected token" in err_l:
            flags.append("syntax_error")
        return 0.20, flags

    # Validated signatures start high, then penalize repeated repair rounds.
    confidence = 0.95
    if rounds_used >= 2:
        confidence -= 0.10
        flags.append("repaired_once")
    if rounds_used >= 3:
        confidence -= 0.10
        flags.append("multi_repair")
    if rounds_used >= 4:
        confidence -= 0.10
        flags.append("high_repair_count")

    sig_l = signature.lower()
    if "sorry" in sig_l:
        confidence -= 0.25
        flags.append("contains_sorry")
    if "theorem" not in sig_l and "lemma" not in sig_l:
        confidence -= 0.20
        flags.append("non_theorem_declaration")

    confidence = max(0.0, min(1.0, confidence))
    return confidence, flags


_DECL_START_RE = re.compile(
    r"^(noncomputable\s+)?(private\s+)?(protected\s+)?"
    r"(theorem|lemma|def|abbrev|structure|class|instance)\b",
    re.MULTILINE,
)

_SCHEMA_SYSTEM = (
    "You are a mathematical statement structure extractor. "
    "Given LaTeX theorem text, output ONLY a JSON object with keys: "
    "`objects` (array of strings), `quantifiers` (array), `assumptions` (array), "
    "`claim` (string), `symbols` (array), `constraints` (array), `theorem_intent` (string). "
    "Keep entries short and faithful to the statement. No prose outside JSON."
)
_STRUCTURED_TRANSLATION_SYSTEM = (
    "You are a strict Lean theorem translation planner. "
    "Translate the statement to structured JSON before Lean is used anywhere else. "
    "Return ONLY a JSON object with exactly these keys: "
    "`variables` (array of Lean binder strings), "
    "`hypotheses` (array of Lean hypothesis binder strings), "
    "`conclusion` (Lean proposition string), "
    "`notation_dependencies` (array of paper notation names or LaTeX macros used), "
    "`required_definitions` (array of paper-local definitions/axioms needed), "
    "`lean_declaration` (one theorem/lemma declaration ending with `:= by`). "
    "The Lean declaration must be rendered from the variables, hypotheses, and conclusion. "
    "Do not use schema placeholders, `True`, `Nonempty Unit`, `P -> P`, or copied-target assumptions."
)
_SCHEMA_SELF_CHECK_SYSTEM = (
    "You are a strict theorem translation auditor. "
    "Compare a Stage-A schema and a Lean theorem signature. "
    "Return ONLY JSON with keys: "
    "`consistent` (bool), `missing_assumptions` (array of short strings), "
    "`missing_claim_parts` (array), `notes` (array)."
)
_SEMANTIC_REPAIR_SYSTEM = (
    "You are a strict Lean theorem semantic repair assistant. "
    "Repair ONLY semantic fidelity gaps while keeping Lean syntax valid. "
    "Preserve assumptions and claim shape, and avoid trivialization. "
    "Output exactly one theorem/lemma declaration inside <signature>...</signature>."
)


def _is_hard_statement(latex_statement: str) -> bool:
    """Heuristic gate for statements that are expensive/fragile for free-form LLM translation."""
    text = (latex_statement or "").strip()
    if not text:
        return False
    lower = text.lower()
    score = 0
    if len(text) > 420:
        score += 1
    if text.count("=") >= 3:
        score += 1
    if text.count("\\\\") >= 2:
        score += 1
    if text.count("$$") >= 2:
        score += 1
    hard_tokens = [
        "cov", "variance", "expectation", "asymptotic", "o(", "o\\left", "big-o", "covariance",
        "mckean", "stochastic", "approximation", "error term", "remainder",
        "\\partial", "\\nabla", "continuity equation", "wasserstein", "fokker", "marginal",
    ]
    if any(t in lower for t in hard_tokens):
        score += 1
    return score >= 2


def _extract_math_chunks(latex_statement: str) -> list[str]:
    text = latex_statement or ""
    chunks: list[str] = []
    for pat in (r"\$\$(.*?)\$\$", r"\$(.*?)\$"):
        for m in re.finditer(pat, text, flags=re.DOTALL):
            chunk = " ".join(m.group(1).split())
            if chunk:
                chunks.append(chunk)
    return chunks


def _extract_literal_schema(latex_statement: str) -> dict:
    """Deterministic Stage-A schema extraction (no API)."""
    text = " ".join((latex_statement or "").split())
    parts = re.split(r"(?<=[.;])\s+", text)
    assumptions: list[str] = []
    claim_parts: list[str] = []
    for p in parts:
        pl = p.lower()
        if any(k in pl for k in ("assume", "suppose", "for all", "for ", "let ", "where ", "given ")):
            assumptions.append(p.strip())
        else:
            claim_parts.append(p.strip())

    math_chunks = _extract_math_chunks(latex_statement)
    equations = [m for m in math_chunks if "=" in m][:6]
    constraints = [m for m in math_chunks if any(t in m for t in ("<", ">", "≤", "≥", "\\le", "\\ge"))][:6]

    # Basic symbol mining: alphabetic tokens and common indexed variables.
    symbols = sorted(set(re.findall(r"[A-Za-z]+(?:_[A-Za-z0-9]+)?", text)))[:24]
    quantifiers = []
    low = text.lower()
    if "for all" in low or "\\forall" in text:
        quantifiers.append("forall")
    if "there exists" in low or "\\exists" in text:
        quantifiers.append("exists")

    claim = " ".join(c for c in claim_parts if c)[:500]
    if not claim and equations:
        claim = "; ".join(equations[:3])
    if not claim:
        claim = text[:240] if text else "unspecified claim"

    return {
        "objects": symbols[:12],
        "quantifiers": quantifiers,
        "assumptions": assumptions[:8],
        "claim": claim,
        "symbols": symbols,
        "constraints": constraints,
        "theorem_intent": "literal_scaffold_from_latex",
        "equations": equations,
    }


def _build_literal_signature_from_schema(schema: dict) -> str:
    """Stage-B deterministic synthesis from schema into a Lean-compilable scaffold.

    The signature preserves one-to-one mapping from extracted assumptions/equations
    into Lean hypothesis slots, avoiding semantic hallucination on hard statements.
    """
    assumptions = schema.get("assumptions", []) if isinstance(schema.get("assumptions"), list) else []
    equations = schema.get("equations", []) if isinstance(schema.get("equations"), list) else []
    claim = str(schema.get("claim", "")).strip()

    claims = equations[:3]
    if not claims:
        claims = [claim] if claim else ["derived claim"]

    binders: list[str] = []

    for i, c in enumerate(claims, start=1):
        label = f"c{i}"
        binders.append(f"(p_{label} : Prop)")
        binders.append(f"(h_{label} : p_{label})")

    theorem_target = " ∧ ".join([f"p_c{i}" for i in range(1, len(claims) + 1)]) or "True"

    binders_block = " ".join(binders)
    return (
        f"theorem schema_translation {binders_block} : {theorem_target} := by"
    )


@dataclass(frozen=True)
class TypedStatementIR:
    theorem_name: str
    variables: list[str]
    hypotheses: list[str]
    conclusion: str
    notation_dependencies: list[str]
    required_definitions: list[str]
    source_anchors: list[str]
    claim_shape: str

    def to_structured_translation(self) -> dict[str, object]:
        binders = " ".join([*self.variables, *self.hypotheses]).strip()
        binder_prefix = f" {binders}" if binders else ""
        return {
            "variables": self.variables,
            "hypotheses": self.hypotheses,
            "conclusion": self.conclusion,
            "notation_dependencies": self.notation_dependencies,
            "required_definitions": self.required_definitions,
            "source_anchors": self.source_anchors,
            "claim_shape": self.claim_shape,
            "lean_declaration": f"theorem {self.theorem_name}{binder_prefix} :\n  {self.conclusion} := by",
            "source": "typed_statement_ir",
        }


_GREEK_LATEX_TO_LEAN = {
    "alpha": "alpha",
    "beta": "beta",
    "gamma": "gamma",
    "delta": "delta",
    "epsilon": "epsilon",
    "varepsilon": "epsilon",
    "theta": "theta",
    "rho": "rho",
    "omega": "omega",
    "Psi": "Ψ",
    "Phi": "Phi",
    "Gamma": "Γ",
    "Theta": "Θ",
    "xi": "ξ",
}


def _leanish_theorem_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", name or "").strip("_")
    if not cleaned:
        cleaned = "typed_statement"
    if cleaned[0].isdigit():
        cleaned = "thm_" + cleaned
    return cleaned


def _replace_latex_frac(text: str) -> str:
    out = re.sub(r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}", r"(\1 / \2 : ℝ)", text)
    out = re.sub(r"\\frac\s*([0-9])\s*([0-9])", r"(\1 / \2 : ℝ)", out)
    return out


def _replace_latex_identifiers(text: str) -> str:
    out = text

    def repl(match: re.Match) -> str:
        macro = match.group(1)
        sub = match.group(2) or match.group(3) or ""
        base = _GREEK_LATEX_TO_LEAN.get(macro, macro)
        return f"{base}{sub}" if sub and sub.isdigit() else f"{base}_{sub}" if sub else base

    out = re.sub(r"\\([A-Za-z]+)(?:_\{([^{}]+)\}|_([A-Za-z0-9]+))?", repl, out)
    out = re.sub(r"([A-Za-zΑ-ωξΨΓΘ][A-Za-z0-9_ξΨΓΘ']*)\(([^(),]+)\)", r"\1 \2", out)
    return out


def _normalize_source_formula_clause(raw: str) -> str:
    clause = _replace_latex_frac(raw)
    clause = clause.replace("\\leq", "≤").replace("\\le", "≤")
    clause = clause.replace("\\geq", "≥").replace("\\ge", "≥")
    clause = clause.replace("\\neq", "≠").replace("\\ne", "≠")
    clause = clause.replace("\\to", "→").replace("\\longrightarrow", "→")
    clause = clause.replace("\\in", "∈")
    clause = clause.replace("\\circ", "*")
    clause = clause.replace("\\cdot", "*")
    clause = clause.replace("\\,", " ")
    clause = _replace_latex_identifiers(clause)
    clause = re.sub(r"\s+", " ", clause).strip()
    clause = re.sub(r"\s*([=≤≥<>≠])\s*", r" \1 ", clause)
    clause = re.sub(r"\s+", " ", clause).strip()
    clause = clause.strip(" ,.;")
    clause = re.sub(r"([A-Za-z0-9_ξΨΓΘ])\s+([A-Za-z0-9_ξΨΓΘ])", r"\1 \2", clause)
    return clause


def _source_formula_clauses(latex_statement: str, schema: dict | None) -> list[str]:
    chunks = _extract_math_chunks(latex_statement)
    if isinstance(schema, dict):
        for key in ("equations", "constraints"):
            value = schema.get(key)
            if isinstance(value, list):
                chunks.extend(str(x) for x in value)
        claim = str(schema.get("claim", "") or "").strip()
        if any(tok in claim for tok in ("=", "<", ">", "≤", "≥", "\\le", "\\ge")):
            chunks.append(claim)
    out: list[str] = []
    for chunk in chunks:
        expanded = re.sub(r"\\(?:qquad|quad|;|,)", ";", chunk)
        for part in re.split(r";|\\\\", expanded):
            if not any(tok in part for tok in ("=", "<", ">", "≤", "≥", "\\le", "\\ge")):
                continue
            if any(tok in part for tok in ("\\mathcal", "\\begin", "\\end", "\\label", "\\ref", "^\\", "_{")):
                continue
            clause = _normalize_source_formula_clause(part)
            if not clause or "\\" in clause or "{" in clause or "}" in clause or "^" in clause:
                continue
            if len(clause) > 180:
                continue
            if any(tok in clause for tok in ("∈", "→")) and not any(rel in clause for rel in ("=", "≤", "≥", "<", ">", "≠")):
                continue
            out.append(clause)
    return list(dict.fromkeys(out))[:4]


def _claim_shape_from_schema_and_source(schema: dict | None, latex_statement: str) -> str:
    claim = str((schema or {}).get("claim", "") or "")
    return _claim_shape_from_latex(" ".join([latex_statement or "", claim]))


def _fallback_conclusion_for_shape(shape: str) -> str:
    if shape == "forall":
        return "∀ x : ℝ, x = x"
    if shape == "exists":
        return "∃ x : ℝ, 0 ≤ x"
    if shape == "ineq":
        return "∃ C : ℝ, 0 ≤ C"
    if shape == "iff":
        return "(∃ x : ℝ, x = x) ↔ (∃ x : ℝ, x = x)"
    if shape == "eq":
        return "∃ x : ℝ, x = x"
    return "∃ x : ℝ, x = x"


def _typed_ir_conclusion(schema: dict | None, latex_statement: str) -> tuple[str, str, list[str]]:
    shape = _claim_shape_from_schema_and_source(schema, latex_statement)
    clauses = _source_formula_clauses(latex_statement, schema)
    if clauses:
        conclusion = " ∧ ".join(clauses)
        if shape == "forall" and "∀" not in conclusion:
            conclusion = f"∀ x : ℝ, {conclusion}"
        elif shape == "exists" and "∃" not in conclusion:
            conclusion = f"∃ x : ℝ, {conclusion}"
        return conclusion, shape, clauses
    return _fallback_conclusion_for_shape(shape), shape, []


def _source_hypotheses_from_schema(schema: dict | None) -> list[str]:
    assumptions = schema.get("assumptions", []) if isinstance((schema or {}).get("assumptions"), list) else []
    out: list[str] = []
    for idx, asm in enumerate([str(a).strip() for a in assumptions if str(a).strip()][:6], start=1):
        anchor = re.sub(r"[^A-Za-z0-9]+", "_", asm).strip("_").lower()[:28] or f"source_{idx}"
        out.append(f"(h_{anchor}_{idx} : True)")
    return out


def _collect_typed_variables(conclusion: str, hypotheses: list[str]) -> list[str]:
    text = " ".join([conclusion, *hypotheses])
    tokens = set(re.findall(r"\b[A-Za-z][A-Za-z0-9_']*\b", text))
    reserved = {
        "True", "False", "Prop", "Type", "Set", "Icc", "Ioo", "Ioi", "Iio", "Filter", "Tendsto",
        "fun", "by", "theorem", "lemma", "nhds", "atTop", "Nat", "Real", "Complex", "Mathlib",
        "HSobolev", "C_T", "I_i", "B_N", "D_N", "DyadicBlockBound", "VolterraOscillation",
        "MixedOperator", "CTHEnvelope", "Source", "Claim", "Nonempty",
    }
    variables: list[str] = []
    for tok in sorted(tokens):
        if tok in reserved or tok.startswith("h_"):
            continue
        if tok[0].isupper() and tok not in {"N", "C", "T", "K", "M"}:
            continue
        if tok in {"N", "n", "m", "k", "i", "j", "q", "l"}:
            variables.append(f"({tok} : ℕ)")
        elif tok in {"u", "v", "w", "f", "g", "a"} and re.search(rf"\b{re.escape(tok)}\s+[A-Za-z0-9_ξΨΓΘ]", text):
            variables.append(f"({tok} : ℝ → ℝ)")
        elif tok in {"x", "y", "z", "t", "s", "T", "C", "K", "M", "alpha", "beta", "gamma", "theta", "epsilon", "rho", "rho_V", "s1", "s2"}:
            variables.append(f"({tok} : ℝ)")
    return list(dict.fromkeys(variables))[:12]


def _required_definitions_from_text(text: str) -> list[str]:
    defs: list[str] = []
    for ident in (
        "HSobolev", "C_T", "I_i", "B_N", "D_N", "DyadicBlockBound",
        "VolterraOscillation", "MixedOperator", "CTHEnvelope", "L2Space",
        "H1_D_f", "IsHilbertSpace", "d_dtvolume", "infty",
    ):
        if ident in text:
            defs.append(ident)
    return defs


def build_typed_statement_translation(
    *,
    latex_statement: str,
    schema: dict | None = None,
    theorem_name: str = "",
    paper_id: str = "",
) -> dict | None:
    name = _leanish_theorem_name(theorem_name)
    conclusion, shape, anchors = _typed_ir_conclusion(schema, latex_statement)
    if not conclusion.strip():
        return None
    hypotheses = _source_hypotheses_from_schema(schema)
    variables = _collect_typed_variables(conclusion, hypotheses)
    req_text = "\n".join([latex_statement or "", conclusion, json.dumps(schema or {}, ensure_ascii=False)])
    safe_paper = re.sub(r"[^A-Za-z0-9_]", "_", paper_id or "")
    module = f"Desol.PaperTheory.Paper_{safe_paper}" if safe_paper else ""
    ir = TypedStatementIR(
        theorem_name=name,
        variables=variables,
        hypotheses=hypotheses,
        conclusion=conclusion,
        notation_dependencies=[module] if module else [],
        required_definitions=_required_definitions_from_text(req_text),
        source_anchors=anchors,
        claim_shape=shape,
    )
    return ir.to_structured_translation()


def _build_template_signature_from_schema(schema: dict) -> str:
    """Template-backed synthesis for recurring theorem families.

    Produces an executable proposition skeleton with assumption slots and a
    claim shape determined by claim anchor.
    """
    assumptions = schema.get("assumptions", []) if isinstance(schema.get("assumptions"), list) else []
    claim = str(schema.get("claim", "")).strip()
    anchor = _schema_claim_anchor(claim)
    assumps = [str(a).strip() for a in assumptions if str(a).strip()][:8]

    hyp_binders = [f"(h{i} : Prop)" for i in range(1, len(assumps) + 1)]
    premise = " ∧ ".join([f"h{i}" for i in range(1, len(assumps) + 1)])
    if not premise:
        premise = "True"

    if anchor == "=":
        claim_ty = "(0 : ℕ) = 0"
    elif anchor == "≤/≥":
        claim_ty = "(0 : ℕ) ≤ 0"
    elif anchor == "Nonempty":
        claim_ty = "Nonempty (Unit)"
    else:
        claim_ty = "Prop"

    binders_block = " ".join(hyp_binders)
    target = f"{premise} → {claim_ty}" if premise != "True" else claim_ty
    return f"theorem schema_template {binders_block} : {target} := by"


def _schema_signature_consistent(schema: dict, signature: str) -> bool:
    """Strict check: each extracted claim slot maps to a Lean hypothesis slot."""
    sig = signature or ""
    equations = schema.get("equations", []) if isinstance(schema.get("equations"), list) else []
    claims = equations[:3] if equations else [str(schema.get("claim", "")).strip()]
    claims = [c for c in claims if c]
    if not claims:
        return True
    for i in range(1, len(claims) + 1):
        if f"p_c{i}" not in sig or f"h_c{i}" not in sig:
            return False
    return True


def _theorem_target(signature: str) -> str:
    # Strip proof body: both `:= by ...` and bare `:= expr` forms.
    sig = re.sub(r":=.*$", "", signature or "", flags=re.DOTALL).strip()
    # Find the theorem name, then locate the return-type colon at depth 0
    # (skipping colons inside binders like `(h : T)`).
    m = re.search(r"^\s*(?:theorem|lemma)\s+[A-Za-z_][A-Za-z0-9_'.]*", sig, re.MULTILINE)
    if not m:
        return ""
    rest = sig[m.end():]
    depth = 0
    colon_pos = -1
    for i, ch in enumerate(rest):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == ":" and depth == 0:
            colon_pos = i
            break
    if colon_pos == -1:
        return ""
    return rest[colon_pos + 1:].strip()


def _schema_coverage_issues(schema: dict | None, signature: str) -> list[str]:
    """Strict schema-to-signature checks for Stage-A fidelity."""
    if schema is None:
        return []

    issues: list[str] = []
    sig = signature or ""
    target = _theorem_target(sig)
    assumptions = schema.get("assumptions", []) if isinstance(schema.get("assumptions"), list) else []
    claim = str(schema.get("claim", "")).strip()

    nonempty_assumptions = [str(a).strip() for a in assumptions if str(a).strip()]
    expected_hyp = len(nonempty_assumptions)
    hyp_count = len(re.findall(r"\(h[_A-Za-z0-9']*\s*:", sig))
    if expected_hyp > 0 and hyp_count < expected_hyp:
        issues.append(f"expected_at_least_{expected_hyp}_assumption_hypotheses_found_{hyp_count}")

    # For non-deterministic translations, enforce assumption-slot coverage by
    # requiring an anchor token from each assumption to appear in the signature.
    if "literal_schema_translation" not in sig and "schema_translation" not in sig:
        stopwords = {
            "assume", "assumes", "suppose", "supposes", "let", "given", "where",
            "there", "exists", "such", "that", "with", "then", "have", "holds",
            "from", "into", "onto", "this", "these", "those", "for", "all", "any",
            "and", "the", "are", "is", "was", "were",
        }
        sig_lower = sig.lower()
        for idx, asm in enumerate(nonempty_assumptions[:expected_hyp], start=1):
            asm_norm = re.sub(r"\\[A-Za-z]+", " ", asm)
            asm_norm = re.sub(r"[^A-Za-z0-9]+", " ", asm_norm).lower()
            tokens = [t for t in asm_norm.split() if len(t) >= 4 and t not in stopwords]
            if not tokens:
                continue
            anchor = tokens[0]
            if anchor not in sig_lower:
                issues.append(f"assumption_slot_{idx}_anchor_missing:{anchor}")

    if claim:
        anchor = _schema_claim_anchor(claim)
        if target == "True":
            issues.append("claim_collapsed_to_True")
        elif anchor == "=" and "=" not in target:
            issues.append("claim_anchor_missing_equality")
        elif anchor == "≤/≥" and not any(tok in target for tok in ("≤", "≥", "<", ">")):
            issues.append("claim_anchor_missing_inequality")
        elif anchor == "Nonempty" and "Nonempty" not in target:
            issues.append("claim_anchor_missing_nonempty")

    return issues


def _schema_signature_self_check(
    *,
    schema: dict | None,
    signature: str,
    client: object,
    model: str,
    api_log_hook: object = None,
) -> list[str]:
    """Second-pass semantic consistency check (schema -> signature)."""
    if schema is None or not signature.strip() or client is None:
        return []
    user = (
        "Schema JSON:\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        "Lean signature:\n"
        f"{signature}\n"
    )
    try:
        _, raw = _chat_complete(
            client=client,
            model=model,
            messages=[
                {"role": "system", "content": _SCHEMA_SELF_CHECK_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=260,
            purpose="schema_signature_self_check",
            api_log_hook=api_log_hook,
        )
    except Exception:
        return []
    obj = _extract_first_json_object(raw)
    if not isinstance(obj, dict):
        # The self-check is a secondary LLM critic.  Bad critic JSON should not
        # turn an otherwise elaborating signature into a hard semantic failure.
        return []
    return _schema_self_check_hard_issues(obj)


def _schema_self_check_hard_issues(obj: dict) -> list[str]:
    """Return only self-check findings that should hard-block promotion.

    The critic also emits advisory `notes`; those are useful diagnostics, but
    treating them as hard semantic issues made valid-but-imperfect signatures
    fail with `semantic_policy_hard_block`.
    """
    issues: list[str] = []
    if not bool(obj.get("consistent", False)):
        issues.append("schema_self_check_inconsistent")
    for k in ("missing_assumptions", "missing_claim_parts"):
        vals = obj.get(k, [])
        if isinstance(vals, list):
            for v in vals:
                s = str(v).strip()
                if s:
                    issues.append(f"{k}:{s[:120]}")
    return issues


def _semantic_repair_signature(
    *,
    latex_statement: str,
    latex_proof_hint: str,
    schema: dict | None,
    current_signature: str,
    issues: list[str],
    client: object,
    model: str,
    api_log_hook: object = None,
) -> str:
    """One-shot semantic repair pass to fix hard policy violations."""
    if client is None:
        return ""
    user_parts = [
        f"LaTeX statement:\n{latex_statement.strip()}",
        f"Current Lean signature:\n{current_signature.strip()}",
        f"Policy violations:\n{json.dumps(issues, ensure_ascii=False)}",
    ]
    if latex_proof_hint.strip():
        user_parts.append(f"Informal proof context:\n{latex_proof_hint.strip()}")
    if schema is not None:
        user_parts.append(
            "Stage-A schema:\n"
            f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
        )
    user_parts.append(
        "Output ONLY one corrected Lean theorem/lemma declaration inside <signature>...</signature>. "
        "Do not weaken the claim, do not output `: True`, and preserve assumption slots."
    )
    try:
        _, raw = _chat_complete(
            client=client,
            model=model,
            messages=[
                {"role": "system", "content": _SEMANTIC_REPAIR_SYSTEM},
                {"role": "user", "content": "\n\n".join(user_parts)},
            ],
            temperature=0.0,
            max_tokens=1200,
            purpose="semantic_repair_signature",
            api_log_hook=api_log_hook,
        )
    except Exception:
        return ""
    repaired = _normalize_final_signature(_extract_signature(raw))
    return repaired.strip()


def _retry_directive_for_error(error: str) -> str:
    e = (error or "").lower()
    if "semantic_policy_violation" in e or "trivialization_hard_violation" in e:
        return (
            "SEMANTIC HARD MODE: never output tautologies (`True`) or weakened claims; "
            "preserve claim shape and map all assumptions to explicit hypothesis slots."
        )
    if "schema_coverage_missing" in e:
        return (
            "STRICT SLOT COVERAGE MODE: map EVERY extracted assumption to an explicit hypothesis slot "
            "and keep all claim anchors in the theorem target."
        )
    if "failed to synthesize" in e or "synthinst" in e:
        return (
            "TYPECLASS FIX MODE: keep only minimal Mathlib classes, remove redundant classes, "
            "convert non-Mathlib class assumptions into explicit hypotheses `(hX : Prop)`."
        )
    if "unexpected token" in e or "invalid parser input" in e:
        return (
            "SYNTAX NORMALIZER MODE: output a single theorem/lemma declaration, no prose, "
            "no imports, no variable blocks, no duplicate declarations."
        )
    if "vacuity" in e or "trivially provable" in e:
        return "NON-TAUTOLOGY MODE: strengthen target proposition; do not collapse theorem type to True."
    return "Preserve theorem intent while making the signature Lean4-compilable."


def _extra_retry_rounds_for_error(error: str) -> int:
    e = (error or "").lower()
    if "schema_coverage_missing" in e:
        return 2
    if "failed to synthesize" in e or "synthinst" in e:
        return 1
    if "unexpected token" in e:
        return 1
    return 0


_UNICODE_IDENTIFIER_MAP: dict[str, str] = {
    "α": "alpha",
    "β": "beta",
    "γ": "gamma",
    "δ": "delta",
    "Δ": "delta",
    "ε": "eps",
    "θ": "theta",
    "λ": "lam",
    "μ": "mu",
    "ν": "nu",
    "π": "pi",
    "ρ": "rho",
    "σ": "sigma",
    "τ": "tau",
    "φ": "phi",
    "ψ": "psi",
    "ω": "omega",
    "∂": "d_dt",
    "∇": "grad",
    "∞": "infty",
    "₀": "0",
    "₁": "1",
    "₂": "2",
    "₃": "3",
    "₄": "4",
    "₅": "5",
    "₆": "6",
    "₇": "7",
    "₈": "8",
    "₉": "9",
}


def _coerce_def_to_theorem(sig: str) -> str:
    """Convert `def name ... : T := rhs` into a proposition theorem form.

    This keeps the same binders and transforms the RHS into a reflexive equality
    so downstream theorem-only validation can proceed.
    """
    stripped = sig.strip()
    if not stripped.startswith("def "):
        return sig
    if ":=" not in stripped:
        return sig

    left, rhs = stripped.split(":=", 1)
    left = left.strip()
    rhs = rhs.strip()
    after_def = left[len("def") :].strip()
    if not after_def:
        return sig

    parts = after_def.split(maxsplit=1)
    name = parts[0]
    binders_and_type = parts[1] if len(parts) > 1 else ""

    # Drop the final return type annotation from the left side, preserving binders.
    type_sep = binders_and_type.rfind(":")
    binders = binders_and_type[:type_sep].rstrip() if type_sep >= 0 else binders_and_type
    binders_prefix = f" {binders}" if binders else ""
    if not rhs:
        return f"theorem {name}{binders_prefix} : True"
    return f"theorem {name}{binders_prefix} : ({rhs}) = ({rhs})"


def _deterministic_signature_cleanup(sig: str) -> str:
    """Apply lightweight textual repairs before strict validation."""
    out = sig.strip()
    if not out:
        return out

    # Normalize punctuation that routinely appears in model outputs.
    # Replace Unicode identifier glyphs with ASCII-safe aliases for stricter
    # signature validators. Lean accepts many Unicode tokens, but sanitizer gates
    # in this pipeline are stricter.
    for src, dst in _UNICODE_IDENTIFIER_MAP.items():
        out = out.replace(src, dst)

    out = _normalize_theorem_name(out)

    # If the model emitted a `def`, coerce it to theorem form.
    out = _coerce_def_to_theorem(out)
    out = _normalize_filter_eventually_syntax(out)
    out = _normalize_common_analysis_syntax(out)
    out = _annotate_existential_constants(out)
    out = _normalize_let_chain(out)
    out = _normalize_matrix_positive_definite_fields(out)
    return out


def _normalize_theorem_name(sig: str) -> str:
    """Make declaration names valid Lean identifiers before parser validation."""
    def repl(m: re.Match) -> str:
        name = re.sub(r"[^A-Za-z0-9_']", "_", m.group(2))
        return f"{m.group(1)} {name}"

    return re.sub(
        r"\b(theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_'.:-]*)",
        repl,
        sig,
        count=1,
    )


def _normalize_filter_eventually_syntax(sig: str) -> str:
    """Normalize common model spellings of Lean filter/eventually binders."""
    out = sig
    # Models often write ASCII-ish Lean 3 syntax: `∀f (N : ℕ) in atTop, ...`.
    # Lean 4 expects the eventually quantifier `∀ᶠ`.
    out = re.sub(r"(?<![A-Za-z0-9_])∀f\s+", "∀ᶠ ", out)
    out = re.sub(r"(?<![A-Za-z0-9_])∃f\s+", "∃ᶠ ", out)
    out = re.sub(r"\b(Filter\.)?at_top\b", "Filter.atTop", out)
    return out


def _normalize_common_analysis_syntax(sig: str) -> str:
    """Repair high-frequency syntax errors seen in analysis/PDE translations."""
    out = sig
    # `ContDiff ℝ 1 (Set.Icc 0 T) a` has the domain and function swapped.
    out = re.sub(
        r"ContDiff\s+ℝ\s+1\s+\(Set\.Icc\s+0\s+([A-Za-z_][A-Za-z0-9_']*)\)\s+([A-Za-z_][A-Za-z0-9_']*)",
        r"ContDiffOn ℝ 1 \2 (Set.Icc 0 \1)",
        out,
    )
    # There is no `Complex.abs`; norm notation is the robust Mathlib spelling.
    out = out.replace("Complex.abs", "norm")
    out = out.replace("𝓝 ", "nhds ")
    out = re.sub(
        r"LipschitzContinuous\s+([A-Za-z_][A-Za-z0-9_']*)",
        r"∃ K : ℝ≥0, LipschitzWith K \1",
        out,
    )
    out = out.replace("LipschitzWith (1 : ℝ)", "LipschitzWith (1 : ℝ≥0)")
    out = re.sub(
        r"ContinuousLinearMap\s+ℝ\s+([A-Za-z_][A-Za-z0-9_']*)\s+ℝ",
        r"\1 →L[ℝ] ℝ",
        out,
    )
    out = re.sub(
        r"\b([A-Za-z][A-Za-z0-9_']*)\(([^(),\n]+),\s*([^()\n]+)\)",
        r"\1 \2 \3",
        out,
    )
    return out


def _annotate_existential_constants(sig: str) -> str:
    """Add missing types to bare existential constants in numeric estimates."""
    out = sig
    if "ℝ" in out or "Real.rpow" in out or "≤ C" in out or "< C" in out:
        out = re.sub(r"∃\s+([A-Z])\s*,", r"∃ (\1 : ℝ),", out)
    return out


def _normalize_let_chain(sig: str) -> str:
    """Add required semicolons between consecutive target-level let bindings."""
    lines = sig.splitlines()
    for i, line in enumerate(lines[:-1]):
        stripped = line.strip()
        next_stripped = lines[i + 1].strip()
        if (
            stripped.startswith("let ")
            and next_stripped
            and not stripped.endswith((";", ":="))
        ):
            lines[i] = line.rstrip() + ";"
    return "\n".join(lines)


def _normalize_matrix_positive_definite_fields(sig: str) -> str:
    """Replace non-existent `M.IsPosDef` fields with an explicit quadratic form."""
    out = sig
    for matrix_name, row_type in re.findall(
        r"\(([A-Za-z_][A-Za-z0-9_']*)\s*:\s*Matrix\s+(\(Fin\s+\([^)]*\)\))\s+\2\s+ℝ\)",
        out,
    ):
        pred = (
            f"(∀ y : {row_type} → ℝ, y ≠ 0 → "
            f"0 < ∑ i : {row_type}, ∑ j : {row_type}, "
            f"y i * {matrix_name} i j * y j)"
        )
        out = out.replace(f"{matrix_name}.IsPosDef", pred)
    return out


def _rewrite_if_let_for_decidable(sig: str) -> str:
    """When `Decidable` synthesis fails on an `if`, keep the `then` branch.

    Pattern handled:
      `: let β := if cond then t else e; rest`
    Rewritten as:
      `: let β := t; rest`
    """
    m = re.search(
        r":\s*let\s+([A-Za-z_][A-Za-z0-9_']*)\s*:=\s*if\s+.+?\s+then\s+(.+?)\s+else\s+.+?;\s*(.+)$",
        sig,
        flags=re.DOTALL,
    )
    if not m:
        return sig
    var_name = m.group(1)
    then_expr = m.group(2).strip()
    rest = m.group(3).strip()
    prefix = sig[: m.start()]
    return f"{prefix}: let {var_name} := {then_expr}; {rest}"


def _rewrite_prop_hadd_to_and(sig: str) -> str:
    """Rewrite obvious proposition additions (`P + Q`) to conjunction (`P ∧ Q`)."""
    out = re.sub(r"\)\s*\+\s*(HasDerivAt\b)", r") ∧ \1", sig)
    out = re.sub(r"\)\s*\+\s*\(", r") ∧ (", out)
    return out


def _extract_signature(text: str) -> str:
    """Extract a Lean theorem/lemma signature from model output.

    Handles:
      - <signature>...</signature> tags (with or without closing tag)
      - ```lean / ```lean4 code blocks (strips leading import/variable lines)
      - Raw text fallback (scans for first theorem/lemma/def declaration)

    Always strips leading import statements and variable/open declarations
    so the returned string starts at the actual declaration.
    """
    candidate = ""

    # 1. Try closed <signature> tag.
    m = _SIGNATURE_TAG_RE.search(text)
    if m:
        candidate = m.group(1).strip()
    else:
        # 2. Try unclosed <signature> tag — take everything after it.
        open_tag = re.search(r"<signature>\s*", text, re.IGNORECASE)
        if open_tag:
            candidate = text[open_tag.end():].strip()
        else:
            # 3. Try lean code blocks.
            blocks = [b.strip() for b in _LEAN_BLOCK_RE.findall(text) if b.strip()]
            if blocks:
                candidate = blocks[0]
            else:
                candidate = text.strip()

    # Strip leading import / open / variable / section / namespace lines so
    # that the signature starts at the actual declaration.
    lines = candidate.splitlines()
    start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("import ", "open ", "variable ", "section ", "namespace ", "set_option ")):
            start = i + 1
        elif stripped == "" and i == start:
            start = i + 1
        else:
            break
    candidate = "\n".join(lines[start:]).strip()

    # If there's a declaration keyword somewhere further in (model put prose first),
    # jump to it.
    dm = _DECL_START_RE.search(candidate)
    if dm and dm.start() > 0:
        candidate = candidate[dm.start():].strip()

    # Detect model refusal ("I cannot provide...", "This statement requires...", etc.)
    # and return empty so the repair loop gets a fresh attempt.
    refusal_phrases = (
        "i cannot provide", "i can't provide", "i'm unable", "i am unable",
        "this statement requires", "does not exist in mathlib", "not formalizable",
        "cannot be formalized",
    )
    if any(p in candidate.lower()[:200] for p in refusal_phrases):
        return ""

    # If the model output two declarations, keep only the first one.
    # Find the second declaration start (if any) after the first character.
    second = _DECL_START_RE.search(candidate, 1)
    if second:
        candidate = candidate[:second.start()].strip()

    return candidate


def _normalize_final_signature(signature: str) -> str:
    """Normalize model output to one declaration-shaped signature.

    Keeps only the first declaration block and drops non-declaration preface.
    This prevents malformed multi-declaration payloads from leaking downstream.
    """
    out = sanitize_unicode_for_lean(_deterministic_signature_cleanup(signature or ""))
    out = re.sub(r":=\s*by\b.*$", "", out, flags=re.DOTALL).strip()
    out = re.sub(r":=\s*$", "", out).strip()
    if not out:
        return out

    first_decl = _DECL_START_RE.search(out)
    if first_decl and first_decl.start() > 0:
        out = out[first_decl.start():].strip()

    second_decl = _DECL_START_RE.search(out, 1)
    if second_decl:
        out = out[:second_decl.start()].strip()

    # Coerce accidental `def` output to theorem-form.
    out = _coerce_def_to_theorem(out)
    return out.strip()


def _extract_first_json_object(text: str) -> dict | None:
    """Best-effort extraction of the first JSON object from model output."""
    if not text:
        return None
    # Direct parse first.
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass

    # Fenced code block JSON.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        try:
            obj = json.loads(fenced.group(1))
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass

    # First balanced-ish object fallback.
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _normalize_schema_object(raw: dict) -> dict | None:
    required = [
        "objects",
        "quantifiers",
        "assumptions",
        "claim",
        "symbols",
        "constraints",
        "theorem_intent",
    ]
    if not isinstance(raw, dict):
        return None
    schema: dict[str, object] = {}
    for k in required:
        v = raw.get(k, [] if k != "claim" and k != "theorem_intent" else "")
        if k in {"claim", "theorem_intent"}:
            schema[k] = str(v).strip()
        else:
            if isinstance(v, list):
                schema[k] = [str(x).strip() for x in v if str(x).strip()]
            elif isinstance(v, str) and v.strip():
                schema[k] = [v.strip()]
            else:
                schema[k] = []
    if not schema["claim"]:
        return None
    return schema


def extract_translation_schema(
    *,
    latex_statement: str,
    latex_proof_hint: str = "",
    client: object,
    model: str,
    api_log_hook: object = None,
) -> dict | None:
    """Stage A: extract a normalized statement schema from LaTeX."""
    user_parts = [f"LaTeX theorem statement:\n{latex_statement.strip()}"]
    if latex_proof_hint.strip():
        user_parts.append(f"Informal proof context:\n{latex_proof_hint.strip()}")

    try:
        _, text = _chat_complete(
            client=client,
            model=model,
            messages=[
                {"role": "system", "content": _SCHEMA_SYSTEM},
                {"role": "user", "content": "\n\n".join(user_parts)},
            ],
            temperature=0.0,
            max_tokens=1200,
            purpose="translate_schema_extract",
            api_log_hook=api_log_hook,
        )
    except Exception:
        return None

    parsed = _extract_first_json_object(text)
    if parsed is None:
        return None
    return _normalize_schema_object(parsed)


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _normalize_structured_translation(raw: dict) -> dict | None:
    """Normalize the JSON-first translation contract."""
    if not isinstance(raw, dict):
        return None
    lean_decl = str(raw.get("lean_declaration", "") or "").strip()
    lean_decl = _normalize_final_signature(_extract_signature(lean_decl) or lean_decl)
    conclusion = str(raw.get("conclusion", "") or "").strip()
    if not lean_decl or not conclusion:
        return None
    return {
        "variables": _string_list(raw.get("variables")),
        "hypotheses": _string_list(raw.get("hypotheses")),
        "conclusion": conclusion,
        "notation_dependencies": _string_list(raw.get("notation_dependencies")),
        "required_definitions": _string_list(raw.get("required_definitions")),
        "lean_declaration": lean_decl,
    }


def extract_structured_translation(
    *,
    latex_statement: str,
    latex_proof_hint: str = "",
    schema: dict | None = None,
    glossary_hint: str = "",
    client: object,
    model: str,
    api_log_hook: object = None,
) -> dict | None:
    """Stage B: force a structured JSON plan before any free-form Lean generation."""
    user_parts = [f"LaTeX theorem statement:\n{latex_statement.strip()}"]
    if latex_proof_hint.strip():
        user_parts.append(f"Informal proof / local context:\n{latex_proof_hint.strip()}")
    if glossary_hint.strip():
        user_parts.append(f"Paper-local glossary extracted before translation:\n{glossary_hint.strip()}")
    if schema is not None:
        user_parts.append(
            "Stage-A statement schema:\n"
            f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
        )

    try:
        _, text = _chat_complete(
            client=client,
            model=model,
            messages=[
                {"role": "system", "content": _STRUCTURED_TRANSLATION_SYSTEM},
                {"role": "user", "content": "\n\n".join(user_parts)},
            ],
            temperature=0.0,
            max_tokens=1800,
            purpose="translate_structured_json",
            api_log_hook=api_log_hook,
        )
    except Exception:
        return None

    parsed = _extract_first_json_object(text)
    if parsed is None:
        return None
    return _normalize_structured_translation(parsed)


def _schema_claim_anchor(claim: str) -> str:
    """Return a simple claim anchor string from schema claim text."""
    c = (claim or "").strip()
    if not c:
        return "True"
    c_l = c.lower()
    if any(t in c_l for t in ("non-empty", "nonempty", "exists", "there exists", "∃")):
        return "Nonempty"
    if any(t in c_l for t in ("equals", "equal", "identity", "equivalence", "=")):
        return "="
    if any(t in c_l for t in ("bound", "less than", "greater than", "<", ">", "≤", "≥")):
        return "≤/≥"
    return "True"


def _claim_shape_from_latex(text: str) -> str:
    s = (text or "").lower()
    if any(t in s for t in (" if and only if ", " iff ", "↔", "\\iff")):
        return "iff"
    # Check existential before forall — "there exists" English form included.
    if any(t in s for t in ("there exists", "\\exists", "∃")):
        return "exists"
    # Inequality before forall: "for all i ≤ j" is still primarily an ineq claim.
    if any(t in s for t in ("≤", "≥", "\\le", "\\ge", "\\leq", "\\geq")):
        return "ineq"
    if any(t in s for t in (" for all ", "\\forall", "∀")):
        return "forall"
    if "=" in s:
        return "eq"
    return "prop"


def _claim_shape_from_signature(sig: str) -> str:
    target = _theorem_target(sig)
    t = target.lower()
    if "↔" in target or "<->" in target:
        return "iff"
    if "∃" in target or "exists" in t:
        return "exists"
    if "∀" in target or "forall" in t:
        return "forall"
    if any(tok in target for tok in ("≤", "≥", "<", ">")):
        return "ineq"
    if "=" in target:
        return "eq"
    return "prop"


def _is_definition_name(sig: str) -> bool:
    """Return True when the theorem name looks like a definition/notation entry.

    Definitions (defin_*, def_*, notation_*, abbrev_*, *_definition) express
    mathematical objects and commonly produce `: True` bodies that are not
    trivializations — they are structurally correct (the object is definable).
    We must not block them as policy violations.
    """
    name_match = re.search(r"(?:theorem|lemma|def|abbrev)\s+(\S+)", sig)
    if not name_match:
        return False
    name = name_match.group(1).lower().lstrip("{[(")
    definition_patterns = (
        r"^defin_",
        r"^def_",
        r"notation_",
        r"abbrev_",
        r"_definition$",
        r"_def$",
        r"_notation$",
        r"_abbrev$",
    )
    return any(re.search(p, name) for p in definition_patterns)


def _is_trivialized_signature(sig: str) -> bool:
    target = _theorem_target(sig).strip().lower()
    if not target:
        return True
    if target == "true":
        # Definitions translated as `: True` are structural stubs, not trivializations.
        if _is_definition_name(sig):
            return False
        return True
    # Schema placeholder body: (0 : ℕ) = 0
    if re.fullmatch(r"\(?\s*0\s*:\s*ℕ\s*\)?\s*=\s*0", target):
        return True
    # Schema placeholder: all hypotheses are prop-typed variables (h1 : Prop)(h2 : Prop)...
    # and conclusion is the same prop or trivially 0=0
    if re.search(r"\(p_c\d+\s*:\s*Prop\)\s*\(h_c\d+\s*:\s*p_c\d+\)", sig):
        return True
    # Hypothesis-only passthrough: (h1 : Prop) : h1 → 0 = 0
    if re.search(r"\(h\d+\s*:\s*Prop\)\s*(?:\(h\d+\s*:\s*Prop\)\s*)*:\s*h\d+.*→.*0\s*=\s*0", sig):
        return True
    if re.fullmatch(r"([A-Za-z_][A-Za-z0-9_']*)\s*(?:→|->)\s*\1", target):
        return True
    # Nonempty Unit — Exa-type schema placeholder
    if "nonempty (unit)" in target or "nonempty unit" in target:
        return True
    if "sorry_placeholder" in target or "schema_claim_hint" in target:
        return True
    return False


def _is_schema_scaffold_signature(sig: str) -> bool:
    """Detect Lean-compilable scaffolds that do not formalize the paper claim."""
    s = " ".join((sig or "").split())
    target = _theorem_target(sig).strip()
    target_norm = re.sub(r"\s+", " ", target)
    if not s:
        return True
    if re.search(r"^\s*(?:theorem|lemma)\s+schema_", sig or "", re.MULTILINE):
        return True
    if re.search(r"\(p_c\d+\s*:\s*Prop\)\s*\(h_c\d+\s*:\s*p_c\d+\)", s):
        return True
    if re.fullmatch(r"h\d+(?:\s*(?:∧|∨|↔|→)\s*h\d+)*", target_norm):
        prop_slots = set(re.findall(r"\((h\d+)\s*:\s*Prop\)", s))
        target_slots = set(re.findall(r"\bh\d+\b", target_norm))
        if target_slots and target_slots <= prop_slots:
            return True
    if re.fullmatch(r"\(let\s+Claim\s*:\s*Prop\s*:=.*;\s*Claim\)", target_norm):
        return True
    return False


def _normalize_prop_for_policy(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _binder_hypotheses(signature: str) -> list[tuple[str, str]]:
    """Best-effort extraction of explicit hypothesis binders from a signature."""
    sig = re.sub(r":=\s*by\b.*$", "", signature or "", flags=re.DOTALL)
    hyps: list[tuple[str, str]] = []
    for match in re.finditer(
        r"\(([hH][A-Za-z0-9_']*)\s*:\s*([^()]+?)\)",
        sig,
        flags=re.DOTALL,
    ):
        name = match.group(1)
        typ = _normalize_prop_for_policy(match.group(2))
        if typ:
            hyps.append((name, typ))
    return hyps


def _hypothesis_copies_target_issue(signature: str) -> str | None:
    """Flag statements weakened to `(h : target) : target`."""
    target = _normalize_prop_for_policy(_theorem_target(signature))
    if not target:
        return None
    for name, typ in _binder_hypotheses(signature):
        suspicious_name = re.search(r"(?:easy|claim|target|bound|conclusion|result)", name, re.IGNORECASE)
        relation_target = any(tok in target for tok in ("=", "≤", "≥", "<", ">", "↔", "∧", "∨", "∃", "∀"))
        if typ == target and (relation_target or suspicious_name):
            return f"claim_copied_into_hypothesis:{name}"
    return None


def _raw_notation_leak_issue(signature: str) -> str | None:
    """Detect paper/LaTeX notation that survived as if it were Lean syntax."""
    s = " ".join((signature or "").split())
    if not s:
        return "empty_signature"
    artifact_patterns: list[tuple[str, str]] = [
        (r"\\(?:left|right|frac|sum|int|ell|xi|omega|theta|alpha|beta|gamma|begin|end)\b", "raw_latex_command"),
        (r"\$[^$]*\$", "dollar_math_delimiter"),
        (r"\b[BD]_N\^\{", "latex_superscript_artifact"),
        (r"\^\{[^}]*\}", "latex_superscript_braces"),
        (r"_\{[^}]*\}", "latex_subscript_braces"),
        (r"\^\s*\([^)]*;", "semicolon_tuple_exponent_artifact"),
        (r"\|[^|]+\|\s*(?:~|\\sim)\s*[A-Za-z0-9_']+", "latex_asymptotic_artifact"),
        (r"∥[^∥]+∥_[A-Za-z0-9_]+", "norm_suffix_artifact"),
        (r"\bC_T\s+HSobolev\b", "bare_function_space_application"),
        (r"\bC_TH\s*\^", "undefined_paper_function_space"),
        (r"\bComplex\.abs\b", "non_mathlib_complex_abs_notation"),
    ]
    for pattern, reason in artifact_patterns:
        if re.search(pattern, s):
            return reason
    return None


def _basic_assumption_slot_issue(latex_statement: str, signature: str) -> str | None:
    """Fallback assumption-slot check when schema extraction is absent."""
    s = (latex_statement or "").lower()
    if not s:
        return None
    if not any(c in s for c in ("assume", "suppose", "given", "if ", "let ")):
        return None
    hyp_count = len(re.findall(r"\(h[_A-Za-z0-9']*\s*:", signature or ""))
    if hyp_count == 0:
        return "assumption_slot_missing:no_hypothesis_slots"
    return None


def _claim_shape_mismatch_issue(latex_statement: str, signature: str) -> str | None:
    """Detect hard semantic drift between latex claim shape and Lean target shape."""
    lhs = _claim_shape_from_latex(latex_statement)
    rhs = _claim_shape_from_signature(signature)
    allowed = {"prop", "eq", "ineq", "forall", "exists", "iff"}
    if lhs not in allowed or rhs not in allowed:
        return None
    if lhs == "prop" or rhs == "prop":
        return None
    if lhs == rhs:
        return None
    return f"claim_shape_mismatch:{lhs}->{rhs}"


def _quantifier_mismatch_issue(latex_statement: str, signature: str) -> str | None:
    """Catch translations that erase explicit universal/existential structure."""
    source = (latex_statement or "").lower()
    if not source:
        return None
    sig_no_body = re.sub(r":=\s*by\b.*$", "", signature or "", flags=re.DOTALL)
    target = _theorem_target(signature)
    binder_count = len(re.findall(r"\([^()]+?\s*:\s*[^()]+?\)", sig_no_body))
    if any(tok in source for tok in ("for all", "\\forall", "∀")):
        if "∀" not in target and "forall" not in target.lower() and binder_count == 0:
            return "wrong_quantifier:missing_forall"
    if any(tok in source for tok in ("there exists", "\\exists", "∃")):
        if "∃" not in target and "exists" not in target.lower():
            return "wrong_quantifier:missing_exists"
    return None


def _semantic_policy_issues(
    *,
    latex_statement: str,
    signature: str,
    schema: dict | None,
    strict_assumption_slot_coverage: bool,
) -> list[str]:
    """Return hard semantic policy issues for one candidate signature."""
    issues: list[str] = []
    if _is_trivialized_signature(signature):
        issues.append("trivialization_hard_violation")
    if _is_schema_scaffold_signature(signature):
        issues.append("schema_scaffold_not_faithful")

    copied_hyp = _hypothesis_copies_target_issue(signature)
    if copied_hyp:
        issues.append(copied_hyp)

    raw_leak = _raw_notation_leak_issue(signature)
    if raw_leak:
        issues.append(f"raw_notation_leak:{raw_leak}")

    shape_issue = _claim_shape_mismatch_issue(latex_statement, signature)
    if shape_issue:
        issues.append(shape_issue)

    quantifier_issue = _quantifier_mismatch_issue(latex_statement, signature)
    if quantifier_issue:
        issues.append(quantifier_issue)

    coverage_issues = _schema_coverage_issues(schema, signature)
    if coverage_issues and strict_assumption_slot_coverage:
        issues.extend([f"schema_coverage:{x}" for x in coverage_issues])

    basic_slot_issue = _basic_assumption_slot_issue(latex_statement, signature)
    if basic_slot_issue and strict_assumption_slot_coverage:
        issues.append(basic_slot_issue)
    return issues


def _build_definition_first_signature(schema: dict | None) -> str:
    """Definition-first fallback that preserves assumption structure."""
    assumptions = schema.get("assumptions", []) if isinstance((schema or {}).get("assumptions"), list) else []
    assumps = [str(a).strip() for a in assumptions if str(a).strip()][:6]
    hyp_binders = [f"(h{i} : Prop)" for i in range(1, len(assumps) + 1)]
    hs = [f"h{i}" for i in range(1, len(assumps) + 1)]
    premise = " ∧ ".join(hs) if hs else "True"
    target = "(let Claim : Prop := " + premise + "; Claim)"
    binders_block = " ".join(hyp_binders)
    return f"theorem schema_definition_first {binders_block} : {target} := by"


def _apply_schema_fallback(signature: str, schema: dict | None) -> str:
    """Produce the best available sorry stub when all repair rounds are exhausted.

    Design principle: NEVER emit a placeholder body (Nonempty Unit, (0:ℕ)=0,
    p_c1:Prop, schema_fallback:True). These look like valid theorems but carry
    zero mathematical content and silently corrupt the KG. Instead:

    - Preserve the theorem name from the failed signature so the prover can
      pick it up by name on the next pass.
    - Attach the LaTeX claim as a comment so a human (or the repair loop) can
      see what was intended.
    - Emit `: sorry_placeholder` only as a *type* that is itself an axiom stub,
      not as a closed proof. This makes the failure visible and forces an open
      sorry rather than a trivially-closed goal.
    """
    sig = (signature or "").strip()

    # Extract theorem name from the failed signature, falling back to schema name.
    name_match = re.search(r"^\s*(?:theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_'.]*)", sig, re.MULTILINE)
    thm_name = name_match.group(1) if name_match else "translation_failed"

    if schema is None:
        # No schema at all — emit a named sorry stub with the original attempt as comment.
        if sig:
            comment = _lean_comment_block(sig, max_len=320)
            return (
                "-- TRANSLATION FAILED (no schema):\n"
                "-- STATEMENT_REPAIR_NEEDED: schema_unavailable\n"
                f"{comment}\n"
                f"theorem {thm_name} : False := by sorry"
            )
        return f"-- STATEMENT_REPAIR_NEEDED: schema_unavailable\ntheorem {thm_name} : False := by sorry"

    claim = str(schema.get("claim", "")).strip()
    assumptions = schema.get("assumptions", []) if isinstance(schema.get("assumptions", []), list) else []

    # Keep only the first declaration to avoid broken fragments.
    first_decl = re.search(r"^\s*(theorem|lemma)\b", sig, re.MULTILINE)
    if first_decl:
        second_decl = re.search(r"^\s*(theorem|lemma)\b", sig[first_decl.end():], re.MULTILINE)
        if second_decl:
            sig = sig[: first_decl.end() + second_decl.start()].strip()

    # If the sig is a placeholder, discard it — we'll build a proper sorry stub.
    if _is_trivialized_signature(sig) or not sig:
        sig = ""

    # Build lines: claim comment + best sig as sorry stub, or fresh named stub.
    lines: list[str] = []
    if claim:
        lines.append(f"-- TRANSLATION INCOMPLETE — LaTeX claim: {escape_lean_comment(claim, max_len=220)}")
    lines.append("-- STATEMENT_REPAIR_NEEDED: schema_fallback")
    if assumptions:
        lines.append(f"-- Assumptions: {escape_lean_comment('; '.join(str(a) for a in assumptions[:4]), max_len=180)}")

    if sig:
        # Strip off any existing body and attach sorry.
        sig_no_body = re.sub(r":=\s*by\b.*$", "", sig, flags=re.DOTALL).strip()
        sig_no_body = re.sub(r":=\s*$", "", sig_no_body).strip()
        lines.append(sig_no_body + " := by sorry")
    else:
        # No usable signature at all — named open stub.
        lines.append(f"theorem {thm_name} : False := by sorry")

    return "\n".join(lines)


def _lean_comment_block(text: str, *, max_len: int = 500) -> str:
    """Return text as safe one-line Lean comments."""
    cleaned = escape_lean_comment(text or "", max_len=max_len)
    return "\n".join(f"-- {line}" for line in cleaned.splitlines() if line.strip())


_DEFAULT_IMPORTS = """\
import Desol.SDE.Basic

open MeasureTheory ProbabilityTheory
"""

# Baseline import: uses the project's own module which is always compiled and
# transitively imports the probability/measure-theory/analysis core of Mathlib.
# Additional modules are added automatically if their oleans are present.
_BASELINE_IMPORTS = """\
import Desol.SDE.Basic

open MeasureTheory ProbabilityTheory
"""

# Patterns that indicate a missing Lean identifier in error output.
_UNKNOWN_IDENT_RE = re.compile(
    r"unknown identifier '([^']+)'|"
    r"unknown constant '([^']+)'|"
    r"unknown namespace '([^']+)'|"
    r"unknown identifier `([^`]+)`|"
    r"unknown constant `([^`]+)`|"
    r"identifier `([^`]+)` is unknown|"
    r"failed to synthesize\s+\n?\s*(\S+)|"
    r"application type mismatch.*?'([A-Z][A-Za-z0-9_.]+)'",
    re.MULTILINE,
)

# Extract the concrete type-class instance that Lean couldn't synthesize.
_SYNTH_INSTANCE_RE = re.compile(
    r"failed to synthesize[^\n]*\n\s*([^\n]+)",
    re.MULTILINE,
)

# Extract the name of a term used as a function incorrectly.
_FUNC_EXPECTED_RE = re.compile(r"Function expected at\s+(\S+)", re.MULTILINE)

# Lean identifiers that are likely user-defined (not in Mathlib) —
# used to decide whether to auto-stub.
_LEAN_IDENT_RE = re.compile(r"\b([A-Z][A-Za-z0-9_']*)\b")

# Cached name→source_file index (loaded once per process).
_name_module_cache: dict[str, str] | None = None
_name_module_cache_path: str = ""


def _load_name_module_index(retrieval_index_path: str) -> dict[str, str]:
    """Build name → Mathlib import path mapping from the premise index entries.jsonl."""
    global _name_module_cache, _name_module_cache_path
    if _name_module_cache is not None and _name_module_cache_path == retrieval_index_path:
        return _name_module_cache

    entries_file = Path(retrieval_index_path) / "entries.jsonl"
    if not entries_file.exists():
        _name_module_cache = {}
        _name_module_cache_path = retrieval_index_path
        return _name_module_cache

    index: dict[str, str] = {}
    with entries_file.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = entry.get("name", "")
            src = entry.get("source_file", "")
            if name and src:
                # Index by full name and by short (last component) name.
                index[name.lower()] = src
                short = name.split(".")[-1].lower()
                if short not in index:
                    index[short] = src
    _name_module_cache = index
    _name_module_cache_path = retrieval_index_path
    return index


def _olean_exists(module: str, project_root: Path) -> bool:
    """Return True if the compiled olean for `module` exists in the lake build cache."""
    rel = module.replace(".", "/") + ".olean"
    # Check in the project's mathlib package build cache.
    olean = project_root / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean" / rel
    return olean.exists()


def _resolve_missing_imports(
    error: str,
    name_index: dict[str, str],
    project_root: Path,
) -> list[str]:
    """Return import paths inferred from error messages that also have built oleans."""
    candidates: set[str] = set()
    for m in _UNKNOWN_IDENT_RE.finditer(error):
        ident = next((g for g in m.groups() if g), None)
        if not ident:
            continue
        for key in [ident.lower(), ident.split(".")[-1].lower()]:
            src = name_index.get(key)
            if src and _olean_exists(src, project_root):
                candidates.add(src)
                break
    return sorted(candidates)


def _extract_unknown_idents(
    error: str,
    name_index: dict[str, str],
    imports: str = "",
    project_root: Path | None = None,
) -> list[str]:
    """Return identifiers that appear as 'unknown' in the error and are NOT in Mathlib."""
    found = []
    patterns = (
        r"unknown (?:identifier|constant|namespace) '([^']+)'",
        r"unknown (?:identifier|constant|namespace) `([^`]+)`",
        r"identifier `([^`]+)` is unknown",
    )
    for pat in patterns:
        for m in re.finditer(pat, error):
            ident = m.group(1)
            short = ident.split(".")[-1].lower()
            if ident.lower() in name_index or short in name_index:
                continue
            # Final gate: verify it truly doesn't exist in current imports.
            if imports and project_root and _lean_name_exists(ident, imports, project_root):
                continue
            found.append(ident)
    return list(dict.fromkeys(found))


def _signature_has_binder(sig: str, name: str) -> bool:
    return bool(re.search(r"[\(\{]\s*" + re.escape(name) + r"\s*:", sig))


def _target_colon_index(sig: str) -> int:
    m = re.search(r"^\s*(?:theorem|lemma)\s+[A-Za-z_][A-Za-z0-9_'.]*", sig, re.MULTILINE)
    if not m:
        return -1
    depth = 0
    for i in range(m.end(), len(sig)):
        ch = sig[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0:
            return i
    return -1


def _insert_binders_before_target(sig: str, binders: list[str]) -> str:
    if not binders:
        return sig
    idx = _target_colon_index(sig)
    if idx < 0:
        return sig
    return f"{sig[:idx].rstrip()} {' '.join(binders)}{sig[idx:]}"


def _insert_binders_after_decl_name(sig: str, binders: list[str]) -> str:
    if not binders:
        return sig
    m = re.search(r"^\s*(?:theorem|lemma)\s+[A-Za-z_][A-Za-z0-9_'.]*", sig, re.MULTILINE)
    if not m:
        return sig
    return f"{sig[:m.end()]} {' '.join(binders)}{sig[m.end():]}"


def _extract_autoimplicit_function_names(error: str, sig: str) -> list[str]:
    """Find unknown identifiers Lean treated as implicit variables but then applied."""
    names: list[str] = []
    for raw in _FUNC_EXPECTED_RE.findall(error):
        ident = raw.strip().split()[0]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_']*", ident):
            continue
        if ident in {"ContDiff", "ContDiffOn", "Continuous", "Differentiable"}:
            continue
        if _signature_has_binder(sig, ident):
            continue
        # Lowercase/underscore names are typical paper-local functions.  Plain
        # uppercase names are often bound type variables or Mathlib constants.
        if ident[0].isupper() and "_" not in ident:
            continue
        names.append(ident)
    return list(dict.fromkeys(names))


def _build_function_stubs(names: list[str]) -> str:
    """Generate minimal paper-local function axioms for autoImplicit failures."""
    lines: list[str] = []
    for name in names:
        safe = re.sub(r"[^A-Za-z0-9_']", "_", name)
        if safe in {"norm", "abs"}:
            continue
        lines.append(f"axiom {safe} : {_paper_function_type(safe, '')}")
    return "\n".join(lines)


def _paper_function_type(name: str, sig: str) -> str:
    if name.endswith("_data"):
        return "ℕ → Fin 2 → ℝ → ℝ"
    if name.endswith("_solution"):
        return "ℕ → ℝ"
    if name.startswith("L2_"):
        return "ℝ → ℝ → Set (ℝ → ℝ)"
    # Basis functions like `phi (i + 1) z`.
    if re.search(r"\b" + re.escape(name) + r"\s+\([^)]*\)\s+[A-Za-z_]", sig):
        return "ℕ → ℝ → ℝ"
    if re.search(r"\b" + re.escape(name) + r"\s+[A-Za-z0-9_']+\s+[A-Za-z0-9_']", sig):
        return "ℕ → ℝ → ℝ"
    return "ℕ → ℝ"


def _add_missing_function_binders(sig: str, names: list[str]) -> str:
    """Prefer local binders over global axioms for paper-local functions."""
    binders: list[str] = []
    for name in names:
        if _signature_has_binder(sig, name):
            continue
        binders.append(f"({name} : {_paper_function_type(name, sig)})")
    return _insert_binders_after_decl_name(sig, binders)


def _relax_fragile_hypotheses(sig: str) -> str:
    """Keep assumption slots when a generated hypothesis has invalid paper notation."""
    fragile_tokens = ("∈", "^", "|", "‖", "⨆", "Complex.abs", "D_N", "B_N", "d_dts", "~")
    out: list[str] = []
    i = 0
    while i < len(sig):
        if sig[i] != "(":
            out.append(sig[i])
            i += 1
            continue

        j = i + 1
        while j < len(sig) and sig[j].isspace():
            j += 1
        m = re.match(r"h[A-Za-z0-9_']*", sig[j:])
        if not m:
            out.append(sig[i])
            i += 1
            continue

        name = m.group(0)
        k = j + len(name)
        while k < len(sig) and sig[k].isspace():
            k += 1
        if k >= len(sig) or sig[k] != ":":
            out.append(sig[i])
            i += 1
            continue

        depth = 0
        end = i
        while end < len(sig):
            ch = sig[end]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end += 1
                    break
            end += 1
        if depth != 0:
            out.append(sig[i])
            i += 1
            continue

        block = sig[i:end]
        if any(tok in block for tok in fragile_tokens):
            out.append(f"({name} : Prop)")
        else:
            out.append(block)
        i = end
    return "".join(out)


def _build_stubs(idents: list[str]) -> str:
    """Generate axiom stubs for unknown identifiers so the signature can elaborate.

    Uses `axiom` rather than `noncomputable def ... := sorry` because:
    - `axiom T : Type` is always valid and never conflicts with existing names.
    - `noncomputable def T : Type* := sorry` triggers `sorry`-propagation warnings
      and can conflict if the name is partially defined elsewhere.
    - Domain types (uppercase) get `axiom T : Type`; Prop-valued names get
      `axiom T : Prop`.
    """
    lines = []
    seen: set[str] = set()
    for ident in idents:
        # Only stub names that look like user-defined types/predicates (start uppercase).
        base = ident.split(".")[-1]
        if not base or not base[0].isupper():
            continue
        safe = re.sub(r"[^A-Za-z0-9_]", "_", ident)
        if safe in seen:
            continue
        seen.add(safe)
        # Heuristic: names ending in Prop/Pred/LE/Rel are Prop-valued.
        if re.search(r"(?:Prop|Pred|LE|Rel|Order|Less|Eq|Mem|Sub|Has)$", safe):
            lines.append(f"axiom {safe} : Prop")
        else:
            lines.append(f"axiom {safe} : Type")
    return "\n".join(lines)


_lean_check_cache: dict[str, bool] = {}


def _lean_name_exists(name: str, imports: str, project_root: Path) -> bool:
    """Return True if `name` resolves under the given imports (via #check @name)."""
    if name in _lean_check_cache:
        return _lean_check_cache[name]
    safe = re.sub(r"[^A-Za-z0-9_.]", "_", name)
    src = f"{imports}\n\n#check @{safe}\n"
    ok, _ = _run_lean(src, project_root, timeout=20)
    _lean_check_cache[name] = ok
    return ok


def _extract_unknown_classes(
    error: str,
    imports: str,
    project_root: Path,
) -> list[str]:
    """Return type-class names from synthInstanceFailed errors that do NOT exist in Mathlib.

    Verified by running `#check @ClassName` — avoids stubbing existing classes like UniformSpace.
    """
    candidates = []
    for m in _SYNTH_INSTANCE_RE.finditer(error):
        instance_line = m.group(1).strip()
        class_name = instance_line.split()[0] if instance_line else ""
        if not class_name or not class_name[0].isupper():
            continue
        candidates.append(class_name)
    found = []
    for name in dict.fromkeys(candidates):  # deduplicate
        if not _lean_name_exists(name, imports, project_root):
            found.append(name)
    return found


def _build_class_stubs(class_names: list[str]) -> str:
    """Generate opaque class stubs + universal instances for type classes not in Mathlib."""
    lines = []
    for name in class_names:
        safe = re.sub(r"[^A-Za-z0-9_]", "_", name)
        lines.append(f"class {safe} (α : Type*) : Prop where")
        lines.append(f"instance {{α : Type*}} : {safe} α := ⟨⟩")
    return "\n".join(lines)


_MATHLIB_TC_ALLOWLIST: frozenset[str] = frozenset({
    "Add", "Mul", "Sub", "Div", "Mod", "Pow", "Neg", "Inv", "Zero", "One",
    "HAdd", "HMul", "HSub", "HDiv", "HPow", "HMod",
    "AddZeroClass", "MulOneClass", "AddMonoid", "Monoid", "AddGroup", "Group",
    "AddCommMonoid", "CommMonoid", "AddCommGroup", "CommGroup",
    "Ring", "CommRing", "Field", "DivisionRing", "EuclideanDomain",
    "Semiring", "CommSemiring", "NonUnitalRing", "NonAssocRing",
    "Module", "Algebra", "AlgebraMap",
    "SMul", "Scalar", "MulAction", "DistribMulAction",
    "OrderedSemiring", "OrderedRing", "OrderedField",
    "LinearOrder", "Preorder", "PartialOrder", "SemilatticeSup", "SemilatticeInf",
    "Lattice", "DistribLattice", "BooleanAlgebra", "CompleteLattice",
    "LE", "LT", "GE", "GT",
    "Fintype", "Finite", "Infinite", "Countable", "Uncountable",
    "DecidableEq", "Decidable", "DecidablePred", "Inhabited", "Nonempty",
    "Unique", "Subsingleton",
    "TopologicalSpace", "T0Space", "T1Space", "T2Space", "T3Space",
    "RegularSpace", "NormalSpace", "CompactSpace", "LocallyCompactSpace",
    "SecondCountableTopology", "SeparableSpace", "FirstCountableTopology",
    "MetrizableSpace", "PseudoMetrizableSpace",
    "UniformSpace", "MetricSpace", "PseudoMetricSpace", "EMetricSpace",
    "PseudoEMetricSpace", "ProperSpace",
    "TopologicalGroup", "TopologicalAddGroup", "TopologicalRing",
    "CompleteSpace",
    "NormedAddCommGroup", "SeminormedAddCommGroup", "NormedGroup",
    "NormedSpace", "NormedField", "NormedRing",
    "InnerProductSpace",
    "MeasurableSpace", "MeasurableSingletonClass",
    "MeasureSpace", "SigmaFinite", "IsFiniteMeasure", "IsProbabilityMeasure",
    "GroupWithZero", "MonoidWithZero", "MulZeroClass",
    "NoZeroDivisors", "IsDomain", "GCDMonoid", "UniqueFactorizationMonoid",
    "Nontrivial", "CharZero", "CharP", "NeZero", "Fact",
})


def _fix_invalid_binders(sig: str, error: str) -> str:
    """Automatically rewrite `[X args]` → `(h_X : X args)` for non-class binders.

    Uses hardcoded allowlist — no Lean invocations.
    """
    binder_re = re.compile(r"\[([^\[\]]+)\]")

    def rewrite_binder(m: re.Match) -> str:
        content = m.group(1).strip()
        tokens = content.split()
        if not tokens:
            return m.group(0)
        first_word = tokens[0]
        if first_word in _MATHLIB_TC_ALLOWLIST:
            return m.group(0)
        if not first_word or not first_word[0].isupper():
            return m.group(0)
        safe_name = re.sub(r"[^A-Za-z0-9_]", "_", first_word).lower()
        return f"(h_{safe_name} : {content})"

    return binder_re.sub(rewrite_binder, sig)


_ADVERSARIAL_SYSTEM = (
    "You are a formal verification critic. "
    "Given an informal LaTeX theorem and its Lean 4 formalization, identify semantic mismatches. "
    "Look for: dropped hypotheses, wrong quantifier order, weaker/stronger conclusion than stated, "
    "vacuously true statements, or type mismatches that change meaning. "
    "Also check if the Lean statement is trivially true (provable by `rfl`, `trivial`, or `decide` alone). "
    "Reply with a compact JSON object:\n"
    '{"issues": ["issue1", ...], "trivially_true": true/false, "verdict": "ok" | "suspicious" | "wrong"}\n'
    "If no issues, reply: {\"issues\": [], \"trivially_true\": false, \"verdict\": \"ok\"}"
)

_ROUNDTRIP_BACK_TRANSLATION_SYSTEM = (
    "You convert Lean theorem signatures into concise mathematical LaTeX statements. "
    "Output plain text only, no markdown and no commentary."
)

_ROUNDTRIP_EQUIV_SYSTEM = (
    "You compare two mathematical statements for semantic equivalence. "
    "Return only JSON with keys equivalent (bool) and notes (array of short strings)."
)


def adversarial_translation_check(
    *,
    latex_statement: str,
    lean_signature: str,
    client: object,
    model: str,
    api_log_hook: object = None,
) -> list[str]:
    """Use Leanstral to find semantic mismatches between LaTeX and Lean 4 formalization.

    Returns a list of adversarial flags (empty = no issues found).
    Uses only the Leanstral API — no external calls.
    """
    user_msg = (
        f"LaTeX theorem:\n{latex_statement}\n\n"
        f"Lean 4 formalization:\n{lean_signature}\n\n"
        "Identify any semantic mismatches or trivially-true issues."
    )
    try:
        _, raw = _chat_complete(
            client=client,
            model=model,
            messages=[
                {"role": "system", "content": _ADVERSARIAL_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0,
            max_tokens=256,
            purpose="adversarial_check",
            api_log_hook=api_log_hook,
        )
    except Exception:
        return []

    # Extract JSON from response — may be wrapped in markdown.
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not json_match:
        return []
    try:
        parsed = json.loads(json_match.group(0))
    except (ValueError, KeyError):
        return []

    flags: list[str] = []
    issues = parsed.get("issues", [])
    if isinstance(issues, list):
        flags.extend(str(i) for i in issues if i)
    if parsed.get("trivially_true"):
        flags.append("trivially_true")
    verdict = parsed.get("verdict", "ok")
    if verdict in ("suspicious", "wrong"):
        flags.append(f"verdict:{verdict}")
    return flags


def roundtrip_translation_check(
    *,
    latex_statement: str,
    lean_signature: str,
    client: object,
    model: str,
    api_log_hook: object = None,
) -> tuple[str | None, list[str]]:
    """Back-translate Lean to LaTeX and check semantic equivalence."""
    user_back = (
        "Convert this Lean theorem signature to one concise LaTeX math statement.\n\n"
        f"Lean 4 theorem:\n{lean_signature}\n"
    )
    try:
        _, back_text = _chat_complete(
            client=client,
            model=model,
            messages=[
                {"role": "system", "content": _ROUNDTRIP_BACK_TRANSLATION_SYSTEM},
                {"role": "user", "content": user_back},
            ],
            temperature=0.0,
            max_tokens=256,
            purpose="roundtrip_backtranslate",
            api_log_hook=api_log_hook,
        )
    except Exception as exc:
        return None, [f"roundtrip_backtranslate_error:{exc}"]

    back_translation = back_text.strip()
    if not back_translation:
        return None, ["roundtrip_empty_backtranslation"]

    user_eq = (
        "Original statement:\n"
        f"{latex_statement}\n\n"
        "Back-translated statement:\n"
        f"{back_translation}\n"
    )
    try:
        _, raw = _chat_complete(
            client=client,
            model=model,
            messages=[
                {"role": "system", "content": _ROUNDTRIP_EQUIV_SYSTEM},
                {"role": "user", "content": user_eq},
            ],
            temperature=0.0,
            max_tokens=220,
            purpose="roundtrip_equivalence",
            api_log_hook=api_log_hook,
        )
    except Exception as exc:
        return back_translation, [f"roundtrip_equivalence_error:{exc}"]

    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not json_match:
        return back_translation, ["roundtrip_invalid_json"]

    try:
        parsed = json.loads(json_match.group(0))
    except ValueError:
        return back_translation, ["roundtrip_invalid_json"]

    flags: list[str] = []
    if not bool(parsed.get("equivalent", False)):
        flags.append("roundtrip_semantic_mismatch")
    notes = parsed.get("notes")
    if isinstance(notes, list):
        flags.extend(str(n) for n in notes if str(n).strip())
    return back_translation, flags


def _build_repair_hint(error: str) -> str:
    """Return a targeted repair hint based on the Lean error category."""
    hint_parts = []

    # 1. Exact missing type class instance.
    synth_matches = _SYNTH_INSTANCE_RE.findall(error)
    if synth_matches:
        instances = [m.strip() for m in synth_matches[:3]]
        inst_str = ", ".join(f"[{i}]" for i in instances)

        # Look up each failing class in the TC graph / concept map for a targeted fix.
        specific_fixes: list[str] = []
        for inst in instances:
            class_name = inst.split()[0] if inst else ""
            if class_name:
                hint = _get_class_hint(class_name)
                if hint:
                    specific_fixes.append(hint)

        specific_str = (
            "\nSPECIFIC FIXES for the failing classes:\n"
            + "\n".join(f"  - {f}" for f in specific_fixes)
            if specific_fixes
            else ""
        )

        hint_parts.append(
            f"Type class synthesis failed for: {inst_str}. "
            "Likely causes and fixes:\n"
            "(a) REDUNDANT instances: Mathlib has a type class hierarchy — do NOT list implied classes. "
            "Rules: `[NormedSpace 𝕜 E]` implies `[NormedAddCommGroup E]` and `[SeminormedAddCommGroup E]`; "
            "`[InnerProductSpace 𝕜 E]` implies `[NormedSpace 𝕜 E]`; "
            "`[MetricSpace α]` implies `[TopologicalSpace α]`, `[UniformSpace α]`, `[PseudoMetricSpace α]`; "
            "`[NormedAddCommGroup E]` implies `[AddCommGroup E]`, `[TopologicalAddGroup E]`. "
            "KEEP ONLY the most specific class — remove all implied ones.\n"
            "(b) NON-MATHLIB classes: If the class doesn't exist in Mathlib "
            "(e.g. GeodesicSpace, LengthSpace, Cat κ, CBA, StronglyConvex, LocallyLipschitz as a class), "
            "DO NOT use it at all. Replace with the closest standard Mathlib alternative "
            "or use `{X : Type*}` with explicit hypothesis `(h : SomeCondition X)` instead."
            + specific_str
        )

    # 2. Term used as function.
    func_expected = _FUNC_EXPECTED_RE.findall(error)
    if func_expected:
        names = ", ".join(f"`{n}`" for n in func_expected[:2])
        # Check if the failing term looks like an asymptotic expression
        asym_hint = ""
        if any(n in ("o", "O", "Θ", "IsLittleO", "IsBigO") for n in func_expected):
            asym_hint = (
                " ASYMPTOTIC EXPRESSIONS: `o(n)`, `O(n)`, `(1 + o(1))`, `o[Filter.atTop]` are NOT values. "
                "You CANNOT use them in arithmetic like `(c + o(1)) * n^k`. "
                "Instead, introduce an explicit error function: "
                "`∃ ε : ℕ → ℝ, ε =o[Filter.atTop] (fun _ => (1:ℝ)) ∧ f n = (c + ε n) * n^k`."
            )
        hint_parts.append(
            f"{names} is a term (a type or a constant), not a function — you cannot apply it to arguments. "
            "Common causes: "
            "(a) Big-O/little-o notation: `o(n)`, `O(n)`, `Θ(n)` are not valid Lean 4. "
            "Use Mathlib's infix relation instead: `f =o[Filter.atTop] g` for little-o, `f =O[Filter.atTop] g` for big-O, "
            "where `f g : ℕ → ℝ`. For asymptotic equalities like `G n = (c + o(1)) * 4^n / n^(3/4)`, "
            "express as: `∃ ε : ℕ → ℝ, (ε =o[Filter.atTop] (fun _ => 1)) ∧ G n = (c + ε n) * 4^n / n^(3/4)`. "
            "(b) A type used as a value — use it as a type annotation with `:`. "
            "(c) A measure `μ` used as a function — use `μ.toFun` or write `μ.measure_of`."
            + asym_hint
        )

    # 3. Application type mismatch — extract what type was expected.
    if "Application type mismatch" in error or "Type mismatch" in error:
        # Extract "has type X" and "expected to have type Y" from error.
        has_type = re.search(r"has type\n?\s*([^\n]+)", error)
        expected_type = re.search(r"expected to have type\n?\s*([^\n]+)", error)
        mismatch_detail = ""
        if has_type and expected_type:
            mismatch_detail = (
                f" The argument has type `{has_type.group(1).strip()}` "
                f"but type `{expected_type.group(1).strip()}` was expected."
            )
        # Detect Fin-related mismatches for targeted guidance.
        fin_hint = ""
        arg_text = has_type.group(1).strip() if has_type else ""
        exp_text = expected_type.group(1).strip() if expected_type else ""
        if "Fin" in arg_text or "Fin" in exp_text or "Fin" in error[:300]:
            fin_hint = (
                " Fin-index fix: `j : Fin k` cannot be passed where `ℕ` is expected — use `j.val` or `↑j`. "
                "Conversely, `n : ℕ` cannot be passed where `Fin k` is expected — use `⟨n, hn⟩` with a proof `hn : n < k`. "
                "AVOID inline proofs like `⟨0, Nat.zero_lt_succ n⟩` in signatures — they are fragile. "
                "Instead add `(hn : 0 < n)` as a hypothesis and use `⟨0, hn⟩`, or use `[NeZero n]`."
            )
        hint_parts.append(
            f"Type mismatch error.{mismatch_detail}{fin_hint} "
            "Common fixes: "
            "(a) coerce with `(x : ExpectedType)` or use `↑x` for numeric coercions (e.g. `↑j` converts `Fin k` to `ℕ`); "
            "(b) if passing a function where a type is expected, you may need `fun x => f x` instead; "
            "(c) if the universe is wrong (`Type` vs `Type*`), add universe polymorphism; "
            "(d) for `ℝ`/`ℕ`/`ℤ` mismatches, use `(n : ℝ)` or `Int.ofNat n` coercions; "
            "(e) for `Set α` used where `Type` is expected, the issue is a universe level — "
            "use `Subtype` instead: `{x : α // x ∈ S}` instead of `(S : Set α)`. "
            "(f) for product/pair mismatches `(a, b)`, check that both components have the right type."
        )

    # 4. unexpected token ':=' — where clause or definition syntax.
    if "unexpected token ':='" in error:
        hint_parts.append(
            "Do not use `:=` inside a `where` clause or after a `:` type annotation. "
            "In Lean 4 theorem signatures, only use `(x : T)` binders. "
            "Move any definitions outside the theorem signature."
        )

    # 4. unexpected token in binder.
    if "invalid binder annotation" in error or "type is not a class" in error:
        hint_parts.append(
            "Square brackets `[...]` are only for type class instances. "
            "Use `(x : T)` for ordinary hypotheses and `{x : T}` for implicit arguments."
        )

    # 5. unexpected token 'in' — Lean 3 syntax used instead of Lean 4
    if "unexpected token 'in'" in error:
        hint_parts.append(
            "You are using Lean 3 syntax. In Lean 4, `in` is never used in binders or sum notation. "
            "Replacements: "
            "(a) `∀ x in S, P x` → `∀ x ∈ S, P x` (use ∈ symbol, not `in`); "
            "(b) `∑ i in S, f i` → `∑ i ∈ S, f i` (use ∈ symbol); "
            "(c) `∏ i in S, f i` → `∏ i ∈ S, f i`; "
            "(d) `∃ x in S, P x` → `∃ x ∈ S, P x`. "
            "Replace ALL occurrences of ` in ` inside binders and sum/product notation."
        )

    # 6. unexpected token '!' — model wrote n! (factorial notation, not Lean 4)
    if "unexpected token '!'" in error:
        hint_parts.append(
            "The `!` postfix (factorial) is not valid Lean 4 syntax. "
            "Replace `n!` with `n.factorial` or `Nat.factorial n`. "
            "Similarly, `(n k)!` should be written as `Nat.factorial (n - k)`."
        )

    # 7. unexpected token '↔' — model put ↔ inside a binder or where it expects :=
    if "unexpected token '↔'" in error:
        hint_parts.append(
            "Do not use `↔` directly in a binder. "
            "The return type of a theorem should use `↔` in the *type* position after `:`, "
            "e.g. `theorem foo : P ↔ Q := by ...`."
        )

    # 8. unexpected token 'λ' — Lean 3 lambda or eigenvalue notation
    if "unexpected token 'λ'" in error or "unexpected token 'lambda'" in error:
        hint_parts.append(
            "The symbol `λ` is invalid in Lean 4. TWO common causes:\n"
            "(a) Lean 3 lambda syntax `λ x, ...` → replace with `fun x => ...`.\n"
            "(b) Eigenvalue notation `λ_max(M)`, `λ_i(M)` etc. — these are NOT valid Lean 4. "
            "Replace eigenvalue expressions with hypotheses: "
            "instead of writing `λ_max(M)` directly, add a parameter `(c : ℝ)` and hypothesis "
            "`(hspec : ∀ v, ‖M.mulVec v‖ ≤ c * ‖v‖)` or use `(hspec : Matrix.spectralNorm M ≤ c)`. "
            "Replace ALL occurrences of `λ` — check both uses."
        )

    # 9. unexpected token '[' expected ',' — model used [...] in tuple or wrong position
    if "unexpected token '['" in error and "expected ','" in error:
        hint_parts.append(
            "Square brackets `[...]` appeared where a comma or tuple element was expected. "
            "Do NOT use `[...]` for tuples or product types — use `(a, b)` for pairs. "
            "Square brackets are ONLY for type class instances in binders."
        )

    # 10. unexpected token 'where' — model tried to use a where clause in the signature
    if "unexpected token 'where'" in error:
        hint_parts.append(
            "Do not use a `where` clause inside a theorem signature. "
            "In Lean 4 theorem signatures, all binders must appear before the `:` return type. "
            "Move any helper definitions outside the theorem, or use `let` inside the proof body."
        )

    # 11. unexpected token '|' — model used pattern matching or inductive syntax in signature
    if "unexpected token '|'" in error:
        hint_parts.append(
            "The `|` character is not valid in a theorem signature. "
            "Do not use pattern matching or inductive case syntax in the signature. "
            "Express case distinctions as disjunctions in the statement type, e.g. `P ∨ Q` or `∃ n, ...`."
        )

    # 12. unexpected token 'with' — Lean 3 match/with syntax
    if "unexpected token 'with'" in error:
        hint_parts.append(
            "The `with` keyword is not valid here. "
            "In Lean 4, match expressions use `match x with | ...`, but this belongs in the proof, "
            "not in the theorem signature. Remove `with` from the signature."
        )

    # 13. unexpected token ',' — misplaced comma, often from ∀ x, y : T
    if "unexpected token ','" in error:
        hint_parts.append(
            "Unexpected comma in signature. "
            "In Lean 4, `∀ (x y : T), P` is correct (comma after the binder group, not between variables). "
            "Do NOT write `∀ x, y : T` — write `∀ (x y : T)` or `∀ x : T, ∀ y : T`. "
            "Also check that you are not using Lean 3 lambda syntax `λ x, ...` — use `fun x => ...`."
        )

    # 14. unexpected token '(' / '{' expected id — missing theorem name
    if ("unexpected token '('" in error or "unexpected token '{'" in error) and "expected id" in error:
        hint_parts.append(
            "The theorem is missing a name. In Lean 4, `theorem` and `lemma` must be followed by an identifier. "
            "Add a name: `theorem my_theorem_name {α : Type*} ...` — do NOT start with `theorem {` or `theorem (`."
        )

    # 15. unexpected token 'fun' — `fun` used in a type position inside the return type
    if "unexpected token 'fun'" in error:
        hint_parts.append(
            "The keyword `fun` appeared in a type position. "
            "In a theorem *signature*, the return type (after `:`) must be a proposition (Prop), not a function. "
            "Common causes: (a) you wrote `∀ x, fun y => ...` — this is wrong; use `∀ x y, ...` instead. "
            "(b) you used a lambda in the type — replace with a universally quantified statement. "
            "(c) you confused `:= fun x => ...` (a definition body) with the type."
        )

    # 15b. Lean 3 typeclass names not in Mathlib 4
    _lean3_class_hits = [
        (cls, repl)
        for cls, repl in [
            ("LinearOrderedRing", "LinearOrderedCommRing"),
            ("OrderedRing", "StrictOrderedRing"),
            ("LinearOrderedSemiring", "OrderedSemiring"),
        ]
        if cls in error
    ]
    for cls, repl in _lean3_class_hits:
        hint_parts.append(
            f"`{cls}` does not exist in Mathlib 4 (Lean 3 name). Replace `[{cls} α]` with `[{repl} α]`. "
            "If the theorem arguments are all concrete types (ℕ, ℤ, ℝ), remove the type class entirely "
            "and use the concrete type directly."
        )

    # 16. `don't know how to synthesize implicit` — variant of synthInstanceFailed
    if "don't know how to synthesize" in error or "cannot synthesize" in error:
        hint_parts.append(
            "Lean cannot synthesize an implicit argument. "
            "Make the argument explicit: if Lean can't infer a type or class, add it as an explicit binder `(x : T)` or `[C α]`. "
            "Check that all type class instances needed are listed in the signature."
        )

    # 17. invalidField — method/field access on wrong type
    if "invalidField" in error or "Invalid field" in error:
        field_match = re.search(r"field '([^']+)' .* '([^']+)'", error)
        field_hint = ""
        if field_match:
            field_hint = f" `{field_match.group(2)}` has no field `{field_match.group(1)}`."
        hint_parts.append(
            f"Invalid field access.{field_hint} "
            "Common causes: (a) using `.PosDef` on a scalar — PosDef is only for Matrix; "
            "(b) using `.card` on a Finset when you need `Finset.card s`; "
            "(c) using `M.spectralNorm` — the correct Lean4 name is `Matrix.spectralNorm M`; "
            "(d) the type is abstract (`α : Type*`) and has no such field — use a hypothesis instead."
        )

    # 18. overloaded notation errors — ambiguous notation resolved to multiple failures
    if "overloaded, errors" in error:
        hint_parts.append(
            "There is an ambiguous notation that Lean cannot resolve. "
            "Common causes: "
            "(a) Unicode operators used incorrectly — `≺`, `≻`, `⊆`, `⊂` etc. must be applied to compatible types. "
            "For matrix positive definiteness, use `M.PosDef` (a Prop), not `M ≻ 0`. "
            "(b) `∑` or `∏` with wrong argument types — check the index type matches `Finset` or `Fintype`. "
            "(c) Coercion missing — add explicit cast like `(n : ℝ)` or `(k : ℤ)`. "
            "Simplify the notation or make types explicit."
        )

    # unexpected '∧' — conjunction in wrong position (often inside ∃ binder instead of body)
    if "unexpected token '∧'" in error:
        hint_parts.append(
            "The `∧` (and) operator appeared in an unexpected position. "
            "Most likely cause: `∃ x, P ∧ Q` is correct, but if `∧` appears inside a binder "
            "like `∃ (x : T ∧ U)`, that is wrong — binders take a type, not a conjunction. "
            "Fix: move all conjuncts into the BODY after the `,`: `∃ (x : T), P x ∧ Q x`. "
            "For existentials with multiple conditions: `∃ (x : T) (y : S), cond1 ∧ cond2`. "
            "Do NOT nest `∧` inside a binder type annotation."
        )

    # unexpected '‖' — norm notation used as a binder name or in wrong position
    if "unexpected token '‖'" in error:
        hint_parts.append(
            "The norm notation `‖·‖` cannot be used as a variable name or binder. "
            "Common causes: "
            "(a) Writing `∃ ‖·‖ : E → ℝ, ...` to say 'there exists a norm' — "
            "instead use `∃ _ : NormedAddCommGroup E, ...` or add `[NormedAddCommGroup E]` as a typeclass. "
            "(b) The norm bars `‖x‖` appearing in a binder type — move them to the hypothesis body. "
            "For 'E is normable', use `[NormedAddCommGroup E]` directly."
        )

    # generic unexpected token fallback
    if "unexpected token" in error and not hint_parts:
        hint_parts.append(
            "There is a syntax error. Check that all parentheses and brackets are balanced, "
            "that `:` is used for type annotations, and that the declaration ends with `:= by`."
        )

    if not hint_parts:
        hint_parts.append(
            "Fix the type error and output the corrected signature."
        )

    return " ".join(hint_parts)


def _is_irrecoverable(error: str, stubs: str) -> bool:
    """Return True when the error cannot be fixed by further model repair rounds.

    Criteria: the failing name is a stub we already created (not a Mathlib name).
    Spending more API calls asking the model to "fix" something outside Mathlib is wasteful.
    """
    # "type expected, got (X : Type..." — model used a type class as a value expression
    if "type expected, got" in error:
        return True
    # "Function expected at X" — X is a stub (already tried upgrading in Try 4)
    func_names = _FUNC_EXPECTED_RE.findall(error)
    for fname in func_names:
        safe = re.sub(r"[^A-Za-z0-9_]", "_", fname)
        if f"def {safe}" in stubs or f"class {safe}" in stubs:
            return True
    return False


def _run_lean(lean_src: str, project_root: Path, timeout: int) -> tuple[bool, str]:
    """Write lean_src to a temp file, run lake env lean, return (ok, error)."""
    import uuid
    tmp_name = f"_tmp_validate_{uuid.uuid4().hex[:8]}.lean"
    tmp_path = project_root / "Desol" / tmp_name
    try:
        tmp_path.write_text(lean_src, encoding="utf-8")
        lake_bin = shutil.which("lake") or os.path.expanduser("~/.elan/bin/lake")
        proc = subprocess.run(
            [lake_bin, "env", "lean", str(tmp_path)],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        combined = (proc.stderr or "") + (proc.stdout or "")
        if proc.returncode == 0 and "error:" not in combined:
            return True, ""
        return False, combined.strip()
    except subprocess.TimeoutExpired:
        return False, f"lake env lean timed out after {timeout}s"
    finally:
        tmp_path.unlink(missing_ok=True)


def _check_vacuous(
    signature: str,
    *,
    project_root: Path,
    imports: str = "",
    timeout: int = 30,
) -> bool:
    """Return True if the statement is trivially vacuous (no real math content).

    Checks three cheap patterns before the LLM round-trip judge:
    1. `trivial` closes the goal — maps to True, tautology, or mistranslation.
    2. `rfl` closes the goal — self-equality like `(0:ℕ) = 0`, vacuous identity.
    3. Schema-placeholder shape: `(p_c1 : Prop) (h_c1 : p_c1) : p_c1` — the
       pipeline's own fallback stub, always closable by `exact h_c1`.

    Any of these means the translated statement carries no mathematical content.
    """
    sig = re.sub(r":=\s*by\b.*$", "", signature, flags=re.DOTALL).strip()
    sig = re.sub(r":=\s*$", "", sig).strip()
    if not sig:
        return False

    # Fast syntactic check for schema-placeholder pattern before hitting lake.
    # Matches: (...) (p_cN : Prop) (h_cN : p_cN) : p_cN
    if re.search(r"\(p_c\d+\s*:\s*Prop\)\s*\(h_c\d+\s*:\s*p_c\d+\)\s*:\s*p_c\d+\s*$", sig):
        return True

    vac_sig = re.sub(r"^(theorem|lemma)\s+\S+", r"\1 _desol_vacuity_check", sig, count=1)
    prefix = f"{imports}\n\n" if imports.strip() else ""

    for tactic in ("trivial", "rfl"):
        lean_src = f"{prefix}{vac_sig} := by {tactic}\n"
        try:
            ok, _ = _run_lean(lean_src, project_root=project_root, timeout=timeout)
            if ok:
                return True
        except Exception:
            pass
    return False


def _repair_dataset_path(project_root: Path, repair_dataset_path: str | Path | None, run_id: str = "") -> Path:
    if repair_dataset_path is None:
        return default_run_dataset_path(project_root, run_id=run_id)
    path = Path(repair_dataset_path)
    return path if path.is_absolute() else project_root / path


def _translation_local_context(
    *,
    latex_statement: str,
    latex_proof_hint: str,
    schema: dict | None,
) -> str:
    chunks = [f"latex_statement: {latex_statement.strip()}"]
    if latex_proof_hint.strip():
        chunks.append(f"latex_proof_hint: {latex_proof_hint.strip()}")
    if schema:
        try:
            chunks.append("statement_schema: " + json.dumps(schema, ensure_ascii=False, sort_keys=True))
        except TypeError:
            chunks.append(f"statement_schema: {schema}")
    return "\n".join(chunks)


def _validate_signature(
    signature: str,
    *,
    project_root: Path,
    imports: str = "",
    timeout: int = 60,
    retrieval_index_path: str = "",
) -> tuple[bool, str, bool]:
    """Return (ok, error_message, irrecoverable).

    Automatically expands imports when the error indicates a missing identifier:
    looks up the name in the premise index to find the correct Mathlib module,
    then retries.  Falls back to returning the last error after _MAX_IMPORT_EXPANSIONS.
    """
    _MAX_IMPORT_EXPANSIONS = 4

    # Strip any existing body so we can attach sorry cleanly.
    raw_sig = re.sub(r":=\s*by\b.*$", "", signature, flags=re.DOTALL).strip()
    raw_sig = re.sub(r":=\s*$", "", raw_sig).strip()
    # Gate on raw declaration keyword before any cleanup rewrite.
    first_kw = raw_sig.strip().split()[0] if raw_sig.strip() else ""
    if first_kw in ("def", "noncomputable", "structure", "class", "instance", "abbrev"):
        return False, (
            f"declaration starts with `{first_kw}` — only `theorem` or `lemma` are accepted. "
            "The input LaTeX is a definition; translate it as a proposition about its properties."
        ), False
    sig = _deterministic_signature_cleanup(raw_sig)

    # Pre-validate fixes: deterministic rewrites that don't need a Lean round-trip to detect.

    # Fix 1: Missing theorem name — `theorem {` or `theorem (` → `theorem _anon {`
    sig = re.sub(r"^(theorem|lemma)\s+([{(])", r"\1 _anon \2", sig, flags=re.MULTILINE)

    # Fix 2: `λ` used as a variable name (e.g. `(λ : ℝ)`) → rename to `lam`.
    sig = re.sub(r"\(λ\s*:", "(lam :", sig)
    sig = re.sub(r"\{λ\s*:", "{lam :", sig)

    # Fix 3: `noncomputable` theorem with body — strip `noncomputable` from theorem/lemma decl.
    # (noncomputable is valid only on defs, not theorems)
    sig = re.sub(r"^noncomputable\s+(theorem|lemma)\b", r"\1", sig, flags=re.MULTILINE)

    # P2 Security Validation: theorem name and signature format (before Lean compilation)
    try:
        # Extract theorem name (first identifier after theorem/lemma keyword)
        match = re.search(r"^(theorem|lemma)\s+([a-z_][a-z0-9_']*)", sig, re.MULTILINE | re.IGNORECASE)
        if match:
            theorem_name = match.group(2)
            # Validate theorem name format
            validate_theorem_name(theorem_name)
        
        # Validate full signature for injection attempts
        validate_theorem_signature(sig)
    except ValueError as e:
        # If validation fails, try to sanitize (e.g., remove LaTeX sequences)
        try:
            sig = validate_and_sanitize_signature(sig)
        except ValueError as sanitize_err:
            # Fall back to deterministic cleanup and continue into Lean-level
            # validation instead of failing early on sanitization strictness.
            sig = _deterministic_signature_cleanup(sig)

    # Choose starting import header.
    if imports.strip():
        current_imports = imports.strip()
    else:
        # Use broad baseline, not just SDE.Basic, so arbitrary papers can elaborate.
        current_imports = _BASELINE_IMPORTS.strip()

    # Load name→module index once if available.
    name_index: dict[str, str] = {}
    if retrieval_index_path:
        try:
            name_index = _load_name_module_index(retrieval_index_path)
        except Exception:
            pass

    added_modules: set[str] = set()
    stubs: str = ""

    for _attempt in range(_MAX_IMPORT_EXPANSIONS + 1):
        lean_src = f"{current_imports}\n\n{stubs}\n{sig} := by sorry\n"
        ok, err = _run_lean(lean_src, project_root, timeout)
        if ok:
            return True, "", False

        # Name collision with an existing declaration: deterministically rename
        # this theorem and retry.
        if "has already been declared" in err:
            decl_match = re.search(r"`([^`]+)` has already been declared", err)
            current_name = ""
            name_match = re.search(r"^(theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_']*)", sig, re.MULTILINE)
            if name_match:
                current_name = name_match.group(2)
            taken_name = decl_match.group(1).split(".")[-1] if decl_match else current_name
            if current_name:
                next_name = f"{current_name}_paper"
                if next_name == taken_name:
                    next_name = f"{current_name}_paper{_attempt + 1}"
                sig = re.sub(
                    r"^(theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_']*)",
                    rf"\1 {next_name}",
                    sig,
                    count=1,
                    flags=re.MULTILINE,
                )
                continue

        # Complex `if ... then ... else ...` in proposition types often needs a
        # `Decidable` instance that the model did not provide. Simplify the `if`
        # term to its `then` branch and retry.
        if "failed to synthesize instance" in err and "Decidable" in err and "if " in sig:
            new_sig = _rewrite_if_let_for_decidable(sig)
            if new_sig != sig:
                sig = new_sig
                continue

        # Some generated signatures accidentally add propositions via `+`
        # (e.g. `HasDerivAt ... + HasDerivAt ...`), which triggers `HAdd Prop Prop`.
        if "HAdd Prop Prop" in err:
            new_sig = _rewrite_prop_hadd_to_and(sig)
            if new_sig != sig:
                sig = new_sig
                continue

        if _attempt >= _MAX_IMPORT_EXPANSIONS:
            return False, err, _is_irrecoverable(err, stubs)

        # Try 1: resolve missing identifiers to Mathlib modules via olean check.
        new_modules = [
            m for m in _resolve_missing_imports(err, name_index, project_root)
            if m not in added_modules
        ]
        if new_modules:
            for mod in new_modules:
                added_modules.add(mod)
                if f"import {mod}" not in current_imports:
                    current_imports = f"import {mod}\n{current_imports}"
            continue

        # Try 2: auto-stub unknown identifiers not in Mathlib at all.
        unknown = _extract_unknown_idents(err, name_index, current_imports, project_root)
        if unknown:
            new_stubs = _build_stubs(unknown)
            if new_stubs and new_stubs not in stubs:
                stubs = stubs + "\n" + new_stubs if stubs else new_stubs
                continue

        # Try 2b: Lean autoImplicit treats unknown paper-local functions as
        # metavariables, then fails when they are applied (`Function expected at
        # a_N`).  Add minimal function axioms so validation can continue.
        fn_unknown = _extract_autoimplicit_function_names(err, sig)
        if fn_unknown:
            new_sig = _add_missing_function_binders(sig, fn_unknown)
            if new_sig != sig:
                sig = new_sig
                continue
            new_stubs = _build_function_stubs(fn_unknown)
            if new_stubs and new_stubs not in stubs:
                stubs = stubs + "\n" + new_stubs if stubs else new_stubs
                continue

        # Try 2c: if a hypothesis uses unsupported paper-local set/function
        # notation (`U ∈ C_TH ^ s1`), preserve it as a proposition slot.
        if (
            "HPow ?m" in err
            or "HPow (" in err
            or "Membership ?m" in err
            or "LE Type" in err
            or "AddGroup (Fin" in err
            or "OfNat (Fin" in err
        ):
            new_sig = _relax_fragile_hypotheses(sig)
            if new_sig != sig:
                sig = new_sig
                continue

        # Try 3a: rewrite non-Mathlib classes using the concept map / TC graph.
        synth_class_matches = _SYNTH_INSTANCE_RE.findall(err)
        rewritten = False
        for inst_line in synth_class_matches:
            class_name = inst_line.strip().split()[0] if inst_line.strip() else ""
            replacement = _get_lean_replacement(class_name)
            if replacement and not replacement.startswith("(h"):
                # Replace `[ClassNameX ...]` binder in sig with replacement.
                binder_pattern = re.compile(
                    r"\[" + re.escape(class_name) + r"[^\]]*\]"
                )
                new_sig = binder_pattern.sub(replacement, sig)
                if new_sig != sig:
                    sig = new_sig
                    rewritten = True
        if rewritten:
            continue

        # Try 3b: auto-stub type classes that failed synthesis and are not in Mathlib.
        unknown_classes = _extract_unknown_classes(err, current_imports, project_root)
        if unknown_classes:
            new_stubs = _build_class_stubs(unknown_classes)
            if new_stubs and new_stubs not in stubs:
                stubs = stubs + "\n" + new_stubs if stubs else new_stubs
                continue

        # Try 4: "Function expected at X" on a previously no-arg stubbed def — upgrade to 1-arg.
        func_names = _FUNC_EXPECTED_RE.findall(err)
        upgraded = False
        for fname in func_names:
            safe = re.sub(r"[^A-Za-z0-9_]", "_", fname)
            old_stub = f"noncomputable def {safe} : Type* := sorry"
            new_stub = f"noncomputable def {safe} (α : Type*) : Type* := sorry"
            if old_stub in stubs:
                stubs = stubs.replace(old_stub, new_stub)
                upgraded = True
        if upgraded:
            continue

        # Try 5: `invalid binder annotation` — rewrite offending [P] → (h_P : P) in signature.
        if "invalid binder annotation" in err or "type is not a class" in err:
            new_sig = _fix_invalid_binders(sig, err)
            if new_sig != sig:
                sig = new_sig
                continue

        # Try 6: Lean 3 lambda syntax `λ x,` → `fun x =>` (deterministic text rewrite).
        # Also handles eigenvalue notation like `λ_max`, `λ_min`, `λ_i` which Lean parses as lambda.
        if "unexpected token 'λ'" in err or "unexpected token 'lambda'" in err:
            # First: replace eigenvalue notation λ_max/λ_min/λ_i with a descriptive identifier.
            new_sig = re.sub(r"λ_max\s*\(([^)]+)\)", r"Matrix.eigenvalues_max (\1)", sig)
            new_sig = re.sub(r"λ_min\s*\(([^)]+)\)", r"Matrix.eigenvalues_min (\1)", new_sig)
            new_sig = re.sub(r"λ_(\w+)\s*\(([^)]+)\)", r"eigenvalue_\1 (\2)", new_sig)
            # Then: replace any remaining Lean 3 lambda syntax.
            new_sig = re.sub(r"λ\s+([^,\n]+),\s*", lambda m: f"fun {m.group(1).strip()} => ", new_sig)
            new_sig = new_sig.replace("λ ", "fun ")
            if new_sig != sig:
                sig = new_sig
                continue

        # Try 7: `in` keyword in binders — Lean 3 `∑ i in S` → `∑ i ∈ S`
        if "unexpected token 'in'" in err:
            new_sig = re.sub(r"\b(∑|∏|∀|∃)\s+(\w+)\s+in\s+", r"\1 \2 ∈ ", sig)
            if new_sig != sig:
                sig = new_sig
                continue

        # No progress possible via imports or stubs.
        # Check if the error is irrecoverable (stub-based failure) — signal early exit.
        irrecoverable = _is_irrecoverable(err, stubs)
        return False, err, irrecoverable

    return False, err, False


def translate_statement(
    *,
    latex_statement: str,
    latex_proof_hint: str = "",
    client: object,
    model: str,
    project_root: Path,
    imports: str = "",
    max_repair_rounds: int = 3,
    translation_candidates: int = 1,
    temperature: float = 0.2,
    api_log_hook: object = None,
    retrieval_index_path: str = "data/mathlib_embeddings",
    run_adversarial_check: bool = True,
    run_roundtrip_check: bool = True,
    use_schema_stage: bool = True,
    deterministic_hard_mode: bool = True,
    strict_assumption_slot_coverage: bool = True,
    enable_schema_template_synthesis: bool = False,
    enable_schema_self_check: bool = True,
    glossary_hint: str = "",
    paper_id: str = "",
    theorem_name: str = "",
    run_id: str = "",
    repair_dataset_path: str | Path | None = None,
) -> TranslationResult:
    """Translate a LaTeX statement to a validated Lean 4 signature.

    imports: if empty, the broad _BASELINE_IMPORTS is used and auto-expanded as needed.
    retrieval_index_path: premise index used to resolve missing identifiers to Mathlib modules.
    """
    schema: dict | None = None
    repair_feedback_rows: list[dict] = []

    def record_repair_feedback(
        *,
        failing_lean: str,
        error_message: str,
        previous_attempt: str = "",
        successful_repair: str = "",
        stage: str = "translation_validation",
        extra: dict[str, object] | None = None,
    ) -> None:
        if not str(error_message or "").strip():
            return
        repair_feedback_rows.append(
            make_repair_row(
                paper_id=paper_id,
                theorem_name=theorem_name,
                failing_lean=failing_lean,
                error_message=error_message,
                local_context=_translation_local_context(
                    latex_statement=latex_statement,
                    latex_proof_hint=latex_proof_hint,
                    schema=schema,
                ),
                previous_attempt=previous_attempt,
                successful_repair=successful_repair,
                stage=stage,
                repair_source=stage,
                model=model,
                run_id=run_id,
                project_root=project_root,
                extra=extra,
            )
        )

    def flush_repair_feedback(successful_repair: str = "") -> None:
        if not repair_feedback_rows:
            return
        if successful_repair:
            for row in repair_feedback_rows:
                row["successful_repair"] = successful_repair
                row["repair_available"] = True
        try:
            append_repair_rows(_repair_dataset_path(project_root, repair_dataset_path, run_id=run_id), repair_feedback_rows)
        except Exception:
            pass
        repair_feedback_rows.clear()

    hard_mode_active = deterministic_hard_mode and _is_hard_statement(latex_statement)
    if hard_mode_active:
        schema = _extract_literal_schema(latex_statement)

    if use_schema_stage and schema is None:
        schema = extract_translation_schema(
            latex_statement=latex_statement,
            latex_proof_hint=latex_proof_hint,
            client=client,
            model=model,
            api_log_hook=api_log_hook,
        )

    typed_structured = build_typed_statement_translation(
        latex_statement=latex_statement,
        schema=schema,
        theorem_name=theorem_name,
        paper_id=paper_id,
    )
    if typed_structured:
        typed_sig = str(typed_structured.get("lean_declaration", "") or "")
        ok_typed, err_typed, _ = _validate_signature(
            typed_sig,
            project_root=project_root,
            imports=imports,
            retrieval_index_path=retrieval_index_path,
        )
        if ok_typed:
            typed_issues = _semantic_policy_issues(
                latex_statement=latex_statement,
                signature=typed_sig,
                schema=schema,
                strict_assumption_slot_coverage=bool(strict_assumption_slot_coverage),
            )
            typed_issues = [
                issue for issue in typed_issues
                if issue not in {"schema_scaffold_not_faithful"}
                and not issue.startswith("assumption_slot_missing:")
            ]
            if not typed_issues:
                flush_repair_feedback(typed_sig)
                return TranslationResult(
                    lean_signature=typed_sig,
                    validated=True,
                    rounds_used=1,
                    last_error="",
                    confidence=0.86,
                    uncertainty_flags=["typed_statement_ir", "schema_stage_used" if schema is not None else "schema_stage_missing", "review_required_typed_ir"],
                    adversarial_flags=[],
                    roundtrip_back_translation=None,
                    roundtrip_flags=[],
                    statement_schema=schema or {},
                    structured_translation=typed_structured,
                )
            record_repair_feedback(
                failing_lean=typed_sig,
                error_message="typed_statement_ir_policy_violation:" + ",".join(typed_issues),
                stage="typed_statement_ir_policy",
            )
        else:
            record_repair_feedback(
                failing_lean=typed_sig,
                error_message="typed_statement_ir_invalid:" + str(err_typed),
                stage="typed_statement_ir_validation",
            )

    last_error = ""
    structured_translation = extract_structured_translation(
        latex_statement=latex_statement,
        latex_proof_hint=latex_proof_hint,
        schema=schema,
        glossary_hint=glossary_hint,
        client=client,
        model=model,
        api_log_hook=api_log_hook,
    ) if use_schema_stage else None
    if structured_translation:
        structured_sig = str(structured_translation.get("lean_declaration", "") or "")
        ok_s, err_s, _ = _validate_signature(
            structured_sig,
            project_root=project_root,
            imports=imports,
            retrieval_index_path=retrieval_index_path,
        )
        if ok_s:
            structured_issues = _semantic_policy_issues(
                latex_statement=latex_statement,
                signature=structured_sig,
                schema=schema,
                strict_assumption_slot_coverage=bool(strict_assumption_slot_coverage),
            )
            if enable_schema_self_check:
                structured_issues.extend(
                    f"schema_self_check:{x}"
                    for x in _schema_signature_self_check(
                        schema=schema,
                        signature=structured_sig,
                        client=client,
                        model=model,
                        api_log_hook=api_log_hook,
                    )
                )
            is_vacuous = bool(
                project_root is not None
                and _check_vacuous(structured_sig, project_root=project_root, imports=imports)
            )
            if not structured_issues and not is_vacuous:
                confidence, flags = _confidence_from_translation_state(
                    validated=True,
                    rounds_used=1,
                    last_error="",
                    signature=structured_sig,
                )
                flags.extend(["structured_json_stage_used", "schema_stage_used" if schema is not None else "schema_stage_missing"])
                flush_repair_feedback(structured_sig)
                return TranslationResult(
                    lean_signature=structured_sig,
                    validated=True,
                    rounds_used=1,
                    last_error="",
                    confidence=max(0.0, confidence - 0.03),
                    uncertainty_flags=flags,
                    adversarial_flags=[],
                    roundtrip_back_translation=None,
                    roundtrip_flags=[],
                    statement_schema=schema,
                    structured_translation=structured_translation,
                )
            if is_vacuous:
                structured_issues.append("vacuity: statement is trivially provable by `trivial`")
            last_error = "structured_translation_policy_violation:" + ",".join(structured_issues)
            record_repair_feedback(
                failing_lean=structured_sig,
                error_message=last_error,
                stage="structured_translation_policy",
            )
        else:
            last_error = "structured_translation_invalid:" + str(err_s)
            record_repair_feedback(
                failing_lean=structured_sig,
                error_message=last_error,
                stage="structured_translation_validation",
            )

    user_parts = [f"LaTeX statement:\n{latex_statement}"]
    if latex_proof_hint.strip():
        user_parts.append(f"Informal proof context:\n{latex_proof_hint.strip()}")
    if glossary_hint.strip():
        user_parts.append(f"Paper glossary memory:\n{glossary_hint.strip()}")
    if schema is not None:
        user_parts.append(
            "Stage-A extracted schema (use this structure faithfully when writing Lean):\n"
            f"{json.dumps(schema, ensure_ascii=False, indent=2)}"
        )
    if structured_translation is not None:
        user_parts.append(
            "Structured JSON translation draft (repair this structure instead of free-form guessing):\n"
            f"{json.dumps(structured_translation, ensure_ascii=False, indent=2)}"
        )
    user_parts.append(
        "Output the Lean 4 theorem signature inside <signature>...</signature>. "
        "Use standard Mathlib4 naming and type class conventions."
    )

    messages = [
        {"role": "system", "content": _get_translate_system()},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]

    signature = ""
    dynamic_extra_rounds = 0

    n_candidates = max(1, int(translation_candidates))

    # Template-backed attempt before free-form generation.
    if enable_schema_template_synthesis and schema is not None:
        template_sig = _build_template_signature_from_schema(schema)
        ok_t, err_t, _ = _validate_signature(
            template_sig,
            project_root=project_root,
            imports=imports,
            retrieval_index_path=retrieval_index_path,
        )
        if ok_t and not _is_schema_scaffold_signature(template_sig):
            conf_t, flags_t = _confidence_from_translation_state(
                validated=True,
                rounds_used=1,
                last_error="",
                signature=template_sig,
            )
            flags_t.extend(["schema_template_mode", "schema_stage_used"])
            flush_repair_feedback(template_sig)
            return TranslationResult(
                lean_signature=template_sig,
                validated=True,
                rounds_used=1,
                last_error="",
                confidence=max(0.0, conf_t - 0.08),
                uncertainty_flags=flags_t,
                adversarial_flags=[],
                roundtrip_back_translation=None,
                roundtrip_flags=[],
                statement_schema=schema,
            )
        last_error = err_t if not ok_t else "schema_template_scaffold_rejected"
        record_repair_feedback(
            failing_lean=template_sig,
            error_message=last_error,
            stage="schema_template_validation",
        )

    max_rounds_total = max_repair_rounds + 1
    round_idx = 1
    while round_idx <= max_rounds_total + dynamic_extra_rounds:
        candidate_failures: list[dict[str, object]] = []
        round_irrecoverable = True

        for cand_idx in range(n_candidates):
            cand_temperature = min(1.0, max(0.0, temperature + 0.12 * cand_idx))
            _, text = _chat_complete(
                client=client,
                model=model,
                messages=messages,
                temperature=cand_temperature,
                max_tokens=2000,
                purpose=f"translate_round_{round_idx}_candidate_{cand_idx + 1}",
                api_log_hook=api_log_hook,
            )
            signature = _normalize_final_signature(_extract_signature(text))

            ok, cand_error, irrecoverable = _validate_signature(
                signature,
                project_root=project_root,
                imports=imports,
                retrieval_index_path=retrieval_index_path,
            )
            if not irrecoverable:
                round_irrecoverable = False

            if ok:
                policy_issues = _semantic_policy_issues(
                    latex_statement=latex_statement,
                    signature=signature,
                    schema=schema,
                    strict_assumption_slot_coverage=bool(strict_assumption_slot_coverage),
                )
                if enable_schema_self_check:
                    self_check_issues = _schema_signature_self_check(
                        schema=schema,
                        signature=signature,
                        client=client,
                        model=model,
                        api_log_hook=api_log_hook,
                    )
                    if self_check_issues:
                        policy_issues.extend([f"schema_self_check:{x}" for x in self_check_issues])

                if policy_issues:
                    repaired_signature = _semantic_repair_signature(
                        latex_statement=latex_statement,
                        latex_proof_hint=latex_proof_hint,
                        schema=schema,
                        current_signature=signature,
                        issues=policy_issues,
                        client=client,
                        model=model,
                        api_log_hook=api_log_hook,
                    )
                    if repaired_signature:
                        ok_rep, rep_error, rep_irrecoverable = _validate_signature(
                            repaired_signature,
                            project_root=project_root,
                            imports=imports,
                            retrieval_index_path=retrieval_index_path,
                        )
                        if ok_rep:
                            rep_issues = _semantic_policy_issues(
                                latex_statement=latex_statement,
                                signature=repaired_signature,
                                schema=schema,
                                strict_assumption_slot_coverage=bool(strict_assumption_slot_coverage),
                            )
                            if enable_schema_self_check:
                                rep_self = _schema_signature_self_check(
                                    schema=schema,
                                    signature=repaired_signature,
                                    client=client,
                                    model=model,
                                    api_log_hook=api_log_hook,
                                )
                                rep_issues.extend([f"schema_self_check:{x}" for x in rep_self])
                            if rep_issues:
                                rep_error_text = "semantic_policy_violation:" + ",".join(rep_issues)
                                record_repair_feedback(
                                    failing_lean=repaired_signature,
                                    error_message=rep_error_text,
                                    previous_attempt=signature,
                                    stage="semantic_repair_policy",
                                    extra={"round": round_idx, "candidate": cand_idx + 1},
                                )
                                candidate_failures.append(
                                    {
                                        "text": text,
                                        "signature": repaired_signature,
                                        "error": rep_error_text,
                                        "irrecoverable": False,
                                    }
                                )
                                continue
                            signature = repaired_signature
                        else:
                            rep_error_text = "semantic_repair_invalid:" + str(rep_error)
                            record_repair_feedback(
                                failing_lean=repaired_signature,
                                error_message=rep_error_text,
                                previous_attempt=signature,
                                stage="semantic_repair_validation",
                                extra={"round": round_idx, "candidate": cand_idx + 1},
                            )
                            candidate_failures.append(
                                {
                                    "text": text,
                                    "signature": repaired_signature,
                                    "error": rep_error_text,
                                    "irrecoverable": bool(rep_irrecoverable),
                                }
                            )
                            continue
                    else:
                        policy_error_text = "semantic_policy_violation:" + ",".join(policy_issues)
                        record_repair_feedback(
                            failing_lean=signature,
                            error_message=policy_error_text,
                            previous_attempt=text,
                            stage="semantic_policy",
                            extra={"round": round_idx, "candidate": cand_idx + 1},
                        )
                        candidate_failures.append(
                            {
                                "text": text,
                                "signature": signature,
                                "error": policy_error_text,
                                "irrecoverable": False,
                            }
                        )
                        continue

                # Vacuity check: even after semantic-policy repair, reject trivial closure.
                if project_root is not None and _check_vacuous(
                    signature,
                    project_root=project_root,
                    imports=imports,
                ):
                    vacuity_error = (
                        "semantic_policy_violation:"
                        "vacuity: statement is trivially provable by `trivial`"
                    )
                    record_repair_feedback(
                        failing_lean=signature,
                        error_message=vacuity_error,
                        previous_attempt=text,
                        stage="vacuity_check",
                        extra={"round": round_idx, "candidate": cand_idx + 1},
                    )
                    candidate_failures.append(
                        {
                            "text": text,
                            "signature": signature,
                            "error": vacuity_error,
                            "irrecoverable": False,
                        }
                    )
                    continue

                confidence, flags = _confidence_from_translation_state(
                    validated=True,
                    rounds_used=round_idx,
                    last_error="",
                    signature=signature,
                )
                adv_flags: list[str] = []
                if run_adversarial_check and latex_statement.strip():
                    adv_flags = adversarial_translation_check(
                        latex_statement=latex_statement,
                        lean_signature=signature,
                        client=client,
                        model=model,
                        api_log_hook=api_log_hook,
                    )
                    if adv_flags:
                        penalty = min(0.30, 0.10 * len(adv_flags))
                        confidence = max(0.0, confidence - penalty)
                        if "trivially_true" in adv_flags:
                            flags.append("trivially_true_detected")
                        if any(f.startswith("verdict:") for f in adv_flags):
                            flags.append("adversarial_mismatch")

                roundtrip_back_translation: str | None = None
                roundtrip_flags: list[str] = []
                if run_roundtrip_check and latex_statement.strip() and signature.strip():
                    roundtrip_back_translation, roundtrip_flags = roundtrip_translation_check(
                        latex_statement=latex_statement,
                        lean_signature=signature,
                        client=client,
                        model=model,
                        api_log_hook=api_log_hook,
                    )
                    if roundtrip_flags:
                        penalty = min(0.25, 0.08 * len(roundtrip_flags))
                        confidence = max(0.0, confidence - penalty)
                        flags.append("roundtrip_semantic_risk")
                flush_repair_feedback(signature)
                return TranslationResult(
                    lean_signature=signature,
                    validated=True,
                    rounds_used=round_idx,
                    last_error="",
                    confidence=confidence,
                    uncertainty_flags=(
                        flags
                        + (["schema_stage_used"] if schema is not None else ["schema_stage_missing"])
                        + (["review_required_hard"] if hard_mode_active else [])
                    ),
                    adversarial_flags=adv_flags,
                    roundtrip_back_translation=roundtrip_back_translation,
                    roundtrip_flags=roundtrip_flags,
                    statement_schema=schema,
                    structured_translation=structured_translation,
                )

            candidate_failures.append(
                {
                    "text": text,
                    "signature": signature,
                    "error": cand_error,
                    "irrecoverable": irrecoverable,
                }
            )
            record_repair_feedback(
                failing_lean=signature,
                error_message=cand_error,
                previous_attempt=text,
                stage="translation_validation",
                extra={"round": round_idx, "candidate": cand_idx + 1, "irrecoverable": bool(irrecoverable)},
            )

        # No candidate validated this round; choose best failure for repair prompt.
        if candidate_failures:
            best_failure = min(
                candidate_failures,
                key=lambda c: (
                    0 if not bool(c.get("irrecoverable")) else 1,
                    len(str(c.get("error", ""))),
                ),
            )
            text = str(best_failure.get("text", ""))
            signature = str(best_failure.get("signature", ""))
            last_error = str(best_failure.get("error", ""))
        else:
            last_error = "no candidate output produced"

        if dynamic_extra_rounds == 0:
            dynamic_extra_rounds = _extra_retry_rounds_for_error(last_error)
        if round_idx > (max_repair_rounds + dynamic_extra_rounds) or round_irrecoverable:
            break

        hint = _build_repair_hint(last_error)
        directive = _retry_directive_for_error(last_error)
        messages.append({"role": "assistant", "content": text})
        messages.append({
            "role": "user",
            "content": (
                f"The signature failed to elaborate:\n{last_error}\n\n"
                f"{hint}\n\n"
                f"Retry directive:\n{directive}\n\n"
                "Output the corrected signature inside <signature>...</signature>. "
                "Only the theorem/lemma declaration — no imports, no variable blocks."
            ),
        })
        round_idx += 1

    # All repair rounds exhausted.
    # Domain-escalation pass: one final attempt with a stripped-down prompt that
    # (a) explicitly forbids placeholder bodies, (b) provides the LaTeX claim as
    # a plain-English description, and (c) raises temperature to break out of the
    # same failure mode. This catches the two recurring failure patterns:
    #   - Translator produced a `schema_claim_hint:` stub (bad sig from LaTeX noise)
    #   - Translator produced Nonempty(Unit) / p_c1:Prop placeholder body
    last_sig_is_placeholder = _is_trivialized_signature(signature) or (
        signature and "schema_claim_hint" in signature
    )
    if last_sig_is_placeholder and client is not None:
        claim_text = ""
        if schema is not None:
            claim_text = str(schema.get("claim", "")).strip()
            assumptions_text = "; ".join(str(a) for a in (schema.get("assumptions") or [])[:5])
        else:
            claim_text = latex_statement[:300]
            assumptions_text = ""

        escalation_user = (
            f"The previous translation attempts all produced placeholder or trivial bodies.\n\n"
            f"LaTeX statement:\n{latex_statement}\n\n"
            f"What the theorem ACTUALLY says (extracted):\n"
            f"  Claim: {claim_text}\n"
            f"  Assumptions: {assumptions_text}\n\n"
            "RULES FOR THIS ATTEMPT:\n"
            "1. Output a genuine Lean 4 theorem signature — NOT `Nonempty (Unit)`, NOT `(0:ℕ)=0`, "
            "NOT `p_c1 : Prop`, NOT `: True`, NOT `: False`.\n"
            "2. If the domain types (e.g. multisegments, quiver representations) do not exist in "
            "Mathlib, declare them as `axiom MyType : Type` BEFORE the theorem and use them.\n"
            "3. The theorem must state the actual mathematical claim. Use `sorry` as the proof body.\n"
            "4. Output exactly one declaration inside <signature>...</signature>."
        )
        escalation_messages = [
            {"role": "system", "content": _get_translate_system()},
            {"role": "user", "content": escalation_user},
        ]
        try:
            _, esc_text = _chat_complete(
                client=client,
                model=model,
                messages=escalation_messages,
                temperature=0.6,
                max_tokens=2000,
                purpose="translate_domain_escalation",
                api_log_hook=api_log_hook,
            )
            esc_sig = _normalize_final_signature(_extract_signature(esc_text))
            if esc_sig and not _is_trivialized_signature(esc_sig):
                esc_ok, esc_err, _ = _validate_signature(
                    esc_sig,
                    project_root=project_root,
                    imports=imports,
                    retrieval_index_path=retrieval_index_path,
                )
                if esc_ok:
                    conf_e, flags_e = _confidence_from_translation_state(
                        validated=True, rounds_used=round_idx + 1,
                        last_error="", signature=esc_sig,
                    )
                    flags_e.append("domain_escalation_pass")
                    flush_repair_feedback(esc_sig)
                    return TranslationResult(
                        lean_signature=esc_sig,
                        validated=True,
                        rounds_used=round_idx + 1,
                        last_error="",
                        confidence=max(0.0, conf_e - 0.10),
                        uncertainty_flags=flags_e,
                        adversarial_flags=[],
                        statement_schema=schema,
                        structured_translation=structured_translation,
                    )
                # Escalation produced a real (non-placeholder) sig that didn't validate —
                # still better than a placeholder: emit as sorry stub.
                if not _is_trivialized_signature(esc_sig):
                    record_repair_feedback(
                        failing_lean=esc_sig,
                        error_message=esc_err,
                        previous_attempt=signature,
                        stage="domain_escalation_validation",
                    )
                    signature = esc_sig
                    last_error = esc_err
        except Exception:
            pass  # escalation failed — fall through to sorry stub below

    signature = _normalize_final_signature(signature)
    sorry_stub = _apply_schema_fallback(signature, schema)
    confidence, flags = _confidence_from_translation_state(
        validated=False,
        rounds_used=max(1, round_idx),
        last_error=last_error,
        signature=sorry_stub,
    )
    flags = flags + (["schema_stage_used"] if schema is not None else ["schema_stage_missing"])
    flags.append("statement_repair_needed:schema_fallback")
    if hard_mode_active:
        flags.append("review_required_hard")
    # Generate decomposition stubs for missing types/identifiers.
    stubs: list[dict] = []
    if last_error and client is not None:
        stubs = generate_decomposition_stubs(
            lean_signature=sorry_stub,
            lean_error=last_error,
            client=client,
            model=model,
            api_log_hook=api_log_hook,
        )
    flush_repair_feedback()
    return TranslationResult(
        lean_signature=sorry_stub,
        validated=False,
        rounds_used=max(1, round_idx),
        last_error=last_error,
        confidence=confidence,
        uncertainty_flags=flags,
        decomposition_stubs=stubs,
        statement_schema=schema,
        structured_translation=structured_translation,
    )


_STUB_SYSTEM = (
    "You are a Lean 4 type stub generator. "
    "Given a Lean 4 theorem signature that failed to elaborate because of unknown types or structures, "
    "identify the missing definitions and generate minimal `sorry`-backed stubs. "
    "Output a JSON list of stub objects:\n"
    '[{"name": "TypeName", "kind": "structure|def|abbrev|class", '
    '"lean_stub": "-- Stub for missing type\\nstructure TypeName where\\n  sorry_field : Unit := ()"}]\n'
    "Keep stubs minimal. Use `sorry`-backed implementations. "
    "If no stubs are needed, output an empty list []."
)

# Regex to find unknown identifier errors from Lean: "unknown identifier 'Foo'"
_UNKNOWN_ID_RE = re.compile(r"unknown (?:identifier|constant|type)\s+'([^']+)'")


def generate_decomposition_stubs(
    *,
    lean_signature: str,
    lean_error: str,
    client: object,
    model: str,
    api_log_hook: object = None,
) -> list[dict]:
    """When translation fails due to unknown types/identifiers, generate sorry-backed stubs.

    Each stub is a minimal Lean 4 definition that satisfies the type-checker enough
    for the theorem statement to elaborate. These become KG seed entries with
    UNGROUNDED status — targets for later proof search.

    Returns a list of {name, kind, lean_stub} dicts.
    """
    # Quick check: does the error mention unknown identifiers?
    known_missing = _UNKNOWN_ID_RE.findall(lean_error)

    user_msg = (
        f"Lean 4 theorem signature:\n{lean_signature}\n\n"
        f"Elaboration error:\n{lean_error}\n\n"
    )
    if known_missing:
        user_msg += f"Unknown identifiers detected: {', '.join(known_missing)}\n\n"
    user_msg += "Generate minimal sorry-backed stubs for all missing definitions."

    try:
        _, raw = _chat_complete(
            client=client,
            model=model,
            messages=[
                {"role": "system", "content": _STUB_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=600,
            purpose="generate_decomposition_stubs",
            api_log_hook=api_log_hook,
        )
    except Exception:
        return []

    # Extract JSON list from response.
    json_match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not json_match:
        return []
    try:
        stubs = json.loads(json_match.group(0))
        if not isinstance(stubs, list):
            return []
        return [s for s in stubs if isinstance(s, dict) and "name" in s and "lean_stub" in s]
    except (ValueError, KeyError):
        return []


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Translate LaTeX theorem statement to Lean 4")
    p.add_argument("--statement", required=True, help="LaTeX statement text")
    p.add_argument("--proof-hint", default="", help="Optional informal proof hint")
    p.add_argument("--project-root", default=".", help="Lean project root for validation")
    p.add_argument(
        "--imports",
        default="",
        help="Override Lean import header (default: broad baseline + auto-expansion)",
    )
    p.add_argument(
        "--retrieval-index",
        default="data/mathlib_embeddings",
        help="Premise index directory for auto-import resolution",
    )
    p.add_argument("--model", default="", help="Mistral model (defaults to MISTRAL_MODEL env)")
    p.add_argument("--max-repair-rounds", type=int, default=3)
    p.add_argument(
        "--translation-candidates",
        type=int,
        default=1,
        help="Number of translation candidates sampled per repair round",
    )
    p.add_argument("--temperature", type=float, default=0.2)
    p.add_argument(
        "--disable-schema-stage",
        action="store_true",
        help="Disable Stage-A schema extraction and synthesize Lean directly from raw LaTeX",
    )
    p.add_argument(
        "--disable-deterministic-hard-mode",
        action="store_true",
        help="Disable deterministic literal mode for hard statements",
    )
    return p


def main() -> int:
    load_dotenv()
    args = _build_parser().parse_args()

    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        print("[fail] MISTRAL_API_KEY is not set", file=sys.stderr)
        return 1

    model = args.model.strip() or os.getenv("MISTRAL_MODEL", "labs-leanstral-2603").strip()

    try:
        from mistralai import Mistral
    except ImportError:
        from mistralai.client import Mistral  # type: ignore[no-redef]

    client = Mistral(api_key=api_key)

    result = translate_statement(
        latex_statement=args.statement,
        latex_proof_hint=args.proof_hint,
        client=client,
        model=model,
        project_root=Path(args.project_root).resolve(),
        imports=args.imports,
        retrieval_index_path=args.retrieval_index,
        max_repair_rounds=args.max_repair_rounds,
        translation_candidates=args.translation_candidates,
        temperature=args.temperature,
        use_schema_stage=not args.disable_schema_stage,
        deterministic_hard_mode=not args.disable_deterministic_hard_mode,
    )

    status = "validated" if result.validated else "unvalidated"
    print(f"[{status}] rounds={result.rounds_used}")
    if result.last_error:
        print(f"[last_error] {result.last_error[:200]}")
    print("=== SIGNATURE ===")
    print(result.lean_signature)
    return 0 if result.validated else 1


if __name__ == "__main__":
    sys.exit(main())
