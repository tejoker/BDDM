#!/usr/bin/env python3
"""Benchmark `prove_with_ponder.py --mode mcts-draft` over config grids.

The benchmark is empirical: it measures wall-clock latency and success rate for
multiple MCTS parameter combinations over one or more theorem targets.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Target:
    file: str
    theorem: str


@dataclass
class RunResult:
    profile: str
    workers: int
    iterations: int
    variants: int
    depth: int
    target_file: str
    target_theorem: str
    ok: bool
    return_code: int
    elapsed_s: float
    status_line: str


@dataclass
class ConfigSummary:
    profile: str
    workers: int
    iterations: int
    variants: int
    depth: int
    total_runs: int
    success_count: int
    success_rate: float
    mean_elapsed_s: float
    median_elapsed_s: float
    score: float


def _parse_csv_ints(raw: str) -> list[int]:
    vals: list[int] = []
    for chunk in raw.split(","):
        s = chunk.strip()
        if not s:
            continue
        vals.append(int(s))
    if not vals:
        raise ValueError("expected at least one integer value")
    return vals


def _parse_csv_strs(raw: str) -> list[str]:
    vals = [x.strip() for x in raw.split(",") if x.strip()]
    if not vals:
        raise ValueError("expected at least one string value")
    return vals


def _parse_target(raw: str) -> Target:
    # Format: path/to/file.lean:theorem_name (split once from right).
    if ":" not in raw:
        raise ValueError(f"invalid target '{raw}', expected file:theorem")
    file_part, theorem = raw.rsplit(":", 1)
    file_part = file_part.strip()
    theorem = theorem.strip()
    if not file_part or not theorem:
        raise ValueError(f"invalid target '{raw}', expected file:theorem")
    return Target(file=file_part, theorem=theorem)


def _load_targets(args: argparse.Namespace) -> list[Target]:
    targets: list[Target] = []
    for item in args.target:
        targets.append(_parse_target(item))

    if args.target_file:
        content = Path(args.target_file).read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            targets.append(_parse_target(line))

    deduped: list[Target] = []
    seen: set[tuple[str, str]] = set()
    for t in targets:
        key = (t.file, t.theorem)
        if key not in seen:
            seen.add(key)
            deduped.append(t)

    if not deduped:
        raise ValueError("no targets provided; use --target and/or --target-file")
    return deduped


def _status_line(stdout: str, stderr: str) -> str:
    combined = (stdout + "\n" + stderr).splitlines()
    for line in reversed(combined):
        s = line.strip()
        if s.startswith("[ok]") or s.startswith("[fail]"):
            return s
    for line in reversed(combined):
        s = line.strip()
        if s:
            return s
    return ""


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    arr = sorted(values)
    n = len(arr)
    mid = n // 2
    if n % 2 == 1:
        return arr[mid]
    return 0.5 * (arr[mid - 1] + arr[mid])


def _config_key(run: RunResult) -> tuple[str, int, int, int, int]:
    return (run.profile, run.workers, run.iterations, run.variants, run.depth)


def _summarize(runs: list[RunResult]) -> list[ConfigSummary]:
    groups: dict[tuple[str, int, int, int, int], list[RunResult]] = {}
    for r in runs:
        groups.setdefault(_config_key(r), []).append(r)

    summaries: list[ConfigSummary] = []
    for key, bucket in groups.items():
        profile, workers, iterations, variants, depth = key
        elapsed = [r.elapsed_s for r in bucket]
        success_count = sum(1 for r in bucket if r.ok)
        total = len(bucket)
        success_rate = success_count / total if total else 0.0
        mean_elapsed = sum(elapsed) / total if total else 0.0
        med_elapsed = _median(elapsed)
        # Higher is better: prioritize success rate heavily, then speed.
        score = (1000.0 * success_rate) - mean_elapsed
        summaries.append(
            ConfigSummary(
                profile=profile,
                workers=workers,
                iterations=iterations,
                variants=variants,
                depth=depth,
                total_runs=total,
                success_count=success_count,
                success_rate=success_rate,
                mean_elapsed_s=mean_elapsed,
                median_elapsed_s=med_elapsed,
                score=score,
            )
        )

    summaries.sort(key=lambda s: (s.score, s.success_rate), reverse=True)
    return summaries


def _write_report(
    *,
    out_path: Path,
    command: list[str],
    runs: list[RunResult],
    summary: list[ConfigSummary],
) -> None:
    payload = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cwd": str(Path.cwd()),
        "invocation": command,
        "run_count": len(runs),
        "runs": [asdict(r) for r in runs],
        "summary": [asdict(s) for s in summary],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Benchmark draft-MCTS configs for DESol")
    p.add_argument("--project-root", default=".", help="Path to DESol root")
    p.add_argument(
        "--target",
        action="append",
        default=[],
        help="Target in format file.lean:theorem_name (repeatable)",
    )
    p.add_argument(
        "--target-file",
        default="",
        help="File with one target per line in format file.lean:theorem_name",
    )
    p.add_argument("--profiles", default="hybrid,throughput,depth", help="CSV MCTS profiles")
    p.add_argument("--workers", default="0", help="CSV worker counts (0 = auto from cpu target)")
    p.add_argument("--iterations", default="24,36", help="CSV MCTS iteration counts")
    p.add_argument("--variants", default="2,3", help="CSV repair variant counts")
    p.add_argument("--depths", default="3,5", help="CSV max depths")
    p.add_argument("--repeats", type=int, default=1, help="Repetitions per target/config")
    p.add_argument("--cpu-target", type=float, default=0.8, help="Auto-workers CPU target")
    p.add_argument("--temperature", type=float, default=0.2)
    p.add_argument("--dojo-timeout", type=int, default=600)
    p.add_argument("--retrieval-index", default="data/mathlib_embeddings")
    p.add_argument("--retrieval-top-k", type=int, default=12)
    p.add_argument("--premise-file", default="knowledge/dependency_graph.toon")
    p.add_argument("--premise-namespace", default="ProbabilityTheory")
    p.add_argument(
        "--report",
        default="",
        help="Output report JSON path (default: output/mcts_bench/report_<ts>.json)",
    )
    p.add_argument("--dry-run", action="store_true", help="Print planned runs without executing")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    project_root = Path(args.project_root).resolve()
    os.chdir(project_root)

    try:
        targets = _load_targets(args)
        profiles = _parse_csv_strs(args.profiles)
        workers = _parse_csv_ints(args.workers)
        iterations = _parse_csv_ints(args.iterations)
        variants = _parse_csv_ints(args.variants)
        depths = _parse_csv_ints(args.depths)
    except Exception as exc:
        print(f"[fail] {exc}")
        return 1

    if args.repeats < 1:
        print("[fail] repeats must be >= 1")
        return 1

    plan = list(itertools.product(profiles, workers, iterations, variants, depths, targets, range(args.repeats)))

    if args.dry_run:
        print(f"[plan] total runs={len(plan)}")
        for idx, (profile, worker, iters, var_count, depth, target, rep) in enumerate(plan, start=1):
            print(
                f"[{idx}/{len(plan)}] profile={profile} workers={worker} iterations={iters} "
                f"variants={var_count} depth={depth} target={target.file}:{target.theorem} rep={rep + 1}"
            )
        return 0

    runs: list[RunResult] = []
    for idx, (profile, worker, iters, var_count, depth, target, rep) in enumerate(plan, start=1):
        cmd = [
            sys.executable,
            "scripts/prove_with_ponder.py",
            "--file",
            target.file,
            "--theorem",
            target.theorem,
            "--project-root",
            str(project_root),
            "--mode",
            "mcts-draft",
            "--mcts-profile",
            profile,
            "--mcts-parallel-workers",
            str(worker),
            "--mcts-cpu-target",
            str(args.cpu_target),
            "--mcts-iterations",
            str(iters),
            "--mcts-repair-variants",
            str(var_count),
            "--mcts-max-depth",
            str(depth),
            "--temperature",
            str(args.temperature),
            "--dojo-timeout",
            str(args.dojo_timeout),
            "--retrieval-index",
            args.retrieval_index,
            "--retrieval-top-k",
            str(args.retrieval_top_k),
            "--premise-file",
            args.premise_file,
            "--premise-namespace",
            args.premise_namespace,
        ]

        print(
            f"[{idx}/{len(plan)}] profile={profile} workers={worker} iterations={iters} "
            f"variants={var_count} depth={depth} target={target.file}:{target.theorem} rep={rep + 1}"
        )
        started = time.time()
        proc = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True)
        elapsed = time.time() - started
        status = _status_line(proc.stdout, proc.stderr)
        ok = proc.returncode == 0
        runs.append(
            RunResult(
                profile=profile,
                workers=worker,
                iterations=iters,
                variants=var_count,
                depth=depth,
                target_file=target.file,
                target_theorem=target.theorem,
                ok=ok,
                return_code=proc.returncode,
                elapsed_s=elapsed,
                status_line=status,
            )
        )
        print(
            f"      -> ok={ok} return={proc.returncode} elapsed={elapsed:.1f}s"
            + (f" | {status}" if status else "")
        )

    summary = _summarize(runs)
    timestamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    report_path = (
        Path(args.report).resolve()
        if args.report
        else (project_root / "output" / "mcts_bench" / f"report_{timestamp}.json").resolve()
    )
    _write_report(out_path=report_path, command=sys.argv, runs=runs, summary=summary)

    print(f"\n[report] {report_path}")
    if summary:
        best = summary[0]
        print(
            "[best] "
            f"profile={best.profile} workers={best.workers} iterations={best.iterations} "
            f"variants={best.variants} depth={best.depth} "
            f"success={best.success_count}/{best.total_runs} "
            f"mean={best.mean_elapsed_s:.1f}s median={best.median_elapsed_s:.1f}s"
        )

    # Return non-zero if no successful runs.
    any_ok = any(r.ok for r in runs)
    return 0 if any_ok else 1


if __name__ == "__main__":
    sys.exit(main())
