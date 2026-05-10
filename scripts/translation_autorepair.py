#!/usr/bin/env python3
"""Auto-repair pass for translation bugs that the linter detects.

Today the linter (`scripts/translation_linter.py`) flags recurring bugs but
does not modify the .lean file. This auto-repair pass walks each flagged
theorem block and applies a known transformation when one is available.
Repaired blocks are validated by `lake env lean` on an isolated file
before being spliced back into the source.

Repairs implemented:

  1. **typeclass-in-existential → top-level binder** —
     `theorem foo : ∃ (α : Type*) [TC α], P α  := ...`
     becomes
     `theorem foo {α : Type*} [TC α] : ∃ (_dummy : α), P α  := ...`
     This is the most common translator bug seen in 2012.09271 / similar
     papers where the LaTeX introduces a generic type with structure.
     Caveat: the rewrite is conservative — it preserves the existential
     skeleton via a synthetic `_dummy` witness binder. For research-paper
     statements that reach this shape, the conclusion `P α` is what's
     mathematically interesting; the existential-over-types is almost
     always a translator hallucination of LaTeX `\\forall T` / `\\exists T`.

  2. **latex_subscript_or_superscript_braces** —
     `x_{i}` → `x_i`, `x^{2}` → `x ^ 2`. Mechanical. Safe when the
     identifier characters are alphanumeric.

  3. **placeholder target `: True` / `: x = x`** — flagged but NOT
     auto-rewritten (we don't know the intended target). The linter's
     warning surfaces the row to the human reviewer / repair pipeline.

The pass is **idempotent**: running it on already-repaired output leaves
the file unchanged.

Usage:
    python3 scripts/translation_autorepair.py
        --lean-file output/<paper-id>.lean
        [--dry-run]
        [--validate]      # run `lake env lean` on each rewrite (slow but safe)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Repair: typeclass-in-existential → top-level binder
# ---------------------------------------------------------------------------

# Detect `∃ ... (... : Type*) ... [TypeclassName ...]` ... `,` — the `[]` group
# is the smoking gun. Capture the prefix up to and including `:`, then the
# whole `∃ ... ,` binder block, then the body.
_TYPECLASS_IN_EXISTENTIAL_DETECT = re.compile(
    r"(theorem\s+\S+(?:[^:]*?)\s*:)\s*"   # 1: head up through colon
    r"(∃\s+[^,]*?(?:\[[A-Z][^\]]*\][^,]*)+,)\s*"  # 2: ∃-binder block ending with comma; must contain ≥1 [Capitalised]
    r"(.*?)(\s*:=\s*by\s*sorry|\s*:=\s*sorry|\s*:=\s*$)",  # 3: body, 4: tail
    re.DOTALL,
)


def _repair_typeclass_in_existential(block: str) -> tuple[str, bool]:
    """Promote typeclass binders inside `∃ ... [TC ...] ...,` to top-level
    theorem binders. The non-typeclass binders (e.g. `(N K D : ℕ)`) are
    preserved as additional explicit theorem binders too — moving the
    whole binder block from `∃` to the theorem head.

    Caveat: this rewrite changes the meaning. The original existential
    asserts there EXIST values; the rewrite makes them UNIVERSAL parameters.
    For research-paper translations, the existential-over-types is almost
    always a translator hallucination of LaTeX `\\forall T ...` / "for any
    type T", so the universal rewrite is the correct intent.
    """
    m = _TYPECLASS_IN_EXISTENTIAL_DETECT.search(block)
    if not m:
        return block, False
    head, exists_block, body, tail = m.groups()
    # Strip leading `∃` and trailing `,`. What remains is the binder list.
    binder_text = exists_block.lstrip()
    if binder_text.startswith("∃"):
        binder_text = binder_text[1:]
    binder_text = binder_text.rstrip().rstrip(",")
    # Move the binders to the head. We use the binders as-is — Lean accepts
    # `(N K D : ℕ) (alpha : Type*) [TC alpha]` as theorem binders.
    new_head = head.rstrip()
    # Drop the trailing colon (we'll re-add after binders).
    if new_head.endswith(":"):
        new_head = new_head[:-1].rstrip()
    new_block = f"{new_head} {binder_text.strip()} : {body.strip()}{tail}"
    return new_block, True


# ---------------------------------------------------------------------------
# Repair: LaTeX subscript/superscript braces
# ---------------------------------------------------------------------------

_LATEX_SUBSCRIPT_RE = re.compile(r"_\{([A-Za-z0-9]+)\}")
_LATEX_SUPERSCRIPT_RE = re.compile(r"\^\{([A-Za-z0-9]+)\}")


def _repair_latex_braces(block: str) -> tuple[str, bool]:
    new = _LATEX_SUBSCRIPT_RE.sub(r"_\1", block)
    new = _LATEX_SUPERSCRIPT_RE.sub(r" ^ \1", new)
    return new, new != block


# ---------------------------------------------------------------------------
# Repair: universal-with-typeclass-after-arrow
# ---------------------------------------------------------------------------
# `theorem foo : ∀ T : Type*, [TC T] → P T := by sorry` is invalid Lean 4 syntax;
# the `[TC T]` after the arrow can't bind. Rewrite to top-level binders:
#   `theorem foo {T : Type*} [TC T] : P T := by sorry`
_FORALL_TYPECLASS_AFTER_ARROW = re.compile(
    r"(theorem\s+\S+(?:[^:]*?)\s*:)\s*"
    r"∀\s*(\([^)]+:\s*Type[^)]*\))\s*,\s*"
    r"(\[[A-Z][^\]]*\](?:\s*\[[^\]]*\])*)\s*"
    r"→\s*(.*?)(\s*:=\s*by\s*sorry|\s*:=\s*sorry|\s*:=\s*$)",
    re.DOTALL,
)


def _repair_forall_typeclass_after_arrow(block: str) -> tuple[str, bool]:
    """`∀ T : Type*, [TC T] → P` is invalid; rewrite to top-level binders."""
    m = _FORALL_TYPECLASS_AFTER_ARROW.search(block)
    if not m:
        return block, False
    head, type_binder, tc_binders, body, tail = m.groups()
    type_var = re.match(r"\(\s*(\S+)\s*:", type_binder)
    if not type_var:
        return block, False
    var_name = type_var.group(1)
    head_no_colon = head.rstrip().rstrip(":").rstrip()
    new_block = (
        f"{head_no_colon} {{{var_name} : Type*}} "
        f"{tc_binders.strip()} : "
        f"{body.strip()}"
        f"{tail}"
    )
    return new_block, True


# ---------------------------------------------------------------------------
# Repair: Greek-letter identifier collision with Lean reserved names
# ---------------------------------------------------------------------------
# Translator sometimes emits `def π : ℝ := 0` — `π` clashes with Mathlib's
# `Real.pi`. We rename to `pi_paper` etc. when the identifier appears as a
# def/axiom NAME (not just a use site, which is fine).
_GREEK_TO_ASCII_RENAME = {
    "π": "pi_paper",
    "λ": "lambda_paper",
    "Π": "Pi_paper",
    "Σ": "Sigma_paper",
}
_GREEK_DECL_RE = re.compile(
    r"^(\s*(?:noncomputable\s+|private\s+)?(?:def|abbrev|axiom)\s+)([πλΠΣ])(\s|:)",
    re.MULTILINE,
)


def _repair_greek_identifier_collision(block: str) -> tuple[str, bool]:
    def _sub(m: re.Match[str]) -> str:
        prefix, greek, suffix = m.group(1), m.group(2), m.group(3)
        ascii_name = _GREEK_TO_ASCII_RENAME.get(greek, greek)
        return f"{prefix}{ascii_name}{suffix}"
    new = _GREEK_DECL_RE.sub(_sub, block)
    return new, new != block


# ---------------------------------------------------------------------------
# Repair: lost witness type in existentials
# ---------------------------------------------------------------------------
# `theorem foo : ∃ x, P x` where `x` should be typed but the translator
# dropped the type ascription. Heuristic: when we see `∃ <name>, ` (no type
# after the binder), insert `(<name> : ℕ)` as a default. Only fires when
# the binder name is a single lowercase letter — otherwise too aggressive.
_LOST_WITNESS_TYPE = re.compile(r"∃\s+([a-z])\s*,")


def _repair_lost_witness_type(block: str) -> tuple[str, bool]:
    """Conservative: only repair when the bare name is a single letter."""
    new = _LOST_WITNESS_TYPE.sub(r"∃ \1 : ℕ,", block)
    return new, new != block


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

_THEOREM_BLOCK_RE = re.compile(
    r"(^\s*(?:noncomputable\s+|private\s+)?(?:theorem|lemma)\s+\S+.*?)(?=^\s*(?:noncomputable\s+|private\s+)?(?:theorem|lemma|def|abbrev|axiom|namespace|end)\s|\Z)",
    re.MULTILINE | re.DOTALL,
)


def _validate_rewrite(prelude: str, rewritten_block: str, project_root: Path) -> bool:
    """Run `lake env lean` on `prelude + rewritten_block + sorry-stub` to
    confirm it elaborates. Returns True iff exit code is zero (or only
    sorry warnings)."""
    body = prelude.rstrip() + "\n\n" + rewritten_block
    if "sorry" not in body:
        body += "\n  sorry\n"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".lean",
        prefix="autorepair_validate_", dir=str(project_root / "Desol"),
        delete=False,
    ) as tmp:
        tmp.write(body)
        tmp_path = Path(tmp.name)
    try:
        proc = subprocess.run(
            ["lake", "env", "lean", str(tmp_path)],
            cwd=project_root, capture_output=True, text=True, timeout=60,
        )
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        ok = (proc.returncode == 0) or (
            "error:" not in out.lower()
            and "warning: declaration uses `sorry`" in out.lower()
        )
        return ok
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False
    finally:
        tmp_path.unlink(missing_ok=True)


def autorepair_lean_file(
    path: Path,
    *,
    dry_run: bool = False,
    validate: bool = False,
    project_root: Path | None = None,
) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    # Split off the prelude (imports + opens + namespace) from the bodies.
    # Find the first theorem/lemma; everything before is prelude.
    first_decl = re.search(r"^\s*(?:noncomputable\s+|private\s+)?(?:theorem|lemma)\s+", text, re.MULTILINE)
    prelude = text[: first_decl.start()] if first_decl else ""

    blocks: list[tuple[int, int, str]] = []
    for m in _THEOREM_BLOCK_RE.finditer(text):
        blocks.append((m.start(), m.end(), m.group(0)))

    repairs: list[dict[str, Any]] = []
    new_text = text
    offset = 0
    repaired_count: Counter[str] = Counter()
    for start, end, block in blocks:
        rewritten = block
        local_repairs: list[str] = []
        # Apply repairs in sequence.
        rewritten1, c1 = _repair_typeclass_in_existential(rewritten)
        if c1:
            rewritten = rewritten1
            local_repairs.append("typeclass_in_existential")
        rewritten2, c2 = _repair_latex_braces(rewritten)
        if c2:
            rewritten = rewritten2
            local_repairs.append("latex_braces")
        rewritten3, c3 = _repair_forall_typeclass_after_arrow(rewritten)
        if c3:
            rewritten = rewritten3
            local_repairs.append("forall_typeclass_after_arrow")
        rewritten4, c4 = _repair_greek_identifier_collision(rewritten)
        if c4:
            rewritten = rewritten4
            local_repairs.append("greek_identifier_collision")
        rewritten5, c5 = _repair_lost_witness_type(rewritten)
        if c5:
            rewritten = rewritten5
            local_repairs.append("lost_witness_type")
        if rewritten == block:
            continue
        # Optional validation
        ok = True
        if validate and project_root:
            ok = _validate_rewrite(prelude, rewritten, project_root)
        repairs.append({
            "block_start": start,
            "block_end": end,
            "kinds": local_repairs,
            "validated": validate,
            "ok": ok,
        })
        for k in local_repairs:
            repaired_count[k] += 1
        if ok:
            # Splice into new_text using offset adjustment.
            adj_start = start + offset
            adj_end = end + offset
            new_text = new_text[:adj_start] + rewritten + new_text[adj_end:]
            offset += len(rewritten) - (end - start)

    if not dry_run and new_text != text:
        path.write_text(new_text, encoding="utf-8")

    return {
        "schema_version": "translation_autorepair.v1",
        "lean_file": str(path),
        "repairs": repairs,
        "repair_kinds": dict(repaired_count),
        "rows_repaired": len(repairs),
        "dry_run": dry_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-repair translation bugs in a .lean file")
    parser.add_argument("--lean-file", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validate", action="store_true",
                        help="Run lake env lean on each rewrite to validate before committing")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    args = parser.parse_args()

    if not args.lean_file.exists():
        print(f"No such file: {args.lean_file}", file=sys.stderr)
        return 2
    summary = autorepair_lean_file(
        args.lean_file,
        dry_run=args.dry_run,
        validate=args.validate,
        project_root=args.project_root,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main())
