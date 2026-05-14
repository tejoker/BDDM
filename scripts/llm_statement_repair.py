#!/usr/bin/env python3
"""LLM-driven statement repair candidate generator (Leanstral).

The legacy `repair_bad_translations.build_repair_pack` is pure deterministic
string rewriting; in the April-2026 repair-pack audit, 46/48 candidates were
nonsense (opaque `Statement` Prop references, `True` hypotheses, or trivial
`∃ x : ℝ, x = x` shapes). This module adds an LLM-driven generator that
formalizes the original LaTeX claim into a single Lean 4 theorem signature
with a `:= by sorry` body.

Design constraints (per repo policy):
  - Leanstral is the ONLY model the pipeline may call.
  - Generated signatures are rejected if they trigger
    `translator._translate._is_trivialized_signature`.
  - Default OFF in the worker; gate behind `--use-llm-repair` until calibrated.

The public API mirrors the existing `leanstral_*` helpers:
  generate_llm_repair_candidate(*, source_latex, paper_id, theorem_name,
                                paper_theory_hint, client, model, ...)
  → {'repaired_decl', 'reasoning', 'confidence', 'protocol', 'rejected'}
  or None on failure.
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

try:
    from translator._translate import _is_trivialized_signature  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - fallback for unusual import topologies
    def _is_trivialized_signature(sig: str) -> bool:  # type: ignore[misc]
        target = (sig or "").strip().lower()
        if not target:
            return True
        if "true" in target.split(":")[-1]:
            return True
        if re.search(r"∃\s+\w+\s*:\s*ℝ\s*,\s*\w+\s*=\s*\w+", target):
            return True
        return False

try:
    from translator._translate import _deterministic_signature_cleanup  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - fallback when translator package is shaped differently
    def _deterministic_signature_cleanup(sig: str) -> str:  # type: ignore[misc]
        # No-op fallback; the LLM output goes through unmodified.
        return sig or ""


DEFAULT_MODEL = os.getenv("MISTRAL_MODEL", "labs-leanstral-2603")
DEFAULT_MAX_TOKENS = 768
MAX_LATEX_CHARS = 2000
MAX_HINT_CHARS = 1500
DEFAULT_MAX_REPAIR_ROUNDS = 3
MAX_LEAN_ERROR_TAIL_CHARS = 500


_SYSTEM_PROMPT = (
    "You are a research assistant that formalizes mathematical claims into "
    "Lean 4 theorem signatures. You receive (1) a LaTeX statement of a theorem "
    "from an arXiv paper and (2) a paper-theory hint listing the paper-local "
    "Lean definitions, abbreviations, and axioms that are already in scope.\n\n"
    "Your job is to produce a SINGLE Lean 4 theorem declaration that faithfully "
    "formalizes the LaTeX claim, using only paper-local symbols and Mathlib.\n\n"
    "STRICT RULES:\n"
    "  1. The declaration MUST start with `theorem ` (not `lemma`, `def`, "
    "     `abbrev`, `instance`, etc.).\n"
    "  2. The body MUST be exactly ` := by sorry` (a single `sorry`, no proof).\n"
    "  3. Do NOT use placeholder bodies like `: True`, `∃ x : ℝ, x = x`, "
    "     `0 = 0`, opaque `Statement`/`PaperClaim` Prop aliases, or any shape "
    "     that does not encode actual mathematical content.\n"
    "  4. Quantifiers, hypotheses, and the conclusion must reflect the LaTeX. "
    "     If the LaTeX is genuinely informal/procedural and cannot be "
    "     formalized as a theorem, return verdict=`REFUSE`.\n"
    "  5. Use ASCII-friendly Lean: Unicode `∀ ∃ → ↔ ≤ ≥ ℝ ℕ ℤ` are fine, but "
    "     avoid LaTeX-only macros (`\\frac`, `\\mathbb`, etc.).\n\n"
    "Output ONLY a single JSON object with this schema:\n"
    '  {\n'
    '    "verdict": "SIGNATURE" | "REFUSE",\n'
    '    "lean_signature": "theorem <name> ... := by sorry",\n'
    '    "reasoning": "one or two sentences of justification",\n'
    '    "confidence": 0.00\n'
    '  }\n'
    "No prose, no markdown fences — just the JSON."
)


_USER_TEMPLATE = (
    "Theorem name (use exactly this identifier in the Lean declaration): "
    "`{theorem_name}`\n\n"
    "LaTeX statement (from the paper):\n"
    "```latex\n{source_latex}\n```\n\n"
    "Paper-theory hint (Lean signatures already in scope; you may use these "
    "names freely):\n"
    "```lean\n{paper_theory_hint}\n```\n\n"
    "Produce the JSON object now."
)


_RETRY_USER_TEMPLATE = (
    "Theorem name (use exactly this identifier in the Lean declaration): "
    "`{theorem_name}`\n\n"
    "LaTeX statement (from the paper, UNCHANGED — re-formalize the SAME claim):\n"
    "```latex\n{source_latex}\n```\n\n"
    "Paper-theory hint (Lean signatures already in scope):\n"
    "```lean\n{paper_theory_hint}\n```\n\n"
    "Your previous candidate (round {prev_round}/{max_rounds}) was REJECTED by "
    "Lean elaboration:\n"
    "```lean\n{prev_candidate}\n```\n\n"
    "Lean elaboration error:\n"
    "```\n{lean_error_tail}\n```\n\n"
    "{anchor_section}"
    "Fix the SPECIFIC issue Lean reported above. Keep the same mathematical "
    "claim; only adjust types, scopes, instance names, missing imports/opens "
    "(use only paper-local names + Mathlib), parser syntax, or quantifier "
    "binders. Do NOT switch to a trivial body. Return the same JSON schema."
)


# --- Smart-retry: Lean error anchor extraction ---------------------------

# Identifier shape that matches Lean naming (letters, digits, _, ', dots).
_LEAN_IDENT = r"[A-Za-z_][A-Za-z0-9_'.]*"

# Regexes for the four error kinds the smart-retry prompt understands.
_UNKNOWN_IDENT_RX = re.compile(
    r"unknown\s+(?:identifier|constant)\s+[`']([^`']+)[`']",
    re.IGNORECASE,
)
# Match the Lean 4 typeclass-synthesis message. We capture the body after
# "failed to synthesize" or after the `synthInstanceFailed:` prefix and parse
# it into (Class, Type-args) shape downstream.
_SYNTH_FAILED_RX = re.compile(
    r"(?:synthInstanceFailed:|failed to synthesize(?:\s+instance)?)\s*"
    r"(?:of\s+type\s+class\s*)?"
    r"[\n\s]*"
    r"(" + _LEAN_IDENT + r"(?:\s+" + _LEAN_IDENT + r")*)",
    re.IGNORECASE,
)
# `type mismatch` / `application type mismatch` — we grab the expected and
# actual types when Lean prints them in the standard `has type ... but is
# expected to have type ...` shape.
_TYPE_MISMATCH_EXPECTED_RX = re.compile(
    r"(?:but is )?expected to have type[:\s]*\n?\s*([^\n]+)",
    re.IGNORECASE,
)
_TYPE_MISMATCH_GOT_RX = re.compile(
    r"\bhas type[:\s]*\n?\s*([^\n]+)",
    re.IGNORECASE,
)
_FUNCTION_EXPECTED_RX = re.compile(
    r"function expected at\s*\n?\s*[`']?(" + _LEAN_IDENT + r")[`']?",
    re.IGNORECASE,
)
# `invalidField`: e.g. "Invalid field `segments`: The environment does not
# contain `Nat.segments`". The smoke test on Prop_Actions hit this exact
# failure mode and looped 3 rounds with no help. We capture the field name
# from the leading clause and (separately) the missing constant from the
# follow-up clause — splitting into two regexes is more robust than trying
# to express the whole pattern with optional groups under DOTALL.
_INVALID_FIELD_HEAD_RX = re.compile(
    r"[Ii]nvalid (?:field|projection)\s+[`']([^`']+)[`']"
)
_INVALID_FIELD_MISSING_RX = re.compile(
    r"does not contain\s+[`']([^`']+)[`']"
)


def _hint_entries_for(symbol: str, paper_theory_hint: str) -> list[str]:
    """Return paper-theory-hint lines that mention `symbol` as a declared
    identifier. We match on `def`, `abbrev`, `axiom`, `instance`, `class`,
    `structure` heads followed (within the line) by the symbol; we also accept
    matches where the symbol appears as a type-argument in an `instance`
    line so e.g. `instance : LE Multisegment` is surfaced when the LLM asks
    about `Multisegment`.
    """
    if not symbol or not paper_theory_hint:
        return []
    out: list[str] = []
    pattern = re.compile(rf"\b{re.escape(symbol)}\b")
    for line in paper_theory_hint.splitlines():
        if not line.strip():
            continue
        head = line.strip().split()[0] if line.strip().split() else ""
        if head.lstrip("@") not in _HINT_KEYWORDS:
            continue
        if pattern.search(line):
            out.append(line.strip())
    return out


def _extract_lean_error_anchors(error_tail: str, paper_theory_hint: str) -> dict[str, Any]:
    """Pull (identifier, error_kind) tuples from a Lean elaboration error tail
    and match them against the paper-theory hint.

    Returns a dict with the shape:
      {
        "kinds": list[str],                # error kinds detected, in order
        "anchors": [
          {
            "kind": "unknown_identifier" | "synth_instance_failed" |
                    "type_mismatch" | "function_expected",
            "symbol": str,                 # primary identifier (may be empty)
            "extra": dict,                 # kind-specific extras
            "matches": list[str],          # matching paper-theory hint lines
            "no_match_reason": str | "",   # set when matches is empty
          },
          ...
        ],
      }

    The function is deterministic and never raises; on empty / unparseable
    input it returns an empty `anchors` list and `kinds=[]`.
    """
    out: dict[str, Any] = {"kinds": [], "anchors": []}
    if not (error_tail or "").strip():
        return out

    seen: set[tuple[str, str]] = set()

    def _record(kind: str, symbol: str, extra: dict[str, Any] | None = None) -> None:
        key = (kind, symbol or "")
        if key in seen:
            return
        seen.add(key)
        matches = _hint_entries_for(symbol, paper_theory_hint) if symbol else []
        no_match_reason = ""
        if symbol and not matches:
            no_match_reason = (
                f"no paper-theory entry declares `{symbol}` — this is likely a "
                "translator-side gap (missing definition / axiom in the paper "
                "module) rather than a fixable LLM-output issue"
            )
        out["anchors"].append(
            {
                "kind": kind,
                "symbol": symbol,
                "extra": dict(extra or {}),
                "matches": matches,
                "no_match_reason": no_match_reason,
            }
        )
        if kind not in out["kinds"]:
            out["kinds"].append(kind)

    # 1. Unknown identifier / constant.
    for m in _UNKNOWN_IDENT_RX.finditer(error_tail):
        sym = (m.group(1) or "").strip()
        if sym:
            _record("unknown_identifier", sym)

    # 2. Typeclass synthesis failed. Body is e.g. `HasSubset Multisegment` —
    #    first token is the class, remainder is the type-arg list.
    for m in _SYNTH_FAILED_RX.finditer(error_tail):
        body = re.sub(r"\s+", " ", (m.group(1) or "").strip())
        if not body:
            continue
        parts = body.split()
        cls = parts[0]
        type_args = parts[1:]
        _record(
            "synth_instance_failed",
            cls,
            {"type_args": type_args},
        )
        # Also surface the type argument so the LLM sees instance entries for it.
        for arg in type_args:
            _record("synth_instance_failed_type_arg", arg, {"class_name": cls})

    # 3. Function expected at <name>.
    for m in _FUNCTION_EXPECTED_RX.finditer(error_tail):
        sym = (m.group(1) or "").strip()
        if sym:
            _record("function_expected", sym)

    # 4. Invalid field / projection — Lean uses this when a struct projection
    #    references a non-existent field on a type. The smoke test on
    #    `Prop_Actions` hit `Invalid field `segments`: The environment does
    #    not contain `Nat.segments`` and looped all 3 rounds without help.
    head_matches = list(_INVALID_FIELD_HEAD_RX.finditer(error_tail))
    missing_matches = list(_INVALID_FIELD_MISSING_RX.finditer(error_tail))
    for idx, m in enumerate(head_matches):
        field = (m.group(1) or "").strip()
        full = ""
        # Pair the i-th head with the i-th missing-constant clause when both
        # are present in the same error block.
        if idx < len(missing_matches):
            full = (missing_matches[idx].group(1) or "").strip()
        host_type = ""
        if "." in full:
            host_type = full.rsplit(".", 1)[0]
        if field:
            _record(
                "invalid_field",
                field,
                {"host_type": host_type, "missing_constant": full},
            )

    # 5. Type mismatch — capture expected/got verbatim. Symbol slot is empty
    #    because the offending term isn't a single identifier.
    if re.search(r"type mismatch|application type mismatch", error_tail, re.IGNORECASE):
        expected = ""
        got = ""
        m_exp = _TYPE_MISMATCH_EXPECTED_RX.search(error_tail)
        if m_exp:
            expected = m_exp.group(1).strip().rstrip("`'")
        m_got = _TYPE_MISMATCH_GOT_RX.search(error_tail)
        if m_got:
            got = m_got.group(1).strip().rstrip("`'")
        _record(
            "type_mismatch",
            "",
            {"expected": expected, "got": got},
        )

    return out


def _format_anchor_section(anchors: dict[str, Any]) -> str:
    """Render the smart-retry anchor block. Returns "" when there are no
    anchors so the prompt template can drop the section cleanly."""
    entries = anchors.get("anchors") or []
    if not entries:
        return ""
    lines: list[str] = ["SMART-RETRY ANCHORS (extracted from the Lean error above):"]
    for entry in entries:
        kind = entry.get("kind", "")
        symbol = entry.get("symbol", "")
        extra = entry.get("extra") or {}
        matches = entry.get("matches") or []
        no_match = entry.get("no_match_reason", "")
        if kind == "unknown_identifier":
            lines.append(
                f"- Lean rejected with: `unknown identifier '{symbol}'`."
            )
            if matches:
                lines.append("  Matching paper-theory entries:")
                for m in matches:
                    lines.append(f"    - `{m}`")
                lines.append(
                    "  Use ONE of these directly. Do not invent variants."
                )
            else:
                lines.append(f"  ({no_match})")
        elif kind == "synth_instance_failed":
            type_args = extra.get("type_args") or []
            if type_args:
                ta = " ".join(type_args)
                lines.append(
                    f"- Lean rejected with: `synthInstanceFailed: {symbol} {ta}`."
                )
            else:
                lines.append(
                    f"- Lean rejected with: `synthInstanceFailed: {symbol}`."
                )
            if matches:
                lines.append(
                    f"  Paper-theory entries mentioning `{symbol}`:"
                )
                for m in matches:
                    lines.append(f"    - `{m}`")
                lines.append(
                    "  Use the typeclass binder shape the paper-theory module "
                    "already provides."
                )
            else:
                lines.append(
                    f"  (no paper-theory instance for `{symbol}` is in scope — "
                    "switch to a type for which the class is derivable, or use "
                    "an explicit binder providing the instance)"
                )
        elif kind == "synth_instance_failed_type_arg":
            if matches:
                lines.append(
                    f"  Paper-theory entries for the type argument `{symbol}`:"
                )
                for m in matches:
                    lines.append(f"    - `{m}`")
        elif kind == "function_expected":
            lines.append(
                f"- Lean rejected with: `function expected at {symbol}`. "
                f"Suggestion: declare `{symbol}` with an explicit function-type "
                f"binder, e.g. `({symbol} : _ → _)`, or pick a paper-theory "
                f"entry that has function shape."
            )
            if matches:
                lines.append(f"  Paper-theory entries for `{symbol}`:")
                for m in matches:
                    lines.append(f"    - `{m}`")
        elif kind == "invalid_field":
            host = extra.get("host_type", "")
            missing = extra.get("missing_constant", "")
            if host:
                lines.append(
                    f"- Lean rejected with: `Invalid field '{symbol}'`. "
                    f"The host type `{host}` does not have field `{symbol}` "
                    f"(missing constant: `{missing}`). "
                    "Suggestion: do NOT project `.segments` (or similar) on a "
                    "raw paper-theory `abbrev`. Either bind the relevant value "
                    "with an explicit typed binder whose type carries the "
                    "field, or rewrite the expression to use the paper-theory "
                    "function form (e.g. `c_alpha α`) instead of dot-notation."
                )
            else:
                lines.append(
                    f"- Lean rejected with: `Invalid field '{symbol}'`. "
                    "Suggestion: replace dot-projection with a paper-theory "
                    "function applied to the bound variable."
                )
            if matches:
                lines.append(f"  Paper-theory entries mentioning `{symbol}`:")
                for m in matches:
                    lines.append(f"    - `{m}`")
        elif kind == "type_mismatch":
            expected = extra.get("expected", "")
            got = extra.get("got", "")
            lines.append(
                "- Lean rejected with `type mismatch`. "
                f"expected: `{expected or '(not parsed)'}`; "
                f"got: `{got or '(not parsed)'}`. "
                "Suggestion: introduce an explicit typed binder so the term "
                "has the expected type, or adjust the conclusion."
            )
    lines.append("")  # trailing blank line for prompt readability
    return "\n".join(lines) + "\n"


def _render_smart_retry_prompt(
    *,
    theorem_name: str,
    source_latex: str,
    paper_theory_hint: str,
    prev_round: int,
    max_rounds: int,
    prev_candidate: str,
    lean_error_tail: str,
) -> str:
    """Render the retry user prompt with extracted Lean-error anchors. Always
    returns a non-empty string; the anchor block is omitted (cleanly) when no
    anchors can be extracted from the error tail."""
    anchors = _extract_lean_error_anchors(lean_error_tail, paper_theory_hint)
    anchor_section = _format_anchor_section(anchors)
    return _RETRY_USER_TEMPLATE.format(
        theorem_name=theorem_name,
        source_latex=source_latex,
        paper_theory_hint=paper_theory_hint or "-- (no paper-local symbols exported)",
        prev_round=prev_round,
        max_rounds=max_rounds,
        prev_candidate=prev_candidate,
        lean_error_tail=lean_error_tail or "(empty error output)",
        anchor_section=anchor_section,
    )


# --- Placeholder detection -------------------------------------------------

# Patterns the rule-based path emitted that we MUST reject from the LLM too.
_PLACEHOLDER_PATTERNS = (
    re.compile(r":\s*True\s*(:=|$)"),
    re.compile(r"∃\s+\w+\s*:\s*ℝ\s*,\s*(\w+)\s*=\s*\1"),
    re.compile(r":\s*\(?\s*0\s*=\s*0\s*\)?\s*(:=|$)"),
    re.compile(r":\s*\(?\s*0\s*:\s*ℕ\s*\)?\s*=\s*0\s*(:=|$)"),
    re.compile(r"\bPaperClaim\b"),
    re.compile(r"\bSourceStatement\b"),
    re.compile(r"\bStatement_[A-Za-z0-9_]+\s*:\s*Prop\b"),
    re.compile(r":\s*False\s*(:=|$)"),
    re.compile(r":\s*Nonempty\s+Unit\b"),
)


def _is_placeholder_decl(decl: str) -> bool:
    text = (decl or "").strip()
    if not text:
        return True
    for pattern in _PLACEHOLDER_PATTERNS:
        if pattern.search(text):
            return True
    return False


def _extract_signature_only(decl: str) -> str:
    """Return signature stripped of any body — the part before ` := `.

    `_is_trivialized_signature` analyzes the full sig+body shape, so we keep the
    body for that check; this helper is used only when we want the bare sig.
    """
    text = (decl or "").strip()
    text = re.sub(r"```(?:lean)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    return text.strip()


def _normalize_decl(raw: str, theorem_name: str) -> str:
    """Strip fences, normalize whitespace, ensure ` := by sorry` body."""
    decl = (raw or "").strip()
    if not decl:
        return ""
    # Strip code fences.
    if decl.startswith("```"):
        decl = re.sub(r"^```(?:lean)?\s*", "", decl)
        decl = re.sub(r"\s*```\s*$", "", decl)
    decl = decl.strip()
    # If the model returned an extra leading word like "Lean:" / "Output:", drop it.
    decl = re.sub(r"^(?:Lean:|Output:|Answer:)\s*", "", decl, flags=re.IGNORECASE)
    # Ensure the theorem keyword leads. We accept `lemma` too but normalize to
    # `theorem` so the validator's keyword gate doesn't reject it.
    decl = re.sub(r"^lemma\s+", "theorem ", decl)
    # Force the body to be exactly ` := by sorry`.
    decl = re.sub(r":=\s*by\s+.+$", ":= by sorry", decl, flags=re.DOTALL).strip()
    if not decl.endswith(":= by sorry"):
        # Remove any other body and append.
        decl = re.sub(r":=.*$", "", decl, flags=re.DOTALL).strip()
        decl = decl + " := by sorry"
    # Ensure single trailing newline-free form.
    decl = re.sub(r"\s+", " ", decl).strip()
    return decl


# --- JSON extraction (mirrors run_auto_alignment_review pattern) ----------


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
            parsed = json.loads(raw[start : end + 1])
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


# --- Mistral call ----------------------------------------------------------


def _call(
    *,
    client: Any,
    model: str,
    user: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    api_log_hook: Optional[Any] = None,
) -> str:
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    # Prefer ponder_loop._chat_complete for telemetry; fall back to direct call.
    try:
        from ponder_loop import _chat_complete  # type: ignore[import-not-found]

        _, text = _chat_complete(
            client=client,
            model=model,
            messages=messages,
            temperature=0.0,
            max_tokens=max_tokens,
            purpose="llm_statement_repair",
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


# --- Public API ------------------------------------------------------------


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def _validate_name_present(decl: str, theorem_name: str) -> bool:
    """The model is asked to use `theorem_name`. If it picked a different name
    we still accept the signature provided the shape is otherwise valid — the
    downstream apply step rewrites the name. But we do require at least one
    identifier after `theorem`."""
    base = (theorem_name or "").strip().rsplit(".", 1)[-1]
    m = re.search(r"^\s*theorem\s+([A-Za-z_][A-Za-z0-9_'.]*)", decl, flags=re.MULTILINE)
    if not m:
        return False
    if base and m.group(1) != base:
        # Soft-rewrite the name to match the requested one.
        return True
    return True


def _rewrite_theorem_name(decl: str, theorem_name: str) -> str:
    base = (theorem_name or "").strip().rsplit(".", 1)[-1]
    base = re.sub(r"[^A-Za-z0-9_']", "_", base)
    if not base:
        return decl
    return re.sub(
        r"^(\s*)theorem\s+[A-Za-z_][A-Za-z0-9_'.]*",
        rf"\1theorem {base}",
        decl,
        count=1,
        flags=re.MULTILINE,
    )


def _single_attempt(
    *,
    client: Any,
    model: str,
    user: str,
    theorem_name: str,
    max_tokens: int,
    api_log_hook: Optional[Any],
) -> dict[str, Any]:
    """Single LLM call + parse + normalize + trivialization-gate pass.

    Always returns a dict (never None). On success: `repaired_decl` is set and
    `rejected` is empty. On any failure: `repaired_decl == ""` and `rejected`
    enumerates the reasons.
    """
    try:
        raw = _call(
            client=client,
            model=model,
            user=user,
            max_tokens=max_tokens,
            api_log_hook=api_log_hook,
        )
    except Exception as exc:
        return {
            "repaired_decl": "",
            "reasoning": f"llm_transport_error:{type(exc).__name__}:{exc}"[:240],
            "confidence": 0.0,
            "protocol": "llm_statement_repair_v1",
            "rejected": ["transport_error"],
            "raw": "",
            "error": True,
        }

    parsed = _extract_json_object(raw)
    if not parsed:
        return {
            "repaired_decl": "",
            "reasoning": "malformed_json_from_llm",
            "confidence": 0.0,
            "protocol": "llm_statement_repair_v1",
            "rejected": ["malformed_json"],
            "raw": raw[:500],
            "error": True,
        }

    verdict = str(parsed.get("verdict", "") or "").strip().upper()
    sig = str(parsed.get("lean_signature", "") or "").strip()
    reasoning = str(parsed.get("reasoning", "") or "").strip()
    confidence = _clamp01(parsed.get("confidence", 0.0))

    if verdict == "REFUSE" or not sig:
        return {
            "repaired_decl": "",
            "reasoning": reasoning or "llm_refused_or_empty_signature",
            "confidence": confidence,
            "protocol": "llm_statement_repair_v1",
            "rejected": ["llm_refused"],
            "raw": raw[:500],
        }

    decl = _normalize_decl(sig, theorem_name)
    if not decl or not _validate_name_present(decl, theorem_name):
        return {
            "repaired_decl": "",
            "reasoning": reasoning or "missing_theorem_keyword",
            "confidence": confidence,
            "protocol": "llm_statement_repair_v1",
            "rejected": ["missing_theorem_keyword"],
            "raw": raw[:500],
        }
    decl = _rewrite_theorem_name(decl, theorem_name)
    # Apply the translator's deterministic post-cleanup so LLM-repair output
    # passes through the same `λ → lam`, `_balance_brackets`,
    # `_normalize_matrix_positive_definite_fields`, etc., rewrites the main
    # translator path applies. Without this, LLM-repair smoke runs hit
    # Unicode-token issues (Round II-4 smoke caught `λ` in
    # `cor_husimi_fourth_moment`) the translator already knows how to
    # repair. Strictly additive normalization.
    decl = _deterministic_signature_cleanup(decl)

    rejected: list[str] = []
    if _is_placeholder_decl(decl):
        rejected.append("placeholder_pattern_detected")
    if _is_trivialized_signature(decl):
        rejected.append("trivialized_signature")

    if rejected:
        return {
            "repaired_decl": "",
            "reasoning": reasoning,
            "confidence": confidence,
            "protocol": "llm_statement_repair_v1",
            "rejected": rejected,
            "raw": raw[:500],
            "candidate_decl_before_rejection": decl,
        }

    return {
        "repaired_decl": decl,
        "reasoning": reasoning,
        "confidence": confidence,
        "protocol": "llm_statement_repair_v1",
        "rejected": [],
        "raw": raw[:500],
    }


def generate_llm_repair_candidate(
    *,
    source_latex: str,
    paper_id: str,
    theorem_name: str,
    paper_theory_hint: str,
    client: Any,
    model: str = DEFAULT_MODEL,
    api_log_hook: Optional[Any] = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_repair_rounds: int = DEFAULT_MAX_REPAIR_ROUNDS,
    validate_elaboration: Optional[Callable[[str], tuple[bool, str]]] = None,
) -> dict[str, Any] | None:
    """Generate a Lean theorem signature via Leanstral with optional error-feedback retry.

    Returns a dict with keys:
      - repaired_decl: str (the Lean signature, body normalized to `:= by sorry`)
      - reasoning:     str (LLM justification)
      - confidence:    float in [0, 1]
      - protocol:      "llm_statement_repair_v1"
      - rejected:      list[str] (empty on success)
      - retry_rounds:  int (number of LLM calls made; 1 on first-shot success)
      - retry_history: list[dict] (per-round candidate + elaboration result; only
                       present when `validate_elaboration` is supplied)

    Returns None when generation fails (empty input, missing client).

    If `validate_elaboration` is supplied, it is called with the normalized
    candidate decl after each round and must return `(ok: bool, error_tail: str)`.
    When it returns `ok=False`, the loop builds a follow-up prompt that feeds
    the Lean error tail (≤500 chars) back to the LLM so it can fix the specific
    structural issue, and re-validates. Iterates up to `max_repair_rounds`.

    When `validate_elaboration` is None, this is identical to a single-attempt
    call (no retry) — the elaboration gate is the only signal that justifies a
    retry; trivialization/placeholder failures are deterministic.
    """
    if not (source_latex or "").strip():
        return None
    if client is None:
        return None
    if max_repair_rounds < 1:
        max_repair_rounds = 1

    latex_trim = re.sub(r"\s+", " ", source_latex).strip()[:MAX_LATEX_CHARS]
    hint_trim = (paper_theory_hint or "").strip()[:MAX_HINT_CHARS]
    theorem_name_short = (theorem_name or "anon").strip().rsplit(".", 1)[-1] or "anon"
    initial_user = _USER_TEMPLATE.format(
        theorem_name=theorem_name_short,
        source_latex=latex_trim,
        paper_theory_hint=hint_trim or "-- (no paper-local symbols exported)",
    )

    retry_history: list[dict[str, Any]] = []
    last_result: dict[str, Any] = {}
    user_prompt = initial_user

    for round_idx in range(1, max_repair_rounds + 1):
        attempt = _single_attempt(
            client=client,
            model=model,
            user=user_prompt,
            theorem_name=theorem_name,
            max_tokens=max_tokens,
            api_log_hook=api_log_hook,
        )
        attempt["retry_rounds"] = round_idx
        last_result = attempt

        decl = str(attempt.get("repaired_decl") or "")
        if not decl:
            # Trivialization/placeholder/refusal/malformed — retry won't help
            # because the elaboration gate hasn't even seen the candidate. Stop.
            attempt["retry_history"] = list(retry_history)
            return attempt

        if validate_elaboration is None:
            # No elaboration callback wired — single-attempt semantics.
            attempt["retry_history"] = list(retry_history)
            return attempt

        try:
            ok, error_tail = validate_elaboration(decl)
        except Exception as exc:  # pragma: no cover - defensive
            attempt["elaboration_validator_exception"] = (
                f"{type(exc).__name__}:{exc}"[:240]
            )
            attempt["retry_history"] = list(retry_history)
            return attempt

        round_record = {
            "round": round_idx,
            "candidate_decl": decl,
            "elaboration_ok": bool(ok),
            "lean_error_tail": (error_tail or "")[-MAX_LEAN_ERROR_TAIL_CHARS:],
        }
        retry_history.append(round_record)

        if ok:
            attempt["retry_history"] = list(retry_history)
            return attempt

        if round_idx >= max_repair_rounds:
            # Exhausted budget; honestly reject as elaboration-failure.
            rejected = list(attempt.get("rejected") or [])
            if "elaboration_gate_after_retry" not in rejected:
                rejected.append("elaboration_gate_after_retry")
            return {
                **attempt,
                "repaired_decl": "",
                "rejected": rejected,
                "reasoning": attempt.get("reasoning") or "elaboration_gate_after_retry",
                "candidate_decl_before_rejection": decl,
                "lean_error_tail": round_record["lean_error_tail"],
                "retry_history": list(retry_history),
            }

        # Build follow-up prompt for the next round. The smart-retry renderer
        # extracts (identifier, error_kind) anchors from the Lean error tail
        # and surfaces matching paper-theory entries verbatim — without this,
        # smoke testing on `Cor_Quant` showed 0/3 rounds rescuing the same
        # `unknown identifier` (the LLM repeated the structural mistake).
        tail = (error_tail or "")[-MAX_LEAN_ERROR_TAIL_CHARS:]
        user_prompt = _render_smart_retry_prompt(
            theorem_name=theorem_name_short,
            source_latex=latex_trim,
            paper_theory_hint=hint_trim,
            prev_round=round_idx,
            max_rounds=max_repair_rounds,
            prev_candidate=decl,
            lean_error_tail=tail,
        )

    # Unreachable in normal flow (loop returns on every path) but keep a safe
    # fall-through for defensive completeness.
    return last_result or None


# --- Paper-theory hint extraction -----------------------------------------


_HINT_KEYWORDS = ("abbrev", "def", "axiom", "instance", "class", "structure")


def extract_paper_theory_hint(paper_theory_path: Path, *, max_lines: int = 60) -> str:
    """Read a Paper_<id>.lean file and return up to `max_lines` of header
    signatures (def/abbrev/axiom/instance lines) for use as the LLM hint.

    Trailing `:= ...` bodies are stripped so the hint stays compact."""
    try:
        text = paper_theory_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        head = stripped.split()[0] if stripped.split() else ""
        if head.lstrip("@") not in _HINT_KEYWORDS:
            continue
        # Truncate body for compactness.
        compact = re.sub(r":=.*$", "", stripped).strip()
        if not compact:
            continue
        out.append(compact)
        if len(out) >= max_lines:
            break
    return "\n".join(out)
