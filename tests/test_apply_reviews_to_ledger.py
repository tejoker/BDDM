from __future__ import annotations

import json
from pathlib import Path

from apply_reviews_to_ledger import (
    _index_reviewed_rows,
    _is_human_or_hybrid,
    _review_priority,
    apply_reviews_to_ledger_file,
    apply_reviews_to_ledgers,
)


def _write_ledger(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"entries": entries}, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_apply_reviews_populates_empty_review_fields(tmp_path: Path) -> None:
    """Round-trip: ledger row starts with empty reviewed_* fields; apply writes
    verdict, alignment_class, confidence, and review_provenance. This is the
    fix for: 0 of 177 ledger rows have reviewed_equivalence_verdict set."""
    ledger_dir = tmp_path / "output" / "verification_ledgers"
    ledger_path = ledger_dir / "0000.99999.json"
    _write_ledger(
        ledger_path,
        [{"theorem_name": "T1", "canonical_theorem_id": "cth_aaaa", "status": "UNRESOLVED"}],
    )
    reviewed_path = tmp_path / "reviewed_corpus.jsonl"
    _write_jsonl(reviewed_path, [{
        "arxiv_id": "0000.99999",
        "canonical_theorem_id": "cth_aaaa",
        "reviewed_equivalence_verdict": "equivalent",
        "reviewed_statement_alignment_class": "exact",
        "reviewed_alignment_confidence": 0.9,
        "review_provenance": {
            "reviewed_by": "hybrid:conservative-assisted-review",
            "reviewed_at": "2026-05-08T12:00:00Z",
            "artifact_id": "art:1",
        },
    }])

    summary = apply_reviews_to_ledgers(reviewed_path, ledger_dir)
    assert summary["total_updated"] == 1

    after = json.loads(ledger_path.read_text())["entries"][0]
    assert after["reviewed_equivalence_verdict"] == "equivalent"
    assert after["reviewed_statement_alignment_class"] == "exact"
    assert after["reviewed_alignment_confidence"] == 0.9
    assert after["review_provenance"]["reviewed_by"] == "hybrid:conservative-assisted-review"


def test_apply_reviews_flips_claim_equivalent_gate_for_hybrid_review(tmp_path: Path) -> None:
    """Generalisation: a hybrid-reviewed equivalent row must clear the
    `claim_equivalent` and `independent_semantic_equivalence_evidence` gates,
    even though `claim_equivalence_verdict` was previously 'unclear'. This is
    the missing round-trip step — without it, even reviewed rows stay stuck at
    INTERMEDIARY_PROVEN forever."""
    ledger_dir = tmp_path / "output" / "verification_ledgers"
    ledger_path = ledger_dir / "p1.json"
    _write_ledger(
        ledger_path,
        [{
            "theorem_name": "T1",
            "canonical_theorem_id": "cth_a",
            "status": "INTERMEDIARY_PROVEN",
            "proved": True,
            "proof_method": "lean_verified",
            "step_verdict": "VERIFIED",
            "claim_equivalence_verdict": "unclear",
            "validation_gates": {"lean_proof_closed": True, "step_verdict_verified": True},
            "gate_failures": ["claim_equivalent", "independent_semantic_equivalence_evidence", "no_paper_axiom_debt"],
            "axiom_debt": ["paper_definition_stub:foo"],
        }],
    )
    reviewed_path = tmp_path / "rev.jsonl"
    _write_jsonl(reviewed_path, [{
        "arxiv_id": "p1",
        "canonical_theorem_id": "cth_a",
        "reviewed_equivalence_verdict": "equivalent",
        "reviewed_statement_alignment_class": "exact",
        "reviewed_alignment_confidence": 0.9,
        "review_provenance": {
            "reviewed_by": "hybrid:conservative-assisted-review",
            "reviewed_at": "2026-05-08T12:00:00Z",
        },
    }])
    apply_reviews_to_ledgers(reviewed_path, ledger_dir)

    after = json.loads(ledger_path.read_text())["entries"][0]
    assert after["claim_equivalence_verdict"] == "equivalent"
    failures = set(after.get("gate_failures") or [])
    assert "claim_equivalent" not in failures, f"gate_failures still has claim_equivalent: {failures}"
    assert "independent_semantic_equivalence_evidence" not in failures
    artifact = after.get("semantic_equivalence_artifact", {})
    assert artifact.get("independent_semantic_evidence") is True


def test_apply_reviews_does_not_flip_gates_for_auto_llm_only(tmp_path: Path) -> None:
    """Auto-LLM reviews are NOT release-eligible — they must NOT flip the gates
    even if they assert 'equivalent'. Only hybrid/human reviews can promote."""
    ledger_dir = tmp_path / "output" / "verification_ledgers"
    ledger_path = ledger_dir / "p1.json"
    _write_ledger(
        ledger_path,
        [{
            "theorem_name": "T1",
            "canonical_theorem_id": "cth_a",
            "status": "INTERMEDIARY_PROVEN",
            "claim_equivalence_verdict": "unclear",
            "gate_failures": ["claim_equivalent"],
        }],
    )
    reviewed_path = tmp_path / "rev.jsonl"
    _write_jsonl(reviewed_path, [{
        "arxiv_id": "p1",
        "canonical_theorem_id": "cth_a",
        "reviewed_equivalence_verdict": "equivalent",
        "reviewed_statement_alignment_class": "exact",
        "reviewed_alignment_confidence": 0.81,
        "review_provenance": {
            "reviewed_by": "auto_llm:alignment-review",
        },
    }])
    apply_reviews_to_ledgers(reviewed_path, ledger_dir)

    after = json.loads(ledger_path.read_text())["entries"][0]
    # claim_equivalence_verdict NOT promoted to 'equivalent' on LLM-only reviews.
    assert after["claim_equivalence_verdict"] != "equivalent"


def test_apply_reviews_is_idempotent(tmp_path: Path) -> None:
    """Re-applying the same review must not modify the ledger again."""
    ledger_dir = tmp_path / "output" / "verification_ledgers"
    ledger_path = ledger_dir / "0000.99999.json"
    _write_ledger(
        ledger_path,
        [{"theorem_name": "T1", "canonical_theorem_id": "cth_a", "status": "UNRESOLVED"}],
    )
    reviewed_path = tmp_path / "rev.jsonl"
    _write_jsonl(reviewed_path, [{
        "arxiv_id": "0000.99999",
        "canonical_theorem_id": "cth_a",
        "reviewed_equivalence_verdict": "equivalent",
        "reviewed_statement_alignment_class": "exact",
        "reviewed_alignment_confidence": 0.9,
        "review_provenance": {"reviewed_by": "hybrid:x", "reviewed_at": "2026-01-01T00:00:00Z"},
    }])

    apply_reviews_to_ledgers(reviewed_path, ledger_dir)
    first_mtime = ledger_path.stat().st_mtime
    summary2 = apply_reviews_to_ledgers(reviewed_path, ledger_dir)
    assert summary2["total_updated"] == 0
    assert ledger_path.stat().st_mtime == first_mtime


def test_apply_reviews_human_beats_auto_llm_on_same_canonical_id(tmp_path: Path) -> None:
    """When both a human/hybrid review and an auto-LLM review target the same
    canonical_theorem_id, the human/hybrid one wins regardless of arrival order."""
    reviewed = [
        {
            "arxiv_id": "p1", "canonical_theorem_id": "cth_x",
            "reviewed_equivalence_verdict": "equivalent",
            "reviewed_alignment_confidence": 0.81,
            "review_provenance": {"reviewed_by": "auto_llm:alignment-review", "reviewed_at": "2026-05-08T08:00:00Z"},
        },
        {
            "arxiv_id": "p1", "canonical_theorem_id": "cth_x",
            "reviewed_equivalence_verdict": "equivalent",
            "reviewed_alignment_confidence": 0.95,
            "review_provenance": {"reviewed_by": "human:alice", "reviewed_at": "2026-05-08T07:00:00Z"},
        },
    ]
    idx, _by_name = _index_reviewed_rows(reviewed)
    chosen = idx[("p1", "cth_x")]
    assert chosen["review_provenance"]["reviewed_by"] == "human:alice"


def test_apply_reviews_skips_rows_with_no_verdict(tmp_path: Path) -> None:
    """A reviewed-corpus row without `reviewed_equivalence_verdict` must NOT
    overwrite ledger fields. Prevents wiping good review state with empties."""
    ledger_dir = tmp_path / "output" / "verification_ledgers"
    ledger_path = ledger_dir / "p1.json"
    _write_ledger(
        ledger_path,
        [{
            "theorem_name": "T1",
            "canonical_theorem_id": "cth_a",
            "reviewed_equivalence_verdict": "equivalent",
            "reviewed_alignment_confidence": 0.85,
        }],
    )
    reviewed_path = tmp_path / "rev.jsonl"
    _write_jsonl(reviewed_path, [{
        "arxiv_id": "p1",
        "canonical_theorem_id": "cth_a",
        "reviewed_equivalence_verdict": "",
    }])
    apply_reviews_to_ledgers(reviewed_path, ledger_dir)
    after = json.loads(ledger_path.read_text())["entries"][0]
    assert after["reviewed_equivalence_verdict"] == "equivalent"


def test_apply_reviews_no_match_does_nothing(tmp_path: Path) -> None:
    """canonical_theorem_id with no matching ledger row should not crash."""
    ledger_dir = tmp_path / "output" / "verification_ledgers"
    ledger_path = ledger_dir / "p1.json"
    _write_ledger(ledger_path, [{"theorem_name": "T1", "canonical_theorem_id": "cth_present"}])
    reviewed_path = tmp_path / "rev.jsonl"
    _write_jsonl(reviewed_path, [{
        "arxiv_id": "p1",
        "canonical_theorem_id": "cth_absent",
        "reviewed_equivalence_verdict": "equivalent",
        "review_provenance": {"reviewed_by": "human:alice"},
    }])
    summary = apply_reviews_to_ledgers(reviewed_path, ledger_dir)
    assert summary["total_updated"] == 0


def test_is_human_or_hybrid_classifies_correctly() -> None:
    assert _is_human_or_hybrid({"reviewed_by": "human:alice"})
    assert _is_human_or_hybrid({"reviewed_by": "hybrid:conservative-assisted-review"})
    assert not _is_human_or_hybrid({"reviewed_by": "auto_llm:alignment-review"})
    assert not _is_human_or_hybrid({"reviewed_by": ""})
    assert not _is_human_or_hybrid(None)


def test_theorem_name_fallback_recovers_drifted_canonical_id(tmp_path: Path) -> None:
    """A re-prove can regenerate canonical_theorem_id from a slightly different
    lean_statement, drifting away from the CTH the reviewed corpus was keyed on.
    The (paper_id, theorem_name) fallback recovers the match so the verdict
    still reaches the ledger."""
    ledger_dir = tmp_path / "output" / "verification_ledgers"
    ledger_path = ledger_dir / "p1.json"
    _write_ledger(
        ledger_path,
        [{
            "theorem_name": "EqualAB",  # bare ledger name
            "canonical_theorem_id": "cth_NEW_after_reprove",
            "status": "INTERMEDIARY_PROVEN",
            "claim_equivalence_verdict": "unclear",
            "gate_failures": ["claim_equivalent"],
        }],
    )
    reviewed_path = tmp_path / "rev.jsonl"
    _write_jsonl(reviewed_path, [{
        "arxiv_id": "p1",
        "canonical_theorem_id": "cth_OLD_pre_reprove",  # stale CTH from old batch
        "theorem_id": "ArxivPaper.EqualAB",  # qualified review name
        "reviewed_equivalence_verdict": "equivalent",
        "reviewed_statement_alignment_class": "exact",
        "reviewed_alignment_confidence": 0.9,
        "review_provenance": {"reviewed_by": "hybrid:conservative-assisted-review"},
    }])
    summary = apply_reviews_to_ledgers(reviewed_path, ledger_dir)
    assert summary["total_updated"] == 1
    assert summary["total_name_fallback"] == 1
    after = json.loads(ledger_path.read_text())["entries"][0]
    assert after["reviewed_equivalence_verdict"] == "equivalent"


def test_cth_match_takes_precedence_over_name_fallback(tmp_path: Path) -> None:
    """When both the canonical_theorem_id AND theorem_name match, the strict CTH
    match must win — name fallback should not be counted in that case."""
    ledger_dir = tmp_path / "output" / "verification_ledgers"
    ledger_path = ledger_dir / "p1.json"
    _write_ledger(
        ledger_path,
        [{
            "theorem_name": "EqualAB",
            "canonical_theorem_id": "cth_match",
            "status": "UNRESOLVED",
        }],
    )
    reviewed_path = tmp_path / "rev.jsonl"
    _write_jsonl(reviewed_path, [{
        "arxiv_id": "p1",
        "canonical_theorem_id": "cth_match",
        "theorem_id": "ArxivPaper.EqualAB",
        "reviewed_equivalence_verdict": "equivalent",
        "reviewed_alignment_confidence": 0.9,
        "review_provenance": {"reviewed_by": "hybrid:x"},
    }])
    summary = apply_reviews_to_ledgers(reviewed_path, ledger_dir)
    assert summary["total_updated"] == 1
    assert summary["total_name_fallback"] == 0


def test_name_fallback_normalizes_namespace_prefix(tmp_path: Path) -> None:
    """Reviewed-corpus qualified name → ledger bare name."""
    from apply_reviews_to_ledger import _normalize_theorem_name
    assert _normalize_theorem_name("ArxivPaper.EqualAB") == "equalab"
    assert _normalize_theorem_name("EqualAB") == "equalab"
    assert _normalize_theorem_name("Foo.Bar.Baz") == "baz"
    assert _normalize_theorem_name("") == ""
    assert _normalize_theorem_name(None) == ""  # type: ignore[arg-type]


def test_name_fallback_normalizes_latex_label_to_lean_identifier() -> None:
    """LaTeX label form (`lem:HS-full-norm`) and Lean identifier form
    (`lem_HS_full_norm`) refer to the same paper theorem — normalize both."""
    from apply_reviews_to_ledger import _normalize_theorem_name
    assert _normalize_theorem_name("lem:HS-full-norm-moment-mu") == "lem_hs_full_norm_moment_mu"
    assert _normalize_theorem_name("lem_HS_full_norm_moment_mu") == "lem_hs_full_norm_moment_mu"
    assert _normalize_theorem_name("ArxivPaper.lem:HS-full-norm") == "lem_hs_full_norm"
    # Collapse runs of underscore (handle `lem::foo--bar` edge case).
    assert _normalize_theorem_name("lem::foo--bar") == "lem_foo_bar"


def test_backfill_quality_fields_for_release_eligible_review(tmp_path: Path) -> None:
    """A row with proved=True + step_verdict=VERIFIED + reviewed_equivalent (hybrid)
    must get translation_fidelity_score, status_alignment_score, and
    reproducible_env backfilled. Without this, the gates fail on the missing
    score fields and the row stays at INTERMEDIARY_PROVEN forever."""
    ledger_dir = tmp_path / "output" / "verification_ledgers"
    ledger_path = ledger_dir / "p1.json"
    _write_ledger(
        ledger_path,
        [{
            "theorem_name": "T1",
            "canonical_theorem_id": "cth_a",
            "status": "INTERMEDIARY_PROVEN",
            "proved": True,
            "step_verdict": "VERIFIED",
            "proof_method": "lean_verified",
            # The bug: scores are unset, so the gates fail.
            "translation_fidelity_score": None,
            "status_alignment_score": None,
            "reproducible_env": None,
            "validation_gates": {
                "lean_proof_closed": True,
                "step_verdict_verified": True,
                "claim_equivalent": True,
                "translation_fidelity_ok": False,
                "status_alignment_ok": False,
                "reproducible_env": False,
            },
            "gate_failures": ["translation_fidelity_ok", "status_alignment_ok", "reproducible_env"],
            "axiom_debt": [],
        }],
    )
    reviewed_path = tmp_path / "rev.jsonl"
    _write_jsonl(reviewed_path, [{
        "arxiv_id": "p1",
        "canonical_theorem_id": "cth_a",
        "reviewed_equivalence_verdict": "equivalent",
        "reviewed_alignment_confidence": 0.85,
        "review_provenance": {"reviewed_by": "hybrid:conservative-assisted-review"},
    }])
    summary = apply_reviews_to_ledgers(reviewed_path, ledger_dir)
    assert summary["total_backfilled"] == 1
    after = json.loads(ledger_path.read_text())["entries"][0]
    assert after["translation_fidelity_score"] == 0.90
    assert after["status_alignment_score"] == 0.90
    # reproducible_env may be False if test is run outside a git repo with a
    # lean-toolchain file; the apply uses the actual project root, so we only
    # assert the field was set to something explicit.
    assert after["reproducible_env"] is not None


def test_apply_does_not_overwrite_hybrid_with_auto_llm(tmp_path: Path) -> None:
    """Monotonicity guard: a ledger entry that already carries a hybrid/human
    review must NOT be overwritten by an auto_llm review of the same row.
    Without this guard, the bridge's non-monotonic regeneration (which can
    drop hybrid wrappers between runs) silently demoted FULLY_PROVEN → IP
    on previously-promoted rows."""
    ledger_dir = tmp_path / "output" / "verification_ledgers"
    ledger_path = ledger_dir / "p1.json"
    _write_ledger(
        ledger_path,
        [{
            "theorem_name": "T1",
            "canonical_theorem_id": "cth_a",
            "status": "FULLY_PROVEN",
            "proved": True,
            "step_verdict": "VERIFIED",
            "reviewed_equivalence_verdict": "equivalent",
            "review_provenance": {
                "reviewed_by": "hybrid:conservative-assisted-review",
                "reviewed_at": "2026-05-09T19:00:00Z",
            },
            "translation_fidelity_score": 0.9,
            "status_alignment_score": 0.85,
            "reproducible_env": True,
        }],
    )
    reviewed_path = tmp_path / "rev.jsonl"
    # New corpus has only the underlying auto_llm review for the same row.
    _write_jsonl(reviewed_path, [{
        "arxiv_id": "p1",
        "canonical_theorem_id": "cth_a",
        "reviewed_equivalence_verdict": "equivalent",
        "reviewed_alignment_confidence": 0.81,
        "review_provenance": {
            "reviewed_by": "auto_llm:alignment-review",
            "reviewed_at": "2026-05-09T21:00:00Z",  # newer, but lower priority
        },
    }])
    apply_reviews_to_ledgers(reviewed_path, ledger_dir)
    after = json.loads(ledger_path.read_text())["entries"][0]
    # Hybrid review preserved; auto_llm did NOT overwrite it.
    assert after["review_provenance"]["reviewed_by"] == "hybrid:conservative-assisted-review"
    assert after["status"] == "FULLY_PROVEN"


def test_backfill_skips_auto_llm_only_review(tmp_path: Path) -> None:
    """Auto-LLM reviews must NOT trigger the quality-fields backfill — only
    release-eligible (hybrid/human) reviews count as fidelity evidence."""
    ledger_dir = tmp_path / "output" / "verification_ledgers"
    ledger_path = ledger_dir / "p1.json"
    _write_ledger(
        ledger_path,
        [{
            "theorem_name": "T1",
            "canonical_theorem_id": "cth_a",
            "status": "INTERMEDIARY_PROVEN",
            "proved": True,
            "step_verdict": "VERIFIED",
            "translation_fidelity_score": None,
            "status_alignment_score": None,
            "reproducible_env": None,
        }],
    )
    reviewed_path = tmp_path / "rev.jsonl"
    _write_jsonl(reviewed_path, [{
        "arxiv_id": "p1",
        "canonical_theorem_id": "cth_a",
        "reviewed_equivalence_verdict": "equivalent",
        "reviewed_alignment_confidence": 0.81,
        "review_provenance": {"reviewed_by": "auto_llm:alignment-review"},
    }])
    summary = apply_reviews_to_ledgers(reviewed_path, ledger_dir)
    assert summary["total_backfilled"] == 0
    after = json.loads(ledger_path.read_text())["entries"][0]
    assert after["translation_fidelity_score"] is None
    assert after["status_alignment_score"] is None


def test_latex_label_review_matches_lean_identifier_ledger_row(tmp_path: Path) -> None:
    """End-to-end: a review keyed by LaTeX label `lem:HS-full-norm-moment-mu`
    must match a ledger row whose theorem_name is `lem_HS_full_norm_moment_mu`.
    This is the 2604.21583 case from production."""
    ledger_dir = tmp_path / "output" / "verification_ledgers"
    ledger_path = ledger_dir / "2604.21583.json"
    _write_ledger(
        ledger_path,
        [{
            "theorem_name": "lem_HS_full_norm_moment_mu",
            "canonical_theorem_id": "cth_NEW",
            "status": "INTERMEDIARY_PROVEN",
            "claim_equivalence_verdict": "unclear",
            "gate_failures": ["claim_equivalent"],
        }],
    )
    reviewed_path = tmp_path / "rev.jsonl"
    _write_jsonl(reviewed_path, [{
        "arxiv_id": "2604.21583",
        "canonical_theorem_id": "cth_OLD",
        "theorem_id": "lem:HS-full-norm-moment-mu",
        "reviewed_equivalence_verdict": "equivalent",
        "reviewed_alignment_confidence": 0.9,
        "review_provenance": {"reviewed_by": "hybrid:x"},
    }])
    summary = apply_reviews_to_ledgers(reviewed_path, ledger_dir)
    assert summary["total_updated"] == 1
    assert summary["total_name_fallback"] == 1
