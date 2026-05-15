#!/usr/bin/env python3
"""Cross-validation reliability check for the lemma-factor-v2 sweep.

Runs the SAME sweep configuration twice with different random seeds and
reports which row-level closures appear in BOTH runs (high-confidence) vs
only one run (lower-confidence). The reliability rate `|both| / |union|`
is the evidence that closures are reproducible across independent
proof-search attempts.

This is metadata-only: it does NOT mutate canonical ledgers or the
committed reproducibility evidence. Each sweep is invoked in a
subprocess, the per-paper summary at
``output/lemma_factor_v2_sweep_summary.json`` is captured into a private
output directory, and the canonical summary is restored at the end so
the file on disk matches what was there before the reliability run.

A "closure" for the purposes of reliability is a row whose `stages`
contain a closure-positive stage:

  - first_pass_validated
  - repl_prover_validated
  - composed
  - routed_to_axiom_backed

The row id is ``"<paper_id>::<theorem_name>"``.

Usage
=====

    python scripts/sweep_reliability_check.py \\
        --seed-a 1 --seed-b 2 \\
        --paper 2304.09598 --paper 2604.21884 \\
        --max-candidates 5 \\
        --out output/reliability_check.json

The seeds are passed to the sweep as the ``MISTRAL_SEED`` env-var (read
opportunistically by the generators) AND as a deterministic salt on
PYTHONHASHSEED so any python-level randomness diverges between runs.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SUMMARY = PROJECT_ROOT / "output" / "lemma_factor_v2_sweep_summary.json"

CLOSURE_STAGES = (
    "first_pass_validated",
    "repl_prover_validated",
    "composed",
    "routed_to_axiom_backed",
)


def _row_closed(per_row: dict[str, Any]) -> bool:
    """True iff the row's `stages` contain any closure-positive stage."""
    for s in per_row.get("stages", []) or []:
        if isinstance(s, dict) and s.get("stage") in CLOSURE_STAGES:
            return True
        if isinstance(s, str) and s in CLOSURE_STAGES:
            return True
    return False


def extract_closures(summary: dict[str, Any]) -> dict[str, str]:
    """Return {row_id: closure_stage} for every closed row in the summary.

    `row_id` is ``"<paper_id>::<theorem>"``. The closure_stage is the
    FIRST closure-positive stage found in ``stages``.
    """
    out: dict[str, str] = {}
    for paper in summary.get("papers", []) or []:
        pid = str(paper.get("paper_id", "") or "")
        for row in paper.get("details", []) or []:
            theorem = str(row.get("theorem", "") or "")
            if not theorem:
                continue
            for s in row.get("stages", []) or []:
                stage = s.get("stage") if isinstance(s, dict) else (
                    s if isinstance(s, str) else None
                )
                if stage in CLOSURE_STAGES:
                    out[f"{pid}::{theorem}"] = stage
                    break
    return out


def compute_reliability(
    seed_a: dict[str, str], seed_b: dict[str, str]
) -> dict[str, Any]:
    """Compute {both, a_only, b_only, reliability_rate} from two seed maps."""
    a_keys = set(seed_a)
    b_keys = set(seed_b)
    both = sorted(a_keys & b_keys)
    a_only = sorted(a_keys - b_keys)
    b_only = sorted(b_keys - a_keys)
    union = a_keys | b_keys
    rate = (len(both) / len(union)) if union else 0.0
    return {
        "seed_a": dict(sorted(seed_a.items())),
        "seed_b": dict(sorted(seed_b.items())),
        "both": both,
        "a_only": a_only,
        "b_only": b_only,
        "reliability_rate": rate,
        "counts": {
            "a_total": len(a_keys),
            "b_total": len(b_keys),
            "both": len(both),
            "a_only": len(a_only),
            "b_only": len(b_only),
            "union": len(union),
        },
    }


def _run_sweep_subprocess(
    *,
    seed: int,
    paper_ids: list[str],
    max_candidates: int,
    summary_path: Path,
    extra_args: list[str] | None = None,
    sweep_runner: list[str] | None = None,
    env_overrides: dict[str, str] | None = None,
    timeout_s: int = 0,
) -> tuple[int, str]:
    """Invoke ``scripts/sweep_lemma_factor_v2.py`` once, writing the
    summary to ``summary_path``. Returns ``(returncode, stderr_tail)``.
    """
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    runner = sweep_runner or [sys.executable, str(PROJECT_ROOT / "scripts" / "sweep_lemma_factor_v2.py")]
    cmd = list(runner) + [
        "--max-candidates", str(max_candidates),
        "--summary", str(summary_path),
    ]
    for pid in paper_ids:
        cmd += ["--paper", pid]
    if extra_args:
        cmd += list(extra_args)
    env = os.environ.copy()
    env["MISTRAL_SEED"] = str(seed)
    env["PYTHONHASHSEED"] = str(seed)
    env["BDDM_RELIABILITY_SEED"] = str(seed)
    if env_overrides:
        env.update(env_overrides)
    kwargs: dict[str, Any] = dict(
        cwd=str(PROJECT_ROOT), env=env, capture_output=True, text=True,
    )
    if timeout_s and timeout_s > 0:
        kwargs["timeout"] = timeout_s
    try:
        cp = subprocess.run(cmd, **kwargs)
    except subprocess.TimeoutExpired as e:
        return 124, f"timeout after {timeout_s}s: {e}"
    tail = (cp.stderr or "")[-2000:]
    return cp.returncode, tail


def reliability_check(
    *,
    seed_a: int,
    seed_b: int,
    paper_ids: list[str],
    max_candidates: int = 5,
    workdir: Path | None = None,
    canonical_summary: Path = DEFAULT_SUMMARY,
    extra_args: list[str] | None = None,
    sweep_runner: list[str] | None = None,
    timeout_s: int = 0,
) -> dict[str, Any]:
    """Run two sweeps with different seeds and compare per-row closures.

    Returns the dict described in the module docstring. The canonical
    summary file is backed up at the start and RESTORED at the end so
    this routine is non-mutating wrt the rest of the pipeline.
    """
    workdir = workdir or Path(tempfile.mkdtemp(prefix="reliability_"))
    workdir.mkdir(parents=True, exist_ok=True)

    # Back up the canonical summary so we can restore it after both runs.
    backup_path = workdir / "canonical_summary.backup.json"
    if canonical_summary.exists():
        shutil.copy2(canonical_summary, backup_path)
    summary_a = workdir / "summary_seed_a.json"
    summary_b = workdir / "summary_seed_b.json"

    t0 = time.time()
    rc_a, err_a = _run_sweep_subprocess(
        seed=seed_a, paper_ids=paper_ids,
        max_candidates=max_candidates, summary_path=summary_a,
        extra_args=extra_args, sweep_runner=sweep_runner, timeout_s=timeout_s,
    )
    t_a = time.time() - t0
    t1 = time.time()
    rc_b, err_b = _run_sweep_subprocess(
        seed=seed_b, paper_ids=paper_ids,
        max_candidates=max_candidates, summary_path=summary_b,
        extra_args=extra_args, sweep_runner=sweep_runner, timeout_s=timeout_s,
    )
    t_b = time.time() - t1

    closures_a: dict[str, str] = {}
    closures_b: dict[str, str] = {}
    if summary_a.exists():
        closures_a = extract_closures(json.loads(summary_a.read_text(encoding="utf-8")))
    if summary_b.exists():
        closures_b = extract_closures(json.loads(summary_b.read_text(encoding="utf-8")))

    result = compute_reliability(closures_a, closures_b)
    result.update({
        "config": {
            "seed_a": seed_a,
            "seed_b": seed_b,
            "paper_ids": list(paper_ids),
            "max_candidates": max_candidates,
            "extra_args": list(extra_args or ()),
        },
        "sweep_a": {
            "returncode": rc_a, "stderr_tail": err_a, "elapsed_s": round(t_a, 2),
            "summary_path": str(summary_a),
        },
        "sweep_b": {
            "returncode": rc_b, "stderr_tail": err_b, "elapsed_s": round(t_b, 2),
            "summary_path": str(summary_b),
        },
    })

    # Restore canonical summary so this routine is non-mutating.
    if backup_path.exists():
        shutil.copy2(backup_path, canonical_summary)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-a", type=int, default=1)
    parser.add_argument("--seed-b", type=int, default=2)
    parser.add_argument("--paper", action="append", default=[], required=False)
    parser.add_argument("--max-candidates", type=int, default=5)
    parser.add_argument(
        "--out",
        default=str(PROJECT_ROOT / "output" / "reliability_check.json"),
        help="Where to write the reliability report JSON.",
    )
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        help=(
            "Extra args to forward to the underlying sweep "
            "(e.g. --extra=--no-use-fast-validation). May repeat."
        ),
    )
    parser.add_argument(
        "--timeout-s",
        type=int,
        default=0,
        help="Per-sweep subprocess timeout (0 = unlimited).",
    )
    args = parser.parse_args()
    if not args.paper:
        print("[error] --paper is required (at least one)", file=sys.stderr)
        return 2
    result = reliability_check(
        seed_a=args.seed_a,
        seed_b=args.seed_b,
        paper_ids=list(args.paper),
        max_candidates=args.max_candidates,
        extra_args=list(args.extra or ()),
        timeout_s=args.timeout_s,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[reliability] wrote {out}")
    print(json.dumps(result["counts"], indent=2))
    print(f"[reliability] rate={result['reliability_rate']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
