"""Tests for rescue_translation_limited_rows.

Hermetic: every test stubs the translator + elaboration probe so we
never shell to lake. The rescue module is one of the "intersection"
files that pulls in the translator + the prove-loop probe; tests use
monkeypatch to keep that surface area inert.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import rescue_translation_limited_rows as rtl


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "verification_ledgers").mkdir(parents=True)
    return tmp_path


def _make_row(**overrides) -> dict:
    base = {
        "paper_id": "9999.99999",
        "theorem_name": "lem_x",
        "status": "TRANSLATION_LIMITED",
        "lean_statement": "theorem lem_x : (0 : ℕ) = 0",
        "failure_kind": "import_mismatch",
        "error_message": "final_translation_gate:trivial_nat0eq0_target",
        "source_latex": "For every $k \\ge 1$, the bound holds.",
        "validation_gates": {"lean_proof_closed": False},
        "gate_failures": ["translation_limited_statement"],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# rescue_row: action classification
# ---------------------------------------------------------------------------


def test_skipped_when_source_latex_empty(monkeypatch, project: Path) -> None:
    row = _make_row(source_latex="")
    outcome = rtl.rescue_row(row, project_root=project)
    assert outcome.action == "skipped"
    assert outcome.elab_detail == "no_source_latex"


def test_stays_tl_when_translator_refuses(monkeypatch, project: Path) -> None:
    monkeypatch.setattr(rtl, "_retranslate", lambda *a, **kw: (None, {}))
    row = _make_row()
    outcome = rtl.rescue_row(row, project_root=project)
    assert outcome.action == "stays_tl_translator_refused"
    assert outcome.elaborates is None


def test_stays_tl_when_new_decl_still_trivial(monkeypatch, project: Path) -> None:
    monkeypatch.setattr(
        rtl,
        "_retranslate",
        lambda *a, **kw: ("theorem lem_x : (0 : ℕ) = 0 := by", {}),
    )
    row = _make_row()
    outcome = rtl.rescue_row(row, project_root=project)
    assert outcome.action == "stays_tl_trivial"
    # The new decl is still recorded for forensics even when we don't promote it.
    assert outcome.lean_statement_new.startswith("theorem lem_x")
    assert outcome.tl_reason == "trivial_nat0eq0_target"


def test_stays_tl_when_new_decl_does_not_elaborate(monkeypatch, project: Path) -> None:
    monkeypatch.setattr(
        rtl,
        "_retranslate",
        lambda *a, **kw: ("theorem lem_x (k : ℕ) : k ≥ 1 := by", {}),
    )
    monkeypatch.setattr(
        rtl, "_elaborates", lambda **kw: (False, "file_check_fail:parse error tail")
    )
    row = _make_row()
    outcome = rtl.rescue_row(row, project_root=project)
    assert outcome.action == "stays_tl_no_elaborate"
    assert outcome.elaborates is False
    assert "parse error" in outcome.elab_detail


def test_demotes_when_new_decl_elaborates(monkeypatch, project: Path) -> None:
    new_decl = "theorem lem_x (k : ℕ) : k ≥ 1 := by"
    monkeypatch.setattr(rtl, "_retranslate", lambda *a, **kw: (new_decl, {}))
    monkeypatch.setattr(rtl, "_elaborates", lambda **kw: (True, ""))
    row = _make_row()
    outcome = rtl.rescue_row(row, project_root=project)
    assert outcome.action == "demoted_to_unresolved"
    assert outcome.elaborates is True
    assert outcome.lean_statement_new == new_decl


def test_skip_elaboration_short_circuits(monkeypatch, project: Path) -> None:
    monkeypatch.setattr(
        rtl, "_retranslate", lambda *a, **kw: ("theorem lem_x : True := by", {})
    )
    sentinel = {"called": False}

    def _should_not_be_called(**kw):
        sentinel["called"] = True
        return True, ""

    monkeypatch.setattr(rtl, "_elaborates", _should_not_be_called)
    row = _make_row()
    outcome = rtl.rescue_row(row, project_root=project, skip_elaboration=True)
    # `theorem lem_x : True := by` is detected as `trivial_true_target` by
    # `_translation_limited_reason`, so the row stays TL via the trivial path.
    # The point of this test is that elaboration is NEVER invoked under
    # skip_elaboration regardless of which branch fires.
    assert sentinel["called"] is False
    assert outcome.action in {"stays_tl_trivial", "skipped"}


# ---------------------------------------------------------------------------
# _apply_demotion_to_row: invariants on the demotion mutation
# ---------------------------------------------------------------------------


def test_apply_demotion_resets_status_and_records_audit() -> None:
    row = _make_row()
    rtl._apply_demotion_to_row(row, new_decl="theorem lem_x (k : ℕ) : k ≥ 1 := by")
    assert row["status"] == "UNRESOLVED"
    assert row["proved"] is False
    assert row["lean_statement"] == "theorem lem_x (k : ℕ) : k ≥ 1 := by"
    assert row["failure_kind"] == "proof_search_unattempted"
    audit = row["translation_rescue"]
    assert audit["previous_status"] == "TRANSLATION_LIMITED"
    assert audit["previous_lean_statement"] == "theorem lem_x : (0 : ℕ) = 0"
    assert audit["rescue_method"] == "deterministic_typed_ir_retranslation"
    # `translation_limited_statement` must be dropped from gate_failures.
    assert "translation_limited_statement" not in row["gate_failures"]
    assert "translation_rescue:deterministic_retranslation" in row["claim_equivalence_notes"]


def test_apply_demotion_preserves_other_review_evidence() -> None:
    """The rescue must not nuke review evidence stored on the row — the
    re-translated row will need its alignment evidence to re-graduate."""
    row = _make_row()
    row["claim_equivalence_verdict"] = "unclear"
    row["semantic_equivalence_artifact"] = {"x": 1}
    row["provenance"] = {"paper_id": "9999.99999"}
    rtl._apply_demotion_to_row(row, new_decl="theorem lem_x (k : ℕ) : k ≥ 1 := by")
    assert row["claim_equivalence_verdict"] == "unclear"
    assert row["semantic_equivalence_artifact"] == {"x": 1}
    assert row["provenance"] == {"paper_id": "9999.99999"}


def test_attach_attempted_audit_preserves_status() -> None:
    row = _make_row()
    outcome = rtl.RescueOutcome(
        paper_id="9999.99999",
        theorem_name="lem_x",
        action="stays_tl_no_elaborate",
        lean_statement_old=row["lean_statement"],
        lean_statement_new="theorem lem_x (k : ℕ) : Γ: = U := by",
        elaborates=False,
        elab_detail="parse error",
        tl_reason="",
        trivial=False,
    )
    rtl._attach_attempted_audit(row, outcome=outcome)
    # Status MUST remain TRANSLATION_LIMITED — failed rescue does not change row.
    assert row["status"] == "TRANSLATION_LIMITED"
    audit = row["translation_rescue_attempt"]
    assert audit["elaborates"] is False
    assert audit["result"] == "stays_tl_no_elaborate"


# ---------------------------------------------------------------------------
# rescue_ledger_file: integration over a ledger
# ---------------------------------------------------------------------------


def test_ledger_walk_demotes_only_elaborating_rows(monkeypatch, project: Path) -> None:
    """A mixed ledger with one elaborating + one non-elaborating + one
    trivial TL row must produce exactly one demotion and two attempted-audit
    annotations."""
    ledger_path = project / "output" / "verification_ledgers" / "9999.99999.json"
    rows = [
        _make_row(theorem_name="lem_good", lean_statement="theorem lem_good : (0 : ℕ) = 0"),
        _make_row(theorem_name="lem_bad", lean_statement="theorem lem_bad : (0 : ℕ) = 0"),
        _make_row(theorem_name="lem_trivial", lean_statement="theorem lem_trivial : (0 : ℕ) = 0"),
        # An UNRESOLVED row that should be IGNORED entirely.
        _make_row(theorem_name="lem_ur", status="UNRESOLVED"),
    ]
    ledger_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    def fake_retranslate(latex, *, paper_id, theorem_name):
        if theorem_name == "lem_good":
            return "theorem lem_good (k : ℕ) : k ≥ 1 := by", {}
        if theorem_name == "lem_bad":
            return "theorem lem_bad (k : ℕ) : k ≥ 1 := by", {}
        if theorem_name == "lem_trivial":
            return "theorem lem_trivial : (0 : ℕ) = 0 := by", {}
        raise AssertionError("UR row must not call retranslate")

    monkeypatch.setattr(rtl, "_retranslate", fake_retranslate)

    def fake_elaborates(**kw):
        decl = kw["decl"]
        return (True, "") if "lem_good" in decl else (False, "parse error")

    monkeypatch.setattr(rtl, "_elaborates", fake_elaborates)

    outcomes = rtl.rescue_ledger_file(
        ledger_path,
        project_root=project,
        write=True,
    )
    actions = {o.theorem_name: o.action for o in outcomes}
    assert actions == {
        "lem_good": "demoted_to_unresolved",
        "lem_bad": "stays_tl_no_elaborate",
        "lem_trivial": "stays_tl_trivial",
    }
    # Re-read the ledger to confirm the mutation persisted.
    after = json.loads(ledger_path.read_text(encoding="utf-8"))
    by_name = {r["theorem_name"]: r for r in after}
    assert by_name["lem_good"]["status"] == "UNRESOLVED"
    assert by_name["lem_good"]["lean_statement"] == "theorem lem_good (k : ℕ) : k ≥ 1 := by"
    assert by_name["lem_bad"]["status"] == "TRANSLATION_LIMITED"
    assert by_name["lem_bad"]["translation_rescue_attempt"]["elaborates"] is False
    assert by_name["lem_trivial"]["status"] == "TRANSLATION_LIMITED"
    assert by_name["lem_trivial"]["translation_rescue_attempt"]["trivial"] is True
    # The UR row is untouched.
    assert "translation_rescue" not in by_name["lem_ur"]
    assert "translation_rescue_attempt" not in by_name["lem_ur"]


def test_ledger_walk_dry_run_does_not_persist(monkeypatch, project: Path) -> None:
    ledger_path = project / "output" / "verification_ledgers" / "9999.99999.json"
    row = _make_row()
    ledger_path.write_text(json.dumps([row], indent=2, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        rtl,
        "_retranslate",
        lambda *a, **kw: ("theorem lem_x (k : ℕ) : k ≥ 1 := by", {}),
    )
    monkeypatch.setattr(rtl, "_elaborates", lambda **kw: (True, ""))
    outcomes = rtl.rescue_ledger_file(
        ledger_path,
        project_root=project,
        write=False,
    )
    assert outcomes[0].action == "demoted_to_unresolved"
    after = json.loads(ledger_path.read_text(encoding="utf-8"))
    # Dry run: the ledger is unchanged.
    assert after[0]["status"] == "TRANSLATION_LIMITED"


def test_ledger_walk_respects_targets_filter(monkeypatch, project: Path) -> None:
    ledger_path = project / "output" / "verification_ledgers" / "9999.99999.json"
    rows = [
        _make_row(theorem_name="lem_one"),
        _make_row(theorem_name="lem_two"),
    ]
    ledger_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    seen: list[str] = []

    def fake_retranslate(latex, *, paper_id, theorem_name):
        seen.append(theorem_name)
        return "theorem t : (0 : ℕ) = 0 := by", {}

    monkeypatch.setattr(rtl, "_retranslate", fake_retranslate)
    rtl.rescue_ledger_file(
        ledger_path,
        project_root=project,
        write=False,
        targets={("9999.99999", "lem_two")},
    )
    assert seen == ["lem_two"]
