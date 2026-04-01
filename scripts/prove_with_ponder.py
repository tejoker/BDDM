#!/usr/bin/env python3
"""Run ponder-loop tactic search directly against LeanDojo proof states."""

from __future__ import annotations

import argparse
import logging
import multiprocessing as mp
import os
import re
import shutil
import sys
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Any

from dotenv import load_dotenv

# Ensure sibling script imports work when invoked from project root.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from git import Repo as GitRepo
except ModuleNotFoundError:  # GitPython is optional
    GitRepo = None  # type: ignore[assignment]
try:
    from mistralai import Mistral
except ImportError:
    from mistralai.client import Mistral  # type: ignore[no-redef]

try:
    from lean_dojo import Dojo, LeanGitRepo, Theorem
    from lean_dojo.interaction.dojo import LeanError, ProofFinished, ProofGivenUp, TacticState

    _USE_LEAN_DOJO = True
except ModuleNotFoundError:
    try:
        from lean_repl_dojo import REPLDojo as Dojo
        from lean_repl_dojo import LeanError, ProofFinished, ProofGivenUp, TacticState

        LeanGitRepo = Any  # type: ignore[assignment]
        Theorem = Any  # type: ignore[assignment]
        _USE_LEAN_DOJO = False
        _DOJO_BACKEND_AVAILABLE = True
    except ModuleNotFoundError:
        Dojo = None  # type: ignore[assignment]
        LeanGitRepo = Any  # type: ignore[assignment]
        Theorem = Any  # type: ignore[assignment]
        LeanError = RuntimeError  # type: ignore[assignment]
        ProofFinished = type("ProofFinished", (), {})  # type: ignore[assignment]
        ProofGivenUp = type("ProofGivenUp", (), {})  # type: ignore[assignment]
        TacticState = type("TacticState", (), {})  # type: ignore[assignment]
        _USE_LEAN_DOJO = False
        _DOJO_BACKEND_AVAILABLE = False
else:
    _DOJO_BACKEND_AVAILABLE = True

from ponder_loop import (
    generate_full_proof_draft,
    load_premise_context,
    repair_full_proof_draft,
    run_ponder_loop,
)

logger = logging.getLogger(__name__)

_SNAPSHOT_CACHE: dict[str, tuple[Path, Path]] = {}
_NAME_VALIDATION_CACHE: dict[tuple[str, str], bool] = {}
_TACTIC_NAME_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)+)\b")
_TACTIC_KEYWORDS = {
    "by",
    "exact",
    "apply",
    "refine",
    "intro",
    "intros",
    "rw",
    "simp",
    "simpa",
    "at",
    "with",
    "fun",
    "have",
    "show",
    "constructor",
    "cases",
    "induction",
    "where",
}


def _ensure_lake_on_path() -> None:
    """Best-effort PATH fix for non-interactive shells missing elan bin dir."""
    if shutil.which("lake") is not None:
        return

    candidates = [
        str(Path.home() / ".elan" / "bin"),
        str(Path.home() / ".local" / "bin"),
        "/usr/local/bin",
    ]
    current = os.environ.get("PATH", "")
    parts = current.split(":") if current else []

    changed = False
    for cand in candidates:
        if cand and cand not in parts and Path(cand).exists():
            parts.append(cand)
            changed = True

    if changed:
        os.environ["PATH"] = ":".join(parts)


_ensure_lake_on_path()


@dataclass
class StepRecord:
    step: int
    attempt: int
    tactic: str
    model_turns: int
    result: str
    detail: str = ""


@dataclass
class DifficultyEstimate:
    level: str
    score: float
    goals: int
    state_chars: int
    hypotheses: int


def _split_draft_into_tactics(draft: str) -> list[str]:
    lines = [ln.rstrip() for ln in draft.splitlines()]
    tactics: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("--"):
            continue
        if stripped in {"by", "begin", "end"}:
            continue
        tactics.append(stripped)
    return tactics


def _execute_draft(
    *,
    dojo: Any,
    initial_state: TacticState,
    draft: str,
    round_idx: int,
) -> tuple[bool, TacticState, list[StepRecord], str]:
    """Execute draft tactics in sequence and return structured error feedback."""
    state = initial_state
    records: list[StepRecord] = []
    tactics = _split_draft_into_tactics(draft)

    if not tactics:
        msg = "Draft contains no executable tactics"
        records.append(
            StepRecord(
                step=round_idx,
                attempt=1,
                tactic="",
                model_turns=1,
                result="lean-error",
                detail=msg,
            )
        )
        return False, state, records, "line=1; message=empty draft"

    for idx, tactic in enumerate(tactics, start=1):
        outcome = dojo.run_tac(state, tactic)

        if isinstance(outcome, TacticState):
            records.append(
                StepRecord(
                    step=round_idx,
                    attempt=idx,
                    tactic=tactic,
                    model_turns=1,
                    result="state-advanced",
                    detail=f"goals={outcome.num_goals}",
                )
            )
            state = outcome
            continue

        if isinstance(outcome, ProofFinished):
            records.append(
                StepRecord(
                    step=round_idx,
                    attempt=idx,
                    tactic=tactic,
                    model_turns=1,
                    result="proof-finished",
                    detail=(outcome.message or ""),
                )
            )
            return True, state, records, "proof-finished"

        if isinstance(outcome, LeanError):
            err = outcome.error.strip()
            detail = f"line={idx}; message={err}"
            records.append(
                StepRecord(
                    step=round_idx,
                    attempt=idx,
                    tactic=tactic,
                    model_turns=1,
                    result="lean-error",
                    detail=detail,
                )
            )
            return False, state, records, detail

        if isinstance(outcome, ProofGivenUp):
            detail = f"line={idx}; message=proof-given-up"
            records.append(
                StepRecord(
                    step=round_idx,
                    attempt=idx,
                    tactic=tactic,
                    model_turns=1,
                    result="proof-given-up",
                    detail=detail,
                )
            )
            return False, state, records, detail

        detail = f"line={idx}; message=unknown outcome {type(outcome).__name__}"
        records.append(
            StepRecord(
                step=round_idx,
                attempt=idx,
                tactic=tactic,
                model_turns=1,
                result="unknown-outcome",
                detail=detail,
            )
        )
        return False, state, records, detail

    return False, state, records, "line=end; message=draft exhausted without finishing proof"


def extract_tactic_theorem_names(tactic: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for name in _TACTIC_NAME_RE.findall(tactic):
        head = name.split(".", 1)[0].lower()
        if head in _TACTIC_KEYWORDS:
            continue
        if name in seen:
            continue
        seen.add(name)
        found.append(name)
    return found


def validate_lean_name(name: str, project_root: Path) -> bool:
    """Return True if `#check <name>` succeeds via lake env lean."""
    key = (str(project_root.resolve()), name)
    if key in _NAME_VALIDATION_CACHE:
        return _NAME_VALIDATION_CACHE[key]

    import uuid
    tmp_path = project_root / "Desol" / f"_tmp_namecheck_{uuid.uuid4().hex[:8]}.lean"
    lean_src = "import Desol.SDE.Basic\n\n#check @" + name + "\n"
    try:
        tmp_path.write_text(lean_src, encoding="utf-8")
        lake_bin = shutil.which("lake") or os.path.expanduser("~/.elan/bin/lake")
        proc = subprocess.run(
            [lake_bin, "env", "lean", str(tmp_path)],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        combined = (proc.stderr or "") + (proc.stdout or "")
        ok = proc.returncode == 0 and "error:" not in combined
    except Exception:
        ok = False
    finally:
        tmp_path.unlink(missing_ok=True)
    _NAME_VALIDATION_CACHE[key] = ok
    return ok


def _create_snapshot_repo(project_root: Path) -> tuple[Path, Path]:
    """Create a temporary committed snapshot when the source repo has no commits."""
    tmp_root = Path(tempfile.mkdtemp(prefix="desol-dojo-snapshot-"))
    snapshot_repo = tmp_root / "repo"

    def _ignore(_src: str, names: list[str]) -> set[str]:
        ignored = {".lake", "__pycache__", ".venv", ".git", ".mypy_cache", ".pytest_cache"}
        return {n for n in names if n in ignored}

    shutil.copytree(project_root, snapshot_repo, ignore=_ignore)

    def _git(args: list[str]) -> None:
        subprocess.run(
            ["git", *args],
            cwd=snapshot_repo,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    try:
        if GitRepo is not None:
            repo = GitRepo.init(snapshot_repo)
            with repo.config_writer() as cw:
                cw.set_value("user", "name", "desol-bot")
                cw.set_value("user", "email", "desol-bot@example.local")
            repo.git.add(A=True)
            repo.index.commit("Temporary snapshot commit for LeanDojo")
        else:
            _git(["init"])
            _git(["config", "user.name", "desol-bot"])
            _git(["config", "user.email", "desol-bot@example.local"])
            _git(["add", "-A"])
            _git(["commit", "-m", "Temporary snapshot commit for LeanDojo"])
    except Exception as exc:
        raise RuntimeError(f"Failed to initialize temporary git snapshot: {exc}") from exc
    return tmp_root, snapshot_repo


def _repo_has_commit(project_root: Path) -> bool:
    if GitRepo is not None:
        try:
            _ = GitRepo(project_root).head.commit.hexsha
            return True
        except Exception:
            return False

    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _prepare_leandojo_repo(project_root: Path) -> tuple[Any, Path | None]:
    """Return LeanGitRepo and optional temp dir to clean up."""
    if not _USE_LEAN_DOJO:
        return None, None

    try:
        if _repo_has_commit(project_root):
            return LeanGitRepo.from_path(project_root), None
    except Exception:
        pass

    try:
        cache_key = str(project_root.resolve())
        if cache_key in _SNAPSHOT_CACHE:
            cached_tmp_root, cached_repo = _SNAPSHOT_CACHE[cache_key]
            if cached_repo.exists() and cached_tmp_root.exists():
                # Reuse cached snapshot to avoid repeated copytree+git init overhead.
                return LeanGitRepo.from_path(cached_repo), None

        tmp_root, snapshot_repo = _create_snapshot_repo(project_root)
        _SNAPSHOT_CACHE[cache_key] = (tmp_root, snapshot_repo)
        return LeanGitRepo.from_path(snapshot_repo), tmp_root
    except Exception as exc:
        raise RuntimeError(f"Failed preparing LeanDojo git repository: {exc}") from exc


def _open_dojo(
    *,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    dojo_timeout: int,
) -> tuple[Any, Path | None]:
    """Create the right dojo context manager for the active backend."""
    if not _DOJO_BACKEND_AVAILABLE:
        raise RuntimeError(
            "No proof backend available: install lean_dojo or provide scripts/lean_repl_dojo.py"
        )

    if _USE_LEAN_DOJO:
        try:
            repo, tmp_root = _prepare_leandojo_repo(project_root)
            theorem = Theorem(repo, file_path, theorem_name)
            return Dojo(theorem, timeout=dojo_timeout), tmp_root
        except Exception as e:
            import traceback
            logger.warning(f"LeanDojo initialization failed: {e}\n{traceback.format_exc()}")
            if "git" in str(e).lower() or "lake" in str(e).lower():
                msg = (
                    f"Backend initialization failed during toolchain setup:\n"
                    f"  Error: {e}\n"
                    f"  This typically means:\n"
                    f"    - git/lake toolchain issue or mathlib4 unreachable\n"
                    f"    - Lean version mismatch with project\n"
                    f"  Fallback: Use model-only mode (--mode mcts-draft without backend)\n"
                )
                raise RuntimeError(msg) from e
            raise

    return Dojo(project_root, file_path, theorem_name, timeout=dojo_timeout), None


def _estimate_theorem_difficulty(
    *,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    dojo_timeout: int,
) -> DifficultyEstimate:
    """Estimate theorem search difficulty from the initial proof state."""
    dojo_ctx, tmp_root = _open_dojo(
        project_root=project_root,
        file_path=file_path,
        theorem_name=theorem_name,
        dojo_timeout=min(dojo_timeout, 180),
    )
    try:
        with dojo_ctx as (_dojo, state):
            state_text = getattr(state, "pp", "") or ""
            goals = int(getattr(state, "num_goals", 0) or state_text.count("⊢") or 1)
            state_chars = len(state_text)
            hypotheses = sum(
                1
                for line in state_text.splitlines()
                if line.strip() and not line.strip().startswith("⊢")
            )

            goals_factor = min(goals / 6.0, 1.0)
            chars_factor = min(state_chars / 2200.0, 1.0)
            hyps_factor = min(hypotheses / 35.0, 1.0)
            score = (0.55 * goals_factor) + (0.25 * chars_factor) + (0.20 * hyps_factor)

            if score >= 0.67:
                level = "hard"
            elif score >= 0.36:
                level = "medium"
            else:
                level = "easy"

            return DifficultyEstimate(
                level=level,
                score=min(max(score, 0.0), 1.0),
                goals=max(goals, 1),
                state_chars=state_chars,
                hypotheses=hypotheses,
            )
    except Exception:
        # Conservative fallback when dojo pre-estimation fails.
        return DifficultyEstimate(
            level="medium",
            score=0.5,
            goals=1,
            state_chars=0,
            hypotheses=0,
        )
    finally:
        if tmp_root is not None:
            shutil.rmtree(tmp_root, ignore_errors=True)


def _adapt_mcts_params(
    *,
    profile: str,
    base_workers: int,
    iterations: int,
    repair_variants: int,
    max_depth: int,
    difficulty: DifficultyEstimate,
) -> tuple[int, int, int, int, str]:
    """Return adapted (workers, iterations, variants, depth, rationale)."""
    workers = max(1, base_workers)
    iters = max(1, iterations)
    variants = max(1, repair_variants)
    depth = max(1, max_depth)

    if profile == "throughput":
        workers = max(workers, min(mp.cpu_count(), workers + 1))
        depth = max(2, depth - 1)
        rationale = "throughput"
        return workers, iters, variants, depth, rationale

    if profile == "depth":
        workers = max(1, workers // 2)
        iters = max(iters, iters * 2)
        variants = max(variants, variants + 1)
        depth = max(depth, depth + 2)
        rationale = "depth"
        return workers, iters, variants, depth, rationale

    if profile != "hybrid":
        return workers, iters, variants, depth, "fixed"

    # Hybrid: easy => breadth, hard => depth.
    if difficulty.level == "easy":
        workers = max(workers, min(mp.cpu_count(), workers + 1))
        iters = max(iters, workers * 2)
        variants = max(1, variants - 1)
        depth = max(2, depth - 1)
        rationale = "hybrid-easy"
    elif difficulty.level == "hard":
        workers = max(1, workers // 2)
        iters = max(iters * 2, workers * 6)
        variants = max(variants + 1, 3)
        depth = max(depth + 2, 5)
        rationale = "hybrid-hard"
    else:
        iters = max(iters, workers * 3)
        rationale = "hybrid-medium"

    return workers, iters, variants, depth, rationale


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
    premise_context: str = "",
    retrieval_index_path: str = "",
    retrieval_top_k: int = 12,
) -> tuple[bool, list[StepRecord], str]:
    dojo_ctx, tmp_root = _open_dojo(
        project_root=project_root,
        file_path=file_path,
        theorem_name=theorem_name,
        dojo_timeout=dojo_timeout,
    )

    records: list[StepRecord] = []

    try:
        with dojo_ctx as (dojo, state):
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
                            premise_context=premise_context,
                            retrieval_index_path=retrieval_index_path,
                            retrieval_top_k=retrieval_top_k,
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

                    if re.fullmatch(r"\s*sorry\s*", tactic):
                        err = "Tactic is 'sorry': proof is incomplete. Propose a real tactic."
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

                    invalid_name = None
                    for candidate_name in extract_tactic_theorem_names(tactic):
                        if not validate_lean_name(candidate_name, project_root):
                            invalid_name = candidate_name
                            break

                    if invalid_name is not None:
                        err = (
                            f"The theorem {invalid_name} does not exist in Mathlib. "
                            "Use a verified name."
                        )
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


def prove_with_full_draft_repair(
    *,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    client: Mistral,
    model: str,
    repair_rounds: int = 5,
    temperature: float = 0.2,
    dojo_timeout: int = 600,
    premise_context: str = "",
    retrieval_index_path: str = "",
    retrieval_top_k: int = 12,
    informal_proof_hint: str = "",
) -> tuple[bool, list[StepRecord], str]:
    dojo_ctx, tmp_root = _open_dojo(
        project_root=project_root,
        file_path=file_path,
        theorem_name=theorem_name,
        dojo_timeout=dojo_timeout,
    )
    records: list[StepRecord] = []

    try:
        with dojo_ctx as (dojo, state):
            if not isinstance(state, TacticState):
                return False, records, f"Unexpected initial state type: {type(state).__name__}"

            current_draft = generate_full_proof_draft(
                lean_state=state.pp,
                client=client,
                model=model,
                informal_proof_hint=informal_proof_hint,
                temperature=temperature,
                premise_context=premise_context,
                retrieval_index_path=retrieval_index_path,
                retrieval_top_k=retrieval_top_k,
            )

            for round_idx in range(1, repair_rounds + 1):
                solved, _new_state, round_records, error_feedback = _execute_draft(
                    dojo=dojo,
                    initial_state=state,
                    draft=current_draft,
                    round_idx=round_idx,
                )
                records.extend(round_records)

                if solved:
                    return True, records, f"Proof finished in round {round_idx}"

                if round_idx == repair_rounds:
                    return False, records, (
                        f"Failed after repair_rounds={repair_rounds}; "
                        f"last_error={error_feedback}"
                    )

                current_draft = repair_full_proof_draft(
                    lean_state=state.pp,
                    current_draft=current_draft,
                    error_feedback=error_feedback,
                    client=client,
                    model=model,
                    informal_proof_hint=informal_proof_hint,
                    temperature=temperature,
                    premise_context=premise_context,
                    retrieval_index_path=retrieval_index_path,
                    retrieval_top_k=retrieval_top_k,
                )

            return False, records, "Exhausted repair loop"
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
    parser.add_argument(
        "--mode",
        choices=["tactic", "full-draft", "mcts-draft"],
        default="tactic",
        help="Proof mode: tactic-by-tactic, full draft + repair loop, or draft-level MCTS repair",
    )
    parser.add_argument(
        "--repair-rounds",
        type=int,
        default=5,
        help="Number of full-draft repair rounds (full-draft mode only)",
    )
    parser.add_argument(
        "--informal-proof-hint",
        default="",
        help="Optional informal proof hint text injected at draft generation",
    )
    parser.add_argument(
        "--informal-proof-hint-file",
        default="",
        help="Path to a file containing informal proof hint text",
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
    parser.add_argument(
        "--mcts-iterations",
        type=int,
        default=12,
        help="Number of draft-MCTS selection/expansion iterations (mcts-draft mode)",
    )
    parser.add_argument(
        "--mcts-repair-variants",
        type=int,
        default=3,
        help="Repair variants generated per draft-MCTS node (mcts-draft mode)",
    )
    parser.add_argument(
        "--mcts-max-depth",
        type=int,
        default=5,
        help="Maximum draft-MCTS depth in repair rounds (mcts-draft mode)",
    )
    parser.add_argument(
        "--mcts-exploration-c",
        type=float,
        default=1.4,
        help="UCB exploration constant for draft-MCTS (mcts-draft mode)",
    )
    parser.add_argument(
        "--mcts-parallel-workers",
        type=int,
        default=0,
        help="Parallel draft-MCTS workers; 0 selects automatically from --mcts-cpu-target",
    )
    parser.add_argument(
        "--mcts-cpu-target",
        type=float,
        default=0.8,
        help="Target CPU fraction for auto worker selection (mcts-draft mode)",
    )
    parser.add_argument(
        "--mcts-profile",
        choices=["fixed", "throughput", "depth", "hybrid"],
        default="hybrid",
        help="Draft-MCTS tuning profile (mcts-draft mode)",
    )
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
    informal_hint = args.informal_proof_hint.strip()
    if args.informal_proof_hint_file:
        informal_hint = Path(args.informal_proof_hint_file).read_text(encoding="utf-8").strip()

    premise_context = ""
    if args.premise_file:
        premise_context = load_premise_context(
            args.premise_file,
            namespace_filter=args.premise_namespace,
        )

    client = Mistral(api_key=api_key)

    if args.mode == "full-draft":
        ok, records, summary = prove_with_full_draft_repair(
            project_root=project_root,
            file_path=file_path,
            theorem_name=args.theorem,
            client=client,
            model=model,
            repair_rounds=args.repair_rounds,
            temperature=args.temperature,
            dojo_timeout=args.dojo_timeout,
            premise_context=premise_context,
            retrieval_index_path=args.retrieval_index,
            retrieval_top_k=args.retrieval_top_k,
            informal_proof_hint=informal_hint,
        )
    elif args.mode == "mcts-draft":
        from mcts_search import run_draft_mcts, run_draft_mcts_parallel

        cpu_target = max(0.1, min(args.mcts_cpu_target, 1.0))
        auto_workers = max(1, int(mp.cpu_count() * cpu_target))
        worker_count = args.mcts_parallel_workers if args.mcts_parallel_workers > 0 else auto_workers

        difficulty = _estimate_theorem_difficulty(
            project_root=project_root,
            file_path=file_path,
            theorem_name=args.theorem,
            dojo_timeout=args.dojo_timeout,
        )
        tuned_workers, tuned_iterations, tuned_variants, tuned_depth, tuning_reason = _adapt_mcts_params(
            profile=args.mcts_profile,
            base_workers=worker_count,
            iterations=args.mcts_iterations,
            repair_variants=args.mcts_repair_variants,
            max_depth=args.mcts_max_depth,
            difficulty=difficulty,
        )

        if tuned_workers > 1:
            ok, raw_records, summary, worker_results = run_draft_mcts_parallel(
                project_root=project_root,
                file_path=file_path,
                theorem_name=args.theorem,
                api_key=api_key,
                model=model,
                total_iterations=tuned_iterations,
                num_workers=tuned_workers,
                repair_variants=tuned_variants,
                max_depth=tuned_depth,
                exploration_c=args.mcts_exploration_c,
                temperature=args.temperature,
                dojo_timeout=args.dojo_timeout,
                premise_context=premise_context,
                retrieval_index_path=args.retrieval_index,
                retrieval_top_k=args.retrieval_top_k,
                informal_proof_hint=informal_hint,
            )
            worker_summary_bits = [
                (
                    f"worker {w.worker_id}: "
                    f"ok={w.ok}, best_value={w.best_value:.3f}, "
                    f"records={len(w.records)}"
                    + (f", error={w.error}" if w.error else "")
                )
                for w in sorted(worker_results, key=lambda x: x.worker_id)
            ]
            if worker_summary_bits:
                summary = summary + " | " + " ; ".join(worker_summary_bits)
        else:
            ok, raw_records, summary = run_draft_mcts(
                project_root=project_root,
                file_path=file_path,
                theorem_name=args.theorem,
                client=client,
                model=model,
                iterations=tuned_iterations,
                repair_variants=tuned_variants,
                max_depth=tuned_depth,
                exploration_c=args.mcts_exploration_c,
                temperature=args.temperature,
                dojo_timeout=args.dojo_timeout,
                premise_context=premise_context,
                retrieval_index_path=args.retrieval_index,
                retrieval_top_k=args.retrieval_top_k,
                informal_proof_hint=informal_hint,
            )
        summary = (
            summary
            + " | "
            + (
                f"profile={args.mcts_profile} ({tuning_reason}), "
                f"difficulty={difficulty.level}:{difficulty.score:.2f}, "
                f"goals={difficulty.goals}, state_chars={difficulty.state_chars}, hyps={difficulty.hypotheses}, "
                f"workers={tuned_workers}, iterations={tuned_iterations}, "
                f"variants={tuned_variants}, depth={tuned_depth}"
            )
        )
        records = [
            StepRecord(
                step=int(r.get("step", 0)),
                attempt=int(r.get("attempt", 0)),
                tactic=str(r.get("tactic", "")),
                model_turns=int(r.get("model_turns", 0)),
                result=str(r.get("result", "")),
                detail=str(r.get("detail", "")),
            )
            for r in raw_records
        ]
    else:
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
            premise_context=premise_context,
            retrieval_index_path=args.retrieval_index,
            retrieval_top_k=args.retrieval_top_k,
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
