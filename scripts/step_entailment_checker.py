#!/usr/bin/env python3
"""Optional per-step entailment checker.

This module supports an SMT-backed consistency check (z3) for arithmetic
constraints seen in step traces. When z3 is unavailable, it falls back to a
conservative signal-only mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

try:
    import z3  # type: ignore[import]
except Exception:  # pragma: no cover - optional dependency
    z3 = None


_NUMBER = r"-?\d+(?:\.\d+)?"
_ATOM_RE = re.compile(rf"\b([A-Za-z_][A-Za-z0-9_]*)\s*(<=|>=|!=|=|<|>)\s*({_NUMBER})\b")
_NONLINEAR_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\*\s*[A-Za-z_][A-Za-z0-9_]*\b|\^")
_QUANTIFIER_RE = re.compile(r"\b(forall|exists|∃|∀|fun|λ)\b", re.IGNORECASE)
_HIGHER_ORDER_RE = re.compile(r"\b(Type|Sort|Prop|Set|Subtype|Filter|Measurable|Continuous)\b")


@dataclass
class EntailmentAssessment:
    checked_steps: int
    flawed_steps: int
    unknown_steps: int
    smt_inconsistent_steps: int
    backend: str
    is_flawed: bool
    route_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class ConstraintAtom:
    variable: str
    op: str
    value: float
    sort: str  # "int" | "real"


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
        raw_value = m.group(3)
        sort = "real" if "." in raw_value else "int"
        atoms.append(
            ConstraintAtom(
                variable=m.group(1),
                op=m.group(2),
                value=float(raw_value),
                sort=sort,
            )
        )
    return atoms


def _dispatch_route(detail: str, result: str) -> str:
    """Route a step to the best available checker without manual per-field indexing."""
    r = (result or "").strip().lower()
    if r in {"lean-error", "proof-given-up"}:
        return "explicit_failure"

    if not detail:
        return "heuristic_no_detail"

    normalized = (
        detail.replace("≤", "<=")
        .replace("≥", ">=")
        .replace("≠", "!=")
    )

    if _QUANTIFIER_RE.search(normalized):
        return "lean_required_quantified"
    if _HIGHER_ORDER_RE.search(normalized):
        return "lean_required_higher_order"
    if _NONLINEAR_RE.search(normalized):
        return "nonlinear_unhandled"

    atoms = _extract_atoms(normalized)
    if not atoms:
        return "heuristic_no_atoms"
    if any(a.sort == "real" for a in atoms):
        return "linear_real_z3" if z3 is not None else "linear_real_no_solver"
    return "linear_int_z3" if z3 is not None else "linear_int_no_solver"


def _build_z3_expr(atom: ConstraintAtom, vars_map: dict[str, Any], force_real: bool):
    if atom.variable not in vars_map:
        vars_map[atom.variable] = z3.Real(atom.variable) if force_real else z3.Int(atom.variable)
    v = vars_map[atom.variable]
    c = z3.RealVal(str(atom.value)) if force_real else z3.IntVal(int(atom.value))
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
    force_real = any(a.sort == "real" for a in atoms)
    solver = z3.Solver()
    vars_map: dict[str, Any] = {}
    for atom in atoms:
        expr = _build_z3_expr(atom, vars_map, force_real=force_real)
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
    backend = "dispatcher+z3" if z3 is not None else "dispatcher+heuristic"
    accumulated_atoms: list[ConstraintAtom] = []
    route_counts: dict[str, int] = {}

    def _bump(route: str) -> None:
        route_counts[route] = route_counts.get(route, 0) + 1

    for s in step_obligations:
        result = str(getattr(s, "result", "") or "").strip().lower()
        detail = str(getattr(s, "detail", "") or "")
        route = _dispatch_route(detail, result)
        _bump(route)

        if route == "explicit_failure":
            checked += 1
            flawed += 1
            continue

        if route in {
            "heuristic_no_detail",
            "heuristic_no_atoms",
            "linear_int_no_solver",
            "linear_real_no_solver",
            "nonlinear_unhandled",
            "lean_required_quantified",
            "lean_required_higher_order",
        }:
            unknown += 1
            continue

        atoms = _extract_atoms(detail)
        if not atoms:
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
        route_counts=route_counts,
    )


import types


def parse_proof_draft_to_obligations(proof_text: str) -> list[dict]:
    obligations = []
    i = 0
    for line in proof_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("--"):
            continue
        obligations.append({
            "step_index": i,
            "tactic": stripped,
            "result": "pending",
            "detail": "",
            "verified": False,
        })
        i += 1
    return obligations


def assess_proof_draft(proof_text: str) -> EntailmentAssessment:
    dicts = parse_proof_draft_to_obligations(proof_text)
    obligations = [types.SimpleNamespace(**d) for d in dicts]
    return assess_step_entailment(obligations)
