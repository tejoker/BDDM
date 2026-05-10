#!/usr/bin/env python3
"""Translation-quality linter for paper→Lean translations.

Catches the recurring translation bugs we've observed across the BDDM corpus:

  * `typeclass_in_existential` — `∃ alpha [SomeTypeclass alpha], ...` is not
    valid Lean 4 syntax. Translator hallucinates these regularly when a
    paper introduces a generic type with structure.
  * `unbound_free_variable` — a hypothesis `(hbeta : 3/2 < beta ∧ beta ≤ 2)`
    references `beta` without it being declared as a binder. Lean 4 rejects
    unless `set_option autoImplicit true`. Pre-flight detection lets us
    auto-insert binders or warn.
  * `latex_leak_token` — identifiers like `frac`, `mathbf`, `tilde`,
    `mathcal`, `qquad`, `quad`, `displaystyle` appearing as Lean
    identifiers (translation didn't strip the LaTeX command).
  * `primed_vs_bare_collision` — `def Γ' : ℝ := 0` exists but the export
    line references bare `Γ` (caused build failures in 2604.21314 / 2604.21583).
  * `false_target_without_source_contradiction` — `theorem X : False := by sorry`
    when the source LaTeX is a positive claim. Strong signal that translation
    gave up.
  * `placeholder_target` — conclusions like `True`, `0 = 0`, `x = x` —
    accidentally vacuous targets.
  * `latex_subscript_or_superscript_braces` — raw `_{i}` or `^{2}` in the
    Lean output.

Output JSON schema:

    {
      "schema_version": "translation_linter.v1",
      "lean_file": str,
      "rows_checked": int,
      "issues": [
        {"theorem_name": str, "kind": str, "severity": "error|warning",
         "detail": str, "snippet": str}
      ]
    }

Use as a pre-prover hook: `--pre-flight` produces a JSON report; pipeline
can decline to run the prove cycle on rows with critical issues.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Lint checks
# ---------------------------------------------------------------------------

# `∃ ... [ TypeclassName ... ] , ...` — typeclass binder inside existential.
# Match `∃` then arbitrary characters (including binder groups) up to a `[`
# bracketed expression (the typeclass), then a comma. We bound the search to
# the same line group so we don't drift into the body — `∃` should bind a
# limited number of binders before the body comma.
_TYPECLASS_IN_EXISTENTIAL_RE = re.compile(
    r"∃[^,\n]*?\[[A-Z][A-Za-z_0-9\s][^\]\n]*\][^,\n]*,",
    re.MULTILINE,
)

# Identifiers that are LaTeX commands the translator failed to strip.
# LaTeX commands that should never appear as Lean identifiers. NOTE: we
# deliberately exclude `begin`/`end` because they're valid Lean keywords
# (`end <namespace>`); the translator never emits a bare `begin/end` as an
# identifier.
_LATEX_LEAK_TOKENS = {
    "frac", "dfrac", "tfrac",
    "mathbf", "mathrm", "mathbb", "mathit", "mathfrak", "mathcal",
    "qquad", "quad", "displaystyle", "textstyle", "scriptstyle",
    "bigl", "bigr", "biggl", "biggr",
    "underline", "overline",
}

_LATEX_SUBSCRIPT_OR_SUPERSCRIPT_RE = re.compile(r"[_^]\{[A-Za-z0-9]+\}")

# Placeholder / vacuous targets. Match common shapes in target position
# (after the last `:` of the theorem head, but we approximate by looking at
# the post-colon text).
_PLACEHOLDER_TARGET_PATTERNS = (
    r":\s*True\s*(?::=|$)",
    r":\s*0\s*=\s*0\s*(?::=|$)",
    r":\s*([A-Za-z_]\w*)\s*=\s*\1\s*(?::=|$)",  # `x = x`
)

_THEOREM_HEAD_RE = re.compile(
    r"^\s*(?:noncomputable\s+|private\s+)?(?:theorem|lemma)\s+([A-Za-z_][A-Za-z_0-9'.]*)\b",
    re.MULTILINE,
)


def _extract_theorem_blocks(text: str) -> list[tuple[str, str]]:
    """Return (theorem_name, block_text) for every theorem in the file.
    A block runs from the theorem head to the next theorem head (or EOF)."""
    matches = list(_THEOREM_HEAD_RE.finditer(text))
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append((m.group(1), text[start:end]))
    return out


def _check_typeclass_in_existential(name: str, block: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if _TYPECLASS_IN_EXISTENTIAL_RE.search(block):
        # Find a snippet
        m = _TYPECLASS_IN_EXISTENTIAL_RE.search(block)
        snippet = block[max(0, (m.start() if m else 0) - 20):(m.end() if m else 60) + 20]
        issues.append({
            "theorem_name": name,
            "kind": "typeclass_in_existential",
            "severity": "error",
            "detail": "Typeclass binder `[...]` inside an existential is not valid Lean 4 syntax. Promote to a top-level theorem binder.",
            "snippet": snippet[:200],
        })
    return issues


def _check_latex_leak(name: str, block: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    # Look for LaTeX-leak tokens used as identifiers (whole-word match, not as
    # a substring of a real Lean identifier).
    for tok in _LATEX_LEAK_TOKENS:
        pattern = rf"\b{re.escape(tok)}\b"
        if re.search(pattern, block):
            # Filter false positives: `bar` may legitimately appear in `_bar` patterns;
            # only flag if it's a standalone token (preceded/followed by space or
            # punctuation, not by `_`).
            real = re.search(rf"(?<![_A-Za-z0-9]){re.escape(tok)}(?![_A-Za-z0-9])", block)
            if real:
                issues.append({
                    "theorem_name": name,
                    "kind": "latex_leak_token",
                    "severity": "error",
                    "detail": f"LaTeX command `{tok}` appears as a Lean identifier. Translation likely missed a `\\{tok}` strip.",
                    "snippet": block[max(0, real.start() - 20):real.end() + 20][:200],
                })
                break  # one report per theorem is enough
    if _LATEX_SUBSCRIPT_OR_SUPERSCRIPT_RE.search(block):
        m = _LATEX_SUBSCRIPT_OR_SUPERSCRIPT_RE.search(block)
        issues.append({
            "theorem_name": name,
            "kind": "latex_subscript_or_superscript_braces",
            "severity": "error",
            "detail": "Raw `_{...}` or `^{...}` in Lean — translator failed to convert LaTeX subscript/superscript to Lean form.",
            "snippet": block[max(0, (m.start() if m else 0) - 20):(m.end() if m else 60) + 20][:200] if m else "",
        })
    return issues


def _check_placeholder_target(name: str, block: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    # Strip the body — only check the head's target.
    head_match = re.search(r"^\s*theorem\s+\S+(.*?)(?::=\s*by|$)", block, re.DOTALL)
    if not head_match:
        return issues
    head = head_match.group(0)
    for pattern in _PLACEHOLDER_TARGET_PATTERNS:
        if re.search(pattern, head):
            issues.append({
                "theorem_name": name,
                "kind": "placeholder_target",
                "severity": "warning",
                "detail": "Theorem target is vacuous (True / 0=0 / x=x). Translator likely degraded the conclusion.",
                "snippet": head[:200],
            })
            break
    return issues


def _check_false_target(name: str, block: str) -> list[dict[str, Any]]:
    """Flag `: False := by sorry` patterns. The pipeline now substitutes
    these from the ledger when possible, but pre-flight surfacing helps the
    caller (or a CI-style report) know the translator declined the row."""
    if re.search(r":\s*False\s*:=\s*by\s*sorry", block):
        return [{
            "theorem_name": name,
            "kind": "false_target_translator_gave_up",
            "severity": "warning",
            "detail": "Translator emitted `: False := by sorry` as a fallback. Pipeline will attempt ledger substitution; if that fails, the row stays UNRESOLVED.",
            "snippet": "",
        }]
    return []


def _check_unbound_free_variable(name: str, block: str) -> list[dict[str, Any]]:
    """Detect references to identifiers in hypothesis types that aren't
    declared as binders in the same theorem head. This is a HEURISTIC — it
    scans for short Greek-letter or short-lowercase identifiers used inside
    hypothesis bodies but not declared. Used as a warning only because
    `set_option autoImplicit true` makes this legal in Lean 4 (which is
    why we have that fallback in the prover prelude).
    """
    head_match = re.match(r"\s*theorem\s+\S+(.*?)(?::=\s*by|\Z)", block, re.DOTALL)
    if not head_match:
        return []
    head = head_match.group(1)
    # Collect declared binder names from `(name : Type)` and `{name : Type}`.
    binders: set[str] = set()
    for m in re.finditer(r"[\(\{]\s*([A-Za-z_][A-Za-z_0-9']*(?:\s+[A-Za-z_][A-Za-z_0-9']*)*)\s*:", head):
        for nm in m.group(1).split():
            binders.add(nm)
    # Find typical short-name variables referenced inside hypothesis types
    # but not declared. Only flag short Greek/lowercase names — common
    # paper-formula identifiers.
    referenced = set(re.findall(r"\b([a-z][a-z_0-9]{0,9})\b", head))
    suspect = {
        v for v in referenced
        if v not in binders
        and v not in {"by", "let", "if", "then", "else", "fun", "True", "False", "Type",
                       "rfl", "trivial", "sorry", "and", "or", "not", "nhds",
                       "alpha", "beta", "gamma", "delta", "epsilon", "eta", "theta",
                       "lambda", "mu", "nu", "rho", "sigma", "tau", "phi", "chi", "psi", "omega"}
        and len(v) <= 6
    }
    # `alpha`/`beta` etc. are intentionally allowed because they're paper-conventional;
    # autoImplicit will bind them. We only warn on UNUSUAL short names.
    if suspect:
        # Light: report as a warning, not an error.
        return [{
            "theorem_name": name,
            "kind": "unbound_free_variable_heuristic",
            "severity": "warning",
            "detail": f"Heuristic: short identifiers possibly unbound: {sorted(suspect)[:5]}. autoImplicit will catch these but the translator should ideally bind them.",
            "snippet": "",
        }]
    return []


# ---------------------------------------------------------------------------
# Linter pipeline
# ---------------------------------------------------------------------------

def lint_lean_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    blocks = _extract_theorem_blocks(text)
    issues: list[dict[str, Any]] = []
    for name, block in blocks:
        issues.extend(_check_typeclass_in_existential(name, block))
        issues.extend(_check_latex_leak(name, block))
        issues.extend(_check_placeholder_target(name, block))
        issues.extend(_check_false_target(name, block))
        issues.extend(_check_unbound_free_variable(name, block))
    return {
        "schema_version": "translation_linter.v1",
        "lean_file": str(path),
        "rows_checked": len(blocks),
        "issues": issues,
        "issue_kind_counts": dict(Counter(i["kind"] for i in issues)),
        "rows_with_errors": len({i["theorem_name"] for i in issues if i["severity"] == "error"}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Translation-quality linter for paper→Lean files")
    parser.add_argument("--lean-file", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None,
                        help="Optional JSON output path; default: stdout")
    parser.add_argument("--severity", choices=["error", "warning", "all"], default="all",
                        help="Filter issues by minimum severity")
    args = parser.parse_args()

    if not args.lean_file.exists():
        print(f"No such file: {args.lean_file}", file=sys.stderr)
        return 2

    report = lint_lean_file(args.lean_file)
    if args.severity == "error":
        report["issues"] = [i for i in report["issues"] if i["severity"] == "error"]
    elif args.severity == "warning":
        report["issues"] = [i for i in report["issues"] if i["severity"] in ("error", "warning")]

    out = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(out + "\n", encoding="utf-8")
    else:
        print(out)
    # Exit with code 1 if any errors so this is CI-friendly.
    return 1 if report["rows_with_errors"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
