#!/usr/bin/env python3
"""Per-paper audited-core hint extraction.

Each canonical paper has hand-curated proofs in
`Desol/PaperProofs/Paper_<id>.lean` (and lower-confidence companions under
`Desol/PaperProofs/Auto/Paper_<id>.lean`). These are the only audited
artefacts that show, for a given paper, WHICH Mathlib lemmas the paper
actually relies on, what naming patterns it uses, and what hypothesis
shapes are idiomatic. Surfacing those proofs to Leanstral as in-context
examples removes a major source of drift between the proof-search
heuristic and the paper's idioms.

Public API:

    walk_paper_proofs_files(root: Path | None = None) -> list[Path]
        Return every `Desol/PaperProofs/Paper_*.lean` and
        `Desol/PaperProofs/Auto/Paper_*.lean` file under `root` (defaults
        to the in-tree project root).

    parse_paper_id_from_path(path: Path) -> str | None
        Convert `Paper_2604_21884.lean` -> `2604.21884`. Returns None if
        the filename does not match the expected pattern.

    extract_theorem_blocks(lean_src: str) -> list[dict]
        For each top-level `theorem NAME ... := by BODY`, return a record
        `{'name', 'block', 'tactic_count', 'has_calc', 'priority'}`. The
        block is the verbatim source text (signature + body). Block
        boundaries follow the same rules as
        `leanstral_whole_proof_generator._split_declarations`: a header
        starts at `theorem|lemma|def|abbrev|axiom NAME` and ends at the
        next such header or EOF. We retain only `theorem`/`lemma`.

    build_paper_hint(blocks: list[dict], *, max_chars: int = 4000) -> str
        Concatenate the most-curated blocks (highest priority first)
        until adding the next block would exceed `max_chars`. The first
        block is always included (truncated if necessary), so a paper
        with one giant proof still produces a non-empty hint.

    build_all_hints(*, root: Path | None = None,
                    cache_dir: Path | None = None,
                    max_chars: int = 4000) -> dict[str, str]
        Walk every paper-proofs file, group by paper_id, build a hint per
        paper, write each to `data/paper_audited_proof_hints/<paper>.txt`,
        and return the in-memory `{paper_id: hint}` dict. Files for
        papers without proofs are NOT created.

    load_hint(paper_id: str, *, cache_dir: Path | None = None) -> str
        Read the cached `data/paper_audited_proof_hints/<paper>.txt` (or
        return "" if missing). Callers in the proof-prompt path use this
        to embed the hint without rebuilding the cache.

Priority is a coarse signal: curated (non-Auto) files outrank Auto files,
and within a file we prefer proofs with `calc` chains (richer idioms)
followed by multi-tactic proofs over one-liners. This keeps the most
instructive examples up-top under the `max_chars` cap.

Standards-positive: the cached text is a HINT, never substituted into a
proof. Leanstral must still close the goal; the hint just shrinks the
search space.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

DEFAULT_PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_CACHE_DIRNAME = "paper_audited_proof_hints"
DEFAULT_MAX_CHARS = 4000

_PAPER_FILENAME_RX = re.compile(r"^Paper_(\d{4})[._](\d{4,6})\.lean$")
# Match the same top-level decl heads as the whole-proof generator. We
# capture (kind, name) so we can filter to theorem/lemma later.
_DECL_HEAD_RX = re.compile(
    r"^(?:noncomputable\s+|private\s+)?"
    r"(theorem|lemma|def|abbrev|axiom)\s+([A-Za-z_][A-Za-z0-9_'.]*)",
    re.MULTILINE,
)


# --- File discovery -------------------------------------------------------


def walk_paper_proofs_files(root: Optional[Path] = None) -> list[Path]:
    """Return every Paper_*.lean file under Desol/PaperProofs (curated and
    Auto). The list is sorted: curated first, then Auto, each section
    sorted alphabetically. Returns [] when the directory is absent."""
    base = Path(root or DEFAULT_PROJECT_ROOT) / "Desol" / "PaperProofs"
    if not base.exists():
        return []
    curated = sorted(
        p for p in base.glob("Paper_*.lean") if p.is_file()
    )
    auto_dir = base / "Auto"
    auto = sorted(
        p for p in auto_dir.glob("Paper_*.lean") if p.is_file()
    ) if auto_dir.exists() else []
    return curated + auto


def parse_paper_id_from_path(path: Path) -> Optional[str]:
    """Map `Paper_2604_21884.lean` -> `2604.21884`. Returns None on
    unexpected filename shapes."""
    m = _PAPER_FILENAME_RX.match(path.name)
    if not m:
        return None
    return f"{m.group(1)}.{m.group(2)}"


# --- Block extraction -----------------------------------------------------


def _split_declarations(lean_src: str) -> list[tuple[str, str, int, int]]:
    """Return (kind, name, start_offset, end_offset) for each top-level
    declaration. `end_offset` is the offset of the next header or EOF."""
    matches = list(_DECL_HEAD_RX.finditer(lean_src))
    out: list[tuple[str, str, int, int]] = []
    for i, m in enumerate(matches):
        kind = m.group(1)
        name = m.group(2)
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(lean_src)
        out.append((kind, name, start, end))
    return out


def _block_priority(block: str, *, source_tier: int) -> int:
    """Lower is better. `source_tier` is 0 for curated paper proofs and 1
    for Auto-generated. Within a tier we prefer (a) blocks containing a
    `calc` chain, (b) multi-tactic blocks, (c) one-liners."""
    has_calc = "calc" in block
    body = block.split(":= by", 1)[1] if ":= by" in block else ""
    tactic_count = sum(
        1
        for line in body.splitlines()
        if line.strip() and not line.lstrip().startswith("--")
    )
    # Compose a tuple-like integer key.
    # source_tier dominates; then calc-presence; then negative tactic_count
    # (more tactics first); we encode by simple weighted sum that keeps
    # ordering stable.
    return (
        source_tier * 1000
        + (0 if has_calc else 100)
        + max(0, 50 - min(tactic_count, 50))
    )


def extract_theorem_blocks(lean_src: str, *, source_tier: int = 0) -> list[dict[str, Any]]:
    """Return one record per `theorem`/`lemma` declaration in `lean_src`.

    Each record exposes:
        name          - the declared name
        block         - the verbatim block (signature + body), rstripped
        tactic_count  - heuristic count of tactic lines in the body
        has_calc      - True iff the block contains `calc`
        priority      - integer; lower = surface earlier
    """
    decls = _split_declarations(lean_src)
    records: list[dict[str, Any]] = []
    for kind, name, start, end in decls:
        if kind not in ("theorem", "lemma"):
            continue
        block = lean_src[start:end].rstrip()
        if not block:
            continue
        body_part = block.split(":= by", 1)[1] if ":= by" in block else ""
        tactic_count = sum(
            1
            for line in body_part.splitlines()
            if line.strip() and not line.lstrip().startswith("--")
        )
        records.append(
            {
                "name": name,
                "block": block,
                "tactic_count": tactic_count,
                "has_calc": "calc" in block,
                "priority": _block_priority(block, source_tier=source_tier),
            }
        )
    return records


# --- Per-paper hint construction ------------------------------------------


def build_paper_hint(
    blocks: list[dict[str, Any]],
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Concatenate blocks (sorted by priority asc, name asc as tiebreaker)
    until adding the next would push past `max_chars`. Always emits at
    least the first block (truncated when needed) so a non-empty input
    yields a non-empty hint."""
    if not blocks:
        return ""
    ordered = sorted(
        blocks,
        key=lambda r: (r.get("priority", 0), r.get("name", "")),
    )
    header = (
        "Audited proof excerpts from this paper's curated core. These show\n"
        "which Mathlib lemmas and naming conventions are idiomatic for the\n"
        "paper. They are HINTS for tactic selection; you still must close\n"
        "the goal under lake validation.\n"
    )
    pieces: list[str] = [header]
    used = len(header)
    for idx, rec in enumerate(ordered):
        block = str(rec.get("block", "")).rstrip()
        if not block:
            continue
        # Build a labelled section.
        section = f"\n-- example {idx + 1}: `{rec.get('name', '')}`\n{block}\n"
        if used + len(section) > max_chars:
            if idx == 0:
                # Ensure the first block surfaces even if oversized.
                room = max(0, max_chars - used - 32)
                if room <= 0:
                    break
                truncated = section[:room] + "\n-- ... (truncated) ...\n"
                pieces.append(truncated)
                used += len(truncated)
            break
        pieces.append(section)
        used += len(section)
    return "".join(pieces).rstrip()


# --- Bulk rebuild ---------------------------------------------------------


def _cache_dir(root: Optional[Path] = None, cache_dir: Optional[Path] = None) -> Path:
    if cache_dir is not None:
        return Path(cache_dir)
    base = Path(root or DEFAULT_PROJECT_ROOT) / "data" / DEFAULT_CACHE_DIRNAME
    return base


def build_all_hints(
    *,
    root: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict[str, str]:
    """Walk paper-proofs, group blocks by paper_id (curated tier 0, Auto
    tier 1), build per-paper hints, write to cache, return in-memory
    mapping. Writes are best-effort; OSError is propagated so callers
    notice cache failures."""
    project_root = Path(root or DEFAULT_PROJECT_ROOT)
    cache = _cache_dir(project_root, cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    files = walk_paper_proofs_files(project_root)
    by_paper: dict[str, list[dict[str, Any]]] = {}
    for path in files:
        paper_id = parse_paper_id_from_path(path)
        if not paper_id:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        # Curated = directory directly under PaperProofs, Auto = under Auto/.
        source_tier = 1 if path.parent.name == "Auto" else 0
        blocks = extract_theorem_blocks(text, source_tier=source_tier)
        if not blocks:
            continue
        by_paper.setdefault(paper_id, []).extend(blocks)
    out: dict[str, str] = {}
    for paper_id, blocks in by_paper.items():
        hint = build_paper_hint(blocks, max_chars=max_chars)
        if not hint:
            continue
        out[paper_id] = hint
        target = cache / f"{paper_id}.txt"
        target.write_text(hint, encoding="utf-8")
    return out


def load_hint(
    paper_id: str,
    *,
    root: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
) -> str:
    """Read the cached audited-core hint for `paper_id`. Returns "" when
    the cache file is absent."""
    cache = _cache_dir(root, cache_dir)
    target = cache / f"{paper_id}.txt"
    if not target.exists():
        return ""
    try:
        return target.read_text(encoding="utf-8")
    except OSError:
        return ""


# --- CLI ------------------------------------------------------------------


def _cli(argv: list[str]) -> int:  # pragma: no cover - thin wiring
    import argparse
    import json

    p = argparse.ArgumentParser(
        description="Build per-paper audited-core hint cache for Leanstral."
    )
    p.add_argument("--root", default=str(DEFAULT_PROJECT_ROOT))
    p.add_argument("--cache-dir", default="")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    p.add_argument(
        "--print-summary",
        action="store_true",
        help="Emit a JSON {paper_id: hint_char_count} summary on stdout.",
    )
    args = p.parse_args(argv)
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    hints = build_all_hints(
        root=Path(args.root),
        cache_dir=cache_dir,
        max_chars=args.max_chars,
    )
    if args.print_summary:
        summary = {pid: len(h) for pid, h in hints.items()}
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        for pid, hint in sorted(hints.items()):
            print(f"[{pid}] {len(hint)} chars -> data/{DEFAULT_CACHE_DIRNAME}/{pid}.txt")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_cli(sys.argv[1:]))
