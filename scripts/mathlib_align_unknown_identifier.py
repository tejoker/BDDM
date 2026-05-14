#!/usr/bin/env python3
"""Resolve an `unknown identifier 'X'` elaboration failure to a Mathlib name.

This tool answers the question: given a paper_id and an identifier `X` that
the Lean elaborator reports as `unknown_identifier`, what is the right thing
to put in its place? There are three honest possibilities:

  1. `X` IS in Mathlib under the same or a near-by name (`Matrix.dotProduct`
     is actually just `dotProduct` at the top level; the `Matrix.` prefix is
     wrong).
  2. `X` IS declared in paper-theory but the user reached it through the
     wrong namespace (`Foo.bar` when the actual declaration is
     `Paper_2304_09598.Foo.bar`).
  3. `X` is genuinely undefined — the right fix is a paper-theory stub.

Zero-Mistral: this is pure index lookup. No LLM cost.

Build of the name index:
  Walks `.lake/packages/mathlib/Mathlib/**/*.lean` and extracts every
  top-level `theorem|lemma|def|abbrev|axiom|instance|structure|class|inductive`
  declaration, tracking the enclosing `namespace ... end` blocks. The result
  is cached to `data/mathlib_name_index.json` on first build (about 7800
  files, a few seconds to a minute on cold disk).

Scoring:
  - exact match on fully-qualified name: 1.0 (`name_normalization` / `exact`)
  - exact match on the final component, any namespace: high (0.8-0.95)
  - tokenized similarity: split CamelCase + dots + underscores; Jaccard on
    the resulting bag-of-tokens.
  - normalized edit distance on the last component.
  Final score is a max-of-the-above (we want the best signal, not an average).

Output schema (`mathlib_align_unknown_identifier.v1`):
  {
    "schema_version": "mathlib_align_unknown_identifier.v1",
    "paper_id": "P",
    "name": "X",
    "verdict": "mathlib_match" | "namespace_prefix" | "no_match",
    "candidates": [
      {
        "target_name": "Y",
        "score": 0.85,
        "module": "Mathlib.Data.Matrix.Mul",
        "kind": "name_normalization" | "namespace_prefix" | "fuzzy_match",
        "rationale": "<short reason>"
      },
      ...
    ]
  }

Constraint — standards-positive: every candidate is VERIFIABLE because it
came from a real Mathlib source line. We do not invent names. The optional
`--auto-register` step appends well-scored candidates (kind=name_normalization
with score >= --register-threshold) to `output/corpus/alignments.json` using
the same schema as `generate_trivial_alignments.py` / `mathlib_alignment_search.py`,
so downstream consumers (`audit_axioms.py`, `apply_reviews_to_ledger.py`)
pick them up without code changes.

The tool ADDS to the alignment registry; it never replaces existing entries.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _find_mathlib_root(project_root: Path = _PROJECT_ROOT) -> Path:
    """Locate `.lake/packages/mathlib/Mathlib` for this project.

    Git worktrees often share `.lake` with the parent checkout, so if the
    local worktree doesn't have it, walk up to find a sibling that does.
    Returns the first existing directory, or the local (non-existent) path
    so callers can report a clean error."""
    local = project_root / ".lake" / "packages" / "mathlib" / "Mathlib"
    if local.exists():
        return local
    cursor = project_root.parent
    for _ in range(6):
        candidate = cursor / ".lake" / "packages" / "mathlib" / "Mathlib"
        if candidate.exists():
            return candidate
        cursor = cursor.parent
    return local


_MATHLIB_ROOT = _find_mathlib_root()


def _find_name_index_path(project_root: Path = _PROJECT_ROOT) -> Path:
    """Same fallback as `_find_mathlib_root`: prefer a sibling checkout's
    cache so worktrees don't have to rebuild the 37MB index."""
    local = project_root / "data" / "mathlib_name_index.json"
    if local.exists():
        return local
    cursor = project_root.parent
    for _ in range(6):
        candidate = cursor / "data" / "mathlib_name_index.json"
        if candidate.exists():
            return candidate
        cursor = cursor.parent
    return local


_NAME_INDEX_PATH = _find_name_index_path()
_ALIGNMENTS_PATH = _PROJECT_ROOT / "output" / "corpus" / "alignments.json"
_PAPER_THEORY_DIR = _PROJECT_ROOT / "Desol" / "PaperTheory"


_SCHEMA_VERSION = "mathlib_align_unknown_identifier.v1"


# Declaration keywords we extract from Mathlib sources. Order doesn't matter
# beyond what we report in the `kind` field of the per-entry index.
_DECL_KINDS = ("theorem", "lemma", "def", "abbrev", "axiom", "instance",
               "structure", "class", "inductive")
_DECL_PATTERN = re.compile(
    r"^(?:@\[[^\]]*\]\s*)*"
    r"(?:noncomputable\s+|private\s+|protected\s+|partial\s+|unsafe\s+|nonrec\s+)*"
    r"(?P<kind>" + "|".join(_DECL_KINDS) + r")\s+"
    r"(?P<name>[A-Za-z_][\w'.]*)",
    re.MULTILINE,
)

_NAMESPACE_OPEN = re.compile(r"^namespace\s+(?P<name>[A-Za-z_][\w'.]*)", re.MULTILINE)
_NAMESPACE_CLOSE = re.compile(r"^end\s+(?P<name>[A-Za-z_][\w'.]*)?\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Name index construction
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Entry:
    """A single Mathlib declaration as it appears in the source."""
    name: str               # fully-qualified, e.g. "Matrix.dotProduct"
    last: str               # last dotted component, e.g. "dotProduct"
    module: str             # e.g. "Mathlib.Data.Matrix.Mul"
    kind: str               # theorem | def | axiom | ...

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "last": self.last, "module": self.module, "kind": self.kind}


def _module_name_from_path(path: Path, root: Path) -> str:
    """`.../Mathlib/Data/Matrix/Mul.lean` → `Mathlib.Data.Matrix.Mul`."""
    rel = path.relative_to(root.parent)  # parent of Mathlib gives us Mathlib/...
    return rel.with_suffix("").as_posix().replace("/", ".")


def _strip_comments(text: str) -> str:
    """Remove `--` line comments and `/- ... -/` block comments.

    Lean block comments can nest, so a hand-written scanner is safer than a
    regex. Performance is fine: each file is read once."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        # Block comment: handle nested /-! ... -/ and /- ... -/
        if i + 1 < n and text[i] == '/' and text[i+1] == '-':
            depth = 1
            j = i + 2
            while j < n and depth > 0:
                if j + 1 < n and text[j] == '/' and text[j+1] == '-':
                    depth += 1
                    j += 2
                elif j + 1 < n and text[j] == '-' and text[j+1] == '/':
                    depth -= 1
                    j += 2
                else:
                    j += 1
            # Preserve newlines so line-anchored regexes still work.
            out.append("\n" * text.count("\n", i, j))
            i = j
            continue
        # Line comment
        if i + 1 < n and text[i] == '-' and text[i+1] == '-':
            j = text.find("\n", i)
            if j < 0:
                break
            i = j
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _extract_entries_from_text(text: str, module: str) -> list[Entry]:
    """Walk a single .lean file's source, tracking the namespace stack, and
    yield Entries for every top-level declaration."""
    clean = _strip_comments(text)
    # Build a per-line stream of events: namespace_open / namespace_close / decl.
    # We sort by character position so the namespace stack stays consistent.
    events: list[tuple[int, str, str]] = []
    for m in _NAMESPACE_OPEN.finditer(clean):
        events.append((m.start(), "open", m.group("name")))
    for m in _NAMESPACE_CLOSE.finditer(clean):
        # An `end` without a name closes the most recent namespace.
        events.append((m.start(), "close", m.group("name") or ""))
    for m in _DECL_PATTERN.finditer(clean):
        events.append((m.start(), "decl", f"{m.group('kind')}|{m.group('name')}"))
    events.sort(key=lambda e: e[0])

    ns_stack: list[str] = []
    entries: list[Entry] = []
    for _pos, kind, payload in events:
        if kind == "open":
            ns_stack.append(payload)
        elif kind == "close":
            # `end Foo` should match a namespace `Foo` on the stack. If the
            # name isn't on the stack, it's closing a `section Foo` (which
            # doesn't change naming) — leave the stack alone. Unnamed `end`
            # pops the most recent namespace.
            if ns_stack:
                if payload:
                    if payload in ns_stack:
                        # Pop until we find the match, then pop it too.
                        while ns_stack and ns_stack[-1] != payload:
                            ns_stack.pop()
                        if ns_stack:
                            ns_stack.pop()
                    # else: section close; ignore.
                else:
                    ns_stack.pop()
        else:  # decl
            decl_kind, name = payload.split("|", 1)
            # Skip anonymous and underscore-only names
            if not name or name.startswith("_"):
                continue
            qualified = ".".join([*ns_stack, name]) if ns_stack else name
            last = qualified.rsplit(".", 1)[-1]
            entries.append(Entry(name=qualified, last=last, module=module, kind=decl_kind))
    return entries


def build_name_index(
    *,
    mathlib_root: Path = _MATHLIB_ROOT,
    cache_path: Path | None = _NAME_INDEX_PATH,
    use_cache: bool = True,
    progress: bool = False,
) -> dict[str, Any]:
    """Build (or load) the Mathlib name index.

    The on-disk cache format is:
      {"schema_version": "mathlib_name_index.v1",
       "mathlib_root": "<abs path>",
       "entries": [{"name": ..., "last": ..., "module": ..., "kind": ...}, ...],
       "by_last": {"dotProduct": [0, 17, 42], ...}}  # indices into entries

    Returns the parsed dict. Building from scratch over ~8000 files takes a
    few seconds to a minute; cache hits are sub-second."""
    if use_cache and cache_path is not None and cache_path.exists():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if data.get("schema_version") == "mathlib_name_index.v1" and data.get("entries"):
                return data
        except Exception:
            pass  # rebuild on cache parse failure

    if not mathlib_root.exists():
        return {
            "schema_version": "mathlib_name_index.v1",
            "mathlib_root": str(mathlib_root),
            "entries": [],
            "by_last": {},
            "error": f"mathlib_root not found: {mathlib_root}",
        }

    all_entries: list[Entry] = []
    files = sorted(mathlib_root.rglob("*.lean"))
    for i, path in enumerate(files):
        if progress and i % 500 == 0:
            print(f"  indexing {i}/{len(files)}: {path.name}", file=sys.stderr)
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        module = _module_name_from_path(path, mathlib_root)
        all_entries.extend(_extract_entries_from_text(text, module))

    by_last: dict[str, list[int]] = {}
    for idx, e in enumerate(all_entries):
        by_last.setdefault(e.last, []).append(idx)

    data = {
        "schema_version": "mathlib_name_index.v1",
        "mathlib_root": str(mathlib_root),
        "entries": [e.as_dict() for e in all_entries],
        "by_last": by_last,
    }

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data), encoding="utf-8")
    return data


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _tokenize(name: str) -> list[str]:
    """Split `Matrix.dotProductMul` into ['matrix', 'dot', 'product', 'mul']."""
    parts: list[str] = []
    for chunk in re.split(r"[._]+", name):
        if not chunk:
            continue
        for piece in _CAMEL_SPLIT.split(chunk):
            piece = piece.strip().lower()
            if piece:
                parts.append(piece)
    return parts


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _edit_ratio(a: str, b: str) -> float:
    """1.0 = identical, 0.0 = totally different. Uses difflib's ratio."""
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def score_candidate(query: str, candidate: Entry) -> tuple[float, str]:
    """Return (score, kind) for `query` against `candidate`.

    The kind classifies the alignment evidence:
      - "name_normalization": query and candidate match exactly on last
        component (likely just a namespace correction).
      - "fuzzy_match": close but not identical names.

    Score is max of (last-component edit ratio, full-name edit ratio,
    token Jaccard)."""
    q_last = query.rsplit(".", 1)[-1]
    last_eq = q_last == candidate.last

    full_edit = _edit_ratio(query, candidate.name)
    last_edit = _edit_ratio(q_last, candidate.last)
    tok = _jaccard(_tokenize(query), _tokenize(candidate.name))

    score = max(full_edit, last_edit, tok)
    if last_eq:
        # Exact last-component match is a strong signal; bump the score.
        score = max(score, 0.9)
        kind = "name_normalization"
    else:
        kind = "fuzzy_match"
    return score, kind


# ---------------------------------------------------------------------------
# Paper-theory namespace-prefix lookup
# ---------------------------------------------------------------------------

def _paper_theory_path(paper_id: str, project_root: Path = _PROJECT_ROOT) -> Path:
    module = "Paper_" + paper_id.replace(".", "_").replace("-", "_")
    return project_root / "Desol" / "PaperTheory" / f"{module}.lean"


def find_paper_theory_namespace_prefix(
    paper_id: str,
    name: str,
    project_root: Path = _PROJECT_ROOT,
) -> dict[str, Any] | None:
    """If `paper_id`'s paper-theory file declares a name that matches `name`'s
    last component (or `name` directly), return a namespace-prefix candidate."""
    path = _paper_theory_path(paper_id, project_root)
    if not path.exists():
        return None
    text = _strip_comments(path.read_text(encoding="utf-8"))
    module = "Paper_" + paper_id.replace(".", "_").replace("-", "_")
    # Look for `def/abbrev/axiom/theorem/lemma <name>` (without namespace prefix).
    # The whole file is assumed to be under `namespace Paper_<id>`.
    last = name.rsplit(".", 1)[-1]
    for m in _DECL_PATTERN.finditer(text):
        cand = m.group("name")
        if cand == last or cand == name:
            return {
                "target_name": f"{module}.{cand}",
                "score": 0.95,
                "module": module,
                "kind": "namespace_prefix",
                "rationale": f"declared in paper-theory module {module}",
            }
    return None


# ---------------------------------------------------------------------------
# Top-level resolver
# ---------------------------------------------------------------------------

def resolve_unknown_identifier(
    *,
    paper_id: str,
    name: str,
    name_index: dict[str, Any],
    top_k: int = 5,
    min_score: float = 0.5,
    project_root: Path = _PROJECT_ROOT,
) -> dict[str, Any]:
    """Produce the candidate list for an unknown identifier `name`.

    Strategy:
      1. Exact full-name match in Mathlib → highest score.
      2. Exact last-component match in Mathlib → name_normalization candidates.
      3. Paper-theory namespace-prefix match.
      4. Fuzzy matches across the whole Mathlib index, limited to the top-K
         by token Jaccard then edit ratio."""
    entries_raw: list[dict[str, str]] = name_index.get("entries", [])
    by_last: dict[str, list[int]] = name_index.get("by_last", {})

    # Materialize on demand. We don't reconstruct dataclasses (just dicts) to
    # keep the hot path cheap.
    candidates: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    last = name.rsplit(".", 1)[-1]

    # (1) Exact full-name match.
    for idx in by_last.get(last, []):
        e = entries_raw[idx]
        if e["name"] == name:
            candidates.append({
                "target_name": e["name"],
                "score": 1.0,
                "module": e["module"],
                "kind": "exact_match",
                "rationale": "exact full-name match in Mathlib source",
            })
            seen_names.add(e["name"])

    # (2) Same last-component, different namespace.
    for idx in by_last.get(last, []):
        e = entries_raw[idx]
        if e["name"] in seen_names:
            continue
        score, kind = score_candidate(name, Entry(name=e["name"], last=e["last"],
                                                  module=e["module"], kind=e["kind"]))
        candidates.append({
            "target_name": e["name"],
            "score": round(score, 4),
            "module": e["module"],
            "kind": kind,
            "rationale": f"same final identifier '{last}' under different namespace",
        })
        seen_names.add(e["name"])

    # (3) Paper-theory namespace-prefix.
    pt = find_paper_theory_namespace_prefix(paper_id, name, project_root=project_root)
    if pt is not None and pt["target_name"] not in seen_names:
        candidates.append(pt)
        seen_names.add(pt["target_name"])

    # (4) Fuzzy global scan. Cap exploration to keep this O(N) and fast.
    # We do a token-overlap pre-filter: candidates must share at least one
    # token with the query.
    q_tokens = set(_tokenize(name))
    if q_tokens:
        # Pre-bucket entries by token presence for speed (small index, no need
        # to be too clever). For now, just iterate; the index is ~hundreds of
        # thousands of entries, this still finishes in <1s.
        for e in entries_raw:
            if e["name"] in seen_names:
                continue
            c_tokens = set(_tokenize(e["name"]))
            if not (q_tokens & c_tokens):
                continue
            score, kind = score_candidate(name, Entry(name=e["name"], last=e["last"],
                                                      module=e["module"], kind=e["kind"]))
            if score < min_score:
                continue
            candidates.append({
                "target_name": e["name"],
                "score": round(score, 4),
                "module": e["module"],
                "kind": kind,
                "rationale": f"token overlap on {sorted(q_tokens & c_tokens)}",
            })
            seen_names.add(e["name"])

    candidates.sort(key=lambda c: -float(c["score"]))
    candidates = candidates[:top_k]

    if candidates and candidates[0]["score"] >= 0.9:
        verdict = (
            "namespace_prefix" if candidates[0]["kind"] == "namespace_prefix"
            else "mathlib_match"
        )
    else:
        verdict = "no_match"

    return {
        "schema_version": _SCHEMA_VERSION,
        "paper_id": paper_id,
        "name": name,
        "verdict": verdict,
        "candidates": candidates,
    }


# ---------------------------------------------------------------------------
# Registry write-back
# ---------------------------------------------------------------------------

def register_resolution(
    *,
    result: dict[str, Any],
    alignments_path: Path = _ALIGNMENTS_PATH,
    min_score: float = 0.9,
) -> dict[str, Any]:
    """If `result`'s top candidate clears `min_score` and is a real Mathlib
    or paper-theory target, append it to alignments.json (deduped).

    The entry shape matches the existing alignments.json schema, so
    `audit_axioms.py` and `apply_reviews_to_ledger.py` pick it up for free."""
    summary = {"registered": 0, "skipped": 0, "reason": ""}
    candidates = result.get("candidates", [])
    if not candidates:
        summary["reason"] = "no_candidates"
        summary["skipped"] = 1
        return summary
    top = candidates[0]
    if float(top.get("score", 0.0)) < min_score:
        summary["reason"] = f"top_score_below_threshold ({top.get('score')})"
        summary["skipped"] = 1
        return summary

    paper_id = str(result.get("paper_id", ""))
    name = str(result.get("name", ""))
    paper_local_name = name.rsplit(".", 1)[-1]

    if alignments_path.exists():
        try:
            data = json.loads(alignments_path.read_text(encoding="utf-8"))
        except Exception:
            data = {"schema_version": "alignments.v1", "alignments": []}
    else:
        data = {"schema_version": "alignments.v1", "alignments": []}

    existing = {
        (a.get("paper_id", ""), a.get("paper_local_name", ""), a.get("mathlib_target", ""))
        for a in data.get("alignments", [])
        if isinstance(a, dict)
    }
    key = (paper_id, paper_local_name, top["target_name"])
    if key in existing:
        summary["reason"] = "already_registered"
        summary["skipped"] = 1
        return summary

    entry = {
        "paper_id": paper_id,
        "paper_local_name": paper_local_name,
        "fully_qualified": name,
        "mathlib_target": top["target_name"],
        "proof": f"Desol.PaperAlignments.{paper_local_name}_unknown_identifier_pending",
        "kind": f"auto_unknown_identifier:{top.get('kind', 'fuzzy_match')}",
        "confidence": float(top.get("score", 0.0)),
        "rationale": top.get("rationale", ""),
        "module": top.get("module", ""),
    }
    data.setdefault("alignments", []).append(entry)
    alignments_path.parent.mkdir(parents=True, exist_ok=True)
    alignments_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    summary["registered"] = 1
    summary["entry"] = entry
    return summary


# ---------------------------------------------------------------------------
# Bulk mode: scan elaboration_failure ledger rows
# ---------------------------------------------------------------------------

_UNKNOWN_RE = re.compile(
    r"unknown(?:Identifier|\s+identifier|\s+constant)[^`'\"]*[`'\"]([^`'\"]+)[`'\"]",
    re.IGNORECASE,
)


def extract_unknown_identifiers_from_error(error: str) -> list[str]:
    r"""Pull out every `unknown identifier 'X'` / `Unknown constant \`X\`` from
    a Lean error message. Returns deduplicated names in first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _UNKNOWN_RE.finditer(error):
        name = m.group(1).strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _iter_unknown_identifier_failures(
    *,
    project_root: Path = _PROJECT_ROOT,
) -> Iterable[tuple[str, str]]:
    """Yield (paper_id, unknown_name) tuples from canonical verification
    ledgers. Tries `categorize_elaboration_failures` first for shared logic;
    falls back to a direct ledger walk when that module isn't on the path
    (e.g. older worktrees)."""
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        from categorize_elaboration_failures import collect_elaboration_failures  # type: ignore
    except ImportError:
        # Inline fallback: walk verification_ledgers/<id>.json directly.
        ledger_dir = project_root / "output" / "verification_ledgers"
        if not ledger_dir.exists():
            return
        skip_suffixes = ("_smoke", "_actionable", "_repair", "_reliable",
                         "ab_repair", "_fdcheck", "_patchcheck", "_rflguard", "_fast")
        for p in sorted(ledger_dir.glob("*.json")):
            if any(s in p.stem for s in skip_suffixes):
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            entries = data if isinstance(data, list) else data.get("entries", [])
            for e in entries:
                err = str(e.get("error_message", "") or "")
                if "unknownIdentifier" not in err and "unknown identifier" not in err.lower():
                    continue
                for name in extract_unknown_identifiers_from_error(err):
                    yield p.stem, name
        return

    failures = collect_elaboration_failures(project_root=project_root)
    for f in failures:
        if f["bucket"] != "unknown_identifier":
            continue
        for name in extract_unknown_identifiers_from_error(f["error_message"]):
            yield f["paper_id"], name


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper-id", help="arxiv id, e.g. 2304.09598")
    parser.add_argument("--name", help="The unknown identifier to resolve, e.g. Matrix.dotProduct")
    parser.add_argument(
        "--scan-ledgers",
        action="store_true",
        help="Scan canonical verification ledgers for unknown_identifier failures and "
             "resolve every one of them. Overrides --paper-id/--name.",
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--min-score", type=float, default=0.5)
    parser.add_argument(
        "--register",
        action="store_true",
        help="Append the top candidate (if score >= --register-threshold) to "
             "output/corpus/alignments.json. Standards-positive: the entry uses "
             "a placeholder proof name (Desol.PaperAlignments.<n>_unknown_identifier_pending) "
             "so a human can fill in the actual rfl/heq proof later.",
    )
    parser.add_argument("--register-threshold", type=float, default=0.9)
    parser.add_argument("--rebuild-index", action="store_true",
                        help="Force a full rebuild of data/mathlib_name_index.json")
    parser.add_argument("--no-cache", action="store_true",
                        help="Don't read OR write the on-disk index cache")
    parser.add_argument("--out", type=Path, default=None,
                        help="Write the JSON result(s) to this file (default: stdout)")
    parser.add_argument("--progress", action="store_true",
                        help="Print indexing progress to stderr")
    args = parser.parse_args()

    cache_path = None if args.no_cache else _NAME_INDEX_PATH
    if args.rebuild_index and cache_path is not None:
        cache_path.unlink(missing_ok=True)
    name_index = build_name_index(
        cache_path=cache_path,
        use_cache=not args.no_cache,
        progress=args.progress,
    )
    if "error" in name_index:
        print(f"WARNING: {name_index['error']}", file=sys.stderr)

    results: list[dict[str, Any]] = []
    if args.scan_ledgers:
        seen: set[tuple[str, str]] = set()
        for paper_id, name in _iter_unknown_identifier_failures():
            if (paper_id, name) in seen:
                continue
            seen.add((paper_id, name))
            results.append(resolve_unknown_identifier(
                paper_id=paper_id,
                name=name,
                name_index=name_index,
                top_k=args.top_k,
                min_score=args.min_score,
            ))
    else:
        if not args.paper_id or not args.name:
            parser.error("either --scan-ledgers or both --paper-id and --name are required")
        results.append(resolve_unknown_identifier(
            paper_id=args.paper_id,
            name=args.name,
            name_index=name_index,
            top_k=args.top_k,
            min_score=args.min_score,
        ))

    register_summary: list[dict[str, Any]] = []
    if args.register:
        for r in results:
            register_summary.append(register_resolution(
                result=r,
                min_score=args.register_threshold,
            ))

    payload: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION + ".batch" if args.scan_ledgers else _SCHEMA_VERSION,
        "results": results,
    }
    if register_summary:
        payload["register_summary"] = register_summary
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {len(results)} resolution(s) to {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
