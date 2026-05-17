"""Mutation tests for `audit_fully_proven_integrity`.

For each historically-observed bypass class, inject a synthetic bypass (a
ledger row + `.lean` file pair) and assert the audit demotes the row. This
is mutation-style differential testing: we don't trust that the audit
catches a *general* bypass simply because it caught the *specific* one we
saw in production. Each test pins a class.

Bypass classes covered (per F2 spec):

1. **Body-is-sorry**: ledger says `proof_text='aesop',
   validation_gates.lean_proof_closed=True`; file has `:= by sorry`.
2. **apply?-only**: same but `proof_text='apply?'` (auto-LLM bypass).
3. **Trivialized statements** (Round-VII patterns; the translator's
   `_is_trivialized_signature` knows the shapes):
   a. `∃ x : Nat, x = 5`           — existential of an equality.
   b. `∃ X : Prop, X ↔ True`       — existential Prop binder.
   c. `f X = f X ∧ g Y = g Y`      — reflexive conjunction.
   d. `∀ (P Q : Prop), P ∧ Q`      — Prop-binder placeholder.
4. **Namespace-qualified bypass**: ledger says `ArxivPaper.X` with
   `apply?` proof_text over a sorry file. (Commit 7b80c4a fix.)
5. **Multi-line sorry bypass**: file body is a multi-line proof that
   contains a hidden `sorry` somewhere mid-body — must demote when the
   first meaningful tactic is `sorry`, AND must NOT demote when `sorry`
   is buried after legitimate tactics (Lean still rejects that body, but
   the audit's contract is: catch *first-tactic-sorry* placeholders;
   downstream lake-build catches the rest).

All tests are hermetic (use `tmp_path` or in-memory entries).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from audit_fully_proven_integrity import (
    _PROOF_CLAIMING_STATUSES,
    audit_ledger_entries,
    audit_ledger_file,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fp_entry(
    name: str,
    *,
    proof_text: str = "aesop",
    status: str = "FULLY_PROVEN",
    lean_statement: str = "",
) -> dict[str, Any]:
    """Construct a single proof-claiming ledger row that we expect the audit
    to demote in each mutation scenario."""
    return {
        "theorem_name": name,
        "status": status,
        "proved": True,
        "step_verdict": "VERIFIED",
        "proof_text": proof_text,
        "proof_method": "lean_verified",
        "lean_statement": lean_statement,
        "validation_gates": {
            "lean_proof_closed": True,
            "step_verdict_verified": True,
        },
        "gate_failures": [],
    }


def _assert_demoted(entries: list[dict[str, Any]], *, paper_id: str, lean_src: str) -> None:
    """Run the audit with the broader-status mode (so all of FP/AB/IP are
    audited) and assert exactly one row was demoted to UNRESOLVED."""
    result = audit_ledger_entries(
        entries, paper_id=paper_id, lean_src=lean_src,
        statuses=_PROOF_CLAIMING_STATUSES,
    )
    assert result.demoted == 1, (
        f"audit failed to demote bypass row: "
        f"demoted={result.demoted} fp_pre={result.fp_pre} "
        f"validated_clean={result.validated_clean} "
        f"term_mode_skipped={result.term_mode_skipped} "
        f"not_found_skipped={result.not_found_skipped}"
    )
    assert entries[0]["status"] == "UNRESOLVED"
    assert entries[0]["validation_gates"]["lean_proof_closed"] is False


# ---------------------------------------------------------------------------
# Bypass class 1: body-is-sorry with proof_text='aesop'
# ---------------------------------------------------------------------------


def test_mutation_body_sorry_aesop_proof_text() -> None:
    """The original FP-bypass pattern: ledger claims `aesop` closed the
    proof; the file has `:= by sorry`. The audit MUST demote."""
    lean_src = "theorem foo : True := by\n  sorry\n"
    entries = [_fp_entry("foo", proof_text="aesop")]
    _assert_demoted(entries, paper_id="bypass.aesop", lean_src=lean_src)
    # Forensic capture must preserve the spurious proof_text.
    assert entries[0]["audit_demotion"]["captured_proof_text"] == "aesop"


# ---------------------------------------------------------------------------
# Bypass class 2: apply?-only bypass
# ---------------------------------------------------------------------------


def test_mutation_body_sorry_applyq_proof_text() -> None:
    """The `apply?` bypass: an LLM-suggested `apply?` was written to
    proof_text without ever being run against Lean. File still has
    `:= by sorry`. The audit MUST demote."""
    lean_src = "theorem foo : True := by\n  sorry\n"
    entries = [_fp_entry("foo", proof_text="apply?")]
    _assert_demoted(entries, paper_id="bypass.applyq", lean_src=lean_src)
    assert entries[0]["audit_demotion"]["captured_proof_text"] == "apply?"


# ---------------------------------------------------------------------------
# Bypass class 3: trivialized statements (Round-VII patterns)
# ---------------------------------------------------------------------------


def test_mutation_trivialized_existential_of_equality() -> None:
    """`∃ x : Nat, x = 5` is a vacuous claim (closed by `⟨5, rfl⟩`).
    Even with a real `by exact ⟨5, rfl⟩` proof, the audit MUST flag it
    as trivialized and demote."""
    stmt = "∃ x : Nat, x = 5"
    lean_src = "theorem trivex : ∃ x : Nat, x = 5 := by\n  exact ⟨5, rfl⟩\n"
    entries = [_fp_entry("trivex", proof_text="exact ⟨5, rfl⟩", lean_statement=stmt)]
    _assert_demoted(entries, paper_id="bypass.triv_eq", lean_src=lean_src)
    assert entries[0]["audit_demotion"]["reason"] == "trivialized_statement"


def test_mutation_trivialized_existential_prop_iff() -> None:
    """`∃ X : Prop, X ↔ True` is a placeholder shape — there is no
    mathematical content. The audit MUST demote."""
    stmt = "∃ X : Prop, X ↔ True"
    lean_src = (
        "theorem trivpropiff : ∃ X : Prop, X ↔ True := by\n"
        "  exact ⟨True, Iff.rfl⟩\n"
    )
    entries = [_fp_entry("trivpropiff", proof_text="exact ⟨True, Iff.rfl⟩", lean_statement=stmt)]
    _assert_demoted(entries, paper_id="bypass.triv_prop_iff", lean_src=lean_src)
    assert entries[0]["audit_demotion"]["reason"] == "trivialized_statement"


def test_mutation_trivialized_reflexive_conjunction() -> None:
    """`f X = f X ∧ g Y = g Y` is closed by `⟨rfl, rfl⟩`; the statement is
    a placeholder. The audit MUST demote even with the technically-valid
    closure."""
    stmt = "f X = f X ∧ g Y = g Y"
    lean_src = (
        "theorem trivreflconj (f g : Nat → Nat) (X Y : Nat) : "
        "f X = f X ∧ g Y = g Y := by\n  exact ⟨rfl, rfl⟩\n"
    )
    entries = [_fp_entry("trivreflconj", proof_text="exact ⟨rfl, rfl⟩", lean_statement=stmt)]
    _assert_demoted(entries, paper_id="bypass.triv_reflconj", lean_src=lean_src)
    assert entries[0]["audit_demotion"]["reason"] == "trivialized_statement"


def test_mutation_trivialized_prop_binder_pair() -> None:
    """`∀ (P Q : Prop), P ∧ Q` is the Prop-binder placeholder — there is no
    closed proof (`P` and `Q` are uninstantiated, so we genuinely need
    `sorry` somewhere). The audit MUST demote regardless of which gate
    fires first (body-sorry or trivialized-statement); both correctly
    flag this row."""
    stmt = "∀ (P Q : Prop), P ∧ Q"
    lean_src = (
        "theorem trivpropbinder : ∀ (P Q : Prop), P ∧ Q := by\n"
        "  intro P Q; exact ⟨sorry, sorry⟩\n"
    )
    entries = [_fp_entry("trivpropbinder", proof_text="intros; aesop", lean_statement=stmt)]
    _assert_demoted(entries, paper_id="bypass.triv_propbinder", lean_src=lean_src)
    # Either the body-sorry path or the trivialized-statement path
    # demoted the row; both are correct verdicts for this bypass.
    assert entries[0]["audit_demotion"]["reason"] in (
        "trivialized_statement",
        "file_body_is_sorry_but_ledger_claimed_closed",
    )


# ---------------------------------------------------------------------------
# Bypass class 4: namespace-qualified ledger names
# ---------------------------------------------------------------------------


def test_mutation_namespace_qualified_apply_question() -> None:
    """Commit 7b80c4a fixed this: a ledger row named `ArxivPaper.lem_X` with
    `proof_text='apply?'` and `lean_proof_closed=True`, pointing at a
    sorry-bodied namespaced theorem, must demote. (Before the fix the
    audit silently skipped namespaced rows.)"""
    lean_src = (
        "namespace ArxivPaper\n"
        "theorem lem_Hilbert_hypercontractivity (n : ℕ) : n = n := by\n"
        "  sorry\n"
        "end ArxivPaper\n"
    )
    entries = [
        _fp_entry(
            "ArxivPaper.lem_Hilbert_hypercontractivity",
            proof_text="apply?",
            status="INTERMEDIARY_PROVEN",
        )
    ]
    _assert_demoted(entries, paper_id="bypass.namespaced", lean_src=lean_src)


# ---------------------------------------------------------------------------
# Bypass class 5: multi-line sorry placement
# ---------------------------------------------------------------------------


def test_mutation_multiline_proof_with_sorry_first() -> None:
    """A multi-line tactic block whose *first meaningful tactic* is `sorry`
    (after comment / blank lines) must demote. This pins the parser:
    `_body_is_sorry` skips comments + blanks then checks the first real
    token."""
    lean_src = (
        "theorem foo : True := by\n"
        "  -- TODO: prove this once we have helper X\n"
        "  -- For now we placeholder.\n"
        "\n"
        "  sorry\n"
        "  -- continuation never reached\n"
    )
    entries = [_fp_entry("foo", proof_text="aesop")]
    _assert_demoted(entries, paper_id="bypass.multiline", lean_src=lean_src)


def test_mutation_multiline_proof_with_hidden_midbody_sorry() -> None:
    """A hidden `sorry` *buried mid-body* (after legitimate tactics) is the
    sneakiest bypass: Lean's `sorry` closes any remaining obligation, so
    `lake build` succeeds with only a warning. The audit MUST still
    demote because the proof has no mathematical content past the
    `sorry`."""
    lean_src = (
        "theorem foo : True := by\n"
        "  have h : True := trivial\n"
        "  -- TODO replace with real argument\n"
        "  sorry\n"
        "  exact h\n"
    )
    entries = [_fp_entry("foo", proof_text="exact h")]
    _assert_demoted(entries, paper_id="bypass.midbody_sorry", lean_src=lean_src)


def test_mutation_sorry_in_comment_is_ignored() -> None:
    """Negative case: a *comment* containing the word `sorry` MUST NOT
    trigger demotion. Otherwise legitimate proofs with `-- TODO sorry`
    leftovers would be wrongly demoted."""
    lean_src = (
        "theorem foo : 1 + 1 = 2 := by\n"
        "  -- previous version used sorry here; replaced with decide\n"
        "  decide\n"
    )
    entries = [_fp_entry("foo", proof_text="decide")]
    result = audit_ledger_entries(
        entries, paper_id="clean.comment_mentions_sorry", lean_src=lean_src,
        statuses=_PROOF_CLAIMING_STATUSES,
    )
    assert result.demoted == 0, f"audit wrongly demoted a comment-only sorry mention: {result}"
    assert result.validated_clean == 1


def test_mutation_sorry_inline_combinator_after_tactic() -> None:
    """`<;> sorry` and `; sorry` are common bypass combinators (run a
    tactic, then close any leftover goals with `sorry`). The audit MUST
    catch these — the standalone `sorry` token appears after whitespace
    or punctuation."""
    lean_src = (
        "theorem foo : True := by\n"
        "  trivial <;> sorry\n"
    )
    entries = [_fp_entry("foo", proof_text="trivial")]
    _assert_demoted(entries, paper_id="bypass.combinator_sorry", lean_src=lean_src)


def test_mutation_multiline_real_proof_is_preserved() -> None:
    """Counter-example: a multi-line *real* proof must NOT be demoted.
    This pins the audit's negative case — false positives would shred
    legitimate work."""
    lean_src = (
        "theorem foo : 1 + 1 = 2 := by\n"
        "  have h : (1 : Nat) + 1 = 2 := by decide\n"
        "  exact h\n"
    )
    entries = [_fp_entry("foo", proof_text="decide")]
    result = audit_ledger_entries(
        entries, paper_id="clean.multiline", lean_src=lean_src,
        statuses=_PROOF_CLAIMING_STATUSES,
    )
    assert result.demoted == 0, f"audit wrongly demoted a real multi-line proof: {result}"
    assert result.validated_clean == 1
    assert entries[0]["status"] == "FULLY_PROVEN"


# ---------------------------------------------------------------------------
# File-level mutation tests (write-back + persistence)
# ---------------------------------------------------------------------------


def test_mutation_file_level_bypass_persists_demotion(tmp_path: Path) -> None:
    """End-to-end: write a real ledger file + .lean file pair on disk,
    run `audit_ledger_file(..., write=True)`, then re-read the ledger and
    confirm the persisted JSON reflects the demotion."""
    lean_path = tmp_path / "p.lean"
    lean_path.write_text("theorem foo : True := by\n  sorry\n", encoding="utf-8")
    ledger_path = tmp_path / "p.json"
    ledger_path.write_text(
        json.dumps({"entries": [_fp_entry("foo", proof_text="apply?")]}, indent=2),
        encoding="utf-8",
    )
    result = audit_ledger_file(
        ledger_path, lean_path, paper_id="p", write=True,
        statuses=_PROOF_CLAIMING_STATUSES,
    )
    assert result.demoted == 1
    after = json.loads(ledger_path.read_text(encoding="utf-8"))
    e = after["entries"][0]
    assert e["status"] == "UNRESOLVED"
    assert e["validation_gates"]["lean_proof_closed"] is False
    assert e["audit_demotion"]["captured_proof_text"] == "apply?"
    assert "lean_proof_closed" in e["gate_failures"]


def test_mutation_axiom_backed_status_is_also_audited(tmp_path: Path) -> None:
    """The same circular bypass that inflated FP can inflate AB. With
    `statuses=_PROOF_CLAIMING_STATUSES`, an AB row whose .lean body is
    `sorry` must be demoted."""
    lean_src = "theorem ab_bypass : True := by\n  sorry\n"
    entries = [_fp_entry("ab_bypass", proof_text="exact axiom_X", status="AXIOM_BACKED")]
    _assert_demoted(entries, paper_id="bypass.ab", lean_src=lean_src)


def test_mutation_unresolved_rows_not_touched() -> None:
    """Negative test: UNRESOLVED rows must NEVER be demoted even in
    broader-status mode — UR rows make no proof-closure claim."""
    lean_src = "theorem ur : True := by\n  sorry\n"
    entries = [{
        "theorem_name": "ur",
        "status": "UNRESOLVED",
        "validation_gates": {"lean_proof_closed": False},
    }]
    result = audit_ledger_entries(
        entries, paper_id="ur.clean", lean_src=lean_src,
        statuses=_PROOF_CLAIMING_STATUSES,
    )
    assert result.demoted == 0
    assert entries[0]["status"] == "UNRESOLVED"


@pytest.mark.parametrize(
    "trivialized_stmt",
    [
        "∃ x : Nat, x = 5",
        "∃ X : Prop, X ↔ True",
        "f X = f X ∧ g Y = g Y",
        "∀ (P Q : Prop), P ∧ Q",
        # Round-XXII bypass class: tautological implication body → body.
        # The bare-identifier form was already caught by a prior regex;
        # these are the GENERALIZED form (any LHS == RHS), which the
        # commit alongside this test added.
        "theorem t : (∃ x : ℝ, x = x) → (∃ x : ℝ, x = x)",
        "theorem t : (P ∧ Q) → (P ∧ Q)",
        "theorem t : (a + b = c) → (a + b = c)",
        # Round-XXII bypass class: existential-of-trivial-inequality.
        # Emitted by the destructure soundness bug; closes trivially
        # with ⟨1, by norm_num⟩.
        "∃ C : ℝ, 0 < C",
        "∃ x : ℕ, 0 ≤ x",
        "∃ y : ℝ, y > 0",
        "∃ z : Real, z ≥ 0",
    ],
)
def test_mutation_trivialized_signature_detection_parametrized(trivialized_stmt: str) -> None:
    """Cross-check the translator's `_is_trivialized_signature` directly.
    If this fails, the audit's trivialized-statement gate has no chance —
    the upstream detector is broken."""
    from translator._translate import _is_trivialized_signature

    assert _is_trivialized_signature(trivialized_stmt) is True, (
        f"translator's trivialized-signature detector missed: {trivialized_stmt!r}"
    )
