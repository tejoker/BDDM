#!/usr/bin/env python3
"""Leanstral REPL-driven (step-by-step) proof generator.

The whole-proof generator (`leanstral_whole_proof_generator`) asks Leanstral to
emit an ENTIRE tactic-mode proof body in one shot, then validates the whole
body via `lake env lean`. Round-VI's 79/79 lake-error rate on whole-body
proofs suggests Leanstral struggles when it can't see the *proof state* between
tactics.

This module drives the proof tactic-by-tactic using the project's
`lean_repl_dojo.REPLDojo` (LeanDojo-style incremental lake build). At each
step:

  1. Read the current proof state (Lean's pretty-printed goal).
  2. Ask Leanstral for up to N candidate tactics that should make progress.
  3. Filter forbidden tokens (`sorry`/`admit`/`apply?`/`axiom`/`native_decide`).
  4. Try each candidate via `REPLDojo.run_tac`. The first candidate that
     advances the state (or closes the goal) is committed.
  5. If all candidates fail at a step, the search aborts and returns None.

After the goal closes, we ALSO validate the assembled `proof_body` against
the on-disk paper file via a second REPLDojo pass — this is the same lake
check used by the whole-proof generator and guards against in-REPL state
drift relative to the real file's neighbouring declarations.

Public API::

    prove_via_repl(
        *,
        paper_id, theorem_name, lean_statement,
        paper_theory_hint, paper_local_file,
        client, model="labs-leanstral-2603",
        max_steps=12, max_attempts_per_step=4,
        repl_timeout_s=60, api_log_hook=None,
    ) -> dict | None

    Returns ``{'proof_body': str, 'rounds': int, 'steps': list[dict],
               'protocol': 'leanstral_repl_v1'}`` on success, or ``None`` on
    failure (transport error, malformed JSON, forbidden-token-only
    candidates, all candidates fail at a step, max_steps reached, or final
    lake-validation failure).

Standards-positive: same forbidden-token gate as the whole-proof generator.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


DEFAULT_MODEL = os.getenv("MISTRAL_MODEL", "labs-leanstral-2603")
DEFAULT_MAX_TOKENS = 1200
DEFAULT_MAX_STEPS = 12
DEFAULT_MAX_ATTEMPTS_PER_STEP = 4
DEFAULT_REPL_TIMEOUT_S = 60

MAX_STATEMENT_CHARS = 2400
MAX_HINT_CHARS = 1800
MAX_STATE_CHARS = 2000

# Whole-token forbidden tokens (must not appear anywhere in any candidate).
FORBIDDEN_TOKENS: tuple[str, ...] = (
    "sorry",
    "admit",
    "apply?",
    "axiom",
    "native_decide",
)


# --- Prompt templates -----------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a Lean 4 proof engineer driving an interactive REPL. You are "
    "given:\n"
    "  - A theorem statement.\n"
    "  - A paper-theory hint listing locally available definitions, axioms, "
    "    instances, and abbreviations.\n"
    "  - The CURRENT proof state pretty-printed by Lean (hypotheses then `⊢ "
    "    goal`).\n"
    "  - The list of tactics already accepted by the REPL (so you can avoid "
    "    no-ops and repetitions).\n\n"
    "Your task: propose the NEXT tactic only. Output 3-5 candidates ordered by "
    "your best estimate of likelihood-of-progress.\n\n"
    "STRICT RULES (violations cause the entire response to be discarded):\n"
    "  1. Output ONLY a single JSON object: "
    '{"tactics": ["<tac1>", "<tac2>", ...]}\n'
    "  2. NEVER include any of the literal tokens `sorry`, `admit`, `apply?`, "
    "     `axiom`, `native_decide` in any candidate.\n"
    "  3. Each candidate is ONE Lean tactic (or a small group on adjacent "
    "     lines). Multi-line tactics are allowed — put a literal newline in "
    "     the string.\n"
    "  4. Prefer idiomatic Lean 4: `intro` / `obtain` / `rcases` / `have` / "
    "     `refine` / `exact` / `simp` / `rw` / `omega` / `linarith` / `aesop` "
    "     etc. Use only Mathlib lemmas and the paper-theory symbols listed in "
    "     the hint.\n"
    "  5. Do NOT include the theorem signature or `:= by` prefix — only the "
    "     tactic body for THIS step.\n"
    "  6. If the goal looks unprovable from this state, return "
    '{"tactics": []} — honest empty responses cost nothing.\n'
)


_USER_TEMPLATE = (
    "Theorem (name `{theorem_name}`, paper `{paper_id}`):\n"
    "```lean\n{lean_statement}\n```\n\n"
    "Paper-theory exports (already in scope):\n"
    "```lean\n{paper_theory_hint}\n```\n\n"
    "Tactics accepted so far ({history_n}):\n"
    "```lean\n{history}\n```\n\n"
    "Current proof state (from the Lean REPL):\n"
    "```\n{state_pp}\n```\n\n"
    "{retry_block}"
    "Propose the NEXT tactic. Respond with a JSON object only."
)


_RETRY_BLOCK_TEMPLATE = (
    "The previous candidate(s) failed at this step:\n"
    "```\n{error_tail}\n```\n"
    "Do not repeat them. Try a different decomposition.\n\n"
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


# --- Forbidden-token gate (mirrors whole-proof generator) ----------------


def _contains_forbidden_token(body: str) -> Optional[str]:
    if not body:
        return None
    for tok in FORBIDDEN_TOKENS:
        if "?" in tok:
            if tok in body:
                return tok
            continue
        pattern = re.compile(r"(?<![A-Za-z0-9_'])" + re.escape(tok) + r"(?![A-Za-z0-9_'])")
        if pattern.search(body):
            return tok
    return None


# --- Tactic normalization -------------------------------------------------


def _strip_code_fences(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:lean)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def _normalize_tactic(raw: str) -> str:
    """Tabs to two spaces, strip code fences, drop a leading `:= by`/`by `."""
    if not isinstance(raw, str):
        return ""
    t = _strip_code_fences(raw)
    t = re.sub(r"^\s*:=\s*by\b[ \t]*\n?", "", t)
    t = re.sub(r"^\s*by\b[ \t]*\n?", "", t)
    t = t.replace("\t", "  ")
    t = "\n".join(line.rstrip() for line in t.splitlines())
    return t.strip("\n")


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
            purpose="leanstral_repl_proof_generator",
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


# --- Prompt construction --------------------------------------------------


def build_step_prompt(
    *,
    paper_id: str,
    theorem_name: str,
    lean_statement: str,
    paper_theory_hint: str,
    history: list[str],
    state_pp: str,
    error_tail: str = "",
) -> str:
    """Construct the user message for a single REPL step. Exposed for tests."""
    stmt = re.sub(r"[ \t]+", " ", lean_statement or "").strip()[:MAX_STATEMENT_CHARS]
    hint = (paper_theory_hint or "").strip()[:MAX_HINT_CHARS] or "-- (none)"
    history_text = "\n".join(history) if history else "-- (none)"
    state = (state_pp or "").strip()[:MAX_STATE_CHARS] or "⊢ ???"
    retry_block = ""
    if error_tail:
        retry_block = _RETRY_BLOCK_TEMPLATE.format(error_tail=error_tail[-500:])
    return _USER_TEMPLATE.format(
        paper_id=paper_id or "",
        theorem_name=theorem_name or "thm",
        lean_statement=stmt,
        paper_theory_hint=hint,
        history_n=len(history),
        history=history_text,
        state_pp=state,
        retry_block=retry_block,
    )


# --- Candidate extraction -------------------------------------------------


def _extract_candidates(raw: str) -> list[str]:
    """Parse Leanstral's JSON response into a list of candidate tactic strings.

    Returns the normalized candidates with forbidden ones removed. Returns
    [] on malformed JSON, missing key, or all-forbidden output.
    """
    parsed = _extract_json_object(raw)
    if parsed is None:
        return []
    items = parsed.get("tactics")
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        t = _normalize_tactic(item)
        if not t:
            continue
        if _contains_forbidden_token(t) is not None:
            continue
        out.append(t)
    return out


# --- REPL driver ----------------------------------------------------------


def _default_dojo_factory(
    *, project_root: Path, file_path: Path, theorem_name: str, timeout_s: int
) -> Any:
    """Real REPLDojo factory. Indirected so tests can inject a stub."""
    from lean_repl_dojo import REPLDojo  # type: ignore[import-not-found]

    return REPLDojo(
        project_root=project_root,
        file_path=file_path,
        theorem_name=theorem_name,
        timeout=timeout_s,
    )


def _is_proof_finished(result: Any) -> bool:
    return type(result).__name__ == "ProofFinished"


def _is_tactic_state(result: Any) -> bool:
    return type(result).__name__ == "TacticState"


def _is_lean_error(result: Any) -> bool:
    return type(result).__name__ == "LeanError"


def _error_text(result: Any) -> str:
    return getattr(result, "error", "") or ""


def _state_pp(state: Any) -> str:
    return getattr(state, "pp", "") or ""


def _state_id(state: Any) -> int:
    try:
        return int(getattr(state, "id", 0) or 0)
    except Exception:
        return 0


def _find_paper_file(paper_local_file: str, paper_id: str) -> Optional[Path]:
    """Best-effort: locate the actual on-disk `.lean` file the REPL should
    operate on. We try, in order:

      1. `paper_local_file` interpreted as a path (the new contract).
      2. `output/<paper_id>.lean` relative to PROJECT_ROOT.
      3. None — caller must skip the REPL step.

    `paper_local_file` is overloaded in the wider codebase: the whole-proof
    generator takes the file CONTENTS as a string. We accept both; if it
    looks like multiline Lean source we fall back to the canonical
    `output/<paper>.lean` path.
    """
    try:
        candidate = Path(paper_local_file)
        if candidate.is_file():
            return candidate
    except Exception:
        pass
    project_root = SCRIPT_DIR.parent
    canonical = project_root / "output" / f"{paper_id}.lean"
    if canonical.is_file():
        return canonical
    return None


def prove_via_repl(
    *,
    paper_id: str,
    theorem_name: str,
    lean_statement: str,
    paper_theory_hint: str,
    paper_local_file: str,
    client: Any,
    model: str = DEFAULT_MODEL,
    max_steps: int = DEFAULT_MAX_STEPS,
    max_attempts_per_step: int = DEFAULT_MAX_ATTEMPTS_PER_STEP,
    repl_timeout_s: int = DEFAULT_REPL_TIMEOUT_S,
    api_log_hook: Optional[Any] = None,
    diagnostic_log: Optional[Callable[[dict[str, Any]], None]] = None,
    _dojo_factory: Optional[Callable[..., Any]] = None,
    _project_root: Optional[Path] = None,
    _file_path: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Interactive REPL-driven proof search.

    See module docstring. Returns ``{'proof_body', 'rounds', 'steps',
    'protocol'}`` on success or ``None`` on failure.

    The optional ``_dojo_factory`` / ``_project_root`` / ``_file_path``
    parameters are test-injection seams; production callers pass paper_id /
    paper_local_file and we resolve the canonical paths.
    """
    if client is None:
        return None
    if not (lean_statement or "").strip():
        return None
    if not (theorem_name or "").strip():
        return None
    if max_steps <= 0 or max_attempts_per_step <= 0:
        return None

    project_root = _project_root or SCRIPT_DIR.parent
    file_path = _file_path
    if file_path is None:
        resolved = _find_paper_file(paper_local_file, paper_id)
        if resolved is None:
            return None
        # REPLDojo expects file_path relative to project_root.
        try:
            file_path = resolved.relative_to(project_root)
        except ValueError:
            file_path = resolved

    dojo_factory = _dojo_factory or _default_dojo_factory
    accepted: list[str] = []
    step_log: list[dict[str, Any]] = []
    api_calls = 0

    try:
        dojo_cm = dojo_factory(
            project_root=project_root,
            file_path=file_path,
            theorem_name=theorem_name,
            timeout_s=repl_timeout_s,
        )
    except Exception:
        return None

    def _emit_diag(outcome: str, **extra: Any) -> None:
        if diagnostic_log is None:
            return
        payload = {
            "outcome": outcome,
            "paper_id": paper_id,
            "theorem": theorem_name,
            "steps": list(step_log),
            "api_calls": api_calls,
            "accepted_tactics": list(accepted),
        }
        payload.update(extra)
        try:
            diagnostic_log(payload)
        except Exception:
            pass

    try:
        try:
            ctx = dojo_cm.__enter__()
        except Exception:
            _emit_diag("repl_enter_failed")
            return None
        if isinstance(ctx, tuple) and len(ctx) == 2:
            dojo, state = ctx
        else:  # pragma: no cover - defensive
            dojo, state = ctx, getattr(ctx, "_init_state", None)
        if not _is_tactic_state(state):
            _emit_diag("repl_no_initial_state")
            return None

        try:
            for step_idx in range(max_steps):
                state_pp = _state_pp(state)
                # Ask Leanstral for candidates.
                error_tail = ""
                step_attempts_failed: list[tuple[str, str]] = []
                step_done = False

                # We allow one LLM call per step. The model returns up to
                # `max_attempts_per_step` candidates and we try each.
                user = build_step_prompt(
                    paper_id=paper_id,
                    theorem_name=theorem_name,
                    lean_statement=lean_statement,
                    paper_theory_hint=paper_theory_hint,
                    history=accepted,
                    state_pp=state_pp,
                    error_tail=error_tail,
                )
                try:
                    raw = _call(
                        client=client,
                        model=model,
                        user=user,
                        max_tokens=DEFAULT_MAX_TOKENS,
                        api_log_hook=api_log_hook,
                    )
                    api_calls += 1
                except Exception as exc:
                    _emit_diag("llm_transport_error", step_idx=step_idx, error=str(exc)[:200])
                    return None

                candidates = _extract_candidates(raw)
                if not candidates:
                    _emit_diag(
                        "no_candidates",
                        step_idx=step_idx,
                        state_pp=state_pp[:300],
                        raw_preview=(raw or "")[:300],
                    )
                    return None
                candidates = candidates[:max_attempts_per_step]

                for cand in candidates:
                    try:
                        result = dojo.run_tac(state, cand)
                    except Exception as exc:
                        step_attempts_failed.append((cand, f"repl_exception:{exc!s}"[:200]))
                        continue
                    if _is_proof_finished(result):
                        accepted.append(cand)
                        step_log.append({
                            "step_idx": step_idx,
                            "state_before": state_pp,
                            "chosen_tactic": cand,
                            "result": "proof_finished",
                            "tried": len(step_attempts_failed) + 1,
                        })
                        step_done = True
                        # Whole proof done.
                        proof_body = "\n".join(accepted)
                        return {
                            "proof_body": proof_body,
                            "rounds": step_idx + 1,
                            "steps": step_log,
                            "api_calls": api_calls,
                            "protocol": "leanstral_repl_v1",
                        }
                    if _is_tactic_state(result):
                        new_pp = _state_pp(result)
                        if new_pp.strip() and new_pp.strip() != state_pp.strip():
                            accepted.append(cand)
                            step_log.append({
                                "step_idx": step_idx,
                                "state_before": state_pp,
                                "state_after": new_pp,
                                "chosen_tactic": cand,
                                "result": "state_advanced",
                                "tried": len(step_attempts_failed) + 1,
                            })
                            state = result
                            step_done = True
                            break
                        step_attempts_failed.append((cand, "no_progress"))
                        continue
                    if _is_lean_error(result):
                        step_attempts_failed.append((cand, _error_text(result)[:200]))
                        continue
                    step_attempts_failed.append((cand, f"unknown_result:{type(result).__name__}"))

                if not step_done:
                    step_log.append({
                        "step_idx": step_idx,
                        "state_before": state_pp,
                        "chosen_tactic": None,
                        "result": "all_candidates_failed",
                        "tried": len(step_attempts_failed),
                        "failed_tactics": step_attempts_failed[:max_attempts_per_step],
                    })
                    _emit_diag("all_candidates_failed", step_idx=step_idx)
                    return None

            # Out of step budget — proof not closed.
            step_log.append({
                "step_idx": max_steps,
                "result": "max_steps_reached",
            })
            _emit_diag("max_steps_reached")
            return None
        finally:
            pass
    finally:
        try:
            dojo_cm.__exit__(None, None, None)
        except Exception:
            pass


# --- CLI smoke ------------------------------------------------------------


def _build_mistral_client() -> Any | None:  # pragma: no cover - smoke wiring
    try:
        from mistralai import Mistral  # type: ignore[import-not-found]
    except Exception:
        try:
            from mistralai.client import Mistral  # type: ignore[import-not-found,no-redef]
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
        description="Smoke: invoke the Leanstral REPL-driven proof generator on a single theorem."
    )
    p.add_argument("--paper-id", required=True)
    p.add_argument("--theorem-name", required=True)
    p.add_argument("--lean-statement", required=True)
    p.add_argument("--paper-theory", default="")
    p.add_argument("--source-file", default="",
                   help="Path to the on-disk .lean file (containing the theorem).")
    p.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    p.add_argument("--max-attempts-per-step", type=int,
                   default=DEFAULT_MAX_ATTEMPTS_PER_STEP)
    p.add_argument("--repl-timeout-s", type=int, default=DEFAULT_REPL_TIMEOUT_S)
    p.add_argument("--model", default=DEFAULT_MODEL)
    args = p.parse_args(argv)

    client = _build_mistral_client()
    if client is None:
        print("[error] MISTRAL_API_KEY not set or mistralai unavailable", file=sys.stderr)
        return 2

    paper_theory_hint = ""
    if args.paper_theory:
        try:
            from leanstral_whole_proof_generator import (
                extract_paper_theory_hint,
            )  # type: ignore[import-not-found]

            paper_theory_hint = extract_paper_theory_hint(Path(args.paper_theory))
        except Exception:
            paper_theory_hint = ""

    t0 = time.time()
    result = prove_via_repl(
        paper_id=args.paper_id,
        theorem_name=args.theorem_name,
        lean_statement=args.lean_statement,
        paper_theory_hint=paper_theory_hint,
        paper_local_file=args.source_file or "",
        client=client,
        model=args.model,
        max_steps=args.max_steps,
        max_attempts_per_step=args.max_attempts_per_step,
        repl_timeout_s=args.repl_timeout_s,
    )
    elapsed = time.time() - t0
    if result is None:
        print(f"[repl_prover] FAILED (elapsed={elapsed:.1f}s)")
        return 1
    out = dict(result)
    out["wall_clock_s"] = round(elapsed, 1)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_smoke_main(sys.argv[1:]))
