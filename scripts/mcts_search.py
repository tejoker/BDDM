#!/usr/bin/env python3
"""Phase 4 MCTS skeleton for Lean proof macro-search.

Implements:
- Node data structure with visits/value
- UCT-based selection
- Expansion using model-proposed tactic options
- Value evaluation of new Lean states
- Backpropagation of evaluation scores
"""

from __future__ import annotations

import argparse
import math
import os
import random
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from lean_dojo import Dojo, Theorem
from lean_dojo.interaction.dojo import LeanError, ProofFinished, ProofGivenUp, TacticState
from mistralai.client import Mistral

# Ensure sibling script imports work when invoked from project root.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from ponder_loop import generate_tactic_options, load_premise_context
from prove_with_ponder import _prepare_leandojo_repo

VALUE_RE = re.compile(r"<value>([01](?:\.\d+)?)</value>", re.IGNORECASE)
STATE_RE = re.compile(r"<state>(.*?)</state>", re.IGNORECASE | re.DOTALL)

EVAL_SYSTEM_PROMPT = (
    "You are a Lean proof-value estimator. "
    "Given a Lean proof state, output exactly one score in <value> tags from 0.0 to 1.0, "
    "where 1.0 means solved (no goals) and 0.0 means very far from solved. "
    "Output only the <value> tag."
)

EVAL_USER_PROMPT = "Evaluate this Lean 4 proof state:\n\n{state}"
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


@dataclass
class MCTSNode:
    state: Any
    state_text: str
    tactic_from_parent: str | None
    parent: MCTSNode | None = None
    visits: int = 0
    value_sum: float = 0.0
    children: list[MCTSNode] = field(default_factory=list)
    is_terminal: bool = False
    terminal_reason: str = ""

    @property
    def mean_value(self) -> float:
        if self.visits == 0:
            return 0.0
        return self.value_sum / self.visits


@dataclass
class SearchStats:
    iterations: int = 0
    expanded_nodes: int = 0
    evaluated_nodes: int = 0
    cache_hits: int = 0


@dataclass
class PreflightResult:
    ok: bool
    message: str
    prepared_repo: Any | None = None
    tmp_root: Path | None = None


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


def evaluate_state_value(*, state_text: str, client: Mistral, model: str) -> float:
    response = client.chat.complete(
        model=model,
        messages=[
            {"role": "system", "content": EVAL_SYSTEM_PROMPT},
            {"role": "user", "content": EVAL_USER_PROMPT.format(state=state_text)},
        ],
        temperature=0.0,
        max_tokens=64,
    )

    raw = _response_to_text(response)
    score = parse_value_score(raw)
    if score is not None:
        return score

    # Accept common malformed variant like <0.7>
    stripped = raw.strip()
    if stripped.startswith("<") and stripped.endswith(">"):
        try:
            maybe = float(stripped[1:-1].strip())
            if 0.0 <= maybe <= 1.0:
                return maybe
        except ValueError:
            pass

    # Heuristic fallback when strict parser fails.
    goals = state_text.count("⊢")
    return 1.0 / (1.0 + goals)


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
    dojo: Dojo,
    client: Mistral,
    model: str,
    premise_context: str = "",
    branch_min: int = 3,
    branch_max: int = 5,
) -> list[MCTSNode]:
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
            child = MCTSNode(
                state=outcome,
                state_text=outcome.pp,
                tactic_from_parent=tactic,
                parent=leaf,
            )
            children.append(child)
            continue

        if isinstance(outcome, ProofFinished):
            child = MCTSNode(
                state=leaf.state,
                state_text="no goals",
                tactic_from_parent=tactic,
                parent=leaf,
                is_terminal=True,
                terminal_reason="proof-finished",
            )
            children.append(child)
            continue

        if isinstance(outcome, (LeanError, ProofGivenUp)):
            continue

    leaf.children.extend(children)
    return children


def expand_leaf_fallback(
    *,
    leaf: MCTSNode,
    client: Mistral,
    model: str,
    premise_context: str = "",
    branch_min: int = 3,
    branch_max: int = 5,
) -> list[MCTSNode]:
    """Expansion fallback when LeanDojo is unavailable.

    This mode predicts next states from model text transitions.
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
        child = MCTSNode(
            state=None,
            state_text=next_state,
            tactic_from_parent=tactic,
            parent=leaf,
            is_terminal=is_terminal,
            terminal_reason=("predicted-proof-finished" if is_terminal else ""),
        )
        children.append(child)

    leaf.children.extend(children)
    return children


def best_path_from_root(root: MCTSNode, max_depth: int = 64) -> list[str]:
    path: list[str] = []
    node = root
    depth = 0

    while node.children and depth < max_depth:
        node = max(node.children, key=lambda c: (c.visits, c.mean_value))
        if node.tactic_from_parent:
            path.append(node.tactic_from_parent)
        if node.is_terminal:
            break
        depth += 1

    return path


def run_mcts(
    *,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    client: Mistral,
    model: str,
    premise_context: str = "",
    iterations: int = 30,
    exploration_c: float = 1.4,
    branch_min: int = 3,
    branch_max: int = 5,
    dojo_timeout: int = 600,
    prepared_repo: Any | None = None,
    prepared_tmp_root: Path | None = None,
) -> tuple[MCTSNode, SearchStats]:
    if prepared_repo is None:
        repo, tmp_root = _prepare_leandojo_repo(project_root)
    else:
        repo, tmp_root = prepared_repo, prepared_tmp_root
    theorem = Theorem(repo, file_path, theorem_name)
    stats = SearchStats()

    try:
        with Dojo(theorem, timeout=dojo_timeout) as (dojo, initial_state):
            if not isinstance(initial_state, TacticState):
                raise RuntimeError(f"Unexpected initial state type: {type(initial_state).__name__}")

            root = MCTSNode(
                state=initial_state,
                state_text=initial_state.pp,
                tactic_from_parent=None,
            )

            # Cache: state_text hash -> evaluated value. Avoids redundant API calls
            # when different tactic paths converge to the same Lean proof state.
            _value_cache: dict[str, float] = {}

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
                        branch_min=branch_min,
                        branch_max=branch_max,
                    )
                    if new_children:
                        stats.expanded_nodes += 1
                        eval_node = random.choice(new_children)
                        path = path + [eval_node]

                if eval_node.is_terminal and eval_node.terminal_reason == "proof-finished":
                    value = 1.0
                else:
                    cache_key = eval_node.state_text.strip()
                    if cache_key in _value_cache:
                        value = _value_cache[cache_key]
                        stats.cache_hits += 1
                    else:
                        value = evaluate_state_value(
                            state_text=eval_node.state_text,
                            client=client,
                            model=model,
                        )
                        _value_cache[cache_key] = value
                        stats.evaluated_nodes += 1

                backpropagate(path, value)

            return root, stats
    finally:
        if tmp_root is not None:
            import shutil

            shutil.rmtree(tmp_root, ignore_errors=True)


def run_mcts_fallback(
    *,
    theorem_name: str,
    client: Mistral,
    model: str,
    premise_context: str = "",
    iterations: int = 30,
    exploration_c: float = 1.4,
    branch_min: int = 3,
    branch_max: int = 5,
) -> tuple[MCTSNode, SearchStats]:
    stats = SearchStats()

    root = MCTSNode(
        state=None,
        state_text=(
            "Fallback root state (unverified by LeanDojo). "
            f"Target theorem: {theorem_name}."
        ),
        tactic_from_parent=None,
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
                branch_min=branch_min,
                branch_max=branch_max,
            )
            if new_children:
                stats.expanded_nodes += 1
                eval_node = random.choice(new_children)
                path = path + [eval_node]

        if eval_node.is_terminal and "finished" in eval_node.terminal_reason:
            value = 1.0
        else:
            value = evaluate_state_value(
                state_text=eval_node.state_text,
                client=client,
                model=model,
            )
            stats.evaluated_nodes += 1

        backpropagate(path, value)

    return root, stats


def leandojo_preflight(
    *,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    dojo_timeout: int,
) -> PreflightResult:
    """Check whether LeanDojo can trace and open the theorem state."""
    repo, tmp_root = _prepare_leandojo_repo(project_root)
    theorem = Theorem(repo, file_path, theorem_name)
    keep_tmp = False

    try:
        with Dojo(theorem, timeout=dojo_timeout) as (_dojo, state):
            if isinstance(state, TacticState):
                keep_tmp = True
                return PreflightResult(
                    True,
                    "LeanDojo preflight passed",
                    prepared_repo=repo,
                    tmp_root=tmp_root,
                )
            return PreflightResult(
                False,
                f"LeanDojo returned unexpected initial state type: {type(state).__name__}",
            )
    except Exception as exc:
        return PreflightResult(False, f"LeanDojo preflight failed: {exc}")
    finally:
        # Keep prepared snapshot alive when preflight succeeds to avoid duplicating copy+trace work.
        if tmp_root is not None and not keep_tmp:
            import shutil

            shutil.rmtree(tmp_root, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lean proof MCTS skeleton")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--file", required=True, help="Lean file path relative to project root")
    parser.add_argument("--theorem", required=True, help="Theorem name")
    parser.add_argument("--model", default="", help="Mistral model (defaults to MISTRAL_MODEL)")
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--exploration-c", type=float, default=1.4)
    parser.add_argument("--branch-min", type=int, default=3)
    parser.add_argument("--branch-max", type=int, default=5)
    parser.add_argument("--dojo-timeout", type=int, default=600)
    parser.add_argument(
        "--fallback-mode",
        choices=["none", "model"],
        default="model",
        help="Fallback when LeanDojo preflight fails",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip LeanDojo preflight and run selected mode directly",
    )
    parser.add_argument(
        "--auto-patch-leandojo",
        action="store_true",
        help="Apply known LeanDojo ExtractData compatibility patches before preflight",
    )
    parser.add_argument(
        "--premise-file",
        default="",
        help="Path to .toon knowledge inventory for premise injection",
    )
    parser.add_argument(
        "--premise-namespace",
        default="ProbabilityTheory",
        help="Namespace filter applied when loading premise-file",
    )
    return parser


def main() -> int:
    load_dotenv()
    args = build_parser().parse_args()

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

    client = Mistral(api_key=api_key)
    premise_context = ""
    if args.premise_file:
        premise_context = load_premise_context(
            args.premise_file,
            namespace_filter=args.premise_namespace,
        )

    if args.auto_patch_leandojo:
        changed, patch_msg = patch_leandojo_extractdata_compat()
        status = "[ok]" if changed else "[info]"
        print(f"{status} {patch_msg}")

    mode = "leandojo"
    prepared_repo: Any | None = None
    prepared_tmp_root: Path | None = None
    if not args.skip_preflight:
        preflight = leandojo_preflight(
            project_root=Path(args.project_root).resolve(),
            file_path=Path(args.file),
            theorem_name=args.theorem,
            dojo_timeout=min(args.dojo_timeout, 120),
        )
        if preflight.ok:
            print(f"[ok] {preflight.message}")
            prepared_repo = preflight.prepared_repo
            prepared_tmp_root = preflight.tmp_root
        else:
            print(f"[warn] {preflight.message}")
            if args.fallback_mode == "model":
                mode = "model"
                print("[warn] falling back to model-only macro-search (state transitions are unverified)")
            else:
                print("[fail] preflight failed and fallback-mode=none")
                return 1

    try:
        if mode == "leandojo":
            root, stats = run_mcts(
                project_root=Path(args.project_root).resolve(),
                file_path=Path(args.file),
                theorem_name=args.theorem,
                client=client,
                model=model,
                premise_context=premise_context,
                iterations=args.iterations,
                exploration_c=args.exploration_c,
                branch_min=args.branch_min,
                branch_max=args.branch_max,
                dojo_timeout=args.dojo_timeout,
                prepared_repo=prepared_repo,
                prepared_tmp_root=prepared_tmp_root,
            )
        else:
            root, stats = run_mcts_fallback(
                theorem_name=args.theorem,
                client=client,
                model=model,
                premise_context=premise_context,
                iterations=args.iterations,
                exploration_c=args.exploration_c,
                branch_min=args.branch_min,
                branch_max=args.branch_max,
            )
    except Exception as exc:
        print(f"[fail] mcts search failed: {exc}")
        return 1

    best_path = best_path_from_root(root)

    print(f"[ok] mode={mode} iterations={stats.iterations}")
    print(f"[info] root_visits={root.visits} root_mean_value={root.mean_value:.4f}")
    print(f"[info] expanded_nodes={stats.expanded_nodes} evaluated_nodes={stats.evaluated_nodes} cache_hits={stats.cache_hits}")
    print("[info] best_tactic_path:")
    if not best_path:
        print("(empty)")
    else:
        for i, t in enumerate(best_path, start=1):
            print(f"{i}. {t}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
