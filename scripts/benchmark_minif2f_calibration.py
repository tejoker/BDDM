#!/usr/bin/env python3
"""External calibration: run our whole-proof generator on a miniF2F sample.

Why this exists
---------------
The pipeline's headline `FP=14` number is uncalibrated. To know whether
that's strong or weak we need an external benchmark with published
baselines. miniF2F (`cat-searcher/minif2f-lean4`) is the standard
reference, and its closure rate on our exact prover stack is the
apples-to-apples calibration anchor.

Compared to `benchmark_minif2f.py` (which supports ponder / MCTS modes and
parallel workers), this script is purpose-built for the F1 calibration:
single-call whole-proof generation against the Leanstral generator we
actually use in `sweep_leanstral_whole_proof.py`, validated by
`lake env lean` on an isolated bench file. One Mistral call per problem,
strict budget cap.

Run:
    PYTHONPATH=scripts python3 scripts/benchmark_minif2f_calibration.py \
        --n-problems 30 --out output/minif2f_calibration.json

The sample defaults to the first 30 problems for reproducibility; pass
`--shuffle-seed N` for a randomized sample. Cumulative `lake env lean`
runs are sequential (avoids fighting for the Mathlib build lock).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Read .env without requiring python-dotenv to be on the system path.
ENV_PATH = REPO_ROOT / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

DATASET_JSONL = Path(
    "/home/projectx/.cache/huggingface/hub/datasets--cat-searcher--minif2f-lean4/"
    "snapshots/70a1249ce240667f6bcdd1ccd62f847f0e065d57/test.jsonl"
)


# Categories derived from the miniF2F problem ID prefix. The miniF2F
# convention puts the source competition or theme in the first / second
# underscore-delimited token: `mathd_algebra_478`, `imo_1959_p1`, etc.
def _categorize(problem_id: str) -> str:
    parts = problem_id.split("_")
    if not parts:
        return "other"
    head = parts[0]
    if head == "mathd" and len(parts) >= 2:
        return f"mathd_{parts[1]}"
    return head


def _load_problems(n_problems: int, shuffle_seed: int | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with DATASET_JSONL.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    if shuffle_seed is not None:
        rng = random.Random(shuffle_seed)
        rng.shuffle(rows)
    return rows[:n_problems]


_THEOREM_NAME_RE = re.compile(r"(?:theorem|lemma)\s+([\w']+)")


def _extract_theorem_name(stmt: str) -> str:
    m = _THEOREM_NAME_RE.search(stmt)
    return m.group(1) if m else "minif2f_problem"


def _strip_trailing_sorry(stmt: str) -> str:
    """The miniF2F dataset stores statements with a placeholder `:= sorry`;
    strip it so we can patch in a real proof body."""
    s = stmt.rstrip()
    # Common shapes: `:= sorry`, `:= by sorry`, `:= by\n  sorry`.
    s = re.sub(r":=\s*by\s*sorry\s*$", "", s, flags=re.DOTALL)
    s = re.sub(r":=\s*sorry\s*$", "", s, flags=re.DOTALL)
    return s.rstrip()


def _write_bench_file(
    problem: dict[str, Any], proof_body: str, bench_path: Path
) -> None:
    """Write the miniF2F theorem with `proof_body` patched in as the proof,
    using a broad `import Mathlib` header for compatibility across Mathlib
    snapshots."""
    stmt = _strip_trailing_sorry(problem["formal_statement"])
    indented = "\n".join("  " + line for line in proof_body.strip().splitlines())
    content = "import Mathlib\n\n" + stmt + " := by\n" + indented + "\n"
    bench_path.write_text(content, encoding="utf-8")


def _run_lake_env_lean(bench_path: Path, timeout: int) -> tuple[bool, str]:
    """Run `lake env lean <bench>` and return (success, error_tail)."""
    try:
        proc = subprocess.run(
            ["lake", "env", "lean", str(bench_path.relative_to(REPO_ROOT))],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "lake_env_lean_timeout"
    except Exception as exc:  # pragma: no cover — defensive
        return False, f"lake_env_lean_error: {exc}"
    stderr_tail = (proc.stderr or "").strip()[-1200:]
    stdout_tail = (proc.stdout or "").strip()[-400:]
    if proc.returncode == 0 and not _has_error_marker(proc.stderr) and not _has_error_marker(proc.stdout):
        return True, ""
    return False, (stderr_tail + "\n--stdout--\n" + stdout_tail).strip()


def _has_error_marker(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    return "error:" in t or "unsolved goals" in t or "unknown identifier" in t


def _build_mistral_client() -> Any:
    api_key = os.environ.get("MISTRAL_API_KEY") or os.environ.get("LEANSTRAL_API_KEY")
    if not api_key:
        raise RuntimeError("MISTRAL_API_KEY not set in environment")
    try:
        from mistralai import Mistral  # type: ignore[import-not-found]
    except ImportError:
        from mistralai.client import Mistral  # type: ignore[no-redef]
    return Mistral(api_key=api_key)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--n-problems", type=int, default=30)
    p.add_argument("--shuffle-seed", type=int, default=None,
                   help="Randomize problem order (default: first N for reproducibility)")
    p.add_argument("--lake-timeout", type=int, default=180,
                   help="seconds per problem for lake env lean")
    p.add_argument("--budget-seconds", type=int, default=7200,
                   help="hard wall-clock cap; abort once exceeded")
    p.add_argument("--out", type=Path, default=Path("output") / "minif2f_calibration.json")
    p.add_argument("--model", default=os.environ.get("MISTRAL_MODEL", "labs-leanstral-2603"))
    args = p.parse_args()

    if not DATASET_JSONL.exists():
        print(f"ERROR: miniF2F dataset not found at {DATASET_JSONL}", file=sys.stderr)
        return 2

    problems = _load_problems(args.n_problems, args.shuffle_seed)
    print(f"Loaded {len(problems)} miniF2F test problems")

    # Lazy-import the generator so the script's --help still works if Mistral
    # isn't installed (e.g. for dataset-only inspection).
    from leanstral_whole_proof_generator import generate_proof_candidate

    client = _build_mistral_client()
    bench_dir = REPO_ROOT / "output" / "minif2f_calibration_bench"
    bench_dir.mkdir(parents=True, exist_ok=True)

    t_start = time.time()
    api_calls = 0
    tokens_in_total = 0
    tokens_out_total = 0

    api_log: list[dict[str, Any]] = []

    def _api_log_hook(record: dict[str, Any]) -> None:
        """The ponder_loop hook signature is
        `{timestamp, purpose, request, response_text, latency_seconds}` — no
        usage field. We approximate tokens by char-length to drive the spend
        estimate (rough but useful as an order-of-magnitude check)."""
        nonlocal api_calls, tokens_in_total, tokens_out_total
        api_calls += 1
        req = record.get("request") or {}
        msgs = req.get("messages") or []
        chars_in = sum(len(str(m.get("content", ""))) for m in msgs)
        chars_out = len(str(record.get("response_text", "")))
        # Mistral uses BPE; ~4 chars/token is the common approximation.
        tokens_in_total += chars_in // 4
        tokens_out_total += chars_out // 4
        api_log.append({
            "purpose": record.get("purpose"),
            "latency_s": round(record.get("latency_seconds", 0.0), 2),
            "chars_in": chars_in,
            "chars_out": chars_out,
        })

    per_problem: list[dict[str, Any]] = []
    cat_counts: Counter = Counter()
    cat_solved: Counter = Counter()

    for idx, prob in enumerate(problems):
        elapsed = time.time() - t_start
        if elapsed > args.budget_seconds:
            print(f"BUDGET-CAP: aborting after {elapsed:.0f}s (budget={args.budget_seconds}s)")
            break
        pid = prob.get("id") or f"prob_{idx}"
        category = _categorize(pid)
        cat_counts[category] += 1
        stmt = prob.get("formal_statement", "")
        thm_name = _extract_theorem_name(stmt)
        print(f"\n[{idx+1}/{len(problems)}] {pid} ({category})  thm={thm_name}")

        t_prob = time.time()
        # Single whole-proof generation attempt. No retries; this is a
        # calibration anchor, not a tuned closure run.
        try:
            cand = generate_proof_candidate(
                paper_id=f"minif2f.{pid}",
                theorem_name=thm_name,
                lean_statement=_strip_trailing_sorry(stmt),
                paper_theory_hint="-- miniF2F: only Mathlib is in scope\n",
                paper_local_file="-- (no neighbouring declarations)\n",
                error_tail="",
                client=client,
                model=args.model,
                api_log_hook=_api_log_hook,
                use_mathlib_anchors=False,  # F1 keeps the baseline simple
            )
        except Exception as exc:
            cand = None
            print(f"  generator raised: {exc}")

        generated_body = cand["proof_body"] if cand else ""
        gen_elapsed = time.time() - t_prob
        if not generated_body:
            per_problem.append({
                "id": pid, "category": category, "theorem_name": thm_name,
                "solved": False, "phase": "generator_empty",
                "gen_elapsed_s": round(gen_elapsed, 1),
                "lake_elapsed_s": 0.0, "error_tail": "",
            })
            print(f"  generator returned empty (gen={gen_elapsed:.1f}s)")
            continue

        bench_path = bench_dir / f"Bench_{idx}.lean"
        _write_bench_file(prob, generated_body, bench_path)

        t_lake = time.time()
        ok, err_tail = _run_lake_env_lean(bench_path, timeout=args.lake_timeout)
        lake_elapsed = time.time() - t_lake

        per_problem.append({
            "id": pid,
            "category": category,
            "theorem_name": thm_name,
            "solved": ok,
            "phase": "verified" if ok else "lake_rejected",
            "gen_elapsed_s": round(gen_elapsed, 1),
            "lake_elapsed_s": round(lake_elapsed, 1),
            "proof_body": generated_body if ok else "",
            "error_tail": "" if ok else err_tail[:600],
            "confidence": cand.get("confidence") if cand else 0.0,
        })
        if ok:
            cat_solved[category] += 1
            print(f"  CLOSED  (gen={gen_elapsed:.1f}s, lake={lake_elapsed:.1f}s)")
        else:
            print(f"  REJECT  (gen={gen_elapsed:.1f}s, lake={lake_elapsed:.1f}s)")
            if err_tail:
                head = err_tail.replace("\n", " ")[:120]
                print(f"    err: {head}")

    elapsed_total = time.time() - t_start
    solved = sum(1 for r in per_problem if r["solved"])
    attempted = len(per_problem)
    # Mistral mistral-large pricing as of 2024-2025: $2/MTok in, $6/MTok out.
    # Leanstral pricing isn't public; approximate with the same to give an
    # upper-bound spend estimate.
    est_spend = round(
        (tokens_in_total / 1_000_000.0) * 2.0
        + (tokens_out_total / 1_000_000.0) * 6.0,
        4,
    )

    summary = {
        "schema_version": "minif2f_calibration.v1",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "model": args.model,
        "n_problems_in_sample": len(problems),
        "n_attempted": attempted,
        "n_solved": solved,
        "closure_rate": round(solved / max(1, attempted), 4),
        "elapsed_seconds": round(elapsed_total, 1),
        "api_calls": api_calls,
        "tokens_in": tokens_in_total,
        "tokens_out": tokens_out_total,
        "estimated_spend_usd_at_mistral_large_rates": est_spend,
        "per_category": {
            cat: {
                "attempted": cat_counts[cat],
                "solved": cat_solved[cat],
                "closure_rate": round(cat_solved[cat] / max(1, cat_counts[cat]), 4),
            }
            for cat in sorted(cat_counts)
        },
        "per_problem": per_problem,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print()
    print("=" * 60)
    print(f"miniF2F calibration: {solved}/{attempted} closed "
          f"({100.0 * solved / max(1, attempted):.1f}%)")
    print(f"  wall-clock: {elapsed_total:.0f}s  api_calls: {api_calls}  "
          f"tokens: {tokens_in_total} in / {tokens_out_total} out  "
          f"est_spend: ${est_spend}")
    print("  per-category:")
    for cat in sorted(cat_counts):
        c = cat_counts[cat]
        s = cat_solved[cat]
        print(f"    {cat:24s}  {s}/{c}  ({100.0 * s / c:.1f}%)")
    print(f"  output: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
