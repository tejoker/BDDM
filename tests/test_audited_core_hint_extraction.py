"""Hermetic tests for `extract_audited_core_hints`.

No live filesystem dependencies except optional reads from the in-tree
`Desol/PaperProofs/` (covered by the "real-file" test). All other tests
use `tmp_path`-rooted scratch trees.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import extract_audited_core_hints as ech


# --- Helpers --------------------------------------------------------------


def _make_paper_proofs_tree(
    tmp_path: Path,
    *,
    curated: dict[str, str] | None = None,
    auto: dict[str, str] | None = None,
) -> Path:
    """Build a fake `Desol/PaperProofs/` layout. Returns the project root.

    `curated` and `auto` map paper-filename-stem (e.g. `Paper_2604_21884`)
    to the file contents.
    """
    root = tmp_path / "fake_root"
    base = root / "Desol" / "PaperProofs"
    base.mkdir(parents=True)
    for stem, body in (curated or {}).items():
        (base / f"{stem}.lean").write_text(body, encoding="utf-8")
    auto_dir = base / "Auto"
    if auto:
        auto_dir.mkdir(parents=True, exist_ok=True)
        for stem, body in auto.items():
            (auto_dir / f"{stem}.lean").write_text(body, encoding="utf-8")
    return root


_REAL_FILE_BODY = """\
import Mathlib

namespace Demo

theorem one_eq_one : 1 = 1 := by
  rfl

theorem add_zero_nat (n : Nat) : n + 0 = n := by
  simp

theorem calc_demo (a b : Nat) : a + b = b + a := by
  calc
    a + b = b + a := by rw [Nat.add_comm]

end Demo
"""


# --- Discovery / parsing --------------------------------------------------


def test_walk_paper_proofs_files_returns_curated_and_auto(tmp_path: Path) -> None:
    root = _make_paper_proofs_tree(
        tmp_path,
        curated={"Paper_2604_21884": _REAL_FILE_BODY},
        auto={"Paper_2604_21884": _REAL_FILE_BODY, "Paper_2304_09598": _REAL_FILE_BODY},
    )
    paths = ech.walk_paper_proofs_files(root)
    rel = sorted(str(p.relative_to(root)) for p in paths)
    assert rel == sorted(
        [
            "Desol/PaperProofs/Paper_2604_21884.lean",
            "Desol/PaperProofs/Auto/Paper_2604_21884.lean",
            "Desol/PaperProofs/Auto/Paper_2304_09598.lean",
        ]
    )


def test_walk_returns_empty_when_directory_absent(tmp_path: Path) -> None:
    assert ech.walk_paper_proofs_files(tmp_path) == []


def test_parse_paper_id_from_path_round_trips() -> None:
    assert ech.parse_paper_id_from_path(Path("Paper_2604_21884.lean")) == "2604.21884"
    assert ech.parse_paper_id_from_path(Path("Paper_2304_09598.lean")) == "2304.09598"
    # Filename pattern mismatch yields None.
    assert ech.parse_paper_id_from_path(Path("PaperFoo.lean")) is None
    assert ech.parse_paper_id_from_path(Path("Paper_X_Y.lean")) is None


def test_extract_theorem_blocks_finds_theorems_only() -> None:
    blocks = ech.extract_theorem_blocks(_REAL_FILE_BODY, source_tier=0)
    names = sorted(b["name"] for b in blocks)
    assert names == ["add_zero_nat", "calc_demo", "one_eq_one"]
    calc_block = next(b for b in blocks if b["name"] == "calc_demo")
    assert calc_block["has_calc"] is True
    assert calc_block["tactic_count"] >= 1


# --- Hint construction ----------------------------------------------------


def test_build_paper_hint_non_empty_for_real_input() -> None:
    blocks = ech.extract_theorem_blocks(_REAL_FILE_BODY)
    hint = ech.build_paper_hint(blocks, max_chars=4000)
    assert hint, "expected a non-empty hint for a populated PaperProofs file"
    # At minimum, the highest-priority block name should appear.
    assert "calc_demo" in hint or "add_zero_nat" in hint


def test_build_paper_hint_caps_to_max_chars() -> None:
    # Build a body with many oversized theorems to force the cap.
    big_block = "\n".join(["theorem big_{i} : True := by trivial".replace("{i}", str(i)) for i in range(200)])
    src = "import Mathlib\n" + big_block
    blocks = ech.extract_theorem_blocks(src)
    hint = ech.build_paper_hint(blocks, max_chars=500)
    assert hint, "expected at least one (truncated) block under a tight cap"
    # Allow modest header/truncation overhead but not gross runaway.
    assert len(hint) <= 800, f"hint exceeded soft bound, got {len(hint)}"


def test_build_paper_hint_returns_empty_for_no_blocks() -> None:
    assert ech.build_paper_hint([], max_chars=4000) == ""
    # File with no theorem/lemma decls -> empty too.
    src = "import Mathlib\nnamespace X\ndef foo := 1\nend X\n"
    blocks = ech.extract_theorem_blocks(src)
    assert blocks == []
    assert ech.build_paper_hint(blocks) == ""


def test_build_all_hints_writes_cache_and_returns_mapping(tmp_path: Path) -> None:
    root = _make_paper_proofs_tree(
        tmp_path,
        curated={"Paper_2604_21884": _REAL_FILE_BODY},
        auto={"Paper_2304_09598": _REAL_FILE_BODY},
    )
    cache_dir = tmp_path / "cache"
    out = ech.build_all_hints(root=root, cache_dir=cache_dir, max_chars=4000)
    assert set(out) == {"2604.21884", "2304.09598"}
    assert all(out[pid] for pid in out)
    # Cache files are written.
    assert (cache_dir / "2604.21884.txt").exists()
    assert (cache_dir / "2304.09598.txt").exists()
    # `load_hint` reads from the same cache.
    assert ech.load_hint("2604.21884", cache_dir=cache_dir) == out["2604.21884"]
    # Missing paper -> empty.
    assert ech.load_hint("9999.99999", cache_dir=cache_dir) == ""


def test_real_paper_proofs_file_yields_hint_for_2604_21884() -> None:
    """Live-tree check: the in-tree curated Paper_2604_21884.lean produces a
    non-empty hint when present (otherwise the test is a no-op)."""
    project_root = Path(__file__).resolve().parent.parent
    paper_file = project_root / "Desol" / "PaperProofs" / "Paper_2604_21884.lean"
    if not paper_file.exists():
        pytest.skip("curated PaperProofs file not present in this checkout")
    blocks = ech.extract_theorem_blocks(paper_file.read_text(encoding="utf-8"))
    hint = ech.build_paper_hint(blocks, max_chars=4000)
    assert hint, "real PaperProofs file produced empty hint"
    assert len(hint) <= 4000 + 64  # allow tiny header overhead
    assert "theorem" in hint
