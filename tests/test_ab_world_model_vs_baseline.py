from __future__ import annotations

from ab_world_model_vs_baseline import evaluate_ab


def test_evaluate_ab_counts_and_pvalue() -> None:
    payload = {
        "count": 3,
        "delta_grounded": 1,
        "rows": [
            {"world_model": {"grounded_count": 2}, "baseline_text_bridge": {"grounded_count": 1}},
            {"world_model": {"grounded_count": 0}, "baseline_text_bridge": {"grounded_count": 1}},
            {"world_model": {"grounded_count": 1}, "baseline_text_bridge": {"grounded_count": 1}},
        ],
    }
    out = evaluate_ab(payload)
    assert out["wins_world_model"] == 1
    assert out["losses_world_model"] == 1
    assert out["ties"] == 1
    assert 0.0 <= out["sign_test_pvalue"] <= 1.0
