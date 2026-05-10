from __future__ import annotations

from pathlib import Path

from repair_paper_theory_exports import repair_file


def _write_stub(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_repair_drops_undefined_export_names(tmp_path: Path) -> None:
    """A stub that defines `ξ'` but exports bare `ξ` must be repaired so that
    the export list keeps only names that actually appear as declarations."""
    stub = tmp_path / "Paper_0000_00001.lean"
    _write_stub(
        stub,
        "namespace Paper_0000_00001\n\n"
        "def ξ' : ℝ := 0\n"
        "def Γ_gamma : ℝ := 0\n"
        "axiom foo (a b : ℕ) : a = b\n\n"
        "end Paper_0000_00001\n\n"
        "export Paper_0000_00001 (ξ' Γ_gamma foo ξ Γ unrelated)\n",
    )

    summary = repair_file(stub)
    text = stub.read_text(encoding="utf-8")

    assert summary["changed"] is True
    assert summary["removed"] == 3  # ξ, Γ, unrelated
    export_line = next(l for l in text.splitlines() if l.startswith("export "))
    names = export_line.split("(", 1)[1].rstrip(")").split()
    assert names == ["ξ'", "Γ_gamma", "foo"]


def test_repair_is_idempotent(tmp_path: Path) -> None:
    stub = tmp_path / "Paper_0000_00002.lean"
    _write_stub(
        stub,
        "namespace Paper_0000_00002\n\n"
        "def ok' : ℝ := 0\n\n"
        "end Paper_0000_00002\n\n"
        "export Paper_0000_00002 (ok' nope)\n",
    )

    first = repair_file(stub)
    assert first["changed"] is True
    second = repair_file(stub)
    assert second["changed"] is False
    assert second["removed"] == 0


def test_repair_no_op_when_all_exports_defined(tmp_path: Path) -> None:
    stub = tmp_path / "Paper_0000_00003.lean"
    body = (
        "namespace Paper_0000_00003\n\n"
        "def a : ℝ := 0\n"
        "axiom b : True\n\n"
        "end Paper_0000_00003\n\n"
        "export Paper_0000_00003 (a b)\n"
    )
    _write_stub(stub, body)

    summary = repair_file(stub)
    assert summary["changed"] is False
    assert stub.read_text() == body


def test_repair_preserves_namespace_and_def_block(tmp_path: Path) -> None:
    """Only the export line is mutated; declarations and namespace markers stay intact."""
    stub = tmp_path / "Paper_0000_00004.lean"
    body = (
        "import Mathlib\n\n"
        "namespace Paper_0000_00004\n\n"
        "def keep' : ℝ := 0\n\n"
        "end Paper_0000_00004\n\n"
        "export Paper_0000_00004 (keep' drop_me)\n"
    )
    _write_stub(stub, body)

    repair_file(stub)
    text = stub.read_text(encoding="utf-8")
    assert "import Mathlib" in text
    assert "namespace Paper_0000_00004" in text
    assert "def keep' : ℝ := 0" in text
    assert "end Paper_0000_00004" in text
    assert "drop_me" not in text
