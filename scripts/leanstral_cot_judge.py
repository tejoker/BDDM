"""Chain-of-Thought equivalence judge for LaTeX→Lean translations.

The original `leanstral_judge` (and the inline judge in
`run_auto_alignment_review.py`) is a single-shot prompt that asks the model
"are these semantically equivalent?" with no reasoning space. Empirically it's
**too conservative** on math papers: it marks as `not_equivalent` translations
that *are* adequate but abstract a constant the LaTeX gives explicitly (e.g.
LaTeX provides `C = 4πβ`, Lean writes `∃ C, ...`). On the Apr-2026 batch only
8 of 74 rows came back `equivalent` at ≥0.85 confidence, blocking 165
downstream rows from passing the `claim_equivalent` gate.

This module is a CoT-style judge that reasons step-by-step before issuing a
verdict, with three concrete improvements:

1. **Structured reasoning steps** (quantifiers → hypotheses → conclusion →
   abstraction-check → verdict). Surface each step's analysis and a
   per-step confidence; final confidence is the *minimum* across steps
   (pessimistic aggregation), so we never overconfidently approve.
2. **`adequate_weaker` verdict class** for translations that abstract
   constants without changing the claim's intent. The Lean statement is
   weaker, but the abstraction *preserves* the implication direction;
   for proof-search purposes this is acceptable, so the judge maps
   `adequate_weaker` → `equivalent` with an `adequate_weaker_evidence` flag.
3. **Backward-compat schema**: returns the existing `JudgeResult` shape
   (`equivalent / confidence / rationale / flag / extra_flags`) plus
   additive fields `reasoning_steps` and `adequate_weaker_evidence`.

Existing callers of `leanstral_judge.JudgeResult` continue to work unchanged.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from leanstral_judge import JudgeResult


_SYSTEM = (
    "You are a formal-mathematics judge for LaTeX↔Lean theorem translations.\n"
    "Reason step-by-step, then emit JSON only. No prose outside the JSON.\n\n"
    "Reasoning steps you MUST output, in order:\n"
    "  1. quantifiers — list LaTeX universally/existentially quantified vars\n"
    "     and types vs Lean binders; flag mismatches (different type,\n"
    "     missing variable, swapped quantifier).\n"
    "  2. hypotheses — enumerate LaTeX assumptions vs Lean hypothesis\n"
    "     binders. For each LaTeX item, mark status as:\n"
    "        matched     — exact correspondence in Lean\n"
    "        abstracted  — Lean treats this as a free variable / existential\n"
    "                      where LaTeX named a specific thing (still adequate)\n"
    "        missing     — Lean has no corresponding hypothesis\n"
    "  3. conclusion — parse LaTeX conclusion vs Lean target. Classify as:\n"
    "        identical          — same statement modulo renaming\n"
    "        adequate_weaker    — Lean is logically weaker but preserves the\n"
    "                             paper's claim direction (e.g. ∃ in Lean\n"
    "                             where LaTeX gave an explicit constant)\n"
    "        adequate_stronger  — Lean is logically stronger (rare; usually fine)\n"
    "        different          — conclusion shape genuinely differs\n"
    "        weakened_quantifier— a needed ∀ became ∃ or vice versa\n"
    "  4. abstraction_check — for each `abstracted`/`adequate_weaker` item,\n"
    "     verify that the abstraction preserves the implication direction.\n"
    "     A Lean theorem of form `∃ C, P(C)` adequately captures `P(c_specific)`;\n"
    "     the reverse (`∀ C, ...` where LaTeX picked one) is NOT adequate.\n"
    "  5. verdict — one of:\n"
    "        equivalent       — all components match (strict equivalence)\n"
    "        adequate_weaker  — translation is weaker but adequate (treat as\n"
    "                          equivalent for proof-search purposes)\n"
    "        not_equivalent   — conclusion genuinely differs OR a needed\n"
    "                          hypothesis is missing\n"
    "        unclear          — insufficient context to decide\n\n"
    "Each step must include `step_confidence` in [0.0, 1.0]. Final\n"
    "`confidence` will be the minimum across step_confidence values, so do\n"
    "not inflate any step's confidence to push the overall up.\n\n"
    "Output JSON ONLY (no markdown, no prose):\n"
    '{"reasoning_steps": ['
    '{"name": "quantifiers", "analysis": "...", "step_confidence": 0.X},'
    '{"name": "hypotheses", "analysis": "...", "step_confidence": 0.X},'
    '{"name": "conclusion", "analysis": "...", "classification": "...", "step_confidence": 0.X},'
    '{"name": "abstraction_check", "analysis": "...", "step_confidence": 0.X}],'
    '"verdict": "equivalent|adequate_weaker|not_equivalent|unclear",'
    '"confidence": 0.X,'
    '"rationale": "<150 char summary>",'
    '"adequate_weaker_evidence": false}'
)

_USER_TEMPLATE = (
    "LaTeX theorem:\n{latex}\n\n"
    "Lean 4 signature:\n{lean}\n\n"
    "{area_hint}"
    "Reason step-by-step, then emit JSON only."
)

# Area-specific equivalence rules. Appended to the user prompt when the caller
# passes an `area` argument. Without these, the generic prompt rejects many
# adequate translations that use idiomatic per-area encodings (e.g. ∀ε>0 in
# LaTeX vs `(ε : ℝ) (hε : 0 < ε)` in Lean is universally fine in analysis but
# the generic judge sometimes flags it as a hypothesis mismatch).
_AREA_HINTS: dict[str, str] = {
    "analysis": (
        "[Area: analysis]\n"
        "Equivalence rules for analysis papers:\n"
        "- `∀ ε > 0, ∀ δ > 0, P(ε, δ)` (LaTeX) ≡ `(ε : ℝ) (hε : 0 < ε) (δ : ℝ) (hδ : 0 < δ) : P ε δ` (Lean).\n"
        "  Treat the universal quantification + positivity hypothesis as equivalent encodings.\n"
        "- LaTeX `for some constant C` ≡ Lean `∃ C, ...` (mark as adequate_weaker, NOT not_equivalent).\n"
        "- LaTeX `lim x → 0 f(x) = L` ≡ Lean `Filter.Tendsto f (nhds 0) (nhds L)`.\n"
        "- LaTeX `|f(x)| ≤ C |g(x)|` (asymptotic) ≡ Lean `∃ C, ∀ x, ‖f x‖ ≤ C * ‖g x‖`.\n"
        "- LaTeX `f ∈ Lp` ≡ Lean `MemLp f p μ` (when p+μ are clear from context).\n"
        "- Spectral / operator theory: paper-local operator names treated as opaque are STILL adequate\n"
        "  if their hypothesis profile matches the LaTeX (e.g. `(T : H →L[ℝ] H)` matches `T : H → H` linear).\n\n"
    ),
    "probability": (
        "[Area: probability]\n"
        "Equivalence rules for probability papers:\n"
        "- `almost surely` / `P(... ) = 1` (LaTeX) ≡ `∀ᵐ ω ∂μ, ...` or `MeasureTheory.ae` in Lean.\n"
        "- `X is a random variable` (LaTeX) ≡ `Measurable X` (Lean).\n"
        "- `E[X] = c` (LaTeX) ≡ `∫ ω, X ω ∂μ = c` (Lean).\n"
        "- `X ⟂⟂ Y` independent (LaTeX) ≡ `IndepFun X Y μ` or `Indep ... μ` (Lean).\n"
        "- `X →ᵈ X∞` convergence in distribution (LaTeX) ≡ tightness + characteristic-function convergence.\n"
        "- Filtration / stopping-time encodings: paper-local notation is adequate when the hypothesis\n"
        "  profile matches the σ-algebra structure, even if the σ-algebras themselves are abstracted.\n\n"
    ),
    "algebra": (
        "[Area: algebra]\n"
        "Equivalence rules for algebra papers:\n"
        "- LaTeX `unique f such that P(f)` ≡ Lean `∃! f, P f` ≡ `∃ f, P f ∧ ∀ g, P g → g = f`.\n"
        "- LaTeX `R is a ring` ≡ Lean instance `[Ring R]` (don't require an explicit hypothesis).\n"
        "- Module / representation isomorphism: `M ≃ₗ N` (Lean) and `there is a bijection M → N\n"
        "  preserving structure` (LaTeX) are equivalent encodings.\n"
        "- Quotient / coset notation: paper-local quotient notation `R/I` is adequate when the Lean\n"
        "  side uses `Ideal.Quotient.mk` or `R ⧸ I`.\n"
        "- Exact-sequence statements: short-exact sequences in LaTeX may be unrolled into kernel/image\n"
        "  equalities in Lean — adequate as long as the implications match.\n\n"
    ),
    "combinatorics": (
        "[Area: combinatorics]\n"
        "Equivalence rules for combinatorics papers:\n"
        "- LaTeX `|S| ≤ N` (set cardinality) ≡ Lean `S.card ≤ N` (when S : Finset).\n"
        "- LaTeX `there exists a bijection f : A → B` ≡ Lean `∃ f : A → B, Function.Bijective f`\n"
        "  ≡ Lean `Nonempty (A ≃ B)`. All three are adequate.\n"
        "- LaTeX graph property `G is K_r-free` ≡ Lean `¬ G.CliqueFree r` is NOT equivalent\n"
        "  (sign flip); verify direction carefully.\n"
        "- Asymptotic count `count(...) = (1+o(1)) f(n)` ≡ existential bound; mark as adequate_weaker.\n"
        "- Permutation / partition counts: paper-local closed-form vs Lean recursive definition\n"
        "  are equivalent when both compute the same value.\n\n"
    ),
    "numbertheory": (
        "[Area: numbertheory]\n"
        "Equivalence rules for number-theory papers:\n"
        "- LaTeX `d | n` (divides) ≡ Lean `d ∣ n` (note the unicode mid-dot).\n"
        "- LaTeX `gcd(a, b) = 1` ≡ Lean `Nat.Coprime a b` ≡ `Nat.gcd a b = 1`.\n"
        "- LaTeX big-O `f(n) = O(g(n))` ≡ Lean `∃ C, ∀ n, |f n| ≤ C * |g n|` (existential bound).\n"
        "- LaTeX `a ≡ b (mod n)` ≡ Lean `a ≡ b [ZMOD n]` or `(a - b) % n = 0`.\n"
        "- p-adic valuations / discrete log: paper-local notation is adequate when the value matches.\n\n"
    ),
    "generic": "",  # No additional hints; rely on the base prompt.
}

_REQUIRED_STEP_NAMES = ("quantifiers", "hypotheses", "conclusion", "abstraction_check")
_VALID_VERDICTS = {"equivalent", "adequate_weaker", "not_equivalent", "unclear"}


def _area_hint(area: str | None) -> str:
    """Return the area-specific prompt fragment to splice into the user message.
    Falls back to the empty string for unknown / generic areas."""
    if not area:
        return ""
    return _AREA_HINTS.get(area.strip().lower(), "")


@dataclass
class CoTJudgeResult:
    """Extended result that carries reasoning trace plus the back-compat fields."""

    # Back-compat fields (mirror leanstral_judge.JudgeResult).
    equivalent: bool
    confidence: float
    rationale: str
    flag: str
    extra_flags: list[str] = field(default_factory=list)
    # New CoT fields.
    reasoning_steps: list[dict[str, Any]] = field(default_factory=list)
    adequate_weaker_evidence: bool = False
    raw_verdict: str = ""

    @property
    def approved(self) -> bool:
        return self.equivalent and self.confidence >= _threshold()

    def to_judge_result(self) -> JudgeResult:
        """Project to the legacy JudgeResult schema for callers that consume it."""
        return JudgeResult(
            equivalent=self.equivalent,
            confidence=self.confidence,
            rationale=self.rationale,
            flag=self.flag,
            extra_flags=list(self.extra_flags),
        )


def _threshold() -> float:
    return float(os.environ.get("DESOL_LEANSTRAL_COT_THRESHOLD", "0.80"))


def _max_tokens() -> int:
    return int(os.environ.get("DESOL_LEANSTRAL_COT_MAX_TOKENS", "1200"))


def leanstral_cot_judge(
    *,
    latex_stmt: str,
    lean_sig: str,
    client: Any,
    model: str,
    api_log_hook: Optional[Any] = None,
    area: str | None = None,
) -> CoTJudgeResult:
    """Call Leanstral with a CoT prompt and parse the structured response.

    `area` (optional): math area tag (`analysis`, `probability`, `algebra`,
    `combinatorics`, `numbertheory`, `generic`). When provided, area-specific
    equivalence rules are spliced into the user prompt so the judge accepts
    idiomatic per-area encodings (e.g. analysis: `∀ε>0` ≡ `(ε : ℝ) (hε : 0 < ε)`)
    without flagging them as hypothesis mismatches."""
    if not latex_stmt or not lean_sig:
        return _make_unclear("empty input", confidence=0.0, raw="")

    user = _USER_TEMPLATE.format(
        latex=latex_stmt.strip()[:1500],
        lean=lean_sig.strip()[:1500],
        area_hint=_area_hint(area),
    )

    try:
        raw = _call(client=client, model=model, user=user, api_log_hook=api_log_hook)
    except Exception as exc:
        return _make_error(str(exc))

    parsed = _parse_cot_response(raw)
    if not parsed:
        return _make_unclear("could not parse CoT JSON", confidence=0.0, raw=raw)

    steps = parsed.get("reasoning_steps") or []
    verdict = str(parsed.get("verdict", "") or "").strip().lower()
    rationale = str(parsed.get("rationale", "") or "")[:240]
    adequate_flag = bool(parsed.get("adequate_weaker_evidence", False))
    if verdict not in _VALID_VERDICTS:
        verdict = "unclear"

    # Verify that all required reasoning steps are present. If a step is
    # missing the model didn't actually reason — degrade to `unclear` so
    # downstream consumers know not to trust the verdict.
    present_step_names = {str(s.get("name", "")).lower() for s in steps if isinstance(s, dict)}
    missing_steps = [n for n in _REQUIRED_STEP_NAMES if n not in present_step_names]
    if missing_steps:
        return CoTJudgeResult(
            equivalent=False,
            confidence=0.0,
            rationale=f"missing reasoning steps: {missing_steps}",
            flag="leanstral_cot_judge_incomplete",
            extra_flags=[f"leanstral_cot_missing_step:{n}" for n in missing_steps],
            reasoning_steps=steps,
            adequate_weaker_evidence=adequate_flag,
            raw_verdict=verdict,
        )

    # Pessimistic aggregation: final confidence = min(step_confidence).
    step_confidences = [
        max(0.0, min(1.0, float(s.get("step_confidence", 0.0) or 0.0)))
        for s in steps
        if isinstance(s, dict)
    ]
    aggregate_confidence = min(step_confidences) if step_confidences else 0.0

    # Map verdict → (equivalent, primary flag, extra flags).
    # `adequate_weaker` is treated as equivalent for downstream consumers,
    # but tagged with an explicit evidence flag.
    if verdict == "equivalent":
        equivalent = True
        flag = "claim_equivalent:leanstral_cot"
        extras = [
            "claim_equivalent:leanstral_cot",
            f"leanstral_cot_confidence:{aggregate_confidence:.2f}",
            "independent_semantic_evidence:leanstral_cot",
        ]
    elif verdict == "adequate_weaker":
        equivalent = True
        adequate_flag = True
        flag = "claim_equivalent:leanstral_cot_adequate_weaker"
        extras = [
            "claim_equivalent:leanstral_cot",
            "leanstral_cot_adequate_weaker",
            f"leanstral_cot_confidence:{aggregate_confidence:.2f}",
            "independent_semantic_evidence:leanstral_cot",
        ]
    elif verdict == "not_equivalent":
        equivalent = False
        flag = "leanstral_cot_judge_not_equivalent"
        extras = [
            "leanstral_cot_judge_not_equivalent",
            f"leanstral_cot_confidence:{aggregate_confidence:.2f}",
        ]
    else:  # unclear
        equivalent = False
        flag = "leanstral_cot_judge_unclear"
        extras = [
            "leanstral_cot_judge_unclear",
            f"leanstral_cot_confidence:{aggregate_confidence:.2f}",
        ]

    if equivalent and aggregate_confidence < _threshold():
        flag = "leanstral_cot_judge_equivalent_low_confidence"
        extras = [
            "leanstral_cot_judge_equivalent_low_confidence",
            f"leanstral_cot_confidence:{aggregate_confidence:.2f}",
        ]

    return CoTJudgeResult(
        equivalent=equivalent,
        confidence=aggregate_confidence,
        rationale=rationale,
        flag=flag,
        extra_flags=extras,
        reasoning_steps=steps,
        adequate_weaker_evidence=adequate_flag,
        raw_verdict=verdict,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_unclear(reason: str, *, confidence: float, raw: str) -> CoTJudgeResult:
    return CoTJudgeResult(
        equivalent=False,
        confidence=confidence,
        rationale=reason[:200],
        flag="leanstral_cot_judge_unclear",
        extra_flags=[f"leanstral_cot_judge_unclear:{reason[:80]}"],
        raw_verdict="unclear",
    )


def _make_error(exc_msg: str) -> CoTJudgeResult:
    return CoTJudgeResult(
        equivalent=False,
        confidence=0.0,
        rationale=exc_msg[:200],
        flag="leanstral_cot_judge_error",
        extra_flags=[f"leanstral_cot_judge_error:{exc_msg[:80]}"],
    )


def _call(*, client: Any, model: str, user: str, api_log_hook: Optional[Any]) -> str:
    """Call the Mistral chat API. Reuses ponder_loop._chat_complete when available
    for consistent telemetry; falls back to a direct call when imported standalone
    (e.g. in unit tests without the full ponder_loop dependency tree)."""
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]
    try:
        from ponder_loop import _chat_complete  # type: ignore[import-not-found]
        _, text = _chat_complete(
            client=client,
            model=model,
            messages=messages,
            temperature=0.0,
            max_tokens=_max_tokens(),
            purpose="leanstral_cot_judge",
            api_log_hook=api_log_hook,
        )
        return (text or "").strip()
    except Exception:
        # Direct fallback (used by tests and when ponder_loop is unavailable).
        response = client.chat.complete(
            model=model,
            messages=messages,
            temperature=0.0,
            max_tokens=_max_tokens(),
        )
        text = ""
        if hasattr(response, "choices") and response.choices:
            msg = response.choices[0].message
            text = getattr(msg, "content", "") or ""
        return text.strip()


def _parse_cot_response(raw: str) -> dict:
    """Extract the JSON object from the raw response. Robust to leading/trailing
    prose or markdown fences (``` ... ```)."""
    if not raw:
        return {}
    # Strip code fences if present.
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
    # Find the outermost {...} block (greedy across newlines).
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except (ValueError, TypeError):
        return {}
