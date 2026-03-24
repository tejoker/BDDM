#!/usr/bin/env python3
"""Run ponder-loop tactic search directly against LeanDojo proof states."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from git import Repo as GitRepo
from lean_dojo import Dojo, LeanGitRepo, Theorem
from lean_dojo.interaction.dojo import LeanError, ProofFinished, ProofGivenUp, TacticState
from mistralai.client import Mistral

# Ensure sibling script imports work when invoked from project root.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from ponder_loop import run_ponder_loop

_SNAPSHOT_CACHE: dict[str, tuple[Path, Path]] = {}


@dataclass
class StepRecord:
    step: int
    attempt: int
    tactic: str
    model_turns: int
    result: str
    detail: str = ""


def _create_snapshot_repo(project_root: Path) -> tuple[Path, Path]:
    """Create a temporary committed snapshot when the source repo has no commits."""
    tmp_root = Path(tempfile.mkdtemp(prefix="desol-dojo-snapshot-"))
    snapshot_repo = tmp_root / "repo"

    def _ignore(_src: str, names: list[str]) -> set[str]:
        ignored = {".lake", "__pycache__", ".venv", ".git", ".mypy_cache", ".pytest_cache"}
        return {n for n in names if n in ignored}

    shutil.copytree(project_root, snapshot_repo, ignore=_ignore)

    repo = GitRepo.init(snapshot_repo)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "desol-bot")
        cw.set_value("user", "email", "desol-bot@example.local")
    repo.git.add(A=True)
    repo.index.commit("Temporary snapshot commit for LeanDojo")
    return tmp_root, snapshot_repo


def _prepare_leandojo_repo(project_root: Path) -> tuple[LeanGitRepo, Path | None]:
    """Return LeanGitRepo and optional temp dir to clean up."""
    try:
        _ = GitRepo(project_root).head.commit.hexsha
        return LeanGitRepo.from_path(project_root), None
    except Exception:
        cache_key = str(project_root.resolve())
        if cache_key in _SNAPSHOT_CACHE:
            cached_tmp_root, cached_repo = _SNAPSHOT_CACHE[cache_key]
            if cached_repo.exists() and cached_tmp_root.exists():
                # Reuse cached snapshot to avoid repeated copytree+git init overhead.
                return LeanGitRepo.from_path(cached_repo), None

        tmp_root, snapshot_repo = _create_snapshot_repo(project_root)
        _SNAPSHOT_CACHE[cache_key] = (tmp_root, snapshot_repo)
        return LeanGitRepo.from_path(snapshot_repo), tmp_root


def prove_with_ponder(
    *,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    client: Mistral,
    model: str,
    max_steps: int = 20,
    max_attempts_per_state: int = 3,
    ponder_max_turns: int = 0,
    ponder_min_act_turns: int = 2,
    ponder_max_act_turns: int = 8,
    confidence_threshold: float = 0.9,
    temperature: float = 0.2,
    dojo_timeout: int = 600,
) -> tuple[bool, list[StepRecord], str]:
    repo, tmp_root = _prepare_leandojo_repo(project_root)
    theorem = Theorem(repo, file_path, theorem_name)

    records: list[StepRecord] = []

    try:
        with Dojo(theorem, timeout=dojo_timeout) as (dojo, state):
            if not isinstance(state, TacticState):
                return False, records, f"Unexpected initial state type: {type(state).__name__}"

            for step in range(1, max_steps + 1):
                current_state = state
                failed_attempts: list[tuple[str, str]] = []

                for attempt in range(1, max_attempts_per_state + 1):
                    state_context = current_state.pp
                    if failed_attempts:
                        lines = ["Previous failed attempts on this exact state:"]
                        for i, (tac, err) in enumerate(failed_attempts, start=1):
                            lines.append(f"{i}. tactic: {tac}")
                            lines.append(f"   lean_error: {err}")
                        lines.append("Propose a different tactic.")
                        state_context = state_context + "\n\n" + "\n".join(lines)

                    try:
                        ponder_result = run_ponder_loop(
                            lean_state=state_context,
                            client=client,
                            model=model,
                            max_turns=(ponder_max_turns if ponder_max_turns > 0 else None),
                            temperature=temperature,
                            min_act_turns=ponder_min_act_turns,
                            max_act_turns=ponder_max_act_turns,
                            confidence_threshold=confidence_threshold,
                        )
                    except TimeoutError as exc:
                        records.append(
                            StepRecord(
                                step=step,
                                attempt=attempt,
                                tactic="",
                                model_turns=0,
                                result="model-timeout",
                                detail=str(exc),
                            )
                        )
                        failed_attempts.append(("<no tactic>", f"ponder timeout: {exc}"))
                        continue
                    tactic = ponder_result.tactic.strip()

                    outcome = dojo.run_tac(current_state, tactic)

                    if isinstance(outcome, TacticState):
                        records.append(
                            StepRecord(
                                step=step,
                                attempt=attempt,
                                tactic=tactic,
                                model_turns=ponder_result.turns,
                                result="state-advanced",
                                detail=f"goals={outcome.num_goals}",
                            )
                        )
                        state = outcome
                        break

                    if isinstance(outcome, ProofFinished):
                        records.append(
                            StepRecord(
                                step=step,
                                attempt=attempt,
                                tactic=tactic,
                                model_turns=ponder_result.turns,
                                result="proof-finished",
                                detail=(outcome.message or ""),
                            )
                        )
                        return True, records, "Proof finished"

                    if isinstance(outcome, LeanError):
                        err = outcome.error.strip()
                        records.append(
                            StepRecord(
                                step=step,
                                attempt=attempt,
                                tactic=tactic,
                                model_turns=ponder_result.turns,
                                result="lean-error",
                                detail=err,
                            )
                        )
                        failed_attempts.append((tactic, err))
                        continue

                    if isinstance(outcome, ProofGivenUp):
                        records.append(
                            StepRecord(
                                step=step,
                                attempt=attempt,
                                tactic=tactic,
                                model_turns=ponder_result.turns,
                                result="proof-given-up",
                            )
                        )
                        return False, records, "Model gave up the proof"

                    records.append(
                        StepRecord(
                            step=step,
                            attempt=attempt,
                            tactic=tactic,
                            model_turns=ponder_result.turns,
                            result="unknown-outcome",
                            detail=type(outcome).__name__,
                        )
                    )
                    return False, records, f"Unknown LeanDojo outcome: {type(outcome).__name__}"
                else:
                    return (
                        False,
                        records,
                        f"Failed to advance proof state after {max_attempts_per_state} attempts at step {step}",
                    )

            return False, records, f"Reached max_steps={max_steps} without finishing proof"
    finally:
        if tmp_root is not None:
            shutil.rmtree(tmp_root, ignore_errors=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Drive Lean proof search with ponder loop + LeanDojo")
    parser.add_argument("--project-root", default=".", help="Path to Lean git repo root")
    parser.add_argument("--file", required=True, help="Lean file path relative to project root")
    parser.add_argument("--theorem", required=True, help="Fully-qualified theorem name")
    parser.add_argument("--model", default="", help="Mistral model (defaults to MISTRAL_MODEL)")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--max-attempts-per-state", type=int, default=3)
    parser.add_argument(
        "--ponder-max-turns",
        type=int,
        default=0,
        help="Fixed ACT budget per tactic proposal; 0 means adaptive",
    )
    parser.add_argument("--ponder-min-act-turns", type=int, default=2)
    parser.add_argument("--ponder-max-act-turns", type=int, default=8)
    parser.add_argument("--confidence-threshold", type=float, default=0.9)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--dojo-timeout", type=int, default=600)
    return parser


def main() -> int:
    load_dotenv()
    parser = _build_parser()
    args = parser.parse_args()

    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        print("[fail] MISTRAL_API_KEY is not set")
        return 1

    model = args.model.strip() or os.getenv("MISTRAL_MODEL", "labs-leanstral-2603").strip()
    if not model:
        print("[fail] no model configured")
        return 1

    project_root = Path(args.project_root).resolve()
    file_path = Path(args.file)

    client = Mistral(api_key=api_key)

    ok, records, summary = prove_with_ponder(
        project_root=project_root,
        file_path=file_path,
        theorem_name=args.theorem,
        client=client,
        model=model,
        max_steps=args.max_steps,
        max_attempts_per_state=args.max_attempts_per_state,
        ponder_max_turns=args.ponder_max_turns,
        ponder_min_act_turns=args.ponder_min_act_turns,
        ponder_max_act_turns=args.ponder_max_act_turns,
        confidence_threshold=args.confidence_threshold,
        temperature=args.temperature,
        dojo_timeout=args.dojo_timeout,
    )

    for r in records:
        detail = f" | {r.detail}" if r.detail else ""
        print(
            f"[step {r.step} attempt {r.attempt}] "
            f"tactic={r.tactic!r} | model_turns={r.model_turns} | result={r.result}{detail}"
        )

    if ok:
        print(f"[ok] {summary}")
        return 0

    print(f"[fail] {summary}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
