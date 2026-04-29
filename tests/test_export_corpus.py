from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from export_corpus import build_corpus_rows, export_corpus, parse_imports, toolchain_metadata, validate_corpus_export
from theorem_extractor import extract_theorems


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_project_pins(root: Path) -> None:
    (root / "lean-toolchain").write_text("leanprover/lean4:v4.29.0-rc7\n", encoding="utf-8")
    (root / "lakefile.toml").write_text(
        '\n'.join(
            [
                'name = "desol"',
                "",
                "[[require]]",
                'name = "mathlib"',
                'git = "https://github.com/leanprover-community/mathlib4.git"',
                'rev = "abc123"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_toolchain_hash_and_import_parsing_are_stable(tmp_path: Path) -> None:
    _write_project_pins(tmp_path)
    lean = tmp_path / "Paper.lean"
    lean.write_text(
        "import Mathlib\nimport Desol.SDE.Basic -- needed locally\n\n"
        "theorem demo : True := by\n  trivial\n",
        encoding="utf-8",
    )

    first = toolchain_metadata(tmp_path)
    second = toolchain_metadata(tmp_path, pipeline_commit="ignored_for_hash")

    assert first["lean_toolchain"] == "leanprover/lean4:v4.29.0-rc7"
    assert first["mathlib"]["rev"] == "abc123"
    assert first["toolchain_hash"] == second["toolchain_hash"]
    assert parse_imports(lean) == ["Mathlib", "Desol.SDE.Basic"]


def test_build_corpus_rows_joins_ledger_report_evidence_and_spans(tmp_path: Path) -> None:
    _write_project_pins(tmp_path)
    paper_id = "2300.00001"
    safe = paper_id
    tex = tmp_path / "sources" / safe / "main.tex"
    tex.parent.mkdir(parents=True)
    tex.write_text(
        "\\begin{theorem}\n"
        "\\label{thm:demo}\n"
        "For every natural number n, n equals n.\n"
        "\\end{theorem}\n"
        "\\begin{proof}\n"
        "Immediate.\n"
        "\\end{proof}\n",
        encoding="utf-8",
    )
    [entry] = extract_theorems(tex)
    evidence_dir = tmp_path / "evidence" / safe
    _write_json(
        evidence_dir / "extracted_theorems.json",
        {"paper_id": paper_id, "theorem_count": 1, "entries": [asdict(entry)]},
    )

    lean = tmp_path / "output" / f"{safe}.lean"
    lean.parent.mkdir(parents=True)
    lean.write_text(
        "import Mathlib\nimport Desol.SDE.Basic\n\n"
        "theorem demo (n : Nat) : n = n := by\n  rfl\n",
        encoding="utf-8",
    )
    ledger_dir = tmp_path / "output" / "verification_ledgers"
    _write_json(
        ledger_dir / f"{safe}.json",
        {
            "paper_id": paper_id,
            "schema_version": "2.0.0",
            "pipeline_commit": "commit-a",
            "entries": [
                {
                    "theorem_name": "demo",
                    "lean_file": str(lean),
                    "lean_statement": "theorem demo (n : Nat) : n = n := by\n  rfl",
                    "status": "FULLY_PROVEN",
                    "proof_method": "lean_verified",
                    "trust_class": "TRUST_MATHLIB",
                    "trust_reference": "all_assumptions_mathlib",
                    "proof_text": "rfl",
                    "provenance": {"paper_id": paper_id, "label": "thm:demo", "section": "1"},
                    "semantic_equivalence_artifact": {
                        "original_latex_theorem": entry.statement,
                        "normalized_natural_language_theorem": "For every natural number n, n equals n.",
                        "extracted_conclusion": "n equals n",
                        "lean_statement": "theorem demo (n : Nat) : n = n",
                    },
                }
            ],
        },
    )
    report_dir = tmp_path / "output" / "reports" / "full_paper"
    _write_json(
        report_dir / f"{safe}_suite_report.json",
        {
            "paper_id": paper_id,
            "out_lean": str(lean),
            "ledger_path": str(ledger_dir / f"{safe}.json"),
            "results_file": str(tmp_path / "logs" / "results.json"),
            "reproducibility_bundle": {"ledger": str(tmp_path / "repro" / "ledger.json")},
        },
    )

    rows, summary = build_corpus_rows(
        ledger_paths=[ledger_dir],
        project_root=tmp_path,
        report_roots=[report_dir],
        evidence_roots=[tmp_path / "evidence"],
    )

    assert summary["rows"] == 1
    assert validate_corpus_export(rows, summary) == []
    assert summary["rows_with_imports"] == 1
    assert summary["span_confidence_counts"] == {"exact_extractor": 1}
    assert summary["gold_proof_count"] == 1
    assert summary["verified_proven_scope_counts"]["direct_or_unknown_scope"] == 1
    assert summary["verified_proven_full_source_claim"] == 0
    assert summary["verified_proven_audited_component"] == 0
    assert summary["dataset_tier_counts"]["gold_proof"] == 1
    row = rows[0]
    assert row["arxiv_id"] == paper_id
    assert row["theorem_id"] == "thm:demo"
    assert row["theorem_id_source"] == "provenance.label"
    assert row["source_latex"] == entry.statement
    assert row["normalized_text"] == "For every natural number n, n equals n."
    assert row["lean_statement"] == "theorem demo (n : Nat) : n = n"
    assert row["proof_text"] == "rfl"
    assert row["status"] == "FULLY_PROVEN"
    assert row["dataset_tier"] == "gold_proof"
    assert row["training_tier"] == "gold_proof"
    assert row["statement_alignment_class"] in {"partial", "exact"}
    assert row["alignment_evidence"]["source_match"]["match_status"] == "matched"
    assert row["source_span_quality"] == "extractor_native"
    assert row["alignment_tier"] in {"alignment_candidate", "alignment_gold"}
    assert row["alignment_review_required"] is False
    assert row["identity_status"] == "unknown"
    assert row["identity_evidence"]["human_review_required"] is True
    assert row["trust_tier"] == "TRUST_MATHLIB"
    assert row["imports"] == ["Mathlib", "Desol.SDE.Basic"]
    assert row["mathlib_pin"]["rev"] == "abc123"
    assert row["pipeline_commit"] == "commit-a"
    assert row["source_span"]["source_file"] == str(tex)
    assert row["artifact_paths"]["report"].endswith(f"{safe}_suite_report.json")
    assert row["artifact_paths"]["reproducibility_bundle"]["ledger"].endswith("ledger.json")

    rows_again, _ = build_corpus_rows(
        ledger_paths=[ledger_dir],
        project_root=tmp_path,
        report_roots=[report_dir],
        evidence_roots=[tmp_path / "evidence"],
    )
    assert rows_again[0]["row_id"] == row["row_id"]
    assert rows_again[0]["toolchain_hash"] == row["toolchain_hash"]


def test_export_corpus_reverifies_legacy_extractor_span(tmp_path: Path) -> None:
    _write_project_pins(tmp_path)
    paper_id = "2300.00002"
    tex = tmp_path / "legacy.tex"
    tex.write_text("\\begin{lemma}Legacy source body.\\end{lemma}", encoding="utf-8")
    evidence = tmp_path / "evidence" / paper_id / "extracted_theorems.json"
    _write_json(
        evidence,
        {
            "paper_id": paper_id,
            "entries": [
                {
                    "kind": "lemma",
                    "name": "lem:legacy",
                    "statement": "Legacy source body.",
                    "proof": "",
                    "source_file": str(tex),
                }
            ],
        },
    )
    lean = tmp_path / "legacy.lean"
    lean.write_text(
        "import Mathlib\n\n"
        "lemma legacy : False := by sorry\n\n"
        "-- [lemma] next source marker\n",
        encoding="utf-8",
    )
    ledger = tmp_path / "ledgers" / f"{paper_id}.json"
    _write_json(
        ledger,
        {
            "paper_id": paper_id,
            "entries": [
                {
                    "theorem_name": "legacy",
                    "lean_file": str(lean),
                    "lean_statement": "lemma legacy : True",
                    "status": "UNRESOLVED",
                    "trust_class": "TRUST_PLACEHOLDER",
                    "provenance": {"paper_id": paper_id, "label": "lem:legacy"},
                    "semantic_equivalence_artifact": {
                        "original_latex_theorem": "Legacy source body.",
                        "lean_statement": "lemma legacy : True",
                    },
                }
            ],
        },
    )

    result = export_corpus(
        ledger_paths=[tmp_path / "ledgers"],
        project_root=tmp_path,
        report_roots=[],
        evidence_roots=[tmp_path / "evidence"],
        out_jsonl=tmp_path / "out" / "corpus.jsonl",
        out_summary=tmp_path / "out" / "summary.json",
    )

    assert result["rows"] == 1
    rows = [json.loads(line) for line in (tmp_path / "out" / "corpus.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["source_span"]["span_confidence"] == "exact_extractor_reverified"
    assert rows[0]["source_span"]["source_span_id"].startswith("srcspan_")
    assert rows[0]["source_span_quality"] == "extractor_native"
    assert rows[0]["alignment_review_required"] is False
    assert rows[0]["source_span"]["start_byte"] > 0
    assert rows[0]["generated_lean_declaration"] == "lemma legacy : False := by sorry"
    assert rows[0]["proof_text"] == ""
    assert json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))["rows"] == 1


def test_export_corpus_keeps_string_recovery_when_no_extractor_match(tmp_path: Path) -> None:
    _write_project_pins(tmp_path)
    paper_id = "2300.00008"
    tex = tmp_path / "legacy.txt"
    tex.write_text("Legacy source body.", encoding="utf-8")
    evidence = tmp_path / "evidence" / paper_id / "extracted_theorems.json"
    _write_json(
        evidence,
        {
            "paper_id": paper_id,
            "entries": [
                {
                    "kind": "lemma",
                    "name": "lem:legacy",
                    "statement": "Legacy source body.",
                    "proof": "",
                    "source_file": str(tex),
                }
            ],
        },
    )
    ledger = tmp_path / "ledgers" / f"{paper_id}.json"
    _write_json(
        ledger,
        {
            "paper_id": paper_id,
            "entries": [
                {
                    "theorem_name": "legacy",
                    "lean_statement": "lemma legacy : True",
                    "status": "UNRESOLVED",
                    "provenance": {"label": "lem:legacy"},
                    "semantic_equivalence_artifact": {
                        "original_latex_theorem": "Legacy source body.",
                        "lean_statement": "lemma legacy : True",
                    },
                }
            ],
        },
    )

    result = export_corpus(
        ledger_paths=[tmp_path / "ledgers"],
        project_root=tmp_path,
        report_roots=[],
        evidence_roots=[tmp_path / "evidence"],
        out_jsonl=tmp_path / "out" / "corpus.jsonl",
        out_summary=tmp_path / "out" / "summary.json",
    )

    assert result["rows"] == 1
    rows = [json.loads(line) for line in (tmp_path / "out" / "corpus.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["source_span"]["span_confidence"] == "string_recovered_exact"
    assert rows[0]["source_span_quality"] == "string_recovered"
    assert rows[0]["alignment_review_required"] is True


def test_export_corpus_does_not_mark_raw_fully_proven_as_gold(tmp_path: Path) -> None:
    _write_project_pins(tmp_path)
    paper_id = "2300.00005"
    ledger = tmp_path / "ledgers" / f"{paper_id}.json"
    _write_json(
        ledger,
        {
            "paper_id": paper_id,
            "entries": [
                {
                    "theorem_name": "fake_gold",
                    "lean_statement": "theorem fake_gold : True",
                    "status": "FULLY_PROVEN",
                    "proof_text": "trivial",
                    "trust_class": "TRUST_INTERNAL_PROVED",
                }
            ],
        },
    )

    rows, summary = build_corpus_rows(
        ledger_paths=[tmp_path / "ledgers"],
        project_root=tmp_path,
        report_roots=[],
        evidence_roots=[],
    )

    assert summary["status_counts"]["FULLY_PROVEN"] == 1
    assert summary["verified_proven_count"] == 0
    assert summary["gold_proof_count"] == 0
    assert rows[0]["dataset_tier"] == "diagnostic"
    assert "proof_method_not_lean_verified" in rows[0]["tier_evidence"]["gold_blockers"]


def test_export_corpus_downgrades_unsupported_new_candidate(tmp_path: Path) -> None:
    _write_project_pins(tmp_path)
    paper_id = "2300.00007"
    ledger = tmp_path / "ledgers" / f"{paper_id}.json"
    _write_json(
        ledger,
        {
            "paper_id": paper_id,
            "entries": [
                {
                    "theorem_name": "unsupported_novelty",
                    "lean_statement": "theorem unsupported_novelty (n : Nat) : n = n",
                    "status": "UNRESOLVED",
                    "novelty_status": "new_candidate",
                    "novelty_evidence": {"mathlib": {"checks_run": []}},
                }
            ],
        },
    )

    rows, summary = build_corpus_rows(
        ledger_paths=[tmp_path / "ledgers"],
        project_root=tmp_path,
        report_roots=[],
        evidence_roots=[],
    )

    assert rows[0]["novelty_status"] == "unknown"
    assert rows[0]["mathlib_novelty_status"] == "unknown"
    assert rows[0]["novelty_evidence"]["original_novelty_status"] == "new_candidate"
    assert summary["novelty_status_counts"]["unknown"] == 1


def test_export_corpus_discovers_nested_release_bundle_by_default(tmp_path: Path) -> None:
    _write_project_pins(tmp_path)
    paper_id = "2300.00003"
    bundle = tmp_path / "reproducibility" / "full_paper_reports" / paper_id
    lean = tmp_path / "paper.lean"
    lean.write_text("import Mathlib\n\ntheorem release_demo : True := by\n  trivial\n", encoding="utf-8")
    _write_json(
        bundle / "verification_ledger.json",
        {
            "paper_id": paper_id,
            "pipeline_commit": "commit-release",
            "entries": [
                {
                    "theorem_name": "release_demo",
                    "lean_file": str(lean),
                    "lean_statement": "theorem release_demo : True",
                    "status": "FULLY_PROVEN",
                    "proof_method": "lean_verified",
                    "proof_text": "trivial",
                }
            ],
        },
    )
    _write_json(bundle / "suite_report.json", {"paper_id": paper_id, "out_lean": str(lean)})

    result = export_corpus(
        ledger_paths=[tmp_path / "reproducibility" / "full_paper_reports"],
        project_root=tmp_path,
        report_roots=[tmp_path / "reproducibility" / "full_paper_reports"],
        evidence_roots=[],
        out_jsonl=tmp_path / "out" / "corpus.jsonl",
        out_summary=tmp_path / "out" / "summary.json",
    )

    rows = [json.loads(line) for line in (tmp_path / "out" / "corpus.jsonl").read_text(encoding="utf-8").splitlines()]
    assert result["rows"] == 1
    assert rows[0]["arxiv_id"] == paper_id
    assert rows[0]["artifact_paths"]["ledger"].endswith("verification_ledger.json")
    assert rows[0]["artifact_paths"]["report"].endswith("suite_report.json")


def test_export_corpus_deduplicates_logical_row_ids_with_conflict_metadata(tmp_path: Path) -> None:
    _write_project_pins(tmp_path)
    paper_id = "2300.00004"
    ledger_dir = tmp_path / "ledgers"
    for idx, status in enumerate(["UNRESOLVED", "FULLY_PROVEN"]):
        _write_json(
            ledger_dir / f"{paper_id}_{idx}.json",
            {
                "paper_id": paper_id,
                "entries": [
                    {
                        "theorem_name": "same",
                        "lean_statement": "theorem same : True",
                        "status": status,
                        "proof_method": "lean_verified" if status == "FULLY_PROVEN" else "",
                    }
                ],
            },
        )

    rows, summary = build_corpus_rows(
        ledger_paths=[ledger_dir],
        project_root=tmp_path,
        report_roots=[],
        evidence_roots=[],
    )

    assert summary["input_rows_before_dedup"] == 2
    assert summary["rows"] == 1
    assert summary["duplicate_row_id_count"] == 1
    assert summary["conflict_count"] == 1
    assert rows[0]["status"] == "FULLY_PROVEN"
    assert rows[0]["deduplication"]["conflict"] is True
    assert summary["conflict_examples"][0]["row_id"] == rows[0]["row_id"]


def test_export_corpus_surfaces_unreadable_ledger_warning(tmp_path: Path) -> None:
    _write_project_pins(tmp_path)
    bad = tmp_path / "ledgers" / "bad.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("{not json", encoding="utf-8")

    rows, summary = build_corpus_rows(
        ledger_paths=[tmp_path / "ledgers"],
        project_root=tmp_path,
        report_roots=[],
        evidence_roots=[],
    )

    assert rows == []
    assert any("json_unreadable" in warning for warning in summary["warnings"])


def test_export_corpus_marks_ambiguous_source_label_matches(tmp_path: Path) -> None:
    _write_project_pins(tmp_path)
    paper_id = "2300.00006"
    evidence = tmp_path / "evidence" / paper_id / "extracted_theorems.json"
    _write_json(
        evidence,
        {
            "paper_id": paper_id,
            "entries": [
                {"name": "thm:foo", "statement": "First statement."},
                {"name": "thm_foo", "statement": "First statement."},
            ],
        },
    )
    ledger = tmp_path / "ledgers" / f"{paper_id}.json"
    _write_json(
        ledger,
        {
            "paper_id": paper_id,
            "entries": [
                {
                    "theorem_name": "demo",
                    "lean_statement": "theorem demo : True",
                    "status": "UNRESOLVED",
                    "provenance": {"label": "thm:foo"},
                    "semantic_equivalence_artifact": {
                        "original_latex_theorem": "First statement.",
                        "normalized_natural_language_theorem": "First statement.",
                        "lean_statement": "theorem demo : True",
                        "alignment_decision": {"alignment_class": "exact"},
                    },
                }
            ],
        },
    )

    rows, summary = build_corpus_rows(
        ledger_paths=[tmp_path / "ledgers"],
        project_root=tmp_path,
        report_roots=[],
        evidence_roots=[tmp_path / "evidence"],
    )

    assert rows[0]["alignment_evidence"]["source_match"]["match_status"] == "ambiguous"
    assert rows[0]["statement_alignment_class"] != "exact"
    assert rows[0]["source_span_quality"] == "ambiguous"
    assert rows[0]["alignment_tier"] == "alignment_review_required"
    assert summary["ambiguous_source_match_count"] == 1
    assert summary["alignment_review_required_count"] == 1
    assert any("ambiguous_source_match" in warning for warning in summary["warnings"])


def test_export_corpus_resolves_label_collision_with_source_text(tmp_path: Path) -> None:
    _write_project_pins(tmp_path)
    paper_id = "2300.00009"
    evidence = tmp_path / "evidence" / paper_id / "extracted_theorems.json"
    _write_json(
        evidence,
        {
            "paper_id": paper_id,
            "entries": [
                {"name": "thm:foo", "statement": "First statement."},
                {"name": "thm_foo", "statement": "Second statement."},
            ],
        },
    )
    ledger = tmp_path / "ledgers" / f"{paper_id}.json"
    _write_json(
        ledger,
        {
            "paper_id": paper_id,
            "entries": [
                {
                    "theorem_name": "demo",
                    "lean_statement": "theorem demo : True",
                    "status": "UNRESOLVED",
                    "provenance": {"label": "thm:foo"},
                    "semantic_equivalence_artifact": {
                        "original_latex_theorem": "First statement.",
                        "normalized_natural_language_theorem": "First statement.",
                        "lean_statement": "theorem demo : True",
                    },
                }
            ],
        },
    )

    rows, summary = build_corpus_rows(
        ledger_paths=[tmp_path / "ledgers"],
        project_root=tmp_path,
        report_roots=[],
        evidence_roots=[tmp_path / "evidence"],
    )

    source_match = rows[0]["alignment_evidence"]["source_match"]
    assert source_match["match_status"] == "matched"
    assert source_match["match_method"] == "scored_evidence_resolver"
    assert source_match["selected_candidate"]["name"] == "thm:foo"
    assert rows[0]["source_latex"] == "First statement."
    assert summary["ambiguous_source_match_count"] == 0
