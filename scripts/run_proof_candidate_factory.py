#!/usr/bin/env python3
"""Proof-candidate factory: repair → re-review → gold proof queue.

Full loop (no proof search):
    bad source row
    → source context pack (deterministic, no LLM)
    → regenerated Lean statement
    → validity gate (classify_statement + statement_fidelity_gate)
    → auto alignment review (stateless LLM, requires MISTRAL_API_KEY)
    → gold proof queue

Proof search should resume only after `gold_proof_queue_rows_after > 0`.

Phases
------
Phase 1 – Repair (always runs, no LLM)
    Reads statement_repair_queue.jsonl, executes source-backed statement
    regeneration, applies validated candidates to ledgers, and rebuilds all
    downstream queues (corpus → fidelity → repair → review batch → gold proof
    queue).  Repairs that produce no new graduated rows are auto-rolled-back.

Phase 2 – Auto-review (skipped when MISTRAL_API_KEY is absent or --repair-only)
    Runs stateless reverse-translation + judge on the updated review batch.
    Writes to auto_alignment_reviews.jsonl.

Phase 3 – Bridge to gold (always runs after phase 1 or 2)
    Applies all available reviews (auto + assisted) to the corpus, rebuilds
    the gold proof queue, and writes reviewed_statement_corpus.jsonl.

Delta report
    Prints before/after counts for: repair queue, gold proof queue,
    reviewed-exact rows.  Exit code 0 even when gold queue stays empty.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_gold_proof_queue import DEFAULT_OUT_JSONL as _GOLD_JSONL
from build_statement_review_batch import DEFAULT_OUT_JSONL as _REVIEW_BATCH_JSONL
from export_corpus import DEFAULT_OUT_JSONL as _CORPUS_JSONL
from run_auto_alignment_review import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_COMPONENT_THRESHOLD,
    DEFAULT_MODEL as _DEFAULT_REVIEW_MODEL,
    DEFAULT_OUT_REVIEWS as _AUTO_REVIEWS_JSONL,
    DEFAULT_OUT_SUMMARY as _AUTO_REVIEWS_SUMMARY,
    DEFAULT_OUT_TRIAGE as _AUTO_REVIEWS_TRIAGE,
    run_auto_alignment_review,
)
from run_review_to_gold_proof_bridge import (
    DEFAULT_OUT_GOLD_JSONL as _BRIDGE_GOLD_JSONL,
    DEFAULT_OUT_REVIEWED_CORPUS_JSONL as _REVIEWED_CORPUS_JSONL,
    DEFAULT_OUT_REVIEWS_JSONL as _ASSISTED_REVIEWS_JSONL,
    DEFAULT_OUT_SUMMARY as _BRIDGE_SUMMARY,
    run_review_to_gold_bridge,
)
from run_statement_repair_worker import (
    DEFAULT_LEDGER_DIR,
    DEFAULT_OUT_ACTIONS,
    DEFAULT_OUT_SUMMARY,
    DEFAULT_REPAIR_QUEUE_OUT,
    DEFAULT_REPORT_DIR,
    DEFAULT_EVIDENCE_DIR,
    run_worker,
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except Exception:
            continue
        if isinstance(raw, dict):
            rows.append(raw)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _count_reviewed_exact(rows: list[dict[str, Any]]) -> int:
    return sum(
        1 for r in rows
        if str(r.get("reviewed_statement_alignment_class", "")) == "exact"
        or str(r.get("reviewed_equivalence_verdict", "")) in {"equivalent", "exact"}
    )


def _count_reviewed_exact_from_reviews(reviews: list[dict[str, Any]]) -> int:
    return sum(
        1 for r in reviews
        if str(r.get("reviewed_statement_alignment_class", "")) == "exact"
    )


def _project_path(project_root: Path, rel: Path) -> Path:
    return rel if rel.is_absolute() else project_root / rel


def run_factory(
    *,
    project_root: Path,
    repair_queue_jsonl: Path | None = None,
    limit: int = 500,
    max_write_groups: int = 10,
    repair_only: bool = False,
    review_only: bool = False,
    review_model: str = _DEFAULT_REVIEW_MODEL,
    review_confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    review_component_threshold: float = DEFAULT_COMPONENT_THRESHOLD,
    review_rate_delay: float = 0.5,
    dry_run: bool = False,
    ledger_paths: list[Path] | None = None,
    report_roots: list[Path] | None = None,
    evidence_roots: list[Path] | None = None,
) -> dict[str, Any]:
    now_str = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    repair_queue_path = _project_path(project_root, repair_queue_jsonl or DEFAULT_REPAIR_QUEUE_OUT)
    review_batch_path = _project_path(project_root, _REVIEW_BATCH_JSONL)
    corpus_path = _project_path(project_root, _CORPUS_JSONL)
    gold_queue_path = _project_path(project_root, _GOLD_JSONL)
    auto_reviews_path = _project_path(project_root, _AUTO_REVIEWS_JSONL)
    auto_reviews_summary_path = _project_path(project_root, _AUTO_REVIEWS_SUMMARY)
    auto_reviews_triage_path = _project_path(project_root, _AUTO_REVIEWS_TRIAGE)
    assisted_reviews_path = _project_path(project_root, _ASSISTED_REVIEWS_JSONL)
    reviewed_corpus_path = _project_path(project_root, _REVIEWED_CORPUS_JSONL)
    bridge_gold_path = _project_path(project_root, _BRIDGE_GOLD_JSONL)
    bridge_summary_path = _project_path(project_root, _BRIDGE_SUMMARY)

    # --- Snapshot before -------------------------------------------------------
    before_repair_queue = _read_jsonl(repair_queue_path)
    before_gold_queue = _read_jsonl(gold_queue_path)
    before_auto_reviews = _read_jsonl(auto_reviews_path)
    before_corpus = _read_jsonl(corpus_path)
    before_reviewed_exact = _count_reviewed_exact_from_reviews(before_auto_reviews)

    result: dict[str, Any] = {
        "schema_version": "proof_candidate_factory.v1",
        "timestamp": now_str,
        "project_root": str(project_root),
        "dry_run": dry_run,
        "repair_only": repair_only,
        "review_only": review_only,
        "non_promotable": bool(dry_run),
        "before": {
            "repair_queue_rows": len(before_repair_queue),
            "gold_proof_queue_rows": len(before_gold_queue),
            "auto_reviewed_exact": before_reviewed_exact,
        },
        "phases": {},
    }

    print(f"\n{'='*64}")
    print(f"Proof-Candidate Factory  [{now_str}]")
    print(f"{'='*64}")
    print(f"  repair queue : {len(before_repair_queue)} rows")
    print(f"  gold queue   : {len(before_gold_queue)} rows  ← target: >5")
    print(f"  reviewed-exact: {before_reviewed_exact}  ← target: >10")
    print(f"  dry_run={dry_run}  repair_only={repair_only}  review_only={review_only}")
    print()

    # =========================================================================
    # Phase 1: Repair  (skipped when --review-only)
    # =========================================================================
    print("Phase 1 — Statement Repair (deterministic, source-backed)")
    print("-" * 50)
    if review_only:
        print("  [skip] --review-only flag set")
        result["phases"]["repair"] = {"status": "skipped_review_only"}
    else:
        repair_rows = _read_jsonl(repair_queue_path)
        if not repair_rows:
            print("  [skip] repair queue is empty")
            result["phases"]["repair"] = {"status": "skipped_empty_queue"}
        else:
            print(f"  {len(repair_rows)} rows to process  (max_write_groups={max_write_groups})")
            _executed_actions, worker_summary = run_worker(
                repair_rows,
                project_root=project_root,
                write=not dry_run,
                limit=limit,
                max_write_groups=max_write_groups,
                validate_candidates=True,
                ledger_paths=[_project_path(project_root, p) for p in (ledger_paths or [DEFAULT_LEDGER_DIR])],
                report_roots=[_project_path(project_root, p) for p in (report_roots or [DEFAULT_REPORT_DIR])],
                evidence_roots=[_project_path(project_root, p) for p in (evidence_roots or [DEFAULT_EVIDENCE_DIR])],
            )
            net_graduated = int(worker_summary.get("net_graduated_rows", 0) or 0)
            wrote = sum(1 for a in _executed_actions if bool(a.get("mutated")))
            mutated_rows = sum(int(a.get("mutated_rows", 0) or 0) for a in _executed_actions)
            rolled_back = bool(worker_summary.get("rollback"))
            review_batch_after = int(worker_summary.get("review_batch_rows_after", 0) or 0)
            gold_after_repair = int(worker_summary.get("gold_proof_queue_rows_after", 0) or 0)

            print(f"  wrote {wrote} action groups  ({mutated_rows} rows mutated)")
            print(f"  net graduated: {net_graduated}")
            print(f"  review batch after rebuild: {review_batch_after}")
            print(f"  gold proof queue after repair: {gold_after_repair}")
            if rolled_back:
                print("  [rollback] no rows graduated — changes reverted")
            result["phases"]["repair"] = {
                "status": "rolled_back" if rolled_back else ("dry_run" if dry_run else "completed"),
                "wrote_groups": wrote,
                "mutated_rows": mutated_rows,
                "net_graduated_rows": net_graduated,
                "review_batch_rows_after": review_batch_after,
                "gold_proof_queue_rows_after": gold_after_repair,
                "rollback": rolled_back,
                "worker_summary": {
                    k: v for k, v in worker_summary.items()
                    if k not in ("post_rebuild",)
                },
            }
    print()

    if repair_only:
        _print_delta(result, _read_jsonl(repair_queue_path), _read_jsonl(bridge_gold_path))
        return result

    # =========================================================================
    # Phase 2: Auto Alignment Review  (requires MISTRAL_API_KEY)
    # =========================================================================
    print("Phase 2 — Auto Alignment Review (stateless LLM)")
    print("-" * 50)
    api_key = os.environ.get("MISTRAL_API_KEY", "").strip()
    if not api_key:
        print("  [skip] MISTRAL_API_KEY not set — run with MISTRAL_API_KEY=... to enable review")
        result["phases"]["auto_review"] = {"status": "skipped_no_api_key"}
    else:
        updated_batch = _read_jsonl(review_batch_path)
        print(f"  {len(updated_batch)} rows in updated review batch")
        try:
            review_summary = run_auto_alignment_review(
                batch_jsonl=review_batch_path,
                out_reviews=auto_reviews_path,
                out_summary=auto_reviews_summary_path,
                out_triage=auto_reviews_triage_path,
                model=review_model,
                confidence_threshold=review_confidence_threshold,
                component_threshold=review_component_threshold,
                existing_reviews=_read_jsonl(auto_reviews_path),
                rate_delay=review_rate_delay,
                dry_run=dry_run,
            )
            promoted = int(review_summary.get("reviewed_exact", 0) or 0)
            not_equiv = int(review_summary.get("not_equivalent", 0) or 0)
            needs_human = int(review_summary.get("needs_human", 0) or 0)
            errors = int(review_summary.get("errors", 0) or 0)
            print(f"  reviewed_exact={promoted}  not_equivalent={not_equiv}  needs_human={needs_human}  errors={errors}")
            result["phases"]["auto_review"] = {
                "status": "dry_run" if dry_run else "completed",
                "model": review_model,
                "reviewed_exact": promoted,
                "not_equivalent": not_equiv,
                "needs_human": needs_human,
                "errors": errors,
            }
        except Exception as exc:
            print(f"  [error] auto review failed: {exc}")
            result["phases"]["auto_review"] = {"status": "error", "error": str(exc)}
    print()

    # =========================================================================
    # Phase 3: Bridge to Gold Proof Queue
    # =========================================================================
    print("Phase 3 — Bridge to Gold Proof Queue")
    print("-" * 50)
    corpus_rows = _read_jsonl(corpus_path)
    batch_rows = _read_jsonl(review_batch_path)
    auto_reviews = _read_jsonl(auto_reviews_path)

    print(f"  corpus: {len(corpus_rows)} rows  batch: {len(batch_rows)} rows  reviews: {len(auto_reviews)}")
    if dry_run:
        print("  [dry_run] bridge computation skipped — no files written")
        result["phases"]["bridge"] = {"status": "dry_run"}
    else:
        try:
            assisted_reviews, reviewed_corpus, gold_queue, bridge_summary_data = run_review_to_gold_bridge(
                batch_rows=batch_rows,
                corpus_rows=corpus_rows,
                additional_reviews=auto_reviews,
                reviewed_at=now_str,
            )
            _write_jsonl(assisted_reviews_path, assisted_reviews)
            _write_jsonl(reviewed_corpus_path, reviewed_corpus)
            _write_jsonl(bridge_gold_path, gold_queue)
            _write_json(bridge_summary_path, bridge_summary_data)

            gold_rows = len(gold_queue)
            assisted_exact = int(bridge_summary_data.get("assisted_reviewed_exact_rows", 0) or 0)
            promoted_gold = int(bridge_summary_data.get("promoted_alignment_gold", 0) or 0)
            print(f"  assisted_reviewed_exact: {assisted_exact}")
            print(f"  promoted_alignment_gold: {promoted_gold}")
            print(f"  gold proof queue rows  : {gold_rows}")
            result["phases"]["bridge"] = {
                "status": "completed",
                "assisted_reviewed_exact": assisted_exact,
                "promoted_alignment_gold": promoted_gold,
                "gold_proof_queue_rows": gold_rows,
            }
        except Exception as exc:
            print(f"  [error] bridge failed: {exc}")
            result["phases"]["bridge"] = {"status": "error", "error": str(exc)}
    print()

    _print_delta(result, _read_jsonl(repair_queue_path), _read_jsonl(bridge_gold_path))
    return result


def _print_delta(result: dict[str, Any], after_repair_queue: list, after_gold_queue: list) -> None:
    before = result["before"]
    before_repair = before["repair_queue_rows"]
    before_gold = before["gold_proof_queue_rows"]
    before_exact = before["auto_reviewed_exact"]

    after_repair = len(after_repair_queue)
    after_gold = len(after_gold_queue)
    after_exact = (
        int(result.get("phases", {}).get("bridge", {}).get("assisted_reviewed_exact", 0) or 0)
        + int(result.get("phases", {}).get("auto_review", {}).get("reviewed_exact", 0) or 0)
        + before_exact
    )

    repair_delta = after_repair - before_repair
    gold_delta = after_gold - before_gold
    exact_delta = after_exact - before_exact

    def _arrow(n: int, *, lower_is_better: bool = False) -> str:
        if n == 0:
            return "—"
        if lower_is_better:
            return f"↓{abs(n)}" if n < 0 else f"↑{n} (unexpected)"
        return f"↑{n}" if n > 0 else f"↓{abs(n)}"

    print("=" * 64)
    print("Delta Summary")
    print("=" * 64)
    print(f"  repair queue   : {before_repair:>4} → {after_repair:<4}  {_arrow(repair_delta, lower_is_better=True)}")
    print(f"  reviewed-exact : {before_exact:>4} → {after_exact:<4}  {_arrow(exact_delta)}")
    print(f"  gold proof queue: {before_gold:>3} → {after_gold:<4}  {_arrow(gold_delta)}")
    print()
    if after_gold > 0:
        print(f"  ✓ Gold queue ready — run proof search on {after_gold} candidate(s)")
    else:
        print("  ✗ Gold queue empty — check blockers before running proof search")
        _suggest_next_actions(result)
    print()


def _suggest_next_actions(result: dict[str, Any]) -> None:
    repair_phase = result.get("phases", {}).get("repair", {})
    if repair_phase.get("rollback"):
        print(
            "  Next: inspect repair blockers in"
            " output/corpus/statement_repair_worker_actions.jsonl"
        )
        print(
            "  The repair worker rolled back because source-backed regeneration"
            " did not produce any rows that passed the graduation gate."
        )
        print(
            "  Possible causes: missing source_latex in repair rows, schema"
            " extraction failure, or strict claim-shape mismatch."
        )
    if result.get("phases", {}).get("auto_review", {}).get("status") == "skipped_no_api_key":
        print("  Next: set MISTRAL_API_KEY (LeanStral endpoint) and re-run to add alignment reviews")
    review_phase = result.get("phases", {}).get("auto_review", {})
    if review_phase.get("not_equivalent", 0) > 5:
        print(
            f"  {review_phase['not_equivalent']} rows judged not-equivalent by auto-review."
            " These need fresh statement regeneration or human adjudication."
        )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Proof-candidate factory: repair → review → gold proof queue",
    )
    p.add_argument("--project-root", type=Path, default=Path("."), help="Root of BDDM project")
    p.add_argument("--repair-queue-jsonl", type=Path, default=None, help="Override repair queue path")
    p.add_argument("--limit", type=int, default=500, help="Max repair queue rows to process")
    p.add_argument("--max-write-groups", type=int, default=10, help="Max repair action groups to write per run")
    p.add_argument("--repair-only", action="store_true", help="Stop after Phase 1 (skip LLM review)")
    p.add_argument("--review-only", action="store_true", help="Skip Phase 1 (repair), run review + bridge only")
    p.add_argument("--review-model", default=_DEFAULT_REVIEW_MODEL, help="Mistral model for auto alignment review")
    p.add_argument("--review-confidence", type=float, default=DEFAULT_CONFIDENCE_THRESHOLD)
    p.add_argument("--review-component", type=float, default=DEFAULT_COMPONENT_THRESHOLD)
    p.add_argument("--review-rate-delay", type=float, default=0.5, help="Seconds between Mistral API calls")
    p.add_argument("--dry-run", action="store_true", help="Plan but do not write any files")
    p.add_argument("--out-summary", type=Path, default=Path("output/corpus/proof_candidate_factory_summary.json"))
    p.add_argument("--ledger-path", action="append", type=Path, default=[])
    p.add_argument("--report-root", action="append", type=Path, default=[])
    p.add_argument("--evidence-root", action="append", type=Path, default=[])
    return p


def main() -> int:
    args = _build_parser().parse_args()
    project_root = args.project_root.resolve()

    result = run_factory(
        project_root=project_root,
        repair_queue_jsonl=args.repair_queue_jsonl,
        limit=args.limit,
        max_write_groups=args.max_write_groups,
        repair_only=bool(args.repair_only),
        review_only=bool(args.review_only),
        review_model=args.review_model,
        review_confidence_threshold=args.review_confidence,
        review_component_threshold=args.review_component,
        review_rate_delay=args.review_rate_delay,
        dry_run=bool(args.dry_run),
        ledger_paths=args.ledger_path or [],
        report_roots=args.report_root or [],
        evidence_roots=args.evidence_root or [],
    )

    out_summary = args.out_summary if args.out_summary.is_absolute() else project_root / args.out_summary
    if args.dry_run:
        print(f"[dry_run] summary not written; requested path was {out_summary}")
    else:
        _write_json(out_summary, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
