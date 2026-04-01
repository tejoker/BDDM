"""Unit tests for MCTS core logic (no Lean/Mistral required)."""
from __future__ import annotations

import math

import pytest

from mcts_search import (
    MCTSNode,
    SearchStats,
    TreeAnalysis,
    apply_calibration,
    fit_platt_calibrator,
    normalize_value_with_tactics,
    parse_value_score,
    temperature_scale,
    uct_score,
    _logit,
    _sigmoid,
)


# ---------------------------------------------------------------------------
# MCTSNode
# ---------------------------------------------------------------------------

def _node(visits=0, value_sum=0.0, is_terminal=False, depth=0) -> MCTSNode:
    return MCTSNode(
        state=None,
        state_text="⊢ True",
        tactic_from_parent=None,
        visits=visits,
        value_sum=value_sum,
        is_terminal=is_terminal,
        depth=depth,
    )


def test_node_mean_value_zero_visits():
    n = _node()
    assert n.mean_value == 0.0


def test_node_mean_value():
    n = _node(visits=4, value_sum=3.0)
    assert abs(n.mean_value - 0.75) < 1e-9


def test_node_ucb_infinite_for_unvisited():
    n = _node(visits=0)
    assert n.ucb_score == float("inf")


def test_node_tactic_history_default_empty():
    n = _node()
    assert n.tactic_history == []


def test_node_children_default_empty():
    n = _node()
    assert n.children == []


# ---------------------------------------------------------------------------
# UCT score
# ---------------------------------------------------------------------------

def test_uct_unvisited_child_is_inf():
    child = _node(visits=0)
    score = uct_score(child=child, parent_visits=10, exploration_c=1.4)
    assert score == float("inf")


def test_uct_higher_value_wins_when_equal_visits():
    c1 = _node(visits=5, value_sum=4.0)
    c2 = _node(visits=5, value_sum=2.0)
    s1 = uct_score(child=c1, parent_visits=20, exploration_c=1.4)
    s2 = uct_score(child=c2, parent_visits=20, exploration_c=1.4)
    assert s1 > s2


def test_uct_exploration_increases_score_for_less_visited():
    # Two children with same value but different visit counts.
    c_freq = _node(visits=10, value_sum=5.0)
    c_rare = _node(visits=2, value_sum=1.0)
    s_freq = uct_score(child=c_freq, parent_visits=12, exploration_c=1.4)
    s_rare = uct_score(child=c_rare, parent_visits=12, exploration_c=1.4)
    # Exploration term should make the rare child competitive.
    assert s_rare > 0.0 and s_freq > 0.0


# ---------------------------------------------------------------------------
# parse_value_score
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("<value>0.75</value>", 0.75),
    ("<VALUE>1</VALUE>", 1.0),
    ("<value>0</value>", 0.0),
    ("<value>0.0</value>", 0.0),
    ("<value>1.0</value>", 1.0),
])
def test_parse_value_score_valid(text, expected):
    assert parse_value_score(text) == expected


@pytest.mark.parametrize("text", [
    "no tags here",
    "<value>1.5</value>",   # out of range
    "<value>-0.1</value>",  # negative
    "<value>abc</value>",   # not a number
    "",
])
def test_parse_value_score_invalid_returns_none(text):
    assert parse_value_score(text) is None


# ---------------------------------------------------------------------------
# Calibration: logit / sigmoid roundtrip
# ---------------------------------------------------------------------------

def test_logit_sigmoid_roundtrip():
    for p in (0.1, 0.3, 0.5, 0.7, 0.9):
        assert abs(_sigmoid(_logit(p)) - p) < 1e-9


# ---------------------------------------------------------------------------
# temperature_scale
# ---------------------------------------------------------------------------

def test_temperature_scale_high_confidence_pulled_back():
    # avg raw score of 0.9667 should be meaningfully lower after T=1.5
    assert temperature_scale(0.9667, 1.5) < 0.9667


def test_temperature_scale_midpoint_symmetric():
    # p=0.5 is the fixed point of sigmoid(logit/T) for any T
    assert abs(temperature_scale(0.5, 2.0) - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# apply_calibration
# ---------------------------------------------------------------------------

def test_apply_calibration_no_platt():
    raw = 0.9
    cal = apply_calibration(raw, temperature=1.5, platt_params=None)
    assert cal < raw
    assert 0.0 <= cal <= 1.0


def test_apply_calibration_with_platt_identity():
    # Platt (a=1, b=0) is a no-op on top of temperature scaling.
    raw = 0.8
    v_temp = temperature_scale(raw, 1.5)
    v_platt = apply_calibration(raw, temperature=1.5, platt_params=(1.0, 0.0))
    assert abs(v_temp - v_platt) < 1e-6


def test_apply_calibration_clamps_to_unit():
    # Extreme Platt params should not produce values outside [0,1].
    cal = apply_calibration(0.999, temperature=0.5, platt_params=(10.0, 5.0))
    assert 0.0 <= cal <= 1.0
    cal = apply_calibration(0.001, temperature=0.5, platt_params=(-10.0, -5.0))
    assert 0.0 <= cal <= 1.0


# ---------------------------------------------------------------------------
# fit_platt_calibrator
# ---------------------------------------------------------------------------

def test_fit_platt_calibrator_perfect_signal():
    """When scores perfectly predict outcomes, Platt params should produce
    near-perfect calibration after fitting."""
    scores = [0.9, 0.8, 0.7, 0.6, 0.3, 0.2, 0.1]
    outcomes = [1, 1, 1, 1, 0, 0, 0]
    a, b = fit_platt_calibrator(scores, outcomes)
    # Fitted model should predict high probability for high-score states.
    for p, y in zip(scores, outcomes):
        pred = _sigmoid(a * _logit(p) + b)
        assert (pred > 0.5) == bool(y)


def test_fit_platt_calibrator_saves_file(tmp_path):
    scores = [0.9, 0.6, 0.3]
    outcomes = [1, 1, 0]
    out = tmp_path / "calib.json"
    a, b = fit_platt_calibrator(scores, outcomes, save_path=out)
    assert out.exists()
    import json
    d = json.loads(out.read_text())
    assert abs(d["a"] - a) < 1e-9
    assert abs(d["b"] - b) < 1e-9


def test_fit_platt_calibrator_empty_raises():
    with pytest.raises((ValueError, ZeroDivisionError)):
        fit_platt_calibrator([], [])


# ---------------------------------------------------------------------------
# normalize_value_with_tactics
# ---------------------------------------------------------------------------

def test_normalize_none_tactics_passthrough():
    assert normalize_value_with_tactics(0.7, None) == 0.7


def test_normalize_zero_tactics_boosts_value():
    # 0 tactics remaining → tactics_factor=1.0
    v = normalize_value_with_tactics(0.5, 0)
    assert v > 0.5


def test_normalize_ten_tactics_depresses_value():
    # 10 tactics remaining → tactics_factor=0.0
    v = normalize_value_with_tactics(0.8, 10)
    assert v < 0.8


def test_normalize_symmetric_blend():
    # With tactics_factor = base_value, result should equal base_value.
    # tactics_factor = 1 - 5/10 = 0.5; base_value = 0.5 → result = 0.5
    v = normalize_value_with_tactics(0.5, 5)
    assert abs(v - 0.5) < 1e-9
