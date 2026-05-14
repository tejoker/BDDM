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
    "Fix the SPECIFIC issue Lean reported above. Keep the same mathematical "
    "claim; only adjust types, scopes, instance names, missing imports/opens "
    "(use only paper-local names + Mathlib), parser syntax, or quantifier "
    "binders. Do NOT switch to a trivial body. Return the same JSON schema."
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

        # Build follow-up prompt for the next round.
        tail = (error_tail or "")[-MAX_LEAN_ERROR_TAIL_CHARS:]
        user_prompt = _RETRY_USER_TEMPLATE.format(
            theorem_name=theorem_name_short,
            source_latex=latex_trim,
            paper_theory_hint=hint_trim or "-- (no paper-local symbols exported)",
            prev_round=round_idx,
            max_rounds=max_repair_rounds,
            prev_candidate=decl,
            lean_error_tail=tail or "(empty error output)",
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
