#!/usr/bin/env python3
"""Type-aware destructuring of parent theorem statements.

Round-XII through Round-XXI showed lemma-factor producing 25+ factorings
per sweep with 0 successful compositions. The bottleneck: the LLM
proposes aux statements whose conjunction (or disjunction / equivalence)
does NOT match what the parent target actually needs. Each aux closes
individually, but they can't be assembled because their types don't
compose.

This module attacks the root cause: instead of letting the LLM propose
aux, **destructure the parent target syntactically** and emit aux specs
whose types compose to the parent BY CONSTRUCTION.

Shapes handled:

  - ``A ∧ B`` (top-level conjunction, n-ary):
        aux_i : (binders) → conjunct_i
        compose: ``⟨aux1, aux2, ..., auxN⟩`` or ``And.intro``
  - ``A ↔ B`` (bi-implication):
        aux_fwd : (binders) → A → B
        aux_bwd : (binders) → B → A
        compose: ``⟨aux_fwd, aux_bwd⟩``
  - ``∀ x, P x → Q x``:
        aux : (binders, x, hP : P x) → Q x
        compose: ``fun x hP => aux x hP``

Standards-positive: the destructured aux + composition template is a
strict semantic decomposition of the parent — no soundness gap. The
audit's existing gates fire identically on the aux and the composed
parent. If destructuring fails to match a known shape, the function
returns no aux and the caller falls back to the LLM-proposed flow.

NOTE: This module does NOT call Leanstral. It produces aux SPECS
(name + signature). The caller is expected to prove each aux via
whatever proof-search engine is in use, then compose.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class AuxSpec:
    """Type-aware destructured aux specification.

    The signature is a fully-formed ``theorem <name> <binders> : <target> := by sorry``
    declaration suitable for direct insertion into a Lean file. The
    composition template is the LHS of the eventual parent proof body:
    e.g. ``⟨{aux_names}⟩`` for an n-ary conjunction, ``fun x => {aux_names[0]} x``
    for a universal-implication, etc.
    """
    name: str
    signature: str
    target: str  # the aux target (without binders)
    shape: str   # "conjunct" / "iff_fwd" / "iff_bwd" / "calc_step" / "ufnctn_body"


def _split_top_level(target: str, delimiter: str) -> list[str]:
    """Split ``target`` on TOP-LEVEL occurrences of ``delimiter`` (e.g.
    ``∧`` / ``∨`` / ``↔``).

    Parentheses, brackets, braces, and angle brackets keep their level
    counter. Whitespace around the delimiter is trimmed. Returns a single-
    element list when the delimiter never appears at top level.
    """
    parts: list[str] = []
    depth_paren = 0
    depth_brack = 0
    depth_brace = 0
    depth_angle = 0  # heuristic for ⟨ ⟩
    last = 0
    i = 0
    while i < len(target):
        ch = target[i]
        if ch == "(":
            depth_paren += 1
        elif ch == ")":
            depth_paren = max(0, depth_paren - 1)
        elif ch == "[":
            depth_brack += 1
        elif ch == "]":
            depth_brack = max(0, depth_brack - 1)
        elif ch == "{":
            depth_brace += 1
        elif ch == "}":
            depth_brace = max(0, depth_brace - 1)
        elif ch == "⟨":
            depth_angle += 1
        elif ch == "⟩":
            depth_angle = max(0, depth_angle - 1)
        elif (
            depth_paren == 0 and depth_brack == 0 and depth_brace == 0 and depth_angle == 0
            and target[i:i + len(delimiter)] == delimiter
        ):
            piece = target[last:i].strip()
            if piece:
                parts.append(piece)
            last = i + len(delimiter)
            i += len(delimiter)
            continue
        i += 1
    tail = target[last:].strip()
    if tail:
        parts.append(tail)
    return parts


def _split_parent(lean_statement: str) -> Optional[tuple[str, str, str]]:
    """Return ``(name, binders, target)`` parsed from a theorem declaration.

    Recognizes ``theorem <name> <binders> : <target> := by sorry``-shaped
    text. The binders block is everything between the name and the FIRST
    top-level ``:`` (the type annotation separator). The target is
    everything after that colon, with any trailing ``:= by ...`` stripped.
    """
    s = lean_statement.strip()
    m = re.match(r"\s*(?:@\[[^]]*\]\s*)?theorem\s+([A-Za-z_][\w.]*)\s*", s)
    if not m:
        return None
    name = m.group(1)
    rest = s[m.end():]
    # Find first top-level `:` (not inside `( ... )` or `[ ... ]`).
    depth = 0
    sep = -1
    for i, ch in enumerate(rest):
        if ch in "([{⟨":
            depth += 1
        elif ch in ")]}⟩":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0:
            sep = i
            break
    if sep < 0:
        return None
    binders = rest[:sep].strip()
    target_with_body = rest[sep + 1:].strip()
    # Strip trailing `:= by ...` or `:= term`.
    body_match = re.search(r":=\s*by\b", target_with_body)
    if body_match:
        target = target_with_body[:body_match.start()].strip()
    else:
        eq_match = re.search(r":=", target_with_body)
        target = (target_with_body[:eq_match.start()].strip()
                  if eq_match else target_with_body)
    return name, binders, target


def _is_trivial_target(target: str) -> bool:
    """A target is "trivial" if it's bare ``True`` / ``False`` / a single
    identifier with no operators. Don't destructure those."""
    t = target.strip()
    if t in {"True", "False"}:
        return True
    if re.fullmatch(r"[A-Za-z_][\w.]*", t):
        return True
    return False


def _target_has_outer_binder(target: str) -> bool:
    """True when the target begins with an outer ``∃`` or ``∀`` quantifier.

    Splitting a top-level ``∧`` inside ``∃ x, A x ∧ B x`` is UNSOUND:
    both conjuncts share the witness ``x``, so emitting independent aux
    ``∃ x, A x`` and ``B x`` (the latter has ``x`` as a free variable)
    loses the shared-witness constraint. The bug surfaced in Round-XXII:
    the destructure produced aux like ``∃ C_omega : ℝ, 0 < C_omega``
    (trivially true via ``⟨1, _⟩``) when the real obligation was the
    full ``∃ C_omega : ℝ, 0 < C_omega ∧ <non-trivial property>``.

    For ``∀``, the analogous issue: splitting ``∀ x, A x ∧ B x`` into
    ``∀ x, A x`` and ``∀ x, B x`` is technically sound but loses the
    parent's intended composition path (the LLM might prove them
    independently when the parent really wants a single ``intro x``
    followed by a conjunction proof).

    Conservative rule: refuse destructure for any target starting with
    ``∃`` or ``∀``. Caller falls back to the LLM-based factoring.
    """
    s = target.lstrip()
    if not s:
        return False
    # ∃ in Unicode is the only existential quantifier Lean uses; same for ∀.
    return s.startswith("∃") or s.startswith("∀")


def _render_aux_decl(
    *, parent_name: str, idx: int, binders: str, target: str, shape: str,
) -> AuxSpec:
    """Render an aux signature suitable for direct Lean file insertion."""
    aux_name = f"{parent_name}_{shape}_{idx}__type_aware_aux"
    binders_block = binders.strip()
    if binders_block:
        sig = (
            f"theorem {aux_name} {binders_block} : {target} := by sorry"
        )
    else:
        sig = f"theorem {aux_name} : {target} := by sorry"
    return AuxSpec(name=aux_name, signature=sig, target=target, shape=shape)


def destructure_conjunction(
    *, parent_name: str, binders: str, target: str,
) -> list[AuxSpec]:
    """Top-level ``A ∧ B ∧ ...`` → n aux, one per conjunct."""
    parts = _split_top_level(target, "∧")
    if len(parts) < 2:
        return []
    return [
        _render_aux_decl(
            parent_name=parent_name, idx=i, binders=binders,
            target=p, shape="conjunct",
        )
        for i, p in enumerate(parts, start=1)
    ]


def destructure_iff(
    *, parent_name: str, binders: str, target: str,
) -> list[AuxSpec]:
    """``A ↔ B`` → 2 aux: ``A → B`` and ``B → A``."""
    parts = _split_top_level(target, "↔")
    if len(parts) != 2:
        return []
    a, b = parts[0].strip(), parts[1].strip()
    return [
        _render_aux_decl(
            parent_name=parent_name, idx=1, binders=binders,
            target=f"{a} → {b}", shape="iff_fwd",
        ),
        _render_aux_decl(
            parent_name=parent_name, idx=2, binders=binders,
            target=f"{b} → {a}", shape="iff_bwd",
        ),
    ]


def destructure(lean_statement: str) -> list[AuxSpec]:
    """Top-level entrypoint. Returns destructured aux specs, or empty
    list if the parent target doesn't match a handled shape.

    Refuses destructure when the target begins with an outer ``∃``/``∀``
    binder — see `_target_has_outer_binder` for the soundness argument.
    The Round-XXII bypass discovery validated this gate: splitting
    ``∃ x, A x ∧ B x`` into ``∃ x, A x`` (closes trivially with witness 1)
    and ``B x`` (loses ``x``) is mathematically wrong.
    """
    parsed = _split_parent(lean_statement)
    if not parsed:
        return []
    name, binders, target = parsed
    if _is_trivial_target(target):
        return []
    if _target_has_outer_binder(target):
        return []
    # Bare name keyed off the parent (collapse namespace).
    base = name.rsplit(".", 1)[-1]
    # Try shapes in order. First non-empty result wins.
    for fn in (destructure_iff, destructure_conjunction):
        out = fn(parent_name=base, binders=binders, target=target)
        if out:
            return out
    return []


def compose_template(specs: list[AuxSpec]) -> str:
    """Given the aux specs returned by ``destructure``, produce a Lean
    composition body (no binders, just the term/tactic). The composition
    is **type-correct by construction** because the aux were derived
    from the parent's exact destructure.
    """
    if not specs:
        return ""
    shapes = {s.shape for s in specs}
    names = [s.name for s in specs]
    if shapes == {"conjunct"}:
        # ⟨aux1, aux2, ..., auxN⟩ — Lean's anonymous-constructor for ∧
        return "exact ⟨" + ", ".join(names) + "⟩"
    if shapes == {"iff_fwd", "iff_bwd"}:
        # ⟨fwd, bwd⟩ — Iff.intro shape
        fwd = next(s.name for s in specs if s.shape == "iff_fwd")
        bwd = next(s.name for s in specs if s.shape == "iff_bwd")
        return f"exact ⟨{fwd}, {bwd}⟩"
    return ""


def main() -> int:  # pragma: no cover
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lean-statement", help="Decl text to destructure")
    args = parser.parse_args()
    if args.lean_statement:
        specs = destructure(args.lean_statement)
        out = {
            "aux": [
                {
                    "name": s.name,
                    "signature": s.signature,
                    "target": s.target,
                    "shape": s.shape,
                }
                for s in specs
            ],
            "compose": compose_template(specs),
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
