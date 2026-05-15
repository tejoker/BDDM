"""Hermetic tests for ``scripts/promote_closed_aux_as_rows.py``.

No lake, no Mistral, no HTTP. Each test fabricates a synthetic
``aux_records`` list and a parent ledger entry, then asserts the module's
derived-row schema and gate behaviour. The optional ``validate_elaboration``
parameter is exercised with stub callables.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import promote_closed_aux_as_rows as paux


# --- Fixtures -------------------------------------------------------------


def _parent_entry() -> dict[str, object]:
    """A minimal parent ledger row shaped like an UNRESOLVED canonical row.

    The validation_gates dict carries `provenance_linked=True`,
    `claim_equivalent=True`, `independent_semantic_equivalence_evidence=True`,
    and `no_paper_axiom_debt=True` so the derived row's status decision
    can fall to either FP or IP depending on the test.
    """
    return {
        "theorem_name": "parent_thm",
        "lean_statement": "theorem parent_thm : True",
        "status": "UNRESOLVED",
        "validation_gates": {
            "lean_proof_closed": False,
            "step_verdict_verified": False,
            "assumptions_grounded": True,
            "provenance_linked": True,
            "translation_fidelity_ok": True,
            "status_alignment_ok": True,
            "dependency_trust_complete": True,
            "reproducible_env": True,
            "claim_equivalent": True,
            "independent_semantic_equivalence_evidence": True,
            "semantic_adversarial_checks_passed": True,
            "no_paper_axiom_debt": True,
        },
        "gate_failures": ["lean_proof_closed", "step_verdict_verified"],
        "claim_equivalence_verdict": "equivalent",
        "provenance": {"paper_id": "9999.99999", "section": "", "label": "thm:parent"},
        "trust_class": "TRUST_INTERNAL_PROVED",
        "trust_reference": "internal_verified_pipeline",
        "reproducible_env": True,
        "translation_fidelity_score": 0.95,
        "status_alignment_score": 0.95,
        "review_policy": "release_eligible",
        "reviewer_type": "hybrid",
        "axiom_debt": [],
        "axiom_debt_hash": "",
    }


def _aux(
    name: str,
    sig: str,
    body: str,
    *,
    compose_hint: str = "",
) -> dict[str, str]:
    return {
        "aux_name": name,
        "aux_signature": sig,
        "proof_body": body,
        "compose_hint": compose_hint,
    }


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


def test_derived_theorem_name_uses_double_colon_separator() -> None:
    assert paux.derived_theorem_name("parent", "aux_x") == "parent::aux::aux_x"


def test_derived_theorem_name_strips_namespace_qualification() -> None:
    # `Foo.Bar.parent` -> bare suffix; `Ns.aux` -> bare suffix.
    assert (
        paux.derived_theorem_name("Foo.Bar.parent", "Ns.aux_x")
        == "parent::aux::aux_x"
    )


def test_derived_theorem_name_falls_back_for_empty_inputs() -> None:
    assert paux.derived_theorem_name("", "") == "thm::aux::aux"


def test_is_derived_aux_row_detects_by_name() -> None:
    assert paux.is_derived_aux_row({"theorem_name": "parent::aux::a"}) is True
    assert paux.is_derived_aux_row({"theorem_name": "plain_theorem"}) is False
    assert (
        paux.is_derived_aux_row(
            {"theorem_name": "x", "proof_method": paux.PROMOTION_PROTOCOL}
        )
        is True
    )
    assert (
        paux.is_derived_aux_row(
            {"theorem_name": "y", "ledger_role": paux.DERIVED_LEDGER_ROLE}
        )
        is True
    )


# ---------------------------------------------------------------------------
# Happy path: 3 closed aux -> 3 derived rows
# ---------------------------------------------------------------------------


def test_three_closed_aux_produce_three_new_rows(tmp_path: Path) -> None:
    parent = _parent_entry()
    aux_records = [
        _aux("aux_one", "theorem aux_one (n : Nat) : n + 0 = n := by sorry",
             "exact Nat.add_zero n"),
        _aux("aux_two", "theorem aux_two (n : Nat) : 0 + n = n := by sorry",
             "exact Nat.zero_add n"),
        _aux("aux_three", "theorem aux_three (n : Nat) : n + 1 = 1 + n := by sorry",
             "exact (Nat.add_comm n 1)"),
    ]
    entries: list[dict] = [parent]
    results = paux.promote_closed_aux(
        paper_id="9999.99999",
        parent_theorem_name="parent_thm",
        parent_entry=parent,
        aux_records=aux_records,
        project_root=tmp_path,
        ledger_entries=entries,
        validate_elaboration=None,
    )
    assert len(results) == 3
    promoted = [r for r in results if r["status"] == "promoted"]
    assert len(promoted) == 3
    for r in promoted:
        assert r["derived_name"].startswith("parent_thm::aux::")
        assert isinstance(r["row"], dict)

    # Caller would append these to the ledger. Verify each row has the
    # canonical shape we promised.
    rows = [r["row"] for r in promoted]
    for row in rows:
        assert row["status"] in {"FULLY_PROVEN", "AXIOM_BACKED", "INTERMEDIARY_PROVEN"}
        assert row["proof_method"] == paux.PROMOTION_PROTOCOL
        assert row["validation_gates"]["lean_proof_closed"] is True
        assert row["validation_gates"]["step_verdict_verified"] is True
        assert row["step_verdict"] == "VERIFIED"
        assert row["proved"] is True
        assert row["ledger_role"] == paux.DERIVED_LEDGER_ROLE
        assert row["parent_theorem_name"] == "parent_thm"
        assert any(
            e.get("event") == "derived_aux_from_factor" and e.get("parent") == "parent_thm"
            for e in row["audit_trail"]
        )


# ---------------------------------------------------------------------------
# Status decision
# ---------------------------------------------------------------------------


def test_status_is_fp_when_parent_gates_complete(tmp_path: Path) -> None:
    parent = _parent_entry()
    results = paux.promote_closed_aux(
        paper_id="9999.99999",
        parent_theorem_name="parent_thm",
        parent_entry=parent,
        aux_records=[
            _aux("aux_one", "theorem aux_one : Nat := by sorry", "exact 0"),
        ],
        project_root=tmp_path,
        ledger_entries=[parent],
        validate_elaboration=None,
    )
    assert results[0]["status"] == "promoted"
    assert results[0]["row"]["status"] == "FULLY_PROVEN"


def test_status_is_ab_when_axiom_debt_present(tmp_path: Path) -> None:
    parent = _parent_entry()
    parent["validation_gates"]["no_paper_axiom_debt"] = False
    results = paux.promote_closed_aux(
        paper_id="9999.99999",
        parent_theorem_name="parent_thm",
        parent_entry=parent,
        aux_records=[
            _aux("aux_one", "theorem aux_one : Nat := by sorry", "exact 0"),
        ],
        project_root=tmp_path,
        ledger_entries=[parent],
        validate_elaboration=None,
    )
    assert results[0]["row"]["status"] == "AXIOM_BACKED"


def test_status_is_ip_when_evidence_gates_missing(tmp_path: Path) -> None:
    parent = _parent_entry()
    parent["validation_gates"]["claim_equivalent"] = False
    results = paux.promote_closed_aux(
        paper_id="9999.99999",
        parent_theorem_name="parent_thm",
        parent_entry=parent,
        aux_records=[
            _aux("aux_one", "theorem aux_one : Nat := by sorry", "exact 0"),
        ],
        project_root=tmp_path,
        ledger_entries=[parent],
        validate_elaboration=None,
    )
    assert results[0]["row"]["status"] == "INTERMEDIARY_PROVEN"


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_refuses_forbidden_token_in_proof_body(tmp_path: Path) -> None:
    parent = _parent_entry()
    results = paux.promote_closed_aux(
        paper_id="9999.99999",
        parent_theorem_name="parent_thm",
        parent_entry=parent,
        aux_records=[
            _aux("aux_bad", "theorem aux_bad : Nat := by sorry", "sorry"),
        ],
        project_root=tmp_path,
        ledger_entries=[parent],
        validate_elaboration=None,
    )
    assert len(results) == 1
    assert results[0]["status"].startswith("refused_forbidden_token")
    assert "row" not in results[0]


def test_refuses_admit_body(tmp_path: Path) -> None:
    parent = _parent_entry()
    results = paux.promote_closed_aux(
        paper_id="9999.99999",
        parent_theorem_name="parent_thm",
        parent_entry=parent,
        aux_records=[
            _aux("aux_bad", "theorem aux_bad : Nat := by sorry", "exact 0; admit"),
        ],
        project_root=tmp_path,
        ledger_entries=[parent],
        validate_elaboration=None,
    )
    assert results[0]["status"].startswith("refused_forbidden_token")


def test_refuses_trivialized_signature(tmp_path: Path) -> None:
    parent = _parent_entry()
    # `theorem foo : True := by sorry` is the canonical trivialized shape;
    # `_is_trivialized_signature` flags it.
    results = paux.promote_closed_aux(
        paper_id="9999.99999",
        parent_theorem_name="parent_thm",
        parent_entry=parent,
        aux_records=[
            _aux("aux_triv", "theorem aux_triv : True := by sorry", "trivial"),
        ],
        project_root=tmp_path,
        ledger_entries=[parent],
        validate_elaboration=None,
    )
    assert results[0]["status"] == "refused_trivialized_signature"


def test_refuses_empty_proof_body(tmp_path: Path) -> None:
    parent = _parent_entry()
    results = paux.promote_closed_aux(
        paper_id="9999.99999",
        parent_theorem_name="parent_thm",
        parent_entry=parent,
        aux_records=[
            _aux("aux_one", "theorem aux_one : Nat := by sorry", ""),
        ],
        project_root=tmp_path,
        ledger_entries=[parent],
        validate_elaboration=None,
    )
    assert results[0]["status"] == "refused_empty_proof_body"


def test_refuses_elaboration_gate_when_validator_rejects(tmp_path: Path) -> None:
    parent = _parent_entry()
    def _always_fail(decl: str) -> tuple[bool, str]:
        return False, "fake_elab_error: nonsense"
    results = paux.promote_closed_aux(
        paper_id="9999.99999",
        parent_theorem_name="parent_thm",
        parent_entry=parent,
        aux_records=[
            _aux("aux_one", "theorem aux_one (n : Nat) : n + 0 = n",
                 "exact Nat.add_zero n"),
        ],
        project_root=tmp_path,
        ledger_entries=[parent],
        validate_elaboration=_always_fail,
    )
    assert results[0]["status"] == "refused_elaboration_gate"
    assert "fake_elab_error" in results[0]["error"]


def test_passes_when_validator_accepts(tmp_path: Path) -> None:
    parent = _parent_entry()
    def _always_ok(decl: str) -> tuple[bool, str]:
        return True, ""
    results = paux.promote_closed_aux(
        paper_id="9999.99999",
        parent_theorem_name="parent_thm",
        parent_entry=parent,
        aux_records=[
            _aux("aux_one", "theorem aux_one (n : Nat) : n + 0 = n",
                 "exact Nat.add_zero n"),
        ],
        project_root=tmp_path,
        ledger_entries=[parent],
        validate_elaboration=_always_ok,
    )
    assert results[0]["status"] == "promoted"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_already_present_derived_row_is_idempotent(tmp_path: Path) -> None:
    parent = _parent_entry()
    # Pre-seed the ledger with the derived row.
    derived_name = paux.derived_theorem_name("parent_thm", "aux_one")
    entries = [parent, {"theorem_name": derived_name, "status": "INTERMEDIARY_PROVEN"}]
    results = paux.promote_closed_aux(
        paper_id="9999.99999",
        parent_theorem_name="parent_thm",
        parent_entry=parent,
        aux_records=[
            _aux("aux_one", "theorem aux_one (n : Nat) : n + 0 = n := by sorry",
                 "exact Nat.add_zero n"),
        ],
        project_root=tmp_path,
        ledger_entries=entries,
        validate_elaboration=None,
    )
    assert len(results) == 1
    assert results[0]["status"] == "idempotent"
    assert "row" not in results[0]


def test_duplicate_aux_names_in_same_batch_dedupe(tmp_path: Path) -> None:
    parent = _parent_entry()
    results = paux.promote_closed_aux(
        paper_id="9999.99999",
        parent_theorem_name="parent_thm",
        parent_entry=parent,
        aux_records=[
            _aux("aux_dup", "theorem aux_dup : Nat := by sorry", "exact 0"),
            _aux("aux_dup", "theorem aux_dup : Nat := by sorry", "exact 1"),
        ],
        project_root=tmp_path,
        ledger_entries=[parent],
        validate_elaboration=None,
    )
    statuses = [r["status"] for r in results]
    assert statuses.count("promoted") == 1
    assert statuses.count("idempotent") == 1


# ---------------------------------------------------------------------------
# Schema details: lean_statement strips proof body
# ---------------------------------------------------------------------------


def test_lean_statement_strips_proof_body(tmp_path: Path) -> None:
    parent = _parent_entry()
    results = paux.promote_closed_aux(
        paper_id="9999.99999",
        parent_theorem_name="parent_thm",
        parent_entry=parent,
        aux_records=[
            _aux(
                "aux_one",
                "theorem aux_one (n : Nat) : n + 0 = n := by sorry",
                "exact Nat.add_zero n",
            ),
        ],
        project_root=tmp_path,
        ledger_entries=[parent],
        validate_elaboration=None,
    )
    row = results[0]["row"]
    assert ":= by" not in row["lean_statement"]
    assert ":= sorry" not in row["lean_statement"]
    assert "theorem aux_one (n : Nat) : n + 0 = n" in row["lean_statement"]
    # The PROOF body is preserved in proof_text.
    assert row["proof_text"] == "exact Nat.add_zero n"


def test_aux_local_name_recorded_for_audit_lookup(tmp_path: Path) -> None:
    parent = _parent_entry()
    results = paux.promote_closed_aux(
        paper_id="9999.99999",
        parent_theorem_name="parent_thm",
        parent_entry=parent,
        aux_records=[
            _aux("aux_one", "theorem aux_one : Nat := by sorry", "exact 0"),
        ],
        project_root=tmp_path,
        ledger_entries=[parent],
        validate_elaboration=None,
    )
    row = results[0]["row"]
    # `aux_local_name` is the renamed aux name on disk; the audit
    # consults it to verify the body lives in `output/<paper>.lean`.
    assert row["aux_local_name"] == "aux_one"


# ---------------------------------------------------------------------------
# Lean file path
# ---------------------------------------------------------------------------


def test_lean_file_path_uses_project_root(tmp_path: Path) -> None:
    parent = _parent_entry()
    results = paux.promote_closed_aux(
        paper_id="9999.99999",
        parent_theorem_name="parent_thm",
        parent_entry=parent,
        aux_records=[
            _aux("aux_one", "theorem aux_one : Nat := by sorry", "exact 0"),
        ],
        project_root=tmp_path,
        ledger_entries=[parent],
        validate_elaboration=None,
    )
    row = results[0]["row"]
    expected = str((tmp_path / "output" / "9999.99999.lean"))
    assert row["lean_file"] == expected


# ---------------------------------------------------------------------------
# Empty / no-op
# ---------------------------------------------------------------------------


def test_empty_aux_records_returns_empty_list(tmp_path: Path) -> None:
    parent = _parent_entry()
    assert (
        paux.promote_closed_aux(
            paper_id="9999.99999",
            parent_theorem_name="parent_thm",
            parent_entry=parent,
            aux_records=[],
            project_root=tmp_path,
            ledger_entries=[parent],
            validate_elaboration=None,
        )
        == []
    )


def test_audit_trail_records_parent_and_protocol(tmp_path: Path) -> None:
    parent = _parent_entry()
    results = paux.promote_closed_aux(
        paper_id="9999.99999",
        parent_theorem_name="parent_thm",
        parent_entry=parent,
        aux_records=[
            _aux("aux_one", "theorem aux_one : Nat := by sorry", "exact 0"),
        ],
        project_root=tmp_path,
        ledger_entries=[parent],
        validate_elaboration=None,
    )
    trail = results[0]["row"]["audit_trail"]
    assert len(trail) == 1
    assert trail[0]["event"] == "derived_aux_from_factor"
    assert trail[0]["parent"] == "parent_thm"
    assert trail[0]["paper_id"] == "9999.99999"
    assert trail[0]["protocol"] == paux.PROMOTION_PROTOCOL
