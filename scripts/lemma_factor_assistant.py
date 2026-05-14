#!/usr/bin/env python3
"""LLM-driven lemma factoring for long unresolved theorems (Leanstral).

Long UR theorems (median ~222 chars, p90 ~545) with multi-conjunction or
multi-existential conclusions are hard for state-MCTS and for `aesop`. The
leverage move: ask Leanstral to propose 2-5 *auxiliary* lemma signatures that
prove parts of the conclusion, then chain them with `And.intro` / `⟨_, _⟩` for
the final theorem.

Public API mirrors `llm_statement_repair`:

    factor_long_theorem(*, paper_id, theorem_name, lean_statement,
                        paper_theory_hint, client, model=...,
                        validate_elaboration=None, max_aux=5)
        -> list[dict]
        # each dict: {'aux_name', 'aux_signature', 'compose_hint', 'rejected'}

Standards-positive: an aux signature that does NOT elaborate is rejected. No
sorry-bodied aux can be propagated as proven. If `validate_elaboration` is
supplied, each candidate aux is probed before being kept; aux entries that
fail elaboration are dropped (still recorded in the audit JSONL with their
rejection reason).

Pipeline policy: Leanstral is the ONLY model permitted; default
`labs-leanstral-2603`.
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
        body_tail = target.split(":=", 1)[-1] if ":=" in target else target
        if "true" == body_tail.strip().split("by", 1)[0].strip():
            return True
        if re.search(r"∃\s+\w+\s*:\s*ℝ\s*,\s*(\w+)\s*=\s*\1", target):
            return True
        return False


try:
    from translator._translate import _deterministic_signature_cleanup  # type: ignore[import-not-found]
except Exception:  # pragma: no cover

    def _deterministic_signature_cleanup(sig: str) -> str:  # type: ignore[misc]
        return sig or ""


DEFAULT_MODEL = os.getenv("MISTRAL_MODEL", "labs-leanstral-2603")
DEFAULT_MAX_TOKENS = 1200
MAX_STATEMENT_CHARS = 2000
MAX_HINT_CHARS = 1500
MAX_AUX_DEFAULT = 5
MIN_AUX_DEFAULT = 2
MAX_LEAN_ERROR_TAIL_CHARS = 500


_SYSTEM_PROMPT = (
    "You are a research assistant that decomposes Lean 4 theorems into "
    "smaller, easier auxiliary lemmas. You receive (1) the original theorem "
    "(name + statement, ending in `:= by sorry`) and (2) a paper-theory hint "
    "with paper-local definitions, abbreviations, axioms, and instances.\n\n"
    "Your job: propose 2-5 AUXILIARY LEMMA SIGNATURES that, when combined, "
    "imply the original theorem's conclusion. Good factorings include:\n"
    "  * splitting a conjunction `P ∧ Q ∧ R` into one lemma per conjunct;\n"
    "  * splitting an existential `∃ C > 0, ∀ x, P x ∧ Q x` into a "
    "    constant-construction lemma plus per-claim lemmas;\n"
    "  * isolating a bound that can be proven by `simp`/`linarith` alone "
    "    from the harder structural part.\n\n"
    "STRICT RULES:\n"
    "  1. Each aux signature MUST start with `theorem ` and have body `:= by sorry`.\n"
    "  2. Each aux name MUST be derived from the original (e.g. `<orig>_aux_<k>` "
    "     or `<orig>_<part>`) and MUST be a valid Lean identifier.\n"
    "  3. Aux signatures use ONLY paper-local symbols (from the hint) and Mathlib. "
    "     Do NOT introduce new typeclasses, new axioms, or new variables that "
    "     aren't bound by the aux's own arguments or already in the hint.\n"
    "  4. Body MUST be exactly ` := by sorry` (one sorry, no proof attempt).\n"
    "  5. Do NOT produce trivial aux lemmas (`: True`, `0 = 0`, `∃ x : ℝ, x = x`, "
    "     opaque `Statement`/`PaperClaim` Props, `: False`).\n"
    "  6. For each aux, also provide a one-line `compose_hint` describing how "
    "     it contributes to the original (e.g. \"first conjunct\", \"witness for ∃ C\").\n\n"
    "Output ONLY a single JSON object with this schema:\n"
    '  {\n'
    '    "verdict": "FACTOR" | "REFUSE",\n'
    '    "aux_lemmas": [\n'
    '       {"aux_name": "...", "aux_signature": "theorem ... := by sorry",\n'
    '        "compose_hint": "..."}\n'
    '    ],\n'
    '    "compose_strategy": "one-sentence sketch of how to combine aux into orig",\n'
    '    "reasoning": "one or two sentences justifying the split",\n'
    '    "confidence": 0.00\n'
    '  }\n'
    "No prose, no markdown fences — just the JSON. If the theorem cannot be "
    "factored (e.g. atomic conclusion, single equation), return verdict=`REFUSE` "
    "with an empty `aux_lemmas` list."
)


_USER_TEMPLATE = (
    "Original theorem (name `{theorem_name}`):\n"
    "```lean\n{lean_statement}\n```\n\n"
    "Paper-theory hint (already in scope):\n"
    "```lean\n{paper_theory_hint}\n```\n\n"
    "Propose 2-5 auxiliary lemmas now. Respond with the JSON object only."
)


# Patterns the placeholder gate rejects (identical to llm_statement_repair).
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


# --- JSON extraction ------------------------------------------------------


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


# --- Decl normalization ---------------------------------------------------


_IDENT_SAFE_RX = re.compile(r"[^A-Za-z0-9_']")


def _sanitize_aux_name(raw_name: str, base: str, idx: int) -> str:
    """Coerce an aux name to a valid Lean identifier; fall back to `<base>_aux_<idx>`."""
    nm = (raw_name or "").strip()
    nm = nm.rsplit(".", 1)[-1]
    nm = _IDENT_SAFE_RX.sub("_", nm).strip("_")
    if not nm or not re.match(r"^[A-Za-z_]", nm):
        base_safe = _IDENT_SAFE_RX.sub("_", (base or "thm").rsplit(".", 1)[-1]).strip("_") or "thm"
        return f"{base_safe}_aux_{idx}"
    return nm


def _normalize_aux_decl(raw: str, aux_name: str) -> str:
    """Strip fences, ensure leading `theorem <aux_name>`, body `:= by sorry`."""
    decl = (raw or "").strip()
    if not decl:
        return ""
    if decl.startswith("```"):
        decl = re.sub(r"^```(?:lean)?\s*", "", decl)
        decl = re.sub(r"\s*```\s*$", "", decl)
    decl = decl.strip()
    decl = re.sub(r"^(?:Lean:|Output:|Answer:)\s*", "", decl, flags=re.IGNORECASE)
    # Accept `lemma`, normalize to `theorem`.
    decl = re.sub(r"^lemma\s+", "theorem ", decl)
    # Rewrite the theorem name slot to our sanitized aux_name.
    if re.match(r"^\s*theorem\s+[A-Za-z_]", decl):
        decl = re.sub(
            r"^(\s*)theorem\s+[A-Za-z_][A-Za-z0-9_'.]*",
            rf"\1theorem {aux_name}",
            decl,
            count=1,
        )
    else:
        # Prepend if model omitted the keyword.
        decl = f"theorem {aux_name} {decl.lstrip()}"
    # Force body to `:= by sorry`.
    decl = re.sub(r":=\s*by\s+.+$", ":= by sorry", decl, flags=re.DOTALL).strip()
    if not decl.endswith(":= by sorry"):
        decl = re.sub(r":=.*$", "", decl, flags=re.DOTALL).strip()
        decl = decl + " := by sorry"
    decl = re.sub(r"\s+", " ", decl).strip()
    return decl


# --- LLM transport --------------------------------------------------------


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
    try:  # pragma: no cover - prefer telemetry path
        from ponder_loop import _chat_complete  # type: ignore[import-not-found]

        _, text = _chat_complete(
            client=client,
            model=model,
            messages=messages,
            temperature=0.0,
            max_tokens=max_tokens,
            purpose="lemma_factor_assistant",
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


def factor_long_theorem(
    *,
    paper_id: str,
    theorem_name: str,
    lean_statement: str,
    paper_theory_hint: str,
    client: Any,
    model: str = DEFAULT_MODEL,
    api_log_hook: Optional[Any] = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    validate_elaboration: Optional[Callable[[str], tuple[bool, str]]] = None,
    max_aux: int = MAX_AUX_DEFAULT,
    min_aux: int = MIN_AUX_DEFAULT,
) -> list[dict[str, Any]]:
    """Ask Leanstral to propose 2-5 auxiliary lemmas decomposing
    `lean_statement`. Returns a list of dicts:

        {
            "aux_name": str,
            "aux_signature": str,             # `:= by sorry` normalized
            "compose_hint": str,
            "rejected": list[str],            # empty when kept
            "compose_strategy": str,          # optional, present on first kept aux
            "elaboration_ok": bool | None,    # None when no validator supplied
            "elaboration_error": str,         # tail of lean error if any
            "protocol": "lemma_factor_v1",
        }

    Each aux signature is normalized and gated through `_is_placeholder_decl`
    and `_is_trivialized_signature` BEFORE optional elaboration. If
    `validate_elaboration` is supplied, aux signatures that fail elaboration
    are kept in the return list with `rejected=['elaboration_gate']` so the
    caller can audit; the downstream JSONL writer / proof-search wiring is
    expected to drop them.

    The return list contains **only kept** aux entries (rejected ones can be
    inspected via the JSONL writer / audit log).

    Returns an empty list on transport error, malformed JSON, LLM refusal,
    empty input, or when fewer than `min_aux` aux signatures survive gating.
    """
    if not (lean_statement or "").strip():
        return []
    if client is None:
        return []
    max_aux = max(1, min(int(max_aux or MAX_AUX_DEFAULT), 10))
    min_aux = max(1, min(int(min_aux or MIN_AUX_DEFAULT), max_aux))

    stmt_trim = re.sub(r"[ \t]+", " ", lean_statement).strip()[:MAX_STATEMENT_CHARS]
    hint_trim = (paper_theory_hint or "").strip()[:MAX_HINT_CHARS]
    base_name = (theorem_name or "thm").strip().rsplit(".", 1)[-1] or "thm"

    user = _USER_TEMPLATE.format(
        theorem_name=base_name,
        lean_statement=stmt_trim,
        paper_theory_hint=hint_trim or "-- (no paper-local symbols exported)",
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
        aux_name = _sanitize_aux_name(raw_name, base_name, idx)
        decl = _normalize_aux_decl(raw_sig, aux_name)
        if not decl:
            continue
        # Apply translator-side cleanup so λ→lam etc. normalize.
        decl = _deterministic_signature_cleanup(decl)

        rejected: list[str] = []
        if _is_placeholder_decl(decl):
            rejected.append("placeholder_pattern_detected")
        if _is_trivialized_signature(decl):
            rejected.append("trivialized_signature")

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
            "protocol": "lemma_factor_v1",
            "paper_id": paper_id,
            "parent_theorem_name": theorem_name,
        }
        kept.append(record)

    # Standards-positive: only emit when at least `min_aux` aux survived ALL gates.
    surviving = [r for r in kept if not r["rejected"]]
    if len(surviving) < min_aux:
        # Return ALL kept records (including rejected) so the caller can audit;
        # the downstream JSONL writer differentiates kept vs rejected.
        return kept
    return kept


# --- JSONL writer ---------------------------------------------------------


def write_lemma_factor_jsonl(
    *,
    candidates: list[dict[str, Any]],
    output_path: Path,
    append: bool = True,
) -> int:
    """Write per-aux candidate rows to JSONL. Returns count of rows written.

    Rows include the full record from `factor_long_theorem` plus a generated
    `row_id` (paper_id + parent_theorem_name + aux_name).
    """
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


# --- Paper-theory hint extraction (mirrors llm_statement_repair) ----------


_HINT_KEYWORDS = ("abbrev", "def", "axiom", "instance", "class", "structure")


def extract_paper_theory_hint(paper_theory_path: Path, *, max_lines: int = 80) -> str:
    """Return up to `max_lines` def/abbrev/axiom/instance/class/structure lines."""
    try:
        text = Path(paper_theory_path).read_text(encoding="utf-8")
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
        compact = re.sub(r":=.*$", "", stripped).strip()
        if not compact:
            continue
        out.append(compact)
        if len(out) >= max_lines:
            break
    return "\n".join(out)


# --- CLI (smoke entry point) ----------------------------------------------


def _build_default_validator(
    *, project_root: Path, source_file: Path, timeout_s: int = 45
) -> Optional[Callable[[str], tuple[bool, str]]]:
    """Best-effort: import the in-tree `_run_isolated_file_check` and return a
    validator closure. Returns None if the helper can't be imported (e.g.
    unit-test environment without lake)."""
    try:
        from prove_arxiv_batch import _run_isolated_file_check  # type: ignore[import-not-found]
    except Exception:
        return None

    def _validate(decl: str) -> tuple[bool, str]:
        return _run_isolated_file_check(
            project_root=project_root,
            source_file=source_file,
            theorem_decl=decl,
            timeout_s=timeout_s,
        )

    return _validate


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
        description="Smoke: factor long UR theorems into aux lemmas via Leanstral."
    )
    p.add_argument("--paper-id", required=True, help="e.g. 2604.21583")
    p.add_argument("--theorem-name", required=True, help="parent theorem short name")
    p.add_argument("--lean-statement", required=True, help="full theorem decl")
    p.add_argument("--paper-theory", default="", help="path to Paper_<id>.lean (optional)")
    p.add_argument("--project-root", default=".")
    p.add_argument("--source-file", default="", help="path to output/<id>.lean for prelude")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument(
        "--output-jsonl",
        default="output/lemma_factor_candidates.jsonl",
        help="Where to append aux-lemma rows.",
    )
    p.add_argument("--no-validate", action="store_true", help="Skip elaboration probe.")
    p.add_argument("--timeout-s", type=int, default=45)
    args = p.parse_args(argv)

    client = _build_mistral_client()
    if client is None:
        print("[error] MISTRAL_API_KEY not set or mistralai unavailable", file=sys.stderr)
        return 2

    project_root = Path(args.project_root).resolve()
    paper_theory_hint = ""
    if args.paper_theory:
        paper_theory_hint = extract_paper_theory_hint(Path(args.paper_theory))

    validator: Optional[Callable[[str], tuple[bool, str]]] = None
    if not args.no_validate and args.source_file:
        validator = _build_default_validator(
            project_root=project_root,
            source_file=Path(args.source_file),
            timeout_s=args.timeout_s,
        )

    candidates = factor_long_theorem(
        paper_id=args.paper_id,
        theorem_name=args.theorem_name,
        lean_statement=args.lean_statement,
        paper_theory_hint=paper_theory_hint,
        client=client,
        model=args.model,
        validate_elaboration=validator,
    )
    kept = [c for c in candidates if not c.get("rejected")]
    print(
        f"[lemma_factor] proposed={len(candidates)} elaborated={len(kept)} "
        f"theorem={args.theorem_name}"
    )
    written = write_lemma_factor_jsonl(
        candidates=candidates, output_path=project_root / args.output_jsonl
    )
    print(f"[lemma_factor] wrote {written} rows to {args.output_jsonl}")
    return 0 if kept else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_smoke_main(sys.argv[1:]))
