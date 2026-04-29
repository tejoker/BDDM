#!/usr/bin/env python3
"""Parse LaTeX theorem/lemma/proposition environments from .tex files.

For each environment found, extracts:
  - kind       : theorem | lemma | proposition | corollary | definition
  - name       : optional \\label{...} or positional index
  - statement  : the LaTeX source of the statement body
  - proof      : the LaTeX source of the immediately following proof (if any)

Usage:
    python3 theorem_extractor.py main.tex
    python3 theorem_extractor.py main.tex --json
    python3 theorem_extractor.py main.tex --json > theorems.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# Canonical kind name for each environment alias.
# Keys are lowercase env names as they appear in \begin{...}.
_ENV_KIND: dict[str, str] = {
    # theorem
    "theorem": "theorem", "thm": "theorem", "theo": "theorem",
    # lemma
    "lemma": "lemma", "lem": "lemma",
    # proposition
    "proposition": "proposition", "prop": "proposition",
    # corollary
    "corollary": "corollary", "cor": "corollary", "coro": "corollary",
    # other theorem-like
    "fact": "fact", "claim": "claim", "observation": "observation",
    "remark": "remark", "rem": "remark",
    # definition (skipped during proof search by default)
    "definition": "definition", "defn": "definition", "notation": "definition",
}

_THEOREM_KINDS = frozenset({"theorem", "lemma", "proposition", "corollary", "fact", "claim", "observation", "remark"})
_DEFINITION_KINDS = frozenset({"definition"})
_ALL_ENVS = frozenset(_ENV_KIND.keys())

_BEGIN_RE = None


def _rebuild_begin_re() -> None:
    global _BEGIN_RE, _ALL_ENVS
    _ALL_ENVS = frozenset(_ENV_KIND.keys())
    _BEGIN_RE = re.compile(
        r"\\begin\{(" + "|".join(re.escape(e) for e in sorted(_ALL_ENVS, key=len, reverse=True)) + r")\*?\}",
        re.IGNORECASE,
    )


_rebuild_begin_re()
_END_RE_TEMPLATE = r"\\end\{%s\*?\}"
_LABEL_RE = re.compile(r"\\label\{([^}]+)\}")
_PROOF_BEGIN_RE = re.compile(r"\\begin\{proof\}", re.IGNORECASE)
_PROOF_END_RE = re.compile(r"\\end\{proof\}", re.IGNORECASE)


@dataclass
class SourceSpan:
    source_file: str
    start_byte: int
    end_byte: int
    start_line: int
    start_col: int
    end_line: int
    end_col: int


@dataclass
class TheoremEntry:
    kind: str
    name: str          # label or "thm_<n>"
    statement: str     # raw LaTeX of the statement body
    proof: str         # raw LaTeX of the proof body (empty if none)
    source_file: str
    source_span: SourceSpan | None = None
    proof_span: SourceSpan | None = None
    env_name: str = ""
    label: str = ""
    span_start: int = -1
    span_end: int = -1
    body_start: int = -1
    body_end: int = -1
    start_line: int = -1
    end_line: int = -1
    source_span_id: str = ""


def _find_env_span(text: str, start: int, env_name: str) -> tuple[int, int]:
    """Return the body-end/env-end span for a possibly nested environment."""
    env_key = env_name.lower().rstrip("*")
    token_re = re.compile(r"\\(begin|end)\{" + re.escape(env_key) + r"\*?\}", re.IGNORECASE)
    depth = 1
    for m in token_re.finditer(text, start):
        if m.group(1).lower() == "begin":
            depth += 1
            continue
        depth -= 1
        if depth == 0:
            return m.start(), m.end()
    return len(text), len(text)


def _extract_label(body: str) -> str:
    m = _LABEL_RE.search(body)
    return m.group(1) if m else ""


def _line_col(text: str, offset: int) -> tuple[int, int]:
    """Return 1-indexed line/column for a character offset."""
    prefix = text[:offset]
    line = prefix.count("\n") + 1
    last_newline = prefix.rfind("\n")
    if last_newline < 0:
        return line, offset + 1
    return line, offset - last_newline


def _byte_offset(text: str, offset: int) -> int:
    return len(text[:offset].encode("utf-8"))


def _make_span(text: str, source_file: str, start: int, end: int) -> SourceSpan:
    start_line, start_col = _line_col(text, start)
    end_line, end_col = _line_col(text, end)
    return SourceSpan(
        source_file=source_file,
        start_byte=_byte_offset(text, start),
        end_byte=_byte_offset(text, end),
        start_line=start_line,
        start_col=start_col,
        end_line=end_line,
        end_col=end_col,
    )


def _source_span_id(
    *,
    source_file: Path,
    env_name: str,
    span_start: int,
    span_end: int,
    label: str,
    statement: str,
) -> str:
    normalized = " ".join((statement or "").split())
    payload = "\n".join(
        [
            str(source_file),
            env_name.lower().rstrip("*"),
            str(span_start),
            str(span_end),
            label,
            normalized,
        ]
    )
    return "srcspan_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _trimmed_span(text: str, start: int, end: int) -> tuple[str, int, int]:
    raw = text[start:end]
    leading = len(raw) - len(raw.lstrip())
    trailing = len(raw.rstrip())
    trimmed_start = start + leading
    trimmed_end = start + trailing
    return text[trimmed_start:trimmed_end], trimmed_start, trimmed_end


def _extract_proof_after(text: str, env_end: int) -> tuple[str, int, int]:
    """Return proof body and span if a proof follows immediately (within 200 chars)."""
    window = text[env_end: env_end + 200]
    # Only blank lines / whitespace / comments between env end and proof begin.
    gap = window.lstrip()
    if not gap.startswith("\\begin{proof}") and not gap.startswith("\\begin{Proof}"):
        return "", -1, -1
    proof_start_abs = env_end + (len(window) - len(gap))
    proof_body_start = text.index("{proof}", proof_start_abs) + len("{proof}")
    proof_end = _PROOF_END_RE.search(text, proof_body_start)
    if proof_end is None:
        return "", -1, -1
    return _trimmed_span(text, proof_body_start, proof_end.start())


def register_environment_aliases(aliases: dict[str, str]) -> None:
    changed = False
    for env_name, canonical_kind in aliases.items():
        env_key = env_name.lower().rstrip("*")
        if env_key and _ENV_KIND.get(env_key) != canonical_kind:
            _ENV_KIND[env_key] = canonical_kind
            changed = True
    if changed:
        _rebuild_begin_re()


def extract_theorems(tex_path: Path) -> list[TheoremEntry]:
    try:
        text = tex_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"[warn] cannot read {tex_path}: {exc}", file=sys.stderr)
        return []

    entries: list[TheoremEntry] = []
    counter = 0

    if _BEGIN_RE is None:
        _rebuild_begin_re()
    for m in _BEGIN_RE.finditer(text):
        env_name = m.group(1).lower().rstrip("*")
        body_start = m.end()
        body_end, env_end = _find_env_span(text, body_start, env_name)
        body, body_trimmed_start, body_trimmed_end = _trimmed_span(text, body_start, body_end)

        label = _extract_label(body)
        counter += 1
        name = label if label else f"{env_name}_{counter}"
        proof, proof_start, proof_end = _extract_proof_after(text, env_end)

        canonical_kind = _ENV_KIND.get(env_name, env_name)
        entries.append(TheoremEntry(
            kind=canonical_kind,
            name=name,
            statement=body,
            proof=proof,
            source_file=str(tex_path),
            source_span=_make_span(text, str(tex_path), body_trimmed_start, body_trimmed_end),
            proof_span=_make_span(text, str(tex_path), proof_start, proof_end) if proof_start >= 0 else None,
            env_name=env_name,
            label=label,
            span_start=m.start(),
            span_end=env_end,
            body_start=body_trimmed_start,
            body_end=body_trimmed_end,
            start_line=_line_col(text, m.start())[0],
            end_line=_line_col(text, env_end)[0],
            source_span_id=_source_span_id(
                source_file=tex_path,
                env_name=env_name,
                span_start=m.start(),
                span_end=env_end,
                label=label,
                statement=body,
            ),
        ))

    return entries


def extract_from_files(tex_paths: list[Path]) -> list[TheoremEntry]:
    all_entries: list[TheoremEntry] = []
    for p in tex_paths:
        entries = extract_theorems(p)
        all_entries.extend(entries)
    return all_entries


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Extract theorem environments from LaTeX")
    p.add_argument("tex_files", nargs="+", help=".tex file paths")
    p.add_argument("--json", action="store_true", help="Output JSON instead of plain text")
    p.add_argument(
        "--kinds",
        default="",
        help="Comma-separated kinds to include (default: all). E.g. theorem,lemma",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()
    kinds_filter: set[str] = set()
    if args.kinds:
        kinds_filter = {k.strip().lower() for k in args.kinds.split(",")}

    tex_paths = [Path(f) for f in args.tex_files]
    entries = extract_from_files(tex_paths)

    if kinds_filter:
        entries = [e for e in entries if e.kind in kinds_filter]

    if not entries:
        print("[warn] no theorem environments found", file=sys.stderr)
        return 0

    if args.json:
        print(json.dumps([asdict(e) for e in entries], indent=2, ensure_ascii=False))
    else:
        for e in entries:
            print(f"[{e.kind}] {e.name}  ({e.source_file})")
            stmt = " ".join(e.statement.split())[:160]
            print(f"  {stmt}")
            if e.proof:
                prf = " ".join(e.proof.split())[:100]
                print(f"  proof: {prf}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
