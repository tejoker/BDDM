#!/usr/bin/env python3
"""Round-IV proof-search sweep over canonical UR/IP rows with verified statements.

Iterates per-paper across the 8 canonical papers. Per candidate row:
  1. Elaboration probe: prove_arxiv_batch._run_isolated_file_check on the
     row's lean_statement. Skip if it doesn't elaborate; statement-repair
     (phase C) is the right tool.
  2. Tactic cascade: invoke prove_arxiv_batch.main(...) as a subprocess with
     --paper-id <pid> --lean-file output/<pid>.lean --target-theorem <name>.
     prove_arxiv_batch writes the ephemeral ledger automatically.
  3. Re-read the ephemeral ledger to check whether the row closed
     (proof_method != 'translation-limited' and proof_text without 'sorry').

Budget: 8-min wall per row; 180-min overall. Honest accounting: failures
remain UR/IP. Emits a JSON summary to logs/sweep_canonical_proof_search.json.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Bootstrap sys.path so we can import prove_arxiv_batch helpers.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

PROJECT_ROOT = _HERE.parent
CANONICAL = [
    "2012.09271",
    "2304.09598",
    "2401.04567",
    "2604.21314",
    "2604.21583",
    "2604.21616",
    "2604.21821",
    "2604.21884",
]


def _load_dotenv() -> None:
    env = PROJECT_ROOT / ".env"
    if not env.exists():
        return
    for ln in env.read_text().splitlines():
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _enumerate_candidates() -> list[dict]:
    cands: list[dict] = []
    for pid in CANONICAL:
        p = PROJECT_ROOT / "reproducibility" / "full_paper_reports" / pid / "verification_ledger.json"
        if not p.exists():
            continue
        data = json.loads(p.read_text())
        entries = data if isinstance(data, list) else data.get("entries", [])
        for e in entries:
            row = {
                "paper_id": pid,
                "theorem_name": e.get("theorem_name"),
                "status": e.get("status"),
                "lean_statement": e.get("lean_statement") or "",
                "reviewed_equivalence_verdict": e.get("reviewed_equivalence_verdict"),
                "gate_failures": e.get("gate_failures") or [],
                "kind": None,
            }
            if e.get("status") == "UNRESOLVED" and e.get("reviewed_equivalence_verdict") == "equivalent":
                row["kind"] = "UR_eq"
                cands.append(row)
            elif e.get("status") == "INTERMEDIARY_PROVEN" and "lean_proof_closed" in (e.get("gate_failures") or []):
                row["kind"] = "IP_blocked"
                cands.append(row)
    return cands


def _decl_in_file(name: str, lean_file: Path) -> bool:
    if not lean_file.exists():
        return False
    text = lean_file.read_text(encoding="utf-8")
    pat = re.compile(rf"\b(?:theorem|lemma|def|noncomputable\s+def|noncomputable\s+theorem|noncomputable\s+lemma|proposition|axiom|corollary|remark)\s+{re.escape(name)}\b")
    return bool(pat.search(text))


def _elaboration_probe(
    *,
    lean_statement: str,
    lean_file: Path,
    theorem_name: str,
    paper_id: str = "",
    use_fast: bool = True,
    diff_check: bool = False,
) -> tuple[bool, str]:
    """Run the isolated elaboration check using the ledger's lean_statement.
    Returns (ok, error_tail).

    When ``use_fast`` is True we route through ``lake_validation_cache`` so
    the Mathlib import is paid once per process. ``diff_check`` runs BOTH
    validators and asserts agreement (used for the first N rows of a sweep).
    """
    decl = (lean_statement or "").strip()
    if not decl:
        return False, "empty_lean_statement"
    if not decl.lstrip().startswith(("theorem", "lemma", "def", "noncomputable", "axiom", "private")):
        # Wrap if it's a bare body — unlikely for our rows.
        return False, "non_decl_lean_statement"

    if use_fast:
        try:
            import lake_validation_cache as _lvc  # type: ignore
        except Exception:
            _lvc = None  # type: ignore[assignment]
        if _lvc is not None:
            if diff_check:
                ok, tail, diag = _lvc.differential_check(
                    project_root=PROJECT_ROOT, source_file=lean_file,
                    paper_id=paper_id or theorem_name, theorem_decl=decl, timeout_s=60,
                )
                if not diag.get("agreement", True):
                    print(
                        f"[fast-validation][DIVERGENCE] {paper_id}::{theorem_name} "
                        f"fast_ok={diag['fast_ok']} slow_ok={diag['slow_ok']}",
                        flush=True,
                    )
                return ok, tail
            return _lvc.validated_isolated_check(
                project_root=PROJECT_ROOT, paper_id=paper_id or theorem_name,
                theorem_decl=decl, timeout_s=60,
            )

    from prove_arxiv_batch import _run_isolated_file_check  # type: ignore
    return _run_isolated_file_check(
        project_root=PROJECT_ROOT,
        source_file=lean_file,
        theorem_decl=decl,
        timeout_s=60,
    )


def _load_ledger_row(paper_id: str, theorem_name: str) -> dict | None:
    p = PROJECT_ROOT / "output" / "verification_ledgers" / f"{paper_id}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except Exception:
        return None
    entries = data if isinstance(data, list) else data.get("entries", [])
    for e in entries:
        if e.get("theorem_name") == theorem_name:
            return e
    return None


def _ledger_row_proved(row: dict | None) -> bool:
    if not row:
        return False
    if row.get("status") not in ("FULLY_PROVEN", "INTERMEDIARY_PROVEN", "AXIOM_BACKED"):
        return False
    # Use proof_text + status discrimination from the ledger directly.
    proof_text = row.get("proof_text") or ""
    if "sorry" in proof_text:
        return False
    return bool(row.get("proved")) or row.get("status") == "FULLY_PROVEN"


def _invoke_prove(*, paper_id: str, lean_file: Path, theorem_name: str, timeout_s: int) -> tuple[int, str]:
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "prove_arxiv_batch.py"),
        "--lean-file", str(lean_file),
        "--paper-id", paper_id,
        "--target-theorem", theorem_name,
        "--disable-require-claim-equivalent",
    ]
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=os.environ.copy(),
        )
        elapsed = time.monotonic() - start
        out_tail = ((proc.stdout or "") + "\n" + (proc.stderr or ""))[-2000:]
        return proc.returncode, f"elapsed={elapsed:.1f}s\n{out_tail}"
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - start
        out_tail = (((exc.stdout or "") if isinstance(exc.stdout, str) else "") + "\n" + ((exc.stderr or "") if isinstance(exc.stderr, str) else ""))[-2000:]
        return 124, f"TIMEOUT after {elapsed:.1f}s\n{out_tail}"
    except Exception as exc:
        return 1, f"exception: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-row-timeout", type=int, default=480, help="Per-row wall budget in seconds (default 8 min)")
    parser.add_argument("--overall-timeout", type=int, default=180 * 60, help="Overall sweep budget in seconds (default 180 min)")
    parser.add_argument("--summary-out", type=Path, default=PROJECT_ROOT / "logs" / "sweep_canonical_proof_search.json")
    parser.add_argument("--paper", action="append", default=[], help="Restrict to specific paper_id(s)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-elaboration-probe", action="store_true")
    parser.add_argument(
        "--use-fast-validation",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Route the elaboration probe through scripts/lake_validation_cache "
            "(persistent REPL worker). Use --no-use-fast-validation to force "
            "the legacy `lake env lean` path."
        ),
    )
    parser.add_argument(
        "--differential-check-first",
        type=int,
        default=10,
        help="With --use-fast-validation, run BOTH validators for the first N rows and assert agreement (0 = disable).",
    )
    args = parser.parse_args()
    diff_remaining = max(0, int(args.differential_check_first)) if args.use_fast_validation else 0

    _load_dotenv()
    if not os.environ.get("MISTRAL_API_KEY"):
        print("[error] MISTRAL_API_KEY not set in env or .env", file=sys.stderr)
        return 1

    cands = _enumerate_candidates()
    if args.paper:
        keep = set(args.paper)
        cands = [c for c in cands if c["paper_id"] in keep]
    print(f"[sweep] candidates: {len(cands)} across {len(set(c['paper_id'] for c in cands))} papers")

    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    overall_start = time.monotonic()
    closed_by_paper: dict[str, int] = {}
    attempted_by_paper: dict[str, int] = {}
    elab_skipped: list[dict] = []

    # Group by paper to avoid cross-paper state churn.
    by_paper: dict[str, list[dict]] = {}
    for c in cands:
        by_paper.setdefault(c["paper_id"], []).append(c)

    for pid, rows in by_paper.items():
        lean_file = PROJECT_ROOT / "output" / f"{pid}.lean"
        print(f"\n=== Paper {pid} ({len(rows)} rows) ===")
        for r in rows:
            tn = r["theorem_name"]
            elapsed_overall = time.monotonic() - overall_start
            if elapsed_overall > args.overall_timeout:
                print(f"[budget] overall budget exceeded ({elapsed_overall:.0f}s > {args.overall_timeout}s); stopping")
                r_out = dict(r)
                r_out["outcome"] = "skipped_budget_exhausted"
                results.append(r_out)
                continue

            # Skip rows whose declaration isn't actually in the on-disk .lean
            # (prove_arxiv_batch can only target declarations present there).
            in_file = _decl_in_file(tn, lean_file)
            elab_ok, elab_msg = (True, "")
            if not args.skip_elaboration_probe:
                do_diff = diff_remaining > 0
                if do_diff:
                    diff_remaining -= 1
                elab_ok, elab_msg = _elaboration_probe(
                    lean_statement=r["lean_statement"],
                    lean_file=lean_file,
                    theorem_name=tn,
                    paper_id=pid,
                    use_fast=bool(args.use_fast_validation),
                    diff_check=do_diff,
                )
            r_out = dict(r)
            r_out["in_lean_file"] = in_file
            r_out["elaboration_ok"] = elab_ok
            r_out["elaboration_error"] = elab_msg

            if not in_file:
                r_out["outcome"] = "skipped_not_in_lean_file"
                print(f"[skip] {pid}::{tn}  NOT_IN_LEAN_FILE")
                results.append(r_out)
                continue
            if not elab_ok:
                r_out["outcome"] = "skipped_elaboration_fail"
                elab_skipped.append({"paper_id": pid, "theorem_name": tn, "error": elab_msg[:200]})
                print(f"[skip] {pid}::{tn}  ELABORATION_FAIL: {elab_msg[:120]}")
                results.append(r_out)
                continue

            print(f"[prove] {pid}::{tn}  (status={r['status']}/{r['kind']})")
            if args.dry_run:
                r_out["outcome"] = "dry_run"
                results.append(r_out)
                continue
            attempted_by_paper[pid] = attempted_by_paper.get(pid, 0) + 1

            # Snapshot pre-state.
            pre_row = _load_ledger_row(pid, tn) or {}
            pre_status = pre_row.get("status")
            pre_proved = bool(pre_row.get("proved"))

            rc, log_tail = _invoke_prove(
                paper_id=pid,
                lean_file=lean_file,
                theorem_name=tn,
                timeout_s=args.per_row_timeout,
            )
            r_out["prove_returncode"] = rc
            r_out["log_tail"] = log_tail[-1500:]

            post_row = _load_ledger_row(pid, tn) or {}
            r_out["post_status"] = post_row.get("status")
            r_out["post_proved"] = bool(post_row.get("proved"))
            r_out["post_proof_method"] = post_row.get("proof_method")
            r_out["pre_status"] = pre_status
            r_out["pre_proved"] = pre_proved

            closed = _ledger_row_proved(post_row) and not _ledger_row_proved(pre_row)
            if closed:
                closed_by_paper[pid] = closed_by_paper.get(pid, 0) + 1
                r_out["outcome"] = "closed"
                print(f"[CLOSE] {pid}::{tn}  -> {post_row.get('status')}")
            else:
                # Status may still have flipped (UR→IP, UR→AB without proof_text).
                if post_row.get("status") and post_row.get("status") != pre_status:
                    r_out["outcome"] = f"status_change:{pre_status}->{post_row.get('status')}"
                    print(f"[status] {pid}::{tn}  {pre_status} -> {post_row.get('status')}")
                else:
                    r_out["outcome"] = "no_change"
                    print(f"[noop ] {pid}::{tn}  remains {post_row.get('status') or pre_status}")
            results.append(r_out)

            # Persist incrementally.
            args.summary_out.write_text(json.dumps({
                "results": results,
                "closed_by_paper": closed_by_paper,
                "attempted_by_paper": attempted_by_paper,
                "elapsed_s": time.monotonic() - overall_start,
            }, indent=2))

    summary = {
        "results": results,
        "closed_by_paper": closed_by_paper,
        "attempted_by_paper": attempted_by_paper,
        "elaboration_skipped": elab_skipped,
        "elapsed_s": time.monotonic() - overall_start,
    }
    args.summary_out.write_text(json.dumps(summary, indent=2))
    print(f"\n[sweep] complete. attempted={sum(attempted_by_paper.values())} closed={sum(closed_by_paper.values())} elaboration_skipped={len(elab_skipped)} elapsed={(time.monotonic()-overall_start):.1f}s")
    print(f"[sweep] summary written to {args.summary_out}")
    if args.use_fast_validation:
        try:
            import lake_validation_cache as _lvc  # type: ignore
            _lvc.shutdown_all_workers()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
