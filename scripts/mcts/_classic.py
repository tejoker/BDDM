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
try:
    from lean_repl_dojo import LeanError, ProofFinished, ProofGivenUp, REPLDojo, TacticState
    _HAS_REPLDOJO = True
except Exception as exc:
    REPLDojo = None  # type: ignore[assignment]
    LeanError = RuntimeError  # type: ignore[assignment]
    ProofFinished = type("ProofFinished", (), {})  # type: ignore[assignment]
    ProofGivenUp = type("ProofGivenUp", (), {})  # type: ignore[assignment]
    TacticState = type("TacticState", (), {})  # type: ignore[assignment]
    _HAS_REPLDOJO = False
    _REPLDOJO_IMPORT_ERROR = str(exc)
else:
    _REPLDOJO_IMPORT_ERROR = ""
from proof_backend import (
    BackendHealthReport,
    build_backend_startup_summary,
    build_backend_health_report,
    emit_backend_parity_event,
    format_backend_startup_summary,
    load_proof_backend_flags,
    probe_leandojo_importability,
)

try:
    # Keep module-level symbol so tests can patch mcts_search.LeanREPLServer.
    from lean_repl_server import LeanREPLServer as LeanREPLServer  # type: ignore[assignment]
except Exception:
    LeanREPLServer = None  # type: ignore[assignment]

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

# ---------------------------------------------------------------------------
# Tactic policy scorer (H3) — loads sft/rl weights and reranks candidates
# ---------------------------------------------------------------------------

class _TacticPolicyScorer:
    """Bag-of-words logistic policy scorer backed by numpy weight files.

    Loads weights lazily from ``output/research/tactic_policy/`` (rl first,
    then sft).  Falls back to no-op if numpy or weight files are unavailable.
    """

    _WEIGHT_SEARCH_PATHS = [
        Path("output/research/tactic_policy/rl_weights.npy"),
        Path("output/research/tactic_policy/sft_weights.npy"),
    ]

    def __init__(self) -> None:
        self._weights: "Any" = None
        self._dims: int = 0
        self._loaded = False

    def _try_load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            import numpy as np  # type: ignore[import]

            for candidate in self._WEIGHT_SEARCH_PATHS:
                if candidate.exists():
                    w = np.load(candidate)
                    self._weights = w
                    self._dims = int(w.shape[0] - 1)
                    logger.info("Loaded tactic policy weights from %s (dims=%d)", candidate, self._dims)
                    return
        except Exception as exc:
            logger.debug("Tactic policy scorer unavailable: %s", exc)

    @staticmethod
    def _stable_hash_int(text: str, mod: int) -> int:
        import hashlib
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return int(h[:16], 16) % mod

    def score(self, state: str, tactic: str) -> float:
        """Return P(tactic succeeds | state) in [0, 1]; 0.5 when policy unavailable."""
        self._try_load()
        if self._weights is None:
            return 0.5
        try:
            import numpy as np

            vec = np.zeros(self._dims + 1, dtype=np.float64)
            vec[0] = 1.0
            tokens = [t for t in (state + " " + tactic).split() if t]
            for tok in tokens:
                idx = 1 + self._stable_hash_int(tok.lower(), self._dims)
                vec[idx] += 1.0
            norm = float(np.linalg.norm(vec))
            if norm > 0:
                vec /= norm
            logit = float(np.dot(self._weights, vec))
            return 1.0 / (1.0 + math.exp(-logit))
        except Exception:
            return 0.5

    def rerank(self, state: str, tactics: list[str]) -> list[str]:
        """Return tactics sorted by descending policy score (best first)."""
        if self._weights is None:
            self._try_load()
        if self._weights is None:
            return tactics
        scored = [(self.score(state, t), t) for t in tactics]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored]


_TACTIC_POLICY = _TacticPolicyScorer()


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

# P1 mitigation: Cap tactic string length to prevent DoS via massive tactics
MAX_TACTIC_LEN = 10_000  # ~10KB per tactic


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
        # P1 mitigation: Validate tactic length to prevent DoS
        if len(tactic) > MAX_TACTIC_LEN:
            logger.warning(
                "Tactic exceeds max length (%d > %d): %s...",
                len(tactic),
                MAX_TACTIC_LEN,
                tactic[:100],
            )
            continue  # Skip oversized tactic
        
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


def run_mcts(
    *,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    client: Mistral,
    model: str,
    premise_context: str = "",
    retrieval_index_path: str = "",
    retrieval_top_k: int = 12,
    iterations: int = 30,
    exploration_c: float = 1.4,
    branch_min: int = 3,
    branch_max: int = 5,
    dojo_timeout: int = 600,
    use_tactics_estimate: bool = True,
) -> tuple[MCTSNode, SearchStats]:
    """Run MCTS with improved value estimation (Phase 3.1 + 3.2)."""
    if not _HAS_REPLDOJO:
        raise RuntimeError(f"REPLDojo unavailable: {_REPLDOJO_IMPORT_ERROR or 'unknown import error'}")

    rel_file_path = file_path
    if file_path.is_absolute():
        rel_file_path = file_path.relative_to(project_root)

    stats = SearchStats(start_time=time.time())

    with REPLDojo(
        project_root=project_root,
        file_path=rel_file_path,
        theorem_name=theorem_name,
        timeout=dojo_timeout,
    ) as (dojo, initial_state):
        if not isinstance(initial_state, TacticState):
            raise RuntimeError(f"Unexpected initial state type: {type(initial_state).__name__}")

        root = MCTSNode(
            state=initial_state,
            state_text=initial_state.pp,
            tactic_from_parent=None,
            tactic_history=[],
            first_visit_time=time.time(),
        )

        # Cache: state_text -> (raw_value, normalized_value, tactics_estimate).
        _value_cache: dict[str, tuple[float, float, int | None]] = {}

        for _ in range(iterations):
            stats.iterations += 1

            path = select_leaf(root, exploration_c)
            leaf = path[-1]

            eval_node = leaf
            if not leaf.is_terminal:
                new_children = expand_leaf(
                    leaf=leaf,
                    dojo=dojo,
                    client=client,
                    model=model,
                    premise_context=premise_context,
                    retrieval_index_path=retrieval_index_path,
                    retrieval_top_k=retrieval_top_k,
                    branch_min=branch_min,
                    branch_max=branch_max,
                    use_tactics_estimate=use_tactics_estimate,
                )
                if new_children:
                    stats.expanded_nodes += 1
                    eval_node = random.choice(new_children)
                    path = path + [eval_node]

            if eval_node.is_terminal and eval_node.terminal_reason == "proof-finished":
                value = 1.0
                stats.proofs_found += 1
                _append_value_sample(
                    stats,
                    state_text=eval_node.state_text,
                    raw_value=1.0,
                    normalized_value=1.0,
                    tactics_estimate=0,
                    cache_hit=False,
                    source="terminal",
                )
            else:
                cache_key = eval_node.state_text.strip()
                if cache_key in _value_cache:
                    raw_value, value, tactics_est = _value_cache[cache_key]
                    stats.cache_hits += 1
                    _append_value_sample(
                        stats,
                        state_text=eval_node.state_text,
                        raw_value=raw_value,
                        normalized_value=value,
                        tactics_estimate=tactics_est,
                        cache_hit=True,
                        source="run_mcts",
                    )
                else:
                    raw_value, tactics_est = evaluate_state_value(
                        state_text=eval_node.state_text,
                        client=client,
                        model=model,
                        use_tactics_estimate=use_tactics_estimate,
                    )
                    # Apply Phase 3.2 improvement: normalize with tactics_remaining
                    value = normalize_value_with_tactics(raw_value, tactics_est)
                    _value_cache[cache_key] = (raw_value, value, tactics_est)
                    stats.evaluated_nodes += 1
                    stats.api_calls += 1
                    _append_value_sample(
                        stats,
                        state_text=eval_node.state_text,
                        raw_value=raw_value,
                        normalized_value=value,
                        tactics_estimate=tactics_est,
                        cache_hit=False,
                        source="run_mcts",
                    )

            backpropagate(path, value)

        stats.end_time = time.time()
        return root, stats


def run_mcts_fallback(
    *,
    theorem_name: str,
    initial_state_text: str = "",
    client: Mistral,
    model: str,
    premise_context: str = "",
    retrieval_index_path: str = "",
    retrieval_top_k: int = 12,
    iterations: int = 30,
    exploration_c: float = 1.4,
    branch_min: int = 3,
    branch_max: int = 5,
    use_tactics_estimate: bool = True,
) -> tuple[MCTSNode, SearchStats]:
    """Run MCTS in fallback mode (model-only without LeanDojo)."""
    stats = SearchStats(start_time=time.time())

    root = MCTSNode(
        state=None,
        state_text=(
            initial_state_text.strip()
            if initial_state_text.strip()
            else (
                "Fallback root state (unverified by LeanDojo). "
                f"Target theorem: {theorem_name}."
            )
        ),
        tactic_from_parent=None,
        tactic_history=[],
        first_visit_time=time.time(),
    )

    for _ in range(iterations):
        stats.iterations += 1

        path = select_leaf(root, exploration_c)
        leaf = path[-1]

        eval_node = leaf
        if not leaf.is_terminal:
            new_children = expand_leaf_fallback(
                leaf=leaf,
                client=client,
                model=model,
                premise_context=premise_context,
                retrieval_index_path=retrieval_index_path,
                retrieval_top_k=retrieval_top_k,
                branch_min=branch_min,
                branch_max=branch_max,
            )
            if new_children:
                stats.expanded_nodes += 1
                eval_node = random.choice(new_children)
                path = path + [eval_node]

        if eval_node.is_terminal and "finished" in eval_node.terminal_reason:
            value = 1.0
            stats.proofs_found += 1
            _append_value_sample(
                stats,
                state_text=eval_node.state_text,
                raw_value=1.0,
                normalized_value=1.0,
                tactics_estimate=0,
                cache_hit=False,
                source="fallback_terminal",
            )
        else:
            raw_value, tactics_est = evaluate_state_value(
                state_text=eval_node.state_text,
                client=client,
                model=model,
                use_tactics_estimate=use_tactics_estimate,
            )
            value = normalize_value_with_tactics(raw_value, tactics_est)
            stats.evaluated_nodes += 1
            stats.api_calls += 1
            _append_value_sample(
                stats,
                state_text=eval_node.state_text,
                raw_value=raw_value,
                normalized_value=value,
                tactics_estimate=tactics_est,
                cache_hit=False,
                source="run_mcts_fallback",
            )

        backpropagate(path, value)

    stats.end_time = time.time()
    return root, stats


def _draft_uct_score(*, child: DraftMCTSNode, parent_visits: int, exploration_c: float) -> float:
    if child.visits == 0:
        return float("inf")
    exploit = child.mean_value
    explore = exploration_c * math.sqrt(math.log(parent_visits + 1) / child.visits)
    return exploit + explore


def _select_draft_leaf(root: DraftMCTSNode, exploration_c: float) -> list[DraftMCTSNode]:
    path = [root]
    node = root

    while node.children and not node.is_terminal:
        node = max(
            node.children,
            key=lambda c: _draft_uct_score(
                child=c,
                parent_visits=max(1, path[-1].visits),
                exploration_c=exploration_c,
            ),
        )
        path.append(node)

    return path


def _backpropagate_draft(path: list[DraftMCTSNode], value: float) -> None:
    for node in path:
        node.visits += 1
        node.value_sum += value


def _step_records_to_dicts(records: Sequence[Any]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for record in records:
        converted.append(
            {
                "step": getattr(record, "step", 0),
                "attempt": getattr(record, "attempt", 0),
                "tactic": getattr(record, "tactic", ""),
                "model_turns": getattr(record, "model_turns", 0),
                "result": getattr(record, "result", ""),
                "detail": getattr(record, "detail", ""),
            }
        )
    return converted


def _evaluate_draft_result(
    *,
    solved: bool,
    state_text: str,
    client: Mistral,
    model: str,
) -> tuple[float, float, int | None]:
    if solved:
        return 1.0, 1.0, 0

    # Structural value: zero-API signal from goal count + type depth.
    struct_v = structural_value(state_text)

    raw_value, tactics_est = evaluate_state_value(
        state_text=state_text,
        client=client,
        model=model,
        use_tactics_estimate=True,
    )

    # Blend: 0.7 model + 0.3 structural.  Structural acts as a regularizer
    # against the model's systematic overconfidence (raw scores near 1.0).
    blended_raw = round(0.7 * raw_value + 0.3 * struct_v, 6)
    normalized = normalize_value_with_tactics(blended_raw, tactics_est)
    return blended_raw, normalized, tactics_est


def _draft_path(node: DraftMCTSNode) -> list[DraftMCTSNode]:
    path: list[DraftMCTSNode] = []
    current: DraftMCTSNode | None = node
    while current is not None:
        path.append(current)
        current = current.parent
    path.reverse()
    return path


def _expand_draft_node(
    *,
    leaf: DraftMCTSNode,
    dojo: Any,
    initial_state: TacticState,
    client: Mistral,
    model: str,
    repair_variants: int,
    temperature: float,
    premise_context: str,
    retrieval_index_path: str,
    retrieval_top_k: int,
    informal_proof_hint: str,
    max_depth: int,
    transposition_cache: dict[str, DraftTransitionCacheEntry],
    value_cache: dict[str, tuple[float, float, int | None]],
) -> list[tuple[DraftMCTSNode, float]]:
    if leaf.is_terminal or leaf.depth >= max_depth:
        return []

    children: list[tuple[DraftMCTSNode, float]] = []
    seen_drafts: set[str] = set()
    # Progressive widening: expand few repair branches first, then widen as node is revisited.
    dynamic_variants = max(1, min(repair_variants, 1 + int(math.sqrt(max(1, leaf.visits)))))

    for variant_idx in range(dynamic_variants):
        variant_temp = min(1.0, max(0.0, temperature + (0.05 * variant_idx)))
        repaired_draft = repair_full_proof_draft(
            lean_state=initial_state.pp,
            current_draft=leaf.draft,
            error_feedback=leaf.error_feedback,
            client=client,
            model=model,
            informal_proof_hint=informal_proof_hint,
            temperature=variant_temp,
            premise_context=premise_context,
            retrieval_index_path=retrieval_index_path,
            retrieval_top_k=retrieval_top_k,
        )
        normalized = "\n".join(line.rstrip() for line in repaired_draft.splitlines()).strip()
        if not normalized or normalized in seen_drafts:
            continue
        seen_drafts.add(normalized)

        cached_transition = transposition_cache.get(normalized)
        if cached_transition is not None:
            solved = cached_transition.solved
            state_text = cached_transition.state_text
            error_feedback = cached_transition.error_feedback
            step_records_dicts = cached_transition.step_records
            value = cached_transition.value
        else:
            solved, end_state, step_records, error_feedback = _execute_draft(
                dojo=dojo,
                initial_state=initial_state,
                draft=normalized,
                round_idx=leaf.depth + 2,
            )

            state_text = getattr(end_state, "pp", "") or initial_state.pp
            cache_key = state_text.strip()
            value_cache_hit = False
            if solved:
                raw_value, value, tactics_est = 1.0, 1.0, 0
                value = 1.0
            elif cache_key in value_cache:
                value_cache_hit = True
                raw_value, value, tactics_est = value_cache[cache_key]
            else:
                raw_value, value, tactics_est = _evaluate_draft_result(
                    solved=solved,
                    state_text=state_text,
                    client=client,
                    model=model,
                )
                value_cache[cache_key] = (raw_value, value, tactics_est)

            step_records_dicts = _step_records_to_dicts(step_records)
            # Persist URM calibration signal in trace for later analysis.
            step_records_dicts.append(
                {
                    "step": leaf.depth + 2,
                    "attempt": 0,
                    "tactic": "__value_estimate__",
                    "model_turns": 1,
                    "result": "value-estimate",
                    "detail": json.dumps(
                        {
                            "raw_value": round(raw_value, 6),
                            "normalized_value": round(value, 6),
                            "tactics_estimate": tactics_est,
                            "cache_hit": value_cache_hit,
                        },
                        ensure_ascii=True,
                    ),
                }
            )
            transposition_cache[normalized] = DraftTransitionCacheEntry(
                solved=solved,
                state_text=state_text,
                error_feedback=error_feedback,
                step_records=step_records_dicts,
                value=value,
            )

        # Skip children that make no measurable progress (same failure feedback).
        if (not solved) and error_feedback.strip() == leaf.error_feedback.strip():
            continue

        child = DraftMCTSNode(
            draft=normalized,
            error_feedback=error_feedback,
            last_state_text=state_text,
            execution_trace=step_records_dicts,
            parent=leaf,
            repair_from_parent=f"repair_variant_{variant_idx + 1}",
            is_terminal=solved or (leaf.depth + 1 >= max_depth),
            terminal_reason=(
                "proof-finished"
                if solved
                else ("max-depth" if leaf.depth + 1 >= max_depth else "")
            ),
            depth=leaf.depth + 1,
            first_visit_time=time.time(),
        )
        children.append((child, value))

    leaf.children.extend([child for child, _ in children])
    return children


def run_draft_mcts(
    *,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    client: Mistral,
    model: str,
    iterations: int = 12,
    repair_variants: int = 3,
    max_depth: int = 5,
    exploration_c: float = 1.4,
    temperature: float = 0.2,
    dojo_timeout: int = 600,
    premise_context: str = "",
    retrieval_index_path: str = "",
    retrieval_top_k: int = 12,
    informal_proof_hint: str = "",
    warm_start_draft: str = "",
    partial_cache_path: str = "",
) -> tuple[bool, list[dict[str, Any]], str]:
    """Run draft-level MCTS where each node branches on k repaired proof drafts."""
    dojo_ctx, tmp_root = _open_dojo(
        project_root=project_root,
        file_path=file_path,
        theorem_name=theorem_name,
        dojo_timeout=dojo_timeout,
    )

    best_node: DraftMCTSNode | None = None
    solved_node: DraftMCTSNode | None = None

    try:
        with dojo_ctx as (dojo, initial_state):
            if not hasattr(initial_state, "pp"):
                return False, [], f"Unexpected initial state type: {type(initial_state).__name__}"

            transposition_cache: dict[str, DraftTransitionCacheEntry] = {}
            value_cache: dict[str, tuple[float, float, int | None]] = {}

            if warm_start_draft:
                initial_draft = warm_start_draft
                logger.info("[warm-start] using cached partial proof (%d chars)", len(initial_draft))
            else:
                initial_draft = generate_full_proof_draft(
                    lean_state=initial_state.pp,
                    client=client,
                    model=model,
                    informal_proof_hint=informal_proof_hint,
                    temperature=temperature,
                    premise_context=premise_context,
                    retrieval_index_path=retrieval_index_path,
                    retrieval_top_k=retrieval_top_k,
                )
            solved, end_state, step_records, error_feedback = _execute_draft(
                dojo=dojo,
                initial_state=initial_state,
                draft=initial_draft,
                round_idx=1,
            )
            root_state_text = getattr(end_state, "pp", "") or initial_state.pp
            root_cache_hit = False
            if solved:
                root_raw_value, root_value, root_tactics_est = 1.0, 1.0, 0
            elif root_state_text.strip() in value_cache:
                root_cache_hit = True
                root_raw_value, root_value, root_tactics_est = value_cache[root_state_text.strip()]
            else:
                root_raw_value, root_value, root_tactics_est = _evaluate_draft_result(
                    solved=solved,
                    state_text=root_state_text,
                    client=client,
                    model=model,
                )
                value_cache[root_state_text.strip()] = (root_raw_value, root_value, root_tactics_est)

            initial_trace = _step_records_to_dicts(step_records)
            initial_trace.append(
                {
                    "step": 1,
                    "attempt": 0,
                    "tactic": "__value_estimate__",
                    "model_turns": 1,
                    "result": "value-estimate",
                    "detail": json.dumps(
                        {
                            "raw_value": round(root_raw_value, 6),
                            "normalized_value": round(root_value, 6),
                            "tactics_estimate": root_tactics_est,
                            "cache_hit": root_cache_hit,
                        },
                        ensure_ascii=True,
                    ),
                }
            )

            transposition_cache["\n".join(line.rstrip() for line in initial_draft.splitlines()).strip()] = DraftTransitionCacheEntry(
                solved=solved,
                state_text=root_state_text,
                error_feedback=error_feedback,
                step_records=initial_trace,
                value=root_value,
            )
            root = DraftMCTSNode(
                draft=initial_draft,
                error_feedback=error_feedback,
                last_state_text=root_state_text,
                execution_trace=initial_trace,
                is_terminal=solved,
                terminal_reason=("proof-finished" if solved else ""),
                depth=0,
                first_visit_time=time.time(),
            )
            _backpropagate_draft([root], root_value)
            best_node = root
            if solved:
                solved_node = root

            for _ in range(iterations):
                path = _select_draft_leaf(root, exploration_c)
                leaf = path[-1]

                eval_node = leaf
                eval_value = leaf.mean_value if leaf.visits > 0 else 0.0

                expanded = _expand_draft_node(
                    leaf=leaf,
                    dojo=dojo,
                    initial_state=initial_state,
                    client=client,
                    model=model,
                    repair_variants=repair_variants,
                    temperature=temperature,
                    premise_context=premise_context,
                    retrieval_index_path=retrieval_index_path,
                    retrieval_top_k=retrieval_top_k,
                    informal_proof_hint=informal_proof_hint,
                    max_depth=max_depth,
                    transposition_cache=transposition_cache,
                    value_cache=value_cache,
                )
                if expanded:
                    eval_node, eval_value = random.choice(expanded)
                    path = path + [eval_node]

                _backpropagate_draft(path, eval_value)

                if best_node is None or eval_node.mean_value >= best_node.mean_value:
                    best_node = eval_node
                if eval_node.terminal_reason == "proof-finished":
                    solved_node = eval_node
                    break

            target = solved_node or best_node or root
            final_path = _draft_path(target)
            combined_records: list[dict[str, Any]] = []
            for node in final_path:
                combined_records.extend(node.execution_trace)

            tree_solved = solved_node is not None
            _collect_proof_trace(root, tree_solved)

            if tree_solved:
                summary = f"Draft MCTS solved proof at depth={solved_node.depth}"
                return True, combined_records, summary

            summary = (
                "Draft MCTS exhausted iterations "
                f"(iterations={iterations}, best_depth={target.depth}, best_value={target.mean_value:.3f})"
            )
            if partial_cache_path and target.draft:
                try:
                    import json as _json
                    Path(partial_cache_path).parent.mkdir(parents=True, exist_ok=True)
                    Path(partial_cache_path).write_text(
                        _json.dumps({"draft": target.draft, "value": target.mean_value}, ensure_ascii=True)
                    )
                    logger.info("[partial-cache] saved best draft (value=%.3f) to %s", target.mean_value, partial_cache_path)
                except Exception as _exc:
                    logger.warning("[partial-cache] save failed: %s", _exc)
            return False, combined_records, summary
    finally:
        if tmp_root is not None:
            import shutil

            shutil.rmtree(tmp_root, ignore_errors=True)


_PROOF_TRACE_PATH = Path("data/value_calibration.proof_traces.jsonl")


def _collect_proof_trace(root: "DraftMCTSNode", tree_solved: bool) -> None:
    """Walk all nodes in the MCTS tree and write outcome-labeled training pairs.

    Each record: {state_text, struct_value, outcome, depth, visits, mean_value}
    outcome=1 if the tree eventually found a proof, 0 otherwise.
    This enables outcome-bootstrapped value regression (Option A).
    """
    target = _PROOF_TRACE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    # BFS over all nodes reachable from root.
    queue = [root]
    seen: set[int] = set()
    records: list[dict[str, Any]] = []
    while queue:
        node = queue.pop()
        nid = id(node)
        if nid in seen:
            continue
        seen.add(nid)
        state = node.last_state_text or ""
        if state.strip():
            records.append({
                "state_text": state[:512],  # truncate to keep file manageable
                "struct_value": structural_value(state),
                "outcome": 1 if tree_solved else 0,
                "depth": node.depth,
                "visits": node.visits,
                "mean_value": round(node.mean_value, 6),
                "terminal_reason": node.terminal_reason,
            })
        queue.extend(node.children)

    if not records:
        return
    with target.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=True) + "\n")


def _extract_draft_best_value(*, ok: bool, summary: str) -> float:
    if ok:
        return 1.0
    match = re.search(r"best_value=([01](?:\.\d+)?)", summary)
    if not match:
        return 0.0
    try:
        value = float(match.group(1))
    except ValueError:
        return 0.0
    return min(1.0, max(0.0, value))


def run_hierarchical_mcts(
    *,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    client: Mistral,
    model: str,
    iterations: int = 15,
    repair_variants: int = 3,
    max_depth: int = 5,
    dojo_timeout: int = 120,
    retrieval_index_path: str = "",
    retrieval_top_k: int = 12,
    informal_proof_hint: str = "",
    premise_context: str = "",
    max_subgoals: int = 5,
) -> tuple[bool, list[dict[str, Any]], str]:
    """Hierarchical MCTS: sketch first, then close each sorry subgoal bottom-up.

    Algorithm:
    1. Generate a sorry-backed proof skeleton with named `have` subgoals.
    2. Extract each `have hN : T := by sorry` subgoal.
    3. Run `run_draft_mcts` independently on each subgoal (leaf-first ordering).
    4. Substitute closed proofs back into the sketch, replacing `sorry`.
    5. Run a final `run_draft_mcts` on the assembled sketch to close the root.

    Returns (solved, combined_records, summary).
    Falls back to flat `run_draft_mcts` if sketch generation fails or yields no subgoals.
    """
    loaded_premise_context = premise_context or load_premise_context(
        retrieval_index_path=retrieval_index_path,
    )

    # Step 1: generate sketch.
    sketch = sketch_proof_with_sorry(
        lean_state=f"-- theorem {theorem_name}",
        client=client,
        model=model,
        informal_proof_hint=informal_proof_hint,
        premise_context=loaded_premise_context,
    )

    subgoals = extract_sorry_subgoals(sketch)[:max_subgoals]
    if not subgoals:
        logger.info("[hierarchical] No subgoals extracted — falling back to flat MCTS")
        return run_draft_mcts(
            project_root=project_root,
            file_path=file_path,
            theorem_name=theorem_name,
            client=client,
            model=model,
            iterations=iterations,
            repair_variants=repair_variants,
            max_depth=max_depth,
            dojo_timeout=dojo_timeout,
            retrieval_index_path=retrieval_index_path,
            retrieval_top_k=retrieval_top_k,
            informal_proof_hint=informal_proof_hint,
        )

    logger.info("[hierarchical] Sketch has %d subgoals: %s", len(subgoals), [n for n, _ in subgoals])

    all_records: list[dict[str, Any]] = []
    closed_proofs: dict[str, str] = {}  # name -> proof text

    # Step 3: close each subgoal independently (leaf-first = reverse order).
    for name, type_expr in reversed(subgoals):
        subgoal_state = f"⊢ {type_expr}"
        logger.info("[hierarchical] Attempting subgoal %s : %s", name, type_expr[:60])
        sub_ok, sub_records, sub_summary = run_draft_mcts(
            project_root=project_root,
            file_path=file_path,
            theorem_name=theorem_name,
            client=client,
            model=model,
            iterations=max(5, iterations // 2),
            repair_variants=repair_variants,
            max_depth=max_depth,
            dojo_timeout=dojo_timeout,
            retrieval_index_path=retrieval_index_path,
            retrieval_top_k=retrieval_top_k,
            informal_proof_hint=f"Prove subgoal for {theorem_name}: {type_expr}",
        )
        all_records.extend(sub_records)
        if sub_ok and sub_records:
            # Extract the proof tactic from the best record.
            best_tactic = next(
                (r.get("tactic", "") for r in reversed(sub_records) if r.get("result") == "ok"),
                "sorry",
            )
            closed_proofs[name] = best_tactic
            logger.info("[hierarchical] Subgoal %s closed: %s", name, best_tactic[:40])
        else:
            closed_proofs[name] = "sorry"  # keep sorry if subgoal unsolvable

    # Step 4: substitute closed proofs back into sketch.
    assembled = sketch
    for name, proof in closed_proofs.items():
        assembled = re.sub(
            rf"(have\s+{re.escape(name)}\s*:[^\n]+:=\s*by\s*)sorry",
            rf"\1{proof}",
            assembled,
        )

    # Step 5: run final MCTS on assembled sketch as starting draft.
    logger.info("[hierarchical] Running final MCTS pass on assembled sketch")
    final_ok, final_records, final_summary = run_draft_mcts(
        project_root=project_root,
        file_path=file_path,
        theorem_name=theorem_name,
        client=client,
        model=model,
        iterations=iterations,
        repair_variants=repair_variants,
        max_depth=max_depth,
        dojo_timeout=dojo_timeout,
        retrieval_index_path=retrieval_index_path,
        retrieval_top_k=retrieval_top_k,
        informal_proof_hint=informal_proof_hint,
    )
    all_records.extend(final_records)

    solved_count = sum(1 for n, p in closed_proofs.items() if p != "sorry")
    summary = (
        f"Hierarchical MCTS: {solved_count}/{len(subgoals)} subgoals closed. "
        f"Final: {final_summary}"
    )
    return final_ok, all_records, summary


def _isolate_project_for_worker(project_root: Path, worker_id: int) -> Path:
    """Copy project tree (excluding .lake/) into a temp dir for process isolation.

    Each parallel worker needs its own copy so concurrent Lake builds don't
    corrupt the shared .lake/ cache.  The caller is responsible for cleanup.
    """
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix=f"desol_worker{worker_id}_"))

    def _ignore(src: str, names: list[str]) -> set[str]:
        return {".lake"} & set(names)

    shutil.copytree(project_root, tmp / "project", ignore=_ignore, symlinks=True)
    return tmp / "project"


def _run_draft_mcts_worker(
    worker_id: int,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    api_key: str,
    model: str,
    iterations: int,
    repair_variants: int,
    max_depth: int,
    exploration_c: float,
    temperature: float,
    dojo_timeout: int,
    premise_context: str,
    retrieval_index_path: str,
    retrieval_top_k: int,
    informal_proof_hint: str,
) -> DraftMCTSParallelResult:
    import shutil as _shutil

    iso_root: Path | None = None
    try:
        random.seed(time.time() + (worker_id * 7919))
        iso_root = _isolate_project_for_worker(project_root, worker_id)
        # Remap file_path into isolated copy if it was inside project_root.
        try:
            rel = file_path.resolve().relative_to(project_root.resolve())
            iso_file = iso_root / rel
        except ValueError:
            iso_file = file_path  # outside project — use as-is
        client = Mistral(api_key=api_key)
        ok, records, summary = run_draft_mcts(
            project_root=iso_root,
            file_path=iso_file,
            theorem_name=theorem_name,
            client=client,
            model=model,
            iterations=iterations,
            repair_variants=repair_variants,
            max_depth=max_depth,
            exploration_c=exploration_c,
            temperature=temperature,
            dojo_timeout=dojo_timeout,
            premise_context=premise_context,
            retrieval_index_path=retrieval_index_path,
            retrieval_top_k=retrieval_top_k,
            informal_proof_hint=informal_proof_hint,
        )
        return DraftMCTSParallelResult(
            worker_id=worker_id,
            ok=ok,
            records=records,
            summary=summary,
            best_value=_extract_draft_best_value(ok=ok, summary=summary),
        )
    except Exception as exc:
        logger.error(f"Draft worker {worker_id} failed: {exc}")
        return DraftMCTSParallelResult(
            worker_id=worker_id,
            ok=False,
            records=[],
            summary="",
            best_value=0.0,
            error=str(exc),
        )
    finally:
        if iso_root is not None:
            try:
                _shutil.rmtree(iso_root.parent, ignore_errors=True)
            except Exception:
                pass


def run_draft_mcts_parallel(
    *,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    api_key: str,
    model: str,
    total_iterations: int = 24,
    num_workers: int = 2,
    repair_variants: int = 3,
    max_depth: int = 5,
    exploration_c: float = 1.4,
    temperature: float = 0.2,
    dojo_timeout: int = 600,
    premise_context: str = "",
    retrieval_index_path: str = "",
    retrieval_top_k: int = 12,
    informal_proof_hint: str = "",
) -> tuple[bool, list[dict[str, Any]], str, list[DraftMCTSParallelResult]]:
    """Run independent draft-MCTS trees in parallel and keep the best result."""
    workers = max(1, min(num_workers, mp.cpu_count()))
    iterations_per_worker = max(1, total_iterations // workers)

    logger.info(
        "Starting parallel draft MCTS: %s workers, %s iterations each",
        workers,
        iterations_per_worker,
    )

    results: list[DraftMCTSParallelResult] = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                _run_draft_mcts_worker,
                worker_id,
                project_root,
                file_path,
                theorem_name,
                api_key,
                model,
                iterations_per_worker,
                repair_variants,
                max_depth,
                exploration_c,
                temperature,
                dojo_timeout,
                premise_context,
                retrieval_index_path,
                retrieval_top_k,
                informal_proof_hint,
            )
            for worker_id in range(workers)
        ]

        for future in as_completed(futures):
            try:
                results.append(future.result(timeout=dojo_timeout * 2))
            except Exception as exc:
                results.append(
                    DraftMCTSParallelResult(
                        worker_id=-1,
                        ok=False,
                        records=[],
                        summary="",
                        best_value=0.0,
                        error=str(exc),
                    )
                )

    successful = [r for r in results if r.error is None]
    solved = [r for r in successful if r.ok]
    if solved:
        best = max(solved, key=lambda r: len(r.records))
        summary = (
            f"Parallel draft MCTS solved proof (workers={workers}, "
            f"iterations_per_worker={iterations_per_worker}, winner={best.worker_id})"
        )
        return True, best.records, summary, results

    if successful:
        best = max(successful, key=lambda r: r.best_value)
        summary = (
            f"Parallel draft MCTS exhausted iterations "
            f"(workers={workers}, iterations_per_worker={iterations_per_worker}, "
            f"winner={best.worker_id}, best_value={best.best_value:.3f})"
        )
        return False, best.records, summary, results

    return False, [], "Parallel draft MCTS failed: no successful workers", results


def run_mcts_worker(
    worker_id: int,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    api_key: str,
    model: str,
    premise_context: str,
    retrieval_index_path: str,
    retrieval_top_k: int,
    iterations: int,
    exploration_c: float,
    branch_min: int,
    branch_max: int,
    dojo_timeout: int,
) -> MCTSParallelResult:
    """Worker function for parallel MCTS search (runs in separate process)."""
    import shutil as _shutil

    iso_root: Path | None = None
    try:
        iso_root = _isolate_project_for_worker(project_root, worker_id)
        try:
            rel = file_path.resolve().relative_to(project_root.resolve())
            iso_file = iso_root / rel
        except ValueError:
            iso_file = file_path
        client = Mistral(api_key=api_key)
        root, stats = run_mcts(
            project_root=iso_root,
            file_path=iso_file,
            theorem_name=theorem_name,
            client=client,
            model=model,
            premise_context=premise_context,
            retrieval_index_path=retrieval_index_path,
            retrieval_top_k=retrieval_top_k,
            iterations=iterations,
            exploration_c=exploration_c,
            branch_min=branch_min,
            branch_max=branch_max,
            dojo_timeout=dojo_timeout,
            use_tactics_estimate=True,
        )
        return MCTSParallelResult(
            root=root,
            stats=stats,
            worker_id=worker_id,
            success=True,
            error=None,
        )
    except Exception as exc:
        logger.error(f"Worker {worker_id} failed: {exc}")
        return MCTSParallelResult(
            root=None,
            stats=SearchStats(),
            worker_id=worker_id,
            success=False,
            error=str(exc),
        )
    finally:
        if iso_root is not None:
            try:
                _shutil.rmtree(iso_root.parent, ignore_errors=True)
            except Exception:
                pass


def run_mcts_parallel(
    *,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    api_key: str,
    model: str,
    premise_context: str = "",
    retrieval_index_path: str = "",
    retrieval_top_k: int = 12,
    total_iterations: int = 100,
    num_processes: int = 2,
    exploration_c: float = 1.4,
    branch_min: int = 3,
    branch_max: int = 5,
    dojo_timeout: int = 600,
) -> tuple[MCTSNode, SearchStats, list[MCTSParallelResult]]:
    """Run multiple MCTS trees in parallel and merge results.
    
    This enables parallelizable macro-search as described in Phase 3.1.
    Each worker runs an independent tree search; the results are merged
    by selecting the best proof found.
    
    Returns:
        (best_root, merged_stats, all_results)
    """
    num_processes = max(1, min(num_processes, mp.cpu_count()))
    iterations_per_process = max(1, total_iterations // num_processes)

    logger.info(
        f"Starting parallel MCTS: {num_processes} processes, "
        f"{iterations_per_process} iterations each"
    )

    results: list[MCTSParallelResult] = []
    merged_stats = SearchStats(start_time=time.time())

    try:
        with ProcessPoolExecutor(max_workers=num_processes) as executor:
            futures = []
            for worker_id in range(num_processes):
                future = executor.submit(
                    run_mcts_worker,
                    worker_id,
                    project_root,
                    file_path,
                    theorem_name,
                    api_key,
                    model,
                    premise_context,
                    retrieval_index_path,
                    retrieval_top_k,
                    iterations_per_process,
                    exploration_c,
                    branch_min,
                    branch_max,
                    dojo_timeout,
                )
                futures.append(future)

            for future in as_completed(futures):
                try:
                    result = future.result(timeout=dojo_timeout * 2)
                    results.append(result)
                    if result.success:
                        logger.info(
                            f"[worker {result.worker_id}] completed: "
                            f"{result.stats.iterations} iterations, "
                            f"{result.stats.proofs_found} proofs found"
                        )
                    else:
                        logger.warning(f"[worker {result.worker_id}] failed: {result.error}")
                except Exception as exc:
                    logger.error(f"Future collection failed: {exc}")

    except Exception as exc:
        logger.error(f"Parallel execution failed: {exc}")

    # Merge statistics
    for result in results:
        if result.success:
            merged_stats.iterations += result.stats.iterations
            merged_stats.expanded_nodes += result.stats.expanded_nodes
            merged_stats.evaluated_nodes += result.stats.evaluated_nodes
            merged_stats.cache_hits += result.stats.cache_hits
            merged_stats.proofs_found += result.stats.proofs_found
            merged_stats.api_calls += result.stats.api_calls
            remaining = max(0, 300 - len(merged_stats.value_samples))
            if remaining > 0 and result.stats.value_samples:
                merged_stats.value_samples.extend(result.stats.value_samples[:remaining])

    # Select best root: prioritize proofs found, then by mean_value
    best_result = None
    if results:
        best_result = max(
            (r for r in results if r.success and r.root is not None),
            key=lambda r: (
                r.stats.proofs_found,
                r.root.mean_value if r.root else 0.0,
            ),
            default=None,
        )

    if best_result is None:
        logger.warning("No successful MCTS runs; creating empty root")
        best_root = MCTSNode(
            state=None,
            state_text=f"Parallel search failed for {theorem_name}",
            tactic_from_parent=None,
        )
    else:
        best_root = best_result.root

    merged_stats.end_time = time.time()
    return best_root, merged_stats, results


def repldojo_preflight(
    *,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    dojo_timeout: int,
) -> PreflightResult:
    """Check whether REPLDojo can open the theorem state."""
    if not _HAS_REPLDOJO:
        return PreflightResult(False, f"REPLDojo unavailable: {_REPLDOJO_IMPORT_ERROR or 'unknown import error'}")

    backend_flags = load_proof_backend_flags()
    emit_backend_parity_event(
        backend_flags,
        "preflight-start",
        {
            "backend": "repldojo",
            "project_root": str(project_root),
            "file_path": str(file_path),
            "theorem": theorem_name,
            "dojo_timeout": dojo_timeout,
        },
    )

    rel_file_path = file_path
    if file_path.is_absolute():
        rel_file_path = file_path.relative_to(project_root)

    try:
        with REPLDojo(
            project_root=project_root,
            file_path=rel_file_path,
            theorem_name=theorem_name,
            timeout=dojo_timeout,
        ) as (_dojo, state):
            if isinstance(state, TacticState):
                emit_backend_parity_event(
                    backend_flags,
                    "preflight-success",
                    {"backend": "repldojo"},
                )
                return PreflightResult(True, "REPLDojo preflight passed")
            emit_backend_parity_event(
                backend_flags,
                "preflight-failure",
                {
                    "backend": "repldojo",
                    "error": f"unexpected initial state type: {type(state).__name__}",
                },
            )
            return PreflightResult(
                False,
                f"REPLDojo returned unexpected initial state type: {type(state).__name__}",
            )
    except Exception as exc:
        emit_backend_parity_event(
            backend_flags,
            "preflight-failure",
            {
                "backend": "repldojo",
                "error": str(exc),
            },
        )
        return PreflightResult(False, f"REPLDojo preflight failed: {exc}")


def check_repldojo_backend_health(
    *,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    dojo_timeout: int,
) -> BackendHealthReport:
    preflight = repldojo_preflight(
        project_root=project_root,
        file_path=file_path,
        theorem_name=theorem_name,
        dojo_timeout=min(dojo_timeout, 180),
    )
    if preflight.ok:
        return build_backend_health_report(backend="repldojo")
    return build_backend_health_report(backend="repldojo", error_text=preflight.message)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 3.1 MCTS tree search for Lean theorem proving",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single-process MCTS search
  python mcts_search.py --file Desol/SDE/Basic.lean --theorem gaussian_process_zero_mean --iterations 50

  # Parallel MCTS search (2 processes, 100 total iterations)
  python mcts_search.py --file Desol/SDE/Basic.lean --theorem gaussian_process_zero_mean --parallel --num-processes 2 --iterations 100

  # With tree analysis and export
  python mcts_search.py --file Desol/SDE/Basic.lean --theorem some_theorem --analyze-tree --export-tree tree.json
        """,
    )
    
    # Core theorem specification
    parser.add_argument("--project-root", default=".", help="DESol project root")
    parser.add_argument("--file", required=True, help="Lean file path relative to project root")
    parser.add_argument("--theorem", required=True, help="Theorem name to prove")

    # MCTS parameters
    parser.add_argument(
        "--iterations",
        type=int,
        default=DEFAULT_ITERATIONS,
        help=f"Total MCTS iterations (default: {DEFAULT_ITERATIONS})",
    )
    parser.add_argument(
        "--exploration-c",
        type=float,
        default=DEFAULT_EXPLORATION_C,
        help=f"UCB1 exploration constant (default: {DEFAULT_EXPLORATION_C})",
    )
    parser.add_argument(
        "--branch-min",
        type=int,
        default=DEFAULT_BRANCH_MIN,
        help=f"Min tactics per expansion (default: {DEFAULT_BRANCH_MIN})",
    )
    parser.add_argument(
        "--branch-max",
        type=int,
        default=DEFAULT_BRANCH_MAX,
        help=f"Max tactics per expansion (default: {DEFAULT_BRANCH_MAX})",
    )

    # Parallelization options
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Enable parallel MCTS mode (multiple independent trees)",
    )
    parser.add_argument(
        "--num-processes",
        type=int,
        default=DEFAULT_PROCESSES,
        help=f"Number of parallel processes (default: {DEFAULT_PROCESSES})",
    )

    # Model and backend setup
    parser.add_argument("--model", default="", help="Mistral model ID")
    parser.add_argument("--dojo-timeout", type=int, default=600, help="REPLDojo timeout in seconds")

    # Premise injection
    parser.add_argument(
        "--premise-file",
        default="",
        help="Path to .toon premise inventory file",
    )
    parser.add_argument(
        "--premise-namespace",
        default="",
        help="Filter premises by namespace",
    )
    parser.add_argument(
        "--retrieval-index",
        default="",
        help="Path to retrieval index JSON for dynamic top-k premise injection",
    )
    parser.add_argument(
        "--retrieval-top-k",
        type=int,
        default=12,
        help="Number of retrieved premises injected per proof state",
    )

    # Preflight and fallback
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip REPLDojo preflight check",
    )
    parser.add_argument(
        "--fallback-mode",
        choices=["none", "model"],
        default="model",
        help="Fallback strategy if REPLDojo backend fails",
    )
    parser.add_argument(
        "--auto-patch-leandojo",
        action="store_true",
        help="Deprecated: patch helper for legacy LeanDojo environments",
    )
    parser.add_argument(
        "--backend-health-check",
        action="store_true",
        help="Run REPLDojo backend health check and exit",
    )

    # Search mode
    parser.add_argument(
        "--search-mode",
        choices=["state", "draft", "fallback"],
        default="state",
        help=(
            "Search mode: "
            "'state' = tactic-level MCTS via leanprover-community/repl (default, recommended); "
            "'draft' = draft-level MCTS via REPLDojo; "
            "'fallback' = model-only, no Lean verification"
        ),
    )
    parser.add_argument(
        "--state-mcts-n-tactics",
        type=int,
        default=4,
        help="Tactic candidates generated per state-MCTS expansion (default: 4)",
    )
    parser.add_argument(
        "--state-mcts-max-depth",
        type=int,
        default=12,
        help="Maximum depth of state-MCTS tree (default: 12)",
    )
    parser.add_argument(
        "--repl-timeout",
        type=float,
        default=30.0,
        help="Timeout in seconds for each REPL tactic call in state-MCTS (default: 30)",
    )
    parser.add_argument(
        "--theorem-statement",
        default="",
        help="Lean theorem statement string for state-MCTS (alternative to --file/--theorem)",
    )

    # Analysis and export options
    parser.add_argument(
        "--analyze-tree",
        action="store_true",
        help="Compute and print tree analysis statistics",
    )
    parser.add_argument(
        "--export-tree",
        default="",
        help="Export tree structure to JSON file for visualization",
    )

    return parser


def main() -> int:
    load_dotenv()
    args = build_parser().parse_args()

    backend_flags = load_proof_backend_flags()
    leandojo_available, leandojo_import_error = probe_leandojo_importability()
    startup = build_backend_startup_summary(
        project_root=Path(args.project_root).resolve(),
        flags=backend_flags,
        leandojo_available=leandojo_available,
        leandojo_import_error=leandojo_import_error,
    )
    if args.backend_health_check or backend_flags.phase1_enabled:
        for line in format_backend_startup_summary(startup):
            print(line)

    if args.backend_health_check:
        report = check_repldojo_backend_health(
            project_root=Path(args.project_root).resolve(),
            file_path=Path(args.file),
            theorem_name=args.theorem,
            dojo_timeout=args.dojo_timeout,
        )
        if report.ok:
            print(f"[ok] {report.message}")
            return 0
        print(f"[fail] code={report.error_code} backend={report.backend} message={report.message}")
        if report.recommendation:
            print(f"[hint] {report.recommendation}")
        return 1

    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        print("[fail] MISTRAL_API_KEY is not set")
        return 1

    model = args.model.strip() or os.getenv("MISTRAL_MODEL", "labs-leanstral-2603").strip()
    if not model:
        print("[fail] no model configured")
        return 1

    if args.branch_min < 1 or args.branch_max < args.branch_min:
        print("[fail] invalid branching range")
        return 1

    premise_context = ""
    if args.premise_file:
        try:
            premise_context = load_premise_context(
                args.premise_file,
                namespace_filter=args.premise_namespace,
            )
            print(f"[ok] loaded premise context from {args.premise_file}")
        except Exception as exc:
            print(f"[warn] premise file load failed: {exc}")

    if args.auto_patch_leandojo:
        changed, patch_msg = patch_leandojo_extractdata_compat()
        status = "[ok]" if changed else "[info]"
        print(f"{status} {patch_msg}")

    # Determine execution mode
    mode = "leandojo"
    _preflight_tmp_root: Path | None = None

    if not args.skip_preflight:
        preflight = repldojo_preflight(
            project_root=Path(args.project_root).resolve(),
            file_path=Path(args.file),
            theorem_name=args.theorem,
            dojo_timeout=min(args.dojo_timeout, 120),
        )
        if preflight.ok:
            print(f"[ok] {preflight.message}")
            _preflight_tmp_root = preflight.tmp_root
        else:
            print(f"[warn] {preflight.message}")
            if args.fallback_mode == "model":
                mode = "model"
                print("[warn] falling back to model-only MCTS (state transitions unverified)")
            else:
                print("[fail] preflight failed and fallback-mode=none")
                return 1

    # Execute MCTS search
    client = Mistral(api_key=api_key)
    search_mode = args.search_mode
    # If preflight failed and we fell back to model-only, honour that regardless of --search-mode.
    if mode == "model":
        search_mode = "fallback"

    try:
        if search_mode == "state":
            # Tactic-level MCTS via leanprover-community/repl — the recommended path.
            theorem_statement = args.theorem_statement.strip()
            if not theorem_statement:
                # Extract statement from file if not provided directly.
                try:
                    theorem_statement = extract_theorem_statement_from_file(
                        project_root=Path(args.project_root).resolve(),
                        file_path=Path(args.file),
                        theorem_name=args.theorem,
                    )
                except Exception as exc:
                    print(f"[warn] could not extract theorem statement: {exc}; falling back to draft-MCTS")
                    search_mode = "draft"

            if search_mode == "state":
                print(f"[info] state-MCTS: iterations={args.iterations} n_tactics={args.state_mcts_n_tactics} max_depth={args.state_mcts_max_depth}")
                proved, tactics, summary = run_state_mcts(
                    project_root=Path(args.project_root).resolve(),
                    theorem_statement=theorem_statement,
                    client=client,
                    model=model,
                    iterations=args.iterations,
                    n_tactics=args.state_mcts_n_tactics,
                    max_depth=args.state_mcts_max_depth,
                    repl_timeout=args.repl_timeout,
                    premise_context=premise_context,
                    retrieval_index_path=args.retrieval_index,
                    retrieval_top_k=args.retrieval_top_k,
                )
                print(f"\n[{'ok' if proved else 'fail'}] state-MCTS: proved={proved}")
                print(f"[info] {summary}")
                if tactics:
                    print("[info] Proof tactic sequence:")
                    for i, t in enumerate(tactics, 1):
                        print(f"  {i}. {t}")
                return 0 if proved else 1

        if search_mode == "draft" or (search_mode == "state" and not args.theorem_statement):
            if args.parallel:
                if mode == "model":
                    print("[warn] model-only fallback: search is unverifiable against Lean kernel")
                    print("[warn] parallel requested but model fallback is single-process; running fallback")
                    root, stats = run_mcts_fallback(
                        theorem_name=args.theorem,
                        client=client,
                        model=model,
                        premise_context=premise_context,
                        retrieval_index_path=args.retrieval_index,
                        retrieval_top_k=args.retrieval_top_k,
                        iterations=args.iterations,
                        exploration_c=args.exploration_c,
                        branch_min=args.branch_min,
                        branch_max=args.branch_max,
                        use_tactics_estimate=True,
                    )
                    parallel_results = []
                else:
                    print(f"[info] draft-MCTS parallel: {args.num_processes} processes, {args.iterations} total iterations")
                    root, stats, parallel_results = run_mcts_parallel(
                        project_root=Path(args.project_root).resolve(),
                        file_path=Path(args.file),
                        theorem_name=args.theorem,
                        api_key=api_key,
                        model=model,
                        premise_context=premise_context,
                        retrieval_index_path=args.retrieval_index,
                        retrieval_top_k=args.retrieval_top_k,
                        total_iterations=args.iterations,
                        num_processes=args.num_processes,
                        exploration_c=args.exploration_c,
                        branch_min=args.branch_min,
                        branch_max=args.branch_max,
                        dojo_timeout=args.dojo_timeout,
                    )
                    for result in parallel_results:
                        worker_status = "ok" if result.success else "fail"
                        print(f"[{worker_status}] worker {result.worker_id}: {result.stats.iterations} iterations, {result.stats.proofs_found} proofs")
            else:
                print(f"[info] draft-MCTS single-process: iterations={args.iterations}")
                root, stats = run_mcts(
                    project_root=Path(args.project_root).resolve(),
                    file_path=Path(args.file),
                    theorem_name=args.theorem,
                    client=client,
                    model=model,
                    premise_context=premise_context,
                    retrieval_index_path=args.retrieval_index,
                    retrieval_top_k=args.retrieval_top_k,
                    iterations=args.iterations,
                    exploration_c=args.exploration_c,
                    branch_min=args.branch_min,
                    branch_max=args.branch_max,
                    dojo_timeout=args.dojo_timeout,
                    use_tactics_estimate=True,
                )
        else:
            # fallback
            print("[warn] model-only fallback: search is unverifiable against Lean kernel")
            root, stats = run_mcts_fallback(
                theorem_name=args.theorem,
                client=client,
                model=model,
                premise_context=premise_context,
                retrieval_index_path=args.retrieval_index,
                retrieval_top_k=args.retrieval_top_k,
                iterations=args.iterations,
                exploration_c=args.exploration_c,
                branch_min=args.branch_min,
                branch_max=args.branch_max,
                use_tactics_estimate=True,
            )
    except Exception as exc:
        print(f"[fail] MCTS search failed: {exc}")
        logger.exception("Search exception")
        return 1
    finally:
        if _preflight_tmp_root is not None:
            import shutil as _shutil
            _shutil.rmtree(_preflight_tmp_root, ignore_errors=True)

    # Extract and report best path
    best_path = best_path_from_root(root)

    print(f"\n[ok] Search completed")
    print(f"[info] mode={mode} iterations={stats.iterations} elapsed={stats.elapsed_seconds:.2f}s")
    print(f"[info] proofs_found={stats.proofs_found} expanded_nodes={stats.expanded_nodes}")
    print(f"[info] evaluated_nodes={stats.evaluated_nodes} cache_hits={stats.cache_hits} api_calls={stats.api_calls}")
    if stats.value_samples:
        avg_raw = sum(s.get("raw_value", 0.0) for s in stats.value_samples) / len(stats.value_samples)
        avg_norm = sum(s.get("normalized_value", 0.0) for s in stats.value_samples) / len(stats.value_samples)
        cache_hits = sum(1 for s in stats.value_samples if s.get("cache_hit"))
        print(
            "[info] value_calibration="
            f"samples={len(stats.value_samples)} avg_raw={avg_raw:.3f} "
            f"avg_normalized={avg_norm:.3f} sample_cache_hits={cache_hits}"
        )
    if stats.elapsed_seconds > 0:
        print(f"[info] iterations/sec={stats.iterations_per_second:.2f}")
    
    print(f"[info] root: visits={root.visits} mean_value={root.mean_value:.4f}")
    
    print("[info] Best tactic path:")
    if not best_path:
        print("  (empty - no tactics found)")
    else:
        for i, tactic in enumerate(best_path, start=1):
            print(f"  {i}. {tactic}")

    # Optional tree analysis
    if args.analyze_tree:
        print("\n[info] Computing tree analysis...")
        analysis = analyze_tree(root)
        print(f"[info] Tree analysis:")
        print(f"  total_nodes={analysis.total_nodes}")
        print(f"  max_depth={analysis.max_depth}")
        print(f"  terminal_nodes={analysis.terminal_nodes}")
        print(f"  avg_branching_factor={analysis.avg_branching_factor:.2f}")
        print(f"  total_visits={analysis.total_visits}")
        print(f"  best_path_length={analysis.best_path_length}")
        print(f"  best_path_value={analysis.best_path_value:.4f}")

    # Optional tree export
    if args.export_tree:
        try:
            print(f"\n[info] Exporting tree to {args.export_tree}...")
            tree_json = export_tree_to_json(root, max_depth=15)
            output_path = Path(args.export_tree)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(tree_json, indent=2), encoding="utf-8")
            print(f"[ok] Tree exported to {args.export_tree}")
        except Exception as exc:
            print(f"[fail] Tree export failed: {exc}")

    return 0



if __name__ == "__main__":
    sys.exit(main())
