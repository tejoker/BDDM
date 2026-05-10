#!/usr/bin/env python3
# ruff: noqa: W605  (regex docstrings contain literal backticks)
r"""Categorize elaboration_failure rows in the corpus by Lean-error root cause.

The headline number from this analysis is the answer to the question:
"of the rows that fail UNRESOLVED, what fraction are blocked by translation
quality (paper-theory) vs. by proof-search depth?" Across the 8-paper
committed corpus, ~57% of UNRESOLVED rows fail at Lean elaboration of the
*statement* (not the proof). This breakdown identifies the dominant
root-cause buckets so future translator / paper-theory-builder work can
target them in priority order.

Buckets (Lean-error pattern → root cause):
  1. typeclass_instance_missing  — `synthInstanceFailed`. Translator picked
     a Mathlib type the paper-local stub doesn't have an instance for
     (`HasSubset Multisegment`, `MeasureSpace ℕ`).
  2. unknown_identifier          — `unknownIdentifier`/`unknown constant`.
     Translator referenced a name that wasn't declared in paper-theory.
  3. invalid_field_projection    — `invalidField`/`Invalid \`⟨…⟩\``. Trying
     to project a member or use anonymous-constructor on the wrong type.
  4. parse_error                 — `unexpected token`/`expected …`. The
     signature itself is malformed Lean syntax.
  5. application_type_mismatch   — `Function expected at`/`Application type
     mismatch`/`Type mismatch`. Wrong arity or wrong-typed argument.
  6. metavariable_unresolved     — `don't know how to synthesize placeholder`/
     `metavariables`. Type inference couldn't fill an `_`.
  7. other                       — everything else.

Output: a table per bucket with counts + per-paper breakdown + 3 sample
errors. JSON output via `--json`.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Pattern → bucket. Order matters: more-specific patterns must come first.
_BUCKETS: list[tuple[str, str]] = [
    ("typeclass_instance_missing",
     r"synthInstanceFailed|failed to synthesize instance"),
    ("invalid_field_projection",
     r"invalidField|Invalid `⟨"),
    ("unknown_identifier",
     r"unknownIdentifier|unknown constant|unknown namespace|identifier `[^`]+` is unknown"),
    ("metavariable_unresolved",
     r"don't know how to synthesize placeholder|expected type metavariable|metavariables"),
    ("application_type_mismatch",
     r"Function expected at|Application type mismatch|Type mismatch"),
    ("parse_error",
     r"unexpected token|unexpected identifier|expected token|expected '"),
    # The translator records `translation_acceptance_gate:lean_elaboration_failed`
    # without preserving the underlying Lean error. These rows lost their
    # error detail at gate time — separate orchestration bucket so they
    # don't pollute "other".
    ("elaboration_no_detail",
     r"^translation_acceptance_gate:lean_elaboration_failed\s*$"),
]


def categorize_error(error: str) -> str:
    """Return the bucket name for a Lean error message."""
    for bucket, pat in _BUCKETS:
        if re.search(pat, error, flags=re.IGNORECASE):
            return bucket
    return "other"


def collect_elaboration_failures(
    *,
    project_root: Path = _PROJECT_ROOT,
    paper_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Walk verification ledgers and return one record per elaboration_failure.

    A row is an elaboration_failure if its `failure_kind` is exactly that, OR
    if its `error_message` matches the validation_gate_elaboration_failed
    prefix (catches rows where failure_kind wasn't set but the error pattern
    is present)."""
    if paper_ids is None:
        paper_ids = sorted(
            p.stem for p in (project_root / "output" / "verification_ledgers").glob("*.json")
            if not any(s in p.stem for s in ("_smoke", "_actionable", "_repair", "_reliable", "ab_repair", "_fdcheck", "_patchcheck", "_rflguard", "_fast"))
        )
    out: list[dict[str, Any]] = []
    for pid in paper_ids:
        p = project_root / "output" / "verification_ledgers" / f"{pid}.json"
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        entries = data if isinstance(data, list) else data.get("entries", [])
        for e in entries:
            err = str(e.get("error_message", "") or "")
            failure_kind = str(e.get("failure_kind", "") or "")
            is_elab = (
                failure_kind == "elaboration_failure"
                or "elaboration_failed" in err.lower()
                or "validation_gate_elaboration_failed" in err
            )
            if not is_elab:
                continue
            out.append({
                "paper_id": pid,
                "theorem_name": str(e.get("theorem_name", "") or ""),
                "error_message": err,
                "status": str(e.get("status", "") or ""),
                "bucket": categorize_error(err),
            })
    return out


def build_summary(failures: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate failures into a bucket × paper table + sample errors."""
    bucket_counts = Counter(f["bucket"] for f in failures)
    bucket_papers: dict[str, Counter[str]] = defaultdict(Counter)
    bucket_samples: dict[str, list[str]] = defaultdict(list)
    for f in failures:
        bucket_papers[f["bucket"]][f["paper_id"]] += 1
        if len(bucket_samples[f["bucket"]]) < 3:
            err = f["error_message"]
            # Extract a single representative line from the error.
            for line in err.split("\n"):
                if "error" in line.lower() and "validation_gate" not in line:
                    bucket_samples[f["bucket"]].append(
                        f"{f['paper_id']}/{f['theorem_name']}: {line.strip()[:150]}"
                    )
                    break
    return {
        "schema_version": "elaboration_failure_taxonomy.v1",
        "total_elaboration_failures": len(failures),
        "bucket_counts": dict(bucket_counts.most_common()),
        "bucket_per_paper": {
            b: dict(papers.most_common())
            for b, papers in bucket_papers.items()
        },
        "bucket_samples": {b: samples for b, samples in bucket_samples.items()},
    }


def print_table(summary: dict[str, Any]) -> None:
    total = summary["total_elaboration_failures"]
    print(f"Total elaboration failures: {total}")
    print()
    print(f"{'Bucket':<32}  {'Count':>6}  {'%':>6}")
    print("-" * 50)
    for bucket, count in summary["bucket_counts"].items():
        pct = 100.0 * count / max(1, total)
        print(f"{bucket:<32}  {count:>6}  {pct:>5.1f}%")
    print()
    print("Per-paper distribution:")
    for bucket, papers in summary["bucket_per_paper"].items():
        items = ", ".join(f"{p}:{c}" for p, c in papers.items())
        print(f"  {bucket}: {items}")
    print()
    print("Sample errors (1 per bucket):")
    for bucket, samples in summary["bucket_samples"].items():
        if samples:
            print(f"  [{bucket}] {samples[0]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=_PROJECT_ROOT)
    parser.add_argument("--paper-id", action="append", help="Filter to specific paper(s)")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of a table")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    failures = collect_elaboration_failures(
        project_root=args.project_root,
        paper_ids=args.paper_id,
    )
    summary = build_summary(failures)

    if args.json:
        text = json.dumps(summary, indent=2, ensure_ascii=False)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
    else:
        print_table(summary)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
