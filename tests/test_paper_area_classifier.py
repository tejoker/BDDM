"""Tests for paper_area_classifier."""

from __future__ import annotations

import json
from pathlib import Path

from paper_area_classifier import (
    AREA_TO_IMPORTS,
    classify_paper,
    classify_text,
)


def test_classify_text_analysis_keywords() -> None:
    text = (
        "Let f be Lipschitz continuous and assume the Sobolev embedding holds. "
        "We prove a Strichartz estimate for the Schrödinger operator on Lp."
    )
    area, scores = classify_text(text)
    assert area == "analysis"
    assert scores["analysis"] >= 4


def test_classify_text_probability_keywords() -> None:
    text = (
        "Let X_n be a martingale with stationary increments. "
        "Almost surely the random variable converges in probability. "
        "Brownian motion under filtration F_t."
    )
    area, _ = classify_text(text)
    assert area == "probability"


def test_classify_text_algebra_keywords() -> None:
    text = (
        "Let R be a commutative ring with ideal I. "
        "We construct an irreducible representation of the Lie algebra g via tensor product."
    )
    area, _ = classify_text(text)
    assert area == "algebra"


def test_classify_text_combinatorics_keywords() -> None:
    text = (
        "Consider a graph G with vertex set V and edge set E. "
        "We prove a Ramsey-type bound on the chromatic number "
        "via a bijection between matchings and colorings."
    )
    area, _ = classify_text(text)
    assert area == "combinatorics"


def test_classify_text_numbertheory_keywords() -> None:
    text = (
        "Let p be a prime divisor of n. "
        "We bound the Euler totient using gcd(a, n) = 1 for a coprime to n. "
        "The arithmetic progression has integer solutions modulo n."
    )
    area, _ = classify_text(text)
    assert area == "numbertheory"


def test_classify_text_empty_returns_generic() -> None:
    area, scores = classify_text("")
    assert area == "generic"
    assert scores == {}


def test_classify_text_no_keywords_returns_generic() -> None:
    area, _ = classify_text("This is some random text without math vocabulary.")
    assert area == "generic"


def test_classify_text_tie_prefers_more_specific_area() -> None:
    """When two areas tie on hit count, the preference order
    (probability > combinatorics > numbertheory > algebra > analysis)
    disambiguates. Probability-tinted analysis papers should land under
    probability; algebra-tinted analysis papers should still land under analysis
    (since algebra has lower preference)."""
    # "ring" is a single algebra hit; "operator" is a single analysis hit.
    # Algebra should win because "operator" is in analysis's set but algebra
    # has higher preference when tied.
    area, _ = classify_text("ring operator")
    # 1-1 tie → preference order picks algebra over analysis.
    assert area == "algebra"


def test_classify_paper_reads_extracted_theorems_json(tmp_path: Path) -> None:
    paper = "1234.56789"
    repo = tmp_path / "reproducibility" / "paper_agnostic_golden10_results" / paper
    repo.mkdir(parents=True)
    (repo / "extracted_theorems.json").write_text(json.dumps({
        "paper_id": paper,
        "entries": [
            {"label": "thm:1", "statement": "Let X be a martingale with stationary increments."},
            {"label": "thm:2", "statement": "The random variable converges almost surely."},
        ],
    }), encoding="utf-8")
    result = classify_paper(paper, tmp_path)
    assert result["area"] == "probability"
    assert result["scores"]["probability"] >= 2


def test_classify_paper_falls_back_to_lean_comments(tmp_path: Path) -> None:
    """When extracted_theorems.json is absent, the classifier reads
    `-- Statement (LaTeX): ...` lines from the .lean file's comment headers."""
    paper = "1234.56789"
    out = tmp_path / "output"
    out.mkdir()
    (out / f"{paper}.lean").write_text(
        "namespace ArxivPaper\n"
        "-- [theorem] thm:foo\n"
        "-- Statement (LaTeX): Consider a graph G with vertex set V and a coloring of E.\n"
        "theorem foo : True := trivial\n"
        "end ArxivPaper\n",
        encoding="utf-8",
    )
    result = classify_paper(paper, tmp_path)
    assert result["area"] == "combinatorics"


def test_classify_paper_returns_generic_when_no_evidence(tmp_path: Path) -> None:
    result = classify_paper("9999.99999", tmp_path)
    assert result["area"] == "generic"
    assert result["scores"] == {}


def test_area_to_imports_covers_every_area() -> None:
    """Every classifiable area must have a corresponding Mathlib import bundle."""
    for area in ("analysis", "probability", "algebra", "combinatorics", "numbertheory", "generic"):
        assert area in AREA_TO_IMPORTS, f"Missing imports for area: {area}"
        assert isinstance(AREA_TO_IMPORTS[area], list)
        assert len(AREA_TO_IMPORTS[area]) >= 1
