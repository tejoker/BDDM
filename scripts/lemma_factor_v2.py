#!/usr/bin/env python3
"""Lemma-factor v2 — binder-preserving decomposition for long UR theorems.

The v1 module (`lemma_factor_assistant.py`) produced 0/5 elaborating aux on
its smoke run because the LLM dropped the parent's binder context. v2 fixes
this by:

  1. Extracting the FULL parent binder block verbatim and embedding it in
     the system prompt with the explicit instruction that every aux MUST
     repeat ALL of these binders, in order, with their full types.
  2. Surfacing the `export Paper_<id> (...)` symbol list from the paper-
     theory file so the LLM knows which names don't need binders.
  3. Including 2 in-context examples of well-factored aux lemmas from the
     curated audited-core proofs (`Paper_2604_21884.lean`:
     `remark_20_param_roles` and `rem_primitive_route_witness`).
  4. Forbidding trivial bodies (`:= True`, `∃ x, x = x`, etc.) — same
     forbidden-token list as the whole-proof generator.

Public API mirrors v1:

    factor_long_theorem_v2(*, paper_id, theorem_name, lean_statement,
                          paper_theory_hint, exported_symbols, client,
                          model=..., validate_elaboration=None, max_aux=5)
        -> list[dict]

Each returned dict has the same shape as v1's record plus an explicit
`protocol='lemma_factor_v2'` marker and (optionally) `parent_binder_block`.

Standards-positive: aux that doesn't elaborate is rejected. Aux with
forbidden tokens or trivial bodies is rejected pre-elaboration. Returns an
empty list on transport / refusal / malformed JSON.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# Reuse v1 helpers where possible.
import lemma_factor_assistant as _lfa_v1  # noqa: E402


DEFAULT_MODEL = os.getenv("MISTRAL_MODEL", "labs-leanstral-2603")
DEFAULT_MAX_TOKENS = 1800
MAX_STATEMENT_CHARS = 2400
MAX_HINT_CHARS = 1800
MAX_BINDER_CHARS = 1200
MAX_EXPORTS_CHARS = 1200
MAX_AUX_DEFAULT = 5
MIN_AUX_DEFAULT = 2
MAX_LEAN_ERROR_TAIL_CHARS = 500


# In-context examples drawn directly from curated audited-core proofs in
# `Desol/PaperProofs/Paper_2604_21884.lean` and `Desol/PaperProofs/Auto/`.
# Each example shows ONE parent shape with two-or-more aux lemmas trimmed to
# the SIGNATURE form `:= by sorry` (the whole-proof generator closes them
# later). For shapes WITHOUT a curated example we emit a `MISSING` marker
# rather than synthesize content (standards-positive).
EXAMPLE_AUX_LEMMAS: tuple[dict[str, str], ...] = (
    # ---- shape: exists_with_witness (∃ ε > 0, ineq(ε)) ----
    # Source: Desol/PaperProofs/Paper_2604_21884.lean::remark_20_param_roles
    {
        "shape": "exists_with_witness",
        "parent_name": "remark_20_param_roles",
        "parent_binders": (
            "(alpha s1_val s2_val theta_val : ℝ)\n"
            "(_hs1 : 0 < s1_val) (_hs2 : s1_val < s2_val)\n"
            "(_htheta : 0 < theta_val ∧ theta_val < 1)\n"
            "(hs2alpha : s2_val < 4 * alpha - 3 - (3/2) * theta_val)"
        ),
        "parent_target": (
            "∃ eps : ℝ, 0 < eps ∧ s2_val < 4 * alpha - 3 - (3/2) * theta_val - eps"
        ),
        "aux_examples": (
            "theorem remark_20_param_roles_eps_positive\n"
            "    (alpha s1_val s2_val theta_val : ℝ)\n"
            "    (_hs1 : 0 < s1_val) (_hs2 : s1_val < s2_val)\n"
            "    (_htheta : 0 < theta_val ∧ theta_val < 1)\n"
            "    (hs2alpha : s2_val < 4 * alpha - 3 - (3/2) * theta_val) :\n"
            "  0 < (4 * alpha - 3 - (3/2) * theta_val - s2_val) / 2 := by sorry\n\n"
            "theorem remark_20_param_roles_bound_holds\n"
            "    (alpha s1_val s2_val theta_val : ℝ)\n"
            "    (_hs1 : 0 < s1_val) (_hs2 : s1_val < s2_val)\n"
            "    (_htheta : 0 < theta_val ∧ theta_val < 1)\n"
            "    (hs2alpha : s2_val < 4 * alpha - 3 - (3/2) * theta_val) :\n"
            "  s2_val < 4 * alpha - 3 - (3/2) * theta_val\n"
            "    - (4 * alpha - 3 - (3/2) * theta_val - s2_val) / 2 := by sorry"
        ),
    },
    # ---- shape: conjunction_with_ineq (a ≤ b ∧ c ≤ d ∧ ...) ----
    # Source: Desol/PaperProofs/Paper_2604_21884.lean::admissible_intro
    {
        "shape": "conjunction_with_ineq",
        "parent_name": "admissible_intro_split",
        "parent_binders": (
            "{eps alpha s1 s2 theta : ℝ}\n"
            "(h1 : 0 < s1) (h2 : s1 < s2) (h3 : 0 < theta) (h4 : theta < 1)\n"
            "(h5 : s2 < 4 * alpha - 3 - (3 / 2) * theta - eps)"
        ),
        "parent_target": (
            "0 < s1 ∧ s1 < s2 ∧ 0 < theta ∧ theta < 1 ∧\n"
            "  s2 < 4 * alpha - 3 - (3 / 2) * theta - eps"
        ),
        "aux_examples": (
            "theorem admissible_intro_split_first\n"
            "    {eps alpha s1 s2 theta : ℝ}\n"
            "    (h1 : 0 < s1) (h2 : s1 < s2) (h3 : 0 < theta) (h4 : theta < 1)\n"
            "    (h5 : s2 < 4 * alpha - 3 - (3 / 2) * theta - eps) :\n"
            "  0 < s1 := by sorry\n\n"
            "theorem admissible_intro_split_rest\n"
            "    {eps alpha s1 s2 theta : ℝ}\n"
            "    (h1 : 0 < s1) (h2 : s1 < s2) (h3 : 0 < theta) (h4 : theta < 1)\n"
            "    (h5 : s2 < 4 * alpha - 3 - (3 / 2) * theta - eps) :\n"
            "  s1 < s2 ∧ 0 < theta ∧ theta < 1 ∧\n"
            "    s2 < 4 * alpha - 3 - (3 / 2) * theta - eps := by sorry"
        ),
    },
    # ---- shape: iff_bidirectional (P ↔ Q) ----
    # Source: Desol/PaperProofs/Auto/Paper_2604_21884.lean::
    #         auto_prop_sharpness_critical_exponent_iff
    {
        "shape": "iff_bidirectional",
        "parent_name": "auto_prop_sharpness_iff",
        "parent_binders": (
            "(alpha : ℝ)"
        ),
        "parent_target": (
            "(3 - 4 * alpha = 0) ↔ alpha = 3 / 4"
        ),
        "aux_examples": (
            "theorem auto_prop_sharpness_iff_fwd\n"
            "    (alpha : ℝ) (h : 3 - 4 * alpha = 0) :\n"
            "  alpha = 3 / 4 := by sorry\n\n"
            "theorem auto_prop_sharpness_iff_bwd\n"
            "    (alpha : ℝ) (h : alpha = 3 / 4) :\n"
            "  3 - 4 * alpha = 0 := by sorry"
        ),
    },
    # ---- shape: implication (H → C) ----
    # Source: Desol/PaperProofs/Auto/Paper_2604_21884.lean::
    #         auto_prop_det_contraction_condition_rearrange
    {
        "shape": "implication",
        "parent_name": "auto_prop_det_contraction_rearrange",
        "parent_binders": (
            "(eps alpha s2 theta : ℝ)"
        ),
        "parent_target": (
            "(s2 < 4 * alpha - 3 - (3 / 2) * theta - eps ∧\n"
            "  3 - 4 * alpha + theta * (s2 + eps) < 0) →\n"
            "  s2 + (3 / 2) * theta + eps < 4 * alpha - 3 ∧\n"
            "  theta * (s2 + eps) < 4 * alpha - 3"
        ),
        "aux_examples": (
            "theorem auto_prop_det_contraction_rearrange_first\n"
            "    (eps alpha s2 theta : ℝ)\n"
            "    (h : s2 < 4 * alpha - 3 - (3 / 2) * theta - eps ∧\n"
            "         3 - 4 * alpha + theta * (s2 + eps) < 0) :\n"
            "  s2 + (3 / 2) * theta + eps < 4 * alpha - 3 := by sorry\n\n"
            "theorem auto_prop_det_contraction_rearrange_second\n"
            "    (eps alpha s2 theta : ℝ)\n"
            "    (h : s2 < 4 * alpha - 3 - (3 / 2) * theta - eps ∧\n"
            "         3 - 4 * alpha + theta * (s2 + eps) < 0) :\n"
            "  theta * (s2 + eps) < 4 * alpha - 3 := by sorry"
        ),
    },
    # ---- shape: calc_chain (a ≤ b ≤ c ≤ d via stepwise lemmas) ----
    # Source: Desol/PaperProofs/Auto/Paper_2604_21616.lean::
    #         auto_proof_8_rank_one_triangle
    {
        "shape": "calc_chain",
        "parent_name": "auto_proof_8_rank_one_triangle",
        "parent_binders": (
            "{m n : Type*} [Fintype m] [Fintype n]\n"
            "{E : Type*} [NormedAddCommGroup E] [NormedSpace ℝ E]\n"
            "(A : m → n → ℝ) (B : m → n → E)"
        ),
        "parent_target": (
            "‖∑ i, ∑ j, A i j • B i j‖ ≤ ∑ i, ∑ j, |A i j| * ‖B i j‖"
        ),
        "aux_examples": (
            "theorem auto_proof_8_step1\n"
            "    {m n : Type*} [Fintype m] [Fintype n]\n"
            "    {E : Type*} [NormedAddCommGroup E] [NormedSpace ℝ E]\n"
            "    (A : m → n → ℝ) (B : m → n → E) :\n"
            "  ‖∑ i, ∑ j, A i j • B i j‖ ≤ ∑ i, ‖∑ j, A i j • B i j‖ := by sorry\n\n"
            "theorem auto_proof_8_step2\n"
            "    {m n : Type*} [Fintype m] [Fintype n]\n"
            "    {E : Type*} [NormedAddCommGroup E] [NormedSpace ℝ E]\n"
            "    (A : m → n → ℝ) (B : m → n → E) :\n"
            "  (∑ i, ‖∑ j, A i j • B i j‖) ≤ ∑ i, ∑ j, ‖A i j • B i j‖ := by sorry\n\n"
            "theorem auto_proof_8_step3\n"
            "    {m n : Type*} [Fintype m] [Fintype n]\n"
            "    {E : Type*} [NormedAddCommGroup E] [NormedSpace ℝ E]\n"
            "    (A : m → n → ℝ) (B : m → n → E) :\n"
            "  (∑ i, ∑ j, ‖A i j • B i j‖) = ∑ i, ∑ j, |A i j| * ‖B i j‖ := by sorry"
        ),
    },
    # ---- shape: universal_with_bound (∀ n ≥ N, P n) ----
    # No curated example exists for this shape in PaperProofs/. Marked MISSING.
    {
        "shape": "universal_with_bound",
        "parent_name": "MISSING",
        "parent_binders": "MISSING",
        "parent_target": "MISSING",
        "aux_examples": (
            "-- MISSING: no curated `∀ n ≥ N, P n` aux example available.\n"
            "-- For this shape, decompose into (1) an aux that establishes a\n"
            "-- specific bound `N₀` and (2) an aux that proves `P n` for all\n"
            "-- `n ≥ N₀`. Aux signatures must repeat the parent binder block."
        ),
    },
    # ---- shape: nested_exists (∃ x y, P x y) ----
    # No curated example exists for nested existentials in PaperProofs/. MISSING.
    {
        "shape": "nested_exists",
        "parent_name": "MISSING",
        "parent_binders": "MISSING",
        "parent_target": "MISSING",
        "aux_examples": (
            "-- MISSING: no curated nested-existential aux example available.\n"
            "-- For this shape, propose (1) an aux witnessing the outer\n"
            "-- variable, (2) an aux witnessing the inner variable given the\n"
            "-- outer, and (3) an aux proving the body. Each aux must repeat\n"
            "-- the parent binder block verbatim."
        ),
    },
    # ---- shape: disjunction (P ∨ Q) ----
    # No curated example exists for disjunctive targets in PaperProofs/. MISSING.
    {
        "shape": "disjunction",
        "parent_name": "MISSING",
        "parent_binders": "MISSING",
        "parent_target": "MISSING",
        "aux_examples": (
            "-- MISSING: no curated disjunction aux example available.\n"
            "-- For this shape, propose ONE aux proving P (LHS) OR one aux\n"
            "-- proving Q (RHS), whichever the proof argument supports. Aux\n"
            "-- repeats the parent binder block."
        ),
    },
)


_SYSTEM_PROMPT_HEADER = (
    "You are a research assistant that decomposes Lean 4 theorems into "
    "smaller, easier auxiliary lemmas while PRESERVING the parent binder "
    "context. You receive (1) the original theorem (name + binder block + "
    "target), (2) a paper-theory hint with paper-local definitions/axioms/"
    "instances, (3) the list of paper-theory symbols already exported into "
    "scope, and (4) two in-context examples of well-factored aux lemmas.\n\n"
    "STRICT RULES (binder preservation is mandatory):\n"
    "  1. Each aux signature MUST start with `theorem ` and have body "
    "     `:= by sorry`.\n"
    "  2. Each aux MUST repeat the FULL parent binder block, in order, with "
    "     each binder's full type. Do NOT drop binders. Do NOT change their "
    "     types. Do NOT rename them. Implicit `{x : T}` stays `{x : T}`, "
    "     explicit `(x : T)` stays `(x : T)`. Aux may introduce ADDITIONAL "
    "     binders ONLY when those new binders are NEEDED for the aux's own "
    "     target (e.g. existential witness or auxiliary natural-number).\n"
    "  3. Each aux name MUST be derived from the parent name (e.g. "
    "     `<parent>_aux_<k>` or `<parent>_<part>`) and MUST be a valid Lean "
    "     identifier.\n"
    "  4. Aux signatures use ONLY paper-local symbols (from the hint, listed "
    "     in the `Exports` section) and Mathlib. Do NOT introduce new "
    "     typeclasses, new axioms, or new free variables.\n"
    "  5. Body MUST be exactly ` := by sorry` (one sorry, no proof attempt).\n"
    "  6. Do NOT produce trivial aux lemmas (`: True`, `0 = 0`, `∃ x : ℝ, "
    "     x = x`, opaque `Statement`/`PaperClaim` Props, `: False`). The "
    "     forbidden tokens `sorry`, `admit`, `apply?`, `axiom`, "
    "     `native_decide` may NOT appear in the aux TARGET (the body must "
    "     literally be `sorry`, but the type after the colon must not "
    "     contain these).\n"
    "  7. For each aux, also provide a one-line `compose_hint` describing "
    "     how it contributes to the parent (e.g. \"first conjunct\", "
    "     \"existential witness\").\n\n"
    "Output ONLY a single JSON object with this schema:\n"
    '  {\n'
    '    "verdict": "FACTOR" | "REFUSE",\n'
    '    "aux_lemmas": [\n'
    '       {"aux_name": "...", "aux_signature": "theorem ... := by sorry",\n'
    '        "compose_hint": "..."}\n'
    '    ],\n'
    '    "compose_strategy": "one-sentence sketch of how to combine aux into parent",\n'
    '    "reasoning": "one or two sentences justifying the split",\n'
    '    "confidence": 0.00\n'
    '  }\n'
    "No prose, no markdown fences — just the JSON. If the parent cannot be "
    "factored (atomic conclusion, single equation), return verdict=`REFUSE` "
    "with an empty `aux_lemmas` list.\n"
)


def _build_in_context_examples() -> str:
    pieces: list[str] = ["IN-CONTEXT EXAMPLES OF WELL-FACTORED AUX LEMMAS:\n"]
    for i, ex in enumerate(EXAMPLE_AUX_LEMMAS, start=1):
        shape = ex.get("shape", "unknown")
        if ex.get("parent_name") == "MISSING":
            # Standards-positive: MISSING shapes are marked explicitly rather
            # than synthesized. The LLM receives a note and a brief hint.
            pieces.append(
                f"--- Example {i}: shape `{shape}` (MISSING curated example) ---\n"
                f"{ex['aux_examples']}\n"
            )
            continue
        pieces.append(
            f"--- Example {i}: shape `{shape}`, parent `{ex['parent_name']}` ---\n"
            "Parent binders:\n"
            "```lean\n"
            f"{ex['parent_binders']}\n"
            "```\n"
            "Parent target:\n"
            "```lean\n"
            f"{ex['parent_target']}\n"
            "```\n"
            "Aux lemmas (notice every aux REPEATS the parent binder block):\n"
            "```lean\n"
            f"{ex['aux_examples']}\n"
            "```\n"
        )
    return "\n".join(pieces)


SYSTEM_PROMPT_V2 = _SYSTEM_PROMPT_HEADER + "\n" + _build_in_context_examples()


_USER_TEMPLATE = (
    "Parent theorem name: `{parent_name}`\n\n"
    "Parent binder block (every aux MUST repeat these verbatim):\n"
    "```lean\n{binder_block}\n```\n\n"
    "Parent target:\n"
    "```lean\n{parent_target}\n```\n\n"
    "Full parent statement (for reference):\n"
    "```lean\n{full_statement}\n```\n\n"
    "Paper-theory hint (already in scope — defs, axioms, instances):\n"
    "```lean\n{paper_theory_hint}\n```\n\n"
    "Exports (paper-local symbols already in scope, no binders needed):\n"
    "```lean\n{exported_symbols}\n```\n\n"
    "{audited_core_section}"
    "Propose 2-5 auxiliary lemmas now. Respond with the JSON object only."
)


_AUDITED_CORE_SECTION_TEMPLATE = (
    "Audited proofs from the same paper (paper-local in-context examples — "
    "use them to pick aux-lemma names and shapes that fit this paper's "
    "idioms):\n"
    "```lean\n{audited_core_hint}\n```\n\n"
)


# Audited-core budget for v2's user prompt. Capped lower than the
# whole-proof generator's because v2 already carries the full binder
# block + exports + paper-theory hint in the user message.
MAX_AUDITED_CORE_CHARS_V2 = 2400


# --- Parent statement decomposition ---------------------------------------


_THEOREM_HEAD_RX = re.compile(
    r"^\s*(?:noncomputable\s+|private\s+)?(?:theorem|lemma)\s+"
    r"([A-Za-z_][A-Za-z0-9_'.]*)\s*"
)


def split_parent_statement(lean_statement: str) -> tuple[str, str, str]:
    """Return (theorem_name, binder_block, parent_target).

    The binder block is everything between the theorem name and the
    type-colon at parenthesis depth 0. The parent_target is everything from
    after that colon up to (but not including) `:= by ...`. We strip a
    trailing `:= by sorry` body if present.

    On malformed input we return ("", "", lean_statement.strip()) so the
    caller can fall back gracefully.
    """
    src = (lean_statement or "").strip()
    if not src:
        return "", "", ""
    m = _THEOREM_HEAD_RX.match(src)
    if not m:
        return "", "", src
    name = m.group(1)
    rest = src[m.end():]
    # Strip `:= by ...` tail.
    rest = re.sub(r":=\s*by[\s\S]*$", "", rest).rstrip()
    rest = re.sub(r":=[\s\S]*$", "", rest).rstrip()
    # Find the top-level colon: scan with parenthesis-depth tracking. We
    # treat `(`, `{`, `[`, `⟨` as opening and their matches as closing.
    depth = 0
    opens = {"(": ")", "{": "}", "[": "]", "⟨": "⟩"}
    closers = set(opens.values())
    colon_idx: Optional[int] = None
    i = 0
    n = len(rest)
    while i < n:
        ch = rest[i]
        if ch in opens:
            depth += 1
        elif ch in closers:
            if depth > 0:
                depth -= 1
        elif ch == ":" and depth == 0:
            # Skip `::` (namespaces in identifiers) — but Lean uses `.` not
            # `::`, so a `:` at depth 0 is always the type colon. We do
            # need to skip `:=` which would only matter if we hadn't already
            # stripped the body — defensive check anyway.
            if i + 1 < n and rest[i + 1] == "=":
                i += 2
                continue
            colon_idx = i
            break
        i += 1
    if colon_idx is None:
        return name, rest.strip(), ""
    binder_block = rest[:colon_idx].strip()
    parent_target = rest[colon_idx + 1:].strip()
    return name, binder_block, parent_target


# --- Exports extraction ---------------------------------------------------


_EXPORT_RX = re.compile(
    r"^\s*export\s+[A-Za-z_][A-Za-z0-9_.]*\s*\(([\s\S]*?)\)\s*$",
    re.MULTILINE,
)


def extract_exported_symbols(paper_theory_path: Path) -> str:
    """Read `export Paper_<id> (a b c ...)` directives from a paper-theory
    file and return the joined symbol list (one per line).

    Returns "" when the file is missing or has no exports.
    """
    try:
        text = Path(paper_theory_path).read_text(encoding="utf-8")
    except OSError:
        return ""
    out: list[str] = []
    for m in _EXPORT_RX.finditer(text):
        body = m.group(1)
        # Normalize whitespace -> single space; split on whitespace.
        body = re.sub(r"\s+", " ", body).strip()
        for tok in body.split(" "):
            tok = tok.strip()
            if tok and tok not in out:
                out.append(tok)
    return "\n".join(out)


# --- Forbidden-target gate (v2-specific) -----------------------------------


_FORBIDDEN_TARGET_TOKENS = ("sorry", "admit", "apply?", "axiom", "native_decide")
_TRIVIAL_TARGET_PATTERNS = (
    re.compile(r":\s*True\s*$"),
    re.compile(r":\s*True\b\s*:="),
    re.compile(r"∃\s+\w+\s*:\s*\S+\s*,\s*(\w+)\s*=\s*\1\b"),
    re.compile(r":\s*\(?\s*0\s*=\s*0\s*\)?\s*$"),
    re.compile(r":\s*False\b"),
    re.compile(r":\s*Nonempty\s+Unit\b"),
    re.compile(r"\bPaperClaim\b"),
    re.compile(r"\bSourceStatement\b"),
)


def _target_is_trivial(decl: str) -> Optional[str]:
    """Return a flag if the aux target is trivial, else None."""
    body = (decl or "").strip()
    # Cut at `:= by sorry` so we only inspect the TYPE.
    body = re.sub(r":=\s*by\s+sorry\s*$", "", body).strip()
    for pat in _TRIVIAL_TARGET_PATTERNS:
        if pat.search(body):
            return "trivial_target"
    # Forbidden tokens in target (we already strip body=sorry; so anything
    # remaining is the type).
    for tok in _FORBIDDEN_TARGET_TOKENS:
        # Word-boundary check.
        if "?" in tok:
            if tok in body:
                return f"forbidden_token_in_target:{tok}"
            continue
        if re.search(r"(?<![A-Za-z0-9_'])" + re.escape(tok) + r"(?![A-Za-z0-9_'])", body):
            return f"forbidden_token_in_target:{tok}"
    return None


# --- Composition shape selection ------------------------------------------


# Map fine-grained shape labels to the coarse legacy labels expected by
# downstream callers (sweep_lemma_factor_v2.attempt_composition's existing
# `parent_target_shape` field). The fine labels drive a richer emitter via
# `render_composition_attempts`, which accepts either label vocabulary.
_COARSE_FROM_FINE: dict[str, str] = {
    "exists_with_witness": "exists",
    "exists_with_prop": "exists",
    "nested_exists": "exists",
    "iff_bidirectional": "iff",
    "implication": "other",
    "universal_implication": "other",
    "universal_with_bound": "other",
    "calc_chain": "other",
    "disjunction": "other",
    "conjunction_with_ineq": "and",
    "and": "and",
    "exists": "exists",
    "iff": "iff",
    "other": "other",
}


def detect_target_shape_fine(parent_target: str) -> str:
    """Fine-grained shape classifier returning one of:

      - `exists_with_witness`   : `∃ x, p(x)` and p(x) is a single proposition
      - `exists_with_prop`      : `∃ x, P x ∧ Q x` (or similar) — witness + prop
      - `nested_exists`         : `∃ x, ∃ y, ...` or `∃ x y, ...`
      - `iff_bidirectional`     : `P ↔ Q`
      - `implication`           : `P → Q` at depth 0 (not nested in ∀)
      - `universal_implication` : `∀ x, P x → Q x`
      - `universal_with_bound`  : `∀ n ≥ N, P n` or `∀ n, N ≤ n → P n`
      - `calc_chain`            : equality/inequality target only (no ∧/∨/↔/∃)
      - `disjunction`           : top-level `P ∨ Q`
      - `conjunction_with_ineq` : top-level `∧` with at least one inequality
      - `other`                 : nothing recognized
    """
    raw = (parent_target or "").strip()
    if not raw:
        return "other"
    # Strip a trailing `:= by ...` defensively.
    raw = re.sub(r":=\s*by[\s\S]*$", "", raw).strip()
    t = re.sub(r"\s+", " ", raw)
    # ∃-form: check first (top-level existential dominates).
    if re.match(r"^[(\s]*∃(?=\s|$)", t):
        # Nested existential: `∃ x, ∃ y, ...` OR `∃ x y, ...` with two
        # different identifiers before the comma.
        if re.search(r"^[(\s]*∃[^,]*,\s*\(?\s*∃", t):
            return "nested_exists"
        # `∃ x y z, ...` — at least two binders separated by whitespace
        # before the first comma. Identifiers can include ' and _.
        m = re.match(r"^[(\s]*∃\s+([^,:]+?)(?:\s*:\s*[^,]+)?\s*,", t)
        if m:
            head = m.group(1).strip()
            # Count whitespace-separated identifier-like tokens.
            toks = [tk for tk in re.split(r"\s+", head) if tk and tk not in {",", "(", ")"}]
            if len(toks) >= 2 and all(re.match(r"^[A-Za-z_][A-Za-z0-9_']*$", tk) for tk in toks):
                return "nested_exists"
        # Look at the body after the binder.
        body_m = re.match(r"^[(\s]*∃[^,]*,\s*(.+)$", t)
        body = body_m.group(1) if body_m else ""
        if "∧" in body or "∨" in body:
            return "exists_with_prop"
        return "exists_with_witness"
    # ↔-form.
    if "↔" in t:
        return "iff_bidirectional"
    # ∀-form: distinguish universal_with_bound and universal_implication.
    if re.match(r"^[(\s]*∀(?=\s|$)", t):
        # universal_with_bound: explicit `≥` / `≤` / `>` / `<` in the binder
        # block (e.g. `∀ n ≥ N`) — or, in elaborated form, `∀ n, N ≤ n →`.
        head_match = re.match(r"^[(\s]*∀\s+([^,]+),\s*(.+)$", t)
        if head_match:
            head = head_match.group(1)
            body = head_match.group(2)
            if any(sym in head for sym in ("≥", "≤", ">", "<")):
                return "universal_with_bound"
            if (
                "→" in body
                and re.search(r"\b[NM]\s*[≤≥]\s*", body) is not None
            ):
                return "universal_with_bound"
            if "→" in body:
                return "universal_implication"
        return "universal_implication"
    # ∨-form (top-level disjunction).
    if "∨" in t:
        return "disjunction"
    # ∧-form (top-level conjunction). Prefer conjunction_with_ineq if any
    # inequality symbol appears.
    if "∧" in t:
        if any(sym in t for sym in ("<", ">", "≤", "≥")):
            return "conjunction_with_ineq"
        return "conjunction_with_ineq"
    # `→` at depth 0 (implication).
    if "→" in t:
        return "implication"
    # calc_chain: equality/inequality target, no logical connectives. We
    # recognize this only when there's an `=` / `≤` / `<` etc. and no
    # `∧ ∨ ↔ ∀ ∃ →`.
    if any(sym in t for sym in ("=", "<", ">", "≤", "≥")):
        return "calc_chain"
    return "other"


def detect_target_shape(parent_target: str) -> str:
    """Legacy coarse classifier returning 'and' / 'iff' / 'exists' / 'other'.

    Implemented in terms of `detect_target_shape_fine` + the coarse map so
    the two vocabularies stay in sync.
    """
    fine = detect_target_shape_fine(parent_target)
    return _COARSE_FROM_FINE.get(fine, "other")


# Compose-hint → role keyword mapping. Used by `assign_aux_roles` to figure
# out which aux plays which role in the chosen composition skeleton.
_WITNESS_HINT_TOKENS = (
    "witness", "exists_witness", "pos", "positive", "construct", "construction",
)
_PROP_HINT_TOKENS = (
    "prop", "property", "bound", "ineq", "inequality", "holds", "satisfies",
)
# Order matters in the lookup loop: check `bwd` tokens BEFORE `fwd` tokens
# so the substring "mp" inside "mpr" doesn't accidentally classify a
# backward-direction aux as forward. The mapper applies _BWD first.
_FWD_HINT_TOKENS = ("fwd", "forward", "_mp_", "→", "->")
_BWD_HINT_TOKENS = ("bwd", "backward", "mpr", "←", "<-")
_STEP_HINT_RX = re.compile(r"\b(?:step|calc|stage|chain)[-_ ]?(\d+)\b", re.IGNORECASE)


def _hint_has(hint: str, tokens: tuple[str, ...]) -> bool:
    h = (hint or "").lower()
    return any(tok.lower() in h for tok in tokens)


def assign_aux_roles(
    *,
    shape: str,
    aux: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Given a fine-grained shape and a list of aux records (each with
    `aux_name` and `compose_hint`), assign each aux to one of the roles
    that the chosen shape needs.

    Returned dict maps role-name -> ordered list of aux names. Roles
    depend on shape:

      - exists_with_witness / exists_with_prop:
            {"witness": [...], "prop": [...]}
      - nested_exists: {"witness": [...], "prop": [...]} (witnesses inferred
        from compose_hint containing 'witness'/'pos').
      - iff_bidirectional: {"fwd": [...], "bwd": [...]}
      - implication / universal_implication / universal_with_bound:
            {"body": [...]}   (single aux; we still wrap in a list)
      - calc_chain: {"step": [...]} ordered by `step-N` hint or list order
      - disjunction: {"left": [...], "right": [...]}
      - conjunction_with_ineq / and: {"conjunct": [...]}
      - other / unknown shape: {"any": [<all-aux>]}
    """
    names = [str(a.get("aux_name", "")).strip() for a in aux]
    hints = [str(a.get("compose_hint", "")).strip() for a in aux]
    if shape in ("exists_with_witness", "exists_with_prop", "nested_exists"):
        witness: list[str] = []
        prop: list[str] = []
        for nm, hint in zip(names, hints):
            if not nm:
                continue
            if _hint_has(hint, _WITNESS_HINT_TOKENS) or _hint_has(nm, _WITNESS_HINT_TOKENS):
                witness.append(nm)
            elif _hint_has(hint, _PROP_HINT_TOKENS) or _hint_has(nm, _PROP_HINT_TOKENS):
                prop.append(nm)
            else:
                # Default: first un-classified aux is the witness, rest are props.
                if not witness:
                    witness.append(nm)
                else:
                    prop.append(nm)
        return {"witness": witness, "prop": prop}
    if shape == "iff_bidirectional":
        fwd: list[str] = []
        bwd: list[str] = []
        for nm, hint in zip(names, hints):
            if not nm:
                continue
            # Check BWD before FWD so that strings like "mpr" don't trigger
            # the FWD substring "mp" — but we kept _mp_ wrapped in
            # underscores to defend the same property regardless of order.
            if _hint_has(hint, _BWD_HINT_TOKENS) or _hint_has(nm, _BWD_HINT_TOKENS):
                bwd.append(nm)
            elif _hint_has(hint, _FWD_HINT_TOKENS) or _hint_has(nm, _FWD_HINT_TOKENS):
                fwd.append(nm)
            else:
                # Default: first un-classified aux is the fwd, second bwd.
                if not fwd:
                    fwd.append(nm)
                elif not bwd:
                    bwd.append(nm)
        return {"fwd": fwd, "bwd": bwd}
    if shape in ("implication", "universal_implication", "universal_with_bound"):
        return {"body": [nm for nm in names if nm]}
    if shape == "calc_chain":
        # Sort aux by step-N if hint contains it; else by list order.
        rank: list[tuple[int, str]] = []
        for idx, (nm, hint) in enumerate(zip(names, hints)):
            if not nm:
                continue
            m = _STEP_HINT_RX.search(hint) or _STEP_HINT_RX.search(nm)
            k = int(m.group(1)) if m else (idx + 1)
            rank.append((k, nm))
        rank.sort(key=lambda t: t[0])
        return {"step": [nm for _, nm in rank]}
    if shape == "disjunction":
        # left = first non-rhs aux, right = aux with 'right'/'inr'/'rhs' hint.
        left: list[str] = []
        right: list[str] = []
        for nm, hint in zip(names, hints):
            if not nm:
                continue
            h = (hint + " " + nm).lower()
            if any(t in h for t in ("right", "inr", "rhs", "or.inr")):
                right.append(nm)
            elif any(t in h for t in ("left", "inl", "lhs", "or.inl")):
                left.append(nm)
            else:
                if not left:
                    left.append(nm)
                else:
                    right.append(nm)
        return {"left": left, "right": right}
    if shape in ("conjunction_with_ineq", "and"):
        return {"conjunct": [nm for nm in names if nm]}
    return {"any": [nm for nm in names if nm]}


# --- Composition v3: per-aux-type role-aware -------------------------------
#
# Round-VIII's 0/12 parent-composition rate was bounded by aux signatures
# whose SHAPE matched the parent role (e.g. "first conjunct") but whose
# TYPE didn't fit — most often when a parent's `∃ K D_X D_Z : ℕ → ℕ` needed
# an aux that PRODUCED the function witnesses, not Props about them.
#
# v3 classifies each aux by its return TYPE (parsed from the aux's
# signature) and re-runs role assignment with that information so the
# emitter picks the right composition skeleton.


def _extract_aux_target(aux_signature: str) -> str:
    """Return the `target` portion of an aux signature (the substring after
    the top-level `:` and before `:= by sorry`). Falls back to the full
    signature when parsing fails — caller still gets best-effort matching.
    """
    sig = (aux_signature or "").strip()
    if not sig:
        return ""
    # Strip `:= by ...` tail.
    sig = re.sub(r":=\s*by[\s\S]*$", "", sig).rstrip()
    # First, strip the leading `theorem <name>` so the colon-walker starts
    # at the binder block.
    m = _THEOREM_HEAD_RX.match(sig)
    if m:
        sig = sig[m.end():]
    depth = 0
    opens = {"(": ")", "{": "}", "[": "]", "⟨": "⟩"}
    closers = set(opens.values())
    i = 0
    n = len(sig)
    while i < n:
        ch = sig[i]
        if ch in opens:
            depth += 1
        elif ch in closers:
            if depth > 0:
                depth -= 1
        elif ch == ":" and depth == 0:
            if i + 1 < n and sig[i + 1] == "=":
                i += 2
                continue
            return sig[i + 1:].strip()
        i += 1
    return sig.strip()


# Aux type classification.
#
# - witness_producing: returns ∃ x, P x (or nested universal-then-exists
#   `∀ ε > 0, ∃ δ > 0, ...`).
# - property_establishing: returns a non-existential Prop (e.g. inequality,
#   conjunction of bounds, implication chain).
# - type_coercion: returns a TypeClass instance / coerced value (signature
#   ends with a known Mathlib instance name or a `[...]` style typeclass).
# - equational: returns an equality `f x = g x` or `f = g` at the top.


_INSTANCE_TOKENS = (
    "Inhabited", "Nonempty", "Decidable", "DecidableEq",
    "Fintype", "Group", "Monoid", "CommMonoid", "AddCommGroup",
    "AddGroup", "Ring", "CommRing", "Field", "Module", "Algebra",
    "NormedAddCommGroup", "NormedSpace", "MetricSpace",
    "TopologicalSpace", "OrderedField", "LinearOrder", "PartialOrder",
)


def classify_aux_type(aux_signature: str) -> str:
    """Classify an aux's return TYPE into one of:

    - ``"witness_producing"`` — target starts with `∃` (possibly nested
      under `∀`), so the aux delivers a witness.
    - ``"equational"``        — target is an equality at the top level
      (e.g. `f x = g x`) with no `∧ ∨ ↔ ∀ ∃ →` outside the equality.
    - ``"type_coercion"``     — target is a typeclass instance / coerced
      structure (e.g. `Inhabited X`, `Nonempty Y`).
    - ``"property_establishing"`` — anything else (inequality, conjunction,
      implication, plain Prop).
    - ``"unknown"``           — could not parse a target.
    """
    target = _extract_aux_target(aux_signature)
    if not target:
        return "unknown"
    t = re.sub(r"\s+", " ", target).strip()
    # Strip outer parens once for the top-level check.
    while t.startswith("(") and t.endswith(")"):
        # Ensure balanced.
        d = 0
        ok = True
        for k, ch in enumerate(t):
            if ch == "(":
                d += 1
            elif ch == ")":
                d -= 1
                if d == 0 and k != len(t) - 1:
                    ok = False
                    break
        if not ok:
            break
        t = t[1:-1].strip()
    # Type coercion: target is `Inhabited X` or other named instance.
    head = t.split(" ", 1)[0] if t else ""
    if head in _INSTANCE_TOKENS:
        return "type_coercion"
    # Witness producing: top-level `∃` (allow leading `∀ ... ,` containing
    # an `∃` inside the body — `∀ ε > 0, ∃ δ > 0, ...`). `\b` doesn't
    # behave for Unicode `∃`/`∀`, so we use an explicit whitespace check.
    if re.match(r"^∃(?:\s|$)", t) or re.match(r"^\(\s*∃(?:\s|$)", t):
        return "witness_producing"
    if re.match(r"^∀(?:\s|$)", t):
        body_m = re.match(r"^∀[^,]*,\s*(.+)$", t)
        if body_m and re.match(r"^[(\s]*∃(?:\s|$)", body_m.group(1)):
            return "witness_producing"
    # Equational: top-level `=` with NO `∧ ∨ ↔ ∀ ∃ →`.
    if "=" in t and not any(s in t for s in ("∧", "∨", "↔", "∀", "∃", "→")):
        # Require the `=` to be a real binary equality (skip `≠`, `:=`).
        if re.search(r"(?<![≠:])=(?!=)", t):
            return "equational"
    return "property_establishing"


def count_outer_existential_binders(parent_target: str) -> int:
    """Count how many existential witnesses the parent target needs at its
    OUTER nesting. Recognizes:

    - `∃ x, ...`              → 1
    - `∃ x y, ...`            → 2
    - `∃ x y z, ...`          → 3
    - `∃ x, ∃ y, ...`         → 2 (nested existential)
    - `∃ K D : ℕ → ℕ, ...`    → 2 (two binders sharing a type ascription)

    Returns 0 if the target is not an outer existential.
    """
    raw = (parent_target or "").strip()
    if not raw:
        return 0
    raw = re.sub(r":=\s*by[\s\S]*$", "", raw).strip()
    t = re.sub(r"\s+", " ", raw)
    # Strip outer parens.
    while t.startswith("(") and t.endswith(")"):
        d = 0
        ok = True
        for k, ch in enumerate(t):
            if ch == "(":
                d += 1
            elif ch == ")":
                d -= 1
                if d == 0 and k != len(t) - 1:
                    ok = False
                    break
        if not ok:
            break
        t = t[1:-1].strip()
    if not re.match(r"^∃(?:\s|$)", t):
        return 0
    total = 0
    # Walk through nested `∃ ... , ∃ ..., ...` chains while counting the
    # binders in each `∃`.
    while re.match(r"^∃(?:\s|$)", t):
        m = re.match(r"^∃\s+([^,]+?)\s*,\s*(.+)$", t)
        if not m:
            break
        head = m.group(1).strip()
        body = m.group(2).strip()
        # `head` may be `x` or `x y z` or `x y : T` or `K D : ℕ → ℕ`.
        # Strip a type ascription `: T` if present (top-level).
        depth = 0
        cut = len(head)
        for idx, ch in enumerate(head):
            if ch in "({[⟨":
                depth += 1
            elif ch in ")}]⟩":
                if depth > 0:
                    depth -= 1
            elif ch == ":" and depth == 0:
                cut = idx
                break
        binders_str = head[:cut].strip()
        # Count whitespace-separated identifier-like tokens.
        toks = [tk for tk in re.split(r"\s+", binders_str) if tk]
        identlike = [tk for tk in toks if re.match(r"^[A-Za-z_][A-Za-z0-9_']*$", tk)]
        if not identlike:
            inner_m = re.match(r"^\((.+)\)$", head)
            if inner_m:
                inner = inner_m.group(1)
                cut2 = len(inner)
                depth = 0
                for idx, ch in enumerate(inner):
                    if ch in "({[⟨":
                        depth += 1
                    elif ch in ")}]⟩":
                        if depth > 0:
                            depth -= 1
                    elif ch == ":" and depth == 0:
                        cut2 = idx
                        break
                inner_binders = inner[:cut2].strip()
                identlike = [
                    tk for tk in re.split(r"\s+", inner_binders) if tk and
                    re.match(r"^[A-Za-z_][A-Za-z0-9_']*$", tk)
                ]
        total += max(1, len(identlike)) if identlike else 1
        t = body.lstrip("(").strip()
    return total


def assign_aux_roles_v3(
    *,
    shape: str,
    aux: list[dict[str, Any]],
    parent_target: str = "",
) -> dict[str, list[str]]:
    """Type-aware role assignment (Round-IX, composition v3).

    Inspects each aux's `aux_signature` to classify its return TYPE, then
    matches aux to parent roles by TYPE rather than hint/name alone.

    For witness-shape parents, returns:

    - ``{"witness": [aux of type witness_producing, in input order],
        "prop":    [aux of type property_establishing / equational]}``

    The number of witnesses requested by the parent is inferred from
    ``parent_target`` via ``count_outer_existential_binders``. If FEWER
    witness-producing aux are available than the parent needs, v3 returns
    EMPTY witness/prop role lists — emitter will then refuse to synthesize.

    For non-witness shapes, v3 falls back to the v2 mapper.
    """
    if shape not in ("exists_with_witness", "exists_with_prop", "nested_exists"):
        return assign_aux_roles(shape=shape, aux=aux)

    n_witness_needed = count_outer_existential_binders(parent_target) if parent_target else 1
    n_witness_needed = max(1, n_witness_needed)
    witness: list[str] = []
    prop: list[str] = []
    other: list[str] = []
    for a in aux:
        name = str(a.get("aux_name", "")).strip()
        if not name:
            continue
        sig = str(a.get("aux_signature", "")).strip()
        if sig:
            atype = classify_aux_type(sig)
        else:
            atype = "unknown"
        if atype == "witness_producing":
            witness.append(name)
        elif atype in ("property_establishing", "equational"):
            prop.append(name)
        else:
            other.append(name)
    # Type-mismatch guard: if the parent needs witnesses but NO aux is
    # witness-producing, return empty roles so the emitter declines.
    if not witness:
        return {"witness": [], "prop": []}
    # Enforce enough witnesses for the parent's outer existential.
    if len(witness) < n_witness_needed:
        return {"witness": [], "prop": []}
    prop.extend(other)
    return {"witness": witness, "prop": prop}


def render_composition_attempts(
    *,
    parent_target_shape: str,
    aux_names: list[str],
    aux_records: Optional[list[dict[str, Any]]] = None,
    parent_target: str = "",
) -> list[str]:
    """Return a list of proof-body candidates to try (in order) for
    composing the aux into the parent.

    `parent_target_shape` may be EITHER a legacy coarse label
    ('and'/'exists'/'iff'/'other') OR a fine label as produced by
    `detect_target_shape_fine`. Fine labels select the richer skeleton set
    introduced in Round-VIII.

    `aux_records` (optional) is the list of {aux_name, compose_hint, ...}
    records used by `assign_aux_roles`. When omitted, role assignment falls
    back to positional order. When records include `aux_signature`, role
    assignment switches to the v3 type-aware mapper for witness shapes.

    `parent_target` (optional) is the parent's target string. When present
    AND the shape is a witness shape, v3 uses it to count the number of
    outer existential binders the parent needs — type-mismatch (parent
    needs N witnesses but only properties are available) yields an empty
    composition list rather than a spurious `exact ⟨prop_aux⟩` body.

    Each candidate is a fully-formed tactic body (suitable for the
    `proof_body` slot used by `sweep_leanstral_whole_proof._patch_proof_flex`).
    """
    if not aux_names:
        return []
    names = list(aux_names)
    shape = (parent_target_shape or "other").strip()
    # If the caller passed a coarse label, lift it to the matching fine
    # default (so we still get richer skeletons than the v1 set).
    _COARSE_TO_FINE_DEFAULT = {
        "and": "conjunction_with_ineq",
        "exists": "exists_with_witness",
        "iff": "iff_bidirectional",
        "other": "other",
    }
    if shape in _COARSE_TO_FINE_DEFAULT:
        shape = _COARSE_TO_FINE_DEFAULT[shape]

    # Build records list if not given (positional-order roles).
    if aux_records is None:
        aux_records = [{"aux_name": nm, "compose_hint": ""} for nm in names]
    # Composition v3: when records include `aux_signature` AND the parent
    # is a witness shape, use the type-aware mapper. Type-mismatch yields
    # an EMPTY composition list — emitter declines to synthesize.
    use_v3 = (
        shape in ("exists_with_witness", "exists_with_prop", "nested_exists")
        and any(str(r.get("aux_signature", "")).strip() for r in aux_records)
    )
    if use_v3:
        roles_v3 = assign_aux_roles_v3(
            shape=shape, aux=aux_records, parent_target=parent_target,
        )
        # Type-mismatch: parent needs witness but no witness-producing aux.
        if not roles_v3.get("witness"):
            return []
        roles = roles_v3
        # If parent needs >=2 outer existential witnesses AND we have >=2
        # witness-producing aux, emit a nested-obtain composition.
        n_witness_needed = count_outer_existential_binders(parent_target)
        if n_witness_needed >= 2 and len(roles_v3.get("witness", [])) >= n_witness_needed:
            ws = roles_v3["witness"][:n_witness_needed]
            ps = roles_v3.get("prop", [])
            obtains = []
            wnames = []
            for i, wname in enumerate(ws, start=1):
                obtains.append(f"obtain ⟨w{i}, _⟩ := {wname}")
                wnames.append(f"w{i}")
            pack_args = wnames + ps
            bodies_nested: list[str] = []
            if ps:
                # form 1: pass property aux as-is.
                bodies_nested.append(
                    "\n  ".join(obtains + [f"exact ⟨{', '.join(pack_args)}⟩"])
                )
                # form 2: apply property aux to the witnesses.
                applied_args = wnames + [f"{ps[0]} {' '.join(wnames)}"]
                bodies_nested.append(
                    "\n  ".join(obtains + [f"exact ⟨{', '.join(applied_args)}⟩"])
                )
                # form 3: refine + property under each witness via `exact`.
                holes = ", ".join(["?_"] * (n_witness_needed + len(ps)))
                lines = [f"refine ⟨{holes}⟩"]
                for nm in ws:
                    lines.append(f"  · exact {nm}.choose")
                for nm in ps:
                    lines.append(f"  · exact {nm}")
                bodies_nested.append("\n".join(lines))
            else:
                bodies_nested.append(
                    "\n  ".join(obtains + [f"exact ⟨{', '.join(wnames)}⟩"])
                )
            seen: set[str] = set()
            deduped: list[str] = []
            for c in bodies_nested:
                if not c or c in seen:
                    continue
                seen.add(c)
                deduped.append(c)
            return deduped
    else:
        roles = assign_aux_roles(shape=shape, aux=aux_records)

    out: list[str] = []
    if shape == "exists_with_witness":
        witness = roles.get("witness", [])
        prop = roles.get("prop", [])
        if witness and prop:
            w = witness[0]
            p = prop[0]
            # ⟨witness, proof⟩
            out.append(f"exact ⟨{w}, {p}⟩")
            # obtain witness first, then build the proof
            out.append(
                f"obtain ⟨w, hw⟩ := {w}\n  exact ⟨w, {p}⟩"
            )
            # refine with hole
            out.append(f"refine ⟨{w}, ?_⟩\n  exact {p}")
            # Single-pack form: exact ⟨w, h₁, h₂, ...⟩
            if len(prop) >= 2:
                tup = ", ".join([w] + prop)
                out.append(f"exact ⟨{tup}⟩")
        elif len(names) >= 2:
            tup = ", ".join(names)
            out.append(f"exact ⟨{tup}⟩")
        elif len(names) == 1:
            out.append(f"exact {names[0]}")

    elif shape == "exists_with_prop":
        # The witness aux provides the existential value (or both the value
        # and the propositions, packaged); the prop aux fills the remaining
        # conjunctive body.
        witness = roles.get("witness", [])
        prop = roles.get("prop", [])
        if witness and prop:
            w = witness[0]
            tup = ", ".join([w] + prop)
            out.append(f"exact ⟨{tup}⟩")
            refine_holes = ", ".join(["?_"] * (1 + len(prop)))
            lines = [f"refine ⟨{refine_holes}⟩", f"  · exact {w}"]
            for nm in prop:
                lines.append(f"  · exact {nm}")
            out.append("\n".join(lines))
        elif len(names) >= 2:
            tup = ", ".join(names)
            out.append(f"exact ⟨{tup}⟩")

    elif shape == "nested_exists":
        witness = roles.get("witness", [])
        prop = roles.get("prop", [])
        # `obtain ⟨w₁, _⟩ := aux1; obtain ⟨w₂, _⟩ := aux2; exact ⟨w₁, w₂, ...⟩`.
        if len(witness) >= 2:
            lines = []
            for i, w in enumerate(witness, start=1):
                lines.append(f"obtain ⟨w{i}, h{i}⟩ := {w}")
            tup = ", ".join([f"w{i}" for i in range(1, len(witness) + 1)])
            # Pack hypotheses too.
            htup = ", ".join([f"h{i}" for i in range(1, len(witness) + 1)])
            lines.append(f"exact ⟨{tup}, {htup}⟩")
            out.append("\n  ".join(lines))
        if len(names) >= 2:
            tup = ", ".join(names)
            out.append(f"exact ⟨{tup}⟩")

    elif shape == "iff_bidirectional":
        fwd = roles.get("fwd", [])
        bwd = roles.get("bwd", [])
        if fwd and bwd:
            f, b = fwd[0], bwd[0]
            out.append(f"exact ⟨{f}, {b}⟩")
            out.append(f"refine ⟨?_, ?_⟩\n  · exact {f}\n  · exact {b}")
            out.append(f"constructor\n  · exact {f}\n  · exact {b}")
        elif len(names) >= 2:
            out.append(f"exact ⟨{names[0]}, {names[1]}⟩")
            out.append(f"constructor\n  · exact {names[0]}\n  · exact {names[1]}")

    elif shape == "implication":
        body = roles.get("body", [])
        nm = body[0] if body else (names[0] if names else "")
        if nm:
            out.append(f"intro h\n  exact {nm} h")
            out.append(f"exact {nm}")

    elif shape in ("universal_implication", "universal_with_bound"):
        body = roles.get("body", [])
        nm = body[0] if body else (names[0] if names else "")
        if nm:
            out.append(f"intro n hN\n  exact {nm} n hN")
            out.append(f"intro n h\n  exact {nm} n h")
            out.append(f"intro n\n  exact {nm} n")
            out.append(f"exact {nm}")

    elif shape == "calc_chain":
        steps = roles.get("step", [])
        if len(steps) >= 2:
            # We can't reliably synthesize the intermediate calc terms
            # without parsing each aux's target, so we emit a
            # `Trans.trans`-style chain (works for ≤/=/<) plus a calc-style
            # scaffold the LLM can adapt. Emitter focuses on the simplest
            # closing forms.
            out.append("exact " + ".trans ".join(steps))
            out.append("exact le_trans " + (" (le_trans ".join(steps[:-1]) + " " + steps[-1] + ")" * (len(steps) - 1)))
            # `calc` with named placeholder identifiers — generic skeleton.
            lines = ["calc"]
            # The first step rewrites the lhs to mid1; we can't infer the
            # mid term, so we leave the structure for the lake validator to
            # check (it will fail unless the aux targets line up — which
            # implies the LLM picked aligned aux).
            lines.append(f"  _ ≤ _ := {steps[0]}")
            for s in steps[1:]:
                lines.append(f"  _ ≤ _ := {s}")
            out.append("\n".join(lines))

    elif shape == "disjunction":
        left = roles.get("left", [])
        right = roles.get("right", [])
        if left:
            out.append(f"exact Or.inl {left[0]}")
            out.append(f"left\n  exact {left[0]}")
        if right:
            out.append(f"exact Or.inr {right[0]}")
            out.append(f"right\n  exact {right[0]}")

    elif shape in ("conjunction_with_ineq", "and"):
        conjuncts = roles.get("conjunct", names)
        if len(conjuncts) == 2:
            out.append(f"exact ⟨{conjuncts[0]}, {conjuncts[1]}⟩")
            out.append(f"refine ⟨?_, ?_⟩\n  · exact {conjuncts[0]}\n  · exact {conjuncts[1]}")
            out.append(f"constructor\n  · exact {conjuncts[0]}\n  · exact {conjuncts[1]}")
        elif len(conjuncts) > 2:
            tup = ", ".join(conjuncts)
            out.append(f"exact ⟨{tup}⟩")
            refine_holes = ", ".join("?_" for _ in conjuncts)
            lines = [f"refine ⟨{refine_holes}⟩"]
            for nm in conjuncts:
                lines.append(f"  · exact {nm}")
            out.append("\n".join(lines))

    # Always include conservative fall-throughs:
    out.append(f"exact ⟨{', '.join(names)}⟩")
    if len(names) >= 2:
        out.append(f"refine ⟨{', '.join('?_' for _ in names)}⟩ <;> first | exact {' | exact '.join(names)}")

    # De-dup while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for c in out:
        if c in seen:
            continue
        seen.add(c)
        deduped.append(c)
    return deduped


# --- Body normalization (mirrors v1) --------------------------------------


_IDENT_SAFE_RX = re.compile(r"[^A-Za-z0-9_']")


def _sanitize_aux_name(raw_name: str, base: str, idx: int) -> str:
    nm = (raw_name or "").strip().rsplit(".", 1)[-1]
    nm = _IDENT_SAFE_RX.sub("_", nm).strip("_")
    if not nm or not re.match(r"^[A-Za-z_]", nm):
        base_safe = _IDENT_SAFE_RX.sub("_", (base or "thm").rsplit(".", 1)[-1]).strip("_") or "thm"
        return f"{base_safe}_aux_{idx}"
    return nm


def _normalize_aux_decl(raw: str, aux_name: str) -> str:
    decl = (raw or "").strip()
    if not decl:
        return ""
    if decl.startswith("```"):
        decl = re.sub(r"^```(?:lean)?\s*", "", decl)
        decl = re.sub(r"\s*```\s*$", "", decl)
    decl = decl.strip()
    decl = re.sub(r"^(?:Lean:|Output:|Answer:)\s*", "", decl, flags=re.IGNORECASE)
    decl = re.sub(r"^lemma\s+", "theorem ", decl)
    if re.match(r"^\s*theorem\s+[A-Za-z_]", decl):
        decl = re.sub(
            r"^(\s*)theorem\s+[A-Za-z_][A-Za-z0-9_'.]*",
            rf"\1theorem {aux_name}",
            decl,
            count=1,
        )
    else:
        decl = f"theorem {aux_name} {decl.lstrip()}"
    # Force body to `:= by sorry`.
    decl = re.sub(r":=\s*by\s+.+$", ":= by sorry", decl, flags=re.DOTALL).strip()
    if not decl.endswith(":= by sorry"):
        decl = re.sub(r":=.*$", "", decl, flags=re.DOTALL).strip()
        decl = decl + " := by sorry"
    return decl


# --- JSON extraction (mirrors v1) -----------------------------------------


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(raw[start: end + 1])
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


# --- LLM transport (mirrors v1) -------------------------------------------


def _call(
    *,
    client: Any,
    model: str,
    user: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    api_log_hook: Optional[Any] = None,
) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_V2},
        {"role": "user", "content": user},
    ]
    try:  # pragma: no cover - prefer telemetry path
        from ponder_loop import _chat_complete  # type: ignore[import-not-found]

        _, text = _chat_complete(
            client=client,
            model=model,
            messages=messages,
            temperature=0.0,
            max_tokens=max_tokens,
            purpose="lemma_factor_v2",
            api_log_hook=api_log_hook,
        )
        return (text or "").strip()
    except Exception:
        response = client.chat.complete(
            model=model,
            messages=messages,
            temperature=0.0,
            max_tokens=max_tokens,
        )
        text = ""
        if hasattr(response, "choices") and response.choices:
            msg = response.choices[0].message
            text = getattr(msg, "content", "") or ""
        return text.strip()


# --- Public API -----------------------------------------------------------


def build_user_prompt(
    *,
    paper_id: str,
    theorem_name: str,
    lean_statement: str,
    paper_theory_hint: str,
    exported_symbols: str,
    audited_core_hint: Optional[str] = None,
) -> str:
    """Construct the user-message prompt for v2. Exposed for tests.

    `audited_core_hint` (B3): if None, load from
    `data/paper_audited_proof_hints/<paper_id>.txt` automatically. Pass
    "" to disable.
    """
    parsed_name, binder_block, parent_target = split_parent_statement(lean_statement)
    parent_name = (theorem_name or parsed_name or "thm").strip()
    full_stmt = re.sub(r"[ \t]+", " ", lean_statement or "").strip()[:MAX_STATEMENT_CHARS]
    hint = (paper_theory_hint or "").strip()[:MAX_HINT_CHARS]
    exports = (exported_symbols or "").strip()[:MAX_EXPORTS_CHARS]
    binders_trimmed = (binder_block or "").strip()[:MAX_BINDER_CHARS]
    target_trimmed = (parent_target or "").strip()[:MAX_STATEMENT_CHARS]

    audited_hint = audited_core_hint
    if audited_hint is None:
        try:
            from extract_audited_core_hints import load_hint as _load_audited_hint  # type: ignore[import-not-found]
            audited_hint = _load_audited_hint(paper_id or "")
        except Exception:
            audited_hint = ""
    audited_hint = (audited_hint or "").strip()
    audited_section = ""
    if audited_hint:
        if len(audited_hint) > MAX_AUDITED_CORE_CHARS_V2:
            audited_hint = audited_hint[:MAX_AUDITED_CORE_CHARS_V2] + "\n-- ... (truncated) ..."
        audited_section = _AUDITED_CORE_SECTION_TEMPLATE.format(audited_core_hint=audited_hint)

    return _USER_TEMPLATE.format(
        parent_name=parent_name,
        binder_block=binders_trimmed or "-- (no explicit binders)",
        parent_target=target_trimmed or "-- (target unparseable)",
        full_statement=full_stmt,
        paper_theory_hint=hint or "-- (no paper-local symbols exported)",
        exported_symbols=exports or "-- (no exports detected)",
        audited_core_section=audited_section,
    )


def factor_long_theorem_v2(
    *,
    paper_id: str,
    theorem_name: str,
    lean_statement: str,
    paper_theory_hint: str,
    exported_symbols: str,
    client: Any,
    model: str = DEFAULT_MODEL,
    api_log_hook: Optional[Any] = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    validate_elaboration: Optional[Callable[[str], tuple[bool, str]]] = None,
    max_aux: int = MAX_AUX_DEFAULT,
    min_aux: int = MIN_AUX_DEFAULT,
    audited_core_hint: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Ask Leanstral (v2 binder-preserving) to propose aux lemmas.

    Returns a list of records (same shape as v1's `factor_long_theorem`,
    plus `protocol='lemma_factor_v2'`, `parent_binder_block`,
    `parent_target`, `parent_target_shape`).

    Standards-positive gates (applied before optional elaboration):
      - sanitize aux name
      - normalize body to `:= by sorry`
      - reject placeholder targets (`Statement_*`, `PaperClaim`, `True`, ...)
      - reject trivialized signatures
      - reject forbidden tokens in target
    """
    if not (lean_statement or "").strip():
        return []
    if client is None:
        return []
    max_aux = max(1, min(int(max_aux or MAX_AUX_DEFAULT), 10))
    min_aux = max(1, min(int(min_aux or MIN_AUX_DEFAULT), max_aux))

    parsed_name, binder_block, parent_target = split_parent_statement(lean_statement)
    parent_name = (theorem_name or parsed_name or "thm").strip().rsplit(".", 1)[-1] or "thm"
    parent_shape = detect_target_shape(parent_target)
    parent_shape_fine = detect_target_shape_fine(parent_target)

    user = build_user_prompt(
        paper_id=paper_id,
        theorem_name=parent_name,
        lean_statement=lean_statement,
        paper_theory_hint=paper_theory_hint,
        exported_symbols=exported_symbols,
        audited_core_hint=audited_core_hint,
    )

    try:
        raw = _call(
            client=client,
            model=model,
            user=user,
            max_tokens=max_tokens,
            api_log_hook=api_log_hook,
        )
    except Exception:
        return []

    parsed = _extract_json_object(raw)
    if not parsed:
        return []

    verdict = str(parsed.get("verdict", "") or "").strip().upper()
    if verdict == "REFUSE":
        return []

    aux_raw = parsed.get("aux_lemmas")
    if not isinstance(aux_raw, list) or not aux_raw:
        return []

    compose_strategy = str(parsed.get("compose_strategy", "") or "").strip()
    overall_reasoning = str(parsed.get("reasoning", "") or "").strip()
    overall_confidence = _clamp01(parsed.get("confidence", 0.0))

    kept: list[dict[str, Any]] = []
    for idx, entry in enumerate(aux_raw[:max_aux], start=1):
        if not isinstance(entry, dict):
            continue
        raw_name = str(entry.get("aux_name", "") or "")
        raw_sig = str(entry.get("aux_signature", "") or "")
        compose_hint = str(entry.get("compose_hint", "") or "").strip()
        aux_name = _sanitize_aux_name(raw_name, parent_name, idx)
        decl = _normalize_aux_decl(raw_sig, aux_name)
        if not decl:
            continue
        # Translator-side cleanup (lambda -> lam, etc.). Reuses v1.
        try:
            from translator._translate import _deterministic_signature_cleanup  # type: ignore[import-not-found]
            decl = _deterministic_signature_cleanup(decl)
        except Exception:
            pass

        rejected: list[str] = []
        if _lfa_v1._is_placeholder_decl(decl):
            rejected.append("placeholder_pattern_detected")
        if _lfa_v1._is_trivialized_signature(decl):
            rejected.append("trivialized_signature")
        trivial_flag = _target_is_trivial(decl)
        if trivial_flag is not None:
            rejected.append(trivial_flag)

        elab_ok: Optional[bool] = None
        elab_err = ""
        if not rejected and validate_elaboration is not None:
            try:
                ok, err = validate_elaboration(decl)
            except Exception as exc:  # pragma: no cover - defensive
                ok, err = False, f"elaboration_validator_exception:{type(exc).__name__}:{exc}"
            elab_ok = bool(ok)
            elab_err = (err or "")[-MAX_LEAN_ERROR_TAIL_CHARS:]
            if not ok:
                rejected.append("elaboration_gate")

        record = {
            "aux_name": aux_name,
            "aux_signature": decl,
            "compose_hint": compose_hint,
            "rejected": rejected,
            "compose_strategy": compose_strategy,
            "overall_reasoning": overall_reasoning,
            "overall_confidence": overall_confidence,
            "elaboration_ok": elab_ok,
            "elaboration_error": elab_err,
            "protocol": "lemma_factor_v2",
            "paper_id": paper_id,
            "parent_theorem_name": parent_name,
            "parent_binder_block": binder_block,
            "parent_target": parent_target,
            "parent_target_shape": parent_shape,
            "parent_target_shape_fine": parent_shape_fine,
        }
        kept.append(record)

    return kept


# --- Recursive factoring (depth-2+) ---------------------------------------
#
# Round-X showed 9/27 aux closed at depth-1. Many of the surviving (unclosed)
# aux were themselves long (>200 chars) or multi-conjunction targets — i.e.
# they have ROOM to factor further. `factor_long_theorem_recursive` applies
# the SAME factoring prompt to each unclosed-and-still-long aux up to a hard
# depth cap.
#
# Termination guarantee:
#   - `max_depth` is a hard cap (default 2, validated 0..4).
#   - At each depth we only recurse on aux that BOTH (a) didn't close at
#     this level (their whole_proof attempt left a `sorry` body) AND (b)
#     have a "long" signature (>LONG_SIGNATURE_CHAR_THRESHOLD chars OR
#     contains a multi-conjunction in target).
#   - Each level applies `max_aux_per_level` (default 5) — so the worst-
#     case branching factor is 5 and the worst-case node count is bounded
#     by 5^max_depth ≤ 5^4 = 625. In practice the "long" gate filters
#     aggressively so the tree stays small.
#   - The recursion is depth-first; we never re-visit a closed aux.

LONG_SIGNATURE_CHAR_THRESHOLD = 200
MULTI_CONJUNCTION_THRESHOLD = 3  # >=3 `∧` in target ⇒ multi-conjunction


def _aux_signature_is_long(aux_signature: str) -> bool:
    """Return True when an aux signature is "long enough" to merit a
    recursive factoring attempt.

    Two triggers (logical OR):
      - raw signature length > LONG_SIGNATURE_CHAR_THRESHOLD chars (200)
      - target portion contains >=3 top-level `∧` (multi-conjunction)
    """
    sig = (aux_signature or "").strip()
    if not sig:
        return False
    if len(sig) > LONG_SIGNATURE_CHAR_THRESHOLD:
        return True
    target = _extract_aux_target(sig)
    if not target:
        return False
    # Count top-level `∧` occurrences. We don't need full depth-tracking
    # here — `∧` rarely appears inside parens at the top of an aux target.
    n_and = target.count("∧")
    return n_and >= MULTI_CONJUNCTION_THRESHOLD


def factor_long_theorem_recursive(
    *,
    paper_id: str,
    theorem_name: str,
    lean_statement: str,
    paper_theory_hint: str,
    client: Any,
    model: str = DEFAULT_MODEL,
    max_depth: int = 2,
    max_aux_per_level: int = MAX_AUX_DEFAULT,
    min_aux: int = MIN_AUX_DEFAULT,
    exported_symbols: str = "",
    audited_core_hint: Optional[str] = None,
    validate_elaboration: Optional[Callable[[str], tuple[bool, str]]] = None,
    whole_proof_attempt: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
    api_log_hook: Optional[Any] = None,
    _depth: int = 0,
) -> dict[str, Any]:
    """Recursively factor a long theorem into sub-aux up to ``max_depth``.

    At each level we:
      1. Run `factor_long_theorem_v2` on the current target.
      2. For each aux that elaborates, optionally run ``whole_proof_attempt``
         (the caller's closer — when None, we treat ALL aux as unclosed so
         the tree-shape under test in unit suites is deterministic).
      3. For each aux that DIDN'T close AND has a "long" signature AND
         ``_depth + 1 < max_depth``, recurse with `_depth + 1`.

    Returns a dict shaped::

        {
          "depth":           0,
          "theorem_name":    "...",
          "aux":             [
              {
                "aux_name":          "...",
                "aux_signature":     "...",
                "compose_hint":      "...",
                "rejected":          [...],
                "elaboration_ok":    True | False | None,
                "closed":            True | False,
                "close_result":      {...} (whatever whole_proof_attempt returned),
                "sub_factor":        <nested dict same shape> | None,
                "long_enough":       True | False,
                # ... plus all the standard lemma_factor_v2 fields
              },
              ...
          ],
          "telemetry": {
              "attempts":   <int>   # how many recursive factoring calls this subtree made
              "closures":   <int>   # how many aux closed in this subtree
              "sub_aux_closures": <int>   # closures at depth > _depth
          },
        }

    Caller responsibilities:
      - ``whole_proof_attempt`` MUST return a dict shaped
        ``{"closed": bool, ...}``. When omitted we set ``closed=False`` for
        every aux (so recursion happens whenever ``long_enough`` holds).
      - ``validate_elaboration`` is passed straight through to
        ``factor_long_theorem_v2`` at each level.
      - ``max_depth`` is clamped to [0, 4]. When ``max_depth=0`` we run the
        depth-0 factoring pass and never recurse (the result still records
        per-aux ``closed`` from the caller's ``whole_proof_attempt``).
    """
    max_depth = max(0, min(int(max_depth), 4))
    max_aux_per_level = max(1, min(int(max_aux_per_level or MAX_AUX_DEFAULT), 10))
    min_aux = max(1, min(int(min_aux or MIN_AUX_DEFAULT), max_aux_per_level))

    telemetry = {"attempts": 1, "closures": 0, "sub_aux_closures": 0}

    factor_records = factor_long_theorem_v2(
        paper_id=paper_id,
        theorem_name=theorem_name,
        lean_statement=lean_statement,
        paper_theory_hint=paper_theory_hint,
        exported_symbols=exported_symbols,
        client=client,
        model=model,
        validate_elaboration=validate_elaboration,
        max_aux=max_aux_per_level,
        min_aux=min_aux,
        audited_core_hint=audited_core_hint,
        api_log_hook=api_log_hook,
    )

    aux_results: list[dict[str, Any]] = []
    for rec in factor_records:
        out_rec = dict(rec)
        out_rec.setdefault("closed", False)
        out_rec["close_result"] = None
        out_rec["sub_factor"] = None
        out_rec["long_enough"] = False

        # Skip closing attempts entirely on rejected aux.
        if rec.get("rejected"):
            aux_results.append(out_rec)
            continue

        # Step 2: ask the caller to close this aux.
        close_result: dict[str, Any] = {"closed": False}
        if whole_proof_attempt is not None:
            try:
                close_result = whole_proof_attempt(rec) or {"closed": False}
            except Exception as exc:  # pragma: no cover - defensive
                close_result = {"closed": False, "error": f"{type(exc).__name__}:{exc}"}
        out_rec["close_result"] = close_result
        is_closed = bool(close_result.get("closed", False))
        out_rec["closed"] = is_closed
        if is_closed:
            telemetry["closures"] += 1
            aux_results.append(out_rec)
            continue

        # Step 3: should we recurse on this aux?
        sig = str(rec.get("aux_signature", "") or "")
        long_enough = _aux_signature_is_long(sig)
        out_rec["long_enough"] = long_enough
        if not long_enough or (_depth + 1) >= max_depth:
            aux_results.append(out_rec)
            continue

        # Recurse: factor THIS aux as the next-level parent.
        sub_factor = factor_long_theorem_recursive(
            paper_id=paper_id,
            theorem_name=str(rec.get("aux_name", "") or theorem_name),
            lean_statement=sig,
            paper_theory_hint=paper_theory_hint,
            client=client,
            model=model,
            max_depth=max_depth,
            max_aux_per_level=max_aux_per_level,
            min_aux=min_aux,
            exported_symbols=exported_symbols,
            audited_core_hint=audited_core_hint,
            validate_elaboration=validate_elaboration,
            whole_proof_attempt=whole_proof_attempt,
            api_log_hook=api_log_hook,
            _depth=_depth + 1,
        )
        out_rec["sub_factor"] = sub_factor
        # Roll up sub-tree telemetry.
        sub_tel = sub_factor.get("telemetry", {}) if isinstance(sub_factor, dict) else {}
        telemetry["attempts"] += int(sub_tel.get("attempts", 0) or 0)
        sub_closures = int(sub_tel.get("closures", 0) or 0)
        telemetry["closures"] += sub_closures
        telemetry["sub_aux_closures"] += sub_closures + int(
            sub_tel.get("sub_aux_closures", 0) or 0
        )
        # If ALL sub-aux of THIS aux closed, mark this aux as "closed-via-sub".
        sub_aux = sub_factor.get("aux", []) if isinstance(sub_factor, dict) else []
        elaborated_sub = [s for s in sub_aux if not s.get("rejected")]
        if elaborated_sub and all(bool(s.get("closed")) for s in elaborated_sub):
            out_rec["closed_via_sub"] = True
        else:
            out_rec["closed_via_sub"] = False
        aux_results.append(out_rec)

    return {
        "depth": _depth,
        "theorem_name": theorem_name,
        "lean_statement": lean_statement,
        "aux": aux_results,
        "telemetry": telemetry,
        "protocol": "lemma_factor_v2_recursive",
        "max_depth": max_depth,
    }


# --- JSONL writer (mirrors v1; protocol field is auto-discriminating) -----


def write_lemma_factor_v2_jsonl(
    *,
    candidates: list[dict[str, Any]],
    output_path: Path,
    append: bool = True,
) -> int:
    """Write per-aux candidate rows to JSONL. Returns count of rows written."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    written = 0
    with output_path.open(mode, encoding="utf-8") as fh:
        for rec in candidates:
            row = dict(rec)
            row["row_id"] = "::".join(
                [
                    str(rec.get("paper_id", "")),
                    str(rec.get("parent_theorem_name", "")),
                    str(rec.get("aux_name", "")),
                ]
            )
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            written += 1
    return written
