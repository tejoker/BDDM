from __future__ import annotations

import json
from pathlib import Path

import run_auto_alignment_review as auto


def _batch_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_version": "statement_review_batch.v1",
        "row_id": "r1",
        "arxiv_id": "2604.21616",
        "theorem_id": "nuclear-l1-norms",
        "source_span_sha256": "span123",
        "source_latex": r"For any matrix $A$, $\|A\|_* \leq \|A\|_1$.",
        "lean_statement": "theorem nuclear_l1_norms (A : Matrix (Fin m) (Fin n) ℝ) : ‖A‖_* ≤ ‖A‖_1",
        "current_statement_alignment_class": "partial",
        "claim_equivalence_verdict": "unclear",
        "alignment_evidence": {"source_match": {"match_status": "matched"}},
        "validation_gates": {"translation_fidelity_ok": False},
    }
    row.update(overrides)
    return row


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_parse_structured_judge_response_records_component_scores() -> None:
    parsed = auto._parse_structured_judge_response(
        json.dumps(
            {
                "verdict": "EQUIVALENT",
                "alignment_class": "reviewed_exact",
                "confidence": 0.92,
                "component_scores": {
                    "hypotheses": 0.9,
                    "conclusion": 0.91,
                    "quantifiers": 0.88,
                    "objects": 0.89,
                    "relation": 0.93,
                },
                "blockers": [],
                "reason": "same inequality",
            }
        )
    )

    assert parsed["protocol"] == "structured_json"
    assert parsed["alignment_class"] == "reviewed_exact"
    assert parsed["component_scores"]["relation"] == 0.93
    assert auto._promotion_blockers(parsed, confidence_threshold=0.84, component_threshold=0.78) == []


def test_structured_review_requires_deflated_release_confidence() -> None:
    parsed = auto._parse_structured_judge_response(
        json.dumps(
            {
                "verdict": "EQUIVALENT",
                "alignment_class": "reviewed_exact",
                "confidence": 0.82,
                "component_scores": {key: 0.95 for key in auto.COMPONENT_KEYS},
                "blockers": [],
                "reason": "same statement",
            }
        )
    )

    blockers = auto._promotion_blockers(parsed, confidence_threshold=0.80, component_threshold=0.78)

    assert "deflated_confidence_below_release_threshold" in blockers


def test_component_low_becomes_actionable_triage_reason() -> None:
    row = _batch_row()
    report = auto.build_alignment_triage_report(
        [row],
        decisions=[
            {
                "row_id": "r1",
                "decision": "partial",
                "blockers": ["component_score_low:hypotheses", "component_score_low:conclusion"],
            }
        ],
    )

    assert report["decision_counts"]["partial"] == 1
    assert report["triage_reason_counts"]["missing_or_changed_hypotheses"] == 1
    assert report["triage_reason_counts"]["conclusion_mismatch"] == 1


def test_run_auto_alignment_review_writes_promotable_reviews_and_triage(monkeypatch, tmp_path: Path) -> None:
    batch = tmp_path / "batch.jsonl"
    out_reviews = tmp_path / "reviews.jsonl"
    out_summary = tmp_path / "summary.json"
    out_triage = tmp_path / "triage.json"
    _write_jsonl(batch, [_batch_row()])

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    monkeypatch.setattr(auto, "Mistral", lambda api_key: object())
    monkeypatch.setattr(auto, "_reverse_translate", lambda client, model, lean: "For any matrix A, its nuclear norm is at most its l1 norm.")
    monkeypatch.setattr(
        auto,
        "_judge_equivalence",
        lambda client, model, source, reverse: {
            "protocol": "structured_json",
            "verdict": "EQUIVALENT",
            "alignment_class": "reviewed_exact",
            "confidence": 0.9,
            "component_scores": {key: 0.88 for key in auto.COMPONENT_KEYS},
            "blockers": [],
            "reason": "same inequality",
        },
    )

    summary = auto.run_auto_alignment_review(
        batch_jsonl=batch,
        out_reviews=out_reviews,
        out_summary=out_summary,
        out_triage=out_triage,
        model="test-model",
        confidence_threshold=0.84,
        component_threshold=0.78,
        rate_delay=0.0,
    )

    reviews = [json.loads(line) for line in out_reviews.read_text(encoding="utf-8").splitlines()]
    triage = json.loads(out_triage.read_text(encoding="utf-8"))

    assert summary["promoted_reviews"] == 1
    assert reviews[0]["reviewed_alignment_confidence"] == 0.81
    assert reviews[0]["reviewed_statement_alignment_class"] == "exact"
    assert triage["decision_counts"]["reviewed_exact"] == 1


def test_run_auto_alignment_review_keeps_partial_out_of_reviews(monkeypatch, tmp_path: Path) -> None:
    batch = tmp_path / "batch.jsonl"
    out_reviews = tmp_path / "reviews.jsonl"
    out_summary = tmp_path / "summary.json"
    out_triage = tmp_path / "triage.json"
    _write_jsonl(batch, [_batch_row()])

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    monkeypatch.setattr(auto, "Mistral", lambda api_key: object())
    monkeypatch.setattr(auto, "_reverse_translate", lambda client, model, lean: "A related but weaker statement.")
    monkeypatch.setattr(
        auto,
        "_judge_equivalence",
        lambda client, model, source, reverse: {
            "protocol": "structured_json",
            "verdict": "EQUIVALENT",
            "alignment_class": "partial",
            "confidence": 0.91,
            "component_scores": {**{key: 0.9 for key in auto.COMPONENT_KEYS}, "conclusion": 0.5},
            "blockers": ["weaker_conclusion"],
            "reason": "conclusion is weaker",
        },
    )

    summary = auto.run_auto_alignment_review(
        batch_jsonl=batch,
        out_reviews=out_reviews,
        out_summary=out_summary,
        out_triage=out_triage,
        model="test-model",
        confidence_threshold=0.84,
        component_threshold=0.78,
        rate_delay=0.0,
    )

    assert summary["promoted_reviews"] == 0
    assert out_reviews.read_text(encoding="utf-8") == ""
    triage = json.loads(out_triage.read_text(encoding="utf-8"))
    assert triage["decision_counts"]["partial"] == 1
    assert triage["triage_reason_counts"]["conclusion_mismatch"] == 1


def test_run_auto_alignment_review_dry_run_writes_no_artifacts(monkeypatch, tmp_path: Path) -> None:
    batch = tmp_path / "batch.jsonl"
    out_reviews = tmp_path / "reviews.jsonl"
    out_summary = tmp_path / "summary.json"
    out_triage = tmp_path / "triage.json"
    _write_jsonl(batch, [_batch_row()])

    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setattr(auto, "Mistral", None)

    summary = auto.run_auto_alignment_review(
        batch_jsonl=batch,
        out_reviews=out_reviews,
        out_summary=out_summary,
        out_triage=out_triage,
        model="test-model",
        confidence_threshold=0.84,
        component_threshold=0.78,
        rate_delay=0.0,
        dry_run=True,
    )

    assert summary["dry_run"] is True
    assert summary["non_promotable"] is True
    assert not out_reviews.exists()
    assert not out_summary.exists()
    assert not out_triage.exists()


def test_common_math_words_do_not_skip_alignment_review() -> None:
    row = _batch_row(
        source_latex="Consequently, the cutoff estimate holds for the algorithmic sequence.",
        lean_statement="theorem cutoff_estimate (n : Nat) : n = n",
    )

    assert auto._should_skip(row) is None


def test_judge_equivalence_retries_on_legacy_text_output(monkeypatch) -> None:
    calls: list[str] = []

    def fake_chat(client, model, messages, max_tokens=512):
        calls.append(messages[0]["content"][:20])
        if len(calls) == 1:
            return "EQUIVALENT 0.9 REASON: same"  # legacy_text format
        return json.dumps({
            "verdict": "EQUIVALENT",
            "alignment_class": "reviewed_exact",
            "confidence": 0.91,
            "component_scores": {key: 0.88 for key in auto.COMPONENT_KEYS},
            "blockers": [],
            "reason": "same inequality",
        })

    monkeypatch.setattr(auto, "_chat", fake_chat)
    judge = auto._judge_equivalence(object(), "test-model", "source", "reconstructed")

    assert judge["protocol"] == "structured_json"
    assert judge["retried"] is True
    assert len(calls) == 2


def test_judge_equivalence_accepts_first_call_when_structured(monkeypatch) -> None:
    calls: list[str] = []

    def fake_chat(client, model, messages, max_tokens=512):
        calls.append("called")
        return json.dumps({
            "verdict": "EQUIVALENT",
            "alignment_class": "reviewed_exact",
            "confidence": 0.88,
            "component_scores": {key: 0.85 for key in auto.COMPONENT_KEYS},
            "blockers": [],
            "reason": "match",
        })

    monkeypatch.setattr(auto, "_chat", fake_chat)
    judge = auto._judge_equivalence(object(), "test-model", "source", "reconstructed")

    assert judge["protocol"] == "structured_json"
    assert not judge.get("retried")
    assert len(calls) == 1


def test_judge_equivalence_returns_legacy_text_when_both_calls_malformed(monkeypatch) -> None:
    monkeypatch.setattr(auto, "_chat", lambda *a, **kw: "not json at all")
    judge = auto._judge_equivalence(object(), "test-model", "source", "reconstructed")
    assert judge["protocol"] == "legacy_text"


def test_emitted_review_has_auto_llm_reviewer_type(monkeypatch, tmp_path: Path) -> None:
    batch = tmp_path / "batch.jsonl"
    out_reviews = tmp_path / "reviews.jsonl"
    out_summary = tmp_path / "summary.json"
    out_triage = tmp_path / "triage.json"
    _write_jsonl(batch, [_batch_row()])

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    monkeypatch.setattr(auto, "Mistral", lambda api_key: object())
    monkeypatch.setattr(auto, "_reverse_translate", lambda client, model, lean: "nuclear norm at most l1 norm")
    monkeypatch.setattr(
        auto,
        "_judge_equivalence",
        lambda client, model, source, reverse: {
            "protocol": "structured_json",
            "verdict": "EQUIVALENT",
            "alignment_class": "reviewed_exact",
            "confidence": 0.9,
            "component_scores": {key: 0.88 for key in auto.COMPONENT_KEYS},
            "blockers": [],
            "reason": "same inequality",
        },
    )

    auto.run_auto_alignment_review(
        batch_jsonl=batch,
        out_reviews=out_reviews,
        out_summary=out_summary,
        out_triage=out_triage,
        model="test-model",
        confidence_threshold=0.84,
        component_threshold=0.78,
        rate_delay=0.0,
    )

    reviews = [json.loads(line) for line in out_reviews.read_text(encoding="utf-8").splitlines()]
    assert reviews[0]["reviewer_type"] == "auto_llm"
    assert "auto_llm:alignment-review" in reviews[0]["reviewed_by"]
    assert reviews[0]["_auto_meta"]["proof_release_eligible"] is False


def test_triage_maps_judge_output_malformed_blocker() -> None:
    row = _batch_row()
    report = auto.build_alignment_triage_report(
        [row],
        decisions=[
            {
                "row_id": "r1",
                "decision": "needs_human",
                "blockers": ["judge_output_not_structured_json"],
            }
        ],
    )
    assert report["triage_reason_counts"].get("judge_output_malformed", 0) == 1


def test_triage_maps_confidence_below_release_threshold_blocker() -> None:
    row = _batch_row()
    report = auto.build_alignment_triage_report(
        [row],
        decisions=[
            {
                "row_id": "r1",
                "decision": "needs_human",
                "blockers": ["deflated_confidence_below_release_threshold"],
            }
        ],
    )
    assert report["triage_reason_counts"].get("confidence_below_release_threshold", 0) == 1


def test_triage_flags_obvious_exact_candidate_when_blocked() -> None:
    row = _batch_row(
        current_statement_alignment_class="exact",
        claim_equivalence_verdict="equivalent",
    )
    report = auto.build_alignment_triage_report(
        [row],
        decisions=[
            {
                "row_id": "r1",
                "decision": "needs_human",
                "blockers": ["judge_confidence_below_threshold"],
            }
        ],
    )
    assert report["triage_reason_counts"].get("obvious_exact_candidate_blocked", 0) == 1


def test_obvious_exact_candidate_not_flagged_when_promoted() -> None:
    row = _batch_row(
        current_statement_alignment_class="exact",
        claim_equivalence_verdict="equivalent",
    )
    report = auto.build_alignment_triage_report(
        [row],
        decisions=[
            {
                "row_id": "r1",
                "decision": "reviewed_exact",
                "blockers": [],
            }
        ],
    )
    assert "obvious_exact_candidate_blocked" not in report["triage_reason_counts"]
