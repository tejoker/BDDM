#!/usr/bin/env python3
"""Leanstral whole-proof generator.

The state-MCTS + tactic-catalog prover writes proofs that pass isolated
elaboration but fail in the real-file context (drift between the isolated
prelude and the on-disk file's neighbouring declarations and instances).
The path forward is to give Leanstral the whole real-file context (signature
+ paper-theory exports + ~5 neighbour declarations) and ask it to write a
COMPLETE tactic-mode proof body. Validation then runs `lake env lean` on the
REAL file with the body patched in (Bug-A is already fixed in
`sweep_canonical_patch_and_validate._patch_in_place`).

Public API:

    generate_proof_candidate(
        *,
        paper_id, theorem_name, lean_statement,
        paper_theory_hint, paper_local_file,
        error_tail="",
        client, model="labs-leanstral-2603",
        max_tokens=2400, api_log_hook=None,
    ) -> dict | None

    Returns {'proof_body': str, 'reasoning': str, 'confidence': float}
    or None on transport / malformed-JSON / forbidden-token / refusal.

Standards-positive: ANY of `sorry`, `admit`, `apply?`, `axiom` in the
candidate body is rejected by `_contains_forbidden_token` before the
caller patches the file.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


DEFAULT_MODEL = os.getenv("MISTRAL_MODEL", "labs-leanstral-2603")
DEFAULT_MAX_TOKENS = 2400
MAX_STATEMENT_CHARS = 2400
MAX_HINT_CHARS = 1800
MAX_NEIGHBOUR_CHARS = 2200
MAX_ERROR_TAIL_CHARS = 800
# Audited-core hint (B3): surfaced as a labelled section in the user
# prompt — paper-local curated proofs as in-context examples. Mirrors
# `extract_audited_core_hints.DEFAULT_MAX_CHARS`.
MAX_AUDITED_CORE_CHARS = 4000
# LaTeX proof-structure hint (C2): per-row structural keywords surfaced
# in the user prompt.
MAX_LATEX_PROOF_HINT_CHARS = 800
# Mathlib-anchor section (A1/A3): hard cap so a chatty premise index
# can't crowd out the statement / neighbours.
MAX_ANCHOR_SECTION_CHARS = 2400


# Lazy-loaded Mathlib name index (built by mathlib_align_unknown_identifier).
# Shared across all calls in the process so the 36MB JSON is read once.
_NAME_INDEX_CACHE: dict[str, Any] | None = None


def _get_name_index() -> dict[str, Any] | None:
    """Return the cached Mathlib name index, or None when unavailable.

    First call reads `data/mathlib_name_index.json` (or builds it if mathlib
    sources are on disk). Subsequent calls reuse the same dict. Used by the
    A1 wiring in `generate_proof_candidate` when the caller didn't supply
    an explicit `name_index`.
    """
    global _NAME_INDEX_CACHE
    if _NAME_INDEX_CACHE is not None:
        return _NAME_INDEX_CACHE
    try:
        from mathlib_align_unknown_identifier import build_name_index  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        idx = build_name_index(progress=False)
    except Exception:
        return None
    if not idx or not idx.get("entries"):
        return None
    _NAME_INDEX_CACHE = idx
    return idx

# Whole-token forbidden tokens (must not appear anywhere in the body).
FORBIDDEN_TOKENS: tuple[str, ...] = (
    "sorry",
    "admit",
    "apply?",
    "axiom",
    "native_decide",  # standards-negative shortcut (not a closure of math)
)


# --- Prompt templates -----------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a Lean 4 proof engineer. You receive a theorem statement, a "
    "paper-theory hint listing locally available definitions / abbreviations / "
    "axioms / instances, and up to a handful of neighbouring declarations from "
    "the same source file. Your task: produce a COMPLETE tactic-mode proof body "
    "for the theorem.\n\n"
    "STRICT RULES (these MUST be followed; otherwise your output is discarded):\n"
    "  1. Output ONLY a single JSON object with keys "
    '`proof_body`, `reasoning`, `confidence`.\n'
    "  2. `proof_body` is the WHOLE proof script with NO leading `:= by` "
    "     prefix and NO theorem signature — just the tactic lines.\n"
    "  3. `proof_body` MUST NOT contain any of the literal tokens: "
    "     `sorry`, `admit`, `apply?`, `axiom`, `native_decide`.\n"
    "  4. Use idiomatic multi-line Lean 4. Prefer `intro` / `obtain` / "
    "     `have` / `refine` / `exact` over single-tactic close attempts.\n"
    "  5. Use only Mathlib lemmas and the paper-theory symbols listed in the "
    "     hint. Do NOT invent new axioms.\n"
    "  6. If you cannot prove the goal, output "
    '{"proof_body":"","reasoning":"...","confidence":0.0} '
    "     — an empty body is honest and will be rejected without penalty.\n"
    "  7. Whitespace: indent each subsequent line with two spaces; do not "
    "     emit tab characters.\n\n"
    "Output format (exact):\n"
    '  {"proof_body": "<the proof tactics>", '
    '"reasoning": "<one or two sentences>", '
    '"confidence": 0.00}\n'
)


_USER_TEMPLATE = (
    "Theorem to prove (name `{theorem_name}`, paper `{paper_id}`):\n"
    "```lean\n{lean_statement}\n```\n\n"
    "Paper-theory exports (already in scope):\n"
    "```lean\n{paper_theory_hint}\n```\n\n"
    "Neighbouring declarations from the same file (for context):\n"
    "```lean\n{neighbours}\n```\n\n"
    "{latex_proof_section}"
    "{audited_core_section}"
    "{anchor_section}"
    "{retry_block}"
    "Write the COMPLETE proof body now. Respond with the JSON object only."
)


_AUDITED_CORE_SECTION_TEMPLATE = (
    "Audited proofs from the same paper (idiomatic examples — DO NOT copy "
    "verbatim, but use these to choose tactics and lemma names):\n"
    "```lean\n{audited_core_hint}\n```\n\n"
)


_ANCHOR_SECTION_TEMPLATE = (
    "Mathlib-anchor evidence (real names; use ONE of these if it fits):\n"
    "{anchor_body}\n\n"
)


_LATEX_PROOF_SECTION_TEMPLATE = (
    "{latex_proof_block}\n\n"
)


_RETRY_BLOCK_TEMPLATE = (
    "Your previous attempt failed `lake env lean` with the following error tail:\n"
    "```\n{error_tail}\n```\n"
    "Address the error directly. Common fixes: replace placeholder identifiers, "
    "add missing `intro`/`obtain` for hypotheses, replace `aesop` with a more "
    "targeted tactic, or destructure conjunctions before applying.\n\n"
)


# --- JSON extraction ------------------------------------------------------


def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidate = raw[start : end + 1]
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


# --- Forbidden-token gate -------------------------------------------------


_TOKEN_BOUNDARY = re.compile(r"[A-Za-z0-9_'?]")


def _contains_forbidden_token(body: str) -> Optional[str]:
    """Return the first forbidden token found in `body` (as a whole token,
    not as a substring of an identifier).

    For tokens that contain `?` (e.g. `apply?`), a simple substring search
    suffices because `?` is not a valid identifier character in Lean.
    For `sorry` / `admit` / `axiom` / `native_decide`, we require a
    word-boundary match so that identifiers like `axiomatized` or
    `pre_admit_check` are NOT flagged (the latter is unlikely in proof
    bodies but the boundary check makes the rule precise).
    """
    if not body:
        return None
    for tok in FORBIDDEN_TOKENS:
        if "?" in tok:
            if tok in body:
                return tok
            continue
        # Word-boundary match: token must NOT be preceded or followed by an
        # identifier character.
        pattern = re.compile(r"(?<![A-Za-z0-9_'])" + re.escape(tok) + r"(?![A-Za-z0-9_'])")
        if pattern.search(body):
            return tok
    return None


# --- Neighbour extraction -------------------------------------------------

_DECL_HEAD = re.compile(
    r"^(?:noncomputable\s+|private\s+)?(?:theorem|lemma|def|abbrev|axiom)\s+([A-Za-z_][A-Za-z0-9_'.]*)",
    re.MULTILINE,
)


def _split_declarations(lean_src: str) -> list[tuple[str, int, int]]:
    """Return a list of (name, start_offset, end_offset) for every top-level
    `theorem/lemma/def/abbrev/axiom` declaration in `lean_src`.

    `end_offset` is the offset of the next declaration's header (or end of
    file). Multi-line bodies are included.
    """
    matches = list(_DECL_HEAD.finditer(lean_src))
    decls: list[tuple[str, int, int]] = []
    for i, m in enumerate(matches):
        name = m.group(1)
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(lean_src)
        decls.append((name, start, end))
    return decls


def _top_neighbour_declarations(
    *,
    lean_src: str,
    target_name: str,
    max_total_chars: int = MAX_NEIGHBOUR_CHARS,
    max_count: int = 5,
) -> str:
    """Return up to `max_count` neighbouring declarations from `lean_src` that
    are textually closest to the target (by source-position distance).

    The target itself is excluded. We bias for proximity because nearby
    declarations are typically in the same section and share notation /
    instances. If the file is small enough we just include all decls except
    the target.
    """
    decls = _split_declarations(lean_src)
    if not decls:
        return ""
    target_pos: Optional[int] = None
    target_short = target_name.rsplit(".", 1)[-1]
    for name, start, _end in decls:
        if name == target_name or name.rsplit(".", 1)[-1] == target_short:
            target_pos = start
            break
    if target_pos is None and decls:
        # Fall back to the first decl as anchor; still useful context.
        target_pos = decls[0][1]
    neighbours = [
        (name, start, end)
        for (name, start, end) in decls
        if name != target_name and name.rsplit(".", 1)[-1] != target_short
    ]
    neighbours.sort(key=lambda triple: abs(triple[1] - (target_pos or 0)))
    out_pieces: list[str] = []
    total = 0
    for name, start, end in neighbours[:max_count]:
        block = lean_src[start:end].rstrip()
        # Trim oversized blocks to a head + tail snippet.
        if len(block) > 600:
            head = block[:300]
            tail = block[-200:]
            block = head + "\n  -- ... (truncated) ...\n" + tail
        if total + len(block) + 2 > max_total_chars:
            break
        out_pieces.append(block)
        total += len(block) + 2
    return "\n\n".join(out_pieces)


# --- Body normalization ---------------------------------------------------


def _strip_code_fences(text: str) -> str:
    s = text or ""
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:lean)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def _normalize_proof_body(raw: str) -> str:
    """Strip code fences, drop a leading `:= by` if the model included it,
    convert tabs to two-space indent, trim trailing whitespace.
    """
    body = _strip_code_fences(raw)
    if not body:
        return ""
    # Drop a leading `:= by` (with surrounding whitespace).
    body = re.sub(r"^\s*:=\s*by\b[ \t]*\n?", "", body)
    body = re.sub(r"^\s*by\b[ \t]*\n?", "", body)
    # Tabs -> two spaces.
    body = body.replace("\t", "  ")
    # Trim trailing whitespace on each line.
    body = "\n".join(line.rstrip() for line in body.splitlines())
    return body.strip("\n")


# --- LLM transport --------------------------------------------------------


def _call(
    *,
    client: Any,
    model: str,
    user: str,
    max_tokens: int,
    api_log_hook: Optional[Any] = None,
) -> str:
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
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
            purpose="leanstral_whole_proof_generator",
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


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


# --- Public API -----------------------------------------------------------


def build_user_prompt(
    *,
    paper_id: str,
    theorem_name: str,
    lean_statement: str,
    paper_theory_hint: str,
    paper_local_file: str,
    error_tail: str = "",
    audited_core_hint: Optional[str] = None,
    latex_proof_hints: Optional[list[str]] = None,
    name_index: Optional[dict[str, Any]] = None,
    premise_index: Any = None,
    anchor_top_k: int = 5,
    premise_top_k: int = 10,
) -> str:
    """Construct the user-message prompt. Exposed for tests.

    `audited_core_hint` (B3): per-paper curated proof excerpts. If None,
    loaded from `data/paper_audited_proof_hints/<paper_id>.txt` via
    `extract_audited_core_hints.load_hint`. Pass "" to disable.

    `latex_proof_hints` (C2): per-row structural keywords from the
    paper's LaTeX proof. If None, loaded from
    `output/corpus/latex_proof_hints.jsonl` via
    `extract_latex_proof_hint.load_hints` keyed by
    (paper_id, theorem_name). Pass [] to disable.

    `name_index` / `premise_index` (A1/A3): Mathlib alignment + premise
    indices used to inject Mathlib-anchor candidates into the prompt.
    When `name_index` is provided AND `error_tail` mentions
    `unknown identifier 'X'` / `synthInstanceFailed: <Class>`, candidate
    Mathlib names are listed. When `premise_index` is provided, top-K
    Mathlib lemmas with overlapping identifier tokens are listed. Both
    blocks are best-effort: silently dropped on miss. See
    `scripts/leanstral_proof_anchors.py` for the builders.
    """
    stmt = re.sub(r"[ \t]+", " ", lean_statement or "").strip()[:MAX_STATEMENT_CHARS]
    hint = (paper_theory_hint or "").strip()[:MAX_HINT_CHARS]
    neighbours = _top_neighbour_declarations(
        lean_src=paper_local_file or "",
        target_name=theorem_name or "",
    ) or "-- (no neighbouring declarations available)"
    retry_block = ""
    if error_tail:
        tail = error_tail[-MAX_ERROR_TAIL_CHARS:]
        retry_block = _RETRY_BLOCK_TEMPLATE.format(error_tail=tail)

    # Audited-core hint (B3): load by paper_id if the caller did not supply.
    audited_section = ""
    audited_hint = audited_core_hint
    if audited_hint is None:
        try:
            from extract_audited_core_hints import load_hint as _load_audited_hint  # type: ignore[import-not-found]
            audited_hint = _load_audited_hint(paper_id or "")
        except Exception:
            audited_hint = ""
    audited_hint = (audited_hint or "").strip()
    if audited_hint:
        if len(audited_hint) > MAX_AUDITED_CORE_CHARS:
            audited_hint = audited_hint[:MAX_AUDITED_CORE_CHARS] + "\n-- ... (truncated) ..."
        audited_section = _AUDITED_CORE_SECTION_TEMPLATE.format(audited_core_hint=audited_hint)

    # LaTeX proof-structure hint (C2): load by (paper_id, theorem_name).
    latex_section = ""
    effective_latex_hints = latex_proof_hints
    if effective_latex_hints is None:
        try:
            from extract_latex_proof_hint import load_hints as _load_latex_hints  # type: ignore[import-not-found]
            cache = _load_latex_hints()
            effective_latex_hints = cache.get((paper_id or "", theorem_name or ""), [])
        except Exception:
            effective_latex_hints = []
    if effective_latex_hints:
        try:
            from extract_latex_proof_hint import format_hint_block as _fmt_latex_hints  # type: ignore[import-not-found]
            block = _fmt_latex_hints(list(effective_latex_hints))
        except Exception:
            block = ""
        if block:
            if len(block) > MAX_LATEX_PROOF_HINT_CHARS:
                block = block[:MAX_LATEX_PROOF_HINT_CHARS] + "\n  ... (truncated) ..."
            latex_section = _LATEX_PROOF_SECTION_TEMPLATE.format(latex_proof_block=block)

    # Mathlib-anchor section (A1/A3) + failure-mode anchors (B1/B2/B3):
    # unknown-identifier + synthInstance candidates from the alignment
    # index, premise-retrieval candidates from the on-disk token-overlap
    # index, plus signature-aware bound-variable / typeclass-gap /
    # tactic-strategy hints when the retry has an error tail. Empty when
    # no Mathlib indices are available AND no failure-mode pattern fires.
    anchor_section = ""
    try:
        from leanstral_proof_anchors import build_anchor_section  # type: ignore[import-not-found]
        body = build_anchor_section(
            error_tail=error_tail or "",
            goal_text=stmt,
            name_index=name_index or {},
            premise_index=premise_index,
            paper_id=paper_id or "",
            anchor_top_k=anchor_top_k,
            premise_top_k=premise_top_k,
            lean_statement=stmt,
        )
        if body:
            if len(body) > MAX_ANCHOR_SECTION_CHARS:
                body = body[:MAX_ANCHOR_SECTION_CHARS] + "\n  ... (truncated) ..."
            anchor_section = _ANCHOR_SECTION_TEMPLATE.format(anchor_body=body)
    except Exception:
        anchor_section = ""

    return _USER_TEMPLATE.format(
        paper_id=paper_id or "",
        theorem_name=theorem_name or "thm",
        lean_statement=stmt,
        paper_theory_hint=hint or "-- (no paper-local symbols exported)",
        neighbours=neighbours,
        latex_proof_section=latex_section,
        audited_core_section=audited_section,
        anchor_section=anchor_section,
        retry_block=retry_block,
    )


def generate_proof_candidate(
    *,
    paper_id: str,
    theorem_name: str,
    lean_statement: str,
    paper_theory_hint: str,
    paper_local_file: str,
    error_tail: str = "",
    client: Any,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    api_log_hook: Optional[Any] = None,
    audited_core_hint: Optional[str] = None,
    latex_proof_hints: Optional[list[str]] = None,
    name_index: Optional[dict[str, Any]] = None,
    premise_index: Any = None,
    use_mathlib_anchors: bool = True,
) -> Optional[dict[str, Any]]:
    """Ask Leanstral to write a whole tactic-mode proof body for `lean_statement`.

    Returns
        {"proof_body": str, "reasoning": str, "confidence": float,
         "rejection_reason": str | None}
        on a candidate that survives the forbidden-token gate, or None when:
            - client is None
            - empty lean_statement
            - transport failure
            - malformed JSON
            - the body is empty after normalization
            - the body contains a forbidden token

    The returned dict includes `rejection_reason=None` on accept. On forbidden-
    token rejection we return None so the caller treats this candidate as a
    miss; the explicit-reason record is only logged.

    Mathlib anchors (A1/A3, `use_mathlib_anchors=True`, default): lazily
    load `data/mathlib_name_index.json` (built by
    mathlib_align_unknown_identifier on first need) and
    `data/mathlib_premise_index.json` (built by leanstral_proof_anchors on
    first need). The name index drives the `unknown identifier 'X'` /
    `synthInstanceFailed: <Class>` retry anchors; the premise index injects
    a top-K premise block on every call (first attempt and retries). Pass
    `use_mathlib_anchors=False` to suppress both (useful for hermetic unit
    tests on machines without `.lake/packages/mathlib`).
    """
    if client is None:
        return None
    if not (lean_statement or "").strip():
        return None

    # Lazy-load Mathlib anchors unless the caller suppressed them or supplied
    # explicit indices. Caching is handled inside the helpers so this only
    # pays the cost on first call.
    effective_name_index = name_index
    effective_premise_index = premise_index
    if use_mathlib_anchors:
        if effective_name_index is None:
            effective_name_index = _get_name_index()
        if effective_premise_index is None:
            try:
                from leanstral_proof_anchors import load_or_build_premise_index  # type: ignore[import-not-found]
                effective_premise_index = load_or_build_premise_index()
            except Exception:
                effective_premise_index = None

    user = build_user_prompt(
        paper_id=paper_id,
        theorem_name=theorem_name,
        lean_statement=lean_statement,
        paper_theory_hint=paper_theory_hint,
        paper_local_file=paper_local_file,
        error_tail=error_tail,
        audited_core_hint=audited_core_hint,
        latex_proof_hints=latex_proof_hints,
        name_index=effective_name_index,
        premise_index=effective_premise_index,
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
        return None

    parsed = _extract_json_object(raw)
    if parsed is None:
        return None

    body_raw = parsed.get("proof_body", "")
    if not isinstance(body_raw, str):
        return None
    body = _normalize_proof_body(body_raw)
    if not body:
        return None

    forbidden = _contains_forbidden_token(body)
    if forbidden is not None:
        return None

    reasoning = str(parsed.get("reasoning", "") or "").strip()[:600]
    confidence = _clamp01(parsed.get("confidence", 0.0))
    return {
        "proof_body": body,
        "reasoning": reasoning,
        "confidence": confidence,
        "rejection_reason": None,
        "protocol": "leanstral_whole_proof_v1",
    }


# --- Paper-theory hint reuse ---------------------------------------------


def extract_paper_theory_hint(paper_theory_path: Path, *, max_lines: int = 80) -> str:
    """Thin wrapper around lemma_factor_assistant.extract_paper_theory_hint
    (we re-export so callers can stay decoupled). Returns "" on any error."""
    try:
        from lemma_factor_assistant import extract_paper_theory_hint as _impl  # type: ignore[import-not-found]
    except Exception:
        return ""
    try:
        return _impl(paper_theory_path, max_lines=max_lines)
    except Exception:
        return ""


# --- CLI smoke ------------------------------------------------------------


def _build_mistral_client() -> Any | None:  # pragma: no cover - smoke wiring
    try:
        from mistralai import Mistral  # type: ignore[import-not-found]
    except Exception:
        return None
    key = os.environ.get("MISTRAL_API_KEY") or os.environ.get("LEANSTRAL_API_KEY")
    if not key:
        return None
    try:
        return Mistral(api_key=key)
    except Exception:
        return None


def _smoke_main(argv: list[str]) -> int:  # pragma: no cover - smoke wiring
    import argparse

    p = argparse.ArgumentParser(
        description="Smoke: invoke Leanstral whole-proof generator on a single theorem."
    )
    p.add_argument("--paper-id", required=True)
    p.add_argument("--theorem-name", required=True)
    p.add_argument("--lean-statement", required=True)
    p.add_argument("--paper-theory", default="")
    p.add_argument("--source-file", default="")
    p.add_argument("--error-tail", default="")
    p.add_argument("--model", default=DEFAULT_MODEL)
    args = p.parse_args(argv)

    client = _build_mistral_client()
    if client is None:
        print("[error] MISTRAL_API_KEY not set or mistralai unavailable", file=sys.stderr)
        return 2

    paper_theory_hint = ""
    if args.paper_theory:
        paper_theory_hint = extract_paper_theory_hint(Path(args.paper_theory))
    paper_local_file = ""
    if args.source_file and Path(args.source_file).exists():
        paper_local_file = Path(args.source_file).read_text(encoding="utf-8")

    result = generate_proof_candidate(
        paper_id=args.paper_id,
        theorem_name=args.theorem_name,
        lean_statement=args.lean_statement,
        paper_theory_hint=paper_theory_hint,
        paper_local_file=paper_local_file,
        error_tail=args.error_tail,
        client=client,
        model=args.model,
    )
    if result is None:
        print("[whole_proof] generator returned None")
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_smoke_main(sys.argv[1:]))
