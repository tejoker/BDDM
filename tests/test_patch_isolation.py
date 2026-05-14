"""Hermetic tests for the patch-isolation helper (Improvement 2):
`sweep_lemma_factor_v2._run_isolated_patch_check`.

The helper validates a candidate proof body against a CLEAN BASELINE
isolated `.lean` file (prelude scraped from the on-disk source + the
target theorem with the candidate body) so pre-existing errors in
unrelated theorems on disk can't contaminate the result.

We mock `prove_arxiv_batch._run_isolated_file_check` by patching the
module-level symbol on `sweep_lemma_factor_v2`. No actual lake invocation
runs in the tests.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import sweep_lemma_factor_v2 as sweep


# --- Fixture: pre-built lean file with a clean target + broken other -----

_LEAN_SRC_WITH_BROKEN_NEIGHBOUR = """\
import Mathlib

set_option autoImplicit true

namespace ArxivPaper

theorem broken_one (n : ℕ) : NotARealLemma n = 42 := by
  sorry

theorem target (n : ℕ) : n + 0 = n := by
  sorry

theorem broken_two (n : ℕ) : AnotherFakeName n := by
  sorry

end ArxivPaper
"""


@pytest.fixture
def lean_file_with_broken_neighbours(tmp_path: Path) -> Path:
    f = tmp_path / "2999.99999.lean"
    f.write_text(_LEAN_SRC_WITH_BROKEN_NEIGHBOUR, encoding="utf-8")
    return f


# --- Helpers --------------------------------------------------------------


class _IsolatedCheckRecorder:
    """Mock `_run_isolated_file_check`. Records each call and returns a
    configurable (ok, error_tail). The recorder can simulate either a
    clean-pass or a body-emits-sorry-warning failure."""

    def __init__(self, *, should_pass: bool = True, err: str = "") -> None:
        self.calls: list[dict[str, Any]] = []
        self.should_pass = should_pass
        self.err = err

    def __call__(
        self,
        *,
        project_root: Path,
        source_file: Path,
        theorem_decl: str,
        timeout_s: int = 45,
        proof_body: str | None = None,
    ) -> tuple[bool, str]:
        self.calls.append({
            "project_root": str(project_root),
            "source_file": str(source_file),
            "theorem_decl": theorem_decl,
            "timeout_s": timeout_s,
            "proof_body": proof_body,
        })
        if self.should_pass:
            return True, ""
        return False, self.err or "mocked_failure"


# =====================================================================
# 1. The isolated patch check passes the candidate body THROUGH to the
#    inner `_run_isolated_file_check` and accepts on its return.
# =====================================================================

def test_isolated_patch_check_passes_body_to_inner(
    monkeypatch: pytest.MonkeyPatch,
    lean_file_with_broken_neighbours: Path,
) -> None:
    rec = _IsolatedCheckRecorder(should_pass=True)
    monkeypatch.setattr(sweep, "_run_isolated_file_check", rec)

    ok, err = sweep._run_isolated_patch_check(
        lean_file=lean_file_with_broken_neighbours,
        theorem_name="target",
        proof_body="simp",
        timeout_s=30,
    )
    assert ok is True
    assert err == ""
    assert len(rec.calls) == 1
    call = rec.calls[0]
    assert call["proof_body"] == "simp"
    # The target theorem signature was scraped from the file (the broken
    # neighbours sit BEFORE/AFTER it and are not in `theorem_decl`).
    assert "theorem target" in call["theorem_decl"]
    assert "broken_one" not in call["theorem_decl"]
    assert "broken_two" not in call["theorem_decl"]


# =====================================================================
# 2. Pre-existing errors in OTHER theorems do NOT contaminate the result:
#    the isolated check is invoked with ONLY the target's decl + body,
#    so the inner mock never sees the broken neighbour text.
# =====================================================================

def test_isolated_patch_check_drops_broken_neighbours_from_decl(
    monkeypatch: pytest.MonkeyPatch,
    lean_file_with_broken_neighbours: Path,
) -> None:
    rec = _IsolatedCheckRecorder(should_pass=True)
    monkeypatch.setattr(sweep, "_run_isolated_file_check", rec)

    sweep._run_isolated_patch_check(
        lean_file=lean_file_with_broken_neighbours,
        theorem_name="target",
        proof_body="simp",
    )
    call = rec.calls[0]
    # Critical contamination guard: the broken-neighbour names must not
    # appear in the theorem_decl that lake actually sees.
    assert "NotARealLemma" not in call["theorem_decl"]
    assert "AnotherFakeName" not in call["theorem_decl"]


# =====================================================================
# 3. Empty / missing target name -> isolated_patch_check_decl_not_found.
# =====================================================================

def test_isolated_patch_check_missing_target_fails(
    monkeypatch: pytest.MonkeyPatch,
    lean_file_with_broken_neighbours: Path,
) -> None:
    rec = _IsolatedCheckRecorder(should_pass=True)
    monkeypatch.setattr(sweep, "_run_isolated_file_check", rec)

    ok, err = sweep._run_isolated_patch_check(
        lean_file=lean_file_with_broken_neighbours,
        theorem_name="not_in_file",
        proof_body="simp",
    )
    assert ok is False
    assert "decl_not_found" in err
    # Inner check was NOT invoked.
    assert rec.calls == []


# =====================================================================
# 4. Caller can pass `theorem_decl` explicitly to override the scrape.
# =====================================================================

def test_isolated_patch_check_explicit_decl_override(
    monkeypatch: pytest.MonkeyPatch,
    lean_file_with_broken_neighbours: Path,
) -> None:
    rec = _IsolatedCheckRecorder(should_pass=True)
    monkeypatch.setattr(sweep, "_run_isolated_file_check", rec)

    # Override the decl entirely (e.g. for an aux that's been renamed in
    # memory but not yet inserted into the file).
    custom = "theorem aux_thing (n : ℕ) : n + 0 = n"
    sweep._run_isolated_patch_check(
        lean_file=lean_file_with_broken_neighbours,
        theorem_name="aux_thing",
        proof_body="simp",
        theorem_decl=custom,
    )
    assert rec.calls[0]["theorem_decl"] == custom


# =====================================================================
# 5. extra_decls (aux lemmas) are prepended ahead of the target so the
#    composition body can reference them inside the isolated baseline.
# =====================================================================

def test_isolated_patch_check_extra_decls_prepended(
    monkeypatch: pytest.MonkeyPatch,
    lean_file_with_broken_neighbours: Path,
) -> None:
    rec = _IsolatedCheckRecorder(should_pass=True)
    monkeypatch.setattr(sweep, "_run_isolated_file_check", rec)

    aux1 = "theorem aux_one : True := by trivial"
    aux2 = "theorem aux_two : True := by trivial"
    sweep._run_isolated_patch_check(
        lean_file=lean_file_with_broken_neighbours,
        theorem_name="target",
        proof_body="exact And.intro aux_one aux_two",
        extra_decls=[aux1, aux2],
    )
    decl = rec.calls[0]["theorem_decl"]
    # Both aux blocks appear BEFORE the target so the parent body can
    # reference them.
    pos_aux1 = decl.find("aux_one")
    pos_aux2 = decl.find("aux_two")
    pos_target = decl.find("theorem target")
    assert pos_aux1 != -1 and pos_aux2 != -1 and pos_target != -1
    assert pos_aux1 < pos_target
    assert pos_aux2 < pos_target


# =====================================================================
# 6. Failure propagation: inner check returns (False, err) -> we surface
#    that error to the caller verbatim.
# =====================================================================

def test_isolated_patch_check_propagates_inner_failure(
    monkeypatch: pytest.MonkeyPatch,
    lean_file_with_broken_neighbours: Path,
) -> None:
    rec = _IsolatedCheckRecorder(should_pass=False, err="file_check_fail:type mismatch")
    monkeypatch.setattr(sweep, "_run_isolated_file_check", rec)

    ok, err = sweep._run_isolated_patch_check(
        lean_file=lean_file_with_broken_neighbours,
        theorem_name="target",
        proof_body="bogus",
    )
    assert ok is False
    assert "type mismatch" in err


# =====================================================================
# 7. When _run_isolated_file_check is unavailable (e.g. lake bridge
#    missing in the sandbox), the helper degrades gracefully — caller
#    falls through to downstream gates instead of getting a hard error.
# =====================================================================

def test_isolated_patch_check_no_lake_bridge_passes_through(
    monkeypatch: pytest.MonkeyPatch,
    lean_file_with_broken_neighbours: Path,
) -> None:
    monkeypatch.setattr(sweep, "_run_isolated_file_check", None)
    ok, err = sweep._run_isolated_patch_check(
        lean_file=lean_file_with_broken_neighbours,
        theorem_name="target",
        proof_body="simp",
    )
    assert ok is True
    assert "no_lake" in err


# =====================================================================
# 8. The `_extract_theorem_decl_from_file` helper picks the correct
#    theorem when the file has multiple decls — and ends the block at
#    the next top-level declaration, never bleeding into neighbours.
# =====================================================================

def test_extract_theorem_decl_picks_correct_block(
    lean_file_with_broken_neighbours: Path,
) -> None:
    text = lean_file_with_broken_neighbours.read_text(encoding="utf-8")
    decl = sweep._extract_theorem_decl_from_file(text, "target")
    assert "theorem target" in decl
    # Block does not bleed into the next decl.
    assert "broken_two" not in decl
    assert "AnotherFakeName" not in decl
    # Block does not include the previous decl either.
    assert "broken_one" not in decl


def test_extract_theorem_decl_returns_empty_when_missing(
    lean_file_with_broken_neighbours: Path,
) -> None:
    text = lean_file_with_broken_neighbours.read_text(encoding="utf-8")
    assert sweep._extract_theorem_decl_from_file(text, "totally_absent") == ""
    # Empty input handled safely.
    assert sweep._extract_theorem_decl_from_file("", "target") == ""
    assert sweep._extract_theorem_decl_from_file(text, "") == ""


# =====================================================================
# 9. Direct test of `prove_arxiv_batch._run_isolated_file_check` with a
#    candidate body (no lake invocation — we use a minimal source file
#    and check the file-construction path runs without exceptions).
#    Lake is mocked via subprocess.run.
# =====================================================================

def test_run_isolated_file_check_with_proof_body_constructs_isolated_src(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from prove_arxiv_batch import _run_isolated_file_check
    import subprocess as _sp

    captured_src: dict[str, str] = {}

    class _FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kw):
        # The isolated .lean file is the last positional arg; capture its
        # contents so we can assert the body was patched in correctly.
        iso_path = Path(cmd[-1])
        if iso_path.exists():
            captured_src["src"] = iso_path.read_text(encoding="utf-8")
        return _FakeProc()

    monkeypatch.setattr(_sp, "run", _fake_run)

    # Build a minimal source file with a target signature.
    src = tmp_path / "fake.lean"
    src.write_text(
        "import Mathlib\nnamespace ArxivPaper\n\ntheorem foo (n : ℕ) : n + 0 = n := by\n  sorry\n",
        encoding="utf-8",
    )

    ok, err = _run_isolated_file_check(
        project_root=tmp_path,
        source_file=src,
        theorem_decl="theorem foo (n : ℕ) : n + 0 = n",
        timeout_s=10,
        proof_body="simp",
    )
    assert ok is True
    # The isolated `.lean` file contained `simp` (the candidate body) and
    # NOT `sorry`.
    iso = captured_src.get("src", "")
    assert "simp" in iso
    assert "by sorry" not in iso
