#!/usr/bin/env python3
"""Mathlib-anchor injection helpers for the Leanstral whole-proof generator.

When a generated proof fails with `unknown identifier 'X'` or
`synthInstanceFailed: <Class> <Type>`, the retry prompt is far more useful if
it contains the names the model *should* have used. This module wires together
two pre-existing assets that the whole-proof generator was ignoring:

  A. The Mathlib name index built by `mathlib_align_unknown_identifier.py`
     (220k-entry resolver) — for `unknown identifier` and `synthInstanceFailed`
     anchors.
  B. A token-overlap premise index over Mathlib theorem signatures — for
     `premise candidates` semantically close to the goal type. We use plain
     token-overlap (Jaccard) so the path is deterministic, hermetic-testable,
     and dependency-free. The on-disk format is cached at
     `data/mathlib_premise_index.json` and is rebuilt from
     `.lake/packages/mathlib/Mathlib/**/*.lean` signatures on first use.

Public surface:

    extract_error_anchors(error_tail, name_index, top_k=5, min_score=0.5,
                          paper_id="") -> list[AnchorBlock]
    build_anchor_block(anchors) -> str
    load_or_build_premise_index(cache_path=None, mathlib_root=None) -> PremiseIndex
    PremiseIndex.query(goal_text, top_k=10) -> list[PremiseHit]
    build_premise_block(hits) -> str

All public functions are pure and side-effect-free except for the on-disk
cache write in `load_or_build_premise_index` (which only runs the first
time). Caches are hermetic-friendly: pass `cache_path=None` to skip both
read and write.

Standards-positive: every anchor is a verifiable Mathlib name with a module
path; we never invent names. Premise candidates likewise come from real
Mathlib sources (signature text scraped from `.lean` files).
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


_PROJECT_ROOT = SCRIPT_DIR.parent
_DEFAULT_PREMISE_CACHE = _PROJECT_ROOT / "data" / "mathlib_premise_index.json"


# --- Error-tail parsing ---------------------------------------------------


# `unknown identifier 'X'` — Lean emits this with single quotes, backticks
# (sometimes), or after `unknown constant`. We tolerate all three.
_UNKNOWN_IDENT_RE = re.compile(
    r"unknown(?:Identifier|\s+identifier|\s+constant)[^`'\"]*[`'\"]([A-Za-z_][\w.']*)[`'\"]",
    re.IGNORECASE,
)

# `synthInstanceFailed: <Class> ...`, `failed to synthesize instance <Class>`,
# or `failed to synthesize instance of type class\n  <Class>` — Lean emits
# several variants. We pull out the first PascalCase identifier that follows
# the marker on either the same line or the next non-empty line.
_SYNTH_FAIL_RE = re.compile(
    r"(?:synthInstanceFailed|failed to synthesize(?:\s+instance(?:\s+of\s+type\s+class)?)?)"
    r"\s*[:]?\s*\n?\s*`?([A-Z][\w.']*)",
)


@dataclass
class AnchorCandidate:
    """One Mathlib-name suggestion for an error anchor."""
    target_name: str
    module: str
    score: float
    kind: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_name": self.target_name,
            "module": self.module,
            "score": self.score,
            "kind": self.kind,
        }


@dataclass
class AnchorBlock:
    """A single error anchor: the unknown name plus top candidates."""
    name: str
    source: str  # "unknown_identifier" | "synth_instance"
    candidates: list[AnchorCandidate] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "candidates": [c.as_dict() for c in self.candidates],
        }


def _extract_unknown_identifier_names(error_tail: str) -> list[str]:
    """Return dedup'd identifier names mentioned in `unknown identifier 'X'`
    occurrences, in first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _UNKNOWN_IDENT_RE.finditer(error_tail or ""):
        nm = m.group(1).strip()
        if nm and nm not in seen:
            seen.add(nm)
            out.append(nm)
    return out


def _extract_synth_instance_classes(error_tail: str) -> list[str]:
    """Return dedup'd class names from `synthInstanceFailed: <Class>` /
    `failed to synthesize instance <Class>` markers, in first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _SYNTH_FAIL_RE.finditer(error_tail or ""):
        nm = m.group(1).strip()
        if nm and nm not in seen:
            seen.add(nm)
            out.append(nm)
    return out


def extract_error_anchors(
    *,
    error_tail: str,
    name_index: dict[str, Any],
    paper_id: str = "",
    top_k: int = 5,
    min_score: float = 0.5,
    max_anchors: int = 4,
) -> list[AnchorBlock]:
    """Extract Mathlib-name candidates for every `unknown identifier 'X'` and
    `synthInstanceFailed: <Class>` mentioned in `error_tail`.

    Returns at most `max_anchors` blocks (one per distinct name), each with up
    to `top_k` candidate names from the Mathlib name index. Returns [] when:
      - `error_tail` is empty
      - the name index is empty (e.g. mathlib unavailable)
      - no anchor patterns match
    """
    if not (error_tail or "").strip():
        return []
    if not name_index or not name_index.get("entries"):
        return []

    try:
        from mathlib_align_unknown_identifier import (  # type: ignore[import-not-found]
            resolve_unknown_identifier,
        )
    except Exception:
        return []

    blocks: list[AnchorBlock] = []
    seen_names: set[str] = set()

    for name in _extract_unknown_identifier_names(error_tail):
        if name in seen_names or len(blocks) >= max_anchors:
            continue
        seen_names.add(name)
        try:
            res = resolve_unknown_identifier(
                paper_id=paper_id or "",
                name=name,
                name_index=name_index,
                top_k=top_k,
                min_score=min_score,
            )
        except Exception:
            continue
        cands = [
            AnchorCandidate(
                target_name=str(c.get("target_name", "")),
                module=str(c.get("module", "")),
                score=float(c.get("score", 0.0)),
                kind=str(c.get("kind", "fuzzy_match")),
            )
            for c in res.get("candidates", [])
            if c.get("target_name")
        ]
        if cands:
            blocks.append(AnchorBlock(name=name, source="unknown_identifier", candidates=cands))

    for class_name in _extract_synth_instance_classes(error_tail):
        if class_name in seen_names or len(blocks) >= max_anchors:
            continue
        seen_names.add(class_name)
        try:
            res = resolve_unknown_identifier(
                paper_id=paper_id or "",
                name=class_name,
                name_index=name_index,
                top_k=top_k,
                min_score=min_score,
            )
        except Exception:
            continue
        cands = [
            AnchorCandidate(
                target_name=str(c.get("target_name", "")),
                module=str(c.get("module", "")),
                score=float(c.get("score", 0.0)),
                kind=str(c.get("kind", "fuzzy_match")),
            )
            for c in res.get("candidates", [])
            if c.get("target_name")
        ]
        if cands:
            blocks.append(AnchorBlock(name=class_name, source="synth_instance", candidates=cands))

    return blocks


def build_anchor_block(anchors: list[AnchorBlock]) -> str:
    """Render error anchors as a human-readable block for the retry prompt.

    Returns "" when `anchors` is empty so the caller can drop the block
    entirely.
    """
    if not anchors:
        return ""
    lines: list[str] = []
    for block in anchors:
        label = (
            "Mathlib name candidates for unknown identifier"
            if block.source == "unknown_identifier"
            else "Mathlib class candidates for unsatisfied instance"
        )
        lines.append(f"{label} `{block.name}`:")
        for c in block.candidates:
            module = f"  ({c.module})" if c.module else ""
            lines.append(
                f"  - {c.target_name}{module}  [score {c.score:.2f}, {c.kind}]"
            )
    lines.append(
        "Use ONE of these names if it matches your intent. Do NOT invent variants "
        "or namespace prefixes that are not in this list."
    )
    return "\n".join(lines)


# --- Premise index --------------------------------------------------------


# Match a `theorem|lemma|def|abbrev` signature line. We capture everything up
# to (but not including) the binding operator `:=` or end-of-line, then
# normalize. Used to extract signatures from Mathlib sources.
_SIG_DECL_RE = re.compile(
    r"^(?:@\[[^\]]*\]\s*)*"
    r"(?:noncomputable\s+|private\s+|protected\s+|partial\s+|nonrec\s+)*"
    r"(?P<kind>theorem|lemma|def|abbrev)\s+"
    r"(?P<name>[A-Za-z_][\w'.]*)"
    r"(?P<rest>[^\n]*)",
    re.MULTILINE,
)


# Words that show up in nearly every signature and provide no discriminative
# signal. Same set as `premise_retrieval._STOPWORDS` but tightened for
# signature tokens (we treat single-letter binders specially below).
_PREMISE_STOPWORDS: frozenset[str] = frozenset({
    "fun", "let", "have", "show", "this", "exact", "apply", "rfl", "simp",
    "intro", "intros", "true", "false", "type", "prop", "sort",
    "def", "theorem", "lemma", "instance", "class", "structure",
    "where", "with", "from", "and", "or", "not", "if", "then", "else",
    "return", "do", "pure", "bind", "the",
})
_PREMISE_MIN_TOKEN_LEN = 3


_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_TOKEN_RE = re.compile(r"[A-Za-z_][\w']*")


def tokenize_for_premise(text: str) -> set[str]:
    """Bag-of-tokens for a Lean signature / goal type.

    Splits on dots / underscores AND CamelCase boundaries, lowercases, drops
    stopwords and short tokens. Returns a SET (we use Jaccard, not TF-IDF —
    multiplicity adds noise more often than it adds signal in short
    signatures)."""
    out: set[str] = set()
    for raw in _TOKEN_RE.findall(text or ""):
        for chunk in re.split(r"[._]+", raw):
            if not chunk:
                continue
            for piece in _CAMEL_SPLIT.split(chunk):
                piece = piece.strip().lower()
                if (
                    len(piece) >= _PREMISE_MIN_TOKEN_LEN
                    and piece not in _PREMISE_STOPWORDS
                    and not piece.isdigit()
                ):
                    out.add(piece)
    return out


@dataclass
class PremiseEntry:
    name: str
    statement: str
    module: str
    tokens: list[str]  # JSON-friendly form; we re-set'ize on load


@dataclass
class PremiseHit:
    name: str
    statement: str
    module: str
    score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "statement": self.statement,
            "module": self.module,
            "score": self.score,
        }


def _strip_lean_comments(text: str) -> str:
    """Strip `--` line and `/- ... -/` block comments (handles nesting)."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if i + 1 < n and text[i] == '/' and text[i + 1] == '-':
            depth = 1
            j = i + 2
            while j < n and depth > 0:
                if j + 1 < n and text[j] == '/' and text[j + 1] == '-':
                    depth += 1
                    j += 2
                elif j + 1 < n and text[j] == '-' and text[j + 1] == '/':
                    depth -= 1
                    j += 2
                else:
                    j += 1
            out.append("\n" * text.count("\n", i, j))
            i = j
            continue
        if i + 1 < n and text[i] == '-' and text[i + 1] == '-':
            j = text.find("\n", i)
            if j < 0:
                break
            i = j
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _module_name_from_path(path: Path, mathlib_root: Path) -> str:
    """`.../Mathlib/Data/Matrix/Mul.lean` → `Mathlib.Data.Matrix.Mul`."""
    try:
        rel = path.relative_to(mathlib_root.parent)
    except ValueError:
        return ""
    return rel.with_suffix("").as_posix().replace("/", ".")


def _signature_text(rest: str, lookahead: str) -> str:
    """Stitch together the captured signature suffix and the continuation
    until we hit `:=` or `where` or a top-level newline followed by another
    declaration. We cap at 240 chars to keep the index compact."""
    # Take from `rest` up to and including the first `:= ` or `where` boundary
    # in `rest + lookahead`. Lean signatures can span lines; the lookahead is
    # the rest of the source from this point.
    combined = (rest or "") + (lookahead or "")
    # Find a top-level `:=` or `where` (best-effort; we don't track
    # parenthesis depth — false positives are tolerable since this is a
    # heuristic index).
    cut = len(combined)
    for marker in (":=", "where"):
        idx = combined.find(marker)
        if 0 <= idx < cut:
            cut = idx
    sig = combined[:cut]
    # Collapse whitespace.
    sig = re.sub(r"\s+", " ", sig).strip()
    return sig[:240]


def _extract_signatures_from_file(
    text: str, module: str
) -> list[tuple[str, str]]:
    """Yield (name, signature) pairs for every theorem/lemma/def/abbrev in
    `text`. Comments are stripped first."""
    clean = _strip_lean_comments(text)
    out: list[tuple[str, str]] = []
    for m in _SIG_DECL_RE.finditer(clean):
        name = m.group("name")
        rest = m.group("rest")
        if not name or name.startswith("_"):
            continue
        lookahead = clean[m.end(): m.end() + 600]
        sig = _signature_text(rest, lookahead)
        if not sig:
            sig = name
        out.append((name, sig))
    return out


def build_premise_index(
    *,
    mathlib_root: Path,
    cache_path: Optional[Path] = None,
    progress: bool = False,
    file_limit: Optional[int] = None,
) -> "PremiseIndex":
    """Walk `mathlib_root` and emit a premise index.

    Each entry is (name, signature, module, tokens). The token set is
    pre-computed at build time so query-time is one set-overlap per entry.

    With ~7800 mathlib files this takes a couple minutes the first time.
    The result is JSON-serialized to `cache_path` if provided.
    """
    if not mathlib_root.exists():
        return PremiseIndex(entries=[], mathlib_root=str(mathlib_root))

    files = sorted(mathlib_root.rglob("*.lean"))
    if file_limit is not None:
        files = files[:file_limit]

    entries: list[PremiseEntry] = []
    for i, path in enumerate(files):
        if progress and i % 500 == 0:
            print(f"  premise-index {i}/{len(files)}: {path.name}", file=sys.stderr)
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        module = _module_name_from_path(path, mathlib_root)
        for name, sig in _extract_signatures_from_file(text, module):
            tokens = tokenize_for_premise(name + " " + sig)
            if not tokens:
                continue
            entries.append(PremiseEntry(
                name=name,
                statement=sig,
                module=module,
                tokens=sorted(tokens),
            ))

    idx = PremiseIndex(entries=entries, mathlib_root=str(mathlib_root))
    if cache_path is not None:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps({
                    "schema_version": "mathlib_premise_index.v1",
                    "mathlib_root": str(mathlib_root),
                    "entries": [
                        {"name": e.name, "statement": e.statement,
                         "module": e.module, "tokens": e.tokens}
                        for e in entries
                    ],
                }),
                encoding="utf-8",
            )
        except Exception:
            pass
    return idx


@dataclass
class PremiseIndex:
    entries: list[PremiseEntry]
    mathlib_root: str = ""

    @classmethod
    def from_cache(cls, cache_path: Path) -> Optional["PremiseIndex"]:
        if not cache_path.exists():
            return None
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if data.get("schema_version") != "mathlib_premise_index.v1":
            return None
        entries_raw = data.get("entries", [])
        if not entries_raw:
            return None
        entries = [
            PremiseEntry(
                name=str(e.get("name", "")),
                statement=str(e.get("statement", "")),
                module=str(e.get("module", "")),
                tokens=[str(t) for t in (e.get("tokens") or [])],
            )
            for e in entries_raw
        ]
        return cls(entries=entries, mathlib_root=str(data.get("mathlib_root", "")))

    def query(self, goal_text: str, *, top_k: int = 10) -> list[PremiseHit]:
        """Token-overlap (Jaccard) ranking. Deterministic: ties broken by
        ascending name to keep output stable for tests."""
        if top_k < 1:
            return []
        q_tokens = tokenize_for_premise(goal_text)
        if not q_tokens:
            return []
        scored: list[tuple[float, int, str, PremiseEntry]] = []
        for idx, e in enumerate(self.entries):
            e_tokens = set(e.tokens)
            if not e_tokens:
                continue
            inter = q_tokens & e_tokens
            if not inter:
                continue
            union = q_tokens | e_tokens
            jaccard = len(inter) / len(union)
            # Small bonus for entries whose name's last component appears as
            # a token in the goal (typical "I want this lemma name" case).
            last = e.name.rsplit(".", 1)[-1].lower()
            last_tokens = tokenize_for_premise(last)
            if last_tokens and last_tokens & q_tokens:
                jaccard = min(1.0, jaccard + 0.05)
            scored.append((jaccard, idx, e.name, e))
        scored.sort(key=lambda t: (-t[0], t[2]))
        return [
            PremiseHit(name=e.name, statement=e.statement, module=e.module, score=round(s, 4))
            for (s, _i, _n, e) in scored[:top_k]
        ]


# Module-level cache so a single process builds at most one premise index.
_PREMISE_INDEX_CACHE: dict[str, PremiseIndex] = {}


def load_or_build_premise_index(
    *,
    cache_path: Optional[Path] = _DEFAULT_PREMISE_CACHE,
    mathlib_root: Optional[Path] = None,
    rebuild: bool = False,
    progress: bool = False,
) -> Optional[PremiseIndex]:
    """Return the premise index, building it on first call.

    Returns None if no cache exists AND `mathlib_root` is not available (e.g.
    in a CI sandbox without the lake packages). Callers should treat None as
    "premise retrieval unavailable" and skip the block.
    """
    cache_key = str(cache_path) if cache_path else "<no-cache>"
    if not rebuild and cache_key in _PREMISE_INDEX_CACHE:
        return _PREMISE_INDEX_CACHE[cache_key]

    if cache_path is not None and not rebuild:
        cached = PremiseIndex.from_cache(cache_path)
        if cached is not None and cached.entries:
            _PREMISE_INDEX_CACHE[cache_key] = cached
            return cached

    # Build path. Need a real mathlib root.
    if mathlib_root is None:
        # Try the same fallback as mathlib_align_unknown_identifier.
        try:
            from mathlib_align_unknown_identifier import _find_mathlib_root  # type: ignore[import-not-found]
            mathlib_root = _find_mathlib_root()
        except Exception:
            mathlib_root = _PROJECT_ROOT / ".lake" / "packages" / "mathlib" / "Mathlib"
    if not mathlib_root.exists():
        return None

    idx = build_premise_index(
        mathlib_root=mathlib_root,
        cache_path=cache_path,
        progress=progress,
    )
    _PREMISE_INDEX_CACHE[cache_key] = idx
    return idx if idx.entries else None


def build_premise_block(hits: list[PremiseHit], *, max_lines: int = 10) -> str:
    """Render premise hits as a prompt block. Returns "" when `hits` empty."""
    if not hits:
        return ""
    lines = ["PREMISE CANDIDATES (Mathlib lemmas with overlapping identifier tokens):"]
    for h in hits[:max_lines]:
        # Truncate signature to keep the prompt budget under control.
        sig = h.statement.strip()
        if len(sig) > 140:
            sig = sig[:137] + "..."
        module = f"  [{h.module}]" if h.module else ""
        lines.append(f"  - {h.name} : {sig}{module}")
    lines.append(
        "Prefer these names if they fit. They are real Mathlib lemmas; "
        "do not invent variants."
    )
    return "\n".join(lines)


# --- Failure-mode anchors (cluster B: real lake-error tails) --------------
#
# The A1/A3 wiring above covers `unknown identifier 'X'` and
# `synthInstanceFailed: <Class>` when X is a Mathlib name. The 5-row anchor
# smoke (commit ccb203c, output/leanstral_anchors_smoke_final.json) showed
# that the dominant non-Mathlib failure modes are:
#
#   B1. Hallucinated bound-variable names — the proof references `h4`/`h5`
#       but the theorem's binders are `(h1 h2 h3 : Prop)`. The LLM invents
#       suffixed/extended binder names that don't exist in the signature.
#   B2. Typeclass-instance gaps — `MeasurableSpace alpha` for a free
#       `alpha : Type*` declared in the theorem signature without enough
#       instance binders.
#   B3. Tactic-strategy errors — `Tactic introN failed`, type mismatch,
#       wrong unification arity.
#
# These extractors parse BOTH the error tail AND the theorem signature so
# the retry prompt can quote the *actual* binder names / type variables
# back to the model. All output is standards-positive: we never suggest
# `sorry`/`admit`/`apply?`/`axiom`/`native_decide`.


# --- B1: hallucinated bound-variable names --------------------------------


# Match binder groups in a theorem signature. We accept multiple consecutive
# names in one group, e.g. `(h1 h2 h3 : Prop)` → names h1, h2, h3 with the
# same type. Captures both `( ... )` (explicit) and `{ ... }` (implicit) and
# `[ ... ]` (instance) styles.
_BINDER_GROUP_RE = re.compile(
    r"[\(\{\[](?P<names>[A-Za-z_][\w']*(?:\s+[A-Za-z_][\w']*)*)\s*:\s*(?P<ty>[^()\{\}\[\]]+?)[\)\}\]]"
)

# Match a single anonymous-style hypothesis name: h1, h2, _1, _2, single
# letter a-z. We use this both to recognize bound-variable patterns in
# `unknown identifier 'X'` AND to decide which identifiers are likely
# binders worth quoting.
_BOUND_VAR_PATTERN = re.compile(r"^(?:h\d+|_\d+|[a-z])$")


def extract_signature_binders(lean_statement: str) -> list[tuple[str, str]]:
    """Extract (name, type) pairs from binder groups in a Lean theorem
    signature. Handles multi-name groups (`(h1 h2 h3 : Prop)` → 3 pairs)
    and the three binder styles `(...)`, `{...}`, `[...]`.

    Returns [] when no binders are found. Order is preserved; duplicates
    can occur if the signature reuses a name (we don't dedupe here)."""
    if not lean_statement:
        return []
    out: list[tuple[str, str]] = []
    # We only want the signature head, not the body. Cut at the first `:=`
    # or `\n  ` (start of tactic block) to avoid picking up binders inside
    # the proof.
    head = lean_statement
    for marker in (":=", "\nby "):
        idx = head.find(marker)
        if idx >= 0:
            head = head[:idx]
    for m in _BINDER_GROUP_RE.finditer(head):
        ty = re.sub(r"\s+", " ", m.group("ty")).strip()
        names = m.group("names").split()
        for nm in names:
            if nm and nm not in ("_",):
                out.append((nm, ty))
    return out


def detect_bound_variable_hallucination(
    *, error_tail: str, lean_statement: str
) -> Optional[dict[str, Any]]:
    """Detect the B1 pattern: `unknown identifier 'X'` where X matches a
    bound-variable shape (`h<digits>`, `_<digits>`, single lowercase letter)
    AND X is NOT among the theorem's declared binders.

    Returns a dict {hallucinated: [...], declared: [(name, ty)], ...} when
    the pattern matches, else None. The dict is fed straight into
    `build_bound_variable_anchor_block`.
    """
    names = _extract_unknown_identifier_names(error_tail or "")
    if not names:
        return None
    binders = extract_signature_binders(lean_statement or "")
    declared = {nm for (nm, _ty) in binders}
    hallucinated: list[str] = []
    seen: set[str] = set()
    for nm in names:
        if nm in seen:
            continue
        seen.add(nm)
        if _BOUND_VAR_PATTERN.match(nm) and nm not in declared:
            hallucinated.append(nm)
    if not hallucinated:
        return None
    return {
        "hallucinated": hallucinated,
        "declared": list(binders),
    }


def build_bound_variable_anchor_block(info: Optional[dict[str, Any]]) -> str:
    """Render the B1 anchor. Empty string when info is None / empty."""
    if not info or not info.get("hallucinated"):
        return ""
    bad = list(info["hallucinated"])
    declared = list(info.get("declared", []))
    lines: list[str] = ["BOUND-VARIABLE ANCHORS:"]
    quoted_bad = ", ".join(f"`{n}`" for n in bad)
    if declared:
        lines.append(
            f"Your previous attempt referenced {quoted_bad}. The theorem actually binds:"
        )
        for nm, ty in declared:
            lines.append(f"  - {nm} : {ty}")
        lines.append(
            "Use ONLY these binder names. Do NOT invent variants like "
            + ", ".join(f"`{n}`" for n in bad)
            + "."
        )
    else:
        lines.append(
            f"Your previous attempt referenced {quoted_bad}, but the theorem "
            "declares no matching binders. Inspect the signature; do not "
            "introduce names that are not bound."
        )
    return "\n".join(lines)


# --- B2: typeclass-instance gap on a free type variable -------------------


# Match a free `Type*` / `Sort*` / `Type u` binder in a theorem signature.
# We capture the binder name so the anchor can quote it back.
_TYPE_VAR_BINDER_RE = re.compile(
    r"[\(\{][^()\{\}]*?\b(?P<name>[A-Za-z_][\w']*)\s*:\s*(?:Type|Sort)\s*(?:\*|u_?\d*)?[\)\}]"
)

# Curated common Mathlib instances per class. Keyed on the class basename.
# The values are (provider_name, module). When a class is not in this map
# we just emit the generic letI hint without specific suggestions.
_CLASS_INSTANCE_HINTS: dict[str, list[tuple[str, str]]] = {
    "MeasurableSpace": [
        ("MeasurableSpace.borel", "Mathlib.MeasureTheory.MeasurableSpace.Constructions"),
        ("MeasurableSpace.top", "Mathlib.MeasureTheory.MeasurableSpace.Defs"),
    ],
    "TopologicalSpace": [
        ("instTopologicalSpaceReal", "Mathlib.Topology.Instances.Real"),
        ("TopologicalSpace.generateFrom", "Mathlib.Topology.Basic"),
    ],
    "MetricSpace": [
        ("PseudoMetricSpace.toMetricSpace", "Mathlib.Topology.MetricSpace.Basic"),
    ],
    "NormedSpace": [
        ("NormedSpace.id", "Mathlib.Analysis.NormedSpace.Basic"),
    ],
    "Inhabited": [
        ("instInhabitedNat", "Mathlib.Init.Data.Nat.Basic"),
    ],
}


def extract_signature_type_vars(lean_statement: str) -> list[str]:
    """Return the names of free `Type*`/`Sort*` binders in the signature."""
    if not lean_statement:
        return []
    head = lean_statement
    for marker in (":=", "\nby "):
        idx = head.find(marker)
        if idx >= 0:
            head = head[:idx]
    seen: set[str] = set()
    out: list[str] = []
    for m in _TYPE_VAR_BINDER_RE.finditer(head):
        nm = m.group("name")
        if nm and nm not in seen:
            seen.add(nm)
            out.append(nm)
    return out


def detect_typeclass_gap(
    *, error_tail: str, lean_statement: str
) -> Optional[dict[str, Any]]:
    """Detect the B2 pattern: a `synthInstanceFailed` / `failed to synthesize
    instance of type class` whose target type contains a free type variable
    declared in the signature without an instance binder for that class.

    Returns dict with {class_name, type_var, instance_hints} on match, else
    None. We only fire when BOTH the class AND a matching free type variable
    are present in the error tail.
    """
    if not (error_tail or "").strip():
        return None
    # Pull the class name and the surrounding context (the line with the
    # class, plus optional next line for `MeasurableSpace alpha` style).
    class_name = ""
    payload = ""
    m = re.search(
        r"(?:synthInstanceFailed|failed to synthesize(?:\s+instance(?:\s+of\s+type\s+class)?)?)"
        r"\s*[:]?\s*\n?\s*`?([A-Z][\w.']*)([^\n]*)",
        error_tail,
    )
    if not m:
        return None
    class_name = m.group(1)
    payload = (m.group(2) or "").strip()
    type_vars = extract_signature_type_vars(lean_statement or "")
    # The payload usually contains the type-var token (e.g.
    # `MeasurableSpace alpha`). Match against the signature's free vars.
    target_var: Optional[str] = None
    for tv in type_vars:
        if re.search(r"\b" + re.escape(tv) + r"\b", payload):
            target_var = tv
            break
    if target_var is None and type_vars:
        # Fall back to the first declared type-var. This still gives the
        # model a name to bind against; the hint says "or another type-var
        # in scope".
        target_var = type_vars[0]
    if target_var is None:
        return None
    hints = _CLASS_INSTANCE_HINTS.get(class_name, [])
    return {
        "class_name": class_name,
        "type_var": target_var,
        "all_type_vars": list(type_vars),
        "instance_hints": hints,
    }


def build_typeclass_gap_anchor_block(info: Optional[dict[str, Any]]) -> str:
    """Render the B2 anchor. Empty string when info is None / empty."""
    if not info or not info.get("class_name") or not info.get("type_var"):
        return ""
    cls = info["class_name"]
    var = info["type_var"]
    hints = info.get("instance_hints") or []
    lines: list[str] = [
        f"TYPECLASS GAP for `{cls} {var}`:",
        f"`{var}` is declared as `Type*` (or similar) without a `{cls}` "
        "instance binder.",
        f"To fix the proof, either add the instance binder to the theorem "
        f"signature (e.g. `[{cls} {var}]`) OR discharge it inline via an "
        f"explicit `letI : {cls} {var} := ...` line before the tactic that "
        "triggered the failure.",
    ]
    if hints:
        lines.append(f"Common Mathlib instances for `{cls}`:")
        for nm, module in hints:
            lines.append(f"  - {nm}  ({module})")
    lines.append(
        "Do NOT invent a new instance; pick one of the listed providers or "
        "an analogous Mathlib instance for the concrete type."
    )
    return "\n".join(lines)


# --- B3: tactic-strategy errors -------------------------------------------


# Detect well-known tactic failure shapes in the error tail. We keep the
# pattern set short — each one maps to a concrete prompt hint.
_TACTIC_INTRON_RE = re.compile(r"[Tt]actic\s+(?:'?introN'?|`introN`)\s+failed", re.IGNORECASE)
_TYPE_MISMATCH_RE = re.compile(
    r"(?:type mismatch|expected to have type)\s*\n?\s*(?P<expected>[^\n]*)",
    re.IGNORECASE,
)
_APPLICATION_FAILED_RE = re.compile(r"application type mismatch|function expected", re.IGNORECASE)
_UNIFICATION_FAILED_RE = re.compile(r"unification failed|tactic 'rfl' failed", re.IGNORECASE)


def detect_tactic_strategy_error(*, error_tail: str) -> Optional[dict[str, Any]]:
    """Detect B3 patterns. Returns a dict {kind, extras} naming the family of
    failure, else None.

    Kinds:
      - introN_failed: too many `intro`s for the goal arity.
      - type_mismatch: expected/got types disagree.
      - application_failed: function expected a different number of args.
      - unification_failed: rfl / unification cannot close the goal.
    """
    tail = error_tail or ""
    if not tail.strip():
        return None
    if _TACTIC_INTRON_RE.search(tail):
        return {"kind": "introN_failed", "extras": {}}
    # `application type mismatch` must be checked BEFORE the bare
    # `type mismatch` pattern so we route it to the application-failure
    # bucket instead of the generic type-mismatch hint.
    if _APPLICATION_FAILED_RE.search(tail):
        return {"kind": "application_failed", "extras": {}}
    m_tm = _TYPE_MISMATCH_RE.search(tail)
    if m_tm:
        return {
            "kind": "type_mismatch",
            "extras": {"expected": (m_tm.group("expected") or "").strip()[:200]},
        }
    if _UNIFICATION_FAILED_RE.search(tail):
        return {"kind": "unification_failed", "extras": {}}
    return None


def build_tactic_strategy_anchor_block(info: Optional[dict[str, Any]]) -> str:
    """Render the B3 anchor. Empty string when info is None / empty."""
    if not info or not info.get("kind"):
        return ""
    kind = info["kind"]
    extras = info.get("extras") or {}
    lines: list[str] = ["TACTIC-STRATEGY ANCHOR:"]
    if kind == "introN_failed":
        lines.append(
            "Lean reported `Tactic introN failed` — the goal accepts fewer "
            "intros than you tried. Use `intro h` (single intro, no count) "
            "followed by inspection, or `obtain ⟨...⟩ := h` for "
            "destructuring an existential / conjunction. Do NOT chain "
            "multiple `intro`s blindly."
        )
    elif kind == "type_mismatch":
        expected = str(extras.get("expected", "") or "").strip()
        if expected:
            lines.append(
                f"Lean reported a type mismatch (expected: {expected!r}). "
                "Insert `show <expected-type>` before the offending tactic "
                "to assert the goal shape, or `change <expected-type>` to "
                "rewrite up to definitional equality."
            )
        else:
            lines.append(
                "Lean reported a type mismatch. Insert `show <type>` to "
                "assert the expected goal form, or use `change` to rewrite "
                "up to definitional equality."
            )
    elif kind == "application_failed":
        lines.append(
            "Lean reported an application failure (wrong arity / function "
            "expected). Use `apply <lemma>` followed by the explicit "
            "arguments, or `refine <lemma> ?_ ?_` to leave the unsolved "
            "subgoals for the next tactic. Do NOT call the lemma directly "
            "with the wrong number of arguments."
        )
    elif kind == "unification_failed":
        lines.append(
            "Lean reported unification failure (often `rfl` against a goal "
            "that needs rewriting). Replace `rfl` with `simp` / `ring` / "
            "`norm_num` / explicit `rw [lemma]` to first normalize, then "
            "close with `rfl`."
        )
    else:
        return ""
    return "\n".join(lines)


# --- Convenience: full anchor section -------------------------------------


def build_anchor_section(
    *,
    error_tail: str,
    goal_text: str,
    name_index: dict[str, Any],
    premise_index: Optional[PremiseIndex],
    paper_id: str = "",
    anchor_top_k: int = 5,
    premise_top_k: int = 10,
    lean_statement: str = "",
) -> str:
    """Convenience wrapper used by the generator. Returns "" when nothing to
    inject. The output is appended to the user prompt (above the retry
    block).

    `lean_statement` (optional) is the full theorem signature. When supplied
    alongside an `error_tail`, the failure-mode anchors (B1 bound-variable,
    B2 typeclass-gap, B3 tactic-strategy) are appended after the Mathlib
    name / premise blocks. Passing "" preserves the pre-cluster-B output
    shape for the existing hermetic tests.
    """
    parts: list[str] = []

    if error_tail:
        anchors = extract_error_anchors(
            error_tail=error_tail,
            name_index=name_index,
            paper_id=paper_id,
            top_k=anchor_top_k,
        )
        block = build_anchor_block(anchors)
        if block:
            parts.append(block)

    if premise_index is not None and goal_text:
        hits = premise_index.query(goal_text, top_k=premise_top_k)
        block = build_premise_block(hits, max_lines=premise_top_k)
        if block:
            parts.append(block)

    if error_tail:
        bv_info = detect_bound_variable_hallucination(
            error_tail=error_tail, lean_statement=lean_statement or goal_text,
        )
        bv_block = build_bound_variable_anchor_block(bv_info)
        if bv_block:
            parts.append(bv_block)
        tc_info = detect_typeclass_gap(
            error_tail=error_tail, lean_statement=lean_statement or goal_text,
        )
        tc_block = build_typeclass_gap_anchor_block(tc_info)
        if tc_block:
            parts.append(tc_block)
        ts_info = detect_tactic_strategy_error(error_tail=error_tail)
        ts_block = build_tactic_strategy_anchor_block(ts_info)
        if ts_block:
            parts.append(ts_block)

    return "\n\n".join(parts)


# --- CLI smoke ------------------------------------------------------------


def _smoke_main(argv: list[str]) -> int:  # pragma: no cover - smoke wiring
    import argparse
    p = argparse.ArgumentParser(description="Smoke-build the premise index.")
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--progress", action="store_true")
    p.add_argument("--file-limit", type=int, default=None)
    p.add_argument("--query", default="")
    p.add_argument("--top-k", type=int, default=10)
    args = p.parse_args(argv)

    if args.file_limit is not None:
        # Build a partial index for smoke-testing without touching the canonical cache.
        from mathlib_align_unknown_identifier import _find_mathlib_root  # type: ignore[import-not-found]
        idx = build_premise_index(
            mathlib_root=_find_mathlib_root(),
            cache_path=None,
            progress=args.progress,
            file_limit=args.file_limit,
        )
    else:
        idx = load_or_build_premise_index(rebuild=args.rebuild, progress=args.progress)

    if idx is None:
        print("[premise_index] mathlib unavailable; no index built", file=sys.stderr)
        return 2
    print(f"[premise_index] {len(idx.entries)} entries indexed", file=sys.stderr)

    if args.query:
        hits = idx.query(args.query, top_k=args.top_k)
        for h in hits:
            print(f"{h.score:.3f}  {h.name}  [{h.module}]")
            print(f"      {h.statement}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_smoke_main(sys.argv[1:]))
