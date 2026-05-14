"""Hermetic tests for mathlib_align_unknown_identifier.

These tests stub out the Mathlib name index and the paper-theory file system
so no real Mathlib walk or `lake env lean` invocation is needed. Each test
exercises one branch of the resolver:

  - exact full-name match in the index
  - same-last-component fuzzy / name-normalization match
  - no_match path (when nothing in the index overlaps)
  - paper-theory namespace-prefix match

Plus auxiliary coverage of:
  - `extract_unknown_identifiers_from_error` (parses Lean error strings)
  - `register_resolution` (appends to a temp alignments.json)
  - the on-disk name-index builder (small synthetic Mathlib tree)
"""

from __future__ import annotations

import json
from pathlib import Path

from mathlib_align_unknown_identifier import (
    Entry,
    build_name_index,
    extract_unknown_identifiers_from_error,
    find_paper_theory_namespace_prefix,
    register_resolution,
    resolve_unknown_identifier,
    score_candidate,
)


def _fake_name_index(entries: list[dict[str, str]]) -> dict:
    """Build the on-disk index shape from a list of entry dicts."""
    by_last: dict[str, list[int]] = {}
    for i, e in enumerate(entries):
        by_last.setdefault(e["last"], []).append(i)
    return {
        "schema_version": "mathlib_name_index.v1",
        "mathlib_root": "<fake>",
        "entries": entries,
        "by_last": by_last,
    }


# ---------------------------------------------------------------------------
# Branch 1: exact full-name match
# ---------------------------------------------------------------------------

def test_resolve_exact_match_returns_full_score() -> None:
    """When the unknown identifier exists verbatim in the index, the top
    candidate is the exact match with score 1.0 and verdict mathlib_match."""
    idx = _fake_name_index([
        {"name": "Matrix.transpose", "last": "transpose",
         "module": "Mathlib.Data.Matrix.Basic", "kind": "def"},
    ])
    r = resolve_unknown_identifier(
        paper_id="2604.21821", name="Matrix.transpose",
        name_index=idx,
    )
    assert r["verdict"] == "mathlib_match"
    assert r["candidates"][0]["target_name"] == "Matrix.transpose"
    assert r["candidates"][0]["score"] == 1.0
    assert r["candidates"][0]["kind"] == "exact_match"


# ---------------------------------------------------------------------------
# Branch 2: fuzzy / name-normalization match (same last component, different ns)
# ---------------------------------------------------------------------------

def test_resolve_name_normalization_when_namespace_is_wrong() -> None:
    """Querying `Matrix.dotProduct` should surface `dotProduct` (top-level)
    as a name_normalization candidate with score >= 0.9."""
    idx = _fake_name_index([
        {"name": "dotProduct", "last": "dotProduct",
         "module": "Mathlib.Data.Matrix.Mul", "kind": "def"},
        {"name": "Matrix.transpose", "last": "transpose",
         "module": "Mathlib.Data.Matrix.Basic", "kind": "def"},
    ])
    r = resolve_unknown_identifier(
        paper_id="2604.21821", name="Matrix.dotProduct",
        name_index=idx,
    )
    assert r["verdict"] == "mathlib_match"
    top = r["candidates"][0]
    assert top["target_name"] == "dotProduct"
    assert top["score"] >= 0.9
    assert top["kind"] == "name_normalization"


# ---------------------------------------------------------------------------
# Branch 3: no_match
# ---------------------------------------------------------------------------

def test_resolve_no_match_when_index_has_no_overlap() -> None:
    """If no entry shares a token or last-component, verdict is no_match
    and the candidate list is empty (or low-score)."""
    idx = _fake_name_index([
        {"name": "Real.exp", "last": "exp",
         "module": "Mathlib.Analysis.SpecialFunctions.Exp", "kind": "def"},
    ])
    r = resolve_unknown_identifier(
        paper_id="2304.09598", name="Multisegment.ofSegments",
        name_index=idx, min_score=0.9,
    )
    assert r["verdict"] == "no_match"
    # The optional fuzzy fallback may still emit low-confidence noise; the
    # contract is that verdict is no_match when nothing clears 0.9.
    assert all(c["score"] < 0.9 for c in r["candidates"])


# ---------------------------------------------------------------------------
# Branch 4: paper-theory namespace-prefix match
# ---------------------------------------------------------------------------

def test_resolve_namespace_prefix_from_paper_theory(tmp_path: Path) -> None:
    """If the paper-theory module declares `Foo`, then `Foo` (without the
    `Paper_<id>.` prefix) should resolve to namespace_prefix with score 0.95."""
    # Build a tiny paper-theory tree under tmp_path.
    paper_id = "9999.99999"
    module = f"Paper_{paper_id.replace('.', '_')}"
    pt_dir = tmp_path / "Desol" / "PaperTheory"
    pt_dir.mkdir(parents=True)
    (pt_dir / f"{module}.lean").write_text(
        f"namespace {module}\n"
        "abbrev Multisegment : Type := Nat\n"
        "def ofSegments (n : Nat) : Multisegment := n\n"
        f"end {module}\n",
        encoding="utf-8",
    )

    # No Mathlib matches at all — only the paper-theory namespace-prefix path
    # should fire.
    idx = _fake_name_index([])
    r = resolve_unknown_identifier(
        paper_id=paper_id, name="ofSegments",
        name_index=idx, project_root=tmp_path,
    )
    assert r["verdict"] == "namespace_prefix"
    top = r["candidates"][0]
    assert top["target_name"] == f"{module}.ofSegments"
    assert top["kind"] == "namespace_prefix"
    assert top["score"] >= 0.9


# ---------------------------------------------------------------------------
# Extract identifiers from Lean errors
# ---------------------------------------------------------------------------

def test_extract_unknown_identifiers_parses_lean_error() -> None:
    err = (
        "validation_gate_elaboration_failed:file_check_fail:/tmp/x.lean:24:238: "
        "error(lean.unknownIdentifier): Unknown constant `Matrix.dotProduct`\n"
        "/tmp/x.lean:24:279: error(lean.unknownIdentifier): Unknown constant `Matrix.mulVec`"
    )
    names = extract_unknown_identifiers_from_error(err)
    assert names == ["Matrix.dotProduct", "Matrix.mulVec"]


def test_extract_unknown_identifiers_handles_single_quotes() -> None:
    err = "error: unknown identifier 'Paper_2304_09598.Multisegment.ofSegments'"
    names = extract_unknown_identifiers_from_error(err)
    assert names == ["Paper_2304_09598.Multisegment.ofSegments"]


# ---------------------------------------------------------------------------
# Index builder over a synthetic Mathlib tree
# ---------------------------------------------------------------------------

def test_build_name_index_extracts_namespaced_decls(tmp_path: Path) -> None:
    """The index builder must track `namespace ... end` blocks correctly and
    qualify declarations accordingly."""
    fake_mathlib = tmp_path / "Mathlib"
    sub = fake_mathlib / "Data" / "Matrix"
    sub.mkdir(parents=True)
    (sub / "Mul.lean").write_text(
        "-- Some file\n"
        "namespace Matrix\n"
        "section DotProduct\n"
        "def dotProduct (v w : Nat) : Nat := v * w\n"
        "theorem dotProduct_comm : True := trivial\n"
        "end DotProduct\n"
        "def transpose (M : Nat) : Nat := M\n"
        "end Matrix\n"
        "def topLevelDef : Nat := 0\n",
        encoding="utf-8",
    )

    cache = tmp_path / "name_index.json"
    idx = build_name_index(
        mathlib_root=fake_mathlib, cache_path=cache, use_cache=False, progress=False,
    )
    names = {e["name"] for e in idx["entries"]}
    assert "Matrix.dotProduct" in names
    assert "Matrix.transpose" in names
    # `section DotProduct ... end DotProduct` is a section, not a namespace,
    # so dotProduct_comm stays under Matrix (not Matrix.DotProduct). Our
    # extractor uses `namespace` only, so this is the expected behavior.
    assert "Matrix.dotProduct_comm" in names
    assert "topLevelDef" in names
    # Cache file should have been written.
    assert cache.exists()
    cached = json.loads(cache.read_text(encoding="utf-8"))
    assert cached["schema_version"] == "mathlib_name_index.v1"


# ---------------------------------------------------------------------------
# Registry write-back
# ---------------------------------------------------------------------------

def test_register_resolution_appends_to_alignments_json(tmp_path: Path) -> None:
    alignments_path = tmp_path / "alignments.json"
    alignments_path.write_text(
        json.dumps({
            "schema_version": "alignments.v1",
            "alignments": [
                {"paper_id": "1234.5678", "paper_local_name": "old",
                 "fully_qualified": "Paper_1234_5678.old", "mathlib_target": "Nat",
                 "proof": "rfl", "kind": "type_abbrev"},
            ],
        }),
        encoding="utf-8",
    )
    result = {
        "schema_version": "mathlib_align_unknown_identifier.v1",
        "paper_id": "2604.21821",
        "name": "Matrix.dotProduct",
        "verdict": "mathlib_match",
        "candidates": [
            {"target_name": "dotProduct", "score": 0.95,
             "module": "Mathlib.Data.Matrix.Mul", "kind": "name_normalization",
             "rationale": "namespace correction"},
        ],
    }
    summary = register_resolution(
        result=result, alignments_path=alignments_path, min_score=0.9,
    )
    assert summary["registered"] == 1
    data = json.loads(alignments_path.read_text(encoding="utf-8"))
    # Original entry preserved.
    assert len(data["alignments"]) == 2
    new_entry = next(a for a in data["alignments"] if a["paper_local_name"] == "dotProduct")
    assert new_entry["paper_id"] == "2604.21821"
    assert new_entry["mathlib_target"] == "dotProduct"
    assert new_entry["kind"].startswith("auto_unknown_identifier:")
    assert "confidence" in new_entry


def test_register_resolution_skips_below_threshold(tmp_path: Path) -> None:
    """A weak candidate (score < threshold) must NOT be registered.
    Standards-positive: only verifiable alignments land in the registry."""
    alignments_path = tmp_path / "alignments.json"
    result = {
        "schema_version": "mathlib_align_unknown_identifier.v1",
        "paper_id": "2604.21821",
        "name": "Matrix.dotProduct",
        "verdict": "no_match",
        "candidates": [
            {"target_name": "Foo.bar", "score": 0.55,
             "module": "Mathlib.Something", "kind": "fuzzy_match",
             "rationale": "weak match"},
        ],
    }
    summary = register_resolution(
        result=result, alignments_path=alignments_path, min_score=0.9,
    )
    assert summary["registered"] == 0
    assert "below_threshold" in summary["reason"]
    # File never created when nothing is written.
    assert not alignments_path.exists()


# ---------------------------------------------------------------------------
# Scoring sanity
# ---------------------------------------------------------------------------

def test_score_candidate_prioritizes_last_component_match() -> None:
    """A namespace mismatch but identical last component must score >= 0.9
    (name_normalization kind), beating a longer fuzzy match."""
    e_norm = Entry(name="dotProduct", last="dotProduct",
                   module="Mathlib.Data.Matrix.Mul", kind="def")
    e_fuzz = Entry(name="Matrix.dotProductMul", last="dotProductMul",
                   module="Mathlib.Data.Matrix.Mul", kind="def")
    s_norm, k_norm = score_candidate("Matrix.dotProduct", e_norm)
    s_fuzz, k_fuzz = score_candidate("Matrix.dotProduct", e_fuzz)
    assert k_norm == "name_normalization"
    assert k_fuzz == "fuzzy_match"
    assert s_norm >= 0.9
    assert s_norm > s_fuzz


# ---------------------------------------------------------------------------
# Paper-theory namespace-prefix helper directly
# ---------------------------------------------------------------------------

def test_find_paper_theory_namespace_prefix_returns_none_when_absent(tmp_path: Path) -> None:
    """No paper-theory file → None."""
    r = find_paper_theory_namespace_prefix("0000.00000", "anything", project_root=tmp_path)
    assert r is None


def test_find_paper_theory_namespace_prefix_returns_match(tmp_path: Path) -> None:
    paper_id = "9999.99999"
    module = f"Paper_{paper_id.replace('.', '_')}"
    pt_dir = tmp_path / "Desol" / "PaperTheory"
    pt_dir.mkdir(parents=True)
    (pt_dir / f"{module}.lean").write_text(
        f"namespace {module}\n"
        "axiom foo : Nat → Nat\n"
        f"end {module}\n",
        encoding="utf-8",
    )
    r = find_paper_theory_namespace_prefix(paper_id, "foo", project_root=tmp_path)
    assert r is not None
    assert r["target_name"] == f"{module}.foo"
    assert r["kind"] == "namespace_prefix"
