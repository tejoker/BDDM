#!/usr/bin/env python3
"""Generate conjectures from paper context or theorem corpus."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

try:
    from mistralai import Mistral
except ImportError:
    from mistralai.client import Mistral  # type: ignore[no-redef]


SYSTEM_PROMPT = (
    "You are a Lean-focused mathematical research assistant. "
    "Generate plausible, non-trivial conjectures from provided context. "
    "Return strict JSON with key 'conjectures' containing list entries with: "
    "title, informal_statement, lean_draft, motivation."
)


_FENCED_JSON_RE = re.compile(r"^```(?:json)?\s*([\s\S]*?)\s*```$", re.IGNORECASE)


def _loads_lenient_json(text: str):
    try:
        return json.loads(text)
    except Exception:
        # Recover common LLM JSON mistakes where LaTeX-style escapes (e.g. "\(")
        # are emitted inside strings but are not valid JSON escape sequences.
        repaired = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", text)
        return json.loads(repaired)


def _response_text(resp) -> str:
    try:
        ch = resp.choices[0]
        msg = getattr(ch, "message", None)
        content = getattr(msg, "content", "") if msg is not None else ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for p in content:
                txt = getattr(p, "text", None)
                if isinstance(txt, str):
                    parts.append(txt)
            return "\n".join(parts)
    except Exception:
        pass
    return str(resp)


def _extract_json_payload(raw: str):
    text = (raw or "").strip()
    if not text:
        return {"conjectures": [], "raw": raw}

    # Accept responses wrapped in markdown fences.
    m = _FENCED_JSON_RE.match(text)
    if m:
        text = m.group(1).strip()

    try:
        return _loads_lenient_json(text)
    except Exception:
        return {"conjectures": [], "raw": raw}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate conjectures from context")
    parser.add_argument("--context-file", required=True, help="Path to text/markdown context")
    parser.add_argument("--model", default="labs-leanstral-2603")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--out", default="output/conjectures/generated_conjectures.json")
    args = parser.parse_args()

    api_key = os.getenv("MISTRAL_API_KEY", "")
    if not api_key:
        raise SystemExit("MISTRAL_API_KEY is required")

    context_path = Path(args.context_file)
    if not context_path.exists():
        raise SystemExit(f"context file not found: {context_path}")

    context = context_path.read_text(encoding="utf-8")[:20000]

    user_prompt = (
        f"Generate {args.count} conjectures from this context. "
        "Output strict JSON only.\n\n"
        f"Context:\n{context}"
    )

    client = Mistral(api_key=api_key)
    resp = client.chat.complete(
        model=args.model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=1800,
    )

    raw = _response_text(resp).strip()
    payload = _extract_json_payload(raw)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(out_path), "count": len(payload.get("conjectures", []))}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
