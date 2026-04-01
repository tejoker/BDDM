#!/usr/bin/env python3
"""Optional per-step entailment checker.

This module supports an SMT-backed consistency check (z3) for arithmetic
constraints seen in step traces. When z3 is unavailable, it falls back to a
conservative signal-only mode.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

try:
    import z3  # type: ignore[import]
except Exception:  # pragma: no cover - optional dependency
    z3 = None


_ATOM_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*)\s*(<=|>=|!=|=|<|>)\s*(-?\d+)\b"
)


@dataclass
class EntailmentAssessment:
    checked_steps: int
    flawed_steps: int
    unknown_steps: int
    smt_inconsistent_steps: int
    backend: str
    is_flawed: bool


@dataclass
class ConstraintAtom:
    variable: str
    op: str
    value: int


def _extract_atoms(text: str) -> list[ConstraintAtom]:
    atoms: list[ConstraintAtom] = []
    if not text:
        return atoms

    normalized = (
        text.replace("≤", "<=")
        .replace("≥", ">=")
        .replace("≠", "!=")
    )
    for m in _ATOM_RE.finditer(normalized):
        atoms.append(
            ConstraintAtom(
                variable=m.group(1),
                op=m.group(2),
                value=int(m.group(3)),
            )
        )
    return atoms


def _build_z3_expr(atom: ConstraintAtom, vars_map: dict[str, Any]):
    if atom.variable not in vars_map:
        vars_map[atom.variable] = z3.Int(atom.variable)
    v = vars_map[atom.variable]
    c = z3.IntVal(atom.value)
    if atom.op == "<":
        return v < c
    if atom.op == "<=":
        return v <= c
    if atom.op == ">":
        return v > c
    if atom.op == ">=":
        return v >= c
    if atom.op == "=":
        return v == c
    if atom.op == "!=":
        return v != c
    return None


def _is_consistent(atoms: list[ConstraintAtom]) -> bool:
    if z3 is None:
        return True
    solver = z3.Solver()
    vars_map: dict[str, Any] = {}
    for atom in atoms:
        expr = _build_z3_expr(atom, vars_map)
        if expr is not None:
            solver.add(expr)
    return solver.check() != z3.unsat


def assess_step_entailment(step_obligations: list[Any]) -> EntailmentAssessment:
    """Assess per-step consistency using optional SMT checks.

    Behavior:
    - Always marks explicit `lean-error` / `proof-given-up` steps as flawed.
    - If z3 is available, parses simple arithmetic atoms from `detail` and marks
      a step flawed if accumulated constraints become inconsistent.
    - If z3 is unavailable, falls back to conservative signal-only mode.
    """
    checked = 0
    flawed = 0
    unknown = 0
    smt_inconsistent = 0
    backend = "z3" if z3 is not None else "heuristic"
    accumulated_atoms: list[ConstraintAtom] = []

    for s in step_obligations:
        result = str(getattr(s, "result", "") or "").strip().lower()
        if result in {"lean-error", "proof-given-up"}:
            checked += 1
            flawed += 1
            continue

        detail = str(getattr(s, "detail", "") or "")
        atoms = _extract_atoms(detail)
        if not atoms:
            unknown += 1
            continue

        if z3 is None:
            unknown += 1
            continue

        checked += 1
        proposed = accumulated_atoms + atoms
        if _is_consistent(proposed):
            accumulated_atoms = proposed
        else:
            flawed += 1
            smt_inconsistent += 1

    return EntailmentAssessment(
        checked_steps=checked,
        flawed_steps=flawed,
        unknown_steps=unknown,
        smt_inconsistent_steps=smt_inconsistent,
        backend=backend,
        is_flawed=flawed > 0,
    )
