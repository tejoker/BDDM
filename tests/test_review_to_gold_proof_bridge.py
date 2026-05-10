from __future__ import annotations

from build_statement_review_batch import source_span_sha256
from run_review_to_gold_proof_bridge import _is_release_eligible_review, run_review_to_gold_bridge


def _batch_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_version": "statement_review_batch.v1",
        "row_id": "r1",
        "arxiv_id": "2604.21616",
        "theorem_id": "nuclear-l1-norms",
        "source_span_sha256": "abc123",
        "source_latex": r"For any matrix $\A$, we have $\|\A\|_*\leq \|\A\|_1$.",
        "lean_statement": "theorem nuclear_l1_norms (A : Matrix (Fin m) (Fin n) ℝ) : ‖A‖_* ≤ ‖A‖_1",
    }
    row.update(overrides)
    return row


def _corpus_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "row_id": "r1",
        "arxiv_id": "2604.21616",
        "theorem_id": "nuclear-l1-norms",
        "status": "UNRESOLVED",
        "lean_statement": "theorem nuclear_l1_norms (A : Matrix (Fin m) (Fin n) ℝ) : ‖A‖_* ≤ ‖A‖_1",
        "source_latex": r"For any matrix $\A$, we have $\|\A\|_*\leq \|\A\|_1$.",
        "source_span": {"source_file": "paper.tex", "start_byte": 1, "end_byte": 9},
        "source_span_quality": "extractor_native",
        "alignment_evidence": {"source_match": {"match_status": "matched"}},
        "statement_alignment_class": "partial",
        "alignment_confidence": 0.2,
        "alignment_review_required": True,
        "claim_equivalence_verdict": "unclear",
        "axiom_debt": [],
        "gate_failures": [],
        "artifact_paths": {"lean_file": "output/2604.21616.lean"},
    }
    row.update(overrides)
    return row


def test_review_to_gold_bridge_promotes_conservative_assisted_exact_rows() -> None:
    corpus = _corpus_row()
    batch = _batch_row(source_span_sha256=source_span_sha256(corpus))
    reviews, reviewed_rows, gold_queue, summary = run_review_to_gold_bridge(
        batch_rows=[batch],
        corpus_rows=[corpus],
        reviewed_at="2026-04-27T14:00:00Z",
    )

    assert len(reviews) == 1
    assert reviewed_rows[0]["alignment_gold_eligible"] is True
    assert len(gold_queue) == 1
    assert summary["assisted_reviewed_exact_rows"] == 1
    assert summary["gold_proof_queue_rows"] == 1


def test_review_to_gold_bridge_auto_llm_review_triggers_bridge_review() -> None:
    """Auto-LLM reviews are not release-eligible alone; the bridge generates a hybrid review on top."""
    corpus = _corpus_row()
    batch = _batch_row(source_span_sha256=source_span_sha256(corpus))
    auto_review = {
        "schema_version": "reviewed_statement_alignment.v1",
        "artifact_id": "auto:r1",
        "row_id": "r1",
        "source_span_sha256": source_span_sha256(corpus),
        "reviewed_statement_alignment_class": "exact",
        "reviewed_equivalence_verdict": "equivalent",
        "reviewed_alignment_confidence": 0.81,
        "reviewed_by": "auto_llm:alignment-review",
        "reviewer_type": "auto_llm",
        "reviewed_at": "2026-04-30T21:14:38Z",
        "reviewer_role": "stateless_reverse_translation_judge",
    }

    reviews, reviewed_rows, gold_queue, summary = run_review_to_gold_bridge(
        batch_rows=[batch],
        corpus_rows=[corpus],
        additional_reviews=[auto_review],
        reviewed_at="2026-04-30T21:15:00Z",
    )

    # Bridge DOES generate a new hybrid review for auto-LLM-only rows
    assert len(reviews) == 1
    assert reviews[0]["reviewed_by"] == "hybrid:conservative-assisted-review"
    assert reviewed_rows[0]["alignment_gold_eligible"] is True
    # Row is in gold queue via the bridge-generated hybrid review (passes fidelity gate)
    assert len(gold_queue) == 1
    assert summary["additional_reviews"] == 1
    assert summary["combined_reviews_used"] == 2
    assert summary["assisted_reviewed_exact_rows"] == 1
    assert summary["promoted_alignment_gold"] == 1


def test_review_to_gold_bridge_human_review_prevents_bridge_generation() -> None:
    """A human review already present prevents the bridge from generating a duplicate."""
    corpus = _corpus_row()
    batch = _batch_row(source_span_sha256=source_span_sha256(corpus))
    human_review = {
        "schema_version": "reviewed_statement_alignment.v1",
        "artifact_id": "human:r1",
        "row_id": "r1",
        "source_span_sha256": source_span_sha256(corpus),
        "reviewed_statement_alignment_class": "exact",
        "reviewed_equivalence_verdict": "equivalent",
        "reviewed_alignment_confidence": 0.95,
        "reviewed_by": "human:alice",
        "reviewed_at": "2026-04-30T21:14:38Z",
        "reviewer_role": "human_expert",
    }

    reviews, reviewed_rows, gold_queue, summary = run_review_to_gold_bridge(
        batch_rows=[batch],
        corpus_rows=[corpus],
        additional_reviews=[human_review],
        reviewed_at="2026-04-30T21:15:00Z",
    )

    assert reviews == []  # no new bridge review; human review is sufficient
    assert summary["assisted_reviewed_exact_rows"] == 0
    assert len(gold_queue) == 1


def test_is_release_eligible_review_classifies_correctly() -> None:
    assert _is_release_eligible_review({"reviewed_by": "hybrid:conservative-assisted-review"}) is True
    assert _is_release_eligible_review({"reviewed_by": "human:alice"}) is True
    assert _is_release_eligible_review({"reviewed_by": "auto_llm:alignment-review"}) is False
    assert _is_release_eligible_review({"reviewed_by": ""}) is False


def test_review_to_gold_bridge_auto_llm_admits_long_complex_statement() -> None:
    """Generalises the bridge to all future arxiv papers: when an auto-LLM review
    confidently confirms equivalence on a long/complex statement that mechanical
    heuristics would reject (size, token coverage, existence-claim phrases), the bridge
    must still admit it via the auto-alignment fast-path. The bridge merges the auto
    review's verdict onto the batch row so adjudication_blockers can see the LLM signal."""
    long_source = (
        r"\begin{lemma}\label{lem:long-yukawa}"
        + r"Let $\beta \in (3/2, 2]$, $0 < s < 1$, and $0 < s_1 \le s_2 < 1$. "
        r"Then there exists a constant $C > 0$ such that for any sequence "
        r"$(v_\beta(k))_{k \in \mathbb{Z}}$ with $\sum_k |v_\beta(k)|^2 < \infty$, "
        r"the inequality $\sum_{k \neq 0} \frac{|v_\beta(k)|^2}{|k|^{2 s_1 + 2 s_2}} "
        r"\le C \cdot \beta^{-1}$ holds uniformly in the parameters."
        + r"\end{lemma}"
    )
    long_lean = (
        "theorem lem_long_yukawa {beta : ℝ} {s s1 s2 : ℝ} {ℓ : ℤ} "
        "(hbeta : 3/2 < beta ∧ beta ≤ 2) (hs : 0 < s ∧ s < 1) "
        "(hs1 : 0 < s1 ∧ s1 ≤ s2 ∧ s2 < 1) "
        "(v_beta : ℤ → ℂ) (hv : Summable (fun k => ‖v_beta k‖^2)) : "
        "∑' k : ℤ, (if k ≠ 0 then ‖v_beta k‖^2 / (|k| ^ (2 * s1 + 2 * s2 : ℝ)) else 0) ≤ C * beta⁻¹"
    )
    corpus = _corpus_row(
        theorem_id="lem-long-yukawa",
        source_latex=long_source,
        lean_statement=long_lean,
    )
    batch = _batch_row(
        theorem_id="lem-long-yukawa",
        source_latex=long_source,
        lean_statement=long_lean,
        source_span_sha256=source_span_sha256(corpus),
    )
    auto_review = {
        "schema_version": "reviewed_statement_alignment.v1",
        "artifact_id": "auto:r1",
        "row_id": "r1",
        "source_span_sha256": source_span_sha256(corpus),
        "reviewed_statement_alignment_class": "exact",
        "reviewed_equivalence_verdict": "equivalent",
        "reviewed_alignment_confidence": 0.9,
        "reviewed_by": "auto_llm:alignment-review",
        "reviewer_type": "auto_llm",
    }

    reviews, reviewed_rows, gold_queue, summary = run_review_to_gold_bridge(
        batch_rows=[batch],
        corpus_rows=[corpus],
        additional_reviews=[auto_review],
        reviewed_at="2026-05-08T10:00:00Z",
    )

    assert len(reviews) == 1, (
        f"Auto-LLM-confirmed long statement should yield a bridge review; got {reviews}"
    )
    assert reviews[0]["reviewed_by"] == "hybrid:conservative-assisted-review"
    assert summary["assisted_reviewed_exact_rows"] == 1
    assert len(gold_queue) == 1, "Long auto-LLM-confirmed row should land in gold queue"


def test_review_to_gold_bridge_keeps_false_targets_out_of_gold_queue() -> None:
    reviews, reviewed_rows, gold_queue, summary = run_review_to_gold_bridge(
        batch_rows=[
            _batch_row(
                source_latex="For every n, n = n.",
                lean_statement="theorem false_target : False",
            )
        ],
        corpus_rows=[
            _corpus_row(
                source_latex="For every n, n = n.",
                lean_statement="theorem false_target : False",
            )
        ],
        reviewed_at="2026-04-27T14:00:00Z",
    )

    assert reviews == []
    assert reviewed_rows[0].get("alignment_gold_eligible") is not True
    assert gold_queue == []
    assert summary["gold_proof_queue_rows"] == 0
