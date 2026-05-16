"""Hermetic tests for ``scripts/autoproved_promotion.py``.

No lake, no Mistral, no HTTP. Every test uses ``tmp_path`` to simulate a
``Desol/PaperProofs/Paper_<id>.lean`` file. The module under test parses
its own section markers, so we only need to fake a minimally-shaped
curated file.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import autoproved_promotion as ap


# --- Fixtures -------------------------------------------------------------


def _seed_paper_proofs(tmp_path: Path, paper_id: str = "2999.99999") -> Path:
    """Write a minimal `Desol/PaperProofs/Paper_<id>.lean` that mimics
    the canonical shape (namespace + at least one curated theorem +
    closing `end` lines). Returns the file path.
    """
    module_safe = ap._safe_module_id(paper_id)
    target = tmp_path / "Desol" / "PaperProofs" / f"Paper_{module_safe}.lean"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "import Mathlib\n"
        "\n"
        "namespace PaperProofs\n"
        f"namespace Paper_{module_safe}\n"
        "\n"
        "theorem curated_one (n : ℕ) : 0 ≤ n := Nat.zero_le n\n"
        "\n"
        f"end Paper_{module_safe}\n"
        "end PaperProofs\n",
        encoding="utf-8",
    )
    return target


# --- Happy path -----------------------------------------------------------


def test_happy_path_appends_companion(tmp_path: Path) -> None:
    paper_id = "2999.99999"
    _seed_paper_proofs(tmp_path, paper_id)

    result = ap.promote_to_autoproved(
        paper_id=paper_id,
        theorem_name="auto_proof_8_rank_one_triangle",
        lean_statement=(
            "theorem auto_proof_8_rank_one_triangle (n : ℕ) : n + 0 = n"
        ),
        proof_body="exact Nat.add_zero n",
        project_root=tmp_path,
    )
    assert result["ok"] is True
    assert result["status"] == "written"
    assert result["autoproved_name"] == "auto_proof_8_rank_one_triangle__autoproved"
    assert result["sha"]

    text = Path(result["path"]).read_text(encoding="utf-8")
    assert "auto_proof_8_rank_one_triangle__autoproved" in text
    assert ap.SECTION_BEGIN in text
    assert ap.SECTION_END in text
    # Companion section sits BEFORE the namespace-closing `end` lines.
    end_pos = text.find("end PaperProofs")
    begin_pos = text.find(ap.SECTION_BEGIN)
    assert 0 < begin_pos < end_pos


# --- Idempotency ----------------------------------------------------------


def test_idempotent_repromotion_does_not_duplicate(tmp_path: Path) -> None:
    paper_id = "2999.99999"
    _seed_paper_proofs(tmp_path, paper_id)

    kwargs = dict(
        paper_id=paper_id,
        theorem_name="thm_alpha",
        lean_statement="theorem thm_alpha (n : ℕ) : 0 + n = n",
        proof_body="exact Nat.zero_add n",
        project_root=tmp_path,
    )
    first = ap.promote_to_autoproved(**kwargs)
    second = ap.promote_to_autoproved(**kwargs)
    assert first["ok"] is True and first["status"] == "written"
    assert second["ok"] is True and second["status"] == "idempotent"

    text = Path(first["path"]).read_text(encoding="utf-8")
    # Companion head should appear EXACTLY once.
    assert text.count("theorem thm_alpha__autoproved") == 1


# --- Multiple companions in same file -------------------------------------


def test_multiple_companions_share_section(tmp_path: Path) -> None:
    paper_id = "2999.99999"
    _seed_paper_proofs(tmp_path, paper_id)

    a = ap.promote_to_autoproved(
        paper_id=paper_id,
        theorem_name="thm_first",
        lean_statement="theorem thm_first (n : ℕ) : 0 + n = n",
        proof_body="exact Nat.zero_add n",
        project_root=tmp_path,
    )
    b = ap.promote_to_autoproved(
        paper_id=paper_id,
        theorem_name="thm_second",
        lean_statement="theorem thm_second (n : ℕ) : n + 0 = n",
        proof_body="exact Nat.add_zero n",
        project_root=tmp_path,
    )
    assert a["ok"] and b["ok"]
    text = Path(a["path"]).read_text(encoding="utf-8")
    assert "thm_first__autoproved" in text
    assert "thm_second__autoproved" in text
    # Exactly one BEGIN/END pair regardless of how many companions live in it.
    assert text.count(ap.SECTION_BEGIN) == 1
    assert text.count(ap.SECTION_END) == 1


# --- Pre-condition: trivialized statement ---------------------------------


def test_refuses_trivialized_statement(tmp_path: Path) -> None:
    paper_id = "2999.99999"
    target = _seed_paper_proofs(tmp_path, paper_id)
    original = target.read_text(encoding="utf-8")

    result = ap.promote_to_autoproved(
        paper_id=paper_id,
        theorem_name="trivial_thm",
        # `: True` is the canonical trivialization pattern flagged by
        # translator._translate._is_trivialized_signature.
        lean_statement="theorem trivial_thm : True",
        proof_body="trivial",
        project_root=tmp_path,
    )
    assert result["ok"] is False
    assert result["status"] == "refused_trivialized_statement"
    # File untouched.
    assert target.read_text(encoding="utf-8") == original


def test_refuses_existential_self_equality(tmp_path: Path) -> None:
    """`∃ x : ℝ, x = x` is the canonical fallback-shape trivialization
    (see `translator._translate._is_trivialized_signature`). The promotion
    must refuse it even when the proof body looks innocent.
    """
    paper_id = "2999.99999"
    target = _seed_paper_proofs(tmp_path, paper_id)
    original = target.read_text(encoding="utf-8")

    result = ap.promote_to_autoproved(
        paper_id=paper_id,
        theorem_name="rem_primitive_route",
        lean_statement="theorem rem_primitive_route : ∃ x : ℝ, x = x",
        proof_body="exact ⟨0, rfl⟩",
        project_root=tmp_path,
    )
    assert result["ok"] is False
    assert result["status"] == "refused_trivialized_statement"
    assert target.read_text(encoding="utf-8") == original


# --- Pre-condition: forbidden token ---------------------------------------


@pytest.mark.parametrize("bad_body, tag", [
    ("sorry", "sorry"),
    ("intro h\n  sorry", "sorry"),
    ("admit", "admit"),
    ("apply?", "apply?"),
    ("native_decide", "native_decide"),
])
def test_refuses_forbidden_token(tmp_path: Path, bad_body: str, tag: str) -> None:
    paper_id = "2999.99999"
    target = _seed_paper_proofs(tmp_path, paper_id)
    original = target.read_text(encoding="utf-8")

    result = ap.promote_to_autoproved(
        paper_id=paper_id,
        theorem_name="thm_x",
        lean_statement="theorem thm_x (n : ℕ) : 0 ≤ n",
        proof_body=bad_body,
        project_root=tmp_path,
    )
    assert result["ok"] is False
    assert result["status"].startswith("refused_forbidden_token:")
    assert tag in result["status"]
    # File untouched.
    assert target.read_text(encoding="utf-8") == original


# --- Section markers preserved across promotions --------------------------


def test_section_markers_preserved_across_promotions(tmp_path: Path) -> None:
    paper_id = "2999.99999"
    _seed_paper_proofs(tmp_path, paper_id)

    for i in range(3):
        r = ap.promote_to_autoproved(
            paper_id=paper_id,
            theorem_name=f"thm_{i}",
            lean_statement=f"theorem thm_{i} (n : ℕ) : n + 0 = n",
            proof_body="exact Nat.add_zero n",
            project_root=tmp_path,
        )
        assert r["ok"]
    text = Path(r["path"]).read_text(encoding="utf-8")
    assert text.count(ap.SECTION_BEGIN) == 1
    assert text.count(ap.SECTION_END) == 1
    # All three companions present.
    for i in range(3):
        assert f"thm_{i}__autoproved" in text


# --- validate_elaboration callback honored --------------------------------


def test_validation_failure_rolls_back(tmp_path: Path) -> None:
    paper_id = "2999.99999"
    target = _seed_paper_proofs(tmp_path, paper_id)
    original = target.read_text(encoding="utf-8")

    def fake_validate(path: Path) -> tuple[bool, str]:
        # Simulate a lake failure — caller-provided validation rejects.
        return False, "elaboration failed: type mismatch"

    result = ap.promote_to_autoproved(
        paper_id=paper_id,
        theorem_name="bad_proof",
        lean_statement="theorem bad_proof (n : ℕ) : n + 0 = n",
        proof_body="exact Nat.add_zero n",
        project_root=tmp_path,
        validate_elaboration=fake_validate,
    )
    assert result["ok"] is False
    assert result["status"] == "validation_failed"
    assert "type mismatch" in result["validation_error"]
    # The file must be restored to its pre-write state.
    assert target.read_text(encoding="utf-8") == original


def test_validation_success_keeps_write(tmp_path: Path) -> None:
    paper_id = "2999.99999"
    _seed_paper_proofs(tmp_path, paper_id)

    seen: dict[str, Path] = {}

    def fake_validate(path: Path) -> tuple[bool, str]:
        seen["path"] = path
        return True, ""

    result = ap.promote_to_autoproved(
        paper_id=paper_id,
        theorem_name="good_proof",
        lean_statement="theorem good_proof (n : ℕ) : 0 + n = n",
        proof_body="exact Nat.zero_add n",
        project_root=tmp_path,
        validate_elaboration=fake_validate,
    )
    assert result["ok"] is True
    assert result["status"] == "written"
    assert "good_proof__autoproved" in Path(result["path"]).read_text("utf-8")
    assert seen["path"] == Path(result["path"])


# --- Missing curated file --------------------------------------------------


def test_refuses_missing_paper_proofs_file(tmp_path: Path) -> None:
    """If the curated file does not exist yet, we don't create one out of
    thin air — that's the responsibility of the curator. The promotion
    refuses cleanly.
    """
    result = ap.promote_to_autoproved(
        paper_id="9999.00000",
        theorem_name="thm_x",
        lean_statement="theorem thm_x (n : ℕ) : 0 ≤ n",
        proof_body="exact Nat.zero_le n",
        project_root=tmp_path,
    )
    assert result["ok"] is False
    assert result["status"] == "refused_missing_paper_proofs_file"


# --- Render helper ---------------------------------------------------------


def test_render_strips_existing_proof_tail() -> None:
    decl, name = ap.render_autoproved_decl(
        theorem_name="ns.thm_y",
        lean_statement="theorem thm_y (n : ℕ) : 0 ≤ n := by sorry",
        proof_body="exact Nat.zero_le n",
    )
    assert name == "thm_y__autoproved"
    assert "theorem thm_y__autoproved" in decl
    # Old `:= by sorry` tail must NOT survive.
    assert "sorry" not in decl
    assert ":= by\n  exact Nat.zero_le n" in decl


def test_render_namespace_qualified_name_short() -> None:
    """A namespace-qualified ledger name like `Foo.Bar.thm_z` collapses
    to the short head `thm_z__autoproved`, mirroring how curated
    PaperProofs declarations are unqualified (the namespace is set at
    the file level).
    """
    decl, name = ap.render_autoproved_decl(
        theorem_name="AutoPaper_2604_21884.thm_z",
        lean_statement="theorem thm_z : 1 + 1 = 2",
        proof_body="rfl",
    )
    assert name == "thm_z__autoproved"
    assert "theorem thm_z__autoproved" in decl


def test_render_tags_aesop_safe_apply() -> None:
    """Autoproved closures carry the ``@[aesop safe apply]`` attribute
    so downstream sweeps' aesop calls can use them as hints — closure
    compounding. The attribute must precede the theorem header."""
    decl, _name = ap.render_autoproved_decl(
        theorem_name="thm_compound",
        lean_statement="theorem thm_compound (n : ℕ) : 0 ≤ n := by sorry",
        proof_body="exact Nat.zero_le n",
    )
    assert decl.startswith("@[aesop safe apply]\n")
    # The attribute must be on its own line BEFORE the `theorem` keyword.
    attr_line, _rest = decl.split("\n", 1)
    assert attr_line == "@[aesop safe apply]"
    assert "\ntheorem thm_compound__autoproved" in decl


# --- Path helper -----------------------------------------------------------


def test_autoproved_target_path_safe_id() -> None:
    p = ap.autoproved_target_path(Path("/tmp/proj"), "2604.21884")
    # Dot in paper id becomes underscore in the module name.
    assert p == Path("/tmp/proj/Desol/PaperProofs/Paper_2604_21884.lean")
