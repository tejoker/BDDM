"""MCTS Core Types - Data structures for tree search algorithms.

This module extracts type definitions from the monolithic mcts_search.py,
providing clean interfaces for:
  - MCTS tree nodes and tree analysis
  - Search statistics and results
  - Draft-mode nodes for full-script repair
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


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
    """Statistics from a single MCTS run."""
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
    """Result of pre-flight environment checks."""
    ok: bool
    message: str
    prepared_repo: Any | None = None
    tmp_root: Any | None = None


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
    """Cached result of a draft-to-state transition."""
    solved: bool
    state_text: str
    error_feedback: str
    step_records: list[dict[str, Any]]
    value: float
