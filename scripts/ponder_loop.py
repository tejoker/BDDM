#!/usr/bin/env python3
"""Micro-search loop for Lean tactic generation using Leanstral-style tags.

Protocol enforced by the system prompt:
- Reasoning inside <think>...</think>
- Ask for more internal steps with <continue>
- Emit exactly one executable tactic in <tactic>...</tactic>
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable

from dotenv import load_dotenv
from mistralai.client import Mistral

SYSTEM_PROMPT = (
    "You are Leanstral. You must think deeply about the current Lean 4 state before acting. "
    "Output your reasoning inside <think> tags. "
    "Inside every <think> block, include a confidence score line formatted exactly as 'CONFIDENCE: <number between 0.0 and 1.0>'. "
    "If you need more time to think, output <continue>. "
    "If you are ready to execute, output exactly one tactic inside <tactic> tags. "
    "Do not output anything outside these tags."
)

CONTINUE_PROMPT = "Continue your train of thought."
FORCE_TACTIC_PROMPT = "ACT HALT: output exactly one <tactic>...</tactic> now. Do not output <continue>."
CONFIDENCE_FORCE_PROMPT = (
    "Your confidence is high. Stop pondering and output exactly one "
    "<tactic>...</tactic> now."
)
TRIVIAL_DIRECT_TACTIC_PROMPT = (
    "The proof state appears trivial. Output exactly one <tactic>...</tactic> now "
    "with no <continue>."
)
FORMAT_REPAIR_PROMPT = (
    "Your previous message did not follow the contract. "
    "Respond again using only these tags: <think>...</think> and either "
    "<continue> or exactly one <tactic>...</tactic>."
)
TACTIC_OPTIONS_SYSTEM_PROMPT = (
    "You are Leanstral. Given a Lean 4 proof state, propose distinct candidate tactics. "
    "Output each candidate inside <tactic>...</tactic>. "
    "Return only tactics, no prose."
)
TACTIC_OPTIONS_USER_PROMPT = (
    "Generate {count} distinct tactics for this Lean 4 state. "
    "Prefer tactics that are likely to compile.\n\n"
    "State:\n{state}"
)

THINK_RE = re.compile(r"<think>(.*?)</think>", re.IGNORECASE | re.DOTALL)
TACTIC_RE = re.compile(r"<tactic>(.*?)</tactic>", re.IGNORECASE | re.DOTALL)
CONTINUE_RE = re.compile(r"<continue\s*/?>|<continue>\s*</continue>", re.IGNORECASE | re.DOTALL)
CONFIDENCE_RE = re.compile(
    r"confidence\s*[:=]\s*([01](?:\.\d+)?)",
    re.IGNORECASE,
)

ApiLogHook = Callable[[dict[str, Any]], None]


@dataclass
class PonderResult:
    tactic: str
    turns: int
    act_budget: int
    thoughts: list[str]
    confidences: list[float]
    raw_responses: list[str]
    halt_reason: str


def _response_to_text(response: Any) -> str:
    """Extract plain assistant text across possible SDK response shapes."""
    try:
        choices = getattr(response, "choices", None)
        if choices and len(choices) > 0:
            first = choices[0]
            message = getattr(first, "message", None)
            if message is not None:
                content = getattr(message, "content", None)
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    chunks: list[str] = []
                    for part in content:
                        txt = getattr(part, "text", None)
                        if isinstance(txt, str):
                            chunks.append(txt)
                    if chunks:
                        return "\n".join(chunks)
    except Exception:
        pass

    # Last-resort stringification keeps debug visibility if SDK shape changes.
    return str(response)


def _chat_complete(
    *,
    client: Mistral,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    purpose: str,
    api_log_hook: ApiLogHook | None,
) -> tuple[Any, str]:
    started = time.time()
    response = client.chat.complete(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    text = _response_to_text(response)
    ended = time.time()

    if api_log_hook is not None:
        api_log_hook(
            {
                "timestamp": started,
                "purpose": purpose,
                "request": {
                    "model": model,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "messages": messages,
                },
                "response_text": text,
                "latency_seconds": max(0.0, ended - started),
            }
        )

    return response, text


def _extract_think(text: str) -> list[str]:
    return [m.strip() for m in THINK_RE.findall(text) if m.strip()]


def _extract_tactics(text: str) -> list[str]:
    return [m.strip() for m in TACTIC_RE.findall(text) if m.strip()]


def _has_continue(text: str) -> bool:
    return bool(CONTINUE_RE.search(text))


def _extract_confidences(think_blocks: list[str]) -> list[float]:
    vals: list[float] = []
    for block in think_blocks:
        for match in CONFIDENCE_RE.findall(block):
            try:
                val = float(match)
            except ValueError:
                continue
            if 0.0 <= val <= 1.0:
                vals.append(val)
    return vals


def _is_trivial_state(lean_state: str, max_chars: int = 80) -> bool:
    s = lean_state.strip()
    if not s:
        return True
    non_empty_lines = [ln for ln in s.splitlines() if ln.strip()]
    if len(s) <= max_chars and len(non_empty_lines) <= 4:
        return True

    lowered = s.lower()
    trivial_markers = [
        "⊢ true",
        "goal: true",
        "goals:\n⊢ true",
        "no goals",
    ]
    return any(marker in lowered for marker in trivial_markers)


def _estimate_state_complexity(lean_state: str) -> float:
    """Return complexity in [0.0, 1.0] from shallow Lean-state heuristics."""
    s = lean_state.strip()
    if not s:
        return 0.0

    non_empty_lines = [ln for ln in s.splitlines() if ln.strip()]
    char_score = min(len(s) / 1200.0, 1.0)
    line_score = min(len(non_empty_lines) / 30.0, 1.0)
    goal_score = min(s.lower().count("⊢") / 6.0, 1.0)
    case_score = min(s.lower().count("case ") / 6.0, 1.0)

    complexity = 0.40 * char_score + 0.30 * line_score + 0.20 * goal_score + 0.10 * case_score
    return max(0.0, min(1.0, complexity))


def adaptive_act_budget(
    *,
    lean_state: str,
    min_turns: int = 2,
    max_turns: int = 8,
) -> int:
    if min_turns < 1:
        raise ValueError("min_turns must be >= 1")
    if max_turns < min_turns:
        raise ValueError("max_turns must be >= min_turns")

    complexity = _estimate_state_complexity(lean_state)
    budget = int(round(min_turns + complexity * (max_turns - min_turns)))
    return max(min_turns, min(max_turns, budget))


def run_ponder_loop(
    *,
    lean_state: str,
    client: Mistral,
    model: str,
    max_turns: int | None = None,
    temperature: float = 0.2,
    confidence_threshold: float = 0.9,
    min_act_turns: int = 2,
    max_act_turns: int = 8,
    trivial_state_chars: int = 80,
    api_log_hook: ApiLogHook | None = None,
) -> PonderResult:
    if not lean_state.strip():
        raise ValueError("lean_state cannot be empty")
    if confidence_threshold < 0.0 or confidence_threshold > 1.0:
        raise ValueError("confidence_threshold must be in [0.0, 1.0]")

    act_budget = (
        max_turns
        if max_turns is not None
        else adaptive_act_budget(
            lean_state=lean_state,
            min_turns=min_act_turns,
            max_turns=max_act_turns,
        )
    )
    if act_budget < 1:
        raise ValueError("act_budget must be >= 1")

    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Current Lean 4 proof state:\n"
                f"{lean_state}\n\n"
                "Follow the format rules strictly and decide your next action."
            ),
        },
    ]

    if _is_trivial_state(lean_state, max_chars=trivial_state_chars):
        messages.append({"role": "user", "content": TRIVIAL_DIRECT_TACTIC_PROMPT})

    thoughts: list[str] = []
    confidences: list[float] = []
    raw_responses: list[str] = []

    is_trivial = _is_trivial_state(lean_state, max_chars=trivial_state_chars)

    for turn in range(1, act_budget + 1):
        _response, text = _chat_complete(
            client=client,
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=500,
            purpose=f"ponder_turn_{turn}",
            api_log_hook=api_log_hook,
        )
        raw_responses.append(text)

        think_blocks = _extract_think(text)
        thoughts.extend(think_blocks)
        confidences.extend(_extract_confidences(think_blocks))

        tactics = _extract_tactics(text)
        if len(tactics) > 1:
            raise RuntimeError(
                "Model returned multiple <tactic> blocks in one turn; expected exactly one."
            )
        if len(tactics) == 1:
            halt_reason = "tactic"
            if is_trivial:
                halt_reason = "trivial-bypass"
            elif confidences and confidences[-1] > confidence_threshold:
                halt_reason = "confidence"
            return PonderResult(
                tactic=tactics[0],
                turns=turn,
                act_budget=act_budget,
                thoughts=thoughts,
                confidences=confidences,
                raw_responses=raw_responses,
                halt_reason=halt_reason,
            )

        if turn == act_budget:
            break

        next_turn = turn + 1
        latest_conf = confidences[-1] if confidences else None
        if latest_conf is not None and latest_conf > confidence_threshold:
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": CONFIDENCE_FORCE_PROMPT})
            continue

        if next_turn == act_budget:
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": FORCE_TACTIC_PROMPT})
            continue

        if _has_continue(text):
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": CONTINUE_PROMPT})
            continue

        # Some responses include only <think>. Request a format-correct follow-up.
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user", "content": FORMAT_REPAIR_PROMPT})
        continue

    raise TimeoutError(f"Reached ACT cap act_budget={act_budget} without receiving <tactic>.")


def generate_tactic_options(
    *,
    lean_state: str,
    client: Mistral,
    model: str,
    num_options: int = 5,
    temperature: float = 0.4,
    api_log_hook: ApiLogHook | None = None,
) -> list[str]:
    """Generate a distinct tactic list for macro-search expansion."""
    if num_options < 1:
        raise ValueError("num_options must be >= 1")

    messages = [
        {"role": "system", "content": TACTIC_OPTIONS_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": TACTIC_OPTIONS_USER_PROMPT.format(count=num_options, state=lean_state),
        },
    ]

    _response, text = _chat_complete(
        client=client,
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=600,
        purpose="generate_tactic_options",
        api_log_hook=api_log_hook,
    )

    seen: set[str] = set()
    tactics: list[str] = []
    for tac in _extract_tactics(text):
        norm = " ".join(tac.split())
        if not norm or norm in seen:
            continue
        seen.add(norm)
        tactics.append(norm)
        if len(tactics) >= num_options:
            break

    return tactics


def _read_lean_state(args: argparse.Namespace) -> str:
    if args.lean_state:
        return args.lean_state
    if args.lean_state_file:
        with open(args.lean_state_file, "r", encoding="utf-8") as fh:
            return fh.read()
    raise ValueError("Provide --lean-state or --lean-state-file")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the URM-style ponder loop")
    parser.add_argument("--lean-state", type=str, default="", help="Lean state string")
    parser.add_argument(
        "--lean-state-file",
        type=str,
        default="",
        help="Path to a file containing Lean state text",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="",
        help="Mistral model name (defaults to MISTRAL_MODEL env)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=0,
        help="Fixed ACT budget; if 0, compute adaptively from proof-state complexity",
    )
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--confidence-threshold", type=float, default=0.9)
    parser.add_argument("--min-act-turns", type=int, default=2)
    parser.add_argument("--max-act-turns", type=int, default=8)
    parser.add_argument("--trivial-state-chars", type=int, default=80)
    parser.add_argument(
        "--show-thoughts",
        action="store_true",
        help="Print collected <think> blocks before the tactic",
    )
    return parser


def main() -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()

    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        print("[fail] MISTRAL_API_KEY is not set")
        return 1

    model = args.model.strip() or os.getenv("MISTRAL_MODEL", "labs-leanstral-2603").strip()
    if not model:
        print("[fail] no model configured")
        return 1

    lean_state = _read_lean_state(args)

    client = Mistral(api_key=api_key)

    fixed_budget = args.max_turns if args.max_turns > 0 else None

    try:
        result = run_ponder_loop(
            lean_state=lean_state,
            client=client,
            model=model,
            max_turns=fixed_budget,
            temperature=args.temperature,
            confidence_threshold=args.confidence_threshold,
            min_act_turns=args.min_act_turns,
            max_act_turns=args.max_act_turns,
            trivial_state_chars=args.trivial_state_chars,
        )
    except Exception as exc:
        print(f"[fail] ponder loop failed: {exc}")
        return 1

    print(
        f"[ok] tactic found in {result.turns} turn(s) "
        f"| act_budget={result.act_budget} | halt_reason={result.halt_reason}"
    )
    if result.confidences:
        print(f"[info] confidences={result.confidences}")
    if args.show_thoughts and result.thoughts:
        print("\n=== THINK TRACE ===")
        for i, t in enumerate(result.thoughts, start=1):
            print(f"[{i}] {t}\n")

    print("=== TACTIC ===")
    print(result.tactic)
    return 0


if __name__ == "__main__":
    sys.exit(main())
