from __future__ import annotations

import json
from pathlib import Path

from paper_agnostic_report import build_report


def test_build_report_maps_statuses_and_blockers(tmp_path: Path) -> None:
    ledger_dir = tmp_path / "ledgers"
    ledger_dir.mkdir()
    toolchain = tmp_path / "lean-toolchain"
    toolchain.write_text("leanprover/lean4:v4.29.0-rc7\n", encoding="utf-8")
    ledger = {
        "schema_version": "2.0.0",
        "paper_id": "2300.00001",
        "entries": [
            {
                "theorem_name": "t1",
                "lean_statement": "theorem t1 : True",
                "status": "FULLY_PROVEN",
                "proof_text": "trivial",
                "step_obligations": [{"verified": True}],
                "assumptions": [],
                "provenance": {"paper_id": "2300.00001"},
            },
            {
                "theorem_name": "t2",
                "lean_statement": "theorem t2 : True",
                "status": "AXIOM_BACKED",
                "axiom_debt": ["paper_symbol:C_T"],
                "assumptions": [{"grounding": "UNGROUNDED"}],
            },
            {
                "theorem_name": "t3",
                "lean_statement": "",
                "status": "UNRESOLVED",
                "error_message": "unexpected token",
            },
        ],
    }
    (ledger_dir / "2300.00001.json").write_text(json.dumps(ledger), encoding="utf-8")

    report = build_report(ledger_dir=ledger_dir, suite_json=None, toolchain_file=toolchain)

    assert report["toolchain"] == "leanprover/lean4:v4.29.0-rc7"
    assert report["evidence_label"] == "partial_diagnostic_evidence"
    assert report["primary_metric"] == "aggregate_statuses.FULLY_PROVEN"
    assert "do not read this as full suite closure" in report["claim_scope"]
    assert report["papers_evaluated"] == 1
    assert report["theorems_evaluated"] == 3
    assert report["aggregate_statuses"]["FULLY_PROVEN"] == 1
    assert report["aggregate_statuses"]["AXIOM_BACKED"] == 1
    assert report["aggregate_statuses"]["TRANSLATION_UNCERTAIN"] == 1
    assert report["paper_local_axiom_disclosure"]["required"] is True
    assert report["paper_local_axiom_disclosure"]["result_label"] == "proved_modulo_paper_local_axioms"
    assert report["paper_local_axiom_disclosure"]["axiom_debt"] == ["paper_symbol:C_T"]
    assert report["axiom_debt_burndown"]["axiom_backed_result_count"] == 1
    assert report["axiom_debt_burndown"]["result_buckets"]["missing_definitions_only"] == 1
    assert report["papers"][0]["evidence_label"] == "partial_diagnostic_evidence"
    assert report["papers"][0]["primary_metric"] == "statuses.FULLY_PROVEN"
    assert report["papers"][0]["paper_local_axiom_disclosure"]["theorems"] == ["t2"]
    assert report["papers"][0]["axiom_debt_burndown"]["ranked_axioms"][0]["needed_by"] == ["t2"]
    assert report["aggregate_blockers"]["none"] == 1
    assert report["aggregate_blockers"]["missing_domain_library"] == 1
    assert report["aggregate_blockers"]["lean_elaboration"] == 1
