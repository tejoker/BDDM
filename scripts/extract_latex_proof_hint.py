#!/usr/bin/env python3
"""Translator-emitted proof hints from LaTeX.

The LaTeX source of each canonical paper includes proof bodies, but the
translator currently drops them. The proofs contain rich structural
signals — phrases like "applies Cauchy-Schwarz", "by Markov's inequality",
"integrate by parts" — which map almost directly to Lean tactics or
Mathlib lemmas. Surfacing these to Leanstral as in-context hints removes
guesswork about which closure tactic to attempt first.

This module performs DETERMINISTIC, structural extraction only. It does
NOT call an LLM, does NOT attempt mathematical interpretation, and does
NOT translate any expression into Lean. Outputs are bullet hints; the
proof-prompt path embeds them under a "LaTeX proof structure" header.

Public API:

    walk_extracted_theorems_files(root: Path | None = None) -> list[Path]
        Return every `reproducibility/*/<paper_id>/extracted_theorems.json`
        path under the project root. The pipeline currently writes these
        under `reproducibility/paper_agnostic_golden10_results/`, but
        we search both that directory and `reproducibility/full_paper_reports/`
        for forward compatibility.

    extract_hints_from_proof(proof: str) -> list[str]
        Deterministic structural extraction over a single LaTeX proof
        body. Returns ordered bullet strings such as:
            "applies Cauchy-Schwarz"
            "uses Markov's inequality"
            "concludes by integration by parts"
            "first proves <ref:lem:speed-gap>, then <ref:eq:HN-moment>"
        Returns [] for an empty or whitespace-only proof.

    build_row_records(entries: list[dict], *, paper_id: str) -> list[dict]
        For each entry with a non-empty proof, return a record:
            {"paper_id", "theorem_name", "label",
             "hints": [...], "raw_proof_len": int}
        Entries without a proof body are SKIPPED (no empty rows).

    write_hints_jsonl(records: list[dict], output_path: Path,
                      *, append: bool = False) -> int
        Write JSONL rows keyed by (paper_id, theorem_name). Returns the
        number of rows written.

    build_all_hints(*, root: Path | None = None,
                    output_path: Path | None = None) -> dict[tuple, list[str]]
        Walk all extracted_theorems.json files, build per-row hints,
        write to `output/corpus/latex_proof_hints.jsonl`, return the
        in-memory `{(paper_id, theorem_name): hints}` mapping.

    load_hints(*, output_path: Path | None = None) -> dict[tuple, list[str]]
        Read the cached JSONL. Used by the prompt-builder path.

    format_hint_block(hints: list[str]) -> str
        Render a list of hints into the prompt-ready block:

            LaTeX proof structure (from the paper):
              - applies Cauchy-Schwarz
              - uses triangle inequality
              - concludes by linarith

        Returns "" when `hints` is empty.

Standards-positive: hints are HINTS, not proofs. The whole-proof
generator still must close the goal under lake validation.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

DEFAULT_PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_OUTPUT_REL = Path("output") / "corpus" / "latex_proof_hints.jsonl"
DEFAULT_REPORT_DIRS = (
    "paper_agnostic_golden10_results",
    "full_paper_reports",
)


# --- Vocabulary ----------------------------------------------------------


# Each entry maps a canonical hint label to a list of regex patterns. We
# walk the patterns in order; the first match wins. Patterns are matched
# case-insensitively against the proof body. Keep the labels short and
# Lean-suggestive so Leanstral can pick a tactic directly.
_NAMED_INEQUALITIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Cauchy-Schwarz", (r"\bcauchy[ -]?schwarz\b",)),
    ("triangle inequality", (r"\btriangle inequality\b",)),
    ("Holder's inequality", (r"\bh[oö]lder(?:'s)? inequality\b",)),
    ("Minkowski's inequality", (r"\bminkowski(?:'s)? inequality\b",)),
    ("Young's inequality", (r"\byoung'?s inequality\b",)),
    ("Jensen's inequality", (r"\bjensen'?s inequality\b",)),
    ("Markov's inequality", (r"\bmarkov'?s inequality\b",)),
    ("Chebyshev's inequality", (r"\bchebyshev'?s inequality\b",)),
    ("Borel-Cantelli", (r"\bborel[-\s]?cantelli\b",)),
    ("Kolmogorov continuity", (r"\bkolmogorov(?:[' ]s)? (?:continuity|inequality|criterion)\b",)),
    ("Fubini's theorem", (r"\bfubini(?:'s)?\b",)),
    ("dominated convergence", (r"\bdominated convergence\b",)),
    ("monotone convergence", (r"\bmonotone convergence\b",)),
    ("Sobolev embedding", (r"\bsobolev embedding\b",)),
    ("hypercontractivity", (r"\bhypercontractivity\b",)),
    ("Wick's theorem", (r"\bwick(?:'s)?\b",)),
    ("Plancherel's theorem", (r"\bplancherel(?:'s)?\b",)),
    ("Parseval's identity", (r"\bparseval(?:'s)?\b",)),
    ("mean value theorem", (r"\bmean value theorem\b",)),
    ("Taylor's theorem", (r"\btaylor(?:'s)? (?:theorem|expansion|formula)\b",)),
    ("Lebesgue differentiation", (r"\blebesgue differentiation\b",)),
)


_TACTIC_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("linarith", (r"\b(?:linearity|linear combination|linear(?:ly)? combine[sd]?)\b",)),
    ("linarith / nlinarith",
     (r"\b(?:absolutely summable|standard estimate|elementary estimate)\b",)),
    ("integration by parts",
     (r"\bintegrat(?:e|ion) by parts\b", r"\bintegrate by parts\b")),
    ("induction", (r"\bby induction\b", r"\binduction on\b")),
    ("contradiction", (r"\b(?:by|reach a) contradiction\b", r"\bsuppose (?:for contradiction|otherwise)\b")),
    ("case analysis", (r"\bcase (?:analysis|distinction)\b", r"\bsplit into cases\b")),
    ("substitution", (r"\bsubstitut(?:e|ing|ion)\b",)),
    ("change of variables", (r"\bchange of variables?\b",)),
    ("ring / field_simp", (r"\b(?:expand|rearrange|simplify|rewrite)\b",)),
    ("norm_num / nlinarith", (r"\b(?:by direct computation|direct computation)\b",)),
    ("polyrith / nlinarith",
     (r"\b(?:polynomial(?:ly)? bounded|polynomial identity)\b",)),
)


# Trigger phrases for the "applies / uses / by" extraction. Each phrase
# starts a structural clause; we collect the next short noun-phrase
# (capped at ~6 words) as the hint payload.
_TRIGGER_RX = re.compile(
    r"\b(applies|apply|applying|uses|using|use|by the|by an?|via|invoking|invoke)\s+"
    r"([A-Z][\w\-']*(?:\s+[\w\-']+){0,5})",
    re.IGNORECASE,
)

# `\applies{X}` / `\apply{X}` — explicit translator macros if any paper
# uses them. These dominate the regex search and produce direct hints.
_MACRO_RX = re.compile(
    r"\\(applies|apply|uses|invokes|by)\s*\{([^{}]{1,200})\}",
)

# Step structure: "first proves ... then ...", "first ... then ...", or
# enumerated proof skeletons.
_STEP_FIRST_THEN_RX = re.compile(
    r"\bfirst[, ]+(?:proves?|shows?|establishes?|notes?)\s+(.{1,140}?)\s+\bthen\s+(.{1,140})",
    re.IGNORECASE | re.DOTALL,
)

# Conclusion markers — e.g. "concludes by Markov's inequality".
_CONCLUDE_RX = re.compile(
    r"\b(conclud(?:es?|ing)|finish(?:es)? off|finally|the conclusion follows)\b"
    r"(?:\s+(?:by|via|using|with))?\s*([^.\n]{0,140})",
    re.IGNORECASE,
)

# LaTeX label references e.g. \ref{lem:speed-gap}, \eqref{eq:foo}. We
# surface them so the LLM knows which earlier statement the paper relies
# on.
_LABEL_REF_RX = re.compile(r"\\(?:ref|eqref|cref|autoref)\s*\{([^{}]+)\}")


# --- File discovery -------------------------------------------------------


def walk_extracted_theorems_files(root: Optional[Path] = None) -> list[Path]:
    """Return every `extracted_theorems.json` under `reproducibility/`.

    Searches both `paper_agnostic_golden10_results/` and
    `full_paper_reports/`. Returns files sorted alphabetically. Returns
    [] when no candidate directory exists.
    """
    base = Path(root or DEFAULT_PROJECT_ROOT) / "reproducibility"
    if not base.exists():
        return []
    paths: list[Path] = []
    for sub in DEFAULT_REPORT_DIRS:
        d = base / sub
        if not d.exists():
            continue
        for f in d.glob("*/extracted_theorems.json"):
            if f.is_file():
                paths.append(f)
    # Deduplicate by absolute path; sort.
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in sorted(paths):
        if p in seen:
            continue
        seen.add(p)
        unique.append(p)
    return unique


# --- Structural extraction -----------------------------------------------


def _normalize_label_payload(text: str) -> str:
    """Trim a candidate hint payload (a few words after a trigger).

    Collapse whitespace, drop trailing punctuation, keep at most ~8 words
    or 60 chars."""
    s = re.sub(r"\s+", " ", text or "").strip()
    s = re.sub(r"[.,;:\\]+$", "", s).strip()
    if not s:
        return ""
    words = s.split(" ")
    if len(words) > 8:
        words = words[:8]
    s = " ".join(words)
    if len(s) > 60:
        s = s[:60].rstrip() + "..."
    return s


def _scan_named_table(
    proof: str, table: tuple[tuple[str, tuple[str, ...]], ...]
) -> list[str]:
    found: list[str] = []
    lowered = proof.lower()
    for label, patterns in table:
        for pat in patterns:
            if re.search(pat, lowered):
                found.append(label)
                break
    return found


def extract_hints_from_proof(proof: str) -> list[str]:
    """Run the deterministic structural parser. Returns a list of bullet
    strings; preserves first-seen order; deduplicates."""
    body = (proof or "").strip()
    if not body:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def _push(line: str) -> None:
        line = line.strip()
        if not line:
            return
        # Normalize whitespace.
        line = re.sub(r"\s+", " ", line)
        if line in seen:
            return
        seen.add(line)
        out.append(line)

    # 1. Explicit translator macros.
    for m in _MACRO_RX.finditer(body):
        verb = m.group(1).lower()
        payload = _normalize_label_payload(m.group(2))
        if not payload:
            continue
        if verb == "by":
            _push(f"concludes by {payload}")
        else:
            _push(f"{verb} {payload}")

    # 2. Named inequalities / theorems.
    for label in _scan_named_table(body, _NAMED_INEQUALITIES):
        _push(f"applies {label}")

    # 3. Tactic-keyword phrases.
    for label in _scan_named_table(body, _TACTIC_KEYWORDS):
        _push(f"uses {label}")

    # 4. Trigger-phrase scan. We exclude payloads that start with a label
    #    keyword like "Lemma", "Theorem", "Proposition" — those are
    #    handled by the label-ref scan below and otherwise produce noisy
    #    duplicates.
    _NOISY_PAYLOAD_HEADS = {"lemma", "theorem", "proposition", "corollary", "remark"}
    for m in _TRIGGER_RX.finditer(body):
        verb = m.group(1).lower().rstrip()
        payload = _normalize_label_payload(m.group(2))
        if not payload:
            continue
        first_word = payload.split(" ", 1)[0].lower().rstrip("s,.")
        if first_word in _NOISY_PAYLOAD_HEADS:
            continue
        # Skip if the payload exactly matches an already-emitted label.
        if any(payload.lower() in s.lower() for s in out):
            continue
        verb_canonical = "applies" if verb.startswith("appl") else (
            "uses" if verb.startswith("us") else "by"
        )
        if verb_canonical == "by":
            _push(f"by {payload}")
        else:
            _push(f"{verb_canonical} {payload}")

    # 5. Step structure: "first ... then ...".
    m = _STEP_FIRST_THEN_RX.search(body)
    if m:
        step_a = _normalize_label_payload(m.group(1))
        step_b = _normalize_label_payload(m.group(2))
        if step_a and step_b:
            _push(f"first proves {step_a}, then {step_b}")

    # 6. Conclusion markers.
    for m in _CONCLUDE_RX.finditer(body):
        payload = _normalize_label_payload(m.group(2) or "")
        if payload:
            _push(f"concludes by {payload}")

    # 7. Label references (lemma/equation citations).
    refs: list[str] = []
    for m in _LABEL_REF_RX.finditer(body):
        ref = m.group(1).strip()
        if not ref:
            continue
        if ref in refs:
            continue
        refs.append(ref)
    if refs:
        # Cap at 6 to avoid swamping the hint block.
        capped = refs[:6]
        _push("relies on " + ", ".join(f"<ref:{r}>" for r in capped))

    return out


# --- Record assembly -----------------------------------------------------


def _theorem_short_name(entry: dict[str, Any]) -> str:
    """Best-effort name. Falls back to label / kind+index. The translator
    elsewhere uses entry['name']; we mirror that."""
    name = str(entry.get("name", "") or "").strip()
    if name:
        return name
    label = str(entry.get("label", "") or "").strip()
    if label:
        return label
    return str(entry.get("kind", "") or "thm").strip()


def build_row_records(
    entries: list[dict[str, Any]], *, paper_id: str
) -> list[dict[str, Any]]:
    """For each entry with a non-empty proof body, build a record.

    Entries with empty / whitespace-only proofs are skipped; entries
    whose parser yields zero hints ARE emitted with `hints=[]` so the
    cache reflects "we looked and found nothing structural" rather than
    "we never checked".
    """
    records: list[dict[str, Any]] = []
    for entry in entries or []:
        proof = str(entry.get("proof", "") or "")
        if not proof.strip():
            continue
        name = _theorem_short_name(entry)
        hints = extract_hints_from_proof(proof)
        records.append(
            {
                "paper_id": paper_id,
                "theorem_name": name,
                "label": str(entry.get("label", "") or ""),
                "hints": hints,
                "raw_proof_len": len(proof),
            }
        )
    return records


def write_hints_jsonl(
    records: list[dict[str, Any]],
    output_path: Path,
    *,
    append: bool = False,
) -> int:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    written = 0
    with output_path.open(mode, encoding="utf-8") as fh:
        for rec in records:
            row = dict(rec)
            row["row_id"] = f"{rec.get('paper_id', '')}::{rec.get('theorem_name', '')}"
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            written += 1
    return written


def build_all_hints(
    *,
    root: Optional[Path] = None,
    output_path: Optional[Path] = None,
) -> dict[tuple[str, str], list[str]]:
    """Walk every `extracted_theorems.json` and write the consolidated
    JSONL. Returns `{(paper_id, theorem_name): hints}`."""
    project_root = Path(root or DEFAULT_PROJECT_ROOT)
    target = Path(output_path) if output_path else project_root / DEFAULT_OUTPUT_REL
    all_records: list[dict[str, Any]] = []
    for path in walk_extracted_theorems_files(project_root):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        paper_id = str(data.get("paper_id", "") or "").strip()
        if not paper_id:
            # Fall back to the parent directory name.
            paper_id = path.parent.name
        entries = data.get("entries") or []
        if not isinstance(entries, list):
            continue
        records = build_row_records(entries, paper_id=paper_id)
        all_records.extend(records)
    # Single write, overwriting. Callers wanting incremental builds can
    # invoke `write_hints_jsonl` directly with append=True.
    write_hints_jsonl(all_records, target, append=False)
    mapping: dict[tuple[str, str], list[str]] = {}
    for rec in all_records:
        key = (str(rec.get("paper_id", "")), str(rec.get("theorem_name", "")))
        mapping[key] = list(rec.get("hints", []))
    return mapping


def load_hints(
    *,
    output_path: Optional[Path] = None,
    root: Optional[Path] = None,
) -> dict[tuple[str, str], list[str]]:
    """Read the cached JSONL. Returns {} when missing or corrupt."""
    project_root = Path(root or DEFAULT_PROJECT_ROOT)
    target = Path(output_path) if output_path else project_root / DEFAULT_OUTPUT_REL
    if not target.exists():
        return {}
    out: dict[tuple[str, str], list[str]] = {}
    with target.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            key = (str(row.get("paper_id", "")), str(row.get("theorem_name", "")))
            hints = row.get("hints") or []
            if isinstance(hints, list):
                out[key] = [str(h) for h in hints if str(h).strip()]
    return out


def format_hint_block(hints: list[str]) -> str:
    """Render hints into the prompt-ready block. Returns "" when empty.

    Output shape:

        LaTeX proof structure (from the paper):
          - applies Cauchy-Schwarz
          - uses triangle inequality
          - concludes by linarith
    """
    if not hints:
        return ""
    lines = ["LaTeX proof structure (from the paper):"]
    for h in hints:
        h = (h or "").strip()
        if not h:
            continue
        lines.append(f"  - {h}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


# --- CLI -----------------------------------------------------------------


def _cli(argv: list[str]) -> int:  # pragma: no cover - thin wiring
    import argparse

    p = argparse.ArgumentParser(
        description="Extract LaTeX-proof structural hints into output/corpus/latex_proof_hints.jsonl"
    )
    p.add_argument("--root", default=str(DEFAULT_PROJECT_ROOT))
    p.add_argument("--output", default="")
    p.add_argument(
        "--print-summary",
        action="store_true",
        help="Emit a JSON {paper::theorem: hint_count} summary on stdout.",
    )
    args = p.parse_args(argv)
    output_path = Path(args.output) if args.output else None
    mapping = build_all_hints(
        root=Path(args.root),
        output_path=output_path,
    )
    if args.print_summary:
        summary = {f"{pid}::{name}": len(h) for (pid, name), h in mapping.items()}
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"rows={len(mapping)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_cli(sys.argv[1:]))
