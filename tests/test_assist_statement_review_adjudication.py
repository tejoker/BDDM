from __future__ import annotations

from assist_statement_review_adjudication import adjudication_blockers, build_assisted_reviews


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
