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
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
try:
    from mistralai import Mistral
except ImportError:
    from mistralai.client import Mistral  # type: ignore[no-redef]

try:
    from premise_retrieval import PremiseRetriever
except Exception:
    PremiseRetriever = None  # type: ignore[assignment]

SYSTEM_PROMPT = (
    "You are Leanstral. You must think deeply about the current Lean 4 proof state before acting. "
    "Output your reasoning inside <think> tags. "
    "Inside every <think> block, include a confidence score line formatted exactly as 'CONFIDENCE: <number between 0.0 and 1.0>'. "
    "When provided with available Mathlib premises, ALWAYS use their exact names — never invent or abbreviate lemma names. "
    "If you need more time to think, output <continue>. "
    "If you are ready to execute, output exactly one tactic inside <tactic> tags. "
    "Do not output anything outside these tags.\n\n"
    "ABSOLUTE RULES — violating these wastes the entire search budget:\n"
    "- NEVER output `sorry` or `admit`. A proof containing sorry is not a proof.\n"
    "- NEVER call `rewrite [lemma]` unless you have verified the lemma's LHS pattern appears verbatim in the goal.\n"
    "- NEVER call `linarith` or `omega` unless the goal is a linear (in)equality over integers or naturals.\n"
    "- NEVER invent a lemma name. If a lemma is not in the retrieved premises list, do not use it.\n\n"
    "STRUCTURED REASONING PROCESS:\n"
    "1. Parse the goal: identify the statement type (equation, inequality, exists, forall, etc.)\n"
    "2. Examine retrieved premises: which lemma names EXACTLY match the goal structure?\n"
    "3. Check hypothesis context: are any hypotheses directly applicable?\n"
    "4. Before choosing rewrite: confirm the LHS pattern is literally present in the goal string\n"
    "5. Before choosing linarith/omega: confirm the goal is a linear numeric (in)equality\n"
    "6. Select one tactic: the highest-confidence choice that satisfies all rules above\n\n"
    "SUCCESSFUL PROOF EXAMPLES:\n"
    "Example 1 - Simple arithmetic: Goal: n²≥0 | Retrieved: sq_nonneg | Tactic: exact sq_nonneg n\n"
    "Example 2 - Linear finisher: Goal: a + b ≤ c (integers) | Tactic: linarith\n"
    "Example 3 - Reflexivity: Goal: x=x | Tactic: rfl\n"
    "Example 4 - Ring normalization: Goal: algebraic equality | Tactic: ring\n"
    "Example 5 - Induction: Goal: ∀ n, P n | Tactic: induction n with | zero => ... | succ n ih => ...\n"
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
DRAFT_RE = re.compile(r"<draft>(.*?)</draft>", re.IGNORECASE | re.DOTALL)
LEAN_CODEBLOCK_RE = re.compile(r"```(?:lean|lean4)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
CONTINUE_RE = re.compile(r"<continue\s*/?>|<continue>\s*</continue>", re.IGNORECASE | re.DOTALL)
CONFIDENCE_RE = re.compile(
    r"confidence\s*[:=]\s*([01](?:\.\d+)?)",
    re.IGNORECASE,
)

ApiLogHook = Callable[[dict[str, Any]], None]
_RETRIEVER_CACHE: dict[str, Any] = {}


def load_premise_context(toon_path: str | Path, namespace_filter: str = "") -> str:
    """Load compact premise bullets from a .toon inventory file.

    The parser is intentionally light-weight: it expects inventory rows under a `nodes[...]`
    block with comma-separated columns:
      name,status,namespace,file,notes
    """
    path = Path(toon_path)
    if not path.exists():
        raise FileNotFoundError(f"premise file not found: {path}")

    ns_filter = namespace_filter.strip().lower()
    lines = path.read_text(encoding="utf-8").splitlines()

    in_nodes = False
    bullets: list[str] = []

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("nodes["):
            in_nodes = True
            continue
        if in_nodes and line.startswith("beachheads["):
            break
        if not in_nodes:
            continue
        if line.startswith("#"):
            continue

        parts = [p.strip() for p in line.split(",", 4)]
        if len(parts) < 5:
            continue

        name, status, namespace, _file, notes = parts
        if status.lower() != "exists":
            continue
        if ns_filter and ns_filter not in namespace.lower() and ns_filter not in notes.lower():
            continue

        summary = notes.split(".")[0].strip()
        if summary:
            bullets.append(f"- {name} ({namespace}): {summary}.")
        else:
            bullets.append(f"- {name} ({namespace}).")

    if not bullets:
        return ""

    return "\n".join(bullets)


def build_system_prompt(*, premise_context: str = "") -> str:
    base = SYSTEM_PROMPT
    ctx = premise_context.strip()
    if not ctx:
        return base
    return (
        f"{base}\n\n"
        "Available Mathlib premises for this proof state:\n"
        f"{ctx}"
    )


def _get_retriever(index_path: str | Path) -> Any | None:
    """Load and cache retriever from an index path."""
    if PremiseRetriever is None:
        return None
    key = str(Path(index_path).resolve())
    if key in _RETRIEVER_CACHE:
        return _RETRIEVER_CACHE[key]

    retriever = PremiseRetriever.load(key)
    _RETRIEVER_CACHE[key] = retriever
    return retriever


def retrieve_premise_context(
    *,
    lean_state: str,
    retrieval_index_path: str | Path,
    top_k: int = 12,
    use_tier_preference: bool = False,
    kg_root: str | Path = "output/kg",
) -> str:
    """Retrieve top-k Mathlib premises for a given Lean state.
    
    Args:
        lean_state: Current Lean proof state
        retrieval_index_path: Path to embedding index
        top_k: Number of results to return
        use_tier_preference: If True, prefer trusted/conditional KG layer results
        kg_root: Root path for KG manifests (used if use_tier_preference=True)
    """
    retriever = _get_retriever(retrieval_index_path)
    if retriever is None:
        return ""

    # Use tier-aware retrieval if requested and available
    if use_tier_preference:
        try:
            from premise_retrieval import load_kg_tier_names
            trusted_names, conditional_names = load_kg_tier_names(kg_root)
            results = retriever.query_with_tier_preference(
                lean_state, 
                kg_trusted_names=trusted_names if trusted_names else None,
                kg_conditional_names=conditional_names if conditional_names else None,
                top_k=top_k
            )
        except (ImportError, Exception):
            # Fallback to regular query if tier-aware fails
            results = retriever.query(lean_state, top_k=top_k)
    else:
        results = retriever.query(lean_state, top_k=top_k)

    if not results:
        return ""

    bullets: list[str] = []
    for hit in results:
        stmt = " ".join(hit.statement.split())
        if len(stmt) > 180:
            stmt = stmt[:177] + "..."
        # Add trust tier annotation if present
        tier_suffix = f" [tier: {hit.trust_tier}]" if hasattr(hit, 'trust_tier') and hit.trust_tier != "unknown" else ""
        bullets.append(f"- {hit.name} ({hit.namespace}): {stmt}{tier_suffix}")
    return "\n".join(bullets)


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


def _extract_drafts(text: str) -> list[str]:
    return [m.strip() for m in DRAFT_RE.findall(text) if m.strip()]


def _extract_best_effort_draft(text: str) -> str:
    """Parse a draft with tolerant fallbacks when tags are missing."""
    drafts = _extract_drafts(text)
    if drafts:
        return drafts[0]

    tactics = _extract_tactics(text)
    if tactics:
        return "\n".join(tactics)

    code_blocks = [m.strip() for m in LEAN_CODEBLOCK_RE.findall(text) if m.strip()]
    if code_blocks:
        return code_blocks[0]

    raw = text.strip()
    if raw:
        return raw

    raise RuntimeError("Model returned an empty draft")


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
    premise_context: str = "",
    retrieval_index_path: str = "",
    retrieval_top_k: int = 12,
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

    retrieved_context = ""
    if retrieval_index_path:
        retrieved_context = retrieve_premise_context(
            lean_state=lean_state,
            retrieval_index_path=retrieval_index_path,
            top_k=retrieval_top_k,
            use_tier_preference=True,
        )

    effective_premise_context = premise_context.strip()
    if retrieved_context:
        if effective_premise_context:
            effective_premise_context = (
                f"{effective_premise_context}\n"
                "- - -\n"
                f"{retrieved_context}"
            )
        else:
            effective_premise_context = retrieved_context

    # Phase 3: Analyze goal structure for better reasoning
    goal_structure_hint = ""
    state_lower = lean_state.lower()
    if "⊢" in lean_state or "goal" in state_lower:
        # Extract the target from turnstile
        if "⊢" in lean_state:
            target = lean_state.split("⊢")[-1].strip()
        else:
            target = lean_state
        
        # Identify goal type
        if "=" in target or "==" in target:
            goal_structure_hint = "[Goal type: EQUALITY]"
        elif "∀" in target or "forall" in state_lower:
            goal_structure_hint = "[Goal type: UNIVERSAL_QUANTIFIER]"
        elif "∃" in target or "exists" in state_lower:
            goal_structure_hint = "[Goal type: EXISTENTIAL]"
        elif any(kw in target for kw in ["<", ">", "≤", "≥", "≠"]):
            goal_structure_hint = "[Goal type: INEQUALITY_OR_COMPARISON]"
        elif "true" in target.lower() or target.strip() == "":
            goal_structure_hint = "[Goal type: TRIVIAL_OR_TRUE]"
        else:
            goal_structure_hint = "[Goal type: GENERAL_PROPOSITION]"
    
    # Format proof state with explicit sections and goal hints
    formatted_state = (
        "Current Lean 4 proof state:\n"
        f"{goal_structure_hint}\n"
        "=" * 50 + "\n"
        f"{lean_state}\n"
        "=" * 50
    )
    
    system_content = build_system_prompt(premise_context=effective_premise_context)
    if effective_premise_context:
        system_content = (
            f"{system_content}\n\n"
            "CRITICAL REMINDERS:\n"
            "- Use ONLY the provided Mathlib premises; reference by exact name\n"
            "- Do NOT invent lemma names\n"
            "- Match the goal structure: equality goals → use lemmas about equality\n"
            "- For trivial goals: prefer immediate tactics (rfl, norm_num, decide)\n"
            "- For inequalities: consider linarith, omega, ring_nf tactics"
        )
    
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": system_content,
        },
        {
            "role": "user",
            "content": (
                f"{formatted_state}\n\n"
                "Using the structured reasoning process, generate the next tactic."
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
    premise_context: str = "",
    retrieval_index_path: str = "",
    retrieval_top_k: int = 12,
    api_log_hook: ApiLogHook | None = None,
) -> list[str]:
    """Generate a distinct tactic list for macro-search expansion."""
    if num_options < 1:
        raise ValueError("num_options must be >= 1")

    retrieved_context = ""
    if retrieval_index_path:
        retrieved_context = retrieve_premise_context(
            lean_state=lean_state,
            retrieval_index_path=retrieval_index_path,
            top_k=retrieval_top_k,
            use_tier_preference=True,
        )

    effective_premise_context = premise_context.strip()
    if retrieved_context:
        if effective_premise_context:
            effective_premise_context = (
                f"{effective_premise_context}\n"
                "- - -\n"
                f"{retrieved_context}"
            )
        else:
            effective_premise_context = retrieved_context

    messages = [
        {
            "role": "system",
            "content": (
                TACTIC_OPTIONS_SYSTEM_PROMPT
                if not effective_premise_context
                else (
                    f"{TACTIC_OPTIONS_SYSTEM_PROMPT}\n\n"
                    "Available Mathlib premises for this proof state:\n"
                    f"{effective_premise_context}"
                )
            ),
        },
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


def _exact_match_premise_lookup(lean_state: str, retrieval_index_path: str) -> str:
    """Return a bullet list of premises whose name exactly ends with any capitalised
    identifier found in the goal.  These are guaranteed-real Mathlib names so the
    model can copy them without hallucinating.

    Example: goal contains `Nat.Prime` → finds `Nat.Prime`, `Nat.Prime.dvd_mul`, etc.
    Only runs when a retrieval index is loaded; returns "" otherwise.
    """
    if not retrieval_index_path:
        return ""
    retriever = _get_retriever(retrieval_index_path)
    if retriever is None:
        return ""

    # Extract camelCase / dotted identifiers that look like Lean names (≥5 chars,
    # contains at least one uppercase letter or a dot).
    TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.']+"   )
    candidates = [
        t for t in TOKEN_RE.findall(lean_state)
        if len(t) >= 5 and (any(c.isupper() for c in t) or "." in t)
    ]
    if not candidates:
        return ""

    # Build a lowercase suffix set for fast matching.
    hits: list[str] = []
    seen: set[str] = set()
    for entry in retriever.entries:
        name_lower = entry.name.lower()
        for cand in candidates:
            cand_lower = cand.lower()
            # Exact suffix match: entry name ends with the candidate token.
            if name_lower == cand_lower or name_lower.endswith("." + cand_lower):
                if entry.name not in seen:
                    seen.add(entry.name)
                    stmt = entry.statement[:80].strip() if entry.statement else ""
                    hits.append(f"- {entry.name}: {stmt}" if stmt else f"- {entry.name}")
                if len(hits) >= 20:
                    break
        if len(hits) >= 20:
            break

    return "\n".join(hits)


def generate_full_proof_draft(
    *,
    lean_state: str,
    client: Mistral,
    model: str,
    informal_proof_hint: str = "",
    temperature: float = 0.2,
    premise_context: str = "",
    retrieval_index_path: str = "",
    retrieval_top_k: int = 12,
    api_log_hook: ApiLogHook | None = None,
) -> str:
    """Generate a full proof draft as a sequence of Lean tactics.

    Contract: return exactly one <draft>...</draft> block containing newline-separated
    tactics executable via run_tac.
    """
    retrieved_context = ""
    if retrieval_index_path:
        retrieved_context = retrieve_premise_context(
            lean_state=lean_state,
            retrieval_index_path=retrieval_index_path,
            top_k=retrieval_top_k,
            use_tier_preference=True,
        )

    effective_premise_context = premise_context.strip()
    if retrieved_context:
        if effective_premise_context:
            effective_premise_context = (
                f"{effective_premise_context}\n"
                "- - -\n"
                f"{retrieved_context}"
            )
        else:
            effective_premise_context = retrieved_context

    system_prompt = (
        "You are Leanstral in full-draft mode. "
        "Output exactly one <draft>...</draft> block containing newline-separated Lean tactics. "
        "Each line must be directly executable with run_tac on the current goal state. "
        "Do not include theorem declarations, comments, markdown, or prose.\n\n"
        "ABSOLUTE RULES:\n"
        "- NEVER write `sorry` or `admit` on any line. A draft containing sorry is rejected.\n"
        "- Only use lemma names that appear in the retrieved premises list. "
        "Do not invent or abbreviate lemma names.\n"
        "- Only use `rewrite [lemma]` if the lemma's LHS pattern is literally present in the goal.\n"
        "- Only use `linarith` or `omega` if the goal is a linear (in)equality over integers or naturals."
    )

    # Inject exact-match lemma hits for any capitalized identifiers in the goal.
    exact_hits = _exact_match_premise_lookup(lean_state, retrieval_index_path)
    if exact_hits:
        if effective_premise_context:
            effective_premise_context = (
                "Exact-match lemmas for identifiers in this goal "
                "(these names are verified to exist in Mathlib):\n"
                f"{exact_hits}\n\n"
                f"{effective_premise_context}"
            )
        else:
            effective_premise_context = (
                "Exact-match lemmas for identifiers in this goal "
                "(these names are verified to exist in Mathlib):\n"
                f"{exact_hits}"
            )

    user_parts = [f"Current Lean 4 proof state:\n{lean_state}"]
    if informal_proof_hint.strip():
        user_parts.append(f"Informal proof hint:\n{informal_proof_hint.strip()}")
    if effective_premise_context:
        user_parts.append(
            "Available Mathlib premises for this proof state:\n"
            f"{effective_premise_context}"
        )
    user_parts.append(
        "Return one executable proof draft now. "
        "Use only lemma names from the premises list above. "
        "Do not write sorry."
    )

    _response, text = _chat_complete(
        client=client,
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ],
        temperature=temperature,
        max_tokens=900,
        purpose="generate_full_proof_draft",
        api_log_hook=api_log_hook,
    )

    return _extract_best_effort_draft(text)


def repair_full_proof_draft(
    *,
    lean_state: str,
    current_draft: str,
    error_feedback: str,
    client: Mistral,
    model: str,
    informal_proof_hint: str = "",
    temperature: float = 0.2,
    premise_context: str = "",
    retrieval_index_path: str = "",
    retrieval_top_k: int = 12,
    api_log_hook: ApiLogHook | None = None,
) -> str:
    """Repair a previous full proof draft using structured Lean error feedback."""
    retrieved_context = ""
    if retrieval_index_path:
        retrieved_context = retrieve_premise_context(
            lean_state=lean_state,
            retrieval_index_path=retrieval_index_path,
            top_k=retrieval_top_k,
            use_tier_preference=True,
        )

    effective_premise_context = premise_context.strip()
    if retrieved_context:
        if effective_premise_context:
            effective_premise_context = (
                f"{effective_premise_context}\n"
                "- - -\n"
                f"{retrieved_context}"
            )
        else:
            effective_premise_context = retrieved_context

    # Detect whether the previous draft's failure was sorry-related.
    _sorry_failure = "sorry" in (error_feedback or "").lower()

    system_prompt = (
        "You are Leanstral in proof-repair mode. "
        "Repair the failing draft and return exactly one <draft>...</draft> block with newline-separated tactics. "
        "Every line must be executable via run_tac. Output no prose.\n\n"
        "ABSOLUTE RULES:\n"
        "- NEVER write `sorry` or `admit`. The previous draft was rejected because it contained sorry.\n"
        if _sorry_failure else
        "You are Leanstral in proof-repair mode. "
        "Repair the failing draft and return exactly one <draft>...</draft> block with newline-separated tactics. "
        "Every line must be executable via run_tac. Output no prose.\n\n"
        "ABSOLUTE RULES:\n"
        "- NEVER write `sorry` or `admit`.\n"
        "- Only use lemma names from the retrieved premises list. Do not invent names.\n"
        "- Only use `rewrite [lemma]` if the lemma's LHS appears literally in the goal.\n"
        "- Only use `linarith`/`omega` if the goal is a linear numeric (in)equality."
    )

    # Inject exact-match hits for the repair prompt too.
    exact_hits = _exact_match_premise_lookup(lean_state, retrieval_index_path)
    if exact_hits:
        if effective_premise_context:
            effective_premise_context = (
                "Exact-match lemmas (verified to exist in Mathlib):\n"
                f"{exact_hits}\n\n"
                f"{effective_premise_context}"
            )
        else:
            effective_premise_context = (
                "Exact-match lemmas (verified to exist in Mathlib):\n"
                f"{exact_hits}"
            )

    user_parts = [
        f"Current Lean 4 proof state:\n{lean_state}",
        f"Current failing draft:\n{current_draft}",
        f"Lean error feedback:\n{error_feedback}",
    ]
    if informal_proof_hint.strip():
        user_parts.append(f"Original informal proof hint:\n{informal_proof_hint.strip()}")
    if effective_premise_context:
        user_parts.append(
            "Available Mathlib premises for this proof state:\n"
            f"{effective_premise_context}"
        )
    user_parts.append(
        "Fix only what is necessary. Use only lemma names from the list above. Do not write sorry."
    )

    _response, text = _chat_complete(
        client=client,
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ],
        temperature=temperature,
        max_tokens=1000,
        purpose="repair_full_proof_draft",
        api_log_hook=api_log_hook,
    )

    return _extract_best_effort_draft(text)


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
    parser.add_argument(
        "--premise-file",
        type=str,
        default="",
        help="Path to .toon knowledge inventory for premise injection",
    )
    parser.add_argument(
        "--premise-namespace",
        type=str,
        default="ProbabilityTheory",
        help="Namespace filter applied when loading premise-file",
    )
    parser.add_argument(
        "--retrieval-index",
        type=str,
        default="",
        help="Path to premise retrieval index JSON (top-k retrieved names/statements).",
    )
    parser.add_argument(
        "--retrieval-top-k",
        type=int,
        default=12,
        help="Number of retrieved premises injected into the prompt.",
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
    premise_context = ""
    if args.premise_file:
        premise_context = load_premise_context(
            args.premise_file,
            namespace_filter=args.premise_namespace,
        )

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
            premise_context=premise_context,
            retrieval_index_path=args.retrieval_index,
            retrieval_top_k=args.retrieval_top_k,
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
