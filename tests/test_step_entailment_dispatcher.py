"""Unit tests for routed entailment checking."""

from __future__ import annotations

import types

from step_entailment_checker import assess_step_entailment


def _ob(result: str, detail: str):
    return types.SimpleNamespace(result=result, detail=detail)


def test_explicit_lean_error_is_flawed():
    out = assess_step_entailment([_ob("lean-error", "some error")])
    assert out.is_flawed
    assert out.flawed_steps == 1
    assert out.route_counts.get("explicit_failure", 0) == 1


def test_quantified_steps_are_routed_to_lean_required():
    out = assess_step_entailment([_ob("state-advanced", "forall x, x = x")])
    assert out.unknown_steps == 1
    assert out.route_counts.get("lean_required_quantified", 0) == 1


def test_nonlinear_steps_are_not_misreported_as_z3_verified():
    out = assess_step_entailment([_ob("state-advanced", "x * y <= 3")])
    assert out.unknown_steps == 1
    assert out.route_counts.get("nonlinear_unhandled", 0) == 1


def test_linear_atoms_are_checked_or_marked_unknown_without_solver():
    out = assess_step_entailment([_ob("state-advanced", "x <= 3")])
    linear_checked = out.route_counts.get("linear_int_z3", 0) + out.route_counts.get("linear_real_z3", 0)
    linear_nosolver = out.route_counts.get("linear_int_no_solver", 0) + out.route_counts.get("linear_real_no_solver", 0)
    assert linear_checked + linear_nosolver == 1
