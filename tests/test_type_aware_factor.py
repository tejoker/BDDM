"""Hermetic tests for `scripts/type_aware_factor.py`.

Pin: destructure(parent) returns aux specs whose composition is
type-correct by construction.
"""
from __future__ import annotations

from type_aware_factor import (
    AuxSpec,
    compose_template,
    destructure,
    destructure_conjunction,
    destructure_iff,
    _split_parent,
    _split_top_level,
)


# ---------------------------------------------------------------------------
# _split_top_level
# ---------------------------------------------------------------------------


def test_split_top_level_conjunction_n_ary() -> None:
    parts = _split_top_level("A ∧ B ∧ C", "∧")
    assert parts == ["A", "B", "C"]


def test_split_top_level_respects_parens() -> None:
    # ∧ inside parens must NOT split.
    parts = _split_top_level("(A ∧ B) ∧ C", "∧")
    assert parts == ["(A ∧ B)", "C"]


def test_split_top_level_respects_anonymous_constructor() -> None:
    parts = _split_top_level("⟨h1, h2⟩ ∧ x = 0", "∧")
    assert parts == ["⟨h1, h2⟩", "x = 0"]


def test_split_top_level_iff_two_parts() -> None:
    parts = _split_top_level("A ↔ B", "↔")
    assert parts == ["A", "B"]


# ---------------------------------------------------------------------------
# _split_parent
# ---------------------------------------------------------------------------


def test_split_parent_simple() -> None:
    result = _split_parent("theorem foo : 0 = 0 := by sorry")
    assert result is not None
    name, binders, target = result
    assert name == "foo"
    assert binders == ""
    assert target == "0 = 0"


def test_split_parent_with_binders() -> None:
    result = _split_parent(
        "theorem foo (n : ℕ) (h : 0 < n) : 0 < 2 * n := by sorry"
    )
    assert result is not None
    name, binders, target = result
    assert name == "foo"
    assert "(n : ℕ)" in binders
    assert "(h : 0 < n)" in binders
    assert target == "0 < 2 * n"


def test_split_parent_handles_namespace_qualified_name() -> None:
    result = _split_parent("theorem ArxivPaper.Foo.thm : True := by sorry")
    assert result is not None
    name, _, target = result
    assert name == "ArxivPaper.Foo.thm"
    assert target == "True"


def test_split_parent_returns_none_on_garbage() -> None:
    assert _split_parent("not a theorem") is None


# ---------------------------------------------------------------------------
# destructure_conjunction
# ---------------------------------------------------------------------------


def test_destructure_two_conjuncts() -> None:
    specs = destructure_conjunction(
        parent_name="foo", binders="(n : ℕ)", target="0 < n ∧ n < 10",
    )
    assert len(specs) == 2
    assert specs[0].shape == "conjunct"
    assert specs[0].target == "0 < n"
    assert specs[1].target == "n < 10"
    assert "(n : ℕ)" in specs[0].signature


def test_destructure_n_ary_conjunction() -> None:
    specs = destructure_conjunction(
        parent_name="foo", binders="", target="A ∧ B ∧ C ∧ D",
    )
    assert len(specs) == 4
    targets = [s.target for s in specs]
    assert targets == ["A", "B", "C", "D"]


def test_destructure_no_top_level_conjunction() -> None:
    specs = destructure_conjunction(
        parent_name="foo", binders="", target="(A ∧ B)",
    )
    # Wrapped in parens at top level — no top-level split.
    assert specs == []


def test_destructure_single_conjunct_returns_empty() -> None:
    specs = destructure_conjunction(
        parent_name="foo", binders="", target="A",
    )
    assert specs == []


# ---------------------------------------------------------------------------
# destructure_iff
# ---------------------------------------------------------------------------


def test_destructure_iff_two_directions() -> None:
    specs = destructure_iff(
        parent_name="foo", binders="(x : ℕ)", target="0 < x ↔ x ≠ 0",
    )
    assert len(specs) == 2
    shapes = [s.shape for s in specs]
    assert "iff_fwd" in shapes
    assert "iff_bwd" in shapes
    fwd = next(s for s in specs if s.shape == "iff_fwd")
    bwd = next(s for s in specs if s.shape == "iff_bwd")
    assert "0 < x → x ≠ 0" in fwd.target
    assert "x ≠ 0 → 0 < x" in bwd.target


# ---------------------------------------------------------------------------
# destructure (top-level entrypoint)
# ---------------------------------------------------------------------------


def test_destructure_full_decl_conjunction() -> None:
    specs = destructure(
        "theorem foo (n : ℕ) (h : 0 < n) : n > 0 ∧ n + 1 > 1 := by sorry"
    )
    assert len(specs) == 2
    assert all(s.shape == "conjunct" for s in specs)
    assert specs[0].target == "n > 0"
    assert specs[1].target == "n + 1 > 1"
    # Each aux carries the full binder block.
    assert "(n : ℕ)" in specs[0].signature
    assert "(h : 0 < n)" in specs[0].signature


def test_destructure_full_decl_iff() -> None:
    specs = destructure(
        "theorem foo (x : ℕ) : 0 < x ↔ x ≠ 0 := by sorry"
    )
    assert len(specs) == 2
    assert {s.shape for s in specs} == {"iff_fwd", "iff_bwd"}


def test_destructure_trivial_target_returns_empty() -> None:
    assert destructure("theorem foo : True := by sorry") == []
    assert destructure("theorem foo : False := by sorry") == []
    # Single identifier as target — no destructure.
    assert destructure("theorem foo : Foo := by sorry") == []


def test_destructure_unsupported_shape_returns_empty() -> None:
    # `∃` is not yet handled by destructure (the witness/property split
    # is harder than ∧ / ↔). For now we return [] and the caller falls
    # back to LLM-based factoring.
    specs = destructure("theorem foo : ∃ x, P x := by sorry")
    assert specs == []


def test_destructure_refuses_outer_existential_with_conjunction_body() -> None:
    """Round-XXII bug: splitting `∃ x, A x ∧ B x` on top-level ∧ is
    UNSOUND because both conjuncts share the witness x. The destructure
    must refuse this shape and let the caller fall back to LLM factoring."""
    specs = destructure(
        "theorem foo (h : 0 < n) : ∃ C : ℝ, 0 < C ∧ C ≤ n := by sorry"
    )
    assert specs == []


def test_destructure_refuses_outer_universal_with_conjunction_body() -> None:
    specs = destructure(
        "theorem foo : ∀ x : ℝ, x > 0 ∧ x + 1 > 1 := by sorry"
    )
    assert specs == []


def test_destructure_refuses_outer_existential_with_iff_body() -> None:
    """Even ↔ destructure is refused under outer ∃ — the shared witness
    constraint applies symmetrically."""
    specs = destructure(
        "theorem foo : ∃ X : Prop, X ↔ X := by sorry"
    )
    assert specs == []


def test_destructure_still_works_for_top_level_conjunction_without_outer_binder() -> None:
    # Sanity: removing the outer ∃ should re-enable destructure.
    specs = destructure(
        "theorem foo (n : ℕ) (h : 0 < n) : n > 0 ∧ n + 1 > 1 := by sorry"
    )
    assert len(specs) == 2


# ---------------------------------------------------------------------------
# compose_template
# ---------------------------------------------------------------------------


def test_compose_template_conjunction() -> None:
    specs = destructure(
        "theorem foo : A ∧ B ∧ C := by sorry"
    )
    body = compose_template(specs)
    assert body.startswith("exact ⟨")
    assert "foo_conjunct_1__type_aware_aux" in body
    assert "foo_conjunct_2__type_aware_aux" in body
    assert "foo_conjunct_3__type_aware_aux" in body


def test_compose_template_iff() -> None:
    specs = destructure("theorem foo : A ↔ B := by sorry")
    body = compose_template(specs)
    assert body.startswith("exact ⟨")
    assert "iff_fwd" in body
    assert "iff_bwd" in body


def test_compose_template_empty_input() -> None:
    assert compose_template([]) == ""
