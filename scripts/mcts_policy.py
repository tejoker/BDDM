"""MCTS Policy Layer - Tactic scoring and value calibration.

This module extracts policy-specific logic from the monolithic mcts_search.py,
providing clean interfaces for:
  - Tactic policy scoring (reranking tactics by predicted success)
  - Value calibration (temperature scaling + Platt fitting)
  - Structural value heuristics (syntax-based scoring without API calls)
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class TacticPolicyScorer:
    """Bag-of-words logistic policy scorer backed by numpy weight files.

    Loads weights lazily from ``output/research/tactic_policy/`` (rl first,
    then sft).  Falls back to no-op if numpy or weight files are unavailable.
    """

    _WEIGHT_SEARCH_PATHS = [
        Path("output/research/tactic_policy/rl_weights.npy"),
        Path("output/research/tactic_policy/sft_weights.npy"),
    ]

    def __init__(self) -> None:
        self._weights: Any = None
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


# Singleton instance
TACTIC_POLICY = TacticPolicyScorer()


# ─────────────────────────────────────────────────────────────────────────────
# Value Calibration
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_CALIBRATION_TEMPERATURE = 1.5
_CALIBRATION_PATH = Path("data/value_calibration.json")


def _logit(p: float) -> float:
    """Compute logit(p) = log(p/(1-p)), bounded to avoid infinities."""
    p = max(1e-7, min(1.0 - 1e-7, p))
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    """Numerically stable sigmoid."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def temperature_scale(
    value: float,
    temperature: float = _DEFAULT_CALIBRATION_TEMPERATURE,
) -> float:
    """Apply temperature scaling to a [0,1] value estimate.

    Divides the logit by ``temperature`` before re-applying sigmoid.
    temperature > 1 spreads values away from the extremes, correcting
    overconfidence. Typical: T=1.5 shifts avg 0.967 → ~0.75.
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
        logger.info(
            "Platt calibration saved to %s (a=%.4f b=%.4f)",
            target,
            a,
            b,
        )

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


def get_platt_params() -> tuple[float, float] | None:
    """Get cached Platt parameters, loading from disk if necessary."""
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
            depth = (
                after_turnstile.count("(")
                + after_turnstile.count("[")
                + after_turnstile.count("∀")
                + after_turnstile.count("∃")
            )
            depths.append(depth)
        avg_depth = sum(depths) / len(depths)

    # Score: fewer goals + shallower depth = higher value.
    goal_penalty = 1.0 / (1.0 + goals)
    depth_penalty = 1.0 / (1.0 + avg_depth * 0.3)
    score = goal_penalty * depth_penalty

    return round(max(0.0, min(1.0, score)), 6)
