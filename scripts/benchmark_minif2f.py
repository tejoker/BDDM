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
_HF_DATASET = "Matharena/minif2f_lean4"

# Known pass@1 baselines for comparison (test split).
BASELINES = {
    "ReProver (GPT-4 + best-first)": 0.273,
    "HyperTree Proof Search (Meta)": 0.330,
    "LLM-Step (Llama-2)": 0.220,
    "Aesop (tactic, no LLM)": 0.040,
}


@dataclass
class ProblemResult:
    problem_id: str
    informal_name: str
    split: str
    lean_statement: str
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

    def summary_lines(self) -> list[str]:
        lines = [
            f"miniF2F benchmark — split={self.split} k={self.k} n={self.n_problems}",
            f"  pass@1 : {self.pass_at_1:.1%}",
            f"  pass@{self.k} : {self.pass_at_k:.1%}  ({self.total_solved}/{self.n_problems} solved)",
            f"  elapsed: {self.elapsed_seconds:.1f}s",
            "",
            "Baselines (pass@1, test split):",
        ]
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
    """Extract the Lean 4 theorem statement from a miniF2F row."""
    # Try common field names across dataset versions.
    for key in ("formal_statement", "lean4_formal_statement", "statement", "lean_statement"):
        val = row.get(key, "")
        if val and isinstance(val, str) and "theorem" in val.lower():
            return val.strip()
    # Last resort: concatenate all string fields.
    return " ".join(str(v) for v in row.values() if isinstance(v, str))


def _attempt_proof(
    *,
    problem_id: str,
    lean_statement: str,
    client: Any,
    model: str,
    attempt_idx: int,
    retrieval_index_path: str = "",
    max_ponder_rounds: int = 6,
) -> dict[str, Any]:
    """Run one proof attempt using the ponder loop.

    Returns a dict with keys: attempt, success, proof, error, elapsed_s.
    """
    from ponder_loop import ponder_loop, load_premise_context

    t0 = time.time()
    premise_context = ""
    if retrieval_index_path:
        try:
            premise_context = load_premise_context(
                lean_statement, retrieval_index_path=retrieval_index_path
            )
        except Exception as exc:
            logger.debug("premise retrieval failed: %s", exc)

    try:
        result = ponder_loop(
            goal=lean_statement,
            client=client,
            model=model,
            max_rounds=max_ponder_rounds,
            premise_context=premise_context,
        )
        success = bool(result.get("proof_found") or result.get("success"))
        proof = result.get("proof") or result.get("tactic") or ""
        error = result.get("error") or ""
    except Exception as exc:
        success = False
        proof = ""
        error = str(exc)

    return {
        "attempt": attempt_idx,
        "success": success,
        "proof": proof,
        "error": error,
        "elapsed_s": round(time.time() - t0, 2),
    }


def run_benchmark(
    *,
    split: str,
    k: int,
    n_problems: int | None,
    model: str,
    retrieval_index_path: str = "",
    max_ponder_rounds: int = 6,
    workers: int = 4,
    out_dir: str = "output",
) -> BenchmarkResult:
    """Run the full miniF2F benchmark and return structured results."""
    from mistralai import Mistral

    api_key = os.environ.get("MISTRAL_API_KEY") or os.environ.get("LEANSTRAL_API_KEY")
    if not api_key:
        raise ValueError(
            "Set MISTRAL_API_KEY or LEANSTRAL_API_KEY in your environment."
        )
    client = Mistral(api_key=api_key)

    problems_raw = _load_minif2f(split, n_problems)
    t_start = time.time()

    results: list[ProblemResult] = []
    for row in problems_raw:
        pid = str(row.get("id") or row.get("name") or row.get("informal_name") or "unknown")
        informal = str(row.get("informal_name") or row.get("name") or pid)
        stmt = _extract_lean_statement(row)
        results.append(ProblemResult(
            problem_id=pid,
            informal_name=informal,
            split=split,
            lean_statement=stmt,
        ))

    logger.info("Starting %d problems × %d attempts = %d total", len(results), k, len(results) * k)

    def _run_one(pr: ProblemResult, attempt_idx: int) -> tuple[ProblemResult, dict[str, Any]]:
        attempt = _attempt_proof(
            problem_id=pr.problem_id,
            lean_statement=pr.lean_statement,
            client=client,
            model=model,
            attempt_idx=attempt_idx,
            retrieval_index_path=retrieval_index_path,
            max_ponder_rounds=max_ponder_rounds,
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
    p.add_argument("--retrieval-index", default="", help="Path to premise retrieval index")
    p.add_argument(
        "--max-ponder-rounds", type=int, default=6,
        help="Max think rounds in ponder loop per attempt"
    )
    p.add_argument("--workers", type=int, default=4, help="Parallel worker threads")
    p.add_argument("--out-dir", default="output", help="Output directory for results JSON")
    args = p.parse_args()

    bench = run_benchmark(
        split=args.split,
        k=args.k,
        n_problems=args.n_problems,
        model=args.model,
        retrieval_index_path=args.retrieval_index,
        max_ponder_rounds=args.max_ponder_rounds,
        workers=args.workers,
        out_dir=args.out_dir,
    )

    print()
    for line in bench.summary_lines():
        print(line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
