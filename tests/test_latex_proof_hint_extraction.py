"""Hermetic tests for `extract_latex_proof_hint`.

The deterministic parser is exercised against fixture LaTeX strings; no
files are read from the live tree (one optional test covers the in-tree
extracted_theorems.json under a `pytest.skip` guard)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import extract_latex_proof_hint as elp


# --- Vocabulary / structural hints ---------------------------------------


def test_applies_macro_surfaces_named_inequality() -> None:
    proof = r"By Cauchy-Schwarz applied to $f$ and $g$, we obtain the bound."
    hints = elp.extract_hints_from_proof(proof)
    assert any("Cauchy-Schwarz" in h for h in hints)


def test_explicit_macro_form_surfaces_hint() -> None:
    proof = r"We \applies{Cauchy-Schwarz inequality} on the pair $(f,g)$."
    hints = elp.extract_hints_from_proof(proof)
    # Either the macro or the named-table path can match; both are accepted.
    assert any("Cauchy-Schwarz" in h for h in hints)


def test_multiple_named_inequalities_surfaced_in_order() -> None:
    proof = (
        "By Markov's inequality and Borel-Cantelli, the result follows. "
        "Sobolev embedding gives the final bound."
    )
    hints = elp.extract_hints_from_proof(proof)
    joined = " | ".join(hints)
    assert "Markov" in joined
    assert "Borel-Cantelli" in joined
    assert "Sobolev embedding" in joined


def test_integration_by_parts_keyword() -> None:
    proof = r"Integrate by parts in $s$ to convert the boundary term."
    hints = elp.extract_hints_from_proof(proof)
    assert any("integration by parts" in h for h in hints)


def test_first_then_step_structure() -> None:
    proof = (
        "We first proves a covariance bound and then conclude with "
        "Kolmogorov continuity."
    )
    hints = elp.extract_hints_from_proof(proof)
    assert any("first proves" in h for h in hints)


def test_label_references_collected() -> None:
    proof = (
        r"Lemma \ref{lem:speed-gap} together with \eqref{eq:HN-moment} "
        r"gives the bound; see also \cref{thm:pathwise}."
    )
    hints = elp.extract_hints_from_proof(proof)
    ref_lines = [h for h in hints if h.startswith("relies on ")]
    assert ref_lines, "expected at least one `relies on <ref:...>` hint"
    payload = ref_lines[0]
    assert "<ref:lem:speed-gap>" in payload
    assert "<ref:eq:HN-moment>" in payload
    assert "<ref:thm:pathwise>" in payload


def test_empty_proof_returns_empty_list() -> None:
    assert elp.extract_hints_from_proof("") == []
    assert elp.extract_hints_from_proof("   \n\n") == []


def test_format_hint_block_renders_bullets() -> None:
    block = elp.format_hint_block(["applies Cauchy-Schwarz", "uses linarith"])
    assert block.startswith("LaTeX proof structure (from the paper):")
    assert "  - applies Cauchy-Schwarz" in block
    assert "  - uses linarith" in block


def test_format_hint_block_empty_returns_empty() -> None:
    assert elp.format_hint_block([]) == ""
    assert elp.format_hint_block(["", "   "]) == ""


# --- Record assembly / JSONL ---------------------------------------------


def test_build_row_records_skips_empty_proofs() -> None:
    entries = [
        {"name": "thm:a", "label": "thm:a", "proof": ""},
        {"name": "thm:b", "label": "thm:b", "proof": "  "},
        {"name": "thm:c", "label": "thm:c",
         "proof": r"By Markov's inequality the bound follows."},
    ]
    records = elp.build_row_records(entries, paper_id="9999.test")
    assert len(records) == 1
    rec = records[0]
    assert rec["paper_id"] == "9999.test"
    assert rec["theorem_name"] == "thm:c"
    assert any("Markov" in h for h in rec["hints"])


def test_build_all_hints_writes_jsonl(tmp_path: Path) -> None:
    # Construct a fake tree.
    root = tmp_path / "fake_root"
    paper_dir = root / "reproducibility" / "paper_agnostic_golden10_results" / "9999.test"
    paper_dir.mkdir(parents=True)
    fixture = {
        "paper_id": "9999.test",
        "entries": [
            {"name": "thm:foo", "label": "thm:foo",
             "proof": r"By Cauchy-Schwarz on $(f,g)$."},
            {"name": "thm:bar", "label": "thm:bar", "proof": ""},
        ],
    }
    (paper_dir / "extracted_theorems.json").write_text(
        json.dumps(fixture), encoding="utf-8"
    )
    output = tmp_path / "hints.jsonl"
    mapping = elp.build_all_hints(root=root, output_path=output)
    assert ("9999.test", "thm:foo") in mapping
    assert ("9999.test", "thm:bar") not in mapping
    text = output.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["paper_id"] == "9999.test"
    assert rows[0]["theorem_name"] == "thm:foo"
    assert any("Cauchy-Schwarz" in h for h in rows[0]["hints"])
    # `load_hints` reads it back.
    loaded = elp.load_hints(output_path=output)
    assert loaded == {("9999.test", "thm:foo"): rows[0]["hints"]}


def test_load_hints_missing_file_returns_empty(tmp_path: Path) -> None:
    assert elp.load_hints(output_path=tmp_path / "absent.jsonl") == {}


def test_real_paper_extracted_theorems_yields_hints_for_2604_21884() -> None:
    """In-tree check: parsing the live extracted_theorems.json for
    2604.21884 surfaces structural keywords. Skipped if the file is
    missing."""
    project_root = Path(__file__).resolve().parent.parent
    f = (
        project_root
        / "reproducibility"
        / "paper_agnostic_golden10_results"
        / "2604.21884"
        / "extracted_theorems.json"
    )
    if not f.exists():
        pytest.skip("extracted_theorems.json fixture not present")
    data = json.loads(f.read_text(encoding="utf-8"))
    records = elp.build_row_records(data.get("entries", []), paper_id="2604.21884")
    # At least one record must carry a structural hint.
    enriched = [r for r in records if r["hints"]]
    assert enriched, "expected at least one row with structural hints"
