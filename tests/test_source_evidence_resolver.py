from __future__ import annotations

from source_evidence_resolver import resolve_evidence_row


def test_resolver_uses_source_text_to_break_label_collision() -> None:
    selected, evidence = resolve_evidence_row(
        paper_id="2300.00001",
        ledger_row={
            "theorem_name": "demo",
            "provenance": {"label": "thm:foo"},
            "semantic_equivalence_artifact": {"original_latex_theorem": "First statement."},
        },
        source_latex="First statement.",
        evidence_rows=[
            {"name": "thm:foo", "statement": "First statement."},
            {"name": "thm_foo", "statement": "Second statement."},
        ],
    )

    assert selected["statement"] == "First statement."
    assert evidence["match_status"] == "matched"
    assert evidence["match_method"] == "scored_evidence_resolver"
    assert evidence["top_score"] > evidence["runner_up_score"]


def test_resolver_keeps_true_ties_for_review() -> None:
    selected, evidence = resolve_evidence_row(
        paper_id="2300.00001",
        ledger_row={"provenance": {"label": "thm:foo"}},
        source_latex="Duplicated statement.",
        evidence_rows=[
            {"name": "thm:foo", "statement": "Duplicated statement."},
            {"name": "thm_foo", "statement": "Duplicated statement."},
        ],
    )

    assert selected == {}
    assert evidence["match_status"] == "ambiguous"
    assert evidence["reason"] == "top_candidate_margin_too_small"
    assert evidence["diagnostics"]["tied_top_count"] == 2


def test_resolver_requires_strong_score_before_matching() -> None:
    selected, evidence = resolve_evidence_row(
        paper_id="2300.00001",
        ledger_row={"theorem_name": "demo"},
        source_latex="A long source statement that only weakly contains part of a candidate.",
        evidence_rows=[
            {"name": "other", "statement": "part of a candidate"},
        ],
    )

    assert selected == {}
    assert evidence["match_status"] == "ambiguous"
    assert evidence["reason"] == "top_score_below_threshold"


def test_resolver_reports_wanted_kind_diagnostics() -> None:
    _selected, evidence = resolve_evidence_row(
        paper_id="2300.00001",
        ledger_row={"theorem_name": "demo", "kind": "theorem"},
        source_latex="",
        evidence_rows=[
            {"name": "demo", "kind": "lemma", "statement": "A."},
            {"name": "demo", "kind": "theorem", "statement": "A."},
        ],
        min_score=1,
        min_margin=200,
    )

    assert evidence["match_status"] == "ambiguous"
    assert evidence["diagnostics"]["wanted_kinds"] == ["theorem"]
    assert evidence["candidate_scores"][0]["reasons"] == ["name_or_label_exact", "kind_exact"]
