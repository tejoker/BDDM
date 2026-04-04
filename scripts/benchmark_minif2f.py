#!/usr/bin/env python3
"""Pass@k evaluation of DESol on the miniF2F benchmark.

miniF2F is the standard benchmark for automated theorem proving in Lean 4.
This script evaluates the ponder-loop prover (Phase 2) and reports:
  - pass@1  — did the first attempt succeed?
  - pass@k  — did any of k independent attempts succeed?

Baseline numbers to beat (from the literature, as of early 2025):
  - GPT-4 + best-first search (ReProver):  ~27% pass@1 on test
  - Hypertree Proof Search:               ~33% pass@1 on test
  - LLM-Step (Llama):                     ~22% pass@1 on test

Usage
-----
# Quick smoke test (first 5 problems, 1 attempt each):
  python3 scripts/benchmark_minif2f.py --split test --n-problems 5 --k 1

# Full evaluation (512 test problems, 10 attempts each):
  python3 scripts/benchmark_minif2f.py --split test --k 10

# Evaluate on valid split:
  python3 scripts/benchmark_minif2f.py --split valid --k 5 --n-problems 50

Output
------
Results are written to output/benchmark_minif2f_<split>_<timestamp>.json and a
summary is printed to stdout.

Notes
-----
miniF2F problems are fetched from the HuggingFace dataset
``Matharena/minif2f_lean4`` (Lean 4 statements).  Each problem is a single
theorem statement; the prover must produce a complete proof.

The backend requires a working Lean 4 / LeanDojo installation OR falls back to
model-only mode (which cannot actually verify proofs — results will be
incomplete in that case).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# HuggingFace dataset for miniF2F Lean 4.
_HF_DATASET = "cat-searcher/minif2f-lean4"

# Known pass@1 baselines for comparison (test split).
BASELINES = {
    "ReProver (GPT-4 + best-first)": 0.273,
    "HyperTree Proof Search (Meta)": 0.330,
    "LLM-Step (Llama-2)": 0.220,
    "Aesop (tactic, no LLM)": 0.040,
}


def _categorize_error(error_text: str) -> str:
    """Map raw error text to a compact diagnostic category."""
    e = (error_text or "").lower()
    if not e:
        return "none"
    if "service unavailable" in e or "status 500" in e or "internal_server_error" in e:
        return "api_unavailable"
    if "theorem '" in e and "not found in source" in e:
        return "theorem_name_parse"
    if "timeout" in e:
        return "lean_timeout"
    if "unsolved goals" in e:
        return "unsolved_goals"
    if "invalid field" in e or "unknown constant" in e:
        return "invalid_symbol"
    if "tactic" in e:
        return "tactic_error"
    if "no proof found" in e:
        return "search_exhausted"
    return "other"


@dataclass
class ProblemResult:
    problem_id: str
    informal_name: str
    split: str
    lean_statement: str
    lean_header: str = ""
    attempts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def solved(self) -> bool:
        return any(a.get("success") for a in self.attempts)

    @property
    def best_proof(self) -> str | None:
        for a in self.attempts:
            if a.get("success"):
                return a.get("proof")
        return None


@dataclass
class BenchmarkResult:
    split: str
    n_problems: int
    k: int
    pass_at_1: float
    pass_at_k: float
    total_solved: int
    total_attempts: int
    elapsed_seconds: float
    timestamp: str
    per_problem: list[dict[str, Any]] = field(default_factory=list)
    baselines: dict[str, float] = field(default_factory=dict)
    # Normalisation metrics — needed for apples-to-apples comparison with literature
    total_api_calls: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    seconds_per_problem: float = 0.0
    api_calls_per_problem: float = 0.0

    def summary_lines(self) -> list[str]:
        lines = [
            f"miniF2F benchmark — split={self.split} k={self.k} n={self.n_problems}",
            f"  pass@1 : {self.pass_at_1:.1%}",
            f"  pass@{self.k} : {self.pass_at_k:.1%}  ({self.total_solved}/{self.n_problems} solved)",
            f"  elapsed: {self.elapsed_seconds:.1f}s  ({self.seconds_per_problem:.1f}s/problem)",
            f"  api_calls: {self.total_api_calls}  ({self.api_calls_per_problem:.1f}/problem)",
        ]
        if self.total_tokens_in or self.total_tokens_out:
            lines.append(
                f"  tokens: {self.total_tokens_in} in / {self.total_tokens_out} out"
            )
        lines += ["", "Baselines (pass@1, test split):"]
        for name, val in BASELINES.items():
            marker = " <-- we beat this!" if self.pass_at_1 > val else ""
            lines.append(f"  {val:.1%}  {name}{marker}")
        return lines


def _load_minif2f(split: str, n_problems: int | None = None) -> list[dict[str, Any]]:
    """Load miniF2F problems from HuggingFace.

    Returns a list of dicts with keys: id, informal_name, formal_statement, split.
    """
    try:
        from datasets import load_dataset  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "pip install datasets to load miniF2F from HuggingFace"
        ) from exc

    logger.info("Loading miniF2F (%s split) from HuggingFace ...", split)
    ds = load_dataset(_HF_DATASET, split=split)
    problems = [dict(row) for row in ds]
    logger.info("Loaded %d problems", len(problems))

    if n_problems is not None and n_problems > 0:
        problems = problems[:n_problems]
        logger.info("Truncated to %d problems (--n-problems)", n_problems)

    return problems


def _extract_lean_statement(row: dict[str, Any]) -> str:
    """Extract the Lean 4 theorem/lemma statement from a miniF2F row.

    Some rows contain commented fallback blocks like:
      -- theorem foo ... := sorry
    In that case we recover the declaration by stripping the `--` prefixes.
    """
    for key in ("formal_statement", "lean4_formal_statement", "statement", "lean_statement"):
        val = row.get(key, "")
        if not val or not isinstance(val, str):
            continue

        s = val.strip()
        low = s.lower()
        if "theorem" in low or "lemma" in low:
            if s.startswith("--"):
                uncommented: list[str] = []
                for line in s.splitlines():
                    stripped = line.lstrip()
                    if stripped.startswith("--"):
                        uncommented.append(stripped[2:].lstrip())
                candidate = "\n".join(uncommented).strip()
                if "theorem" in candidate.lower() or "lemma" in candidate.lower():
                    return candidate
            return s

    return " ".join(str(v) for v in row.values() if isinstance(v, str))


def _extract_header(row: dict[str, Any]) -> str:
    """Extract the import header from a miniF2F row, if present."""
    return str(row.get("header", "")).strip()


def _write_bench_file(
    project_root: Path,
    lean_statement: str,
    lean_header: str = "",
    worker_id: int = 0,
) -> Path:
    """Write a miniF2F problem to a per-worker scratch Lean file.

    Each worker gets its own file (Bench0.lean, Bench1.lean, …) to avoid
    concurrent write conflicts when multiple problems run in parallel.
    """
    stmt = lean_statement.strip()
    stmt = re.sub(r":=\s*(?:by\b.*|sorry\s*)$", "", stmt, flags=re.DOTALL).rstrip()
    stmt = stmt + " := by\n  sorry"

    # miniF2F headers can target a different Mathlib snapshot and break on
    # modern versions. Default to a broad import for compatibility.
    use_dataset_header = os.environ.get("DESOL_USE_MINIF2F_HEADER", "0") == "1"
    header = lean_header if (use_dataset_header and lean_header) else "import Mathlib"
    content = header + "\n\n" + stmt + "\n"
    bench_path = project_root / "Desol" / f"Bench{worker_id}.lean"
    bench_path.write_text(content, encoding="utf-8")
    return bench_path


def _extract_theorem_name(lean_statement: str) -> str:
    """Extract the theorem name from a Lean 4 statement."""
    m = re.search(r"(?:theorem|lemma)\s+([\w']+)", lean_statement)
    return m.group(1) if m else "bench_placeholder"


def _attempt_proof(
    *,
    lean_statement: str,
    lean_header: str = "",
    client: Any,
    model: str,
    attempt_idx: int,
    worker_id: int = 0,
    project_root: Path,
    retrieval_index_path: str = "",
    retrieval_top_k: int = 12,
    max_ponder_rounds: int = 6,
    lean_timeout: int = 90,
    max_api_retries: int = 2,
    mode: str = "ponder",
    mcts_iterations: int = 12,
    mcts_repair_variants: int = 3,
    mcts_max_depth: int = 5,
) -> dict[str, Any]:
    """Run one proof attempt with a real Lean execution loop.

    Architecture:
      1. Write the miniF2F theorem to Desol/Bench.lean.
      2. Open a REPLDojo session to get the real initial proof state.
      3. Loop: call ponder loop with current state → get tactic →
         execute tactic via REPLDojo → advance state or stop.
      4. Return success=True if ProofFinished is returned by REPLDojo.

    Returns a dict with keys: attempt, success, proof, error, elapsed_s.
    """
    import threading
    from ponder_loop import run_ponder_loop

    # Import REPLDojo from the local scripts directory.
    sys.path.insert(0, str(project_root / "scripts"))
    from lean_repl_dojo import REPLDojo, ProofFinished, TacticState, LeanError

    t0 = time.time()
    tactic_history: list[str] = []
    last_lean_error = ""
    failure_stage = "init"

    # State-level MCTS (flat or hierarchical) — real proof states via leanprover-community/repl
    if mode in ("state-mcts", "hierarchical-state"):
        try:
            from mcts_search import run_hierarchical_state_mcts, run_state_mcts
            from premise_retrieval import PremiseRetriever
            premise_ctx = ""
            if retrieval_index_path:
                try:
                    retriever = PremiseRetriever(index_path=retrieval_index_path)
                    entries = retriever.query(lean_statement, top_k=retrieval_top_k)
                    premise_ctx = "\n".join(f"- {e.full_name}" for e in entries)
                except Exception:
                    pass
            _mcts_fn = run_hierarchical_state_mcts if mode == "hierarchical-state" else run_state_mcts
            ok, tactics, summary = _mcts_fn(
                project_root=project_root,
                theorem_statement=lean_statement,
                client=client,
                model=model,
                iterations=mcts_iterations,
                n_tactics=mcts_repair_variants,
                max_depth=mcts_max_depth,
                repl_timeout=float(lean_timeout),
                premise_context=premise_ctx,
                retrieval_index_path=retrieval_index_path,
                retrieval_top_k=retrieval_top_k,
            )
            return {
                "attempt": attempt_idx,
                "success": ok,
                "proof": "\n".join(tactics),
                "error": "" if ok else summary,
                "error_category": _categorize_error(summary) if not ok else "none",
                "elapsed_s": round(time.time() - t0, 2),
                "mode": mode,
                "api_calls": 0,  # not yet tracked in state-mcts
            }
        except Exception as exc:
            return {
                "attempt": attempt_idx,
                "success": False,
                "proof": "",
                "error": str(exc),
                "error_category": _categorize_error(str(exc)),
                "elapsed_s": round(time.time() - t0, 2),
                "mode": mode,
            }

    # mcts-draft and hierarchical are legacy aliases — forward to state-mcts equivalents.
    if mode in ("mcts-draft", "hierarchical"):
        _fwd_mode = "hierarchical-state" if mode == "hierarchical" else "state-mcts"
        return _attempt_proof(
            lean_statement=lean_statement,
            lean_header=lean_header,
            attempt_idx=attempt_idx,
            mode=_fwd_mode,
            client=client,
            model=model,
            project_root=project_root,
            mcts_iterations=mcts_iterations,
            mcts_repair_variants=mcts_repair_variants,
            mcts_max_depth=mcts_max_depth,
            lean_timeout=lean_timeout,
            retrieval_index_path=retrieval_index_path,
            retrieval_top_k=retrieval_top_k,
            worker_id=worker_id,
        )

    try:
        bench_file = _write_bench_file(project_root, lean_statement, lean_header, worker_id)
        theorem_name = _extract_theorem_name(lean_statement)

        with REPLDojo(
            project_root=project_root,
            file_path=bench_file.relative_to(project_root),
            theorem_name=theorem_name,
            timeout=lean_timeout,
        ) as (dojo, state):

            for _round in range(max_ponder_rounds):
                failure_stage = "ponder"
                current_state_text = state.pp if isinstance(state, TacticState) else str(state)

                # Ask ponder loop for the next tactic given the current state.
                last_exc: Exception | None = None
                ponder_result = None
                for retry_idx in range(max_api_retries + 1):
                    try:
                        ponder_result = run_ponder_loop(
                            lean_state=current_state_text,
                            client=client,
                            model=model,
                            max_turns=3,  # cheap inner budget per round
                            retrieval_index_path=retrieval_index_path,
                            retrieval_top_k=retrieval_top_k,
                        )
                        break
                    except Exception as exc:
                        last_exc = exc
                        msg = str(exc)
                        transient = "status 500" in msg.lower() or "service unavailable" in msg.lower()
                        if not transient or retry_idx >= max_api_retries:
                            raise
                        # Exponential backoff for transient API failures.
                        backoff_s = 1.5 * (2 ** retry_idx)
                        logger.warning(
                            "Transient API failure (retry %d/%d): %s",
                            retry_idx + 1,
                            max_api_retries,
                            msg,
                        )
                        time.sleep(backoff_s)

                if ponder_result is None:
                    raise RuntimeError(f"Ponder loop failed without result: {last_exc}")

                tactic = getattr(ponder_result, "tactic", "").strip()
                if not tactic:
                    break

                tactic_history.append(tactic)
                failure_stage = "lean_exec"
                outcome = dojo.run_tac(state, tactic)

                if isinstance(outcome, ProofFinished):
                    return {
                        "attempt": attempt_idx,
                        "success": True,
                        "proof": "\n".join(tactic_history),
                        "error": "",
                        "elapsed_s": round(time.time() - t0, 2),
                        "rounds": _round + 1,
                    }
                elif isinstance(outcome, TacticState):
                    state = outcome  # advance to new proof state
                else:
                    # LeanError or ProofGivenUp — this tactic failed
                    err = getattr(outcome, "error", str(outcome))
                    last_lean_error = str(err)
                    logger.debug("round %d tactic failed: %s | %s", _round, tactic, err)
                    break

        final_error = f"no proof found in {max_ponder_rounds} rounds"
        if last_lean_error:
            final_error = f"{final_error}; last_lean_error={last_lean_error}"
        return {
            "attempt": attempt_idx,
            "success": False,
            "proof": "\n".join(tactic_history),
            "error": final_error,
            "error_category": _categorize_error(final_error),
            "failure_stage": "search_exhausted",
            "tactics_tried": len(tactic_history),
            "last_lean_error": last_lean_error,
            "elapsed_s": round(time.time() - t0, 2),
            "rounds": max_ponder_rounds,
        }

    except Exception as exc:
        err = str(exc)
        return {
            "attempt": attempt_idx,
            "success": False,
            "proof": "\n".join(tactic_history),
            "error": err,
            "error_category": _categorize_error(err),
            "failure_stage": failure_stage,
            "tactics_tried": len(tactic_history),
            "last_lean_error": last_lean_error,
            "elapsed_s": round(time.time() - t0, 2),
            "rounds": 0,
        }


def run_benchmark(
    *,
    split: str,
    k: int,
    n_problems: int | None,
    model: str,
    project_root: str = ".",
    retrieval_index_path: str = "",
    retrieval_top_k: int = 12,
    max_ponder_rounds: int = 6,
    lean_timeout: int = 90,
    max_api_retries: int = 2,
    workers: int = 4,
    out_dir: str = "output",
    mode: str = "ponder",
    mcts_iterations: int = 12,
    mcts_repair_variants: int = 3,
    mcts_max_depth: int = 5,
) -> BenchmarkResult:
    """Run the full miniF2F benchmark and return structured results."""
    try:
        from mistralai import Mistral
    except ImportError:
        from mistralai.client import Mistral  # type: ignore[no-redef]

    api_key = os.environ.get("MISTRAL_API_KEY") or os.environ.get("LEANSTRAL_API_KEY")
    if not api_key:
        raise ValueError(
            "Set MISTRAL_API_KEY or LEANSTRAL_API_KEY in your environment."
        )
    client = Mistral(api_key=api_key)

    root = Path(project_root).resolve()
    problems_raw = _load_minif2f(split, n_problems)
    t_start = time.time()

    results: list[ProblemResult] = []
    for row in problems_raw:
        pid = str(row.get("id") or row.get("name") or row.get("informal_name") or "unknown")
        informal = str(row.get("informal_name") or row.get("name") or pid)
        stmt = _extract_lean_statement(row)
        header = _extract_header(row)
        results.append(ProblemResult(
            problem_id=pid,
            informal_name=informal,
            split=split,
            lean_statement=stmt,
            lean_header=header,
        ))

    logger.info("Starting %d problems × %d attempts = %d total", len(results), k, len(results) * k)

    import threading
    _thread_id_counter: dict[int, int] = {}
    _thread_id_lock = threading.Lock()

    def _get_worker_id() -> int:
        tid = threading.get_ident()
        with _thread_id_lock:
            if tid not in _thread_id_counter:
                _thread_id_counter[tid] = len(_thread_id_counter)
            return _thread_id_counter[tid]

    def _run_one(pr: ProblemResult, attempt_idx: int) -> tuple[ProblemResult, dict[str, Any]]:
        attempt = _attempt_proof(
            lean_statement=pr.lean_statement,
            lean_header=pr.lean_header,
            client=client,
            model=model,
            attempt_idx=attempt_idx,
            worker_id=_get_worker_id(),
            project_root=root,
            retrieval_index_path=retrieval_index_path,
            retrieval_top_k=retrieval_top_k,
            max_ponder_rounds=max_ponder_rounds,
            lean_timeout=lean_timeout,
            max_api_retries=max_api_retries,
            mode=mode,
            mcts_iterations=mcts_iterations,
            mcts_repair_variants=mcts_repair_variants,
            mcts_max_depth=mcts_max_depth,
        )
        return pr, attempt

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_run_one, pr, idx): (pr, idx)
            for pr in results
            for idx in range(k)
        }
        done = 0
        total_jobs = len(futures)
        for fut in as_completed(futures):
            pr, attempt = fut.result()
            pr.attempts.append(attempt)
            done += 1
            if done % max(1, total_jobs // 10) == 0:
                solved_so_far = sum(1 for r in results if r.solved)
                logger.info(
                    "Progress %d/%d — solved %d/%d",
                    done, total_jobs, solved_so_far, len(results),
                )

    elapsed = time.time() - t_start
    total_solved = sum(1 for r in results if r.solved)
    # pass@1: fraction solved on first attempt.
    at1 = sum(
        1 for r in results if r.attempts and r.attempts[0].get("success")
    ) / max(1, len(results))
    atk = total_solved / max(1, len(results))

    # Aggregate normalisation metrics from all attempt records
    total_api_calls = sum(
        a.get("api_calls", 0)
        for r in results for a in r.attempts
    )
    total_tokens_in = sum(
        a.get("tokens_in", 0)
        for r in results for a in r.attempts
    )
    total_tokens_out = sum(
        a.get("tokens_out", 0)
        for r in results for a in r.attempts
    )
    n = max(1, len(results))

    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    bench = BenchmarkResult(
        split=split,
        n_problems=len(results),
        k=k,
        pass_at_1=at1,
        pass_at_k=atk,
        total_solved=total_solved,
        total_attempts=sum(len(r.attempts) for r in results),
        elapsed_seconds=round(elapsed, 1),
        timestamp=ts,
        per_problem=[
            {
                "id": r.problem_id,
                "name": r.informal_name,
                "solved": r.solved,
                "best_proof": r.best_proof,
                "attempts": r.attempts,
            }
            for r in results
        ],
        baselines=BASELINES,
        total_api_calls=total_api_calls,
        total_tokens_in=total_tokens_in,
        total_tokens_out=total_tokens_out,
        seconds_per_problem=round(elapsed / n, 1),
        api_calls_per_problem=round(total_api_calls / n, 1),
    )

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    out_file = out_path / f"benchmark_minif2f_{split}_{ts}.json"
    out_file.write_text(json.dumps(asdict(bench), indent=2), encoding="utf-8")
    logger.info("Results saved to %s", out_file)

    return bench


def main() -> int:
    p = argparse.ArgumentParser(description="Evaluate DESol on miniF2F")
    p.add_argument("--split", choices=["test", "valid"], default="test")
    p.add_argument(
        "--n-problems", type=int, default=None,
        help="Limit evaluation to first N problems (omit for full split)"
    )
    p.add_argument("--k", type=int, default=1, help="Number of independent attempts per problem")
    p.add_argument(
        "--model", default=os.environ.get("LEANSTRAL_MODEL", "mistral-large-latest"),
        help="Mistral model name"
    )
    p.add_argument(
        "--retrieval-index",
        default=os.environ.get("DESOL_RETRIEVAL_INDEX", "data/mathlib_embeddings"),
        help="Path to premise retrieval index (default: data/mathlib_embeddings)"
    )
    p.add_argument(
        "--retrieval-top-k", type=int, default=12,
        help="Number of premises to retrieve per goal (default: 12)"
    )
    p.add_argument(
        "--max-ponder-rounds", type=int, default=6,
        help="Max ponder rounds per tactic step"
    )
    p.add_argument(
        "--lean-timeout", type=int, default=90,
        help="Seconds allowed for each lake build call (default 90)"
    )
    p.add_argument(
        "--max-api-retries", type=int, default=2,
        help="Retries for transient API failures (HTTP 500/service unavailable)"
    )
    p.add_argument(
        "--project-root", default=".",
        help="DESol project root containing lakefile.toml (default: cwd)"
    )
    p.add_argument("--workers", type=int, default=4, help="Parallel worker threads")
    p.add_argument("--out-dir", default="output", help="Output directory for results JSON")
    p.add_argument(
        "--mode", choices=["ponder", "mcts-draft", "hierarchical", "state-mcts", "hierarchical-state"], default="ponder",
        help="Proof search mode: ponder, mcts-draft, hierarchical, state-mcts, or hierarchical-state (sketch+state-MCTS per subgoal)",
    )
    p.add_argument(
        "--mcts-iterations", type=int, default=12,
        help="MCTS iterations per problem (mcts-draft mode only, default 12)",
    )
    p.add_argument(
        "--mcts-repair-variants", type=int, default=3,
        help="Repair variants per MCTS node (mcts-draft mode only, default 3)",
    )
    p.add_argument(
        "--mcts-max-depth", type=int, default=5,
        help="Max MCTS depth in repair rounds (mcts-draft mode only, default 5)",
    )
    args = p.parse_args()

    bench = run_benchmark(
        split=args.split,
        k=args.k,
        n_problems=args.n_problems,
        model=args.model,
        project_root=args.project_root,
        retrieval_index_path=args.retrieval_index,
        retrieval_top_k=args.retrieval_top_k,
        max_ponder_rounds=args.max_ponder_rounds,
        lean_timeout=args.lean_timeout,
        max_api_retries=args.max_api_retries,
        workers=args.workers,
        out_dir=args.out_dir,
        mode=args.mode,
        mcts_iterations=args.mcts_iterations,
        mcts_repair_variants=args.mcts_repair_variants,
        mcts_max_depth=args.mcts_max_depth,
    )

    print()
    for line in bench.summary_lines():
        print(line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
