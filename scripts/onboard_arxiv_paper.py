#!/usr/bin/env python3
"""End-to-end arxiv-paper onboarding — one command, scalable to any paper.

Wraps the full pipeline so that adding a new arxiv paper to the BDDM corpus
is a single invocation. Each step gates the next; the orchestrator stops
early on hard failures (e.g. translation totally rejected) and reports
honestly.

Pipeline stages run by this orchestrator (skip-flags available for each):

  1.  Translation: `arxiv_to_lean.py` ingests the paper, extracts theorems,
      writes `output/<paper-id>.lean`.
  2.  Translation lint: `translation_linter.py --severity error`. Pre-flight
      sanity check; surfaces typeclass-in-existential, latex-leak, etc.
  3.  Paper-theory builder: `paper_theory_builder` writes
      `Desol/PaperTheory/Paper_<safe-id>.lean` + auto-emits standard
      instances + aesop-tags axioms (Cluster A from earlier rounds).
  4.  Paper-imports anchor regen: `regenerate_paper_imports_anchor.py`
      (so REPL fallback can find the paper).
  5.  `repair_bad_translations`: post-translation fix pass — handles the
      latex-superscript / placeholder-abstraction patterns.
  6.  Initial prove sweep: `prove_arxiv_batch.py --disable-require-claim-equivalent`
      (so the per-row fidelity gate doesn't block before we even try).
  7.  Auto-alignment review (CoT Leanstral judge): the bridge admits any
      semantically-confirmed rows as `hybrid:conservative-assisted-review`.
  8.  Apply reviews to ledger: `apply_reviews_to_ledger.py` (gate flipping +
      promotion).
  9.  Backfill provenance: `backfill_provenance.py` (passes the
      `provenance_linked` gate).
  10. Audit + publish: `audit_axioms.py` produces a release-readiness report;
      results are mirrored to `reproducibility/full_paper_reports/<paper>/`.

Single-command usage:
    python3 scripts/onboard_arxiv_paper.py 2604.21884
        [--skip-translation]      # if .lean already exists
        [--skip-prove]            # if you only want to refresh metadata
        [--skip-cot-review]       # if you don't want to spend Mistral budget
        [--max-prove-time 1800]
        [--publish]               # mirror to reproducibility/

Returns 0 on success, 1 on hard failure (e.g. translation totally rejected).
Prints a concise per-stage summary; full per-stage logs go to
`logs/onboard_<paper-id>.json`.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
LOGS = ROOT / "logs"


def _safe_id(paper_id: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_]", "_", (paper_id or "").strip())


def _run(cmd: list[str], *, env: dict[str, str] | None = None, timeout: int = 0) -> dict[str, Any]:
    """Run a subprocess; return ok, returncode, stdout, stderr, time."""
    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout if timeout > 0 else None,
            env={**os.environ, **(env or {})},
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:] if proc.stdout else "",
            "stderr": proc.stderr[-4000:] if proc.stderr else "",
            "time_s": round(time.time() - start, 2),
            "cmd": " ".join(shlex.quote(x) for x in cmd),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": (exc.stdout or "")[-4000:] if exc.stdout else "",
            "stderr": f"TIMEOUT after {timeout}s",
            "time_s": round(time.time() - start, 2),
            "cmd": " ".join(shlex.quote(x) for x in cmd),
        }


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


def stage_translation(paper_id: str, args: argparse.Namespace) -> dict[str, Any]:
    out_lean = ROOT / "output" / f"{paper_id}.lean"
    if args.skip_translation and out_lean.exists():
        return {"stage": "translation", "ok": True, "skipped": True, "lean_file": str(out_lean)}
    # arxiv_to_lean.py takes the paper id as a positional argument, not --paper-id.
    result = _run([
        sys.executable, str(SCRIPTS / "arxiv_to_lean.py"),
        paper_id,
        "--out", str(out_lean),
        "--translate-only",  # skip the inline prove sweep; we have a dedicated stage_prove later
    ], timeout=1200)
    return {"stage": "translation", **result, "lean_file": str(out_lean), "lean_exists": out_lean.exists()}


def stage_translation_lint(paper_id: str, args: argparse.Namespace) -> dict[str, Any]:
    """Lint the .lean file and surface translation-quality issues.

    Note: the linter returns exit code 1 when it FINDS errors. This is
    correct CI behavior but is informational rather than a hard pipeline
    failure for the orchestrator — the prove cycle still runs (other gates
    catch unrecoverable rows). We mark `ok=True` here whenever the linter
    actually completed (regardless of issue count); the lint report is
    surfaced for awareness.
    """
    out_lean = ROOT / "output" / f"{paper_id}.lean"
    if not out_lean.exists():
        return {"stage": "translation_lint", "ok": False, "skipped": True, "reason": "no .lean file"}
    out_path = ROOT / "logs" / f"translation_lint_{paper_id}.json"
    result = _run([
        sys.executable, str(SCRIPTS / "translation_linter.py"),
        "--lean-file", str(out_lean),
        "--out", str(out_path),
        "--severity", "warning",
    ], timeout=60)
    # The linter's exit-1-on-errors is CI-style, not a pipeline error.
    # Promote to ok=True if the linter ran (i.e. wrote its output file).
    if out_path.exists():
        result["ok"] = True
        try:
            report = json.loads(out_path.read_text())
            result["lint_report"] = {
                "rows_checked": report.get("rows_checked"),
                "issue_kind_counts": report.get("issue_kind_counts"),
                "rows_with_errors": report.get("rows_with_errors"),
            }
        except Exception:
            pass
    return {"stage": "translation_lint", **result}


def stage_translation_autorepair(paper_id: str, args: argparse.Namespace) -> dict[str, Any]:
    """Run the auto-repair pass on the translated .lean file. Fixes the
    typeclass-in-existential and latex-brace bugs the linter detects.
    Idempotent. Skipped when --skip-translation (since we then assume the
    .lean is already what the user wants)."""
    out_lean = ROOT / "output" / f"{paper_id}.lean"
    if not out_lean.exists() or args.skip_translation:
        return {"stage": "translation_autorepair", "ok": True, "skipped": True}
    return {"stage": "translation_autorepair", **_run([
        sys.executable, str(SCRIPTS / "translation_autorepair.py"),
        "--lean-file", str(out_lean),
    ], timeout=300)}


def stage_paper_theory(paper_id: str, args: argparse.Namespace) -> dict[str, Any]:
    safe = _safe_id(paper_id)
    paper_lean = ROOT / "Desol" / "PaperTheory" / f"Paper_{safe}.lean"
    if paper_lean.exists() and args.skip_translation:
        return {"stage": "paper_theory", "ok": True, "skipped": True, "paper_theory_file": str(paper_lean)}
    # paper_theory_builder is invoked via formalize_paper_full normally; for a
    # standalone invocation we use its build helper directly.
    result = _run([
        sys.executable, "-c",
        (
            "import sys; sys.path.insert(0, 'scripts'); "
            "from paper_theory_builder import plan_paper_theory, write_paper_theory; "
            f"plan = plan_paper_theory(paper_id='{paper_id}', domain='', seed_text=''); "
            "from pathlib import Path; "
            "out = write_paper_theory(project_root=Path('.'), plan=plan); "
            "print(str(out))"
        ),
    ], timeout=300)
    return {"stage": "paper_theory", **result, "paper_theory_file": str(paper_lean)}


def stage_anchor_regen(paper_id: str, args: argparse.Namespace) -> dict[str, Any]:
    result = _run([
        sys.executable, str(SCRIPTS / "regenerate_paper_imports_anchor.py"),
    ], timeout=60)
    return {"stage": "anchor_regen", **result}


def stage_mathlib_alignment_search(paper_id: str, args: argparse.Namespace) -> dict[str, Any]:
    """Run automated Mathlib counterpart search on every `axiom` declaration
    in the paper-theory file. Top candidates with elaboration_check=ok and
    high confidence are auto-registered into `output/corpus/alignments.json`,
    discharging axiom_debt at AB→FP gate time without manual intervention.

    Skipped when:
      - --skip-alignment-search is passed
      - The paper-theory file has no `axiom` declarations
      - MISTRAL_API_KEY is unset
    """
    if getattr(args, "skip_alignment_search", False):
        return {"stage": "mathlib_alignment_search", "ok": True, "skipped": True}
    if not os.environ.get("MISTRAL_API_KEY", "").strip():
        return {
            "stage": "mathlib_alignment_search",
            "ok": True,
            "skipped": True,
            "reason": "MISTRAL_API_KEY not set",
        }
    cmd = [
        sys.executable, str(SCRIPTS / "mathlib_alignment_search.py"),
        paper_id,
        "--auto-register",
    ]
    # Per-paper search log (timestamped) so we can audit auto-registrations.
    out_path = ROOT / "logs" / f"mathlib_alignment_search_{paper_id}.json"
    cmd += ["--out", str(out_path)]
    result = _run(cmd, timeout=900)
    return {"stage": "mathlib_alignment_search", **result, "out_path": str(out_path)}


def stage_prove(paper_id: str, args: argparse.Namespace) -> dict[str, Any]:
    out_lean = ROOT / "output" / f"{paper_id}.lean"
    if not out_lean.exists():
        return {"stage": "prove", "ok": False, "skipped": True, "reason": "no .lean file"}
    if args.skip_prove:
        return {"stage": "prove", "ok": True, "skipped": True}
    result = _run([
        sys.executable, str(SCRIPTS / "prove_arxiv_batch.py"),
        "--lean-file", str(out_lean),
        "--paper-id", paper_id,
        "--mode", "state-mcts",
        "--disable-require-claim-equivalent",
    ], timeout=args.max_prove_time)
    return {"stage": "prove", **result}


def stage_cot_review(paper_id: str, args: argparse.Namespace) -> dict[str, Any]:
    if args.skip_cot_review:
        return {"stage": "cot_review", "ok": True, "skipped": True}
    # Refresh corpus + batch first
    refresh = _run([sys.executable, str(SCRIPTS / "export_corpus.py")], timeout=300)
    if not refresh["ok"]:
        return {"stage": "cot_review", "ok": False, "reason": "corpus refresh failed", "refresh": refresh}
    rebuild = _run([sys.executable, str(SCRIPTS / "build_statement_review_batch.py"), "--limit", "200"], timeout=120)
    review = _run([
        sys.executable, str(SCRIPTS / "run_auto_alignment_review.py"),
    ], timeout=2400)
    return {"stage": "cot_review", "ok": review["ok"], "rebuild": rebuild, "review": review}


def stage_apply_reviews(paper_id: str, args: argparse.Namespace) -> dict[str, Any]:
    bridge = _run([
        sys.executable, str(SCRIPTS / "run_review_to_gold_proof_bridge.py"),
        "--apply-to-ledger",
    ], timeout=300)
    return {"stage": "apply_reviews", **bridge}


def stage_backfill_provenance(paper_id: str, args: argparse.Namespace) -> dict[str, Any]:
    return {"stage": "backfill_provenance", **_run([sys.executable, str(SCRIPTS / "backfill_provenance.py")], timeout=60)}


def stage_audit(paper_id: str, args: argparse.Namespace) -> dict[str, Any]:
    return {"stage": "axiom_audit", **_run([
        sys.executable, str(SCRIPTS / "audit_axioms.py"),
        "--paper-id", paper_id,
    ], timeout=60)}


def stage_publish(paper_id: str, args: argparse.Namespace) -> dict[str, Any]:
    if not args.publish:
        return {"stage": "publish", "ok": True, "skipped": True}
    src = ROOT / "output" / "verification_ledgers" / f"{paper_id}.json"
    dst = ROOT / "reproducibility" / "full_paper_reports" / paper_id / "verification_ledger.json"
    if not src.exists():
        return {"stage": "publish", "ok": False, "reason": f"no source ledger: {src}"}
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    return {"stage": "publish", "ok": True, "src": str(src), "dst": str(dst)}


def main() -> int:
    parser = argparse.ArgumentParser(description="End-to-end arxiv-paper onboarding")
    parser.add_argument("paper_id", help="arxiv id, e.g. 2604.21884")
    parser.add_argument("--skip-translation", action="store_true", help="reuse existing output/<id>.lean")
    parser.add_argument("--skip-prove", action="store_true", help="skip prove_arxiv_batch")
    parser.add_argument("--skip-cot-review", action="store_true", help="skip the auto-alignment CoT review")
    parser.add_argument(
        "--skip-alignment-search",
        action="store_true",
        help="skip the automated Mathlib counterpart search on paper-theory axioms",
    )
    parser.add_argument("--max-prove-time", type=int, default=2400, help="seconds budget for prove sweep")
    parser.add_argument("--publish", action="store_true", help="mirror final ledger to reproducibility/")
    parser.add_argument("--out-summary", type=Path, default=None, help="path to write per-stage JSON summary")
    args = parser.parse_args()

    LOGS.mkdir(parents=True, exist_ok=True)

    print(f"=== Onboarding arxiv:{args.paper_id} ===")
    stages = []
    for stage_fn in (
        stage_translation,
        stage_translation_lint,
        stage_translation_autorepair,
        stage_paper_theory,
        stage_anchor_regen,
        # Mathlib alignment search runs AFTER paper-theory builds the axiom
        # declarations and BEFORE prove_arxiv_batch consumes them. Auto-
        # registering the high-confidence + elaboration-OK candidates lets
        # the apply step discharge those debts without manual intervention.
        stage_mathlib_alignment_search,
        stage_prove,
        stage_cot_review,
        stage_apply_reviews,
        stage_backfill_provenance,
        stage_audit,
        stage_publish,
    ):
        s = stage_fn(args.paper_id, args)
        stages.append(s)
        marker = "✓" if s.get("ok") else ("○" if s.get("skipped") else "✗")
        print(f"  {marker} {s['stage']:25s}  ({s.get('time_s', 0):.1f}s)")
        # Hard-fail short-circuit on translation: if no .lean, stop.
        if s["stage"] == "translation" and not s.get("ok"):
            print("    translation failed; aborting downstream stages")
            break

    # Summary
    out_summary = args.out_summary or (LOGS / f"onboard_{args.paper_id}.json")
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    out_summary.write_text(json.dumps({"paper_id": args.paper_id, "stages": stages}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Final ledger summary
    ledger_path = ROOT / "output" / "verification_ledgers" / f"{args.paper_id}.json"
    if ledger_path.exists():
        try:
            from collections import Counter
            data = json.loads(ledger_path.read_text())
            entries = data if isinstance(data, list) else data.get("entries", [])
            counts = Counter(r.get("status", "") for r in entries)
            closed = sum(counts.get(s, 0) for s in ("FULLY_PROVEN", "AXIOM_BACKED", "INTERMEDIARY_PROVEN"))
            print(f"\n=== Final: {closed}/{len(entries)} closed ({closed/max(1,len(entries))*100:.0f}%) ===")
            print(f"  status: {dict(counts)}")
        except Exception as exc:
            print(f"  (could not read final ledger: {exc})")

    print(f"\nSummary: {out_summary}")
    return 0 if all(s.get("ok") or s.get("skipped") for s in stages) else 1


if __name__ == "__main__":
    raise SystemExit(main())
