#!/usr/bin/env python3
"""Seed the internal KG with Mathlib lemmas as GROUNDED_MATHLIB trusted entries.

Strategy:
1. Load the Mathlib embedding index (data/mathlib_embeddings/) which has 136k lemma names
   and their statement strings.
2. For each lemma, write a KG entry to output/kg/trusted/mathlib_seed.jsonl with:
   - status: FULLY_PROVEN
   - grounding: GROUNDED_MATHLIB
   - trust_class: TRUST_MATHLIB
   - lean_statement: the Mathlib statement string
   - proof_text: "" (proof exists in Mathlib, not replicated here)
   - provenance: {source: "mathlib4", namespace: inferred from name prefix}
3. Optionally generate a short informal description per lemma via Leanstral
   (only for a subset, controlled by --describe-top-k).

The seed gives the KG a dense trusted foundation so assumption grounding
step 2 (GROUNDED_INTERNAL_KG scan) has real coverage immediately.

Usage:
    python scripts/seed_kg_from_mathlib.py \\
        --index data/mathlib_embeddings \\
        --out output/kg/trusted/mathlib_seed.jsonl \\
        --describe-top-k 0   # set > 0 to generate descriptions via Leanstral
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _load_index_entries(index_path: str) -> list[dict[str, Any]]:
    """Load lemma names and statements from the mathlib embedding index."""
    p = Path(index_path)
    entries_file = p / "entries.jsonl"
    if not entries_file.exists():
        # Try legacy format: separate names/statements files.
        names_file = p / "names.json"
        stmts_file = p / "statements.json"
        if names_file.exists() and stmts_file.exists():
            names = json.loads(names_file.read_text(encoding="utf-8"))
            stmts = json.loads(stmts_file.read_text(encoding="utf-8"))
            return [{"name": n, "statement": s} for n, s in zip(names, stmts)]
        raise FileNotFoundError(
            f"No entries.jsonl or names/statements files found in {index_path}"
        )
    entries = []
    with entries_file.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    pass
    return entries


def _infer_namespace(name: str) -> str:
    """Infer Mathlib namespace from dotted lemma name."""
    parts = name.split(".")
    if len(parts) >= 2:
        return ".".join(parts[:-1])
    return "Mathlib"


def _build_kg_entry(name: str, statement: str) -> dict[str, Any]:
    return {
        "theorem_name": name,
        "lean_statement": statement,
        "lean_file": f"Mathlib ({name})",
        "status": "FULLY_PROVEN",
        "step_verdict": "VERIFIED",
        "failure_origin": None,
        "trust_class": "TRUST_MATHLIB",
        "trust_reference": "mathlib4",
        "promotion_gate_passed": True,
        "step_obligations": [],
        "assumptions": [
            {
                "label": "mathlib_axioms",
                "lean_expr": "(by mathlib)",
                "grounding": "GROUNDED_MATHLIB",
                "grounding_source": "mathlib4",
                "trust_class": "TRUST_MATHLIB",
                "trust_reference": "mathlib4",
            }
        ],
        "provenance": {
            "paper_id": "mathlib4",
            "section": _infer_namespace(name),
            "label": name,
            "cited_refs": [],
        },
        "proof_text": "",  # Proof lives in Mathlib — not replicated.
        "informal_description": "",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _generate_description(
    name: str,
    statement: str,
    client: Any,
    model: str,
) -> str:
    """Ask Leanstral to describe a Mathlib lemma in one sentence."""
    try:
        from mistralai import Mistral
    except ImportError:
        return ""
    try:
        resp = client.chat.complete(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a mathematical explainer. "
                        "Describe the given Lean 4 / Mathlib lemma in one concise English sentence. "
                        "Be precise. Do not say 'this lemma states'. Just state the mathematical fact."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Lemma name: {name}\nStatement: {statement}",
                },
            ],
            temperature=0.0,
            max_tokens=80,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return ""


def seed_kg(
    *,
    index_path: str,
    out_path: str,
    describe_top_k: int = 0,
    model: str = "",
    api_key: str = "",
    batch_size: int = 1000,
) -> int:
    """Write GROUNDED_MATHLIB KG entries from the embedding index.

    Returns total entries written.
    """
    logger.info("Loading Mathlib index from %s", index_path)
    raw_entries = _load_index_entries(index_path)
    logger.info("Loaded %d lemmas", len(raw_entries))

    client = None
    if describe_top_k > 0 and model and api_key:
        try:
            from mistralai import Mistral
            client = Mistral(api_key=api_key)
        except ImportError:
            logger.warning("mistralai not available — skipping descriptions")

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with out.open("w", encoding="utf-8") as fh:
        for i, entry in enumerate(raw_entries):
            name = entry.get("name", entry.get("theorem_name", ""))
            statement = entry.get("statement", entry.get("lean_statement", ""))
            if not name or not statement:
                continue

            kg_entry = _build_kg_entry(name, statement)

            if client is not None and i < describe_top_k:
                kg_entry["informal_description"] = _generate_description(
                    name, statement, client, model
                )

            fh.write(json.dumps(kg_entry, ensure_ascii=True) + "\n")
            written += 1

            if written % batch_size == 0:
                logger.info("Written %d / %d entries", written, len(raw_entries))

    logger.info("Seeding complete: %d entries written to %s", written, out_path)
    return written


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Seed internal KG from Mathlib embedding index")
    p.add_argument(
        "--index",
        default="data/mathlib_embeddings",
        help="Path to mathlib embeddings directory",
    )
    p.add_argument(
        "--out",
        default="output/kg/trusted/mathlib_seed.jsonl",
        help="Output JSONL path",
    )
    p.add_argument(
        "--describe-top-k",
        type=int,
        default=0,
        help="Generate informal descriptions for the top-K entries via Leanstral (0 = skip)",
    )
    p.add_argument("--model", default="labs-leanstral-2603", help="Leanstral model name")
    p.add_argument(
        "--api-key",
        default=os.environ.get("MISTRAL_API_KEY", ""),
        help="Mistral API key (defaults to MISTRAL_API_KEY env var)",
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()
    seed_kg(
        index_path=args.index,
        out_path=args.out,
        describe_top_k=args.describe_top_k,
        model=args.model,
        api_key=args.api_key,
    )


if __name__ == "__main__":
    main()
