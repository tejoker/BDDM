#!/usr/bin/env python3
"""Signature-level typeclass patcher.

The B2 failure-mode anchor (commit `3713df4`) correctly identifies
`synthInstanceFailed: <Class> <Type>` patterns where `Type` is a free
type-variable declared as `{<Type> : Type*}` in the theorem signature.
However, the whole-proof retry loop can only edit the proof BODY — it
cannot insert `[Class Type]` instance binders into the SIGNATURE, which
is where this class of fix actually lives.

This module bridges that gap:

  propose_typeclass_additions(
      *, paper_id, theorem_name, lean_statement, baseline_error,
  ) -> list[str]

returns an ordered list of patched signature strings (most-targeted
single-instance first, then less-targeted multi-instance candidates).
Each returned signature is validated via `_run_isolated_file_check` and
only those that elaborate are surfaced.

The signature patch is a real formalization commitment: the caller is
expected to record `signature_patched_for_typeclass` in the row's
`audit_trail` and the audit's trivialization detector remains the final
arbiter even on a patched signature.

Standards-positive: we never fabricate type-variable names that aren't
already in the signature; we never propose classes that aren't already
named in the baseline error tail.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Callable, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


# Reuse the signature type-var extractor + class hint table from the
# existing B2 anchor module to keep the parsing surface unified.
try:
    from leanstral_proof_anchors import (  # type: ignore[import-not-found]
        extract_signature_type_vars,
        _CLASS_INSTANCE_HINTS,
    )
except Exception:  # pragma: no cover — defensive
    extract_signature_type_vars = lambda s: []  # type: ignore[assignment]
    _CLASS_INSTANCE_HINTS = {}  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Error-tail parsing
# ---------------------------------------------------------------------------


# Match either the `synthInstanceFailed: <Class> <Type>` or
# `failed to synthesize instance <Class> <Type>` /
# `failed to synthesize instance of type class\n  <Class> <Type>` forms.
# Capture the class name AND the rest of the payload up to end-of-line so we
# can sniff the type argument out.
_SYNTH_LINE_RE = re.compile(
    r"(?:synthInstanceFailed|failed to synthesize(?:\s+instance(?:\s+of\s+type\s+class)?)?)"
    r"\s*[:]?\s*\n?\s*`?([A-Z][\w.']*)\s*([^\n`]*)",
)


def parse_synth_instance_failures(error_tail: str) -> list[dict[str, str]]:
    """Parse `synthInstanceFailed: <Class> <Type>` markers out of the lake
    error tail. Returns an ordered, deduplicated list of {class_name,
    type_arg, raw} dicts. `type_arg` is the first whitespace-delimited
    token after the class name (e.g. `alpha` from `MeasurableSpace alpha`)
    or empty when only the class is named.
    """
    if not (error_tail or "").strip():
        return []
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for m in _SYNTH_LINE_RE.finditer(error_tail):
        cls = (m.group(1) or "").strip()
        payload = (m.group(2) or "").strip().strip(",;.`")
        # Take the first token as the type argument. If the payload starts
        # with a function-arrow-style or complex expression, leave type_arg
        # empty so we fall back to "find any free var".
        type_arg = ""
        if payload:
            tok = re.split(r"[\s\(\[\{,]", payload, maxsplit=1)[0].strip()
            if re.match(r"^[A-Za-z_][\w']*$", tok):
                type_arg = tok
        key = (cls, type_arg)
        if cls and key not in seen:
            seen.add(key)
            out.append({"class_name": cls, "type_arg": type_arg, "raw": payload})
    return out


# ---------------------------------------------------------------------------
# Common-alias expansion
# ---------------------------------------------------------------------------

# Classes that are commonly paired with a partner instance. The patcher
# proposes the bare class first and then the bare+partner combination as a
# fallback candidate. Order matters: the most-targeted (single-instance)
# proposal is offered before the broader (multi-instance) fallback.
_CLASS_ALIASES: dict[str, list[list[str]]] = {
    # MetricSpace usually carries a CompleteSpace partner in our corpus.
    "MetricSpace": [["MetricSpace"], ["MetricSpace", "CompleteSpace"]],
    # NormedAddCommGroup is frequently paired with an InnerProductSpace
    # over `ℝ` when subsequent errors mention `InnerProductSpace`.
    "NormedAddCommGroup": [
        ["NormedAddCommGroup"],
        ["NormedAddCommGroup", "InnerProductSpace ℝ"],
    ],
}


def _expand_class_aliases(
    cls: str, error_tail: str
) -> list[list[str]]:
    """Return one or more class-binder-lists for the given class. The first
    list is always the bare class; additional lists are partner pairings
    that may apply if the error tail hints at them.

    e.g. ("NormedAddCommGroup", "...InnerProductSpace..." in error)
         -> [["NormedAddCommGroup"], ["NormedAddCommGroup", "InnerProductSpace ℝ"]]
    """
    base = [[cls]]
    extras = _CLASS_ALIASES.get(cls, [])
    if not extras:
        return base
    # Heuristic: only emit the multi-instance pairing when the partner is
    # actually mentioned somewhere in the error tail.
    out: list[list[str]] = []
    out.append([cls])
    for variant in extras:
        if variant == [cls]:
            continue
        # Tail-match: partner mentioned ⇒ include.
        partners = variant[1:]
        if all(re.search(r"\b" + re.escape(p.split()[0]) + r"\b", error_tail) for p in partners):
            out.append(variant)
    return out


# ---------------------------------------------------------------------------
# Signature patching
# ---------------------------------------------------------------------------


def _find_type_var_binder(
    sig: str, type_var: str
) -> Optional[tuple[int, int]]:
    """Return the (start, end) span of the binder group that declares
    `type_var` as `Type*`/`Sort*`/`Type u` in `sig`, or None when the
    variable isn't a free type-variable in the signature.
    """
    # Search for the literal `{<type_var> : Type*}` or
    # `{<type_var> <other> : Type*}` binder group containing the name.
    pat = re.compile(
        r"[\{\(][^()\{\}]*?\b" + re.escape(type_var) + r"\b[^()\{\}]*?:\s*(?:Type|Sort)\s*(?:\*|u_?\d*)?[\)\}]"
    )
    m = pat.search(sig)
    if m is None:
        return None
    return (m.start(), m.end())


def patch_signature_with_instance(
    sig: str, class_binders: list[str], type_var: str
) -> Optional[str]:
    """Insert `[<binder> <type_var>]` (or `[<binder>]` if `<binder>`
    already contains explicit type-args) after the binder group that
    declares `type_var`. Returns the patched signature or None when:

    - `type_var` is not declared as a Type-binder in `sig`
    - the requested binders are already present in `sig`
    """
    span = _find_type_var_binder(sig, type_var)
    if span is None:
        return None
    # Build the [Class Type] (or pre-applied) text for each binder.
    new_binders: list[str] = []
    for b in class_binders:
        if not b.strip():
            continue
        # If the binder already contains a type-arg (e.g. "InnerProductSpace ℝ"),
        # we still need to attach the type_var as the trailing argument.
        binder_text = f"[{b} {type_var}]"
        # Avoid duplicating an existing identical binder.
        if binder_text in sig:
            continue
        # Avoid duplicating `[Class <type_var>]` even when whitespace differs.
        if re.search(
            r"\[\s*" + re.escape(b) + r"\s+" + re.escape(type_var) + r"\s*\]", sig
        ):
            continue
        new_binders.append(binder_text)
    if not new_binders:
        return None
    insert_at = span[1]
    addition = " " + " ".join(new_binders)
    patched = sig[:insert_at] + addition + sig[insert_at:]
    return patched


# ---------------------------------------------------------------------------
# Top-level proposer
# ---------------------------------------------------------------------------


def propose_typeclass_additions(
    *,
    paper_id: str,
    theorem_name: str,
    lean_statement: str,
    baseline_error: str,
    validate: Optional[Callable[[str], tuple[bool, str]]] = None,
) -> list[str]:
    """Given a row whose proof fails with `synthInstanceFailed: <Class> <Type>`,
    propose Lean signature edits that insert `[Class Type]` instance
    binders. Returns an ordered list of patched signatures to try (each
    validated by `validate` when provided).

    Args:
        paper_id: row's paper id (unused today; reserved for future
            paper-specific heuristics).
        theorem_name: row's theorem name (unused today; reserved for
            audit-trail bookkeeping by the caller).
        lean_statement: the row's `lean_statement` field — the candidate
            signature to patch.
        baseline_error: the lake error from a fresh validation of the
            (unpatched) row. We only fire when this contains
            `synthInstanceFailed:` (or a `failed to synthesize` variant)
            pinned to a type variable that's actually declared in the
            signature.
        validate: optional `(patched_sig) -> (ok, err_tail)` callback. When
            provided, only the patched signatures for which `ok=True` are
            returned. When `None`, all proposed patches are returned (the
            caller is responsible for downstream validation).

    Returns empty list when:
      - the error tail contains no `synthInstanceFailed:` markers
      - no parsed class targets a type variable declared in the signature
      - every proposed patch fails validation
    """
    if not (lean_statement or "").strip():
        return []
    failures = parse_synth_instance_failures(baseline_error or "")
    if not failures:
        return []
    type_vars = extract_signature_type_vars(lean_statement)
    if not type_vars:
        return []
    type_var_set = set(type_vars)

    # Build proposals: (priority, patched_sig). Lower priority first.
    proposals: list[tuple[int, str]] = []
    seen_sigs: set[str] = set()

    # Group failures by chosen type-var so we can also emit a combined
    # patch (all detected classes for the same type-var at once).
    by_var: dict[str, list[str]] = {}

    for fail in failures:
        cls = fail["class_name"]
        type_arg = fail["type_arg"]
        # Pick the type-var: either the parsed argument when it matches a
        # signature binder, else the first declared type-var (this matches
        # the existing B2 anchor fall-back behaviour).
        chosen: Optional[str] = None
        if type_arg and type_arg in type_var_set:
            chosen = type_arg
        elif type_vars:
            chosen = type_vars[0]
        if chosen is None:
            continue
        by_var.setdefault(chosen, [])
        if cls not in by_var[chosen]:
            by_var[chosen].append(cls)

        # Single-instance candidate (and its alias-expansions).
        for variant in _expand_class_aliases(cls, baseline_error or ""):
            patched = patch_signature_with_instance(lean_statement, variant, chosen)
            if patched is None or patched in seen_sigs:
                continue
            seen_sigs.add(patched)
            # Priority 0 = bare class, priority 1 = alias-expanded.
            prio = 0 if variant == [cls] else 1
            proposals.append((prio, patched))

    # Combined: every class targeting a given type-var at once.
    for var, classes in by_var.items():
        if len(classes) <= 1:
            continue
        patched = lean_statement
        applied = False
        for cls in classes:
            nxt = patch_signature_with_instance(patched, [cls], var)
            if nxt is not None:
                patched = nxt
                applied = True
        if applied and patched not in seen_sigs:
            seen_sigs.add(patched)
            proposals.append((2, patched))  # Combined patch is last-resort.

    proposals.sort(key=lambda t: t[0])

    if validate is None:
        return [sig for _prio, sig in proposals]

    # Validate each proposal; only return survivors.
    accepted: list[str] = []
    for _prio, sig in proposals:
        try:
            ok, _err = validate(sig)
        except Exception:
            ok = False
        if ok:
            accepted.append(sig)
    return accepted


# ---------------------------------------------------------------------------
# Convenience: pre-built validator
# ---------------------------------------------------------------------------


def build_isolated_validator(
    *,
    project_root: Path,
    source_file: Path,
    timeout_s: int = 60,
) -> Callable[[str], tuple[bool, str]]:
    """Return a closure validator(patched_sig) -> (ok, err_tail) that runs
    `_run_isolated_file_check` against an isolated file built from the
    source-file prelude + the patched signature (body forced to sorry).

    Returns a stub validator (always True) when `_run_isolated_file_check`
    cannot be imported (e.g. in hermetic tests).
    """
    try:
        from prove_arxiv_batch import _run_isolated_file_check  # type: ignore[import-not-found]
    except Exception:
        return lambda _s: (True, "")

    def _validator(patched_sig: str) -> tuple[bool, str]:
        return _run_isolated_file_check(
            project_root=project_root,
            source_file=source_file,
            theorem_decl=patched_sig,
            timeout_s=timeout_s,
        )

    return _validator


__all__ = [
    "parse_synth_instance_failures",
    "patch_signature_with_instance",
    "propose_typeclass_additions",
    "build_isolated_validator",
]
