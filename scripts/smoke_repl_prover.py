#!/usr/bin/env python3
"""Budget-bounded smoke for the REPL-driven proof generator.

Runs `prove_via_repl` on a small fixed set of UR-with-reviewed-equivalent
rows from the canonical 8-paper set. Compares closure to the whole-proof
generator's Round-VI baseline (0/5 closures, 79/79 lake errors). Each row
has hard caps on steps/attempts/timeout so the worst case is bounded.

Usage::

  python3 scripts/smoke_repl_prover.py [--rows N] [--max-steps N]

Outputs a JSON summary to output/leanstral_repl_smoke_summary.json.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from dotenv import load_dotenv  # type: ignore[import-not-found]
    load_dotenv()
except Exception:
    pass

import leanstral_repl_proof_generator as repl_gen  # noqa: E402
import leanstral_whole_proof_generator as wp_gen  # noqa: E402
from sweep_leanstral_whole_proof import _file_has_sorry_body_for  # noqa: E402


# Pre-selected: 5 short UR-with-reviewed-equivalent rows from canonical papers.
SMOKE_ROWS = [
    ("2304.09598", "Lem_Quant"),
    ("2304.09598", "Lem_IrredQFM"),
    ("2304.09598", "EqualLN"),
    ("2604.21314", "infty"),
    ("2604.21583", "prop_shell_vanishing_local"),
]


def _build_client():
    try:
        from mistralai import Mistral  # type: ignore[import-not-found]
    except Exception:
        try:
            from mistralai.client import Mistral  # type: ignore[import-not-found,no-redef]
        except Exception:
            return None
    key = os.environ.get("MISTRAL_API_KEY") or os.environ.get("LEANSTRAL_API_KEY")
    if not key:
        return None
    return Mistral(api_key=key)


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rows", type=int, default=5, help="How many rows to attempt (max 5).")
    p.add_argument("--max-steps", type=int, default=6)
    p.add_argument("--max-attempts-per-step", type=int, default=3)
    p.add_argument("--repl-timeout-s", type=int, default=180)
    p.add_argument("--summary", default="output/leanstral_repl_smoke_summary.json")
    args = p.parse_args()

    client = _build_client()
    if client is None:
        print("[error] no Mistral client (missing MISTRAL_API_KEY?)", file=sys.stderr)
        return 2

    rows = SMOKE_ROWS[: max(1, min(args.rows, len(SMOKE_ROWS)))]
    results: list[dict] = []
    closed_count = 0
    t_total_start = time.time()

    for paper_id, theorem_short in rows:
        led_path = PROJECT_ROOT / "output" / "verification_ledgers" / f"{paper_id}.json"
        lean_file = PROJECT_ROOT / "output" / f"{paper_id}.lean"
        paper_theory = PROJECT_ROOT / "Desol" / "PaperTheory" / f"Paper_{paper_id.replace('.', '_')}.lean"
        paper_theory_hint = ""
        if paper_theory.exists():
            paper_theory_hint = wp_gen.extract_paper_theory_hint(paper_theory)

        # Find the row by theorem short name.
        data = json.loads(led_path.read_text())
        entries = data if isinstance(data, list) else data.get("entries", [])
        match = None
        for e in entries:
            name = str(e.get("theorem_name", "") or "")
            if name.rsplit(".", 1)[-1] == theorem_short or name == theorem_short:
                match = e
                break
        if match is None:
            results.append({
                "paper_id": paper_id, "theorem": theorem_short, "skipped": "row_not_found",
            })
            continue

        is_sorry, _ = _file_has_sorry_body_for(lean_file, theorem_short)
        if not is_sorry:
            results.append({
                "paper_id": paper_id, "theorem": theorem_short, "skipped": "body_not_sorry",
            })
            continue

        lean_stmt = str(match.get("lean_statement", "") or "")
        print(f"[smoke] {paper_id} :: {theorem_short} (stmt_len={len(lean_stmt)}) starting", flush=True)
        t0 = time.time()
        diag_capture: list[dict] = []
        try:
            out = repl_gen.prove_via_repl(
                paper_id=paper_id,
                theorem_name=theorem_short,
                lean_statement=lean_stmt,
                paper_theory_hint=paper_theory_hint,
                paper_local_file=str(lean_file),
                client=client,
                max_steps=args.max_steps,
                max_attempts_per_step=args.max_attempts_per_step,
                repl_timeout_s=args.repl_timeout_s,
                diagnostic_log=diag_capture.append,
            )
        except Exception as exc:
            out = None
            err = str(exc)[:200]
            print(f"[smoke] {paper_id} :: {theorem_short} EXCEPTION: {err}", flush=True)
        elapsed = time.time() - t0
        row_summary = {
            "paper_id": paper_id,
            "theorem": theorem_short,
            "wall_clock_s": round(elapsed, 1),
            "closed": out is not None,
        }
        if out is not None:
            closed_count += 1
            row_summary["rounds"] = out.get("rounds")
            row_summary["proof_body_preview"] = out.get("proof_body", "")[:200]
            row_summary["api_calls"] = out.get("api_calls", 0)
            # Capture the first step's state -> tactic for the report.
            steps = out.get("steps") or []
            if steps:
                first = steps[0]
                row_summary["first_step_state"] = first.get("state_before", "")[:240]
                row_summary["first_step_tactic"] = first.get("chosen_tactic", "")
        else:
            row_summary["proof_body_preview"] = None
            # On failure, capture the diagnostic outcome + last few steps.
            if diag_capture:
                d = diag_capture[-1]
                row_summary["failure_outcome"] = d.get("outcome")
                row_summary["steps_failed"] = d.get("steps", [])[-3:]
                row_summary["api_calls"] = d.get("api_calls", 0)
                row_summary["accepted_before_fail"] = d.get("accepted_tactics", [])
        results.append(row_summary)
        print(f"[smoke] {paper_id} :: {theorem_short} closed={row_summary['closed']} t={row_summary['wall_clock_s']}s", flush=True)

    total = time.time() - t_total_start
    summary = {
        "rows_attempted": len(rows),
        "rows_closed": closed_count,
        "total_wall_clock_s": round(total, 1),
        "max_steps": args.max_steps,
        "max_attempts_per_step": args.max_attempts_per_step,
        "rows": results,
    }
    out_path = PROJECT_ROOT / args.summary
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("\n=== Smoke summary ===")
    print(json.dumps({"rows_attempted": len(rows), "rows_closed": closed_count, "total_s": round(total, 1)}, indent=2))
    print(f"[summary] wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
