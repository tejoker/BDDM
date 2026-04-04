"""miniF2F regression test set.

These 5 problems were solved by state-MCTS (v2 benchmark, 2026-04-05).
They serve as a regression guard: if state-MCTS regresses, these should fail.

Requires:
- A working Lean 4 / lake installation with the repl package built
- MISTRAL_API_KEY set in environment or .env
- DESOL_RUN_REGRESSION=1 to opt in (skipped otherwise to keep CI fast)

Run with:
    DESOL_RUN_REGRESSION=1 pytest tests/test_regression_minif2f.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

pytestmark = pytest.mark.skipif(
    os.environ.get("DESOL_RUN_REGRESSION") != "1",
    reason="Set DESOL_RUN_REGRESSION=1 to run miniF2F regression tests",
)

# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client_and_model():
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.environ.get("MISTRAL_API_KEY", "")
    if not api_key:
        pytest.skip("MISTRAL_API_KEY not set")
    from mistralai.client import Mistral
    return Mistral(api_key=api_key), "labs-leanstral-2603"


def _run(stmt: str, client, model, iterations: int = 30) -> tuple[bool, list[str]]:
    from mcts_search import run_state_mcts
    ok, tactics, _ = run_state_mcts(
        project_root=PROJECT_ROOT,
        theorem_statement=stmt,
        client=client,
        model=model,
        iterations=iterations,
        n_tactics=4,
        max_depth=8,
        repl_timeout=90.0,
        kg_write_on_success=False,
    )
    return ok, tactics


# ── regression problems ───────────────────────────────────────────────────────
# Each was solved by state-MCTS v2 on 2026-04-05.
# The known-good proof is provided as a reference (actual run may find a different valid proof).

REGRESSION_CASES = [
    pytest.param(
        "theorem mathd_algebra_478\n"
        "  (b h v : ℝ)\n"
        "  (h₀ : 0 < b ∧ 0 < h ∧ 0 < v)\n"
        "  (h₁ : v = 1 / 3 * (b * h))\n"
        "  (h₂ : b = 30)\n"
        "  (h₃ : h = 13 / 2) :\n"
        "  v = 65 := sorry",
        "nlinarith [h₀.1, h₀.2.1, h₀.2.2]",
        id="mathd_algebra_478",
    ),
    pytest.param(
        "theorem mathd_algebra_141\n"
        "  (a b : ℝ)\n"
        "  (h₁ : (a * b)=180)\n"
        "  (h₂ : 2 * (a + b)=54) :\n"
        "  (a^2 + b^2) = 369 := sorry",
        "nlinarith [sq_nonneg (a - b), sq_nonneg (a + b)]",
        id="mathd_algebra_141",
    ),
    pytest.param(
        "theorem mathd_numbertheory_1124\n"
        "  (n : ℕ)\n"
        "  (h₀ : n ≤ 9)\n"
        "  (h₁ : 18∣374 * 10 + n) :\n"
        "  n = 4 := sorry",
        "omega",
        id="mathd_numbertheory_1124",
    ),
    pytest.param(
        "theorem mathd_numbertheory_299 :\n"
        "  (1 * 3 * 5 * 7 * 9 * 11 * 13) % 10 = 5 := sorry",
        "norm_num",
        id="mathd_numbertheory_299",
    ),
    pytest.param(
        "theorem mathd_algebra_33\n"
        "  (x y z : ℝ)\n"
        "  (h₀ : x ≠ 0)\n"
        "  (h₁ : 2 * x = 5 * y)\n"
        "  (h₂ : 7 * y = 10 * z) :\n"
        "  z / x = 7 / 25 := sorry",
        "field_simp at *\nlinarith",
        id="mathd_algebra_33",
    ),
]


@pytest.mark.parametrize("stmt,known_proof", REGRESSION_CASES)
def test_regression(stmt, known_proof, client_and_model):
    """State-MCTS must still solve each regression problem."""
    client, model = client_and_model
    ok, tactics = _run(stmt, client, model)
    assert ok, (
        f"Regression failure: state-MCTS could not solve this problem.\n"
        f"Known-good proof: {known_proof!r}\n"
        f"Partial tactics found: {tactics}"
    )
