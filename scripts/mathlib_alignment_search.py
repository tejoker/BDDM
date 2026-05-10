#!/usr/bin/env python3
"""Search Mathlib for counterpart symbols of a paper-local axiom.

Given a paper-local axiom (signature + optional natural-language description),
this tool queries Leanstral for candidate Mathlib symbols that might be the
same mathematical concept. The output is a ranked candidate list that can be
piped into the alignment registry (`output/corpus/alignments.json`) after
human or programmatic validation.

Why Leanstral as the search oracle:
  - Mathlib has ~150 000 declarations; a brute-force signature scan is
    impractical without a real embeddings index (the project ships a 10-row
    stub at `data/mathlib_embeddings/entries.jsonl`).
  - Leanstral is trained on the full Mathlib corpus and can suggest
    Mathlib-resolvable names + signatures + a brief rationale in one call.
  - The output is treated as a CANDIDATE LIST, not authoritative — every
    candidate must be validated by:
      (a) `lake env lean` elaboration of a probe `theorem _ : <candidate>` line, and
      (b) signature compatibility against the paper-local axiom.
    This file produces (a) automatically; the user reviews + registers (b).

Output schema:
  {
    "schema_version": "mathlib_alignment_search.v1",
    "paper_id": "...",
    "paper_local_name": "...",
    "axiom_signature": "...",
    "candidates": [
      {
        "mathlib_name": "...",
        "mathlib_signature": "...",
        "rationale": "...",
        "confidence": 0.0-1.0,
        "elaboration_check": "ok" | "failed:..." | "skipped",
      },
      ...
    ],
  }

Pipeline policy: only Leanstral. The model defaults to `labs-leanstral-2603`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_MODEL = os.environ.get("MISTRAL_MODEL", "labs-leanstral-2603")


# ---------------------------------------------------------------------------
# Leanstral search prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a Mathlib lookup oracle for paper-to-Lean formalization.\n"
    "Given a paper-local axiom (a Lean signature with paper-specific name)\n"
    "and an optional natural-language description, propose 1-5 Mathlib symbols\n"
    "that might be the same mathematical concept. Each candidate must be a\n"
    "real, current Mathlib name (e.g., `Matrix.opNorm`, `MeasureTheory.ae`),\n"
    "not invented. Output JSON only.\n\n"
    "JSON shape:\n"
    '{"candidates": [\n'
    '  {"mathlib_name": "<full namespaced name>",\n'
    '   "mathlib_signature": "<one-line approx signature>",\n'
    '   "rationale": "<= 80 char>",\n'
    '   "confidence": 0.0-1.0},\n'
    "  ...\n"
    "]}\n\n"
    "If you can't find any plausible counterpart, return an empty candidates list.\n"
    "Do not invent names. Confidence should reflect signature and concept match,\n"
    "with 0.9+ reserved for cases where the Mathlib symbol is provably the same."
)

_USER_TEMPLATE = (
    "Paper-local axiom:\n"
    "  name: {paper_local_name}\n"
    "  signature: {signature}\n"
    "{description_block}"
    "\n"
    "Propose Mathlib counterparts. JSON only."
)


def _leanstral_search(
    *,
    paper_local_name: str,
    signature: str,
    description: str,
    client: Any,
    model: str,
) -> list[dict[str, Any]]:
    """Call Leanstral with the search prompt; parse and return candidates."""
    description_block = (
        f"  description: {description}\n" if description.strip() else ""
    )
    user = _USER_TEMPLATE.format(
        paper_local_name=paper_local_name,
        signature=signature.strip()[:400],
        description_block=description_block,
    )
    response = client.chat.complete(
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        max_tokens=600,
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    candidates = parsed.get("candidates", []) if isinstance(parsed, dict) else []
    return [c for c in candidates if isinstance(c, dict)]


# ---------------------------------------------------------------------------
# Mathlib elaboration check
# ---------------------------------------------------------------------------

def _elaboration_check(mathlib_name: str, *, project_root: Path) -> str:
    """Return 'ok' if `mathlib_name` exists and elaborates under
    `import Mathlib`, otherwise an error string."""
    if not mathlib_name or not re.match(r"^[A-Za-z_][\w'.]*$", mathlib_name):
        return "skipped:invalid_name"
    probe = f"import Mathlib\n#check @{mathlib_name}\n"
    tmp_dir = project_root / "Desol"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="_tmp_align_search_",
        suffix=".lean",
        dir=tmp_dir,
        delete=False,
    ) as f:
        path = Path(f.name)
        f.write(probe.encode())
    try:
        res = subprocess.run(
            ["lake", "env", "lean", str(path)],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if res.returncode == 0:
            return "ok"
        first_err = next(
            (l for l in (res.stdout + res.stderr).splitlines() if "error" in l.lower()),
            "elaboration failed",
        )
        return f"failed:{first_err.strip()[:120]}"
    except subprocess.TimeoutExpired:
        return "failed:timeout"
    except Exception as exc:
        return f"failed:{exc}"
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Top-level search
# ---------------------------------------------------------------------------

def search_alignment(
    *,
    paper_id: str,
    paper_local_name: str,
    signature: str,
    description: str = "",
    client: Any | None = None,
    model: str = _DEFAULT_MODEL,
    skip_elaboration: bool = False,
    project_root: Path = _PROJECT_ROOT,
) -> dict[str, Any]:
    """Run the full search: Leanstral → candidates → optional elaboration check."""
    if client is None:
        client = _build_mistral_client()
    candidates = _leanstral_search(
        paper_local_name=paper_local_name,
        signature=signature,
        description=description,
        client=client,
        model=model,
    )
    for c in candidates:
        name = str(c.get("mathlib_name", "") or "").strip()
        c["elaboration_check"] = (
            "skipped" if skip_elaboration else _elaboration_check(name, project_root=project_root)
        )
    # Sort: ok-elaborating candidates first, then by confidence descending.
    candidates.sort(
        key=lambda c: (
            0 if str(c.get("elaboration_check", "")).startswith("ok") else 1,
            -float(c.get("confidence", 0.0) or 0.0),
        )
    )
    return {
        "schema_version": "mathlib_alignment_search.v1",
        "paper_id": paper_id,
        "paper_local_name": paper_local_name,
        "axiom_signature": signature,
        "candidates": candidates,
    }


def _build_mistral_client() -> Any:
    api_key = os.environ.get("MISTRAL_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("MISTRAL_API_KEY is not set in env; cannot run search")
    try:
        from mistralai import Mistral  # type: ignore[import-not-found]
    except ImportError:
        from mistralai.client import Mistral  # type: ignore[no-redef]
    return Mistral(api_key=api_key)


# ---------------------------------------------------------------------------
# Batch mode: scan a paper-theory file for axiom declarations
# ---------------------------------------------------------------------------

_AXIOM_LINE = re.compile(r"^axiom\s+(?P<name>[\w']+)\s*:\s*(?P<sig>.+?)\s*$", re.MULTILINE)


def scan_axioms_in_paper_theory(paper_id: str, project_root: Path = _PROJECT_ROOT) -> list[dict[str, str]]:
    """Walk `Desol/PaperTheory/Paper_<id>.lean` and return all `axiom` decls."""
    module = "Paper_" + paper_id.replace(".", "_").replace("-", "_")
    path = project_root / "Desol" / "PaperTheory" / f"{module}.lean"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return [
        {"name": m.group("name"), "signature": m.group("sig")}
        for m in _AXIOM_LINE.finditer(text)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paper_id", help="arxiv id, e.g. 2604.21884")
    parser.add_argument(
        "--axiom-name",
        help="Specific axiom to search; if omitted, scan paper-theory for all axiom decls",
    )
    parser.add_argument("--signature", help="Override signature (otherwise read from paper-theory)")
    parser.add_argument("--description", default="", help="Optional natural-language description")
    parser.add_argument("--model", default=_DEFAULT_MODEL)
    parser.add_argument("--skip-elaboration", action="store_true",
                        help="Skip the lake-env-lean elaboration check (faster, less validation)")
    parser.add_argument("--out", type=Path, default=None, help="Write JSON to this file (default: stdout)")
    args = parser.parse_args()

    if args.axiom_name:
        targets = [{"name": args.axiom_name, "signature": args.signature or ""}]
    else:
        targets = scan_axioms_in_paper_theory(args.paper_id)
        if not targets:
            print(f"No axioms found in Desol/PaperTheory/Paper_{args.paper_id.replace('.', '_')}.lean",
                  file=sys.stderr)
            return 0

    client = _build_mistral_client()
    results: list[dict[str, Any]] = []
    for t in targets:
        result = search_alignment(
            paper_id=args.paper_id,
            paper_local_name=t["name"],
            signature=t["signature"],
            description=args.description,
            client=client,
            model=args.model,
            skip_elaboration=args.skip_elaboration,
        )
        results.append(result)

    payload = {
        "schema_version": "mathlib_alignment_search_batch.v1",
        "paper_id": args.paper_id,
        "results": results,
    }
    output = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
        print(f"Wrote {len(results)} axiom search(es) to {args.out}")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
