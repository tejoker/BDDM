"""Tests for the corpus axiom-budget audit (`audit_axioms.py`)."""

from __future__ import annotations

import json
from pathlib import Path

from audit_axioms import _classify_row, _kind_of_debt, audit_corpus


def test_classify_row_release_eligible() -> None:
    """A FULLY_PROVEN row with no debt and no other failing gate is release-eligible."""
    r = {"status": "FULLY_PROVEN", "axiom_debt": [], "gate_failures": []}
    assert _classify_row(r) == "release_eligible"


def test_classify_row_axiom_backed_when_only_paper_local_debt() -> None:
    """Closed proof + only paper-local axiom debt + no other gate failures → axiom_backed."""
    r = {
        "status": "AXIOM_BACKED",
        "axiom_debt": ["paper_definition_stub:Multisegment"],
        "gate_failures": ["no_paper_axiom_debt"],
    }
    assert _classify_row(r) == "axiom_backed"


def test_classify_row_intermediary_when_other_gates_fail() -> None:
    r = {
        "status": "INTERMEDIARY_PROVEN",
        "axiom_debt": ["paper_definition_stub:Multisegment"],
        "gate_failures": ["claim_equivalent", "no_paper_axiom_debt"],
    }
    assert _classify_row(r) == "intermediary"


def test_classify_row_unresolved_passthrough() -> None:
    assert _classify_row({"status": "UNRESOLVED"}) == "unresolved"
    assert _classify_row({"status": "FLAWED"}) == "flawed"
    assert _classify_row({"status": "TRANSLATION_LIMITED"}) == "translation_limited"


def test_kind_of_debt_categorises_correctly() -> None:
    assert _kind_of_debt("paper_definition_stub:foo") == "paper_definition_stub"
    assert _kind_of_debt("paper_symbol:bar") == "paper_symbol"
    assert _kind_of_debt("paper_local_lemma:baz") == "paper_local_lemma"
    assert _kind_of_debt("missing_mathlib_theorem:Mathlib.Foo") == "missing_mathlib_theorem"
    assert _kind_of_debt("bare") == "bare"
    assert _kind_of_debt("") == "bare"


def test_audit_corpus_filters_to_canonical_ledgers(tmp_path: Path) -> None:
    """The audit must skip dev-variant ledgers (smoke, repair_candidates, etc.)."""
    led = tmp_path / "verification_ledgers"
    led.mkdir()
    # Canonical
    (led / "1234.56789.json").write_text(json.dumps({"entries": [{"theorem_name": "T", "status": "FULLY_PROVEN", "axiom_debt": []}]}), encoding="utf-8")
    # Dev variants — must be skipped
    (led / "1234.56789_smoke.json").write_text(json.dumps({"entries": [{"theorem_name": "T", "status": "FLAWED"}]}), encoding="utf-8")
    (led / "ab_repair_topk0.json").write_text(json.dumps({"entries": [{"theorem_name": "T", "status": "FLAWED"}]}), encoding="utf-8")

    audit = audit_corpus(led)
    assert "1234.56789" in audit["papers"]
    assert "1234.56789_smoke" not in audit["papers"]
    assert "ab_repair_topk0" not in audit["papers"]


def test_audit_corpus_aggregates_kinds_per_paper(tmp_path: Path) -> None:
    led = tmp_path / "verification_ledgers"
    led.mkdir()
    (led / "0000.00001.json").write_text(json.dumps({"entries": [
        {"theorem_name": "T1", "status": "AXIOM_BACKED",
         "axiom_debt": ["paper_definition_stub:Multisegment", "paper_symbol:dual"]},
        {"theorem_name": "T2", "status": "INTERMEDIARY_PROVEN",
         "axiom_debt": ["paper_definition_stub:L_alpha"]},
    ]}), encoding="utf-8")

    audit = audit_corpus(led)
    paper = audit["papers"]["0000.00001"]
    assert paper["rows"] == 2
    assert paper["by_axiom_kind"]["paper_definition_stub"] == 2
    assert paper["by_axiom_kind"]["paper_symbol"] == 1
