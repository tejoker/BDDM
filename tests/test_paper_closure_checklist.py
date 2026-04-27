import json

from paper_closure_checklist import _canonical_theorem_key, _dedupe_by_canonical_theorem, run_checklist


def test_canonical_theorem_key_strips_known_namespaces() -> None:
    assert _canonical_theorem_key("ArxivPaper.Lem_A1b") == "Lem_A1b"
    assert _canonical_theorem_key("ArxivPaperActionable.Lem_A1b") == "Lem_A1b"
    assert _canonical_theorem_key("Lem_A1b") == "Lem_A1b"


def test_dedupe_prefers_stronger_status_for_same_canonical_theorem() -> None:
    rows = [
        {"theorem_name": "ArxivPaper.Lem_A1b", "status": "FLAWED", "promotion_gate_passed": False},
        {"theorem_name": "Lem_A1b", "status": "FULLY_PROVEN", "promotion_gate_passed": True},
    ]
    out = _dedupe_by_canonical_theorem(rows)
    assert len(out) == 1
    assert out[0]["theorem_name"] == "Lem_A1b"
    assert out[0]["status"] == "FULLY_PROVEN"


def test_run_checklist_includes_claim_equivalence_review_summary(tmp_path) -> None:
    ledger = tmp_path / "2604.21884.json"
    ledger.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "paper_id": "2604.21884",
                        "theorem_name": "remark_20",
                        "status": "INTERMEDIARY_PROVEN",
                        "proved": True,
                        "proof_method": "lean_verified",
                        "step_verdict": "VERIFIED",
                        "lean_statement": "theorem remark_20 : True := by trivial",
                        "translation_fidelity_score": 0.95,
                        "status_alignment_score": 0.95,
                        "claim_equivalence_verdict": "unclear",
                        "gate_failures": ["claim_equivalent", "independent_semantic_equivalence_evidence"],
                        "validation_gates": {
                            "lean_proof_closed": True,
                            "step_verdict_verified": True,
                            "translation_fidelity_ok": True,
                            "status_alignment_ok": True,
                            "claim_equivalent": False,
                            "independent_semantic_equivalence_evidence": False,
                        },
                        "auto_reliable_core": {
                            "theorem_name": "remark_20",
                            "strict_gate_passed": False,
                            "strict_gate_failures": [
                                "claim_equivalent",
                                "independent_semantic_equivalence_evidence",
                            ],
                        },
                        "semantic_equivalence_artifact": {
                            "original_latex_theorem": "The admissible tuple satisfies the stated inequalities.",
                            "equivalence_verdict": "unclear",
                            "independent_semantic_evidence": False,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    payload = run_checklist(paper_id="2604.21884", ledger_root=tmp_path)

    review = payload["claim_equivalence_review"]
    assert review["pending_review_count"] == 1
    assert review["high_potential_review_count"] == 1
    assert review["top_review_targets"][0]["theorem_name"] == "remark_20"
