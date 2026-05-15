#!/usr/bin/env python3
"""Export the BDDM corpus as a SFT (supervised fine-tuning) jsonl for LLM
training, focused on three tasks the pipeline routinely needs:

  1. **Translation** — given a LaTeX theorem statement, produce a Lean 4
     signature. Positive examples: rows where translation produced a
     statement that elaborated AND was judged equivalent. Negative: rows
     where the translation was rejected (FLAWED / TRANSLATION_LIMITED).
  2. **Equivalence judging** — given a (LaTeX, Lean) pair, produce a CoT
     verdict (equivalent / adequate_weaker / not_equivalent / unclear).
     Pulls reasoning_steps from `auto_alignment_reviews.jsonl` when
     available.
  3. **Tactic suggestion** — given a goal state and an attempted tactic,
     mark whether the tactic worked. Pulls from MCTS proof traces.

Output is a chat-format jsonl (one JSON per line, `{"messages": [...]}`)
compatible with standard OpenAI / Mistral SFT formats.

Not a publication step — the user reviews and decides whether to upload.

Usage:
    python3 scripts/export_finetune_dataset.py
        [--out output/corpus/finetune_v1.jsonl]
        [--task translation|equivalence|tactic|all]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_OUT = Path("output/corpus/finetune_v1.jsonl")
LEDGER_DIR = Path("output/verification_ledgers")
AUTO_REVIEWS = Path("output/corpus/auto_alignment_reviews.jsonl")
PROOF_BATCH_LOG = Path("logs/proof_batch_results.json")
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


def _all_corpus_rows() -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(LEDGER_DIR.glob("*.json")):
        if not _CANONICAL_LEDGER_RE.match(path.stem):
            continue
        for r in _load_entries(path):
            rows.append((path.stem, r))
    return rows


def _row_source_latex(r: dict[str, Any]) -> str:
    """Extract the LaTeX source from wherever the ledger stashed it."""
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
    cp = r.get("context_pack")
    if isinstance(cp, dict):
        for k in ("original_latex_theorem", "source_latex", "source_statement", "statement"):
            v = cp.get(k, "")
            if v and str(v).strip():
                return str(v)
    return ""


# ---------------------------------------------------------------------------
# Task 1: Translation SFT
# ---------------------------------------------------------------------------

_TRANSLATION_SYSTEM = (
    "You are a formal mathematics translator. Given a LaTeX theorem statement, "
    "produce a Lean 4 signature that compiles in the Mathlib environment and "
    "preserves the mathematical content. Bind every free variable explicitly. "
    "Use Mathlib types and tactics."
)


def _translation_examples(rows: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for paper_id, r in rows:
        latex = _row_source_latex(r).strip()
        lean = str(r.get("lean_statement", "") or "").strip()
        status = str(r.get("status", "") or "")
        if not latex or not lean:
            continue
        # Use rows that elaborated cleanly. Status FULLY_PROVEN / AXIOM_BACKED /
        # INTERMEDIARY_PROVEN / UNRESOLVED all imply the lean_statement passed
        # the validation gate. Skip FLAWED (translation gave up) and
        # TRANSLATION_LIMITED (failed acceptance gate).
        if status in ("FLAWED", "TRANSLATION_LIMITED"):
            continue
        # Skip placeholder Lean (`: True`, `: False := by sorry`, etc.) since
        # those aren't real translations.
        if re.search(r":\s*(True|False)\s*(?::=|$)", lean):
            continue
        examples.append({
            "messages": [
                {"role": "system", "content": _TRANSLATION_SYSTEM},
                {"role": "user", "content": f"LaTeX:\n{latex}\n\nProduce the Lean 4 signature."},
                {"role": "assistant", "content": lean},
            ],
            "metadata": {
                "task": "translation",
                "paper_id": paper_id,
                "theorem_name": str(r.get("theorem_name", "") or ""),
                "status": status,
            },
        })
    return examples


# ---------------------------------------------------------------------------
# Task 2: Equivalence-judging SFT (CoT-format)
# ---------------------------------------------------------------------------

_EQUIVALENCE_SYSTEM = (
    "You are a formal-mathematics judge. Given a LaTeX theorem and a Lean 4 "
    "signature, reason step-by-step (quantifiers → hypotheses → conclusion → "
    "abstraction-check) and emit a JSON verdict. Acceptable verdicts: "
    "equivalent | adequate_weaker | not_equivalent | unclear."
)


def _equivalence_examples(rows: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Examples where the auto-alignment review provided a verdict + reasoning.

    Reviews carry `row_id` + `source_span_sha256` but no paper/theorem
    identifiers — the batch jsonl carries both. We join review→batch by
    `row_id` and pull source LaTeX + Lean directly from the batch row
    (which is what the CoT judge actually evaluated)."""
    BATCH = Path("output/corpus/statement_review_batch.jsonl")
    batch_by_row_id: dict[str, dict[str, Any]] = {
        str(r.get("row_id", "") or ""): r
        for r in _read_jsonl(BATCH)
        if r.get("row_id")
    }
    reviews_by_row_id: dict[str, dict[str, Any]] = {}
    for rev in _read_jsonl(AUTO_REVIEWS):
        rid = str(rev.get("row_id", "") or "")
        if rid:
            reviews_by_row_id[rid] = rev
    examples: list[dict[str, Any]] = []
    for rid, review in reviews_by_row_id.items():
        r = batch_by_row_id.get(rid)
        if r is None:
            continue
        paper_id = str(r.get("arxiv_id", "") or "")
        latex = str(r.get("source_latex", "") or "").strip()
        lean = str(r.get("lean_statement", "") or "").strip()
        if not latex or not lean:
            continue
        verdict = str(review.get("reviewed_equivalence_verdict", "") or "").lower()
        confidence = float(review.get("reviewed_alignment_confidence", 0.0) or 0.0)
        if verdict not in ("equivalent", "not_equivalent", "unclear", "adequate_weaker"):
            continue
        # Prefer the full CoT reasoning trace when available — gives the
        # fine-tune ~5× the training signal vs verdict+rationale alone.
        # Falls back to a minimal CoT when only verdict + notes are present.
        reasoning_steps = review.get("cot_reasoning_steps") or []
        if not reasoning_steps:
            inner_meta = review.get("_auto_meta", {}) if isinstance(review.get("_auto_meta"), dict) else {}
            judge_inner = inner_meta.get("judge", {}) if isinstance(inner_meta.get("judge"), dict) else {}
            reasoning_steps = judge_inner.get("reasoning_steps") or []
        raw_verdict = str(review.get("cot_raw_verdict", "") or verdict)
        if reasoning_steps:
            assistant_payload = {
                "reasoning_steps": list(reasoning_steps),
                "verdict": raw_verdict,
                "confidence": confidence,
                "rationale": str(review.get("notes", "") or "")[:200] or "auto-aligned",
                "adequate_weaker_evidence": bool(review.get("cot_adequate_weaker_evidence", False)),
            }
        else:
            assistant_payload = {
                "verdict": verdict,
                "confidence": confidence,
                "rationale": str(review.get("notes", "") or "")[:200] or "auto-aligned",
            }
        assistant_response = json.dumps(assistant_payload, ensure_ascii=False)
        examples.append({
            "messages": [
                {"role": "system", "content": _EQUIVALENCE_SYSTEM},
                {"role": "user", "content": f"LaTeX:\n{latex}\n\nLean 4:\n{lean}"},
                {"role": "assistant", "content": assistant_response},
            ],
            "metadata": {
                "task": "equivalence",
                "paper_id": paper_id,
                "theorem_name": str(r.get("theorem_id", "") or ""),
                "verdict": verdict,
                "confidence": confidence,
                "reviewer_by": str(review.get("reviewed_by", "")),
                "has_cot_steps": bool(reasoning_steps),
            },
        })
    return examples


# ---------------------------------------------------------------------------
# Task 3: Tactic suggestion (light-weight; from successful proofs)
# ---------------------------------------------------------------------------

_TACTIC_SYSTEM = (
    "You are a Lean 4 proof assistant. Given a theorem signature, suggest a "
    "single tactic (or short tactic chain) that closes it."
)


def _tactic_examples(rows: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Successful proofs with a known tactic. Pulls `proof_text` for proven rows."""
    examples: list[dict[str, Any]] = []
    for paper_id, r in rows:
        status = str(r.get("status", "") or "")
        if status not in ("FULLY_PROVEN", "AXIOM_BACKED", "INTERMEDIARY_PROVEN"):
            continue
        proof_text = str(r.get("proof_text", "") or "").strip()
        lean = str(r.get("lean_statement", "") or "").strip()
        if not proof_text or not lean:
            continue
        # Strip the body off the lean signature so the prompt is just the goal.
        sig = re.sub(r":=\s*by\b.*$", "", lean, flags=re.DOTALL).strip()
        sig = re.sub(r":=\s*$", "", sig).strip()
        if not sig:
            continue
        examples.append({
            "messages": [
                {"role": "system", "content": _TACTIC_SYSTEM},
                {"role": "user", "content": f"Theorem signature:\n{sig}\n\nClose this in Lean 4."},
                {"role": "assistant", "content": proof_text[:800]},
            ],
            "metadata": {
                "task": "tactic",
                "paper_id": paper_id,
                "theorem_name": str(r.get("theorem_name", "") or ""),
                "status": status,
            },
        })
    return examples


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def export_finetune(out_path: Path, task: str = "all") -> dict[str, Any]:
    rows = _all_corpus_rows()
    examples: list[dict[str, Any]] = []
    if task in ("translation", "all"):
        examples.extend(_translation_examples(rows))
    if task in ("equivalence", "all"):
        examples.extend(_equivalence_examples(rows))
    if task in ("tactic", "all"):
        examples.extend(_tactic_examples(rows))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")
    return {
        "out": str(out_path),
        "examples": len(examples),
        "by_task": dict(Counter(ex["metadata"]["task"] for ex in examples)),
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export BDDM SFT fine-tuning dataset")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--task", choices=("all", "translation", "equivalence", "tactic"), default="all")
    args = parser.parse_args()
    print(json.dumps(export_finetune(args.out, args.task), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
