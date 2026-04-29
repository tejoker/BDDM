from __future__ import annotations

from build_statement_repair_queue import build_statement_repair_queue


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "row_id": "r1",
        "arxiv_id": "2604.21314",
        "theorem_id": "bad",
        "canonical_theorem_id": "bad",
        "status": "FLAWED",
        "statement_alignment_class": "diagnostic",
        "alignment_confidence": 0.9,
        "alignment_gold_eligible": False,
        "claim_equivalence_verdict": "unclear",
        "identity_status": "unknown",
        "source_span_quality": "extractor_native",
        "source_span": {"source_file": "paper.tex", "start_byte": 1, "end_byte": 2},
        "alignment_evidence": {"source_match": {"match_status": "matched"}},
        "source_latex": "A nontrivial theorem.",
        "normalized_text": "A nontrivial theorem.",
        "lean_statement": "theorem bad : ∃ x : ℝ, x = x",
        "validation_gates": {},
        "gate_failures": [],
        "axiom_debt": [],
        "artifact_paths": {},
    }
    row.update(overrides)
    return row


def test_statement_repair_queue_prioritizes_placeholder_rows() -> None:
    queue, summary = build_statement_repair_queue([_row()])

    assert len(queue) == 1
    assert queue[0]["repair_kind"] == "replace_placeholder_statement"
    assert queue[0]["repair_route"] == "statement_regeneration"
    assert queue[0]["validity_primary_blocker"] == "translation_limited"
    assert queue[0]["alignment_evidence"]["source_match"]["match_status"] == "matched"
    assert "placeholder_or_trivial_lean_statement" in queue[0]["repair_reasons"]
    assert summary["repair_kind_counts"]["replace_placeholder_statement"] == 1
    assert summary["repair_route_counts"]["statement_regeneration"] == 1
    assert summary["validity_blocker_counts"]["translation_limited"] == 1


def test_statement_repair_queue_skips_reviewable_rows() -> None:
    queue, summary = build_statement_repair_queue(
        [
            _row(
                status="UNRESOLVED",
                statement_alignment_class="partial",
                lean_statement="theorem demo (n : Nat) : n = n",
            )
        ]
    )

    assert queue == []
    assert summary["rows"] == 0


def test_statement_repair_queue_routes_flawed_rows_by_validity() -> None:
    queue, summary = build_statement_repair_queue(
        [
            _row(
                lean_statement="theorem bad (n : Nat) : n = n",
                gate_failures=["semantic_policy_violation"],
            )
        ]
    )

    assert queue[0]["repair_kind"] == "regenerate_flawed_statement"
    assert queue[0]["repair_route"] == "statement_regeneration"
    assert queue[0]["validity_primary_blocker"] == "bad_translation_artifact"
    assert "semantic_policy_violation" in queue[0]["validity_reasons"]
    assert summary["repair_kind_counts"]["regenerate_flawed_statement"] == 1


def test_statement_repair_queue_routes_translation_limited_rows() -> None:
    queue, summary = build_statement_repair_queue(
        [
            _row(
                status="TRANSLATION_LIMITED",
                lean_statement="theorem limited : False",
            )
        ]
    )

    assert queue[0]["repair_kind"] == "recover_translation_limited_statement"
    assert queue[0]["validity_primary_blocker"] == "translation_limited"
    assert summary["validity_blocker_counts"]["translation_limited"] == 1


def test_statement_repair_queue_routes_source_span_repairs() -> None:
    queue, summary = build_statement_repair_queue(
        [
            _row(
                status="UNRESOLVED",
                statement_alignment_class="partial",
                lean_statement="theorem demo (n : Nat) : n = n",
                source_span_quality="string_recovered",
                alignment_evidence={"source_match": {"match_status": "matched"}},
            )
        ]
    )

    assert queue[0]["repair_kind"] == "repair_source_span_provenance"
    assert queue[0]["repair_route"] == "source_span_repair"
    assert summary["repair_route_counts"]["source_span_repair"] == 1
