from __future__ import annotations

from pathlib import Path

from regenerate_paper_imports_anchor import (
    ANCHOR_PATH,
    PAPER_THEORY_DIR,
    regenerate,
)


def _make_paper_theory_module(project_root: Path, module_name: str, *, with_olean: bool) -> None:
    """Create a Paper_*.lean source under PaperTheory/, optionally with a fresh .olean."""
    src = project_root / PAPER_THEORY_DIR / f"{module_name}.lean"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(f"namespace {module_name}\n\nend {module_name}\n", encoding="utf-8")
    if with_olean:
        olean = (
            project_root / ".lake" / "build" / "lib" / "lean" / "Desol" / "PaperTheory" / f"{module_name}.olean"
        )
        olean.parent.mkdir(parents=True, exist_ok=True)
        olean.write_text("", encoding="utf-8")
        # Bump olean mtime to be strictly newer than source.
        import os, time
        future = src.stat().st_mtime + 5
        os.utime(olean, (future, future))


def test_regenerate_includes_only_buildable_modules(tmp_path: Path) -> None:
    """Modules with a stale or missing .olean must be excluded from the anchor."""
    _make_paper_theory_module(tmp_path, "Paper_2304_99999", with_olean=True)
    _make_paper_theory_module(tmp_path, "Paper_2604_00001", with_olean=False)

    out = regenerate(tmp_path)

    assert out == tmp_path / ANCHOR_PATH
    text = out.read_text(encoding="utf-8")
    assert "import Mathlib" in text
    assert "import Desol.PaperTheory.Paper_2304_99999" in text
    assert "import Desol.PaperTheory.Paper_2604_00001" not in text
    assert "namespace Desol" in text
    assert "paper_imports_anchor_trivial" in text


def test_regenerate_is_idempotent(tmp_path: Path) -> None:
    """Re-running regenerate with no changes must leave the anchor file untouched."""
    _make_paper_theory_module(tmp_path, "Paper_2304_99998", with_olean=True)

    out = regenerate(tmp_path)
    first_mtime = out.stat().st_mtime
    second_out = regenerate(tmp_path)
    assert second_out == out
    assert out.stat().st_mtime == first_mtime  # no rewrite when content matches


def test_regenerate_handles_missing_paper_theory_dir(tmp_path: Path) -> None:
    """No Paper_* modules → anchor still emitted, with just Mathlib."""
    out = regenerate(tmp_path)
    text = out.read_text(encoding="utf-8")
    assert "import Mathlib" in text
    assert "import Desol.PaperTheory" not in text
    assert "paper_imports_anchor_trivial" in text


def test_regenerate_includes_repair_variants(tmp_path: Path) -> None:
    """Paper_* modules under the Repair/ subdirectory should also be discovered."""
    repair_src = tmp_path / "Desol" / "PaperTheory" / "Repair" / "Paper_2304_77777.lean"
    repair_src.parent.mkdir(parents=True, exist_ok=True)
    repair_src.write_text("namespace Paper_2304_77777_Repair\n\nend Paper_2304_77777_Repair\n", encoding="utf-8")
    olean = (
        tmp_path / ".lake" / "build" / "lib" / "lean" / "Desol" / "PaperTheory" / "Repair" / "Paper_2304_77777.olean"
    )
    olean.parent.mkdir(parents=True, exist_ok=True)
    olean.write_text("", encoding="utf-8")
    import os
    future = repair_src.stat().st_mtime + 5
    os.utime(olean, (future, future))

    out = regenerate(tmp_path)
    text = out.read_text(encoding="utf-8")
    assert "import Desol.PaperTheory.Repair.Paper_2304_77777" in text
