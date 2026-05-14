"""Hermetic tests for Mathlib-anchor injection in the Leanstral whole-proof
retry prompt.

These tests cover both layers of A1 / A3 wiring:

  * Error-tail parsing: `unknown identifier 'X'` / `synthInstanceFailed:
    <Class>` extraction and candidate resolution against a MOCKED Mathlib
    name index. We never touch the real 36MB index.
  * Premise retrieval (A3): token-overlap scoring against a synthetic
    `PremiseIndex` built from a handful of fake entries.

Everything is hermetic: no network, no lake, no real mathlib. The Mistral
client is mocked the same way as `test_leanstral_whole_proof_generator.py`.
"""
from __future__ import annotations

import json
from typing import Any

import leanstral_proof_anchors as anchors
import leanstral_whole_proof_generator as gen


# --- Mock Mistral client (matches the canonical FakeClient shape) --------


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeChat:
    def __init__(self, contents: list[str]) -> None:
        self._contents = list(contents)
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        if not self._contents:
            return _FakeResponse("")
        return _FakeResponse(self._contents.pop(0))


class FakeClient:
    def __init__(self, contents: list[str] | str) -> None:
        if isinstance(contents, str):
            contents = [contents]
        self.chat = _FakeChat(contents)


def _proof_response(body: str, *, confidence: float = 0.5) -> str:
    return json.dumps({"proof_body": body, "reasoning": "ok", "confidence": confidence})


# --- Mock Mathlib name index ---------------------------------------------


def _mock_name_index() -> dict[str, Any]:
    """A tiny stand-in for `data/mathlib_name_index.json`. Three Mathlib
    entries that exercise both `unknown identifier` and `synthInstanceFailed`
    code paths."""
    entries = [
        {"name": "dotProduct", "last": "dotProduct",
         "module": "Mathlib.Data.Matrix.Mul", "kind": "def"},
        {"name": "Matrix.mulVec", "last": "mulVec",
         "module": "Mathlib.Data.Matrix.Basic", "kind": "def"},
        {"name": "IsClosed", "last": "IsClosed",
         "module": "Mathlib.Topology.Basic", "kind": "structure"},
        {"name": "Set.isClosed_eq", "last": "isClosed_eq",
         "module": "Mathlib.Topology.Basic", "kind": "theorem"},
        {"name": "isClosed_singleton", "last": "isClosed_singleton",
         "module": "Mathlib.Topology.MetricSpace.Basic", "kind": "theorem"},
        {"name": "Module", "last": "Module",
         "module": "Mathlib.Algebra.Module.Basic", "kind": "class"},
    ]
    by_last: dict[str, list[int]] = {}
    for i, e in enumerate(entries):
        by_last.setdefault(e["last"], []).append(i)
    return {
        "schema_version": "mathlib_name_index.v1",
        "mathlib_root": "<mock>",
        "entries": entries,
        "by_last": by_last,
    }


def _mock_premise_index() -> anchors.PremiseIndex:
    """Synthetic premise index covering a few topology + algebra lemmas."""
    entries = [
        anchors.PremiseEntry(
            name="isClosed_singleton",
            statement="theorem isClosed_singleton {α} [TopologicalSpace α] [T1Space α] (a : α) : IsClosed {a}",
            module="Mathlib.Topology.MetricSpace.Basic",
            tokens=sorted(anchors.tokenize_for_premise(
                "isClosed_singleton TopologicalSpace T1Space IsClosed"
            )),
        ),
        anchors.PremiseEntry(
            name="Set.isClosed_eq",
            statement="theorem Set.isClosed_eq (hf : Continuous f) (hg : Continuous g) : IsClosed {x | f x = g x}",
            module="Mathlib.Topology.Basic",
            tokens=sorted(anchors.tokenize_for_premise(
                "isClosed_eq Continuous IsClosed Set"
            )),
        ),
        anchors.PremiseEntry(
            name="add_comm",
            statement="theorem add_comm {α} [AddCommMonoid α] (a b : α) : a + b = b + a",
            module="Mathlib.Algebra.Group.Basic",
            tokens=sorted(anchors.tokenize_for_premise(
                "add_comm AddCommMonoid"
            )),
        ),
        anchors.PremiseEntry(
            name="mul_comm",
            statement="theorem mul_comm {α} [CommMonoid α] (a b : α) : a * b = b * a",
            module="Mathlib.Algebra.Group.Basic",
            tokens=sorted(anchors.tokenize_for_premise(
                "mul_comm CommMonoid"
            )),
        ),
    ]
    return anchors.PremiseIndex(entries=entries, mathlib_root="<mock>")


# =====================================================================
# Test 1: unknown-identifier extraction from various error tail formats.
# =====================================================================

def test_extract_unknown_identifier_single_quotes() -> None:
    tail = "error: unknown identifier 'dotProduct'"
    names = anchors._extract_unknown_identifier_names(tail)
    assert names == ["dotProduct"]


def test_extract_unknown_identifier_backticks_and_dedup() -> None:
    tail = (
        "error: unknown identifier `Matrix.mulVec`\n"
        "error: unknown constant `Matrix.mulVec`\n"
        "error: unknown identifier 'IsClosed'"
    )
    names = anchors._extract_unknown_identifier_names(tail)
    # Mulvec appears twice but is deduped; order preserved.
    assert names == ["Matrix.mulVec", "IsClosed"]


def test_extract_synth_instance_classes_single_line() -> None:
    tail = "error: synthInstanceFailed: Module ℝ V"
    classes = anchors._extract_synth_instance_classes(tail)
    assert classes == ["Module"]


def test_extract_synth_instance_classes_multiline() -> None:
    """Lean often prints `failed to synthesize instance of type class` on
    one line with the class name on the next non-blank line. The regex must
    catch that form (this is what 2604.21583 / MeasurableSpace looks like
    in real lake output)."""
    tail = "failed to synthesize instance of type class\n  MeasurableSpace alpha\n\nHint: ..."
    classes = anchors._extract_synth_instance_classes(tail)
    assert classes == ["MeasurableSpace"]


def test_no_anchor_pattern_returns_empty() -> None:
    tail = "error: type mismatch at `Nat.le_succ`\nexpected `ℕ` got `Int`"
    assert anchors._extract_unknown_identifier_names(tail) == []
    assert anchors._extract_synth_instance_classes(tail) == []


# =====================================================================
# Test 2: anchor extraction returns Mathlib candidates with module + score.
# =====================================================================

def test_extract_error_anchors_unknown_identifier() -> None:
    idx = _mock_name_index()
    blocks = anchors.extract_error_anchors(
        error_tail="error: unknown identifier 'dotProduct'",
        name_index=idx,
        paper_id="p1",
        top_k=3,
    )
    assert len(blocks) == 1
    block = blocks[0]
    assert block.name == "dotProduct"
    assert block.source == "unknown_identifier"
    # Top candidate should be the exact match `dotProduct`.
    top = block.candidates[0]
    assert top.target_name == "dotProduct"
    assert top.module == "Mathlib.Data.Matrix.Mul"
    assert top.score >= 0.9


def test_extract_error_anchors_synth_instance() -> None:
    idx = _mock_name_index()
    blocks = anchors.extract_error_anchors(
        error_tail="error: synthInstanceFailed: Module ℝ V",
        name_index=idx,
        top_k=3,
    )
    assert len(blocks) == 1
    block = blocks[0]
    assert block.source == "synth_instance"
    assert block.name == "Module"
    names = [c.target_name for c in block.candidates]
    assert "Module" in names


def test_extract_error_anchors_empty_when_no_match() -> None:
    idx = _mock_name_index()
    # An error tail that mentions a name not in our mock index → no anchor
    # is returned (the resolver returns no candidates above the floor).
    blocks = anchors.extract_error_anchors(
        error_tail="error: type mismatch on Nat.le",
        name_index=idx,
    )
    assert blocks == []


def test_extract_error_anchors_empty_index() -> None:
    blocks = anchors.extract_error_anchors(
        error_tail="error: unknown identifier 'dotProduct'",
        name_index={},
    )
    assert blocks == []


# =====================================================================
# Test 3: anchor block rendering.
# =====================================================================

def test_build_anchor_block_renders_candidates() -> None:
    idx = _mock_name_index()
    blocks = anchors.extract_error_anchors(
        error_tail="error: unknown identifier 'dotProduct'",
        name_index=idx,
    )
    rendered = anchors.build_anchor_block(blocks)
    assert "dotProduct" in rendered
    assert "Mathlib.Data.Matrix.Mul" in rendered
    assert "Use ONE of these" in rendered


def test_build_anchor_block_empty_anchors() -> None:
    assert anchors.build_anchor_block([]) == ""


# =====================================================================
# Test 4: premise retrieval — IsClosed query returns isClosed_* first.
# =====================================================================

def test_premise_index_isclosed_query() -> None:
    idx = _mock_premise_index()
    hits = idx.query("theorem foo : IsClosed (s : Set α) := by sorry", top_k=3)
    assert hits, "premise index returned no hits for IsClosed query"
    names = [h.name for h in hits]
    # At least one of the topology lemmas should appear in the top 3.
    assert "isClosed_singleton" in names or "Set.isClosed_eq" in names
    # `add_comm` / `mul_comm` are unrelated and must NOT rank in the top 2.
    top_names = names[:2]
    assert not any(n in {"add_comm", "mul_comm"} for n in top_names)


def test_premise_index_deterministic_ordering() -> None:
    """Two queries with the same goal text must produce identical hit lists.
    Token-overlap is deterministic; ties are broken by name. This guards
    against non-deterministic dict iteration order."""
    idx = _mock_premise_index()
    a = idx.query("theorem foo : IsClosed s := by sorry", top_k=4)
    b = idx.query("theorem foo : IsClosed s := by sorry", top_k=4)
    assert [(h.name, h.score) for h in a] == [(h.name, h.score) for h in b]


def test_premise_index_no_token_overlap_returns_empty() -> None:
    idx = _mock_premise_index()
    # Pure punctuation / numbers → tokenizer yields nothing.
    hits = idx.query("12345 !!!", top_k=3)
    assert hits == []


def test_tokenize_for_premise_camel_and_underscore() -> None:
    toks = anchors.tokenize_for_premise("Matrix.dotProduct isClosed_singleton")
    # Splits on dots, underscores, AND CamelCase.
    assert "matrix" in toks
    assert "dot" in toks
    assert "product" in toks
    assert "closed" in toks
    assert "singleton" in toks
    # Single-letter tokens are filtered out.
    assert all(len(t) >= 3 for t in toks)


def test_premise_block_rendering_truncates() -> None:
    long_stmt = "theorem foo " + "x " * 200
    hits = [anchors.PremiseHit(
        name="long_thm", statement=long_stmt, module="Mathlib.Foo", score=0.9
    )]
    rendered = anchors.build_premise_block(hits)
    assert "long_thm" in rendered
    assert "Mathlib.Foo" in rendered
    # Should be truncated to keep prompt budget under control.
    assert "..." in rendered
    assert len(rendered) < 600


# =====================================================================
# Test 5: end-to-end build_user_prompt with anchors.
# =====================================================================

NEIGHBOUR_SRC = """\
import Mathlib

theorem foo (n : ℕ) : 0 ≤ n := by exact Nat.zero_le n

theorem target (n : ℕ) : n ≤ n + 1 := by sorry
"""


def test_build_user_prompt_includes_anchor_block_on_error() -> None:
    idx = _mock_name_index()
    user = gen.build_user_prompt(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target (n : ℕ) : n ≤ n + 1 := by sorry",
        paper_theory_hint="",
        paper_local_file=NEIGHBOUR_SRC,
        error_tail="error: unknown identifier 'dotProduct'",
        name_index=idx,
        audited_core_hint="",  # disable per-paper loaders to stay hermetic
        latex_proof_hints=[],
    )
    # The anchor block should mention `dotProduct` and its Mathlib module.
    assert "Mathlib-anchor evidence" in user
    assert "dotProduct" in user
    assert "Mathlib.Data.Matrix.Mul" in user
    # The retry block should also be present.
    assert "previous attempt failed" in user.lower()


def test_build_user_prompt_no_anchor_block_without_error() -> None:
    idx = _mock_name_index()
    user = gen.build_user_prompt(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target (n : ℕ) : n ≤ n + 1 := by sorry",
        paper_theory_hint="",
        paper_local_file=NEIGHBOUR_SRC,
        error_tail="",  # no error → no unknown-identifier anchors
        name_index=idx,
        audited_core_hint="",
        latex_proof_hints=[],
    )
    # No anchor block (no error tail and we did not pass a premise index).
    assert "Mathlib-anchor evidence" not in user
    assert "previous attempt failed" not in user.lower()


def test_build_user_prompt_no_anchor_block_when_error_unmatched() -> None:
    """Error tail mentions no `unknown identifier` and no `synthInstanceFailed`
    → no anchor block (even when a name_index is supplied)."""
    idx = _mock_name_index()
    user = gen.build_user_prompt(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target (n : ℕ) : n ≤ n + 1 := by sorry",
        paper_theory_hint="",
        paper_local_file=NEIGHBOUR_SRC,
        error_tail="error: type mismatch at `Nat.le_succ`",
        name_index=idx,
        audited_core_hint="",
        latex_proof_hints=[],
    )
    assert "Mathlib-anchor evidence" not in user
    # But the retry block IS present (we still want the error in the prompt).
    assert "previous attempt failed" in user.lower()


def test_build_user_prompt_premise_block_included_when_index_supplied() -> None:
    """First-attempt prompt (no error tail) but caller provides a premise
    index → goal-similar lemmas appear in the prompt."""
    user = gen.build_user_prompt(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target (s : Set α) : IsClosed s := by sorry",
        paper_theory_hint="",
        paper_local_file=NEIGHBOUR_SRC,
        error_tail="",
        premise_index=_mock_premise_index(),
        audited_core_hint="",
        latex_proof_hints=[],
    )
    assert "PREMISE CANDIDATES" in user
    # At least one of the isClosed_* lemmas should be cited.
    assert "isClosed_singleton" in user or "isClosed_eq" in user


# =====================================================================
# Test 6: generate_proof_candidate honors mocked indices end-to-end.
# =====================================================================

def test_generate_proof_candidate_uses_anchors_in_retry() -> None:
    """The retry path (`error_tail` set) should inject Mathlib anchors into
    the user message. We assert on the mocked Mistral client's `messages`
    payload — no real network call is made."""
    idx = _mock_name_index()
    premise = _mock_premise_index()
    client = FakeClient(_proof_response("exact rfl"))
    out = gen.generate_proof_candidate(
        paper_id="p1",
        theorem_name="target",
        lean_statement="theorem target : IsClosed (s : Set α) := by sorry",
        paper_theory_hint="",
        paper_local_file=NEIGHBOUR_SRC,
        error_tail="error: unknown identifier 'dotProduct'",
        client=client,
        name_index=idx,
        premise_index=premise,
        use_mathlib_anchors=False,  # don't trigger the lazy real-index loader
        audited_core_hint="",
        latex_proof_hints=[],
    )
    assert out is not None
    assert len(client.chat.calls) == 1
    user_content = client.chat.calls[0]["messages"][1]["content"]
    assert "dotProduct" in user_content
    assert "PREMISE CANDIDATES" in user_content


def test_generate_proof_candidate_use_mathlib_anchors_false_skips_lazy_load() -> None:
    """When `use_mathlib_anchors=False` and no explicit indices are passed,
    the prompt MUST NOT trigger the lazy mathlib index load. This keeps unit
    tests fully hermetic on machines without `.lake/packages/mathlib`."""
    client = FakeClient(_proof_response("trivial"))
    out = gen.generate_proof_candidate(
        paper_id="p1",
        theorem_name="t",
        lean_statement="theorem t : True := by sorry",
        paper_theory_hint="",
        paper_local_file="",
        error_tail="error: unknown identifier 'whatever'",
        client=client,
        use_mathlib_anchors=False,
        audited_core_hint="",
        latex_proof_hints=[],
    )
    assert out is not None
    user_content = client.chat.calls[0]["messages"][1]["content"]
    assert "Mathlib-anchor evidence" not in user_content


# =====================================================================
# Test 7: PremiseIndex cache round-trip.
# =====================================================================

def test_premise_index_cache_round_trip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cache = tmp_path / "premise.json"
    entries = _mock_premise_index().entries
    idx_in = anchors.PremiseIndex(entries=entries, mathlib_root="<mock>")
    # Serialize manually using the same schema as build_premise_index.
    cache.write_text(json.dumps({
        "schema_version": "mathlib_premise_index.v1",
        "mathlib_root": "<mock>",
        "entries": [
            {"name": e.name, "statement": e.statement,
             "module": e.module, "tokens": e.tokens}
            for e in idx_in.entries
        ],
    }), encoding="utf-8")
    loaded = anchors.PremiseIndex.from_cache(cache)
    assert loaded is not None
    assert [e.name for e in loaded.entries] == [e.name for e in idx_in.entries]
    # Querying the loaded index returns the same hit order.
    hits = loaded.query("IsClosed s", top_k=2)
    assert hits
    assert hits[0].name in {"isClosed_singleton", "Set.isClosed_eq"}


def test_premise_index_cache_missing_returns_none(tmp_path) -> None:  # type: ignore[no-untyped-def]
    missing = tmp_path / "does_not_exist.json"
    assert anchors.PremiseIndex.from_cache(missing) is None


def test_premise_index_cache_bad_schema_returns_none(tmp_path) -> None:  # type: ignore[no-untyped-def]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": "wrong.v0", "entries": []}),
                   encoding="utf-8")
    assert anchors.PremiseIndex.from_cache(bad) is None
