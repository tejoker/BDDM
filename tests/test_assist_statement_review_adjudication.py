from __future__ import annotations

from assist_statement_review_adjudication import (
    _relation_compatible,
    _relation_count,
    _tokens,
    adjudication_blockers,
    build_assisted_reviews,
)


def _item(**overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "schema_version": "statement_review_batch.v1",
        "row_id": "r1",
        "arxiv_id": "2604.21616",
        "theorem_id": "nuclear-l1-norms",
        "source_span_sha256": "abc123",
        "source_latex": r"\label{nuclear-l1-norms} For any matrix $\A$, we have $\|\A\|_*\leq \|\A\|_1$.",
        "lean_statement": "theorem nuclear_l1_norms (A : Matrix (Fin m) (Fin n) ℝ) : ‖A‖_* ≤ ‖A‖_1",
    }
    item.update(overrides)
    return item


def test_assisted_review_accepts_short_relation_preserving_statement() -> None:
    reviews, summary = build_assisted_reviews([_item()], reviewed_at="2026-04-27T14:00:00Z")

    assert len(reviews) == 1
    assert reviews[0]["reviewed_statement_alignment_class"] == "exact"
    assert reviews[0]["reviewed_equivalence_verdict"] == "equivalent"
    assert reviews[0]["source_span_sha256"] == "abc123"
    assert summary["assisted_reviewed_exact_rows"] == 1


def test_assisted_review_rejects_missing_chain_equality() -> None:
    item = _item(
        theorem_id="EqualLN",
        source_latex=(
            r"If $L_{\tilde{\alpha}} = n_{\alpha}$, $\alpha \leq \beta$ and "
            r"$\tilde{\alpha} \leq \tilde{\beta}$, then "
            r"$n_{\alpha} = n_{\beta} = L_{\tilde{\alpha}} = L_{\tilde{\beta}}$."
        ),
        lean_statement=(
            "theorem EqualLN (alpha beta : Multisegment) : "
            "n_alpha alpha = n_alpha beta ∧ L_tilde alpha = L_tilde beta"
        ),
    )

    blockers = adjudication_blockers(item)
    reviews, summary = build_assisted_reviews([item], reviewed_at="2026-04-27T14:00:00Z")

    assert "primary_relation_not_preserved" in blockers
    assert reviews == []
    assert summary["blocked_rows"] == 1


def test_assisted_review_skips_existing_reviews() -> None:
    reviews, summary = build_assisted_reviews(
        [_item()],
        existing_reviews=[{"row_id": "r1"}],
        reviewed_at="2026-04-27T14:00:00Z",
    )

    assert reviews == []
    assert summary["skipped_existing_reviews"] == 1


def test_assisted_review_rejects_false_target_rows() -> None:
    item = _item(
        theorem_id="false-target",
        source_latex="For every n, n = n.",
        lean_statement="theorem false_target : False",
    )

    blockers = adjudication_blockers(item)
    reviews, summary = build_assisted_reviews([item], reviewed_at="2026-04-27T14:00:00Z")

    assert "lean_contains_placeholder_or_ungrounded_shape" in blockers
    assert reviews == []
    assert summary["blocked_rows"] == 1


def test_tokens_transliterates_unicode_greek_to_match_latex() -> None:
    """Unicode Greek in Lean (α, β) should produce the same tokens as LaTeX \\alpha, \\beta."""
    assert "alpha" in _tokens("α = β")
    assert "beta" in _tokens("α = β")
    assert _tokens("α") == _tokens(r"\alpha")
    assert _tokens("β") == _tokens(r"\beta")
    assert _tokens("γ") == _tokens(r"\gamma")


def test_relation_count_includes_strict_inequalities() -> None:
    assert _relation_count("a < b") == 1
    assert _relation_count("a > b") == 1
    assert _relation_count("a < b and c > d") == 2
    assert _relation_count(r"\lt x") == 1
    assert _relation_count(r"\gt x") == 1
    assert _relation_count("a ≤ b") == 1
    assert _relation_count("") == 0


def test_relation_compatible_vacuously_true_when_source_has_no_operator() -> None:
    """Source with no relational operator → compatible regardless of Lean content."""
    assert _relation_compatible("A is positive definite", "IsPosDef A") is True
    assert _relation_compatible("", "theorem foo : True") is True
    assert _relation_compatible("The set is non-empty.", "theorem foo : s.Nonempty") is True


def test_adjudication_no_false_block_for_strict_inequality_source() -> None:
    """Source with < only (no ≤/=) used to get source_count=0 → return False.  After fix,
    _relation_count detects < and _relation_compatible returns True when counts balance."""
    item = _item(
        theorem_id="strict-ineq",
        source_latex=r"If $0 < \alpha < 1$ then $f(\alpha) < 1$.",
        lean_statement="theorem strict_ineq (α : ℝ) (h1 : 0 < α) (h2 : α < 1) : f α < 1",
    )
    blockers = adjudication_blockers(item)
    assert "primary_relation_not_preserved" not in blockers


def test_adjudication_greek_unicode_lean_aligns_with_latex_source() -> None:
    """Lean statement using Unicode α/β should have sufficient token coverage against LaTeX source."""
    item = _item(
        theorem_id="greek-eq",
        source_latex=r"$\alpha = \beta$",
        lean_statement="theorem greek_eq : α = β",
    )
    blockers = adjudication_blockers(item)
    assert "token_coverage_below_assisted_review_threshold" not in blockers


def test_auto_alignment_fast_path_admits_long_statement_with_low_token_coverage() -> None:
    """Generalises the bridge to all future arxiv papers: when an automated alignment
    review has confirmed equivalence at high confidence, the assisted-review fast-path
    must accept the row even if it would otherwise fail mechanical heuristics like the
    360-char source limit, the 62% token-coverage floor, or the existence-claim blocker.
    Only safety checks (Lean placeholder, missing source/sha256) still block."""
    long_source = (
        r"\begin{lemma}\label{lem:long}"
        + r"Let $\beta \in (3/2, 2]$, $0 < s < 1$, and $0 < s_1 \le s_2 < 1$. "
        r"Then there exists a constant $C$ such that for any sequence $(v_\beta(k))_{k \in \Z}$ "
        r"the inequality $\sum_{k} \frac{|v_\beta(k)|^2}{|k|^{2 s_1 + 2 s_2}} \le C \cdot \beta^{-1}$ "
        r"holds uniformly in the parameters." * 2
        + r"\end{lemma}"
    )
    long_lean = (
        "theorem lem_long {beta : ℝ} {s s1 s2 : ℝ} {ℓ : ℤ} "
        "(hbeta : 3/2 < beta ∧ beta ≤ 2) (hs : 0 < s ∧ s < 1) "
        "(hs1 : 0 < s1 ∧ s1 ≤ s2 ∧ s2 < 1) : ∑' k : ℤ, "
        "(if k ≠ 0 then ‖(v_beta k)‖ ^ 2 / (|k| ^ (2 * s1 + 2 * s2)) else 0) ≤ Cbeta * beta⁻¹"
    )
    item = _item(
        theorem_id="lem-long",
        source_latex=long_source,
        lean_statement=long_lean,
        # Auto-alignment review fields the bridge merges in:
        reviewed_equivalence_verdict="equivalent",
        reviewed_statement_alignment_class="exact",
        reviewed_alignment_confidence=0.9,
    )
    blockers = adjudication_blockers(item)
    assert blockers == [], f"auto-aligned row should not be blocked, got: {blockers}"
    reviews, _ = build_assisted_reviews([item])
    assert len(reviews) == 1


def test_auto_alignment_fast_path_still_blocks_lean_placeholder() -> None:
    """Even with auto-alignment confirmation, a row whose Lean is a `False` placeholder
    or other ungrounded shape must still be blocked — safety floor must not be bypassed."""
    item = _item(
        theorem_id="placeholder",
        lean_statement="theorem placeholder : False",
        reviewed_equivalence_verdict="equivalent",
        reviewed_statement_alignment_class="exact",
        reviewed_alignment_confidence=0.95,
    )
    blockers = adjudication_blockers(item)
    assert "lean_contains_placeholder_or_ungrounded_shape" in blockers


def test_auto_alignment_fast_path_requires_high_confidence() -> None:
    """A low-confidence auto-alignment review must NOT activate the fast-path."""
    item = _item(
        theorem_id="lowconf",
        source_latex=r"\begin{lemma} " + ("Lorem ipsum dolor sit amet. " * 30) + r"\end{lemma}",
        lean_statement="theorem lowconf " + ("(x_" + str(0) + " : ℕ) " * 80) + ": True",
        reviewed_equivalence_verdict="equivalent",
        reviewed_statement_alignment_class="exact",
        reviewed_alignment_confidence=0.50,
    )
    blockers = adjudication_blockers(item)
    # Should hit mechanical blockers because low confidence prevents fast-path.
    assert "statement_too_large_for_assisted_exact_review" in blockers
