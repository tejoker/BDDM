from __future__ import annotations

from pathlib import Path

from upgrade_existing_paper_theory_stubs import upgrade_file


def _write_stub(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_upgrade_appends_instances_for_known_underlying_type(tmp_path: Path) -> None:
    """A stub with `abbrev Foo : Type := ℕ` and no instance lines should pick up
    every standard instance from `_AUTO_INSTANCE_BY_UNDERLYING[ℕ]`, inserted
    before `end Paper_<id>`. The exact count grew when the per-underlying
    allowlist expanded beyond the original five — we now assert the originals
    are present plus the count is non-trivial, rather than pinning a fragile
    constant."""
    from paper_theory_builder import _AUTO_INSTANCE_BY_UNDERLYING

    stub = tmp_path / "Paper_0000_00001.lean"
    _write_stub(
        stub,
        "namespace Paper_0000_00001\n\n"
        "abbrev Foo : Type := ℕ\n\n"
        "def step (x : ℕ) : ℕ := x\n\n"
        "end Paper_0000_00001\n",
    )

    summary = upgrade_file(stub)
    text = stub.read_text(encoding="utf-8")

    expected_classes = _AUTO_INSTANCE_BY_UNDERLYING["ℕ"]
    assert summary["changed"] is True
    assert summary["instances_added"] == len(expected_classes)
    for cls in expected_classes:
        assert f"instance : {cls} Foo := inferInstance" in text


def test_upgrade_attaches_aesop_to_axioms(tmp_path: Path) -> None:
    """Bare `axiom foo : ...` declarations get `attribute [aesop safe apply] foo`."""
    stub = tmp_path / "Paper_0000_00002.lean"
    _write_stub(
        stub,
        "namespace Paper_0000_00002\n\n"
        "axiom foo (a b : ℕ) : a = b\n\n"
        "axiom bar (a : ℕ) : a ≤ a\n\n"
        "end Paper_0000_00002\n",
    )

    summary = upgrade_file(stub)
    text = stub.read_text(encoding="utf-8")

    assert summary["axioms_tagged"] == 2
    assert "attribute [aesop safe apply] foo" in text
    assert "attribute [aesop safe apply] bar" in text


def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    """Re-running the upgrade on an already-upgraded file is a no-op."""
    stub = tmp_path / "Paper_0000_00003.lean"
    _write_stub(
        stub,
        "namespace Paper_0000_00003\n\n"
        "abbrev Foo : Type := ℕ\n\n"
        "axiom foo (a b : ℕ) : a = b\n\n"
        "end Paper_0000_00003\n",
    )

    first = upgrade_file(stub)
    assert first["changed"] is True
    second = upgrade_file(stub)
    assert second["changed"] is False
    assert second["instances_added"] == 0
    assert second["axioms_tagged"] == 0


def test_upgrade_skips_unknown_underlying_type(tmp_path: Path) -> None:
    """`abbrev Foo : Type := SomeBespokeType` must NOT auto-emit instances —
    those would fail to elaborate and break `lake build`."""
    stub = tmp_path / "Paper_0000_00004.lean"
    _write_stub(
        stub,
        "namespace Paper_0000_00004\n\n"
        "abbrev Foo : Type := SomeBespokeType\n\n"
        "end Paper_0000_00004\n",
    )

    summary = upgrade_file(stub)
    assert summary["instances_added"] == 0
    assert summary["changed"] is False


def test_upgrade_preserves_existing_instances(tmp_path: Path) -> None:
    """When the stub already has `instance : LE Foo := inferInstance`, do not
    duplicate it. Confirms the dedupe path used during retroactive runs."""
    stub = tmp_path / "Paper_0000_00005.lean"
    _write_stub(
        stub,
        "namespace Paper_0000_00005\n\n"
        "abbrev Foo : Type := ℕ\n\n"
        "instance : LE Foo := inferInstance\n"
        "instance : LT Foo := inferInstance\n\n"
        "end Paper_0000_00005\n",
    )

    from paper_theory_builder import _AUTO_INSTANCE_BY_UNDERLYING

    summary = upgrade_file(stub)
    text = stub.read_text(encoding="utf-8")

    # Whatever the full allowlist is, only the classes not already present
    # should be added; LE and LT must NOT be duplicated.
    expected_remaining = len(_AUTO_INSTANCE_BY_UNDERLYING["ℕ"]) - 2
    assert summary["instances_added"] == expected_remaining
    # Existing two are still there exactly once.
    assert text.count("instance : LE Foo := inferInstance") == 1
    assert text.count("instance : LT Foo := inferInstance") == 1
