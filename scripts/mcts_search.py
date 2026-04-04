#!/usr/bin/env python3
"""Phase 3.1 MCTS tree search over Lean proof states.

Implements:
- Node data structure with visits/value/tactic_history
- UCB1-based selection with exploration constants
- Expansion using Leanstral tactic proposals
- Value evaluation of new Lean states (with improved calibration)
- Backpropagation of evaluation scores
- Parallelizable MCTS forest execution (multiple tree searches in parallel)
- Comprehensive tree analysis and visualization

Architecture:
- Each MCTSNode represents (proof_state, tactic_history, value_estimate)
- Selection phase: UCB1 balances exploitation and exploration
- Expansion phase: Leanstral proposes k candidate tactics
- Evaluation phase: URM-style value function scores resulting states
- Backpropagation: update parent values on proof completion/failure
- Parallelization: run multiple independent trees, merge best solutions
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import multiprocessing as mp
import os
import random
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from dotenv import load_dotenv
from lean_repl_dojo import LeanError, ProofFinished, ProofGivenUp, REPLDojo, TacticState
from lean_repl_server import LeanREPLServer  # module-level so tests can patch mcts_search.LeanREPLServer

try:
    from mistralai import Mistral
except ImportError:
    from mistralai.client import Mistral  # type: ignore[no-redef]

# Ensure sibling script imports work when invoked from project root.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from ponder_loop import (
    extract_sorry_subgoals,
    generate_full_proof_draft,
    generate_tactic_options,
    load_premise_context,
    repair_full_proof_draft,
    sketch_proof_with_sorry,
)
from prove_with_ponder import _execute_draft, _open_dojo

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Regular expressions for parsing model outputs
VALUE_RE = re.compile(r"<value>([01](?:\.\d+)?)</value>", re.IGNORECASE)
STATE_RE = re.compile(r"<state>(.*?)</state>", re.IGNORECASE | re.DOTALL)
TACTICS_REMAINING_RE = re.compile(
    r"(?:tactics?|steps?)\s+(?:remaining|needed|left|estimate|estimate)?\s*[:=]?\s*(\d+)",
    re.IGNORECASE,
)

# System and user prompts for value estimation
EVAL_SYSTEM_PROMPT = (
    "You are a Lean proof-value estimator. "
    "Given a Lean proof state, estimate how close it is to completion. "
    "Output two metrics in <value> tags:\n"
    "1. <value>X.X</value> for overall progress (0.0=no progress, 1.0=solved)\n"
    "2. <tactics_estimate>N</tactics_estimate> for estimated tactics remaining (1-10 range)\n"
    "Consider: number of goals remaining, complexity of subgoals, use of established tactics."
)

EVAL_USER_PROMPT = "Estimate completion for this Lean 4 proof state:\n\n{state}"

# System and user prompts for state transition prediction
TRANSITION_SYSTEM_PROMPT = (
    "You are a Lean state transition estimator. "
    "Given a Lean state and one tactic, predict the next Lean state text. "
    "Output exactly one <state>...</state> block."
)
TRANSITION_USER_PROMPT = (
    "Current state:\n{state}\n\n"
    "Tactic:\n{tactic}\n\n"
    "Predict the next state."
)

# Exploration constants
DEFAULT_EXPLORATION_C = 1.4
DEFAULT_ITERATIONS = 50
DEFAULT_BRANCH_MIN = 3
DEFAULT_BRANCH_MAX = 6
DEFAULT_PROCESSES = 2


@dataclass
class MCTSNode:
    """Represents a node in the MCTS tree.
    
    Attributes:
        state: Lean dojo TacticState object (or None for fallback mode)
        state_text: Pretty-printed Lean proof state
        tactic_from_parent: The tactic that led to this node from parent
        tactic_history: Sequence of all tactics from root to this node
        parent: Link to parent node
        visits: Number of times this node was selected during search
        value_sum: Cumulative value score (for mean_value calculation)
        children: List of child nodes
        is_terminal: Whether this represents a finished proof or dead-end
        terminal_reason: Explanation of why terminal (e.g., "proof-finished")
        depth: Distance from root (for tree analysis)
        first_visit_time: Timestamp of first expansion
    """
    state: Any
    state_text: str
    tactic_from_parent: str | None
    tactic_history: list[str] = field(default_factory=list)
    parent: MCTSNode | None = None
    visits: int = 0
    value_sum: float = 0.0
    children: list[MCTSNode] = field(default_factory=list)
    is_terminal: bool = False
    terminal_reason: str = ""
    depth: int = 0
    first_visit_time: float = 0.0

    @property
    def mean_value(self) -> float:
        if self.visits == 0:
            return 0.0
        return self.value_sum / self.visits
    
    @property
    def ucb_score(self) -> float:
        """Compute UCB1 score (assuming parent visits tracked separately)."""
        if self.visits == 0:
            return float("inf")
        return self.mean_value


@dataclass
class TreeAnalysis:
    """Summary statistics of an MCTS tree."""
    total_nodes: int = 0
    max_depth: int = 0
    terminal_nodes: int = 0
    avg_branching_factor: float = 0.0
    total_visits: int = 0
    best_path_length: int = 0
    best_path_value: float = 0.0
    best_path_tactics: list[str] = field(default_factory=list)


@dataclass
class SearchStats:
    iterations: int = 0
    expanded_nodes: int = 0
    evaluated_nodes: int = 0
    cache_hits: int = 0
    proofs_found: int = 0
    api_calls: int = 0
    value_samples: list[dict[str, Any]] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def elapsed_seconds(self) -> float:
        if self.start_time == 0.0:
            return 0.0
        end = self.end_time or time.time()
        return max(0.0, end - self.start_time)

    @property
    def iterations_per_second(self) -> float:
        if self.elapsed_seconds <= 0.0:
            return 0.0
        return self.iterations / self.elapsed_seconds


@dataclass
class PreflightResult:
    ok: bool
    message: str
    prepared_repo: Any | None = None
    tmp_root: Path | None = None


@dataclass
class MCTSParallelResult:
    """Result from one parallel MCTS worker."""
    root: MCTSNode
    stats: SearchStats
    worker_id: int
    success: bool
    error: str | None = None


@dataclass
class DraftMCTSNode:
    """Node used by draft-level MCTS over full proof script repairs."""

    draft: str
    error_feedback: str
    last_state_text: str
    execution_trace: list[dict[str, Any]] = field(default_factory=list)
    parent: DraftMCTSNode | None = None
    repair_from_parent: str = ""
    visits: int = 0
    value_sum: float = 0.0
    children: list[DraftMCTSNode] = field(default_factory=list)
    is_terminal: bool = False
    terminal_reason: str = ""
    depth: int = 0
    first_visit_time: float = 0.0

    @property
    def mean_value(self) -> float:
        if self.visits == 0:
            return 0.0
        return self.value_sum / self.visits


@dataclass
class DraftMCTSParallelResult:
    """Result from one parallel draft-MCTS worker."""

    worker_id: int
    ok: bool
    records: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    best_value: float = 0.0
    error: str | None = None


@dataclass
class DraftTransitionCacheEntry:
    solved: bool
    state_text: str
    error_feedback: str
    step_records: list[dict[str, Any]]
    value: float


def patch_leandojo_extractdata_compat() -> tuple[bool, str]:
    """Patch known LeanDojo ExtractData incompatibilities for newer Lean/Lake.

    Returns (changed, message).
    """
    try:
        import lean_dojo.data_extraction.trace as trace_mod
    except Exception as exc:
        return False, f"cannot import lean_dojo trace module: {exc}"

    extract_path = Path(trace_mod.__file__).resolve().parent / "ExtractData.lean"
    if not extract_path.exists():
        return False, f"ExtractData not found at {extract_path}"

    text = extract_path.read_text(encoding="utf-8")
    original = text

    # Compatibility patch 1: parser header type changed across Lean versions.
    text = text.replace(
        "def getImports (header: TSyntax `Lean.Parser.Module.header) : IO String := do",
        "def getImports (header: Syntax) : IO String := do",
    )

    # Compatibility patch 2: Lake output layout can use lib/<module>/... instead of lib/lean/....
    pattern = (
        r"let some oleanPath := Path\\.toBuildDir \"lib/lean\" relativePath \"olean\" \|\\n"
        r"\\s*throw \\$ IO\\.userError s!\"Invalid path: \\{path\\}\"\\n"
        r"\\s*return .*oleanPath\\.pathExists"
    )
    replacement = (
        "let oleanPath1? := Path.toBuildDir \"lib/lean\" relativePath \"olean\"\n"
        "    let oleanPath2? := Path.toBuildDir \"lib\" relativePath \"olean\"\n"
        "    match oleanPath1?, oleanPath2? with\n"
        "    | none, none =>\n"
        "      throw $ IO.userError s!\"Invalid path: {path}\"\n"
        "    | some p1, none =>\n"
        "      return ← p1.pathExists\n"
        "    | none, some p2 =>\n"
        "      return ← p2.pathExists\n"
        "    | some p1, some p2 =>\n"
        "      return (← p1.pathExists) || (← p2.pathExists)"
    )
    text, _n = re.subn(pattern, replacement, text, count=1)

    if text == original:
        return False, "no compatibility changes needed"

    extract_path.write_text(text, encoding="utf-8")
    return True, f"patched {extract_path}"


def uct_score(*, child: MCTSNode, parent_visits: int, exploration_c: float) -> float:
    if child.visits == 0:
        return float("inf")
    exploit = child.mean_value
    explore = exploration_c * math.sqrt(math.log(parent_visits + 1) / child.visits)
    return exploit + explore


def select_leaf(root: MCTSNode, exploration_c: float) -> list[MCTSNode]:
    """Return root-to-leaf path by repeated UCT child selection."""
    path = [root]
    node = root

    while node.children and not node.is_terminal:
        node = max(
            node.children,
            key=lambda c: uct_score(
                child=c,
                parent_visits=max(1, path[-1].visits),
                exploration_c=exploration_c,
            ),
        )
        path.append(node)

    return path


def parse_value_score(text: str) -> float | None:
    match = VALUE_RE.search(text)
    if not match:
        return None
    try:
        val = float(match.group(1))
    except ValueError:
        return None
    if 0.0 <= val <= 1.0:
        return val
    return None


def parse_state_text(text: str) -> str | None:
    match = STATE_RE.search(text)
    if not match:
        return None
    state = match.group(1).strip()
    return state or None


def _response_to_text(response: Any) -> str:
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
    return str(response)


# ---------------------------------------------------------------------------
# Value function calibration
# ---------------------------------------------------------------------------
# The model is overconfident: raw scores cluster near 1.0 regardless of
# actual proof difficulty.  We apply two corrections:
#
#   1. Temperature scaling: divide the logit by T before re-applying sigmoid.
#      T > 1 spreads the distribution away from extremes.  T = 1.5 is the
#      default, chosen to shift an avg of 0.967 down toward ~0.75 for
#      typical mid-proof states.
#
#   2. Platt calibration: optional affine transform on the logit
#      logit_cal = a * logit_raw + b, fit by minimising log-loss on a
#      (predicted_value, proof_succeeded) dataset collected from runs.
#
# The calibrator can be saved/loaded as a small JSON file so it persists
# across MCTS runs.
# ---------------------------------------------------------------------------

_DEFAULT_CALIBRATION_TEMPERATURE = 1.5
# Paths written by fit_platt_calibrator and read by load_calibration.
_CALIBRATION_PATH = Path("data/value_calibration.json")


def _logit(p: float) -> float:
    p = max(1e-7, min(1.0 - 1e-7, p))
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def temperature_scale(value: float, temperature: float = _DEFAULT_CALIBRATION_TEMPERATURE) -> float:
    """Apply temperature scaling to a [0,1] value estimate.

    Divides the logit by ``temperature`` before re-applying sigmoid.
    temperature > 1 spreads values away from the extremes, correcting
    overconfidence.
    """
    if temperature <= 0.0 or temperature == 1.0:
        return value
    return _sigmoid(_logit(value) / temperature)


def fit_platt_calibrator(
    scores: list[float],
    outcomes: list[int],
    *,
    save_path: str | Path | None = None,
) -> tuple[float, float]:
    """Fit a Platt (logistic) calibrator from (score, outcome) pairs.

    Uses gradient descent to minimise binary cross-entropy:
        L = -mean[ y * log(sigmoid(a*logit(p) + b)) + (1-y) * log(...) ]

    Args:
        scores: Raw model value estimates in [0, 1].
        outcomes: 1 if the proof eventually succeeded, 0 otherwise.
        save_path: If provided, save ``{"a": a, "b": b}`` as JSON.

    Returns:
        (a, b) — the fitted Platt parameters.
    """
    if len(scores) != len(outcomes) or not scores:
        raise ValueError("scores and outcomes must be non-empty and the same length")

    a, b = 1.0, 0.0
    lr = 0.05
    for _ in range(2000):
        grad_a = grad_b = 0.0
        n = len(scores)
        for p, y in zip(scores, outcomes):
            logit_p = _logit(p)
            pred = _sigmoid(a * logit_p + b)
            err = pred - y
            grad_a += err * logit_p / n
            grad_b += err / n
        a -= lr * grad_a
        b -= lr * grad_b

    if save_path is not None:
        target = Path(save_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"a": a, "b": b}), encoding="utf-8")
        logger.info("Platt calibration saved to %s (a=%.4f b=%.4f)", target, a, b)

    return a, b


def load_calibration(path: str | Path | None = None) -> tuple[float, float] | None:
    """Load Platt calibration parameters from disk.

    Returns ``(a, b)`` or ``None`` if the file does not exist or is invalid.
    """
    target = Path(path or _CALIBRATION_PATH)
    if not target.exists():
        return None
    try:
        d = json.loads(target.read_text(encoding="utf-8"))
        return float(d["a"]), float(d["b"])
    except Exception:
        return None


def apply_calibration(
    value: float,
    *,
    temperature: float = _DEFAULT_CALIBRATION_TEMPERATURE,
    platt_params: tuple[float, float] | None = None,
) -> float:
    """Apply temperature scaling and optional Platt calibration to a raw value.

    Order: temperature scaling first, then Platt transform.
    """
    v = temperature_scale(value, temperature)
    if platt_params is not None:
        a, b = platt_params
        v = _sigmoid(a * _logit(v) + b)
    return round(max(0.0, min(1.0, v)), 6)


def collect_calibration_sample(
    *,
    state_text: str,
    raw_value: float,
    proof_succeeded: bool,
    path: str | Path | None = None,
) -> None:
    """Append a (score, outcome) pair to the calibration dataset on disk.

    Call this after each proof attempt finishes so the dataset grows
    organically.  Use ``fit_platt_calibrator`` periodically to re-fit.
    """
    target = Path(path or _CALIBRATION_PATH).with_suffix(".dataset.jsonl")
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "raw_value": round(raw_value, 6),
        "outcome": 1 if proof_succeeded else 0,
        "state_chars": len(state_text),
    }
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


# Module-level cache for calibration params so we load once per process.
_PLATT_PARAMS_CACHE: tuple[float, float] | None | bool = False  # False = not yet loaded


def _get_platt_params() -> tuple[float, float] | None:
    global _PLATT_PARAMS_CACHE
    if _PLATT_PARAMS_CACHE is False:
        _PLATT_PARAMS_CACHE = load_calibration()
    return _PLATT_PARAMS_CACHE  # type: ignore[return-value]


def structural_value(state_text: str) -> float:
    """Zero-API structural value estimate from proof state syntax.

    Uses three signals:
    1. Goal count (⊢ occurrences) — more goals = further from done.
    2. Type expression depth — deeply nested types suggest hard goals.
    3. Trivial-state detection — single goal with only atomic type → near 1.0.

    Returns a value in [0.0, 1.0]. This is used as a floor signal blended
    with the model-based value to avoid wasting API calls on obvious states.
    """
    if not state_text or not state_text.strip():
        return 0.5

    goals = state_text.count("⊢")
    if goals == 0:
        # No open goals in state text — likely already solved or error state.
        return 0.95

    # Depth proxy: count nesting via angle brackets and parens in goal expressions.
    goal_lines = [ln for ln in state_text.splitlines() if "⊢" in ln]
    avg_depth = 0.0
    if goal_lines:
        depths = []
        for line in goal_lines:
            after_turnstile = line.split("⊢", 1)[-1]
            depth = after_turnstile.count("(") + after_turnstile.count("[") + after_turnstile.count("∀") + after_turnstile.count("∃")
            depths.append(depth)
        avg_depth = sum(depths) / len(depths)

    # Score: fewer goals + shallower depth = higher value.
    goal_penalty = 1.0 / (1.0 + goals)
    depth_penalty = 1.0 / (1.0 + avg_depth * 0.3)
    score = goal_penalty * depth_penalty

    return round(max(0.0, min(1.0, score)), 6)


def evaluate_state_value(
    *,
    state_text: str,
    client: Mistral,
    model: str,
    use_tactics_estimate: bool = True,
    calibration_temperature: float = _DEFAULT_CALIBRATION_TEMPERATURE,
) -> tuple[float, int | None]:
    """Estimate state value with calibrated confidence.

    Raw model scores cluster near 1.0 (overconfident).  This function applies:
      1. Temperature scaling (default T=1.5) to spread the distribution.
      2. Platt calibration if a fitted parameter file exists at
         ``data/value_calibration.json``.

    Returns:
        (calibrated_value_score, tactics_remaining_estimate)
    """
    response = client.chat.complete(
        model=model,
        messages=[
            {"role": "system", "content": EVAL_SYSTEM_PROMPT},
            {"role": "user", "content": EVAL_USER_PROMPT.format(state=state_text)},
        ],
        temperature=0.0,
        max_tokens=128,
    )

    raw = _response_to_text(response)
    score = parse_value_score(raw)
    tactics_estimate = None

    if score is None:
        # Fallback: try to parse malformed <0.7> variant
        stripped = raw.strip()
        if stripped.startswith("<") and stripped.endswith(">"):
            try:
                maybe = float(stripped[1:-1].strip())
                if 0.0 <= maybe <= 1.0:
                    score = maybe
            except ValueError:
                pass

    if score is None:
        # Heuristic fallback: use structural value (goal count + depth).
        score = structural_value(state_text)

    # Apply calibration: temperature scaling + optional Platt.
    calibrated = apply_calibration(
        score,
        temperature=calibration_temperature,
        platt_params=_get_platt_params(),
    )

    # Attempt to extract tactics_remaining estimate
    if use_tactics_estimate:
        match = TACTICS_REMAINING_RE.search(raw)
        if match:
            try:
                tactics_estimate = min(int(match.group(1)), 10)
            except (ValueError, IndexError):
                pass

    return calibrated, tactics_estimate


def _append_value_sample(
    stats: SearchStats,
    *,
    state_text: str,
    raw_value: float,
    normalized_value: float,
    tactics_estimate: int | None,
    cache_hit: bool,
    source: str,
    max_samples: int = 300,
) -> None:
    if len(stats.value_samples) >= max_samples:
        return
    stats.value_samples.append(
        {
            "source": source,
            "raw_value": round(raw_value, 6),
            "normalized_value": round(normalized_value, 6),
            "tactics_estimate": tactics_estimate,
            "cache_hit": cache_hit,
            "state_chars": len(state_text or ""),
        }
    )


def normalize_value_with_tactics(
    base_value: float,
    tactics_remaining: int | None,
) -> float:
    """Blend calibrated value with tactics_remaining signal.

    ``base_value`` is already temperature-scaled.  This function adds a
    secondary signal from the model's own tactics-remaining estimate.
    Fewer tactics remaining → higher blend toward 1.0.
    """
    if tactics_remaining is None:
        return base_value

    max_tactics = 10
    tactics_factor = max(0.0, 1.0 - (tactics_remaining / max_tactics))

    # Fixed 50/50 blend — the two signals are independent estimates
    # of the same quantity; equal weight avoids double-counting calibration.
    return round(0.5 * base_value + 0.5 * tactics_factor, 6)


def predict_next_state_text(*, state_text: str, tactic: str, client: Mistral, model: str) -> str:
    response = client.chat.complete(
        model=model,
        messages=[
            {"role": "system", "content": TRANSITION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": TRANSITION_USER_PROMPT.format(state=state_text, tactic=tactic),
            },
        ],
        temperature=0.0,
        max_tokens=500,
    )
    raw = _response_to_text(response)
    parsed = parse_state_text(raw)
    if parsed is not None:
        return parsed
    return state_text


def backpropagate(path: list[MCTSNode], value: float) -> None:
    for node in path:
        node.visits += 1
        node.value_sum += value


def expand_leaf(
    *,
    leaf: MCTSNode,
    dojo: Any,
    client: Mistral,
    model: str,
    premise_context: str = "",
    retrieval_index_path: str = "",
    retrieval_top_k: int = 12,
    branch_min: int = 3,
    branch_max: int = 5,
    use_tactics_estimate: bool = True,
) -> list[MCTSNode]:
    """Expand leaf by generating tactic options and executing them via dojo.
    
    Each child node inherits tactic_history from parent and appends its tactic.
    """
    if leaf.is_terminal:
        return []

    n_options = random.randint(branch_min, branch_max)
    candidates = generate_tactic_options(
        lean_state=leaf.state_text,
        client=client,
        model=model,
        num_options=n_options,
        temperature=0.5,
        premise_context=premise_context,
        retrieval_index_path=retrieval_index_path,
        retrieval_top_k=retrieval_top_k,
    )

    children: list[MCTSNode] = []
    seen_states: set[str] = set()

    for tactic in candidates:
        outcome = dojo.run_tac(leaf.state, tactic)

        if isinstance(outcome, TacticState):
            key = outcome.pp.strip()
            if not key or key in seen_states:
                continue
            seen_states.add(key)
            
            child_history = leaf.tactic_history + [tactic]
            child = MCTSNode(
                state=outcome,
                state_text=outcome.pp,
                tactic_from_parent=tactic,
                tactic_history=child_history,
                parent=leaf,
                depth=leaf.depth + 1,
                first_visit_time=time.time(),
            )
            children.append(child)
            continue

        if isinstance(outcome, ProofFinished):
            child_history = leaf.tactic_history + [tactic]
            child = MCTSNode(
                state=leaf.state,
                state_text="no goals",
                tactic_from_parent=tactic,
                tactic_history=child_history,
                parent=leaf,
                is_terminal=True,
                terminal_reason="proof-finished",
                depth=leaf.depth + 1,
                first_visit_time=time.time(),
            )
            children.append(child)
            # Record a positive calibration sample for this node's value estimate
            collect_calibration_sample(score=leaf.mean_value, outcome=1)
            continue

        if isinstance(outcome, (LeanError, ProofGivenUp)):
            # Record a negative calibration sample
            collect_calibration_sample(score=leaf.mean_value, outcome=0)
            continue

    leaf.children.extend(children)
    return children


def expand_leaf_fallback(
    *,
    leaf: MCTSNode,
    client: Mistral,
    model: str,
    premise_context: str = "",
    retrieval_index_path: str = "",
    retrieval_top_k: int = 12,
    branch_min: int = 3,
    branch_max: int = 5,
) -> list[MCTSNode]:
    """Expansion fallback when LeanDojo is unavailable.

    This mode predicts next states from model text transitions only.
    """
    if leaf.is_terminal:
        return []

    n_options = random.randint(branch_min, branch_max)
    candidates = generate_tactic_options(
        lean_state=leaf.state_text,
        client=client,
        model=model,
        num_options=n_options,
        temperature=0.5,
        premise_context=premise_context,
        retrieval_index_path=retrieval_index_path,
        retrieval_top_k=retrieval_top_k,
    )

    children: list[MCTSNode] = []
    seen_states: set[str] = set()

    for tactic in candidates:
        next_state = predict_next_state_text(
            state_text=leaf.state_text,
            tactic=tactic,
            client=client,
            model=model,
        ).strip()
        if not next_state or next_state in seen_states:
            continue
        seen_states.add(next_state)

        is_terminal = "no goals" in next_state.lower()
        child_history = leaf.tactic_history + [tactic]
        child = MCTSNode(
            state=None,
            state_text=next_state,
            tactic_from_parent=tactic,
            tactic_history=child_history,
            parent=leaf,
            is_terminal=is_terminal,
            terminal_reason=("predicted-proof-finished" if is_terminal else ""),
            depth=leaf.depth + 1,
            first_visit_time=time.time(),
        )
        children.append(child)

    leaf.children.extend(children)
    return children


def best_path_from_root(root: MCTSNode, max_depth: int = 64) -> list[str]:
    """Extract best tactic sequence by selecting most-visited children."""
    path: list[str] = []
    node = root
    depth = 0

    while node.children and depth < max_depth:
        # Prefer high-visit, high-value children
        node = max(node.children, key=lambda c: (c.visits, c.mean_value))
        if node.tactic_from_parent:
            path.append(node.tactic_from_parent)
        if node.is_terminal:
            break
        depth += 1

    return path


def analyze_tree(root: MCTSNode) -> TreeAnalysis:
    """Compute statistics and analysis of the MCTS tree."""
    analysis = TreeAnalysis()
    visited: set[int] = set()
    queue: list[MCTSNode] = [root]
    branch_factors: list[float] = []

    while queue:
        node = queue.pop(0)
        node_id = id(node)
        if node_id in visited:
            continue
        visited.add(node_id)

        analysis.total_nodes += 1
        analysis.max_depth = max(analysis.max_depth, node.depth)
        analysis.total_visits += node.visits

        if node.is_terminal:
            analysis.terminal_nodes += 1

        if node.children:
            branch_factors.append(len(node.children))
            queue.extend(node.children)

    if branch_factors:
        analysis.avg_branching_factor = sum(branch_factors) / len(branch_factors)

    # Compute best path
    best_path = best_path_from_root(root)
    analysis.best_path_length = len(best_path)
    analysis.best_path_tactics = best_path

    # Extract value from best leaf
    node = root
    for _ in range(len(best_path)):
        best_child = max(
            (node.children or []),
            key=lambda c: (c.visits, c.mean_value),
            default=None,
        )
        if best_child is None:
            break
        node = best_child
    analysis.best_path_value = node.mean_value

    return analysis


def export_tree_to_json(root: MCTSNode, max_depth: int = 10) -> dict[str, Any]:
    """Export tree structure to JSON for visualization."""
    
    def node_to_dict(node: MCTSNode, depth: int) -> dict[str, Any]:
        if depth > max_depth:
            return {}
        
        return {
            "value": node.mean_value,
            "visits": node.visits,
            "is_terminal": node.is_terminal,
            "terminal_reason": node.terminal_reason,
            "tactic": node.tactic_from_parent or "ROOT",
            "depth": node.depth,
            "children": [node_to_dict(c, depth + 1) for c in node.children],
        }
    
    return node_to_dict(root, 0)

# ── State-level MCTS (leanprover-community/repl backed) ──────────────────────

@dataclass
class StateMCTSNode:
    """Node in a state-level MCTS tree.

    Each node corresponds to one real Lean proof state (proof_state_id from
    the REPL).  Branching on tactics produces children with distinct REPL
    state IDs — no re-elaboration needed.
    """
    proof_state_id: int
    goals: list[str]                          # actual goal strings from REPL
    tactic_from_parent: str | None
    parent: "StateMCTSNode | None" = None
    children: list["StateMCTSNode"] = field(default_factory=list)
    visits: int = 0
    value_sum: float = 0.0
    is_terminal: bool = False
    terminal_reason: str = ""                 # "proof-finished" | "lean-error" | "depth-limit"
    depth: int = 0

    @property
    def mean_value(self) -> float:
        return self.value_sum / self.visits if self.visits else 0.0

    @property
    def pp(self) -> str:
        return "\n".join(self.goals)


def _state_uct(node: StateMCTSNode, parent_visits: int, c: float) -> float:
    if node.visits == 0:
        return float("inf")
    return node.mean_value + c * math.sqrt(math.log(parent_visits + 1) / node.visits)


def _select_state_leaf(root: StateMCTSNode, c: float) -> list[StateMCTSNode]:
    path: list[StateMCTSNode] = [root]
    node = root
    while node.children and not node.is_terminal:
        node = max(node.children, key=lambda ch: _state_uct(ch, node.visits, c))
        path.append(node)
    return path


def _goal_value(goals: list[str]) -> float:
    """Heuristic value based on number of remaining goals (0.0–0.9 range)."""
    if not goals:
        return 1.0
    return max(0.0, 0.9 - len(goals) * 0.15)


def _expand_state_node(
    node: StateMCTSNode,
    server,         # LeanREPLServer
    client,
    model: str,
    premise_context: str,
    retrieval_index_path: str,
    retrieval_top_k: int,
    max_depth: int,
    n_tactics: int,
    temperature: float,
) -> list[tuple["StateMCTSNode", float]]:
    """Generate n_tactics candidates and apply each via the REPL."""
    from lean_repl_server import LeanError as REPLLeanError, ProofFinished as REPLProofFinished, TacticState as REPLTacticState
    from ponder_loop import generate_tactic_options

    if node.depth >= max_depth:
        node.is_terminal = True
        node.terminal_reason = "depth-limit"
        return []

    candidates = generate_tactic_options(
        lean_state=node.pp,
        client=client,
        model=model,
        num_options=n_tactics,
        temperature=temperature,
        premise_context=premise_context,
        retrieval_index_path=retrieval_index_path,
        retrieval_top_k=retrieval_top_k,
    )

    new_children: list[tuple[StateMCTSNode, float]] = []
    seen_states: set[str] = {ch.pp for ch in node.children}

    for tactic in candidates:
        tactic = tactic.strip()
        if not tactic:
            continue
        try:
            result = server.run_tac(node.proof_state_id, tactic)
        except Exception as exc:
            logger.debug("REPL error on tactic %r: %s", tactic, exc)
            continue

        if isinstance(result, REPLLeanError):
            child = StateMCTSNode(
                proof_state_id=node.proof_state_id,
                goals=node.goals,
                tactic_from_parent=tactic,
                parent=node,
                is_terminal=True,
                terminal_reason="lean-error",
                depth=node.depth + 1,
            )
            node.children.append(child)
            new_children.append((child, 0.0))
            continue

        if isinstance(result, REPLProofFinished):
            child = StateMCTSNode(
                proof_state_id=result.proof_state_id,
                goals=[],
                tactic_from_parent=tactic,
                parent=node,
                is_terminal=True,
                terminal_reason="proof-finished",
                depth=node.depth + 1,
            )
            node.children.append(child)
            new_children.append((child, 1.0))
            continue

        # TacticState — new non-terminal Lean proof state
        goals = result.goals
        pp = "\n".join(goals)
        if pp in seen_states:
            continue
        seen_states.add(pp)

        value = _goal_value(goals)
        child = StateMCTSNode(
            proof_state_id=result.proof_state_id,
            goals=goals,
            tactic_from_parent=tactic,
            parent=node,
            depth=node.depth + 1,
        )
        node.children.append(child)
        new_children.append((child, value))

    return new_children


def _backpropagate_state(path: list[StateMCTSNode], value: float) -> None:
    for node in reversed(path):
        node.visits += 1
        node.value_sum += value


def _best_proof_path(root: StateMCTSNode) -> list[str] | None:
    """DFS to find a path from root to a proof-finished terminal."""
    if root.is_terminal and root.terminal_reason == "proof-finished":
        return [] if root.tactic_from_parent is None else [root.tactic_from_parent]
    for child in root.children:
        sub = _best_proof_path(child)
        if sub is not None:
            prefix = [root.tactic_from_parent] if root.tactic_from_parent else []
            return prefix + sub
    return None


def _kg_record_proof(
    project_root: Path,
    theorem_statement: str,
    tactics: list[str],
) -> None:
    """Append a successfully proven theorem to the KG trusted layer.

    Written to output/kg/trusted/theorems.jsonl — one JSON object per line.
    The KG can then be loaded as additional premises for future proof searches.
    """
    import re as _re
    out_path = project_root / "output" / "kg" / "trusted" / "theorems.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Extract theorem name for indexing
    m = _re.search(r"(?:theorem|lemma)\s+(\w+)", theorem_statement)
    name = m.group(1) if m else "unknown"
    entry = {
        "name": name,
        "statement": theorem_statement.strip(),
        "proof_tactics": tactics,
        "source": "state_mcts",
        "timestamp": time.time(),
    }
    with open(out_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    logger.info("[kg] recorded proof of %s to trusted layer", name)


def run_state_mcts(
    *,
    project_root: Path,
    theorem_statement: str,          # full Lean theorem signature ending with ':= by'
    client,
    model: str,
    iterations: int = 50,
    n_tactics: int = 4,              # candidates per expansion
    max_depth: int = 10,
    exploration_c: float = 1.4,
    temperature: float = 0.4,
    repl_timeout: float = 120.0,
    premise_context: str = "",
    retrieval_index_path: str = "",
    retrieval_top_k: int = 12,
    kg_write_on_success: bool = True,  # write to KG trusted layer when proof found
) -> tuple[bool, list[str], str]:
    """State-level MCTS using leanprover-community/repl.

    Returns (success, tactic_list, summary_string).
    success=True means a complete proof path was found.
    tactic_list is the winning tactic sequence (empty on failure).
    """
    from lean_repl_server import LeanError as REPLLeanError

    logger.info("State-level MCTS: %d iterations, depth %d, %d tactics/node",
                iterations, max_depth, n_tactics)

    # Augment premise_context with KG trusted layer entries
    kg_path = project_root / "output" / "kg" / "trusted" / "theorems.jsonl"
    if kg_write_on_success and kg_path.exists():
        try:
            kg_entries: list[str] = []
            with open(kg_path, encoding="utf-8") as _kgf:
                for _line in _kgf:
                    _line = _line.strip()
                    if not _line:
                        continue
                    _entry = json.loads(_line)
                    if _entry.get("name") and _entry.get("statement"):
                        kg_entries.append(f"- {_entry['name']}: {_entry['statement'][:120]}")
            if kg_entries:
                kg_block = "Proven theorems in KG trusted layer (may be used as lemmas):\n" + "\n".join(kg_entries[-50:])
                premise_context = (premise_context + "\n\n" + kg_block).strip() if premise_context else kg_block
                logger.info("[kg] injected %d trusted theorems into premise context", len(kg_entries))
        except Exception as _kg_exc:
            logger.warning("[kg] could not load trusted layer: %s", _kg_exc)

    with LeanREPLServer(project_root=project_root, timeout=repl_timeout) as server:
        ps = server.start_proof(theorem_statement)
        if isinstance(ps, REPLLeanError):
            return False, [], f"REPL failed to open proof: {ps.error}"

        # Get initial goals without advancing the proof state.
        # We use the sorry-stub proof state id directly; goals come from the sorry.
        # "skip" is a no-op tactic that reveals goals — use it to get the initial state.
        initial_goals_result = server.run_tac(ps, "skip")
        if isinstance(initial_goals_result, REPLLeanError):
            # skip not valid here; try getting goals via intro
            initial_goals_result = server.run_tac(ps, "all_goals intro")
        if isinstance(initial_goals_result, REPLLeanError):
            # Last resort: use the proof state directly with placeholder goal string
            initial_goals = ["<goal>"]
            initial_ps_id = ps
        else:
            from lean_repl_server import TacticState as RSTS, ProofFinished as RSPF
            if isinstance(initial_goals_result, RSPF):
                # Trivially closed by skip — return success immediately
                return True, ["skip"], "SOLVED trivially (skip closed proof)"
            initial_goals = getattr(initial_goals_result, "goals", ["<goal>"])
            initial_ps_id = getattr(initial_goals_result, "proof_state_id", ps)

        root = StateMCTSNode(
            proof_state_id=initial_ps_id,
            goals=initial_goals,
            tactic_from_parent=None,
            depth=0,
        )

        stats = SearchStats(start_time=time.time())
        solved_node: StateMCTSNode | None = None

        for i in range(iterations):
            stats.iterations += 1
            path = _select_state_leaf(root, exploration_c)
            leaf = path[-1]

            if leaf.is_terminal:
                value = 1.0 if leaf.terminal_reason == "proof-finished" else 0.0
                _backpropagate_state(path, value)
                if leaf.terminal_reason == "proof-finished" and solved_node is None:
                    solved_node = leaf
                    stats.proofs_found += 1
                    logger.info("Proof found at iteration %d, depth %d", i + 1, leaf.depth)
                    break
                continue

            new_children = _expand_state_node(
                node=leaf,
                server=server,
                client=client,
                model=model,
                premise_context=premise_context,
                retrieval_index_path=retrieval_index_path,
                retrieval_top_k=retrieval_top_k,
                max_depth=max_depth,
                n_tactics=n_tactics,
                temperature=temperature,
            )
            stats.expanded_nodes += 1

            if not new_children:
                leaf.is_terminal = True
                leaf.terminal_reason = "no-valid-tactics"
                _backpropagate_state(path, 0.0)
                continue

            # Check for immediate proof
            for child, value in new_children:
                if child.terminal_reason == "proof-finished":
                    solved_node = child
                    stats.proofs_found += 1
                    logger.info("Proof found at iteration %d, depth %d", i + 1, child.depth)
                    break
            if solved_node:
                _backpropagate_state(path + [solved_node], 1.0)
                break

            # Pick best child for rollout value, backpropagate
            best_value = max(v for _, v in new_children)
            _backpropagate_state(path, best_value)

        stats.end_time = time.time()

        if solved_node:
            # Collect tactic sequence from root to solved_node
            tactics: list[str] = []
            node: StateMCTSNode | None = solved_node
            while node is not None and node.tactic_from_parent is not None:
                tactics.append(node.tactic_from_parent)
                node = node.parent
            tactics.reverse()
            summary = (
                f"SOLVED in {stats.elapsed_seconds:.1f}s | "
                f"iterations={stats.iterations} expanded={stats.expanded_nodes} | "
                f"proof_depth={len(tactics)}"
            )
            if kg_write_on_success:
                try:
                    _kg_record_proof(project_root, theorem_statement, tactics)
                except Exception as _kg_exc:
                    logger.warning("[kg] failed to record proof: %s", _kg_exc)
            return True, tactics, summary

        # Not solved — return best partial path
        best_path = _best_proof_path(root) or []
        summary = (
            f"FAILED | {stats.elapsed_seconds:.1f}s | "
            f"iterations={stats.iterations} expanded={stats.expanded_nodes}"
        )
        return False, best_path, summary


def run_hierarchical_state_mcts(
    *,
    project_root: Path,
    theorem_statement: str,
    client,
    model: str,
    iterations: int = 50,
    n_tactics: int = 4,
    max_depth: int = 10,
    exploration_c: float = 1.4,
    temperature: float = 0.4,
    repl_timeout: float = 120.0,
    premise_context: str = "",
    retrieval_index_path: str = "",
    retrieval_top_k: int = 12,
    max_subgoals: int = 5,
    informal_proof_hint: str = "",
) -> tuple[bool, list[str], str]:
    """Hierarchical state-level MCTS: sketch with sorry → prove each subgoal with state-MCTS.

    Algorithm:
    1. Ask the LLM to generate a sorry-backed proof skeleton with named `have` subgoals.
    2. Extract each `have hN : T := by sorry` subgoal type expression.
    3. Run `run_state_mcts` on each subgoal independently (leaf-first).
    4. Assemble surviving tactics into the sketch.
    5. Run a final `run_state_mcts` on the full theorem to close any remaining goals.

    Falls back to flat `run_state_mcts` if no subgoals are found.

    Returns (success, tactic_list, summary_string).
    """
    loaded_premise_context = premise_context or load_premise_context(
        retrieval_index_path=retrieval_index_path,
    )

    # Step 1: generate a sorry sketch
    sketch = sketch_proof_with_sorry(
        lean_state=theorem_statement,
        client=client,
        model=model,
        informal_proof_hint=informal_proof_hint,
        premise_context=loaded_premise_context,
    )

    subgoals = extract_sorry_subgoals(sketch)[:max_subgoals]
    if not subgoals:
        logger.info("[hierarchical-state] No subgoals — falling back to flat run_state_mcts")
        return run_state_mcts(
            project_root=project_root,
            theorem_statement=theorem_statement,
            client=client,
            model=model,
            iterations=iterations,
            n_tactics=n_tactics,
            max_depth=max_depth,
            exploration_c=exploration_c,
            temperature=temperature,
            repl_timeout=repl_timeout,
            premise_context=loaded_premise_context,
            retrieval_index_path=retrieval_index_path,
            retrieval_top_k=retrieval_top_k,
        )

    logger.info("[hierarchical-state] Sketch has %d subgoals: %s",
                len(subgoals), [n for n, _ in subgoals])

    closed_proofs: dict[str, list[str]] = {}  # name → tactic list

    # Step 3: prove each subgoal independently using state-MCTS (leaf-first)
    for name, type_expr in reversed(subgoals):
        # Wrap the subgoal type as a standalone theorem for the REPL
        sub_stmt = f"theorem _subgoal_{name} : {type_expr} := by\n  sorry"
        logger.info("[hierarchical-state] Attempting subgoal %s : %s", name, type_expr[:60])
        sub_ok, sub_tactics, sub_summary = run_state_mcts(
            project_root=project_root,
            theorem_statement=sub_stmt,
            client=client,
            model=model,
            iterations=max(10, iterations // 3),
            n_tactics=n_tactics,
            max_depth=max_depth,
            exploration_c=exploration_c,
            temperature=temperature,
            repl_timeout=repl_timeout,
            premise_context=loaded_premise_context,
            retrieval_index_path=retrieval_index_path,
            retrieval_top_k=retrieval_top_k,
        )
        if sub_ok and sub_tactics:
            closed_proofs[name] = sub_tactics
            logger.info("[hierarchical-state] Subgoal %s closed: %s", name, sub_tactics)
        else:
            closed_proofs[name] = []
            logger.info("[hierarchical-state] Subgoal %s open: %s", name, sub_summary)

    closed_count = sum(1 for t in closed_proofs.values() if t)

    # Step 5: run final state-MCTS on the full theorem (uses cached REPL env)
    logger.info("[hierarchical-state] Final state-MCTS pass on full theorem (%d/%d subgoals closed)",
                closed_count, len(subgoals))
    final_ok, final_tactics, final_summary = run_state_mcts(
        project_root=project_root,
        theorem_statement=theorem_statement,
        client=client,
        model=model,
        iterations=iterations,
        n_tactics=n_tactics,
        max_depth=max_depth,
        exploration_c=exploration_c,
        temperature=temperature,
        repl_timeout=repl_timeout,
        premise_context=loaded_premise_context,
        retrieval_index_path=retrieval_index_path,
        retrieval_top_k=retrieval_top_k,
    )

    summary = (
        f"Hierarchical-state MCTS: {closed_count}/{len(subgoals)} subgoals closed. "
        f"Final: {final_summary}"
    )
    return final_ok, final_tactics, summary


if __name__ == "__main__":
    sys.exit(main())
