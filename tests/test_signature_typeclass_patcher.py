"""Hermetic tests for signature_typeclass_patcher.

The patcher proposes Lean signature edits to insert `[Class Type]`
instance binders when the baseline lake error names a
`synthInstanceFailed: <Class> <Type>` whose `<Type>` is declared as a
free `Type*` binder in the row's signature. The whole-proof retry loop
cannot fix this class of failure because the fix lives in the signature,
not the body — this module fills that gap.

All tests are hermetic: no lake, no network, no Mistral.
"""
from __future__ import annotations

import signature_typeclass_patcher as patcher


# ---------------------------------------------------------------------------
# parse_synth_instance_failures
# ---------------------------------------------------------------------------


def test_parse_extracts_class_and_type_arg_from_multiline_form() -> None:
    tail = (
        "error: failed to synthesize instance of type class\n"
        "  MeasurableSpace alpha\n"
    )
    rows = patcher.parse_synth_instance_failures(tail)
    assert len(rows) == 1
    assert rows[0]["class_name"] == "MeasurableSpace"
    assert rows[0]["type_arg"] == "alpha"


def test_parse_extracts_synth_instance_failed_single_line() -> None:
    tail = "error: synthInstanceFailed: TopologicalSpace beta\n"
    rows = patcher.parse_synth_instance_failures(tail)
    assert len(rows) == 1
    assert rows[0]["class_name"] == "TopologicalSpace"
    assert rows[0]["type_arg"] == "beta"


def test_parse_returns_empty_when_no_synth_marker() -> None:
    tail = "error: unknown identifier 'foo'\nerror: tactic failed\n"
    assert patcher.parse_synth_instance_failures(tail) == []


def test_parse_dedups_repeated_class_type_pairs() -> None:
    tail = (
        "error: synthInstanceFailed: MeasurableSpace alpha\n"
        "error: synthInstanceFailed: MeasurableSpace alpha\n"
        "error: synthInstanceFailed: TopologicalSpace alpha\n"
    )
    rows = patcher.parse_synth_instance_failures(tail)
    assert len(rows) == 2
    assert {r["class_name"] for r in rows} == {"MeasurableSpace", "TopologicalSpace"}


def test_parse_empty_input_returns_empty() -> None:
    assert patcher.parse_synth_instance_failures("") == []
    assert patcher.parse_synth_instance_failures("   \n  ") == []


# ---------------------------------------------------------------------------
# patch_signature_with_instance
# ---------------------------------------------------------------------------


def test_patch_inserts_after_type_binder_group() -> None:
    sig = "theorem foo {alpha : Type*} (s : Set alpha) : True"
    patched = patcher.patch_signature_with_instance(sig, ["MeasurableSpace"], "alpha")
    assert patched is not None
    assert "[MeasurableSpace alpha]" in patched
    # The new binder must come AFTER the type-binder group.
    assert patched.index("[MeasurableSpace alpha]") > patched.index("{alpha : Type*}")
    # Body / target preserved.
    assert "(s : Set alpha) : True" in patched


def test_patch_returns_none_when_type_var_not_declared() -> None:
    sig = "theorem foo (n : Nat) : True"
    patched = patcher.patch_signature_with_instance(sig, ["MeasurableSpace"], "alpha")
    assert patched is None


def test_patch_returns_none_when_binder_already_present() -> None:
    sig = "theorem foo {alpha : Type*} [MeasurableSpace alpha] : True"
    patched = patcher.patch_signature_with_instance(sig, ["MeasurableSpace"], "alpha")
    assert patched is None


def test_patch_inserts_multiple_binders_at_once() -> None:
    sig = "theorem foo {alpha : Type*} : True"
    patched = patcher.patch_signature_with_instance(
        sig, ["MetricSpace", "CompleteSpace"], "alpha"
    )
    assert patched is not None
    assert "[MetricSpace alpha]" in patched
    assert "[CompleteSpace alpha]" in patched


# ---------------------------------------------------------------------------
# propose_typeclass_additions — high-level surface
# ---------------------------------------------------------------------------


def test_propose_measurablespace_alpha_baseline() -> None:
    proposals = patcher.propose_typeclass_additions(
        paper_id="test",
        theorem_name="t",
        lean_statement="theorem t {alpha : Type*} (s : Set alpha) : True := by sorry",
        baseline_error=(
            "error: failed to synthesize instance of type class\n"
            "  MeasurableSpace alpha\n"
        ),
    )
    assert proposals, "should propose at least one patched signature"
    # First (most-targeted) proposal contains the bare class binder.
    assert "[MeasurableSpace alpha]" in proposals[0]
    # The patched signature still elaborates the original target / body.
    assert "theorem t" in proposals[0]


def test_propose_combined_patch_when_multiple_classes_target_same_var() -> None:
    proposals = patcher.propose_typeclass_additions(
        paper_id="test",
        theorem_name="t",
        lean_statement="theorem t {alpha : Type*} (s : Set alpha) : True := by sorry",
        baseline_error=(
            "error: synthInstanceFailed: MeasurableSpace alpha\n"
            "error: synthInstanceFailed: TopologicalSpace alpha\n"
        ),
    )
    assert proposals
    # The combined (last) proposal binds both classes.
    combined = proposals[-1]
    assert "[MeasurableSpace alpha]" in combined
    assert "[TopologicalSpace alpha]" in combined


def test_propose_returns_empty_when_no_synth_marker() -> None:
    proposals = patcher.propose_typeclass_additions(
        paper_id="test",
        theorem_name="t",
        lean_statement="theorem t {alpha : Type*} : True := by sorry",
        baseline_error="error: tactic 'omega' failed\n",
    )
    assert proposals == []


def test_propose_returns_empty_when_type_var_not_in_signature() -> None:
    """If the synthInstanceFailed names `gamma alpha` but the signature has
    no free `Type*` binder at all, the patcher must NOT fabricate one.
    """
    proposals = patcher.propose_typeclass_additions(
        paper_id="test",
        theorem_name="t",
        lean_statement="theorem t (n : Nat) : n = n := by rfl",
        baseline_error="error: synthInstanceFailed: MeasurableSpace alpha\n",
    )
    assert proposals == []


def test_propose_with_validator_drops_failing_patches() -> None:
    """When a validator is supplied, only the patches it accepts come back.
    Here we accept exactly the bare-class proposal and reject the
    combined patch — both should be filtered through the validator.
    """
    seen: list[str] = []

    def validator(sig: str) -> tuple[bool, str]:
        seen.append(sig)
        # Accept only proposals that name `MeasurableSpace`, reject the
        # ones that also bring in `TopologicalSpace`.
        if "TopologicalSpace" in sig:
            return False, "fake error"
        return True, ""

    proposals = patcher.propose_typeclass_additions(
        paper_id="test",
        theorem_name="t",
        lean_statement="theorem t {alpha : Type*} (s : Set alpha) : True := by sorry",
        baseline_error=(
            "error: synthInstanceFailed: MeasurableSpace alpha\n"
            "error: synthInstanceFailed: TopologicalSpace alpha\n"
        ),
        validate=validator,
    )
    # The validator was offered every patch, but only Measurable-bearing
    # ones survived.
    assert seen, "validator must be invoked at least once"
    assert proposals
    for p in proposals:
        assert "[MeasurableSpace alpha]" in p
        assert "TopologicalSpace" not in p


def test_propose_with_validator_all_failing_returns_empty() -> None:
    """If the validator rejects every proposal, the patcher returns []."""

    def reject_all(_sig: str) -> tuple[bool, str]:
        return False, "always fails"

    proposals = patcher.propose_typeclass_additions(
        paper_id="test",
        theorem_name="t",
        lean_statement="theorem t {alpha : Type*} (s : Set alpha) : True := by sorry",
        baseline_error="error: synthInstanceFailed: MeasurableSpace alpha\n",
        validate=reject_all,
    )
    assert proposals == []


def test_propose_falls_back_to_first_type_var_when_arg_missing() -> None:
    """When the error tail names the class but not a parseable type
    argument, the patcher falls back to the first declared free
    type-variable — matching the existing B2 anchor's behaviour.
    """
    proposals = patcher.propose_typeclass_additions(
        paper_id="test",
        theorem_name="t",
        lean_statement="theorem t {alpha : Type*} : True := by sorry",
        baseline_error="error: synthInstanceFailed: MeasurableSpace\n",
    )
    assert proposals
    assert "[MeasurableSpace alpha]" in proposals[0]


def test_propose_handles_alias_expansion_for_metricspace() -> None:
    """`MetricSpace` is commonly paired with `CompleteSpace`. When both
    classes are mentioned in the error tail, the patcher proposes the
    combined binder set as a fallback candidate (less-targeted, lower
    priority than the bare `MetricSpace` proposal).
    """
    proposals = patcher.propose_typeclass_additions(
        paper_id="test",
        theorem_name="t",
        lean_statement="theorem t {alpha : Type*} : True := by sorry",
        baseline_error=(
            "error: synthInstanceFailed: MetricSpace alpha\n"
            "error: synthInstanceFailed: CompleteSpace alpha\n"
        ),
    )
    assert proposals
    # First proposal: bare MetricSpace (most-targeted).
    assert "[MetricSpace alpha]" in proposals[0]
    # Some later proposal should bring in CompleteSpace too.
    any_with_complete = any("[CompleteSpace alpha]" in p for p in proposals)
    assert any_with_complete


# ---------------------------------------------------------------------------
# build_isolated_validator — smoke (no-op when lake unavailable)
# ---------------------------------------------------------------------------


def test_build_isolated_validator_returns_callable(tmp_path) -> None:
    """The factory should always return a callable, even when
    `_run_isolated_file_check` cannot be imported (returns stub).
    """
    src = tmp_path / "fake.lean"
    src.write_text("import Mathlib\n", encoding="utf-8")
    v = patcher.build_isolated_validator(
        project_root=tmp_path,
        source_file=src,
        timeout_s=30,
    )
    assert callable(v)
