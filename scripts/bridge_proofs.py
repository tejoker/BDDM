#!/usr/bin/env python3
"""Bridge-proof planning and execution for multi-paper dependencies.

This module:
  1. Plans which previously-proved theorems may bridge an ungrounded assumption
     (token-overlap matching, always available).
  2. Checks simple linear-arithmetic entailment via Z3 (optional, requires
     z3-solver).  A statement like "a + b ≤ c" can be discharged automatically
     without touching Lean.
  3. Executes the bridge-proof chain by submitting each candidate as a Lean
     tactic to a running REPLDojo session (optional, requires lean-dojo).
     On success the assumption is promoted to GROUNDED_INTERNAL_KG.

No assumption is claimed as grounded unless the Lean REPL or Z3 confirms it.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from premise_retrieval import PremiseEntry, PremiseRetriever
    _HAS_RETRIEVAL = True
except ImportError:
    _HAS_RETRIEVAL = False


_TOKEN_RE = re.compile(r"[A-Za-z0-9_'.]+")


@dataclass
class BridgeCandidate:
    theorem_name: str
    paper_id: str
    status: str
    score: float
    lean_statement: str = ""
    actionable: bool = False


@dataclass
class TheoremContextPack:
    theorem_name: str
    definitions: list[str] = field(default_factory=list)
    notations: list[str] = field(default_factory=list)
    context_terms: list[str] = field(default_factory=list)


@dataclass
class BridgePlan:
    assumption_expr: str
    candidates: list[BridgeCandidate]


@dataclass
class BridgeChainPlan:
    target_theorem: str
    ordered_candidates: list[str]
    rationale: list[str]


@dataclass
class EntailmentResult:
    """Result of a Z3 or Lean entailment check on a single assumption."""
    assumption_expr: str
    method: str  # "z3", "lean_repl", "unverified"
    entailed: bool
    counterexample: str = ""
    error: str = ""
    elapsed_s: float = 0.0


@dataclass
class BridgeExecutionResult:
    """Result of running the full bridge-proof execution pipeline."""
    target_theorem: str
    chain_plan: BridgeChainPlan
    entailment_results: list[EntailmentResult] = field(default_factory=list)
    newly_grounded: list[str] = field(default_factory=list)
    still_ungrounded: list[str] = field(default_factory=list)
    failure_reasons: dict[str, int] = field(default_factory=dict)
    assumption_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    repair_attempts_total: int = 0
    repair_success_count: int = 0
    error: str = ""


def _norm_tokens(text: str) -> set[str]:
    tokens = [t.lower() for t in _TOKEN_RE.findall(text or "")]
    return {t for t in tokens if len(t) >= 4}


def _iter_ledger_entries(ledger_root: Path):
    for path in sorted(ledger_root.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
            rows = raw["entries"]
        elif isinstance(raw, list):
            rows = raw
        else:
            continue
        paper_id = path.stem
        for row in rows:
            if isinstance(row, dict):
                yield paper_id, row


def _load_ledger_index(ledger_root: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for _paper_id, row in _iter_ledger_entries(ledger_root):
        name = str(row.get("theorem_name", "")).strip()
        if name and name not in index:
            index[name] = row
    return index


def _extract_type_from_assumption_expr(lean_expr: str) -> str:
    m = re.match(r"\(\w+\s*:\s*(.+)\)$", (lean_expr or "").strip())
    if not m:
        return ""
    return m.group(1).strip()


_PROP_HINT_RE = re.compile(r"(=|≤|≥|<|>|∧|∨|→|↔|∀|∃|∈|⊆|⊂|True|False|¬|\\bProp\\b)")
_NON_ACTIONABLE_HEAD_RE = re.compile(r"^[A-Z][A-Za-z0-9_.']+$")
_HYP_SLOT_RE = re.compile(r"^\(\s*([A-Za-z_][A-Za-z0-9_']*)\s*:\s*.+\)$")
_QUANT_RE = re.compile(r"[∀∃]|\\b(forall|exists)\\b", re.IGNORECASE)
_IDENT_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_']*)\b")
_PLACEHOLDER_RE = re.compile(r"^(p_c\d+|h_c\d+|hyp\d+|tmp\d*)$", re.IGNORECASE)
_SUSPECT_GOAL_RE = re.compile(r"(\\[A-Za-z]+|--|&\s*\.|schema_|literal_schema_translation)")
_NAMESPACE_TOKENS = {
    "Real",
    "Set",
    "Filter",
    "Finset",
    "Asymptotics",
    "Nat",
    "Int",
    "Rat",
    "Complex",
    "NNReal",
    "ENNReal",
    "MeasureTheory",
    "ProbabilityTheory",
    "TopologicalSpace",
    "MetricSpace",
}
_FAIL_TAXONOMY = {
    "assumption_slot_unmapped": "slot_mismatch",
    "context_pack_insufficient": "slot_mismatch",
    "semantic_slot_mismatch": "slot_mismatch",
    "semantic_symbol_drift": "symbol_drift",
    "semantic_quantifier_loss": "quantifier_loss",
    "candidate_generation_empty": "non_actionable_candidate",
    "candidate_only_non_actionable": "non_actionable_candidate",
    "lean_missing_statement": "lean_type_mismatch",
    "lean_proof_failed": "lean_type_mismatch",
}
_DEFAULT_MEMORY_PATH = Path("output/bridge_memory/candidate_stats.json")
_DEFAULT_ARTIFACT_ROOT = Path("output/reports/bridge_failures")


def _looks_proposition(expr: str) -> bool:
    s = (expr or "").strip()
    if not s:
        return False
    if _PROP_HINT_RE.search(s):
        return True
    # Single capitalized constant like Real.exp is almost always not a proposition.
    if _NON_ACTIONABLE_HEAD_RE.match(s):
        return False
    # Light fallback for predicate-style formulas.
    if " " in s and any(ch.islower() for ch in s):
        return True
    return False


def _is_placeholder_atom(expr: str) -> bool:
    s = (expr or "").strip()
    if not s:
        return False
    if _PLACEHOLDER_RE.match(s):
        return True
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_']*", s) and "." not in s and "_" in s and not _PROP_HINT_RE.search(s):
        return True
    return False


def _goal_lane_allowed(*, lean_expr: str, lean_statement: str) -> bool:
    expr = (_extract_type_from_assumption_expr(lean_expr) or lean_expr or "").strip()
    stmt = (lean_statement or "").strip()
    source = stmt if stmt else expr
    if not source:
        return False
    if _SUSPECT_GOAL_RE.search(source):
        return False
    if source.count("[") != source.count("]"):
        return False
    if _is_placeholder_atom(expr):
        return False
    # Reject pure bracketed natural-language fragments.
    plain = source.replace(" ", "")
    if plain.startswith("[") and plain.endswith("]") and not any(
        tok in source for tok in ("∈", "=", "≤", "≥", "<", ">", "∧", "∨", "→", "↔", "∀", "∃")
    ):
        return False
    return _looks_proposition(source)


def _split_statement_head_claim(stmt: str) -> tuple[str, str]:
    s = (stmt or "").strip()
    if not s:
        return "", ""
    paren = 0
    bracket = 0
    brace = 0
    for i, ch in enumerate(s):
        if ch == "(":
            paren += 1
            continue
        if ch == ")" and paren > 0:
            paren -= 1
            continue
        if ch == "[":
            bracket += 1
            continue
        if ch == "]" and bracket > 0:
            bracket -= 1
            continue
        if ch == "{":
            brace += 1
            continue
        if ch == "}" and brace > 0:
            brace -= 1
            continue
        if ch == ":" and paren == 0 and bracket == 0 and brace == 0:
            if i + 1 < len(s) and s[i + 1] == "=":
                continue
            return s[:i].strip(), s[i + 1 :].strip()
    return s, ""


def _extract_statement_claim(stmt: str) -> str:
    _, claim = _split_statement_head_claim(stmt)
    return claim.strip()


def _strict_theorem_shape_ok(lean_statement: str) -> bool:
    stmt = (lean_statement or "").strip()
    if not _candidate_is_actionable(stmt):
        return False
    head, claim = _split_statement_head_claim(stmt)
    if not claim:
        return False
    # Must include at least one proposition operator or quantifier.
    if not any(tok in claim for tok in ("=", "≤", "≥", "<", ">", "∧", "∨", "→", "↔", "∀", "∃", "¬", "∈")):
        return False
    # Reject obvious malformed theorem headers.
    if "--" in head or "--" in claim:
        return False
    if claim.count("(") < claim.count(")"):
        return False
    if claim.count("[") < claim.count("]"):
        return False
    return True


def _infer_var_type(expr: str, name: str) -> str:
    s = expr
    if any(op in s for op in ("+", "-", "*", "/", "≤", "<", "≥", ">")):
        if name in {"n", "m", "k", "i", "j"} and re.search(r"\b\d+\b", s):
            return "ℕ"
        return "ℝ"
    if "Set." in s or "∈" in s:
        return "ℝ"
    return "ℝ"


def _extract_free_symbols(expr: str) -> list[str]:
    reserved = {
        "theorem",
        "lemma",
        "by",
        "have",
        "show",
        "let",
        "in",
        "if",
        "then",
        "else",
        "and",
        "or",
        "not",
        "True",
        "False",
        "Prop",
    }
    out: list[str] = []
    seen: set[str] = set()
    for token in _IDENT_RE.findall(expr or ""):
        if token in reserved or token in _NAMESPACE_TOKENS:
            continue
        if _PLACEHOLDER_RE.match(token):
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _extract_bound_symbols_from_statement(lean_statement: str) -> set[str]:
    s = lean_statement or ""
    out: set[str] = set()
    for m in re.finditer(r"[\(\{]\s*([A-Za-z][A-Za-z0-9_']*)\s*:", s):
        out.add(m.group(1))
    return out


def compile_theorem_context_bundle(
    *,
    lean_statement: str,
    context_pack: TheoremContextPack | None,
    decomposition: dict[str, Any] | None,
) -> dict[str, Any]:
    imports = ["import Mathlib"]
    opens = ["open scoped BigOperators Topology"]
    statement_expr = _extract_statement_claim(lean_statement)
    free_syms = _extract_free_symbols(statement_expr)
    bound = _extract_bound_symbols_from_statement(lean_statement)
    var_lines: list[str] = []
    for sym in free_syms:
        if sym in bound:
            continue
        ty = _infer_var_type(statement_expr, sym)
        var_lines.append(f"variable ({sym} : {ty})")
    # Keep only a small, deterministic prelude.
    var_lines = var_lines[:8]
    hints: list[str] = []
    if context_pack is not None:
        hints.extend(context_pack.definitions[:3])
        hints.extend(context_pack.notations[:3])
    if isinstance(decomposition, dict):
        hints.extend([str(x) for x in (decomposition.get("objects", []) or [])[:4]])
    hint_comments = [f"-- ctx_hint: {h}" for h in hints if h]
    prelude = "\n".join([*opens, *var_lines, *hint_comments]).strip()
    return {"imports": imports, "prelude": prelude}


def synthesize_actionable_goal(
    *,
    lean_expr: str,
    lean_statement: str,
    label: str,
) -> str:
    existing = (lean_statement or "").strip()
    if _candidate_is_actionable(existing):
        return existing
    expr = (_extract_type_from_assumption_expr(lean_expr) or lean_expr or "").strip()
    if not expr or not _goal_lane_allowed(lean_expr=lean_expr, lean_statement=existing):
        return ""
    vars_unique = _extract_free_symbols(expr)
    binders = ""
    if vars_unique:
        chunks = [f"({v} : {_infer_var_type(expr, v)})" for v in vars_unique[:5]]
        binders = " " + " ".join(chunks)
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", label or "bridge_goal").strip("_") or "bridge_goal"
    stmt = f"theorem {safe}{binders} : {expr}"
    if _candidate_is_actionable(stmt):
        return stmt
    return ""


def normalize_assumption_to_lean_statement(
    *,
    lean_expr: str,
    lean_statement: str = "",
    label: str = "",
) -> str:
    """Best-effort conversion of an assumption payload into an executable Lean theorem header."""
    existing = (lean_statement or "").strip()
    if existing and ":" in existing and ("theorem " in existing or "lemma " in existing):
        return existing

    expr = _extract_type_from_assumption_expr(lean_expr) or (lean_expr or "").strip()
    if not expr:
        return ""
    if not _looks_proposition(expr):
        return ""
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", label or "bridge_goal").strip("_") or "bridge_goal"
    return f"theorem {safe} : {expr}"


def extract_assumption_slot_name(*, lean_expr: str, label: str, idx: int) -> str:
    raw = (lean_expr or "").strip()
    m = _HYP_SLOT_RE.match(raw)
    if m:
        return m.group(1).strip()
    clean_label = re.sub(r"[^A-Za-z0-9_]+", "_", (label or "").strip()).strip("_")
    if clean_label:
        return clean_label
    return ""


def _collect_text_values(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        v = value.strip()
        if v:
            out.append(v)
        return out
    if isinstance(value, list):
        for item in value:
            out.extend(_collect_text_values(item))
        return out
    if isinstance(value, dict):
        for item in value.values():
            out.extend(_collect_text_values(item))
        return out
    return out


def build_theorem_context_pack(row: dict[str, Any]) -> TheoremContextPack:
    theorem_name = str(row.get("theorem_name", "")).strip()
    definitions = _collect_text_values(
        row.get("definitions", row.get("local_definitions", row.get("definition_context", [])))
    )
    notations = _collect_text_values(row.get("notations", row.get("notation", row.get("symbol_table", []))))
    context_terms = _collect_text_values(
        row.get("context_pack", row.get("text_context", row.get("proof_context", [])))
    )
    if theorem_name:
        context_terms.append(theorem_name)
    lean_stmt = str(row.get("lean_statement", "")).strip()
    if lean_stmt:
        context_terms.append(lean_stmt)

    # Deduplicate while preserving order.
    def _uniq(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for it in items:
            key = it.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(key)
        return out

    return TheoremContextPack(
        theorem_name=theorem_name,
        definitions=_uniq(definitions)[:20],
        notations=_uniq(notations)[:20],
        context_terms=_uniq(context_terms)[:30],
    )


def decompose_assumption_payload(*, lean_expr: str, lean_statement: str, label: str) -> dict[str, Any]:
    expr = (_extract_type_from_assumption_expr(lean_expr) or lean_expr or "").strip()
    stmt = (lean_statement or "").strip()
    source = stmt if stmt else expr
    objects = sorted({t for t in _norm_tokens(source) if not t.startswith("h_") and t not in {"theorem", "lemma"}})
    assumptions: list[str] = []
    claim = source
    if "→" in source:
        parts = [p.strip() for p in source.split("→") if p.strip()]
        if len(parts) >= 2:
            assumptions = parts[:-1]
            claim = parts[-1]
    lemma_plan = [f"normalize_slot:{label or 'unknown'}", "prove_claim_from_assumptions"]
    return {
        "objects": objects[:12],
        "assumptions": assumptions[:8],
        "claim": claim,
        "lemma_plan": lemma_plan,
    }


def _semantic_failures(*, lean_expr: str, lean_statement: str, slot_name: str) -> list[str]:
    out: list[str] = []
    expr = (_extract_type_from_assumption_expr(lean_expr) or lean_expr or "").strip()
    stmt = (lean_statement or "").strip()
    if not slot_name:
        out.append("semantic_slot_mismatch")
    if expr and stmt:
        expr_tokens = _norm_tokens(expr)
        stmt_tokens = _norm_tokens(stmt)
        if expr_tokens and stmt_tokens:
            overlap = len(expr_tokens.intersection(stmt_tokens))
            if overlap / max(1, len(expr_tokens)) < 0.25:
                out.append("semantic_symbol_drift")
        if bool(_QUANT_RE.search(expr)) and not bool(_QUANT_RE.search(stmt)):
            out.append("semantic_quantifier_loss")
    return out


def _load_memory(path: Path | None) -> dict[str, Any]:
    p = Path(path or _DEFAULT_MEMORY_PATH)
    if not p.exists():
        return {"theorems": {}}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"theorems": {}}
    if not isinstance(raw, dict):
        return {"theorems": {}}
    theorems = raw.get("theorems")
    if not isinstance(theorems, dict):
        raw["theorems"] = {}
    return raw


def _save_memory(path: Path | None, payload: dict[str, Any]) -> None:
    p = Path(path or _DEFAULT_MEMORY_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _memory_boost(theorem_name: str, path: Path | None) -> float:
    mem = _load_memory(path)
    rec = (mem.get("theorems", {}) or {}).get(theorem_name, {})
    attempts = int(rec.get("attempts", 0))
    success = int(rec.get("success", 0))
    if attempts <= 0:
        return 0.0
    rate = success / max(1, attempts)
    return min(0.25, 0.25 * rate)


def _update_memory_attempts(*, theorem_names: list[str], success: bool, path: Path | None) -> None:
    if not theorem_names:
        return
    mem = _load_memory(path)
    t = mem.setdefault("theorems", {})
    changed = False
    for name in theorem_names:
        if not name:
            continue
        rec = t.setdefault(name, {"attempts": 0, "success": 0})
        rec["attempts"] = int(rec.get("attempts", 0)) + 1
        if success:
            rec["success"] = int(rec.get("success", 0)) + 1
        changed = True
    if changed:
        _save_memory(path, mem)


def _classify_lean_error(error: str) -> str:
    e = (error or "").lower()
    if "type mismatch" in e:
        return "lean_type_mismatch"
    if "failed to synthesize" in e or "instance" in e:
        return "lean_typeclass"
    if "unsolved goals" in e:
        return "lean_unsolved_goals"
    if "unknown identifier" in e:
        return "lean_unknown_identifier"
    if "timeout" in e:
        return "lean_timeout"
    return "lean_other"


def _repair_hint(error_class: str) -> str:
    mapping = {
        "lean_type_mismatch": "align binder types and proposition shape with the original assumption",
        "lean_typeclass": "add missing decidable/class constraints explicitly in signature",
        "lean_unsolved_goals": "split implication and state intermediate lemma before final claim",
        "lean_unknown_identifier": "replace unknown symbol with locally declared object from context pack",
        "lean_timeout": "simplify theorem header and avoid heavy automation in one step",
    }
    return mapping.get(error_class, "preserve slot mapping and minimize syntax complexity")


def _default_tactic_for_goal(lean_statement: str) -> str:
    stmt = (lean_statement or "").lower()
    if any(tok in stmt for tok in ["≤", "<", "≥", ">", "+", "-", "*", "/"]):
        return "first | omega | nlinarith | linarith | aesop"
    if any(tok in stmt for tok in ["=", "↔", "→"]):
        return "first | aesop | simp | exact?"
    return "first | aesop | simp | trivial"


def _error_class_tactic_candidates(error_class: str, lean_statement: str) -> list[str]:
    stmt = (lean_statement or "").lower()
    arithmetic = any(tok in stmt for tok in ["≤", "<", "≥", ">", "+", "-", "*", "/"])
    if error_class == "lean_type_mismatch":
        return [
            "first | refine ?_ | aesop",
            "first | intro; aesop",
            "first | simp at *; aesop",
        ]
    if error_class == "lean_typeclass":
        return [
            "first | classical; aesop",
            "first | classical; simp at *; aesop",
            "first | exact?",
        ]
    if error_class == "lean_unsolved_goals":
        return [
            "first | constructor <;> aesop",
            "first | intro; aesop",
            "first | have h := by aesop; exact h",
        ]
    if error_class == "lean_unknown_identifier":
        return [
            "first | simp at *; aesop",
            "first | aesop",
            "first | exact?",
        ]
    if error_class == "lean_timeout":
        return [
            "first | simp",
            "first | exact?",
            "first | aesop",
        ]
    if arithmetic:
        return [
            "first | omega | nlinarith | linarith | aesop",
            "first | ring_nf; nlinarith",
            "first | positivity",
        ]
    return [
        "first | aesop | simp | exact?",
        "first | intro; aesop",
        _default_tactic_for_goal(lean_statement),
    ]


def _build_proof_plan(
    *,
    lean_statement: str,
    error_class: str,
    context_pack: TheoremContextPack | None,
    decomposition: dict[str, Any] | None,
) -> list[str]:
    phases = ["normalize_goal"]
    objects = ((decomposition or {}).get("objects", []) or []) if isinstance(decomposition, dict) else []
    if objects:
        phases.append("bind_local_objects")
    assumptions = ((decomposition or {}).get("assumptions", []) or []) if isinstance(decomposition, dict) else []
    if assumptions:
        phases.append("introduce_assumptions")
    if error_class in {"lean_unsolved_goals", "lean_type_mismatch"}:
        phases.append("derive_intermediate_lemma")
    if error_class == "lean_unknown_identifier":
        phases.append("repair_symbol_binding")
    if error_class == "lean_typeclass":
        phases.append("insert_classical_or_instance_constraints")
    if error_class == "lean_timeout":
        phases.append("simplify_goal")
    phases.append("close_goal")
    # Keep short, deterministic plans.
    return phases[:6]


def _mini_lemma_chain_tactics(lean_statement: str) -> list[str]:
    """Return explicit have-chain tactics for conjunction/implication goals."""
    claim = _extract_statement_claim(lean_statement) or (lean_statement or "").strip()
    if not claim:
        return []
    out: list[str] = []
    # For conjunction goals, try explicit per-branch intermediate lemmas.
    if "∧" in claim:
        conj_parts = [p.strip() for p in claim.split("∧") if p.strip()]
        if len(conj_parts) >= 2:
            left = conj_parts[0]
            right = conj_parts[1]
            out.append(
                "\n".join(
                    [
                        "constructor",
                        f"· have h1 : {left} := by",
                        "    first | aesop | simp at *",
                        "  exact h1",
                        f"· have h2 : {right} := by",
                        "    first | aesop | simp at *",
                        "  exact h2",
                    ]
                )
            )
    # For implication goals, make premise explicit and chain into final goal.
    if "→" in claim:
        imp_parts = [p.strip() for p in claim.split("→") if p.strip()]
        if len(imp_parts) >= 2:
            premise = imp_parts[0]
            conclusion = imp_parts[-1]
            out.append(
                "\n".join(
                    [
                        "intro hPrem",
                        f"have h1 : {premise} := by",
                        "  exact hPrem",
                        f"have h2 : {conclusion} := by",
                        "  first | aesop | simp at *",
                        "exact h2",
                    ]
                )
            )
    return out[:2]


def _synthesize_tactics_from_plan(
    *,
    plan: list[str],
    error_class: str,
    lean_statement: str,
) -> list[str]:
    base = _error_class_tactic_candidates(error_class, lean_statement)
    extra: list[str] = _mini_lemma_chain_tactics(lean_statement)
    claim = _extract_statement_claim(lean_statement) or (lean_statement or "").strip()
    if "∧" in claim:
        extra.append("constructor <;> first | aesop | simp")
    if "→" in claim:
        extra.append("intro h; first | aesop | simp at *")
    if "↔" in claim:
        extra.append("constructor <;> intro h <;> first | aesop | simp at *")
    if "∃" in claim:
        extra.append("refine ⟨?_, ?_⟩ <;> first | aesop | simp")
    if "derive_intermediate_lemma" in plan:
        extra.append("first | have h_aux := by aesop; exact h_aux")
    if "introduce_assumptions" in plan:
        extra.append("first | intro; intro; aesop")
    if "insert_classical_or_instance_constraints" in plan:
        extra.append("first | classical; aesop")
    if "simplify_goal" in plan:
        extra.append("first | simp at *")
    # De-dup preserve order.
    out: list[str] = []
    seen: set[str] = set()
    for t in [*extra, *base]:
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out[:5]


def _call_callback(cb: Any, /, **kwargs: Any) -> Any:
    try:
        sig = inspect.signature(cb)
        accepted = {
            k: v for k, v in kwargs.items() if k in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )
        }
    except Exception:
        accepted = kwargs
    return cb(**accepted)


def _write_failure_artifact(
    *,
    root: Path | None,
    run_id: str,
    target_theorem: str,
    slot_index: int,
    slot_name: str,
    diag: dict[str, Any],
    taxonomy: str,
) -> None:
    out_root = Path(root or _DEFAULT_ARTIFACT_ROOT)
    out_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "target_theorem": target_theorem,
        "slot_index": slot_index,
        "slot_name": slot_name,
        "taxonomy": taxonomy,
        "timestamp_unix": int(time.time()),
        "diagnostic": diag,
    }
    fp = out_root / f"{time.strftime('%Y%m%d', time.gmtime())}.jsonl"
    with fp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _contextualize_assumption_query(assumption_expr: str, context_pack: TheoremContextPack | None) -> str:
    query = (assumption_expr or "").strip()
    if context_pack is None:
        return query
    extras = [*context_pack.definitions[:4], *context_pack.notations[:4], *context_pack.context_terms[:4]]
    extra = " ".join(x for x in extras if x)
    if not extra:
        return query
    return f"{query} {extra}".strip()


def _build_template_candidates(
    *,
    assumption_expr: str,
    max_candidates: int,
    context_pack: TheoremContextPack | None,
) -> list[BridgeCandidate]:
    expr = (assumption_expr or "").strip()
    if not expr or not _looks_proposition(expr):
        return []

    def_tokens = (context_pack.definitions if context_pack else [])[:2]
    note_tokens = (context_pack.notations if context_pack else [])[:2]
    ctx = [*def_tokens, *note_tokens]
    suffix = f" -- ctx: {'; '.join(ctx)}" if ctx else ""

    out: list[BridgeCandidate] = []
    for i in range(max(1, max_candidates)):
        name = f"template_bridge_{i+1}"
        stmt = synthesize_actionable_goal(
            lean_expr=expr,
            lean_statement="",
            label=name,
        )
        if not stmt:
            stmt = f"theorem {name} : {expr}{suffix}"
        if not _candidate_is_actionable(stmt):
            continue
        out.append(
            BridgeCandidate(
                theorem_name=name,
                paper_id="context_pack",
                status="TEMPLATE",
                score=max(0.01, 0.07 - (0.01 * i)),
                lean_statement=stmt,
                actionable=_candidate_is_actionable(stmt),
            )
        )
    return out


def _candidate_compile_sane(statement: str) -> bool:
    if not _candidate_is_actionable(statement):
        return False
    prop = _extract_statement_claim(statement)
    if not prop:
        return False
    return _looks_proposition(prop) and not _is_placeholder_atom(prop) and _strict_theorem_shape_ok(statement)


def _candidate_is_actionable(lean_statement: str) -> bool:
    stmt = (lean_statement or "").strip()
    if not stmt:
        return False
    if ":" not in stmt:
        return False
    if not any(k in stmt for k in ("theorem ", "lemma ")):
        return False
    if "sorry" in stmt:
        return False
    return True


def _extract_hint_candidates(row: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    assumptions = row.get("assumptions", [])
    if not isinstance(assumptions, list):
        return hints
    for a in assumptions:
        if not isinstance(a, dict):
            continue
        src = str(a.get("grounding_source", "")).strip()
        if src.startswith("bridge_candidate:"):
            name = src.split(":", 1)[1].strip()
            if name:
                hints.append(name)
    return hints


def suggest_bridge_candidates(
    *,
    assumption_expr: str,
    ledger_root: Path,
    max_candidates: int = 3,
    context_pack: TheoremContextPack | None = None,
    allow_template_fallback: bool = False,
    retrieval_memory_path: Path | None = None,
) -> list[BridgeCandidate]:
    """Suggest candidate theorem bridges ranked by semantic similarity.

    Uses embedding-based retrieval (sentence-transformers via PremiseRetriever)
    when available, falling back to token-overlap scoring otherwise.
    Candidates are sourced from FULLY_PROVEN or INTERMEDIARY_PROVEN entries.
    """
    raw_assumption_expr = (assumption_expr or "").strip()
    if not raw_assumption_expr:
        return []
    assumption_expr = _contextualize_assumption_query(raw_assumption_expr, context_pack)

    ledger_root = Path(ledger_root)
    if not ledger_root.exists():
        return []

    # Collect eligible entries with their metadata.
    eligible: list[tuple[str, str, str, str]] = []  # (theorem_name, paper_id, status, statement)
    for paper_id, row in _iter_ledger_entries(ledger_root):
        status = str(row.get("status", ""))
        if status not in {"FULLY_PROVEN", "INTERMEDIARY_PROVEN"}:
            continue
        theorem_name = str(row.get("theorem_name", "")).strip()
        statement = str(row.get("lean_statement", "")).strip()
        if not theorem_name:
            continue
        if not _candidate_is_actionable(statement):
            continue
        eligible.append((theorem_name, paper_id, status, statement))

    if not eligible:
        if allow_template_fallback:
            return _build_template_candidates(
                assumption_expr=raw_assumption_expr,
                max_candidates=max_candidates,
                context_pack=context_pack,
            )
        return []

    # Embedding path: build a tiny PremiseRetriever over the eligible entries.
    if _HAS_RETRIEVAL:
        try:
            entries = [
                PremiseEntry(name=name, statement=stmt or name, namespace="", source_file=paper_id)
                for name, paper_id, _status, stmt in eligible
            ]
            retriever = PremiseRetriever.build(entries, encoder_name=None)
            hits = retriever.query(assumption_expr, top_k=max(1, max_candidates))
            # Map hits back to BridgeCandidate using the eligible index.
            name_to_meta = {name: (paper_id, status) for name, paper_id, status, _ in eligible}
            scored: list[BridgeCandidate] = []
            for hit in hits:
                meta = name_to_meta.get(hit.name)
                if meta is None:
                    continue
                stmt = next((s for n, _p, _st, s in eligible if n == hit.name), "")
                if not _candidate_compile_sane(stmt):
                    continue
                scored.append(
                    BridgeCandidate(
                        theorem_name=hit.name,
                        paper_id=meta[0],
                        status=meta[1],
                        score=float(hit.score) + _memory_boost(hit.name, retrieval_memory_path),
                        lean_statement=stmt,
                        actionable=True,
                    )
                )
            scored = [c for c in scored if c.actionable]
            if scored:
                return scored[: max(1, max_candidates)]
        except Exception as exc:
            logger.debug("Embedding retrieval failed, falling back to token overlap: %s", exc)

    # Token-overlap fallback.
    a_tokens = _norm_tokens(assumption_expr)
    if not a_tokens:
        return []

    fallback: list[BridgeCandidate] = []
    for theorem_name, paper_id, status, statement in eligible:
        t_tokens = _norm_tokens(theorem_name + " " + statement)
        if not t_tokens:
            continue
        overlap = len(a_tokens.intersection(t_tokens))
        if overlap == 0:
            continue
        score = overlap / max(1.0, len(a_tokens))
        if not _candidate_compile_sane(statement):
            continue
        fallback.append(
            BridgeCandidate(
                theorem_name=theorem_name,
                paper_id=paper_id,
                status=status,
                score=float(score) + _memory_boost(theorem_name, retrieval_memory_path),
                lean_statement=statement,
                actionable=True,
            )
        )

    fallback.sort(key=lambda c: c.score, reverse=True)
    if fallback:
        return fallback[: max(1, max_candidates)]
    if allow_template_fallback:
        return _build_template_candidates(
            assumption_expr=raw_assumption_expr,
            max_candidates=max_candidates,
            context_pack=context_pack,
        )
    return []


def build_bridge_plan(
    *,
    assumption_expr: str,
    ledger_root: Path,
    max_candidates: int = 3,
    retrieval_memory_path: Path | None = None,
) -> BridgePlan:
    return BridgePlan(
        assumption_expr=assumption_expr,
        candidates=suggest_bridge_candidates(
            assumption_expr=assumption_expr,
            ledger_root=ledger_root,
            max_candidates=max_candidates,
            retrieval_memory_path=retrieval_memory_path,
        ),
    )


# ---------------------------------------------------------------------------
# Z3 entailment checker
# ---------------------------------------------------------------------------

# Patterns that suggest linear arithmetic goals Z3 can handle.
_ARITH_PATTERNS = re.compile(
    r"\b(?:le|lt|ge|gt|add|sub|mul|div|mod|abs|min|max|"
    r"(?:\d+\s*[+\-*/<>=≤≥≠]+\s*\d+)|"
    r"(?:[a-z]\s*[+\-*/<>=≤≥≠]+\s*[a-z0-9]))\b",
    re.IGNORECASE,
)

# Lean → Python operator translation for simple linear expressions.
_LEAN_OP = {
    "≤": "<=", "≥": ">=", "≠": "!=",
    "∧": "and", "∨": "or", "¬": "not ",
}


def _lean_expr_to_z3_str(lean_expr: str) -> str:
    result = lean_expr
    for lean, z3op in _LEAN_OP.items():
        result = result.replace(lean, z3op)
    # Strip Lean type ascriptions: (x : ℕ) → x
    result = re.sub(r"\(\s*\w+\s*:\s*[\w ℕℤℝ]+\s*\)", "", result)
    return result.strip()


def _build_z3_formula(z3_str: str, var_scope: "dict[str, Any]", z3: "Any") -> "Any":
    """Parse ``z3_str`` into a Z3 formula without using eval().

    Handles the restricted grammar produced by ``_lean_expr_to_z3_str``:
      - Integer/variable atoms
      - Arithmetic: +, -, *, // (int division), %
      - Comparison: <, <=, >, >=, ==, !=
      - Boolean connectives: and, or, not
      - Parenthesised sub-expressions

    Raises ``ValueError`` on unrecognised tokens.
    """
    import ast as _ast

    # Use Python's own AST parser — it produces a safe tree (no eval).
    # We then walk the tree and build Z3 expressions node by node.
    try:
        tree = _ast.parse(z3_str.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Cannot parse z3 expression: {exc}") from exc

    def _visit(node: "_ast.expr") -> "Any":
        if isinstance(node, _ast.BoolOp):
            ops = [_visit(v) for v in node.values]
            if isinstance(node.op, _ast.And):
                return z3.And(*ops)
            if isinstance(node.op, _ast.Or):
                return z3.Or(*ops)
        if isinstance(node, _ast.UnaryOp) and isinstance(node.op, _ast.Not):
            return z3.Not(_visit(node.operand))
        if isinstance(node, _ast.Compare):
            left = _visit(node.left)
            result = None
            prev = left
            for op, comp in zip(node.ops, node.comparators):
                right = _visit(comp)
                if isinstance(op, _ast.Lt):
                    clause = prev < right
                elif isinstance(op, _ast.LtE):
                    clause = prev <= right
                elif isinstance(op, _ast.Gt):
                    clause = prev > right
                elif isinstance(op, _ast.GtE):
                    clause = prev >= right
                elif isinstance(op, _ast.Eq):
                    clause = prev == right
                elif isinstance(op, _ast.NotEq):
                    clause = prev != right
                else:
                    raise ValueError(f"Unsupported comparison: {type(op).__name__}")
                result = clause if result is None else z3.And(result, clause)
                prev = right
            return result
        if isinstance(node, _ast.BinOp):
            left, right = _visit(node.left), _visit(node.right)
            if isinstance(node.op, _ast.Add):
                return left + right
            if isinstance(node.op, _ast.Sub):
                return left - right
            if isinstance(node.op, _ast.Mult):
                return left * right
            if isinstance(node.op, _ast.FloorDiv):
                return left / right  # z3 Int division
            if isinstance(node.op, _ast.Mod):
                return left % right
            raise ValueError(f"Unsupported binary op: {type(node.op).__name__}")
        if isinstance(node, _ast.UnaryOp) and isinstance(node.op, _ast.USub):
            return -_visit(node.operand)
        if isinstance(node, _ast.Name):
            if node.id in var_scope:
                return var_scope[node.id]
            raise ValueError(f"Unknown variable: {node.id!r}")
        if isinstance(node, _ast.Constant) and isinstance(node.value, int):
            return z3.IntVal(node.value)
        raise ValueError(f"Unsupported AST node: {type(node).__name__}")

    return _visit(tree.body)


def check_entailment_z3(assumption_expr: str) -> EntailmentResult:
    """Attempt to verify a simple arithmetic assumption using Z3.

    Returns an EntailmentResult.  ``method`` is "z3" on success,
    "unverified" when Z3 cannot handle the expression or is not installed.
    """
    import time
    t0 = time.time()

    try:
        import z3  # type: ignore[import]
    except ImportError:
        return EntailmentResult(
            assumption_expr=assumption_expr,
            method="unverified",
            entailed=False,
            error="z3-solver not installed (pip install z3-solver)",
            elapsed_s=round(time.time() - t0, 3),
        )

    # Heuristic: skip if the expression doesn't look arithmetic.
    if not _ARITH_PATTERNS.search(assumption_expr):
        return EntailmentResult(
            assumption_expr=assumption_expr,
            method="unverified",
            entailed=False,
            error="expression does not appear to be linear arithmetic",
            elapsed_s=round(time.time() - t0, 3),
        )

    try:
        z3_str = _lean_expr_to_z3_str(assumption_expr)
        # Declare free integer variables found in the expression.
        var_names = set(re.findall(r"\b([a-z][a-z0-9_]*)\b", z3_str))
        var_names.discard("and")
        var_names.discard("or")
        var_names.discard("not")
        scope: dict[str, Any] = {}
        for vname in var_names:
            scope[vname] = z3.Int(vname)

        formula = _build_z3_formula(z3_str, scope, z3)
        solver = z3.Solver()
        solver.add(z3.Not(formula))
        status = solver.check()

        if status == z3.unsat:
            return EntailmentResult(
                assumption_expr=assumption_expr,
                method="z3",
                entailed=True,
                elapsed_s=round(time.time() - t0, 3),
            )
        elif status == z3.sat:
            model = solver.model()
            cex = str(model)
            return EntailmentResult(
                assumption_expr=assumption_expr,
                method="z3",
                entailed=False,
                counterexample=cex,
                elapsed_s=round(time.time() - t0, 3),
            )
        else:
            return EntailmentResult(
                assumption_expr=assumption_expr,
                method="unverified",
                entailed=False,
                error="z3 returned unknown",
                elapsed_s=round(time.time() - t0, 3),
            )
    except Exception as exc:
        return EntailmentResult(
            assumption_expr=assumption_expr,
            method="unverified",
            entailed=False,
            error=f"z3 eval failed: {exc}",
            elapsed_s=round(time.time() - t0, 3),
        )


# ---------------------------------------------------------------------------
# Lean REPL execution for bridge proofs
# ---------------------------------------------------------------------------

def _build_lean_bridge_script(
    lean_statement: str,
    tactic_proof: str,
    imports: list[str] | None = None,
    prelude: str = "",
) -> str:
    """Build a minimal Lean 4 file that checks a bridge proof."""
    default_imports = [
        # Use broad Mathlib scope so hard goals with varied namespaces
        # fail on semantics, not missing imports.
        "import Mathlib",
    ]
    header = "\n".join(imports or default_imports)
    body_prelude = (prelude or "").strip()
    prelude_block = f"{body_prelude}\n\n" if body_prelude else ""
    return f"""{header}

-- DESol bridge proof check
{prelude_block}{lean_statement} := by
  {tactic_proof}
"""


def execute_bridge_proof_lean(
    lean_statement: str,
    tactic_proof: str,
    *,
    context_pack: TheoremContextPack | None = None,
    decomposition: dict[str, Any] | None = None,
    timeout_s: int = 60,
    lake_exe: str = "lake",
    lean_exe: str = "lean",
    project_root: Path | None = None,
) -> EntailmentResult:
    """Attempt to verify a bridge proof by invoking `lean --run` on a temp file.

    This is a lightweight check that does not require a full LeanDojo session.
    Suitable for bridge proofs that fit in a single tactic block.

    Returns:
        EntailmentResult with method="lean_repl" on success.
    """
    import time
    t0 = time.time()

    bundle = compile_theorem_context_bundle(
        lean_statement=lean_statement,
        context_pack=context_pack,
        decomposition=decomposition,
    )
    script = _build_lean_bridge_script(
        lean_statement,
        tactic_proof,
        imports=[str(x) for x in (bundle.get("imports", []) or [])],
        prelude=str(bundle.get("prelude", "")),
    )

    tmp_dir = (project_root / "Desol") if project_root is not None else None
    if tmp_dir is not None:
        tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        suffix=".lean", mode="w", encoding="utf-8", delete=False,
        dir=str(tmp_dir) if tmp_dir is not None else None,
        prefix="_tmp_bridge_",
    ) as f:
        f.write(script)
        tmp_path = f.name

    try:
        cwd = str(project_root) if project_root is not None else None
        # Prefer project-aware execution so Mathlib/toolchain context is correct.
        cmd = [lake_exe, "env", lean_exe, tmp_path]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=cwd,
        )
        if proc.returncode != 0 and ("unknown package 'Mathlib'" in (proc.stderr or "") or "command not found" in (proc.stderr or "").lower()):
            # Fallback to raw lean if lake env fails due local setup.
            proc = subprocess.run(
                [lean_exe, tmp_path],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=cwd,
            )
        elapsed = round(time.time() - t0, 3)
        if proc.returncode == 0 and not proc.stderr.strip():
            return EntailmentResult(
                assumption_expr=lean_statement,
                method="lean_repl",
                entailed=True,
                elapsed_s=elapsed,
            )
        else:
            err = (proc.stderr or proc.stdout or "").strip()[:500]
            return EntailmentResult(
                assumption_expr=lean_statement,
                method="lean_repl",
                entailed=False,
                error=err,
                elapsed_s=elapsed,
            )
    except subprocess.TimeoutExpired:
        return EntailmentResult(
            assumption_expr=lean_statement,
            method="lean_repl",
            entailed=False,
            error=f"lean timed out after {timeout_s}s",
            elapsed_s=round(time.time() - t0, 3),
        )
    except FileNotFoundError:
        return EntailmentResult(
            assumption_expr=lean_statement,
            method="lean_repl",
            entailed=False,
            error="lean/lake executable not found; install Lean 4 toolchain",
            elapsed_s=round(time.time() - t0, 3),
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Full bridge execution pipeline
# ---------------------------------------------------------------------------

def execute_bridge_chain(
    *,
    target_theorem: str,
    ledger_root: Path,
    proof_callback: Any | None = None,
    lean_timeout_s: int = 60,
    use_z3: bool = True,
    use_lean: bool = True,
    max_depth: int = 2,
    max_candidates_per_step: int = 3,
    require_assumption_slot_coverage: bool = False,
    require_context_pack: bool = False,
    min_context_items: int = 2,
    max_repair_rounds: int = 2,
    repair_callback: Any | None = None,
    failure_artifact_root: Path | None = None,
    run_id: str = "",
    retrieval_memory_path: Path | None = None,
) -> BridgeExecutionResult:
    """Run the bridge-proof pipeline for a target theorem.

    For each ungrounded assumption of ``target_theorem``:
      1. Try Z3 (if use_z3=True and assumption looks arithmetic).
      2. Try Lean REPL with a tactic proof from ``proof_callback`` (if provided).
      3. Fall back to planning-only (token-overlap candidates).

    Assumptions that are confirmed by Z3 or Lean are added to
    ``newly_grounded``; the rest stay in ``still_ungrounded``.

    Args:
        target_theorem: Name of the theorem with ungrounded assumptions.
        ledger_root: Root of the verification ledger directory.
        proof_callback: Optional ``(lean_statement: str) -> str`` that returns a
            tactic proof string to try.  Typically wraps the ponder loop.
        lean_timeout_s: Timeout for each Lean REPL check.
        use_z3: Whether to attempt Z3 entailment.
        use_lean: Whether to attempt Lean REPL execution.
        max_depth: Depth for bridge chain planning.
        max_candidates_per_step: Branching factor for chain planning.

    Returns:
        BridgeExecutionResult summarising what was grounded.
    """
    ledger_root = Path(ledger_root)
    chain_plan = collect_bridge_retry_targets(
        target_theorem=target_theorem,
        ledger_root=ledger_root,
        max_depth=max_depth,
        max_candidates_per_step=max_candidates_per_step,
        retrieval_memory_path=retrieval_memory_path,
    )

    index = _load_ledger_index(ledger_root)
    row = index.get(target_theorem, {})
    assumptions = row.get("assumptions", [])
    if not isinstance(assumptions, list):
        assumptions = []

    entailment_results: list[EntailmentResult] = []
    newly_grounded: list[str] = []
    still_ungrounded: list[str] = []
    failure_reasons: dict[str, int] = {}
    assumption_diagnostics: list[dict[str, Any]] = []
    repair_attempts_total = 0
    repair_success_count = 0

    def _bump(reason: str) -> None:
        failure_reasons[reason] = int(failure_reasons.get(reason, 0)) + 1

    if not assumptions:
        _bump("no_assumptions")
    context_pack = build_theorem_context_pack(row)
    if require_context_pack:
        context_items = (
            len(context_pack.definitions)
            + len(context_pack.notations)
            + len(context_pack.context_terms)
        )
        if context_items < max(1, int(min_context_items)):
            _bump("context_pack_insufficient")
            return BridgeExecutionResult(
                target_theorem=target_theorem,
                chain_plan=chain_plan,
                entailment_results=entailment_results,
                newly_grounded=newly_grounded,
                still_ungrounded=still_ungrounded,
                failure_reasons=failure_reasons,
                assumption_diagnostics=[
                    {
                        "reason": "context_pack_insufficient",
                        "required_min_context_items": max(1, int(min_context_items)),
                        "found_context_items": context_items,
                    }
                ],
                repair_attempts_total=repair_attempts_total,
                repair_success_count=repair_success_count,
            )
    active_run_id = run_id or f"bridge_{uuid.uuid4().hex[:12]}"

    for idx, a in enumerate(assumptions):
        if not isinstance(a, dict):
            _bump("assumption_not_dict")
            continue
        grounding = str(a.get("grounding", "")).upper()
        if grounding not in {"UNGROUNDED", "UNKNOWN", ""}:
            _bump("assumption_already_grounded")
            continue

        raw_lean_expr = str(a.get("lean_expr", "") or a.get("label", "")).strip()
        lean_expr = raw_lean_expr
        slot_name = extract_assumption_slot_name(
            lean_expr=raw_lean_expr,
            label=str(a.get("label", "")).strip(),
            idx=idx,
        )
        lean_stmt = synthesize_actionable_goal(
            lean_expr=raw_lean_expr,
            lean_statement=str(a.get("lean_statement", "")).strip(),
            label=slot_name,
        )
        if not lean_expr and not lean_stmt:
            _bump("empty_assumption_payload")
            empty_diag = {"reason": "empty_assumption_payload", "lean_expr": "", "lean_statement": ""}
            assumption_diagnostics.append(empty_diag)
            _write_failure_artifact(
                root=failure_artifact_root,
                run_id=active_run_id,
                target_theorem=target_theorem,
                slot_index=idx,
                slot_name="",
                diag=empty_diag,
                taxonomy=_FAIL_TAXONOMY.get("candidate_generation_empty", "unknown"),
            )
            continue

        grounded = False
        diag: dict[str, Any] = {
            "slot_name": slot_name,
            "lean_expr": lean_expr,
            "lean_statement": lean_stmt,
            "lane": "goal",
            "decomposition": decompose_assumption_payload(
                lean_expr=lean_expr,
                lean_statement=lean_stmt,
                label=slot_name,
            ),
            "semantic_failures": [],
            "repair_attempts": [],
            "z3": "skipped",
            "lean": "skipped",
            "candidate_count": 0,
            "final": "ungrounded",
        }
        for sf in _semantic_failures(lean_expr=lean_expr, lean_statement=lean_stmt, slot_name=slot_name):
            _bump(sf)
            diag["semantic_failures"].append(sf)
        if not _goal_lane_allowed(lean_expr=lean_expr, lean_statement=lean_stmt):
            diag["lane"] = "context"
            diag["final"] = "context_only"
            _bump("context_only_assumption")
            assumption_diagnostics.append(diag)
            continue
        if require_assumption_slot_coverage and not slot_name:
            _bump("assumption_slot_unmapped")
            diag["final"] = "slot_unmapped"
            assumption_diagnostics.append(diag)
            still_ungrounded.append(lean_expr or lean_stmt)
            taxonomy = _FAIL_TAXONOMY.get("assumption_slot_unmapped", "unknown")
            _write_failure_artifact(
                root=failure_artifact_root,
                run_id=active_run_id,
                target_theorem=target_theorem,
                slot_index=idx,
                slot_name=slot_name,
                diag=diag,
                taxonomy=taxonomy,
            )
            continue

        # Step 1: Z3 entailment.
        if use_z3 and lean_expr:
            er = check_entailment_z3(lean_expr)
            entailment_results.append(er)
            if er.entailed:
                newly_grounded.append(lean_expr)
                grounded = True
                logger.info("Z3 grounded: %s", lean_expr[:80])
                diag["z3"] = "entailed"
            else:
                if er.method == "unverified":
                    diag["z3"] = "unverified"
                    emsg = (er.error or "").lower()
                    if "not installed" in emsg:
                        _bump("z3_unavailable")
                    elif "linear arithmetic" in emsg:
                        _bump("z3_non_arithmetic_expr")
                    else:
                        _bump("z3_unverified_other")
                else:
                    diag["z3"] = "not_entailed"
                    if er.counterexample:
                        _bump("z3_counterexample")
                    else:
                        _bump("z3_not_entailed_other")
        elif use_z3 and not lean_expr:
            _bump("z3_missing_expression")
            diag["z3"] = "missing_expression"

        # Step 2: Lean REPL.
        if not grounded and use_lean and lean_stmt:
            current_stmt = lean_stmt
            last_error = ""
            last_error_class = "lean_other"
            for attempt_idx in range(max(0, int(max_repair_rounds)) + 1):
                proof_plan = _build_proof_plan(
                    lean_statement=current_stmt,
                    error_class=last_error_class,
                    context_pack=context_pack,
                    decomposition=diag.get("decomposition") if isinstance(diag.get("decomposition"), dict) else None,
                )
                tactic_candidates: list[str] = []
                if proof_callback is not None:
                    try:
                        produced = _call_callback(
                            proof_callback,
                            lean_statement=current_stmt,
                            round_idx=attempt_idx,
                            last_error=last_error,
                            last_error_class=last_error_class,
                            proof_plan=proof_plan,
                            context_pack=context_pack,
                            decomposition=diag["decomposition"],
                        )
                        if isinstance(produced, str) and produced.strip():
                            tactic_candidates = [produced.strip()]
                        elif isinstance(produced, list):
                            tactic_candidates = [str(x).strip() for x in produced if str(x).strip()]
                    except Exception as exc:
                        logger.debug("proof_callback failed: %s", exc)
                        _bump("lean_callback_error")
                        diag["lean"] = "callback_error"
                        break
                if not tactic_candidates:
                    tactic_candidates = _synthesize_tactics_from_plan(
                        plan=proof_plan,
                        error_class=last_error_class,
                        lean_statement=current_stmt,
                    )
                if not tactic_candidates:
                    _bump("lean_no_tactic_proof")
                    diag["lean"] = "no_tactic_proof"
                    break

                attempt_succeeded = False
                for tactic_proof in tactic_candidates:
                    if not tactic_proof or tactic_proof == "sorry":
                        continue
                    er = execute_bridge_proof_lean(
                        current_stmt,
                        tactic_proof,
                        context_pack=context_pack,
                        decomposition=diag.get("decomposition") if isinstance(diag, dict) else None,
                        timeout_s=int(lean_timeout_s),
                        project_root=Path("."),
                    )
                    entailment_results.append(er)
                    this_error_class = _classify_lean_error(er.error)
                    diag["repair_attempts"].append(
                        {
                            "round_idx": attempt_idx,
                            "proof_plan": proof_plan,
                            "tactic_proof": tactic_proof[:240],
                            "entailed": bool(er.entailed),
                            "error_class": this_error_class,
                        }
                    )
                    repair_attempts_total += 1
                    if er.entailed:
                        newly_grounded.append(current_stmt)
                        grounded = True
                        repair_success_count += 1
                        logger.info("Lean REPL grounded: %s", current_stmt[:80])
                        diag["lean"] = "entailed"
                        attempt_succeeded = True
                        break
                    diag["lean"] = "failed"
                    emsg = (er.error or "").lower()
                    if "timed out" in emsg:
                        _bump("lean_timeout")
                    elif "executable not found" in emsg:
                        _bump("lean_unavailable")
                    else:
                        _bump("lean_proof_failed")
                    last_error = er.error or ""
                    last_error_class = this_error_class
                if attempt_succeeded:
                    break
                if attempt_idx >= int(max_repair_rounds):
                    break
                if repair_callback is not None:
                    try:
                        repaired = _call_callback(
                            repair_callback,
                            lean_statement=current_stmt,
                            error=last_error,
                            error_class=_classify_lean_error(last_error),
                            hint=_repair_hint(_classify_lean_error(last_error)),
                            round_idx=attempt_idx + 1,
                            context_pack=context_pack,
                            decomposition=diag["decomposition"],
                        )
                    except Exception as exc:
                        logger.debug("repair_callback failed: %s", exc)
                        repaired = ""
                    if isinstance(repaired, str) and _candidate_is_actionable(repaired):
                        current_stmt = repaired.strip()
                        diag["lean_statement"] = current_stmt
        elif not grounded and use_lean and not lean_stmt:
            _bump("lean_missing_statement")
            diag["lean"] = "missing_statement"

        if not grounded:
            # Diagnose candidate generation bottleneck.
            cand_expr = _extract_type_from_assumption_expr(lean_expr) or lean_expr
            raw_candidates = suggest_bridge_candidates(
                assumption_expr=cand_expr,
                ledger_root=ledger_root,
                max_candidates=max(1, int(max_candidates_per_step)),
                context_pack=context_pack,
                allow_template_fallback=True,
                retrieval_memory_path=retrieval_memory_path,
            ) if cand_expr else []
            candidates = [
                c
                for c in raw_candidates
                if bool(c.actionable)
                and bool((c.lean_statement or "").strip())
                and _candidate_compile_sane(c.lean_statement)
            ]
            diag["candidate_count"] = len(candidates)
            diag["candidate_template_count"] = sum(1 for c in candidates if c.status == "TEMPLATE")
            if not candidates:
                _bump("candidate_generation_empty")
            else:
                if len(candidates) < len(raw_candidates):
                    _bump("candidate_only_non_actionable")
                _bump("candidate_generated_but_not_grounded")
            diag["final"] = "ungrounded"
            still_ungrounded.append(lean_expr or lean_stmt)
            _update_memory_attempts(
                theorem_names=[c.theorem_name for c in candidates],
                success=False,
                path=retrieval_memory_path,
            )
            taxonomy = "unknown"
            for key in failure_reasons.keys():
                if key in _FAIL_TAXONOMY:
                    taxonomy = _FAIL_TAXONOMY[key]
                    break
            _write_failure_artifact(
                root=failure_artifact_root,
                run_id=active_run_id,
                target_theorem=target_theorem,
                slot_index=idx,
                slot_name=slot_name,
                diag=diag,
                taxonomy=taxonomy,
            )
        else:
            diag["final"] = "grounded"
            _update_memory_attempts(
                theorem_names=chain_plan.ordered_candidates[: max(1, int(max_candidates_per_step))],
                success=True,
                path=retrieval_memory_path,
            )
        assumption_diagnostics.append(diag)

    logger.info(
        "Bridge execution for %s: grounded=%d still_ungrounded=%d",
        target_theorem,
        len(newly_grounded),
        len(still_ungrounded),
    )

    return BridgeExecutionResult(
        target_theorem=target_theorem,
        chain_plan=chain_plan,
        entailment_results=entailment_results,
        newly_grounded=newly_grounded,
        still_ungrounded=still_ungrounded,
        failure_reasons=failure_reasons,
        assumption_diagnostics=assumption_diagnostics,
        repair_attempts_total=repair_attempts_total,
        repair_success_count=repair_success_count,
    )


def collect_bridge_retry_targets(
    *,
    target_theorem: str,
    ledger_root: Path,
    max_depth: int = 2,
    max_candidates_per_step: int = 3,
    retrieval_memory_path: Path | None = None,
) -> BridgeChainPlan:
    """Build an ordered list of bridge theorems to attempt before retrying a target theorem.

    The planner follows existing bridge hints from ledger assumptions and augments
    them with token-overlap candidates for currently ungrounded assumptions.
    """
    ledger_root = Path(ledger_root)
    index = _load_ledger_index(ledger_root)
    seen: set[str] = {target_theorem}
    frontier = [target_theorem]
    ordered: list[str] = []
    rationale: list[str] = []

    for _depth in range(max(1, max_depth)):
        if not frontier:
            break
        next_frontier: list[str] = []
        for theorem_name in frontier:
            row = index.get(theorem_name)
            if not row:
                continue
            context_pack = build_theorem_context_pack(row)

            candidates: list[str] = []
            # 1) Existing grounding hints from previous runs.
            candidates.extend(_extract_hint_candidates(row))

            # 2) New candidate suggestions from ungrounded assumptions.
            assumptions = row.get("assumptions", [])
            if isinstance(assumptions, list):
                for a in assumptions:
                    if not isinstance(a, dict):
                        continue
                    grounding = str(a.get("grounding", "")).upper()
                    if grounding not in {"UNGROUNDED", "UNKNOWN", ""}:
                        continue
                    expr = _extract_type_from_assumption_expr(str(a.get("lean_expr", "")))
                    if not expr:
                        expr = str(a.get("label", ""))
                    if not expr:
                        continue
                    suggested = suggest_bridge_candidates(
                        assumption_expr=expr,
                        ledger_root=ledger_root,
                        max_candidates=max_candidates_per_step,
                        context_pack=context_pack,
                        allow_template_fallback=True,
                        retrieval_memory_path=retrieval_memory_path,
                    )
                    candidates.extend(c.theorem_name for c in suggested)

            if candidates:
                rationale.append(f"{theorem_name}: {', '.join(candidates[:max_candidates_per_step])}")

            for cand in candidates[:max_candidates_per_step]:
                if not cand or cand in seen:
                    continue
                seen.add(cand)
                ordered.append(cand)
                next_frontier.append(cand)

        frontier = next_frontier

    return BridgeChainPlan(
        target_theorem=target_theorem,
        ordered_candidates=ordered,
        rationale=rationale,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Bridge planning utilities")
    parser.add_argument("--assumption", default="", help="Assumption expression to bridge")
    parser.add_argument("--target-theorem", default="", help="Target theorem for bridge-chain planning")
    parser.add_argument("--ledger-root", default="output/verification_ledgers", help="Ledger directory")
    parser.add_argument("--top-k", type=int, default=3, help="Number of candidates")
    parser.add_argument("--depth", type=int, default=2, help="Bridge-chain depth")
    args = parser.parse_args()

    if not args.assumption and not args.target_theorem:
        raise SystemExit("provide --assumption or --target-theorem")

    if args.target_theorem:
        chain = collect_bridge_retry_targets(
            target_theorem=args.target_theorem,
            ledger_root=Path(args.ledger_root),
            max_depth=args.depth,
            max_candidates_per_step=args.top_k,
        )
        out = {
            "target_theorem": chain.target_theorem,
            "ordered_candidates": chain.ordered_candidates,
            "rationale": chain.rationale,
        }
        print(json.dumps(out, indent=2))
        raise SystemExit(0)

    plan = build_bridge_plan(
        assumption_expr=args.assumption,
        ledger_root=Path(args.ledger_root),
        max_candidates=args.top_k,
    )

    out = {
        "assumption_expr": plan.assumption_expr,
        "candidates": [
            {
                "theorem_name": c.theorem_name,
                "paper_id": c.paper_id,
                "status": c.status,
                "score": c.score,
                "actionable": c.actionable,
                "lean_statement": c.lean_statement,
            }
            for c in plan.candidates
        ],
    }
    print(json.dumps(out, indent=2))
