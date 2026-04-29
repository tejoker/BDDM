from __future__ import annotations

import json
from pathlib import Path

from repair_extracted_theorem_spans import repair_file


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_repair_extracted_theorem_spans_attaches_native_span(tmp_path: Path) -> None:
    tex = tmp_path / "source.tex"
    tex.write_text(
        "\\begin{theorem}\n"
        "\\label{thm:demo}\n"
        "For every natural number n, n equals n.\n"
        "\\end{theorem}\n",
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence" / "2300.00001" / "extracted_theorems.json"
    _write_json(
        evidence,
        {
            "paper_id": "2300.00001",
            "entries": [
                {
                    "kind": "theorem",
                    "name": "thm:demo",
                    "statement": "\\label{thm:demo}\nFor every natural number n, n equals n.",
                    "proof": "",
                    "source_file": str(tex),
                }
            ],
        },
    )

    result = repair_file(evidence, project_root=tmp_path, write=True)
    repaired = json.loads(evidence.read_text(encoding="utf-8"))

    assert result["repaired_rows"] == 1
    assert repaired["entries"][0]["source_span"]["span_confidence"] == "exact_extractor"
    assert repaired["entries"][0]["source_span_id"].startswith("srcspan_")
    assert repaired["span_repair"]["repaired_rows"] == 1


def test_repair_extracted_theorem_spans_leaves_unmatched_rows(tmp_path: Path) -> None:
    tex = tmp_path / "source.tex"
    tex.write_text("\\begin{theorem}Different statement.\\end{theorem}", encoding="utf-8")
    evidence = tmp_path / "evidence" / "2300.00001" / "extracted_theorems.json"
    _write_json(
        evidence,
        {
            "paper_id": "2300.00001",
            "entries": [
                {
                    "kind": "theorem",
                    "name": "thm:missing",
                    "statement": "Missing statement.",
                    "source_file": str(tex),
                }
            ],
        },
    )

    result = repair_file(evidence, project_root=tmp_path, write=True)
    repaired = json.loads(evidence.read_text(encoding="utf-8"))

    assert result["repaired_rows"] == 0
    assert result["unmatched_rows"] + result["ambiguous_rows"] == 1
    assert "source_span" not in repaired["entries"][0]


def test_repair_extracted_theorem_spans_resolves_label_collision_with_statement(tmp_path: Path) -> None:
    tex = tmp_path / "source.tex"
    tex.write_text(
        "\\begin{theorem}\n"
        "\\label{thm:foo}\n"
        "First statement.\n"
        "\\end{theorem}\n"
        "\\begin{theorem}\n"
        "\\label{thm_foo}\n"
        "Second statement.\n"
        "\\end{theorem}\n",
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence" / "2300.00001" / "extracted_theorems.json"
    _write_json(
        evidence,
        {
            "paper_id": "2300.00001",
            "entries": [
                {
                    "kind": "theorem",
                    "name": "thm:foo",
                    "statement": "\\label{thm:foo}\nFirst statement.",
                    "source_file": str(tex),
                }
            ],
        },
    )

    result = repair_file(evidence, project_root=tmp_path, write=True)
    repaired = json.loads(evidence.read_text(encoding="utf-8"))

    assert result["repaired_rows"] == 1
    assert result["match_status_counts"]["matched"] == 1
    assert repaired["entries"][0]["source_span"]["span_confidence"] == "exact_extractor"
    assert repaired["entries"][0]["source_match_repair"]["match_status"] == "matched"
