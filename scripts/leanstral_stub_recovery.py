#!/usr/bin/env python3
"""Re-translate placeholder theorems via Leanstral.

Some translator runs emit placeholder bodies — `theorem foo : False := by sorry`
or `theorem foo : True := trivial` — when the validation acceptance gate
rejects the original Lean signature for reasons like `claim_shape_mismatch`,
`lean_elaboration_failed`, or `false_target_without_source_contradiction`.

The placeholder rows pollute the UNRESOLVED count and block downstream review.
This tool walks `output/<paper_id>.lean`, identifies each placeholder, reads
the corresponding source LaTeX from `reproducibility/.../extracted_theorems.json`
(or the `output/paper_sources/.../*.tex` file referenced by the source span),
calls `translator.translate_statement()` with explicit context about the
previous failure mode, and replaces the placeholder if the new translation
passes validation.

Pipeline policy: only Leanstral is allowed. The `--model` default is
`labs-leanstral-2603` and an explicit override prevents accidental use of
heavier Mistral models.

Dry-run by default. Pass `--write` to mutate the .lean file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Placeholder detection
# ---------------------------------------------------------------------------

# Match a theorem block:
#   -- [theorem] <label>
#   -- Statement (LaTeX): <text>
#   -- Translation: BLOCKED — <reason>            (zero or more lines)
#   -- ... (other comment lines)
#   theorem <name> : False := by sorry            (or :True := trivial / :=  by sorry)
_THEOREM_HEADER_RE = re.compile(r"^--\s*\[theorem\]\s+(?P<label>\S+)\s*$")
_LATEX_LINE_RE = re.compile(r"^--\s*Statement \(LaTeX\):\s*(?P<latex>.*)$")
_BLOCKED_RE = re.compile(r"^--\s*Translation:\s*BLOCKED\s*—\s*(?P<reason>.+)$")
_PLACEHOLDER_RE = re.compile(
    r"^theorem\s+(?P<name>[A-Za-z_][A-Za-z0-9_'.]*)\s*:\s*"
    r"(?P<placeholder>False|True)\s*:=\s*by\s+sorry\b",
)
_PLACEHOLDER_TRIVIAL_RE = re.compile(
    r"^theorem\s+(?P<name>[A-Za-z_][A-Za-z0-9_'.]*)\s*:\s*True\s*:=\s*trivial\b",
)


@dataclass
class PlaceholderBlock:
    label: str
    name: str
    block_start_line: int        # 1-based, line of the `-- [theorem]` header
    theorem_line: int            # 1-based, line of `theorem foo : False := by sorry`
    truncated_latex: str         # what's in the comment header (often truncated)
    failure_reason: str          # the BLOCKED — <reason> string (may be empty)
    full_text: str = ""          # full original block text (for replacement)
    full_latex: str = ""         # full LaTeX from extracted_theorems (filled in)


def detect_placeholders(lean_text: str) -> list[PlaceholderBlock]:
    """Walk the .lean file and return one PlaceholderBlock per stub theorem.

    A "block" begins at `-- [theorem] <label>` and ends at the matching
    placeholder theorem line (or fizzles if no placeholder follows)."""
    lines = lean_text.split("\n")
    blocks: list[PlaceholderBlock] = []
    i = 0
    while i < len(lines):
        m = _THEOREM_HEADER_RE.match(lines[i])
        if not m:
            i += 1
            continue
        block_start = i + 1  # 1-based
        label = m.group("label")
        truncated_latex = ""
        failure_reason = ""
        j = i + 1
        # Walk forward through comment lines until we hit a non-comment line.
        while j < len(lines):
            line = lines[j]
            if not line.strip().startswith("--"):
                break
            lm = _LATEX_LINE_RE.match(line)
            if lm:
                truncated_latex = lm.group("latex").strip()
            bm = _BLOCKED_RE.match(line)
            if bm:
                failure_reason = bm.group("reason").strip()
            j += 1
        # Now check if the next non-comment line is a placeholder theorem.
        if j < len(lines):
            tline = lines[j].strip()
            pm = _PLACEHOLDER_RE.match(tline) or _PLACEHOLDER_TRIVIAL_RE.match(tline)
            if pm:
                blocks.append(PlaceholderBlock(
                    label=label,
                    name=pm.group("name"),
                    block_start_line=block_start,
                    theorem_line=j + 1,
                    truncated_latex=truncated_latex,
                    failure_reason=failure_reason,
                    full_text="\n".join(lines[i : j + 1]),
                ))
                i = j + 1
                continue
        i = j


    return blocks


# ---------------------------------------------------------------------------
# Source LaTeX retrieval
# ---------------------------------------------------------------------------

_EXTRACTED_THEOREMS_CANDIDATES = (
    Path("reproducibility/paper_agnostic_golden10_results/{paper}/extracted_theorems.json"),
    Path("reproducibility/full_paper_reports/{paper}/extracted_theorems.json"),
    Path("output/paper_extractions/{paper}/extracted_theorems.json"),
)


def find_source_latex(paper_id: str, label: str, project_root: Path) -> tuple[str, dict[str, Any]]:
    """Look up the full source_latex (and span metadata) for a theorem label.

    Returns ("", {}) when not found."""
    for tmpl in _EXTRACTED_THEOREMS_CANDIDATES:
        path = project_root / Path(str(tmpl).replace("{paper}", paper_id))
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        entries = data.get("entries", []) if isinstance(data, dict) else data if isinstance(data, list) else []
        for e in entries:
            if not isinstance(e, dict):
                continue
            if e.get("label") == label or e.get("name") == label:
                return str(e.get("statement", "") or ""), {
                    "source_file": str(e.get("source_file", "") or ""),
                    "source_span": e.get("source_span", {}) or {},
                    "source_span_id": str(e.get("source_span_id", "") or ""),
                    "kind": str(e.get("env_name", "") or e.get("kind", "")),
                }
    return "", {}


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------

@dataclass
class RecoveryResult:
    label: str
    name: str
    failure_reason: str
    full_latex_chars: int
    new_signature: str = ""
    new_signature_validates: bool = False
    notes: list[str] = field(default_factory=list)


def _build_recovery_hint(failure_reason: str) -> str:
    """Convert a BLOCKED-reason into a guidance string for the translator.

    The hint is appended to the translator's standard prompt so the new
    attempt can avoid the same failure mode."""
    if not failure_reason:
        return ""
    fr = failure_reason.lower()
    hints: list[str] = []
    if "claim_shape_mismatch:eq->ineq" in fr or "claim_shape_mismatch:ineq->eq" in fr:
        hints.append(
            "Previous attempt had a claim-shape mismatch (eq vs ineq). "
            "Read the LaTeX carefully: produce an equation if the source asserts "
            "equality, an inequality if the source asserts ordering. Do not "
            "swap the relation."
        )
    if "lean_elaboration_failed" in fr:
        hints.append(
            "Previous attempt failed Lean elaboration. Use only Mathlib-resolvable "
            "identifiers; if the paper introduces local notation, prefer "
            "abstract typeclass-level statements over hard-coded named symbols."
        )
    if "false_target_without_source_contradiction" in fr:
        hints.append(
            "Previous attempt produced a False-targeted statement that does not "
            "express the paper claim. Translate the actual mathematical content; "
            "do not emit `False` as the conclusion."
        )
    if "trivial_exists_self_equality_target" in fr:
        hints.append(
            "Previous attempt produced `∃ x, x = x` or similar trivially-true "
            "scaffold. Read the LaTeX and translate the real existential claim."
        )
    if "raw_latex_leak" in fr or "raw_notation_leak" in fr:
        hints.append(
            "Previous attempt left raw LaTeX or unresolved notation in the Lean "
            "signature. Replace all $\\foo$ commands with their Lean equivalents "
            "or abstract them behind hypotheses."
        )
    if not hints:
        hints.append(f"Previous translation was BLOCKED: {failure_reason}")
    return " ".join(hints)


def recover_one_theorem(
    block: PlaceholderBlock,
    *,
    paper_id: str,
    project_root: Path,
    client: Any,
    model: str,
    timeout_s: int = 90,
) -> RecoveryResult:
    """Re-translate a single placeholder theorem via Leanstral.

    Returns a RecoveryResult; the caller decides whether to apply the
    replacement based on `new_signature_validates`."""
    full_latex, _meta = find_source_latex(paper_id, block.label, project_root)
    result = RecoveryResult(
        label=block.label,
        name=block.name,
        failure_reason=block.failure_reason,
        full_latex_chars=len(full_latex),
    )
    if not full_latex.strip():
        # Try the truncated header LaTeX as a last resort.
        full_latex = block.truncated_latex
        if not full_latex.strip():
            result.notes.append("no_source_latex_found")
            return result
        result.notes.append("using_truncated_header_latex")
    # Augment with the recovery hint so Leanstral has explicit context about
    # what failed last time.
    hint = _build_recovery_hint(block.failure_reason)
    augmented_latex = full_latex
    if hint:
        augmented_latex = full_latex + "\n\n[Recovery hint] " + hint

    # Lazy-import the translator. Heavy dependencies (mathlib retrieval,
    # premise embeddings) load only when we actually run a recovery.
    sys.path.insert(0, str(project_root / "scripts"))
    try:
        from translator._translate import translate_statement
    except Exception as exc:
        result.notes.append(f"translator_import_failed:{exc}")
        return result

    try:
        tr = translate_statement(
            latex_statement=augmented_latex,
            client=client,
            model=model,
            project_root=project_root,
            translation_candidates=2,
            paper_id=paper_id,
            theorem_name=block.name,
            run_id=f"stub_recovery:{paper_id}:{block.label}",
        )
    except Exception as exc:
        result.notes.append(f"translate_statement_raised:{exc.__class__.__name__}:{str(exc)[:120]}")
        return result

    new_signature = str(getattr(tr, "lean_signature", "") or getattr(tr, "lean_statement", "") or "").strip()
    validated = bool(getattr(tr, "validated", False))
    result.new_signature = new_signature
    result.new_signature_validates = validated
    if not new_signature:
        result.notes.append("translator_returned_empty_signature")
    elif not validated:
        result.notes.append("translation_did_not_pass_validation_gate")
    else:
        result.notes.append("translation_validated")
    return result


# ---------------------------------------------------------------------------
# CLI / orchestration
# ---------------------------------------------------------------------------

def _build_mistral_client() -> Any:
    """Construct the Mistral client used by the translator. Pipeline policy:
    Leanstral only — but the underlying SDK is `mistralai.Mistral`."""
    api_key = os.environ.get("MISTRAL_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("MISTRAL_API_KEY is not set in env; cannot run recovery")
    try:
        from mistralai import Mistral  # type: ignore[import-not-found]
    except ImportError:
        from mistralai.client import Mistral  # type: ignore[no-redef]
    return Mistral(api_key=api_key)


def _replace_placeholder_in_text(
    lean_text: str,
    block: PlaceholderBlock,
    new_signature: str,
) -> str:
    """Splice the new signature in place of the placeholder line.

    The new_signature may include `:= by sorry` already; if not, we keep the
    placeholder's `:= by sorry` so proof search can attempt closure later."""
    lines = lean_text.split("\n")
    idx = block.theorem_line - 1  # convert 1-based to 0-based
    sig = new_signature.strip()
    if ":= by" not in sig and not sig.endswith(":="):
        sig = sig + " := by sorry"
    elif sig.endswith(":="):
        sig = sig + " by sorry"
    lines[idx] = sig
    return "\n".join(lines)


def recover_paper(
    paper_id: str,
    *,
    project_root: Path,
    client: Any | None = None,
    model: str = "labs-leanstral-2603",
    write: bool = False,
    limit: int = 0,
) -> dict[str, Any]:
    """Recover all placeholder theorems in `output/<paper_id>.lean`."""
    lean_path = project_root / "output" / f"{paper_id}.lean"
    if not lean_path.exists():
        return {"paper_id": paper_id, "lean_file_missing": True, "blocks": [], "recovered": 0}

    text = lean_path.read_text(encoding="utf-8")
    blocks = detect_placeholders(text)
    summary: dict[str, Any] = {
        "schema_version": "leanstral_stub_recovery.v1",
        "paper_id": paper_id,
        "lean_file": str(lean_path.relative_to(project_root)),
        "placeholders_detected": len(blocks),
        "results": [],
        "recovered": 0,
        "applied": 0,
        "dry_run": not write,
    }
    if not blocks:
        return summary
    if limit > 0:
        blocks = blocks[:limit]

    new_text = text
    if client is None and write:
        client = _build_mistral_client()
    elif client is None:
        # Dry-run: don't actually call the API; just report what we'd attempt.
        for block in blocks:
            full_latex, _ = find_source_latex(paper_id, block.label, project_root)
            summary["results"].append({
                "label": block.label,
                "name": block.name,
                "failure_reason": block.failure_reason,
                "full_latex_chars": len(full_latex),
                "would_attempt": bool(full_latex.strip()) or bool(block.truncated_latex.strip()),
            })
        return summary

    for block in blocks:
        result = recover_one_theorem(
            block,
            paper_id=paper_id,
            project_root=project_root,
            client=client,
            model=model,
        )
        summary["results"].append({
            "label": result.label,
            "name": result.name,
            "failure_reason": result.failure_reason,
            "full_latex_chars": result.full_latex_chars,
            "new_signature_validates": result.new_signature_validates,
            "notes": result.notes,
        })
        if result.new_signature_validates:
            summary["recovered"] += 1
            if write:
                new_text = _replace_placeholder_in_text(new_text, block, result.new_signature)
                summary["applied"] += 1

    if write and summary["applied"] > 0 and new_text != text:
        backup = lean_path.with_suffix(".lean.bak")
        backup.write_text(text, encoding="utf-8")
        lean_path.write_text(new_text, encoding="utf-8")
        summary["backup"] = str(backup.relative_to(project_root))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paper_id", help="arxiv id, e.g. 2604.21884")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--model", default=os.environ.get("MISTRAL_MODEL", "labs-leanstral-2603"))
    parser.add_argument("--write", action="store_true", help="Apply repairs; default is dry-run")
    parser.add_argument("--limit", type=int, default=0, help="Cap on placeholders attempted (0=all)")
    args = parser.parse_args()

    summary = recover_paper(
        args.paper_id,
        project_root=args.project_root,
        model=args.model,
        write=args.write,
        limit=args.limit,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
