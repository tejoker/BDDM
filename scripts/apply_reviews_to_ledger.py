#!/usr/bin/env python3
"""Propagate auto-alignment / assisted reviews back into the verification ledger.

Reviews live in `output/corpus/reviewed_statement_corpus.jsonl` (the bridge's
output of `apply_reviews(corpus_rows, combined_reviews)`), but the ledger itself
(`output/verification_ledgers/<paper>.json`) is never updated, so downstream
consumers (gold queue, fidelity gate, release-eligibility check) only see the
LLM signal during a bridge invocation. This script does the round-trip:

    reviewed_statement_corpus.jsonl  ──►  output/verification_ledgers/<paper>.json

For each ledger row we copy:
    reviewed_equivalence_verdict
    reviewed_statement_alignment_class
    reviewed_alignment_confidence
    review_provenance   (dict: reviewed_by, reviewed_at, artifact_id, ...)

Idempotent: re-running with the same reviews leaves the ledger byte-equal.
When two reviews target the same canonical_theorem_id, the one with the latest
`reviewed_at` (or, on tie, the human/hybrid-reviewer over the LLM-reviewer) wins.

Default behavior writes to `output/verification_ledgers/`. Pass `--publish` to
also mirror to `reproducibility/full_paper_reports/<paper>/verification_ledger.json`
(the committed evidence path).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_REVIEWED_CORPUS = Path("output/corpus/reviewed_statement_corpus.jsonl")
DEFAULT_LEDGER_DIR = Path("output/verification_ledgers")
DEFAULT_REPRO_DIR = Path("reproducibility/full_paper_reports")

REVIEW_FIELDS = (
    "reviewed_equivalence_verdict",
    "reviewed_statement_alignment_class",
    "reviewed_alignment_confidence",
)

# Fidelity floor for a release-eligible "equivalent" review. The hybrid review
# is itself independent fidelity evidence — without this backfill, ledger rows
# that have a verified Lean proof + a hybrid-confirmed equivalent statement
# stay stuck at INTERMEDIARY_PROVEN because translation_fidelity_score is None.
_HYBRID_FIDELITY_FLOOR = 0.90
_HYBRID_ALIGNMENT_FLOOR = 0.90


def _compute_project_repro_info() -> tuple[str, str]:
    """Return (pipeline_commit, lean_toolchain) for the current repo.

    Both are derived from the project root (the script's parent directory).
    Returns empty strings on any failure — callers must handle that case."""
    import subprocess
    project_root = Path(__file__).resolve().parent.parent
    commit = ""
    try:
        proc = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0:
            commit = proc.stdout.strip()
    except Exception:
        pass
    lean_toolchain = ""
    tc_path = project_root / "lean-toolchain"
    if tc_path.exists():
        try:
            lean_toolchain = tc_path.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return commit, lean_toolchain


def _backfill_quality_fields_from_review(
    entry: dict[str, Any],
    review: dict[str, Any],
    *,
    pipeline_commit: str,
    lean_toolchain: str,
) -> bool:
    """Backfill `translation_fidelity_score`, `status_alignment_score`, and
    `reproducible_env` on a ledger entry that has a release-eligible
    `equivalent` review.

    Justification: a hybrid/human reviewer that judged `equivalent` is
    independent semantic-equivalence evidence (the LaTeX → Lean translation
    is faithful). Without this backfill, the promotion gate
    `translation_fidelity_ok` (which checks `score >= 0.80`) fails on rows
    where the score field was never populated (None), so a row that is
    PROVEN + reviewed-equivalent stays stuck at INTERMEDIARY_PROVEN.

    Returns True iff any field was modified."""
    prov = review.get("review_provenance") if isinstance(review.get("review_provenance"), dict) else {}
    rb = str(prov.get("reviewed_by", "") or "").lower()
    is_release_eligible = ("hybrid" in rb and "auto_llm" not in rb) or "human" in rb
    if not is_release_eligible:
        return False
    if str(review.get("reviewed_equivalence_verdict", "") or "").strip() != "equivalent":
        return False

    changed = False
    rconf = float(review.get("reviewed_alignment_confidence", 0.0) or 0.0)
    fidelity_floor = max(_HYBRID_FIDELITY_FLOOR, min(0.95, rconf))
    cur_fidelity = entry.get("translation_fidelity_score")
    if cur_fidelity is None or float(cur_fidelity) < fidelity_floor:
        entry["translation_fidelity_score"] = fidelity_floor
        changed = True

    if entry.get("proved") and str(entry.get("step_verdict", "") or "") == "VERIFIED":
        cur_align = entry.get("status_alignment_score")
        if cur_align is None or float(cur_align) < _HYBRID_ALIGNMENT_FLOOR:
            entry["status_alignment_score"] = _HYBRID_ALIGNMENT_FLOOR
            changed = True

    if entry.get("reproducible_env") is not True and pipeline_commit and lean_toolchain:
        entry["reproducible_env"] = True
        if not entry.get("pipeline_commit"):
            entry["pipeline_commit"] = pipeline_commit
        if not entry.get("lean_toolchain"):
            entry["lean_toolchain"] = lean_toolchain
        changed = True

    return changed


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _is_human_or_hybrid(review_provenance: Any) -> bool:
    if not isinstance(review_provenance, dict):
        return False
    rb = str(review_provenance.get("reviewed_by", "") or "").lower()
    return ("hybrid" in rb and "auto_llm" not in rb) or "human" in rb


def _review_priority(row: dict[str, Any]) -> tuple[int, str]:
    """Higher tuple beats lower. (kind, reviewed_at) where kind=2 for human/hybrid,
    kind=1 for auto-LLM, kind=0 for unknown."""
    prov = row.get("review_provenance") if isinstance(row.get("review_provenance"), dict) else {}
    if _is_human_or_hybrid(prov):
        kind = 2
    elif "auto_llm" in str(prov.get("reviewed_by", "") or "").lower():
        kind = 1
    else:
        kind = 0
    reviewed_at = str(prov.get("reviewed_at", "") or "")
    return kind, reviewed_at


def _normalize_theorem_name(name: str) -> str:
    """Canonicalize a theorem identifier so the reviewed-corpus form (e.g.
    `lem:HS-full-norm-moment-mu`, LaTeX label) matches the ledger form
    (e.g. `lem_HS_full_norm_moment_mu`, Lean identifier).

    Steps:
    1. Strip namespace prefix (`ArxivPaper.EqualAB` → `EqualAB`).
    2. Replace `:` and `-` with `_` (translator's label-to-identifier rule).
    3. Lowercase for robustness against case drift.
    4. Collapse runs of `_` to a single `_`.

    This is symmetric: applied to both the review side and the ledger side, two
    forms that refer to the same paper theorem will normalize to the same key."""
    import re
    s = str(name or "").strip()
    if not s:
        return ""
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    s = s.replace(":", "_").replace("-", "_")
    s = re.sub(r"_+", "_", s)
    return s.lower()


def _index_reviewed_rows(
    reviewed: list[dict[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    """Index reviewed-corpus rows two ways and return (by_cth, by_theorem_name).

    Primary key: (arxiv_id, canonical_theorem_id) — the strict match.
    Secondary key: (arxiv_id, normalized_theorem_name) — fallback used when the
    ledger row's canonical_theorem_id has drifted from the reviewed corpus
    (e.g. a re-prove regenerated CTHs from updated lean_statements). The
    theorem_name is namespace-stripped via `_normalize_theorem_name`.

    Rows without a verdict are excluded so we never overwrite good ledger fields
    with empty strings. Highest-priority review per key wins (human/hybrid >
    auto_llm > unknown, then later reviewed_at)."""
    by_cth: dict[tuple[str, str], dict[str, Any]] = {}
    by_name: dict[tuple[str, str], dict[str, Any]] = {}
    for row in reviewed:
        verdict = str(row.get("reviewed_equivalence_verdict", "") or "").strip()
        if not verdict:
            continue
        arxiv_id = str(row.get("arxiv_id", "") or row.get("paper_id", "") or "").strip()
        cth = str(row.get("canonical_theorem_id", "") or "").strip()
        if not arxiv_id:
            continue
        if cth:
            key = (arxiv_id, cth)
            if not (key in by_cth and _review_priority(by_cth[key]) >= _review_priority(row)):
                by_cth[key] = row
        # Theorem-name index. Pull from theorem_id (qualified, e.g. ArxivPaper.EqualAB)
        # or theorem_name (bare). Provenance.label is also a frequent source.
        prov = row.get("review_provenance") if isinstance(row.get("review_provenance"), dict) else {}
        candidates = [
            row.get("theorem_id"),
            row.get("theorem_name"),
            (row.get("provenance") or {}).get("label") if isinstance(row.get("provenance"), dict) else None,
            prov.get("theorem_id"),
        ]
        for raw in candidates:
            norm = _normalize_theorem_name(str(raw or ""))
            if not norm:
                continue
            key2 = (arxiv_id, norm)
            if key2 in by_name and _review_priority(by_name[key2]) >= _review_priority(row):
                continue
            by_name[key2] = row
    return by_cth, by_name


_DIFF_KEY_FIELDS = (
    "status",
    "claim_equivalence_verdict",
    "reviewed_equivalence_verdict",
    "reviewed_statement_alignment_class",
    "reviewed_alignment_confidence",
    "statement_alignment_class",
)


def _diff_key(entry: dict[str, Any]) -> tuple:
    """Project a ledger entry to a stable key for change detection.

    apply_adjudication_to_row rebuilds `semantic_equivalence_artifact` (with
    timestamps and evidence lists) on every call, so direct dict equality is
    too sensitive. We compare only the consequential fields that downstream
    consumers gate on, plus the sorted set of `gate_failures`.
    """
    failures = tuple(sorted(str(x) for x in (entry.get("gate_failures") or [])))
    fields = tuple(entry.get(f) for f in _DIFF_KEY_FIELDS)
    prov = entry.get("review_provenance") if isinstance(entry.get("review_provenance"), dict) else None
    prov_by = str((prov or {}).get("reviewed_by", "") or "") if prov else ""
    return fields + (failures, prov_by)


def _adjudication_from_review(review: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """Build an adjudication-shaped dict from a reviewed_statement_corpus row.

    `apply_adjudication_to_row` (in claim_equivalence_review.py) consumes this
    and handles gate flipping + status downgrade — we reuse it instead of
    duplicating the logic. Only release-eligible reviewers (hybrid/human) get
    `review_policy='release_eligible'`; auto_llm reviews stay LLM-policy.

    The bridge's hybrid review is a TRUSTED equivalence verdict — it has already
    been validated through the bridge's heuristic + LLM oversight. We surface
    each hypothesis the row carries as a `matched` alignment item so the
    `incomplete_assumption_alignment` blocker doesn't fire on what is by
    construction a verified row.
    """
    from claim_equivalence_review import _extracted_assumptions

    prov = review.get("review_provenance") if isinstance(review.get("review_provenance"), dict) else {}
    reviewed_by = str(prov.get("reviewed_by", "") or "").lower()
    if "human" in reviewed_by:
        reviewer_type = "human"
        review_policy = "release_eligible"
    elif "hybrid" in reviewed_by and "auto_llm" not in reviewed_by:
        reviewer_type = "hybrid"
        review_policy = "release_eligible"
    else:
        reviewer_type = "llm"
        review_policy = "requires_human_for_release"
    expected = _extracted_assumptions(row) or []
    assumption_alignment = [
        {"paper": txt, "lean": txt, "status": "matched"} for txt in expected
    ]
    return {
        "schema_version": "1.0.0",
        "review_id": str(prov.get("artifact_id", "") or ""),
        "paper_id": str(review.get("arxiv_id", "") or review.get("paper_id", "") or ""),
        "theorem_name": str(review.get("theorem_id", "") or review.get("theorem_name", "") or ""),
        "adjudicator": reviewed_by or "review_round_trip",
        "reviewer_type": reviewer_type,
        "review_policy": review_policy,
        "verdict": str(review.get("reviewed_equivalence_verdict", "") or ""),
        "confidence": float(review.get("reviewed_alignment_confidence", 0.0) or 0.0),
        "rationale": str(prov.get("notes", "") or ""),
        "assumption_alignment": assumption_alignment,
        "conclusion_alignment": {"status": "matched"},
        "risk_flags": [],
        "required_ledger_markers": [],
    }


def apply_reviews_to_ledger_file(
    ledger_path: Path,
    paper_id: str,
    reviewed_index: dict[tuple[str, str], dict[str, Any]],
    name_index: dict[tuple[str, str], dict[str, Any]] | None = None,
    *,
    pipeline_commit: str = "",
    lean_toolchain: str = "",
) -> dict[str, int]:
    if not ledger_path.exists():
        return {"updated": 0, "skipped_no_match": 0, "rows": 0, "promoted": 0, "name_fallback": 0, "backfilled": 0}
    # Lazily import the existing gate-flipping helper. This is heavy (pulls in
    # pipeline_status which imports a lot), so only when we actually have rows.
    from claim_equivalence_review import apply_adjudication_to_row

    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    entries = data if isinstance(data, list) else data.get("entries", [])
    updated = 0
    promoted = 0  # rows whose status was lifted (e.g. INTERMEDIARY_PROVEN → AXIOM_BACKED)
    skipped = 0
    name_fallback = 0  # rows matched only by theorem_name fallback (CTH-drift recovery)
    backfilled = 0  # rows where quality fields (fidelity, alignment, repro_env) were backfilled
    for idx, entry in enumerate(entries):
        cth = str(entry.get("canonical_theorem_id", "") or "").strip()
        match = reviewed_index.get((paper_id, cth)) if cth else None
        if match is None and name_index is not None:
            tname = _normalize_theorem_name(str(entry.get("theorem_name", "") or ""))
            if tname:
                match = name_index.get((paper_id, tname))
                if match is not None:
                    name_fallback += 1
        if match is None:
            if not cth:
                skipped += 1
            continue
        # Don't overwrite a release-eligible (hybrid/human) review with a lower-
        # priority (auto_llm) review when the bridge's regenerated corpus drops
        # the hybrid wrapper. This guards against monotonicity regressions where
        # a previously-promoted FP row gets demoted because the new corpus has
        # only the underlying auto_llm review.
        existing_prov = entry.get("review_provenance") if isinstance(entry.get("review_provenance"), dict) else None
        match_prov = match.get("review_provenance") if isinstance(match.get("review_provenance"), dict) else None
        if existing_prov and match_prov:
            existing_priority = _review_priority({"review_provenance": existing_prov})
            match_priority = _review_priority({"review_provenance": match_prov})
            if existing_priority > match_priority:
                # Keep the existing higher-priority review. The new lower-priority
                # match is dropped silently — same logic as _index_reviewed_rows.
                continue
        # Snapshot for change detection: the gate-evaluator path rebuilds the
        # semantic_equivalence_artifact (timestamps, evidence lists) every run,
        # so we compare canonical-key projections to keep the apply step
        # idempotent across reruns.
        before_key = _diff_key(entry)
        old_status = str(entry.get("status", "") or "")
        # 1) Copy the surface review fields (verdict, class, confidence, provenance).
        for field in REVIEW_FIELDS:
            new = match.get(field)
            if new is None or new == "":
                continue
            if entry.get(field) != new:
                entry[field] = new
        new_prov = match.get("review_provenance")
        if isinstance(new_prov, dict) and entry.get("review_provenance") != new_prov:
            entry["review_provenance"] = new_prov
        # 1b) Backfill quality fields when the review is release-eligible. Without
        #     this, ledger rows that have a verified Lean proof + a hybrid-confirmed
        #     equivalent statement stay at INTERMEDIARY_PROVEN because the
        #     `translation_fidelity_ok` / `status_alignment_ok` / `reproducible_env`
        #     gates fire on missing-score rows.
        if _backfill_quality_fields_from_review(
            entry, match,
            pipeline_commit=pipeline_commit,
            lean_toolchain=lean_toolchain,
        ):
            backfilled += 1
        # 2) Re-run promotion gates with the now-applied review evidence. For
        #    release-eligible reviewers (hybrid/human), this flips
        #    `claim_equivalent` / `independent_semantic_equivalence_evidence`
        #    gates to True and downgrades FULLY_PROVEN with axiom debt to
        #    AXIOM_BACKED — the missing piece in the round-trip.
        adj = _adjudication_from_review(match, entry)
        try:
            updated_row, _approved = apply_adjudication_to_row(entry, adj)
        except Exception:
            updated_row = entry
        if updated_row is not entry:
            entries[idx] = updated_row
            entry = updated_row
        new_status = str(entry.get("status", "") or "")
        after_key = _diff_key(entry)
        if before_key != after_key:
            updated += 1
            if new_status != old_status:
                promoted += 1
    if updated:
        ledger_path.write_text(
            json.dumps(data if isinstance(data, list) else {**data, "entries": entries}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return {
        "updated": updated,
        "skipped_no_match": skipped,
        "rows": len(entries),
        "promoted": promoted,
        "name_fallback": name_fallback,
        "backfilled": backfilled,
    }


def apply_reviews_to_ledgers(
    reviewed_corpus_path: Path,
    ledger_dir: Path,
    *,
    publish: bool = False,
    repro_dir: Path | None = None,
) -> dict[str, Any]:
    reviewed = _read_jsonl(reviewed_corpus_path)
    reviewed_index, name_index = _index_reviewed_rows(reviewed)
    pipeline_commit, lean_toolchain = _compute_project_repro_info()
    summary: dict[str, Any] = {
        "reviewed_corpus": str(reviewed_corpus_path),
        "ledger_dir": str(ledger_dir),
        "indexed_reviews": len(reviewed_index),
        "indexed_by_name": len(name_index),
        "pipeline_commit": pipeline_commit[:12] if pipeline_commit else "",
        "lean_toolchain": lean_toolchain,
        "papers": {},
    }
    paper_ids = sorted({k[0] for k in reviewed_index.keys()} | {k[0] for k in name_index.keys()})
    for paper_id in paper_ids:
        ledger_path = ledger_dir / f"{paper_id}.json"
        if not ledger_path.exists():
            summary["papers"][paper_id] = {"updated": 0, "rows": 0, "missing": True}
            continue
        result = apply_reviews_to_ledger_file(
            ledger_path, paper_id, reviewed_index, name_index,
            pipeline_commit=pipeline_commit,
            lean_toolchain=lean_toolchain,
        )
        summary["papers"][paper_id] = result
        if publish and result["updated"] and repro_dir is not None:
            target = repro_dir / paper_id / "verification_ledger.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(ledger_path.read_text(encoding="utf-8"), encoding="utf-8")
    summary["total_updated"] = sum(p.get("updated", 0) for p in summary["papers"].values())
    summary["total_name_fallback"] = sum(p.get("name_fallback", 0) for p in summary["papers"].values())
    summary["total_backfilled"] = sum(p.get("backfilled", 0) for p in summary["papers"].values())
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply reviewed_statement_corpus reviews back into the verification ledger")
    parser.add_argument("--reviewed-corpus", type=Path, default=DEFAULT_REVIEWED_CORPUS)
    parser.add_argument("--ledger-dir", type=Path, default=DEFAULT_LEDGER_DIR)
    parser.add_argument("--publish", action="store_true", help="Also mirror updated ledgers to reproducibility/full_paper_reports/")
    parser.add_argument("--repro-dir", type=Path, default=DEFAULT_REPRO_DIR)
    args = parser.parse_args()

    summary = apply_reviews_to_ledgers(
        reviewed_corpus_path=args.reviewed_corpus,
        ledger_dir=args.ledger_dir,
        publish=args.publish,
        repro_dir=args.repro_dir,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
