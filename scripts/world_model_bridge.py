#!/usr/bin/env python3
"""World-model bridge search scaffold for theorem-linking.

This module defines a lightweight state/action/reward search loop that can be
used to prioritize bridge actions before expensive proving attempts.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bridge_proofs import (
    BridgeCandidate,
    _candidate_is_actionable,
    _goal_lane_allowed,
    build_theorem_context_pack,
    check_entailment_z3,
    extract_assumption_slot_name,
    execute_bridge_chain,
    execute_bridge_proof_lean,
    synthesize_actionable_goal,
    suggest_bridge_candidates,
)


@dataclass
class AssumptionSlot:
    idx: int
    slot_name: str
    lean_expr: str
    lean_statement: str
    label: str
    goal_lane: bool = True


@dataclass
class WMAction:
    kind: str  # z3 | bridge_candidate | lean_check
    assumption_idx: int
    score_hint: float = 0.0
    theorem_name: str = ""
    paper_id: str = ""
    proposer: str = ""
    lean_statement: str = ""


@dataclass
class WorldState:
    grounded: set[int] = field(default_factory=set)
    context_theorems: set[str] = field(default_factory=set)
    attempted_actions: set[tuple[str, int, str]] = field(default_factory=set)
    steps: int = 0
    reward: float = 0.0


@dataclass
class WorldModelResult:
    target_theorem: str
    assumptions_total: int
    grounded_count: int
    reward: float
    actions_taken: list[dict[str, Any]]
    failure_reasons: dict[str, int]
    elapsed_s: float


@dataclass
class MCTSNode:
    state: WorldState
    action_log: list[dict[str, Any]]
    visits: int = 0
    value_sum: float = 0.0
    children: list["MCTSNode"] = field(default_factory=list)
    terminal: bool = False

    @property
    def value(self) -> float:
        return self.value_sum / self.visits if self.visits else 0.0


def _iter_ledger_entries(ledger_root: Path):
    for path in sorted(ledger_root.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = raw.get("entries", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                yield path.stem, row


def _load_target_row(ledger_root: Path, target_theorem: str) -> dict[str, Any] | None:
    for _paper_id, row in _iter_ledger_entries(ledger_root):
        if str(row.get("theorem_name", "")).strip() == target_theorem:
            return row
    return None


def _extract_ungrounded_assumptions(row: dict[str, Any]) -> list[AssumptionSlot]:
    assumptions = row.get("assumptions", [])
    if not isinstance(assumptions, list):
        return []
    out: list[AssumptionSlot] = []
    for i, a in enumerate(assumptions):
        if not isinstance(a, dict):
            continue
        g = str(a.get("grounding", "")).upper()
        if g not in {"UNGROUNDED", "UNKNOWN", ""}:
            continue
        raw_expr = str(a.get("lean_expr", "")).strip()
        label = str(a.get("label", "")).strip()
        slot_name = extract_assumption_slot_name(lean_expr=raw_expr, label=label, idx=i)
        lean_statement = synthesize_actionable_goal(
            lean_expr=raw_expr,
            lean_statement=str(a.get("lean_statement", "")).strip(),
            label=slot_name,
        )
        goal_lane = _goal_lane_allowed(lean_expr=raw_expr, lean_statement=lean_statement)
        out.append(
            AssumptionSlot(
                idx=i,
                slot_name=slot_name,
                lean_expr=raw_expr,
                lean_statement=lean_statement,
                label=label,
                goal_lane=goal_lane,
            )
        )
    return out


def _candidate_actions(
    *,
    assumptions: list[AssumptionSlot],
    ledger_root: Path,
    state: WorldState,
    max_candidates_per_assumption: int,
    context_pack: Any | None = None,
    retrieval_memory_path: Path | None = None,
) -> list[WMAction]:
    actions: list[WMAction] = []
    for slot in assumptions:
        if slot.idx in state.grounded:
            continue
        if not slot.goal_lane:
            continue
        if slot.lean_expr:
            key = ("z3", slot.idx, "")
            if key not in state.attempted_actions:
                actions.append(WMAction(kind="z3", assumption_idx=slot.idx, score_hint=0.4, proposer="symbolic"))
        if slot.lean_statement:
            key = ("lean_check", slot.idx, "")
            if key not in state.attempted_actions:
                actions.append(WMAction(kind="lean_check", assumption_idx=slot.idx, score_hint=0.2, proposer="symbolic"))
        query_expr = slot.lean_expr or slot.label
        if not query_expr:
            continue
        candidates: list[BridgeCandidate] = suggest_bridge_candidates(
            assumption_expr=query_expr,
            ledger_root=ledger_root,
            max_candidates=max_candidates_per_assumption,
            context_pack=context_pack,
            allow_template_fallback=True,
            retrieval_memory_path=retrieval_memory_path,
        )
        for c in candidates:
            key = ("bridge_candidate", slot.idx, c.theorem_name)
            if key in state.attempted_actions:
                continue
            actions.append(
                WMAction(
                    kind="bridge_candidate",
                    assumption_idx=slot.idx,
                    score_hint=float(c.score),
                    theorem_name=c.theorem_name,
                    paper_id=c.paper_id,
                    proposer="retrieval",
                    lean_statement=c.lean_statement,
                )
            )
        # Leanstral/model prior scaffold: bias by assumption text complexity.
        if query_expr:
            complexity = min(1.0, max(0.0, len(query_expr) / 180.0))
            key = ("bridge_candidate", slot.idx, f"model_prior:{slot.idx}")
            if key not in state.attempted_actions:
                actions.append(
                    WMAction(
                        kind="bridge_candidate",
                        assumption_idx=slot.idx,
                        score_hint=0.15 + (0.2 * complexity),
                        theorem_name=f"model_prior:{slot.idx}",
                        paper_id="",
                        proposer="leanstral_prior",
                    )
                )
    actions.sort(key=lambda a: (a.score_hint, a.kind == "z3"), reverse=True)
    return actions


def _apply_action(
    *,
    state: WorldState,
    action: WMAction,
    assumption_by_idx: dict[int, AssumptionSlot],
) -> tuple[WorldState, dict[str, Any]]:
    next_state = WorldState(
        grounded=set(state.grounded),
        context_theorems=set(state.context_theorems),
        attempted_actions=set(state.attempted_actions),
        steps=state.steps + 1,
        reward=state.reward,
    )
    slot = assumption_by_idx[action.assumption_idx]
    log: dict[str, Any] = {
        "kind": action.kind,
        "assumption_idx": action.assumption_idx,
        "theorem_name": action.theorem_name,
        "paper_id": action.paper_id,
        "score_hint": action.score_hint,
        "proposer": action.proposer,
        "grounded": False,
        "detail": "",
    }

    if action.kind == "z3" and slot.lean_expr:
        er = check_entailment_z3(slot.lean_expr)
        if er.entailed:
            next_state.grounded.add(slot.idx)
            next_state.reward += 1.0
            log["grounded"] = True
            log["detail"] = "z3_entails"
        else:
            next_state.reward -= 0.05
            log["detail"] = f"z3_fail:{er.error or 'counterexample'}"
    elif action.kind == "lean_check" and slot.lean_statement:
        # Scaffold: if no tactic proof is provided, we try a tiny placeholder.
        er = execute_bridge_proof_lean(slot.lean_statement, "first | exact? | aesop", timeout_s=20)
        if er.entailed:
            next_state.grounded.add(slot.idx)
            next_state.reward += 1.2
            log["grounded"] = True
            log["detail"] = "lean_entails"
        else:
            next_state.reward -= 0.08
            log["detail"] = f"lean_fail:{er.error or 'unknown'}"
    elif action.kind == "bridge_candidate":
        if action.lean_statement and _candidate_is_actionable(action.lean_statement):
            er = execute_bridge_proof_lean(action.lean_statement, "first | exact? | aesop", timeout_s=15)
            if er.entailed:
                next_state.grounded.add(slot.idx)
                next_state.reward += 0.9
                log["grounded"] = True
                log["detail"] = "candidate_lean_entails"
            else:
                next_state.reward -= 0.03
                if action.theorem_name:
                    next_state.context_theorems.add(action.theorem_name)
                log["detail"] = "candidate_lean_failed_context_augmented"
        else:
            if action.theorem_name:
                next_state.context_theorems.add(action.theorem_name)
            # World-model prior reward: prefer high-confidence candidates.
            next_state.reward += max(0.0, min(0.25, action.score_hint * 0.25))
            log["detail"] = "context_augmented"

    # Small step penalty to encourage shorter bridges.
    next_state.attempted_actions.add((action.kind, action.assumption_idx, action.theorem_name))
    next_state.reward -= 0.02
    return next_state, log


def _ucb_score(parent_visits: int, child: MCTSNode, c: float = 1.25) -> float:
    if child.visits == 0:
        return float("inf")
    exploit = child.value
    explore = c * math.sqrt(max(1e-9, math.log(max(1, parent_visits)) / child.visits))
    return exploit + explore


def _rollout_value(
    *,
    state: WorldState,
    assumptions: list[AssumptionSlot],
) -> float:
    total = max(1, len(assumptions))
    progress = len(state.grounded) / total
    return float(state.reward) + (1.5 * progress)


def run_world_model_mcts(
    *,
    assumptions: list[AssumptionSlot],
    ledger_root: Path,
    budget: int,
    max_depth: int,
    max_candidates_per_assumption: int,
    context_pack: Any | None = None,
    retrieval_memory_path: Path | None = None,
) -> tuple[WorldState, list[dict[str, Any]]]:
    assumption_by_idx = {a.idx: a for a in assumptions}
    root = MCTSNode(state=WorldState(), action_log=[])
    best_state = root.state
    best_log: list[dict[str, Any]] = []
    rnd = random.Random(0)

    for _ in range(max(1, budget)):
        node = root
        path = [node]

        # Selection
        while node.children and not node.terminal:
            node = max(node.children, key=lambda c: _ucb_score(path[-1].visits + 1, c))
            path.append(node)

        # Expansion
        if not node.terminal and node.state.steps < max_depth and len(node.state.grounded) < len(assumptions):
            actions = _candidate_actions(
                assumptions=assumptions,
                ledger_root=ledger_root,
                state=node.state,
                max_candidates_per_assumption=max_candidates_per_assumption,
                context_pack=context_pack,
                retrieval_memory_path=retrieval_memory_path,
            )
            if actions:
                topk = actions[: min(4, len(actions))]
                # Expand a small diversified set.
                for act in topk:
                    ns, lg = _apply_action(
                        state=node.state,
                        action=act,
                        assumption_by_idx=assumption_by_idx,
                    )
                    child = MCTSNode(
                        state=ns,
                        action_log=node.action_log + [lg],
                        terminal=(ns.steps >= max_depth or len(ns.grounded) >= len(assumptions)),
                    )
                    node.children.append(child)
                node = rnd.choice(node.children)
                path.append(node)
            else:
                node.terminal = True

        # Rollout/eval (state-value proxy).
        value = _rollout_value(state=node.state, assumptions=assumptions)

        if (len(node.state.grounded) > len(best_state.grounded)) or (
            len(node.state.grounded) == len(best_state.grounded) and node.state.reward > best_state.reward
        ):
            best_state = node.state
            best_log = node.action_log

        # Backprop
        for p in path:
            p.visits += 1
            p.value_sum += value

    return best_state, best_log


def run_world_model_bridge_search(
    *,
    target_theorem: str,
    ledger_root: Path,
    budget: int = 40,
    max_depth: int = 4,
    max_candidates_per_assumption: int = 3,
    retrieval_memory_path: Path | None = None,
) -> WorldModelResult:
    t0 = time.time()
    row = _load_target_row(ledger_root, target_theorem)
    if row is None:
        return WorldModelResult(
            target_theorem=target_theorem,
            assumptions_total=0,
            grounded_count=0,
            reward=0.0,
            actions_taken=[],
            failure_reasons={"target_missing": 1},
            elapsed_s=round(time.time() - t0, 3),
        )

    assumptions = _extract_ungrounded_assumptions(row)
    context_pack = build_theorem_context_pack(row)
    state, actions_taken = run_world_model_mcts(
        assumptions=assumptions,
        ledger_root=ledger_root,
        budget=budget,
        max_depth=max_depth,
        max_candidates_per_assumption=max_candidates_per_assumption,
        context_pack=context_pack,
        retrieval_memory_path=retrieval_memory_path,
    )
    failure_reasons: dict[str, int] = {}
    if not assumptions:
        failure_reasons["no_assumptions"] = 1
    for slot in assumptions:
        if not slot.slot_name:
            failure_reasons["assumption_slot_unmapped"] = failure_reasons.get("assumption_slot_unmapped", 0) + 1
    if failure_reasons.get("assumption_slot_unmapped", 0) > 0:
        return WorldModelResult(
            target_theorem=target_theorem,
            assumptions_total=len(assumptions),
            grounded_count=0,
            reward=0.0,
            actions_taken=[],
            failure_reasons=failure_reasons,
            elapsed_s=round(time.time() - t0, 3),
        )
    if assumptions and not actions_taken:
        failure_reasons["no_actions_generated"] = 1
    for a in actions_taken:
        if not isinstance(a, dict):
            continue
        if not bool(a.get("grounded", False)):
            kind = str(a.get("kind", "unknown"))
            detail = str(a.get("detail", ""))
            if kind == "z3":
                if detail.startswith("z3_fail:"):
                    failure_reasons["z3_not_entailed_or_error"] = failure_reasons.get("z3_not_entailed_or_error", 0) + 1
            elif kind == "lean_check":
                failure_reasons["lean_check_failed"] = failure_reasons.get("lean_check_failed", 0) + 1
            elif kind == "bridge_candidate":
                failure_reasons["candidate_context_only"] = failure_reasons.get("candidate_context_only", 0) + 1
    if assumptions and len(state.grounded) == 0:
        failure_reasons["none_grounded"] = failure_reasons.get("none_grounded", 0) + 1

    return WorldModelResult(
        target_theorem=target_theorem,
        assumptions_total=len(assumptions),
        grounded_count=len(state.grounded),
        reward=round(state.reward, 4),
        actions_taken=actions_taken,
        failure_reasons=failure_reasons,
        elapsed_s=round(time.time() - t0, 3),
    )


def compare_against_baseline(
    *,
    target_theorem: str,
    ledger_root: Path,
    budget: int = 40,
    max_depth: int = 4,
    max_candidates_per_assumption: int = 3,
    baseline_lean_timeout_s: int = 60,
    baseline_max_repair_rounds: int = 2,
    retrieval_memory_path: Path | None = None,
) -> dict[str, Any]:
    wm = run_world_model_bridge_search(
        target_theorem=target_theorem,
        ledger_root=ledger_root,
        budget=budget,
        max_depth=max_depth,
        max_candidates_per_assumption=max_candidates_per_assumption,
        retrieval_memory_path=retrieval_memory_path,
    )
    baseline = execute_bridge_chain(
        target_theorem=target_theorem,
        ledger_root=ledger_root,
        max_depth=max_depth,
        max_candidates_per_step=max_candidates_per_assumption,
        require_assumption_slot_coverage=True,
        lean_timeout_s=max(5, int(baseline_lean_timeout_s)),
        max_repair_rounds=max(0, int(baseline_max_repair_rounds)),
        retrieval_memory_path=retrieval_memory_path,
    )
    return {
        "target_theorem": target_theorem,
        "world_model": {
            "assumptions_total": wm.assumptions_total,
            "grounded_count": wm.grounded_count,
            "reward": wm.reward,
            "elapsed_s": wm.elapsed_s,
            "actions_taken": wm.actions_taken,
            "failure_reasons": wm.failure_reasons,
        },
        "baseline_text_bridge": {
            "grounded_count": len(baseline.newly_grounded),
            "still_ungrounded": len(baseline.still_ungrounded),
            "entailed_checks": len(baseline.entailment_results),
            "ordered_candidates": baseline.chain_plan.ordered_candidates,
            "failure_reasons": baseline.failure_reasons,
            "assumption_diagnostics": baseline.assumption_diagnostics,
            "repair_attempts_total": baseline.repair_attempts_total,
            "repair_success_count": baseline.repair_success_count,
        },
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="World-model bridge search scaffold")
    p.add_argument("--target-theorem", required=True, help="Theorem name from verification ledger")
    p.add_argument("--ledger-root", default="output/verification_ledgers", help="Verification ledger root")
    p.add_argument("--budget", type=int, default=40)
    p.add_argument("--max-depth", type=int, default=4)
    p.add_argument("--max-candidates-per-assumption", type=int, default=3)
    p.add_argument("--baseline-lean-timeout-s", type=int, default=60)
    p.add_argument("--baseline-max-repair-rounds", type=int, default=2)
    p.add_argument("--retrieval-memory-path", default="", help="Optional retrieval memory JSON path")
    p.add_argument("--compare-baseline", action="store_true", help="Run baseline bridge pipeline too")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    ledger_root = Path(args.ledger_root)
    if args.compare_baseline:
        payload = compare_against_baseline(
            target_theorem=args.target_theorem,
            ledger_root=ledger_root,
            budget=args.budget,
            max_depth=args.max_depth,
            max_candidates_per_assumption=args.max_candidates_per_assumption,
            baseline_lean_timeout_s=args.baseline_lean_timeout_s,
            baseline_max_repair_rounds=args.baseline_max_repair_rounds,
            retrieval_memory_path=Path(args.retrieval_memory_path) if args.retrieval_memory_path else None,
        )
    else:
        wm = run_world_model_bridge_search(
            target_theorem=args.target_theorem,
            ledger_root=ledger_root,
            budget=args.budget,
            max_depth=args.max_depth,
            max_candidates_per_assumption=args.max_candidates_per_assumption,
            retrieval_memory_path=Path(args.retrieval_memory_path) if args.retrieval_memory_path else None,
        )
        payload = {
            "target_theorem": wm.target_theorem,
            "assumptions_total": wm.assumptions_total,
            "grounded_count": wm.grounded_count,
            "reward": wm.reward,
            "elapsed_s": wm.elapsed_s,
            "actions_taken": wm.actions_taken,
            "failure_reasons": wm.failure_reasons,
        }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
