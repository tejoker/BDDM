from prove_with_ponder_exec import _goal_text_from_state_pp, _tactic_actionability_issue


def test_tactic_actionability_blocks_intro_n_noise() -> None:
    issue = _tactic_actionability_issue(state_pp="x : Nat\n⊢ True", tactic="introN")
    assert issue == "unsupported_introN"


def test_tactic_actionability_blocks_intro_n_inside_expression() -> None:
    issue = _tactic_actionability_issue(state_pp="x : Nat\n⊢ True", tactic="first | introN | simp")
    assert issue == "unsupported_introN"


def test_tactic_actionability_blocks_intro_on_non_binder_goal() -> None:
    issue = _tactic_actionability_issue(state_pp="x : Nat\n⊢ True", tactic="intro h")
    assert issue == "intro_on_non_binder_goal"


def test_tactic_actionability_allows_intro_on_implication_goal() -> None:
    issue = _tactic_actionability_issue(state_pp="⊢ P → Q", tactic="intro h")
    assert issue is None


def test_tactic_actionability_rejects_notation_arrow_false_positive() -> None:
    issue = _tactic_actionability_issue(state_pp="⊢ x →ₗ[R] y", tactic="intro h")
    assert issue == "intro_on_non_binder_goal"


def test_goal_text_strips_inline_comment_noise() -> None:
    state_pp = "h : P\n⊢ p_c1 ∧ p_c2 := by sorry -- theorem foo : A → B"
    goal = _goal_text_from_state_pp(state_pp)
    assert goal == "p_c1 ∧ p_c2 := by sorry"


def test_tactic_actionability_blocks_rfl_on_non_reflexive_goal() -> None:
    issue = _tactic_actionability_issue(state_pp="x y : Nat\n⊢ x = y", tactic="rfl")
    assert issue == "rfl_on_non_reflexive_goal"


def test_tactic_actionability_allows_rfl_on_reflexive_goal() -> None:
    issue = _tactic_actionability_issue(state_pp="x : Nat\n⊢ x = x", tactic="rfl")
    assert issue is None


def test_tactic_actionability_blocks_embedded_rfl_on_non_reflexive_goal() -> None:
    issue = _tactic_actionability_issue(state_pp="x y : Nat\n⊢ x = y", tactic="first | rfl | aesop")
    assert issue == "rfl_on_non_reflexive_goal"


def test_tactic_actionability_blocks_assumption_without_matching_hypothesis() -> None:
    issue = _tactic_actionability_issue(state_pp="h : P\n⊢ Q", tactic="assumption")
    assert issue == "assumption_disabled_policy"


def test_tactic_actionability_allows_assumption_with_matching_hypothesis() -> None:
    issue = _tactic_actionability_issue(state_pp="hQ : Q\n⊢ Q", tactic="assumption")
    assert issue == "assumption_disabled_policy"


def test_tactic_actionability_blocks_compound_assumption_even_with_matching_hypothesis() -> None:
    issue = _tactic_actionability_issue(state_pp="hQ : Q\n⊢ Q", tactic="first | assumption | aesop")
    assert issue == "assumption_disabled_policy"


def test_tactic_actionability_blocks_aesop_on_hard_goal() -> None:
    issue = _tactic_actionability_issue(state_pp="hP : P\nhQ : Q\n⊢ P ∧ Q", tactic="aesop")
    assert issue == "hard_goal_auto_tactic_disabled"
