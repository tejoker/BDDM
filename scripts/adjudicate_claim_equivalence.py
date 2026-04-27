#!/usr/bin/env python3
"""Optionally adjudicate claim equivalence review rows with an LLM."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from claim_equivalence_review import read_jsonl, validate_adjudication, write_jsonl

SYSTEM = (
    "You adjudicate whether a Lean theorem statement is semantically equivalent "
    "to a paper theorem. Classify only the statement relationship, not whether "
    "the proof is easy or whether all domain symbols are formalized. Output JSON only."
)


def _extract_json(text: str) -> dict[str, Any]:
    try:
        raw = json.loads(text)
        return raw if isinstance(raw, dict) else {}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text or "", flags=re.DOTALL)
    if not match:
        return {}
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _skeleton_adjudication(row: dict[str, Any], *, adjudicator: str, verdict: str = "unclear", rationale: str = "") -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "review_id": row.get("review_id", ""),
        "paper_id": row.get("paper_id", ""),
        "theorem_name": row.get("theorem_name", ""),
        "adjudicator": adjudicator,
        "reviewer_type": "llm" if adjudicator.startswith(("llm:", "dry_run", "unavailable")) else "human",
        "review_policy": "requires_human_for_release",
        "verdict": verdict,
        "confidence": 0.0 if verdict == "unclear" else 0.8,
        "rationale": rationale or "No adjudication was performed; this row remains pending review.",
        "assumption_alignment": [],
        "conclusion_alignment": {"paper": row.get("extracted_conclusion", ""), "lean": "", "status": "unclear"},
        "risk_flags": ["needs_human_review"],
        "required_ledger_markers": [],
        "created_at_unix": int(time.time()),
    }


def _prompt(row: dict[str, Any]) -> str:
    return (
        f"{row.get('review_prompt', '')}\n\n"
        "Return JSON with keys: verdict, confidence, rationale, assumption_alignment, "
        "conclusion_alignment, risk_flags, required_ledger_markers. "
        "Allowed verdicts: equivalent, weaker, stronger, not_equivalent, unclear. "
        "Use equivalent only when all assumptions and the conclusion match. "
        "This is LLM triage only; human or hybrid review is required for release promotion."
    )


def _fallback_assumption_alignment(row: dict[str, Any], parsed: dict[str, Any]) -> list[dict[str, str]]:
    raw = parsed.get("assumption_alignment")
    if isinstance(raw, list) and raw:
        return raw
    return [
        {"paper": str(item), "lean": "", "status": "unclear", "notes": "LLM output did not align this assumption."}
        for item in (row.get("extracted_assumptions") or [])
    ]


def adjudicate_rows(
    rows: list[dict[str, Any]],
    *,
    model: str,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    if dry_run:
        return [_skeleton_adjudication(row, adjudicator="dry_run") for row in rows]

    load_dotenv()
    api_key = os.environ.get("MISTRAL_API_KEY", "")
    if not api_key:
        return [
            _skeleton_adjudication(
                row,
                adjudicator="unavailable",
                rationale="MISTRAL_API_KEY is not set; no LLM adjudication was performed.",
            )
            for row in rows
        ]

    try:
        from mistralai import Mistral
        from ponder_loop import _chat_complete
    except Exception:
        return [
            _skeleton_adjudication(
                row,
                adjudicator="unavailable",
                rationale="Mistral client or ponder_loop helper is unavailable.",
            )
            for row in rows
        ]

    client = Mistral(api_key=api_key)
    out: list[dict[str, Any]] = []
    for row in rows:
        _, raw = _chat_complete(
            client=client,
            model=model,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": _prompt(row)}],
            temperature=0.0,
            max_tokens=1200,
            purpose="claim_equivalence_adjudication",
        )
        parsed = _extract_json(raw)
        candidate = {
            "schema_version": "1.0.0",
            "review_id": row.get("review_id", ""),
            "paper_id": row.get("paper_id", ""),
            "theorem_name": row.get("theorem_name", ""),
            "adjudicator": f"llm:{model}",
            "reviewer_type": "llm",
            "review_policy": "requires_human_for_release",
            "verdict": parsed.get("verdict", "unclear"),
            "confidence": parsed.get("confidence", 0.0),
            "rationale": parsed.get("rationale", ""),
            "assumption_alignment": _fallback_assumption_alignment(row, parsed),
            "conclusion_alignment": parsed.get("conclusion_alignment", {}),
            "risk_flags": list(dict.fromkeys([*(parsed.get("risk_flags", []) or []), "needs_human_review"])),
            "required_ledger_markers": parsed.get("required_ledger_markers", []),
            "created_at_unix": int(time.time()),
        }
        try:
            out.append(validate_adjudication(candidate))
        except Exception as exc:
            out.append(
                _skeleton_adjudication(
                    row,
                    adjudicator=f"llm:{model}",
                    rationale=f"Invalid structured adjudication output: {exc}",
                )
            )
    return out


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Optionally adjudicate claim-equivalence queue rows with an LLM")
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--out-jsonl", required=True, type=Path)
    parser.add_argument("--model", default=os.environ.get("MISTRAL_MODEL", "mistral-large-latest"))
    parser.add_argument("--dry-run", action="store_true", help="Write pending/unclear skeleton adjudications without API calls")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    rows = read_jsonl(args.queue)
    adjudications = adjudicate_rows(rows, model=str(args.model), dry_run=bool(args.dry_run))
    write_jsonl(args.out_jsonl, adjudications)
    print(json.dumps({"out_jsonl": str(args.out_jsonl), "rows": len(adjudications)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
