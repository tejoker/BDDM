"""State-level MCTS backed by leanprover-community/repl.

Split from mcts_search.py (lines 2759-EOF).
"""
from __future__ import annotations

import json
import logging
import math
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Ensure sibling script imports work when this submodule is imported.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Cross-reference from classic MCTS module.
from mcts._classic import _TACTIC_POLICY, LeanREPLServer, SearchStats  # noqa: E402

logger = logging.getLogger(__name__)

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


@dataclass
class ProofStateFeatures:
    """Feature bundle extracted from real Lean goals for grounded value scoring."""

    num_goals: int
    total_chars: int
    hypothesis_lines: int
    quantifier_count: int
    arithmetic_hint_count: int
    contradiction_hint_count: int
    has_existential_goal: bool
    hypothesis_overlap_ratio: float


_ARITH_HINT_RE = re.compile(
    r"\b(?:linarith|nlinarith|ring|norm_num|omega|Nat|Int|Real|\d+|[<>=+\-*/^])\b"
)
_CONTRADICTION_HINT_RE = re.compile(r"\b(?:False|absurd|contradiction|by_contra|\u22a5)\b")
_QUANTIFIER_RE = re.compile(r"(?:\u2200|\u2203|forall|Exists)")
_STATE_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_'.]*")


def _normalized_state_tokens(text: str) -> set[str]:
    stop = {
        "theorem",
        "lemma",
        "have",
        "show",
        "intro",
        "exact",
        "by",
        "true",
        "false",
        "prop",
        "type",
    }
    toks = {
        t.lower()
        for t in _STATE_TOKEN_RE.findall(text)
        if len(t) >= 1 and t.lower() not in stop
    }
    return toks


def extract_theorem_statement_from_file(
    *,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
) -> str:
    """Extract theorem/lemma declaration text for state-MCTS proof opening.

    Returns declaration text without requiring callers to provide full theorem
    signatures manually.
    """
    src_path = (project_root / file_path).resolve()
    src = src_path.read_text(encoding="utf-8")
    lines = src.splitlines()

    start_pat = re.compile(rf"\s*(?:lemma|theorem)\s+{re.escape(theorem_name)}\b")
    next_decl_pat = re.compile(r"\s*(?:lemma|theorem|def|structure|class|instance)\s+\w+")

    sig_lines: list[str] = []
    in_sig = False
    for line in lines:
        if not in_sig and start_pat.match(line):
            in_sig = True
        if not in_sig:
            continue

        sig_lines.append(line.rstrip())
        joined = " ".join(sig_lines)
        if ":= by" in joined or ":=" in joined:
            break

        if len(sig_lines) > 1 and next_decl_pat.match(line):
            # Defensive break if declaration unexpectedly drifted.
            sig_lines.pop()
            break

    if not sig_lines:
        raise ValueError(f"Theorem {theorem_name!r} not found in {file_path}")

    stmt = "\n".join(sig_lines).strip()
    stmt = re.sub(r":=\s*by\b.*$", "", stmt, flags=re.DOTALL).strip()
    stmt = re.sub(r":=\s*sorry\s*$", "", stmt, flags=re.DOTALL).strip()
    stmt = re.sub(r":=\s*$", "", stmt).strip()
    return stmt


def extract_proof_state_features(goals: list[str]) -> ProofStateFeatures:
    """Parse basic structural features from Lean proof goals."""
    joined = "\n".join(goals)
    goal_lines = [ln.strip() for ln in joined.splitlines() if ln.strip()]
    hypothesis_text_parts: list[str] = []
    goal_text_parts: list[str] = []
    for ln in goal_lines:
        if "⊢" in ln:
            goal_text_parts.append(ln.split("⊢", 1)[1])
        elif " : " in ln or ":" in ln:
            hypothesis_text_parts.append(ln)

    hypothesis_lines = sum(
        1
        for ln in goal_lines
        if (ln.startswith("h") or ln.startswith("have ") or " : " in ln)
    )
    quantifiers = len(_QUANTIFIER_RE.findall(joined))
    arithmetic_hints = len(_ARITH_HINT_RE.findall(joined))
    contradiction_hints = len(_CONTRADICTION_HINT_RE.findall(joined))
    has_existential = any("\u2203" in g or "Exists" in g for g in goals)
    hyp_tokens = _normalized_state_tokens("\n".join(hypothesis_text_parts))
    goal_tokens = _normalized_state_tokens("\n".join(goal_text_parts))
    overlap_ratio = (
        (len(hyp_tokens & goal_tokens) / max(1, len(goal_tokens)))
        if goal_tokens
        else 0.0
    )

    return ProofStateFeatures(
        num_goals=len(goals),
        total_chars=len(joined),
        hypothesis_lines=hypothesis_lines,
        quantifier_count=quantifiers,
        arithmetic_hint_count=arithmetic_hints,
        contradiction_hint_count=contradiction_hints,
        has_existential_goal=has_existential,
        hypothesis_overlap_ratio=overlap_ratio,
    )


def grounded_goal_value(goals: list[str], *, depth: int, max_depth: int) -> float:
    """Grounded heuristic over proof-state features (0.0-1.0).

    Scores combine goal count, textual complexity, structural hints, and depth
    pressure. Real kernel states remain source of truth; this only guides search.
    """
    if not goals:
        return 1.0

    feat = extract_proof_state_features(goals)

    goal_term = max(0.0, 1.0 - 0.18 * feat.num_goals)
    complexity_penalty = min(0.35, feat.total_chars / 2400.0)
    hyps_penalty = min(0.2, feat.hypothesis_lines * 0.015)
    quant_penalty = min(0.15, feat.quantifier_count * 0.02)
    depth_penalty = min(0.2, (depth / max(1, max_depth)) * 0.2)

    arithmetic_bonus = min(0.1, feat.arithmetic_hint_count * 0.01)
    contradiction_bonus = min(0.08, feat.contradiction_hint_count * 0.03)
    overlap_bonus = min(0.12, feat.hypothesis_overlap_ratio * 0.2)
    existential_penalty = 0.03 if feat.has_existential_goal else 0.0

    score = (
        goal_term
        - complexity_penalty
        - hyps_penalty
        - quant_penalty
        - depth_penalty
        - existential_penalty
        + arithmetic_bonus
        + contradiction_bonus
        + overlap_bonus
    )
    return max(0.0, min(0.97, score))


def _build_compounding_retriever(
    *,
    project_root: Path,
    max_entries: int,
) -> tuple[Any | None, int]:
    """Build lightweight retriever from trusted/conditional proved outputs."""
    try:
        from premise_retrieval import PremiseEntry, PremiseRetriever
    except Exception:
        return None, 0

    kg_paths = [
        project_root / "output" / "kg" / "trusted" / "theorems.jsonl",
        project_root / "output" / "kg" / "conditional" / "theorems.jsonl",
    ]
    entries: list[Any] = []
    for kg_path in kg_paths:
        if not kg_path.exists():
            continue
        try:
            for line in kg_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                name = str(row.get("theorem_name") or row.get("name") or "").strip()
                stmt = str(row.get("statement") or row.get("theorem_statement") or "").strip()
                if not name or not stmt:
                    continue
                entries.append(
                    PremiseEntry(
                        name=name,
                        statement=stmt,
                        namespace="Desol.KG",
                        source_file=str(kg_path),
                    )
                )
        except Exception:
            continue

    if not entries:
        return None, 0

    # Keep newest entries; tail has latest proved theorems.
    entries = entries[-max_entries:]
    try:
        retriever = PremiseRetriever.build(entries, dims=256, encoder_name="hash")
    except Exception:
        return None, 0
    return retriever, len(entries)


def _retrieve_compounding_context(
    *,
    retriever: Any | None,
    lean_state: str,
    top_k: int,
) -> str:
    """Retrieve top proved internal lemmas relevant to current state."""
    if retriever is None or top_k < 1:
        return ""
    try:
        hits = retriever.query(lean_state, top_k=top_k)
    except Exception:
        return ""
    if not hits:
        return ""

    lines: list[str] = []
    for hit in hits:
        stmt = " ".join(str(hit.statement).split())
        if len(stmt) > 160:
            stmt = stmt[:157] + "..."
        lines.append(f"- {hit.name}: {stmt}")
    return "Proved internal lemmas (self-compounding retrieval):\n" + "\n".join(lines)


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


def _goal_value(goals: list[str], *, depth: int, max_depth: int) -> float:
    """Compatibility wrapper for grounded value function."""
    return grounded_goal_value(goals, depth=depth, max_depth=max_depth)


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
    compounding_retriever: Any | None = None,
    compounding_top_k: int = 4,
) -> list[tuple["StateMCTSNode", float]]:
    """Generate n_tactics candidates and apply each via the REPL."""
    from lean_repl_server import LeanError as REPLLeanError, ProofFinished as REPLProofFinished, TacticState as REPLTacticState
    from ponder_loop import generate_tactic_options

    if node.depth >= max_depth:
        node.is_terminal = True
        node.terminal_reason = "depth-limit"
        return []

    dynamic_compound_ctx = _retrieve_compounding_context(
        retriever=compounding_retriever,
        lean_state=node.pp,
        top_k=compounding_top_k,
    )
    effective_premise_context = premise_context
    if dynamic_compound_ctx:
        effective_premise_context = (
            f"{premise_context}\n\n{dynamic_compound_ctx}".strip()
            if premise_context
            else dynamic_compound_ctx
        )

    candidates = generate_tactic_options(
        lean_state=node.pp,
        client=client,
        model=model,
        num_options=n_tactics,
        temperature=temperature,
        premise_context=effective_premise_context,
        retrieval_index_path=retrieval_index_path,
        retrieval_top_k=retrieval_top_k,
    )
    candidates = _TACTIC_POLICY.rerank(node.pp, candidates)

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

        value = _goal_value(goals, depth=node.depth + 1, max_depth=max_depth)
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
    file_path: Path | None = None,
    theorem_name: str = "",
    self_compounding_top_k: int = 4,
    self_compounding_max_entries: int = 400,
) -> tuple[bool, list[str], str]:
    """State-level MCTS using leanprover-community/repl.

    Returns (success, tactic_list, summary_string).
    success=True means a complete proof path was found.
    tactic_list is the winning tactic sequence (empty on failure).
    """
    from lean_repl_server import LeanError as REPLLeanError

    if LeanREPLServer is None:
        return False, [], "LeanREPLServer is unavailable"

    if theorem_statement.strip().startswith("--") or not theorem_statement.strip():
        if file_path is None or not theorem_name.strip():
            return False, [], "Missing theorem statement and no file/theorem resolver provided"
        try:
            theorem_statement = extract_theorem_statement_from_file(
                project_root=project_root,
                file_path=file_path,
                theorem_name=theorem_name,
            )
        except Exception as exc:
            return False, [], f"Could not extract theorem statement: {exc}"

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

    compounding_retriever, compounding_entries = _build_compounding_retriever(
        project_root=project_root,
        max_entries=self_compounding_max_entries,
    )
    if compounding_retriever is not None:
        logger.info(
            "[kg] self-compounding retrieval enabled entries=%d top_k=%d",
            compounding_entries,
            self_compounding_top_k,
        )

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
                compounding_retriever=compounding_retriever,
                compounding_top_k=self_compounding_top_k,
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

    # Inject proved subgoal tactics into premise context for the final pass
    final_premise_context = loaded_premise_context
    if closed_count > 0:
        proved_block = "Proved subgoals in this theorem:\n" + "\n".join(
            f"- {name}: {' '.join(tactics)}"
            for name, tactics in closed_proofs.items() if tactics
        )
        final_premise_context = (
            (final_premise_context + "\n\n" + proved_block).strip()
            if final_premise_context else proved_block
        )
        logger.info("[hierarchical-state] Injected %d proved subgoals into final premise context", closed_count)

    # Step 5: run final state-MCTS on the full theorem
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
        premise_context=final_premise_context,
        retrieval_index_path=retrieval_index_path,
        retrieval_top_k=retrieval_top_k,
    )

    summary = (
        f"Hierarchical-state MCTS: {closed_count}/{len(subgoals)} subgoals closed. "
        f"Final: {final_summary}"
    )
    return final_ok, final_tactics, summary


