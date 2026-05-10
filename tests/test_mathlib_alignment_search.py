"""Tests for mathlib_alignment_search (Mathlib counterpart search via Leanstral).

The Leanstral call itself is mocked in tests — we exercise the orchestration
(prompt construction, JSON parsing, elaboration-check sorting, batch scanning
of paper-theory axioms) without actually invoking Mistral or lake."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from mathlib_alignment_search import (
    _SYSTEM_PROMPT,
    _USER_TEMPLATE,
    _leanstral_search,
    scan_axioms_in_paper_theory,
    search_alignment,
)


def _make_mock_client(json_response: dict) -> MagicMock:
    """Build a MagicMock that mimics `mistralai.Mistral.chat.complete`."""
    msg = MagicMock()
    msg.content = json.dumps(json_response)
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    client = MagicMock()
    client.chat.complete.return_value = response
    return client


def test_system_prompt_contains_essential_constraints() -> None:
    """The system prompt must instruct Leanstral to (a) propose real Mathlib
    names (not invented), (b) emit JSON, (c) gate confidence."""
    p = _SYSTEM_PROMPT
    assert "Mathlib" in p
    assert "JSON" in p
    assert "confidence" in p.lower()
    assert "do not invent" in p.lower() or "real, current Mathlib name" in p.lower()


def test_user_template_includes_signature_and_description_slots() -> None:
    assert "{paper_local_name}" in _USER_TEMPLATE
    assert "{signature}" in _USER_TEMPLATE
    assert "{description_block}" in _USER_TEMPLATE


def test_leanstral_search_parses_valid_json_response() -> None:
    client = _make_mock_client({
        "candidates": [
            {"mathlib_name": "Matrix.opNorm", "mathlib_signature": "Matrix m n ℝ → ℝ",
             "rationale": "operator norm", "confidence": 0.85},
            {"mathlib_name": "Matrix.frobeniusNorm", "mathlib_signature": "Matrix m n ℝ → ℝ",
             "rationale": "Frobenius alternative", "confidence": 0.7},
        ]
    })
    candidates = _leanstral_search(
        paper_local_name="nuclearNorm",
        signature="Matrix m n ℝ → ℝ",
        description="sum of singular values",
        client=client,
        model="leanstral",
    )
    assert len(candidates) == 2
    assert candidates[0]["mathlib_name"] == "Matrix.opNorm"
    assert candidates[1]["confidence"] == 0.7


def test_leanstral_search_returns_empty_on_malformed_response() -> None:
    """If Leanstral returns non-JSON garbage, the search must degrade
    gracefully to an empty candidate list (no exception)."""
    msg = MagicMock()
    msg.content = "not valid json {"
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    client = MagicMock()
    client.chat.complete.return_value = response

    candidates = _leanstral_search(
        paper_local_name="foo",
        signature="ℝ → ℝ",
        description="",
        client=client,
        model="leanstral",
    )
    assert candidates == []


def test_search_alignment_skips_elaboration_when_requested() -> None:
    """With skip_elaboration=True, elaboration_check should be 'skipped'
    for every candidate (no lake invocation)."""
    client = _make_mock_client({
        "candidates": [{"mathlib_name": "Matrix.opNorm", "confidence": 0.9}],
    })
    result = search_alignment(
        paper_id="9999.99999",
        paper_local_name="nuclearNorm",
        signature="Matrix m n ℝ → ℝ",
        description="",
        client=client,
        model="leanstral",
        skip_elaboration=True,
    )
    assert result["candidates"][0]["elaboration_check"] == "skipped"
    assert result["paper_local_name"] == "nuclearNorm"
    assert result["axiom_signature"] == "Matrix m n ℝ → ℝ"


def test_search_alignment_sorts_ok_candidates_first() -> None:
    """Candidates whose elaboration_check starts with 'ok' must appear
    before failed ones, regardless of confidence ordering."""
    client = _make_mock_client({
        "candidates": [
            {"mathlib_name": "FakeName1", "confidence": 0.9},
            {"mathlib_name": "RealName", "confidence": 0.5},
            {"mathlib_name": "FakeName2", "confidence": 0.85},
        ],
    })

    # Mock the elaboration check to mark only `RealName` as ok.
    import mathlib_alignment_search
    original = mathlib_alignment_search._elaboration_check
    def mock_elab(name: str, *, project_root: Path) -> str:
        return "ok" if name == "RealName" else "failed:unknown_identifier"
    mathlib_alignment_search._elaboration_check = mock_elab
    try:
        result = search_alignment(
            paper_id="p1",
            paper_local_name="foo",
            signature="ℝ → ℝ",
            client=client,
            model="leanstral",
            skip_elaboration=False,
        )
    finally:
        mathlib_alignment_search._elaboration_check = original

    # RealName comes first because it elaborates, despite lower confidence.
    assert result["candidates"][0]["mathlib_name"] == "RealName"
    assert result["candidates"][0]["elaboration_check"] == "ok"


def test_search_alignment_orders_failed_candidates_by_confidence() -> None:
    """Among failed candidates (or all when none elaborate), order by
    confidence descending."""
    client = _make_mock_client({
        "candidates": [
            {"mathlib_name": "Low", "confidence": 0.3},
            {"mathlib_name": "High", "confidence": 0.9},
            {"mathlib_name": "Mid", "confidence": 0.6},
        ],
    })
    result = search_alignment(
        paper_id="p1",
        paper_local_name="foo",
        signature="ℝ → ℝ",
        client=client,
        model="leanstral",
        skip_elaboration=True,
    )
    names = [c["mathlib_name"] for c in result["candidates"]]
    assert names == ["High", "Mid", "Low"]


def test_scan_axioms_in_paper_theory_returns_axiom_declarations(tmp_path: Path) -> None:
    """The batch-mode scanner walks a paper-theory `.lean` file and extracts
    every `axiom <name> : <sig>` line as a `{name, signature}` dict."""
    paper = "1234.56789"
    pt_dir = tmp_path / "Desol" / "PaperTheory"
    pt_dir.mkdir(parents=True)
    (pt_dir / f"Paper_{paper.replace('.', '_')}.lean").write_text(
        "import Mathlib\n"
        "namespace Paper_1234_56789\n"
        "axiom nuclearNorm : Matrix m n ℝ → ℝ\n"
        "axiom DyadicBlockBound : ℕ → ℕ → ℝ → ℝ\n"
        "def nonAxiom : ℝ := 0\n"
        "end Paper_1234_56789\n",
        encoding="utf-8",
    )
    targets = scan_axioms_in_paper_theory(paper, project_root=tmp_path)
    assert {t["name"] for t in targets} == {"nuclearNorm", "DyadicBlockBound"}
    assert all("signature" in t for t in targets)
    assert next(t for t in targets if t["name"] == "DyadicBlockBound")["signature"].strip().startswith("ℕ → ℕ → ℝ → ℝ")


def test_scan_axioms_in_paper_theory_returns_empty_when_no_paper_theory(tmp_path: Path) -> None:
    targets = scan_axioms_in_paper_theory("9999.99999", project_root=tmp_path)
    assert targets == []


def test_auto_register_promotes_ok_high_confidence_only(tmp_path: Path) -> None:
    """A search result whose top candidate has elaboration_check='ok' AND
    confidence >= threshold gets auto-registered into alignments.json. Lower
    confidence or non-ok elaboration is skipped."""
    from mathlib_alignment_search import auto_register_validated_candidates
    alignments_path = tmp_path / "alignments.json"
    alignments_path.write_text(json.dumps({"schema_version": "alignments.v1", "alignments": []}), encoding="utf-8")

    results = [
        # Should register: ok + high confidence.
        {
            "paper_id": "p1",
            "paper_local_name": "fooBound",
            "axiom_signature": "ℕ → ℝ",
            "candidates": [
                {"mathlib_name": "Nat.cast_le", "confidence": 0.92, "elaboration_check": "ok", "rationale": "..."},
                {"mathlib_name": "Other", "confidence": 0.5, "elaboration_check": "ok"},
            ],
        },
        # Should skip: top candidate's elaboration_check failed.
        {
            "paper_id": "p1",
            "paper_local_name": "barNorm",
            "axiom_signature": "ℝ → ℝ",
            "candidates": [
                {"mathlib_name": "FakeName", "confidence": 0.99, "elaboration_check": "failed:unknown"},
            ],
        },
        # Should skip: confidence below threshold.
        {
            "paper_id": "p1",
            "paper_local_name": "bazFunc",
            "axiom_signature": "ℝ → ℝ",
            "candidates": [
                {"mathlib_name": "Real.exp", "confidence": 0.6, "elaboration_check": "ok"},
            ],
        },
    ]
    summary = auto_register_validated_candidates(
        search_results=results,
        project_root=tmp_path,
        confidence_threshold=0.85,
        alignments_path=alignments_path,
    )
    assert summary["registered"] == 1
    assert summary["skipped_no_ok"] == 1
    assert summary["skipped_low_confidence"] == 1
    persisted = json.loads(alignments_path.read_text())
    names = [a["paper_local_name"] for a in persisted["alignments"]]
    assert names == ["fooBound"]
    assert persisted["alignments"][0]["mathlib_target"] == "Nat.cast_le"
    assert persisted["alignments"][0]["kind"] == "auto_registered_via_mathlib_search"


def test_auto_register_skips_already_registered(tmp_path: Path) -> None:
    """If (paper_id, paper_local_name) already exists in alignments.json,
    the auto-registration is a no-op (preserves the existing entry's proof
    field, which may point to a real human-written theorem)."""
    from mathlib_alignment_search import auto_register_validated_candidates
    alignments_path = tmp_path / "alignments.json"
    alignments_path.write_text(json.dumps({
        "schema_version": "alignments.v1",
        "alignments": [
            {"paper_id": "p1", "paper_local_name": "fooBound",
             "fully_qualified": "Paper_p1.fooBound",
             "mathlib_target": "ManuallyChosen", "proof": "Desol.PaperAlignments.foo_real_proof",
             "kind": "real_lean_theorem"},
        ],
    }), encoding="utf-8")
    results = [{
        "paper_id": "p1",
        "paper_local_name": "fooBound",
        "candidates": [{"mathlib_name": "Nat.cast_le", "confidence": 0.99, "elaboration_check": "ok"}],
    }]
    summary = auto_register_validated_candidates(
        search_results=results,
        project_root=tmp_path,
        confidence_threshold=0.85,
        alignments_path=alignments_path,
    )
    assert summary["registered"] == 0
    assert summary["skipped_already_registered"] == 1
    persisted = json.loads(alignments_path.read_text())
    assert persisted["alignments"][0]["proof"] == "Desol.PaperAlignments.foo_real_proof"
    assert persisted["alignments"][0]["mathlib_target"] == "ManuallyChosen"
