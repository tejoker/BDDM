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


# In-context examples drawn directly from
# `Desol/PaperProofs/Paper_2604_21884.lean` (`remark_20_param_roles` +
# `rem_primitive_route_witness`). Each example shows ONE aux lemma. We trim
# to the SIGNATURE form `:= by sorry` so the LLM mimics that shape (it must
# emit aux SIGNATURES only; the whole-proof generator closes them later).
EXAMPLE_AUX_LEMMAS: tuple[dict[str, str], ...] = (
    {
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
    {
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
        pieces.append(
            f"--- Example {i}: parent `{ex['parent_name']}` ---\n"
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
    "Propose 2-5 auxiliary lemmas now. Respond with the JSON object only."
)


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


def detect_target_shape(parent_target: str) -> str:
    """Classify the parent's target shape: 'and' / 'iff' / 'exists' / 'other'.

    Used to pick the composition strategy. Whitespace-tolerant.
    """
    t = re.sub(r"\s+", " ", (parent_target or "").strip())
    if not t:
        return "other"
    # Strip trailing `:= by sorry` defensively (in case the caller passed
    # the whole statement).
    t = re.sub(r":=\s*by[\s\S]*$", "", t).strip()
    # ∃ at the start (after optional leading parens) -> 'exists'. We check
    # this BEFORE conjunction because `∃ x, P ∧ Q` is existential-shaped at
    # the top. Use a Unicode-tolerant lookahead instead of `\b` (which does
    # not match around non-ASCII codepoints).
    if re.match(r"^[(\s]*∃(?=\s|$)", t):
        return "exists"
    # Iff anywhere at depth 0.
    if "↔" in t:
        return "iff"
    # Top-level And (∧): the simplest heuristic is "has a depth-0 ∧". We
    # don't fully track depth here; conservatively, any `∧` outside binders
    # suggests conjunction-shape (only reached when target does NOT start
    # with `∃`).
    if "∧" in t:
        return "and"
    return "other"


def render_composition_attempts(
    *,
    parent_target_shape: str,
    aux_names: list[str],
) -> list[str]:
    """Return a list of proof-body candidates to try (in order) for
    composing the aux into the parent.

    Each candidate is a fully-formed tactic body (suitable for the
    `proof_body` slot used by `sweep_leanstral_whole_proof._patch_proof_flex`).
    """
    if not aux_names:
        return []
    names = list(aux_names)
    binders_call = " ".join(names)
    out: list[str] = []
    if parent_target_shape == "and":
        # Try several shapes:
        if len(names) == 2:
            out.append(f"exact ⟨{names[0]}, {names[1]}⟩")
            out.append(f"refine ⟨?_, ?_⟩\n  · exact {names[0]}\n  · exact {names[1]}")
            out.append(f"constructor\n  · exact {names[0]}\n  · exact {names[1]}")
        else:
            tup = ", ".join(names)
            out.append(f"exact ⟨{tup}⟩")
            refine_holes = ", ".join("?_" for _ in names)
            lines = [f"refine ⟨{refine_holes}⟩"]
            for nm in names:
                lines.append(f"  · exact {nm}")
            out.append("\n".join(lines))
    elif parent_target_shape == "exists":
        if len(names) == 1:
            out.append(f"exact {names[0]}")
        elif len(names) >= 2:
            # First aux is typically the witness/positivity, rest are
            # property proofs. Try a couple of orderings.
            tup = ", ".join(names)
            out.append(f"exact ⟨{tup}⟩")
            holes = ", ".join("?_" for _ in names)
            lines = [f"refine ⟨{holes}⟩"]
            for nm in names:
                lines.append(f"  · exact {nm}")
            out.append("\n".join(lines))
    elif parent_target_shape == "iff":
        if len(names) >= 2:
            out.append(f"exact ⟨{names[0]}, {names[1]}⟩")
            out.append(f"constructor\n  · exact {names[0]}\n  · exact {names[1]}")
    # Always include a fall-through:
    out.append(f"exact ⟨{', '.join(names)}⟩")
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
) -> str:
    """Construct the user-message prompt for v2. Exposed for tests."""
    parsed_name, binder_block, parent_target = split_parent_statement(lean_statement)
    parent_name = (theorem_name or parsed_name or "thm").strip()
    full_stmt = re.sub(r"[ \t]+", " ", lean_statement or "").strip()[:MAX_STATEMENT_CHARS]
    hint = (paper_theory_hint or "").strip()[:MAX_HINT_CHARS]
    exports = (exported_symbols or "").strip()[:MAX_EXPORTS_CHARS]
    binders_trimmed = (binder_block or "").strip()[:MAX_BINDER_CHARS]
    target_trimmed = (parent_target or "").strip()[:MAX_STATEMENT_CHARS]
    return _USER_TEMPLATE.format(
        parent_name=parent_name,
        binder_block=binders_trimmed or "-- (no explicit binders)",
        parent_target=target_trimmed or "-- (target unparseable)",
        full_statement=full_stmt,
        paper_theory_hint=hint or "-- (no paper-local symbols exported)",
        exported_symbols=exports or "-- (no exports detected)",
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

    user = build_user_prompt(
        paper_id=paper_id,
        theorem_name=parent_name,
        lean_statement=lean_statement,
        paper_theory_hint=paper_theory_hint,
        exported_symbols=exported_symbols,
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
        }
        kept.append(record)

    return kept


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
