#!/usr/bin/env python3
"""Export the BDDM corpus as a Hugging-Face-Datasets-shaped artifact.

Produces a versioned dataset directory at `output/corpus/dataset_v<n>/` with:

  * `dataset_info.json` — schema description, version, license-pending notice.
  * `train.jsonl` — one row per theorem with the full provenance + status:
      - paper_id, theorem_id, canonical_theorem_id, row_id
      - source_latex (the original LaTeX paragraph the row came from)
      - lean_statement (translated)
      - status (FULLY_PROVEN | AXIOM_BACKED | INTERMEDIARY_PROVEN | UNRESOLVED | FLAWED | TRANSLATION_LIMITED)
      - axiom_debt (paper-local axioms used)
      - claim_equivalence_verdict (Mistral CoT judge: equivalent | unclear | not_equivalent)
      - reviewed_alignment_confidence
      - cot_reasoning_steps (when CoT review is available)
      - failure_kind / error_message (for FLAWED rows)
      - statement_alignment_class (exact | partial | weaker | unrelated | diagnostic)
  * `manifest.json` — paper-id-keyed summary: row counts by status, ledger paths.

NOT a publication step. The output is local; the user reviews and decides
whether to upload to HF Datasets / Zenodo. License field is left as
`license-pending` until source rights are confirmed.

Usage:
    python3 scripts/export_corpus_dataset.py
        [--out output/corpus/dataset_v1]
        [--version 1]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_OUT = Path("output/corpus/dataset_v1")
DEFAULT_VERSION = "1"
LEDGER_DIR = Path("output/verification_ledgers")
REPRO_DIR = Path("reproducibility/full_paper_reports")
AUTO_REVIEWS = Path("output/corpus/auto_alignment_reviews.jsonl")
COT_TRIAGE = Path("output/corpus/auto_alignment_triage_report.json")
GOLD_QUEUE = Path("output/corpus/gold_proof_growth_queue.jsonl")
_CANONICAL_LEDGER_RE = re.compile(r"^\d{4}\.\d{4,6}(?:v\d+)?$")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _load_entries(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("entries", [])


def _index_reviews_by_row_id(reviews: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(r.get("row_id", "") or ""): r for r in reviews if r.get("row_id")}


def _index_triage_by_row_id(triage_path: Path) -> dict[str, dict[str, Any]]:
    if not triage_path.exists():
        return {}
    try:
        data = json.loads(triage_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {str(r.get("row_id", "") or ""): r for r in (data.get("rows") or []) if r.get("row_id")}


def _row_source_latex(r: dict[str, Any]) -> str:
    """The source LaTeX for a row — ledgers stash it in different places."""
    if str(r.get("source_latex", "") or "").strip():
        return str(r.get("source_latex", "") or "")
    if str(r.get("original_latex_theorem", "") or "").strip():
        return str(r.get("original_latex_theorem", "") or "")
    sea = r.get("semantic_equivalence_artifact")
    if isinstance(sea, dict):
        for k in ("original_latex_theorem", "source_latex", "source_statement", "statement"):
            v = sea.get(k, "")
            if v and str(v).strip():
                return str(v)
    return ""


def _row_to_dataset_entry(
    paper_id: str,
    row: dict[str, Any],
    review: dict[str, Any] | None,
    triage: dict[str, Any] | None,
    in_gold_queue: bool,
) -> dict[str, Any]:
    """Project a ledger row to the dataset schema."""
    semantic_artifact = row.get("semantic_equivalence_artifact") if isinstance(row.get("semantic_equivalence_artifact"), dict) else {}
    out = {
        "paper_id": paper_id,
        "theorem_id": str(row.get("theorem_id", "") or row.get("theorem_name", "") or ""),
        "theorem_name": str(row.get("theorem_name", "") or ""),
        "canonical_theorem_id": str(row.get("canonical_theorem_id", "") or ""),
        "row_id": str(row.get("row_id", "") or ""),
        # Source LaTeX
        "source_latex": _row_source_latex(row),
        "source_label": str(row.get("paper_statement_id", "") or ""),
        # Translated Lean
        "lean_statement": str(row.get("lean_statement", "") or ""),
        # Verification status
        "status": str(row.get("status", "") or ""),
        "proof_text": str(row.get("proof_text", "") or ""),
        "step_verdict": str(row.get("step_verdict", "") or ""),
        "axiom_debt": [str(d) for d in (row.get("axiom_debt") or []) if str(d).strip()],
        "gate_failures": [str(g) for g in (row.get("gate_failures") or []) if str(g).strip()],
        # Equivalence assessment
        "claim_equivalence_verdict": str(row.get("claim_equivalence_verdict", "") or ""),
        "reviewed_equivalence_verdict": str(row.get("reviewed_equivalence_verdict", "") or ""),
        "reviewed_statement_alignment_class": str(row.get("reviewed_statement_alignment_class", "") or ""),
        "reviewed_alignment_confidence": float(row.get("reviewed_alignment_confidence", 0.0) or 0.0),
        "review_provenance": row.get("review_provenance") if isinstance(row.get("review_provenance"), dict) else {},
        # CoT reasoning (when available from auto-alignment review)
        "cot_reasoning_steps": (
            list(review.get("cot_reasoning_steps") or [])
            if isinstance(review, dict) else []
        ),
        "cot_raw_verdict": (
            str(review.get("cot_raw_verdict", "") or "")
            if isinstance(review, dict) else ""
        ),
        # Failure analysis
        "failure_kind": str(row.get("failure_kind", "") or ""),
        "error_message": str(row.get("error_message", "") or ""),
        "statement_alignment_class": str(row.get("statement_alignment_class", "") or ""),
        "alignment_confidence": float(row.get("alignment_confidence", 0.0) or 0.0),
        # Pipeline membership
        "in_gold_queue": bool(in_gold_queue),
    }
    if isinstance(triage, dict):
        out["triage_decision"] = str(triage.get("decision", "") or "")
        out["triage_reasons"] = [str(r) for r in (triage.get("triage_reasons") or [])]
    return out


def export_dataset(out_dir: Path, version: str) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    auto_reviews = _index_reviews_by_row_id(_read_jsonl(AUTO_REVIEWS))
    triage = _index_triage_by_row_id(COT_TRIAGE)
    gold_rows = _read_jsonl(GOLD_QUEUE)
    gold_keys = {(str(r.get("arxiv_id", "")), str(r.get("theorem_id", ""))) for r in gold_rows}

    train_path = out_dir / "train.jsonl"
    manifest: dict[str, Any] = {}
    total = 0
    status_counts: Counter[str] = Counter()
    paper_ids: list[str] = []

    with train_path.open("w", encoding="utf-8") as f_train:
        for ledger_path in sorted(LEDGER_DIR.glob("*.json")):
            if not _CANONICAL_LEDGER_RE.match(ledger_path.stem):
                continue
            paper_id = ledger_path.stem
            paper_ids.append(paper_id)
            entries = _load_entries(ledger_path)
            paper_status = Counter()
            for row in entries:
                row_id = str(row.get("row_id", "") or "")
                review = auto_reviews.get(row_id)
                triage_row = triage.get(row_id)
                tid = str(row.get("theorem_id", "") or row.get("theorem_name", "") or "")
                in_gold = (paper_id, tid) in gold_keys
                entry = _row_to_dataset_entry(paper_id, row, review, triage_row, in_gold)
                f_train.write(json.dumps(entry, ensure_ascii=False) + "\n")
                total += 1
                paper_status[entry["status"]] += 1
                status_counts[entry["status"]] += 1
            manifest[paper_id] = {
                "rows": len(entries),
                "status": dict(paper_status),
                "ledger_path": str(ledger_path),
                "ledger_sha256": hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
            }

    # dataset_info.json — HF Datasets-shaped metadata
    info = {
        "name": "BDDM-paper-to-lean-corpus",
        "version": version,
        "schema_version": "bddm_dataset.v1",
        "description": (
            "Translation + verification corpus for arxiv research-math papers → Lean 4. "
            "Each row carries source LaTeX, translated Lean signature, verification status, "
            "axiom debt, equivalence judgement (Mistral CoT), and failure analysis. "
            "Generated by the BDDM pipeline."
        ),
        "license": "license-pending (source paper rights to be confirmed before publication)",
        "fields": [
            "paper_id", "theorem_id", "theorem_name", "canonical_theorem_id", "row_id",
            "source_latex", "source_label", "lean_statement",
            "status", "proof_text", "step_verdict", "axiom_debt", "gate_failures",
            "claim_equivalence_verdict", "reviewed_equivalence_verdict",
            "reviewed_statement_alignment_class", "reviewed_alignment_confidence",
            "review_provenance", "cot_reasoning_steps",
            "failure_kind", "error_message",
            "statement_alignment_class", "alignment_confidence",
            "in_gold_queue", "triage_decision", "triage_reasons",
        ],
        "paper_count": len(paper_ids),
        "row_count": total,
        "status_distribution": dict(status_counts),
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "honest_scope": (
            "All `claim_equivalence_verdict` and `reviewed_equivalence_verdict` values "
            "of 'equivalent' from `auto_llm:*` reviewers are LLM judgements — they "
            "carry alignment evidence but are not human-blessed for release. Use only "
            "`reviewed_by` matching `human:*` or `hybrid:*` for release-grade "
            "assertions. Status hierarchy: FULLY_PROVEN > AXIOM_BACKED > "
            "INTERMEDIARY_PROVEN. AXIOM_BACKED rows are verified Lean modulo "
            "explicitly-named paper-local axioms (see `axiom_debt` field)."
        ),
    }
    (out_dir / "dataset_info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Sanity readme so readers know it's a local-only artifact.
    (out_dir / "README.md").write_text(
        f"""# BDDM paper-to-Lean corpus, version {version}

**This is a LOCAL artifact**, not a public dataset. The pipeline produced it
by running translation + verification on a curated set of arxiv papers; it
is shaped for Hugging Face Datasets but has not been pushed.

## Files
- `train.jsonl` — one row per theorem with full provenance + status.
- `dataset_info.json` — HF-shaped metadata (schema, version, license).
- `manifest.json` — per-paper summary + ledger sha256 for reproducibility.

## Schema
See `dataset_info.json:fields` for the full row schema.

## Honest scope
The corpus is small ({total} rows / {len(paper_ids)} papers). Status totals:
{json.dumps(dict(status_counts), indent=2)}

LLM-only reviews (`auto_llm:*` reviewer) are alignment evidence only — not
human-blessed for release. See `dataset_info.json:honest_scope` for full
interpretation rules.
""",
        encoding="utf-8",
    )

    return {
        "out_dir": str(out_dir),
        "row_count": total,
        "paper_count": len(paper_ids),
        "status_distribution": dict(status_counts),
        "info_path": str(out_dir / "dataset_info.json"),
        "manifest_path": str(out_dir / "manifest.json"),
        "train_path": str(train_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export BDDM corpus as HF-Datasets-shaped artifact (LOCAL only)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    args = parser.parse_args()
    summary = export_dataset(args.out, args.version)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
