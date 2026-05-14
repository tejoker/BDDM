"""Hermetic tests for lemma-factor-v2 (binder-preserving decomposition).

The Mistral client is fully mocked; no network calls are made and no lake
invocations are run. All elaboration validators are pure-Python closures.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

import lemma_factor_v2 as lfv2


# --- Mock Mistral client --------------------------------------------------


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
    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict[str, object]] = []

    def complete(self, **kwargs: object) -> _FakeResponse:
        self.calls.append(kwargs)
        return _FakeResponse(self._content)


class FakeClient:
    def __init__(self, content: str) -> None:
        self.chat = _FakeChat(content)


def _factor_response(
    aux_lemmas: list[dict],
    *,
    verdict: str = "FACTOR",
    compose: str = "constructor; apply each aux",
    reasoning: str = "split top-level conjunction",
    confidence: float = 0.8,
) -> str:
    return json.dumps(
        {
            "verdict": verdict,
            "aux_lemmas": aux_lemmas,
            "compose_strategy": compose,
            "reasoning": reasoning,
            "confidence": confidence,
        }
    )


# --- split_parent_statement -----------------------------------------------


def test_split_parent_statement_explicit_binders() -> None:
    src = (
        "theorem foo (n m : ℕ) (h : n ≤ m) : n + 0 = n ∧ 0 ≤ m := by sorry"
    )
    name, binders, target = lfv2.split_parent_statement(src)
    assert name == "foo"
    assert "(n m : ℕ)" in binders
    assert "(h : n ≤ m)" in binders
    assert target.startswith("n + 0 = n")
    assert "∧ 0 ≤ m" in target


def test_split_parent_statement_implicit_binders() -> None:
    src = (
        "theorem bar {α : Type*} [DecidableEq α] (a b : α) (h : a = b) : "
        "b = a := by sorry"
    )
    name, binders, target = lfv2.split_parent_statement(src)
    assert name == "bar"
    assert "{α : Type*}" in binders
    assert "[DecidableEq α]" in binders
    assert target.strip() == "b = a"


def test_split_parent_statement_multi_line() -> None:
    src = (
        "theorem complex (alpha s1 s2 theta : ℝ)\n"
        "    (h1 : 0 < s1) (h2 : s1 < s2)\n"
        "    (h3 : 0 < theta ∧ theta < 1) :\n"
        "  ∃ eps : ℝ, 0 < eps ∧ s2 < 4 * alpha - 3 - (3/2) * theta - eps "
        ":= by sorry"
    )
    name, binders, target = lfv2.split_parent_statement(src)
    assert name == "complex"
    assert "(alpha s1 s2 theta : ℝ)" in binders
    assert "(h1 : 0 < s1)" in binders
    assert "(h3 : 0 < theta ∧ theta < 1)" in binders
    assert target.startswith("∃ eps : ℝ")


def test_split_parent_statement_malformed_returns_fallback() -> None:
    name, binders, target = lfv2.split_parent_statement("not a theorem at all")
    assert name == ""
    assert binders == ""
    assert target == "not a theorem at all"


# --- detect_target_shape --------------------------------------------------


def test_detect_target_shape_conjunction() -> None:
    assert lfv2.detect_target_shape("0 ≤ n ∧ n ≤ n + 1") == "and"


def test_detect_target_shape_existential() -> None:
    assert lfv2.detect_target_shape("∃ x : ℝ, 0 < x ∧ x < 1") == "exists"


def test_detect_target_shape_iff() -> None:
    assert lfv2.detect_target_shape("P ↔ Q") == "iff"


def test_detect_target_shape_other() -> None:
    assert lfv2.detect_target_shape("n + 0 = n") == "other"


# --- render_composition_attempts ------------------------------------------


def test_render_composition_and_two_aux() -> None:
    out = lfv2.render_composition_attempts(
        parent_target_shape="and",
        aux_names=["a1", "a2"],
    )
    assert len(out) >= 2
    # Tuple constructor must appear.
    assert any("⟨a1, a2⟩" in c for c in out)
    # `constructor` shape MUST appear too.
    assert any("constructor" in c for c in out)


def test_render_composition_exists_two_aux() -> None:
    out = lfv2.render_composition_attempts(
        parent_target_shape="exists",
        aux_names=["witness_pos", "bound_holds"],
    )
    # Existential composition: at minimum a tuple construction with
    # both names in order.
    assert any("⟨witness_pos, bound_holds⟩" in c for c in out)


def test_render_composition_empty_returns_empty() -> None:
    assert lfv2.render_composition_attempts(
        parent_target_shape="and", aux_names=[]
    ) == []


# --- extract_exported_symbols ---------------------------------------------


def test_extract_exported_symbols(tmp_path: Path) -> None:
    p = tmp_path / "Paper_test.lean"
    p.write_text(
        "import Mathlib\n"
        "namespace Paper_test\n"
        "def foo : ℕ := 0\n"
        "def bar : ℕ := 1\n"
        "export Paper_test (foo bar)\n"
        "end Paper_test\n",
        encoding="utf-8",
    )
    syms = lfv2.extract_exported_symbols(p)
    assert "foo" in syms
    assert "bar" in syms


def test_extract_exported_symbols_multi_line(tmp_path: Path) -> None:
    p = tmp_path / "Paper_test.lean"
    p.write_text(
        "namespace Paper_test\n"
        "export Paper_test (alpha beta\n"
        "                   gamma delta)\n"
        "end Paper_test\n",
        encoding="utf-8",
    )
    syms = lfv2.extract_exported_symbols(p)
    assert "alpha" in syms
    assert "delta" in syms


def test_extract_exported_symbols_missing_file_returns_empty(tmp_path: Path) -> None:
    assert lfv2.extract_exported_symbols(tmp_path / "nope.lean") == ""


# --- factor_long_theorem_v2 happy path -----------------------------------


def test_factor_v2_three_aux_all_elaborate() -> None:
    aux = [
        {
            "aux_name": "thm_aux_1",
            "aux_signature": (
                "theorem thm_aux_1 (n m : ℕ) (h : n ≤ m) : 0 ≤ n := by sorry"
            ),
            "compose_hint": "first conjunct",
        },
        {
            "aux_name": "thm_aux_2",
            "aux_signature": (
                "theorem thm_aux_2 (n m : ℕ) (h : n ≤ m) : n ≤ m := by sorry"
            ),
            "compose_hint": "second conjunct",
        },
    ]
    client = FakeClient(_factor_response(aux))

    def validate(_decl: str) -> tuple[bool, str]:
        return True, ""

    out = lfv2.factor_long_theorem_v2(
        paper_id="p1",
        theorem_name="thm",
        lean_statement=(
            "theorem thm (n m : ℕ) (h : n ≤ m) : 0 ≤ n ∧ n ≤ m := by sorry"
        ),
        paper_theory_hint="",
        exported_symbols="",
        client=client,
        validate_elaboration=validate,
    )
    assert len(out) == 2
    assert all(r["rejected"] == [] for r in out)
    assert all(r["elaboration_ok"] is True for r in out)
    assert all(r["protocol"] == "lemma_factor_v2" for r in out)
    # Binder block is captured.
    assert "(n m : ℕ)" in out[0]["parent_binder_block"]
    # Target shape detected as `and`.
    assert out[0]["parent_target_shape"] == "and"


# --- v2 forbidden-token-in-target rejection ------------------------------


def test_factor_v2_rejects_aux_with_false_target() -> None:
    """`: False` is rejected by the trivial-target pattern set."""
    aux = [
        {
            "aux_name": "bad",
            "aux_signature": "theorem bad (n : ℕ) : False := by sorry",
            "compose_hint": "trivial false",
        },
        {
            "aux_name": "good",
            "aux_signature": "theorem good (n : ℕ) : 0 ≤ n := by sorry",
            "compose_hint": "non-trivial",
        },
    ]
    client = FakeClient(_factor_response(aux))
    out = lfv2.factor_long_theorem_v2(
        paper_id="p",
        theorem_name="t",
        lean_statement="theorem t (n : ℕ) : 0 ≤ n := by sorry",
        paper_theory_hint="",
        exported_symbols="",
        client=client,
    )
    # `bad` aux has `: False` target — must be flagged (trivial-target).
    bad = [r for r in out if r["aux_name"] == "bad"]
    good = [r for r in out if r["aux_name"] == "good"]
    assert bad and bad[0]["rejected"]
    assert good and good[0]["rejected"] == []


def test_factor_v2_rejects_aux_with_bare_axiom_keyword() -> None:
    """A bare `axiom` keyword in the target (as a token) is rejected."""
    aux = [
        {
            "aux_name": "uses_axiom",
            # The LLM might try to weave the word `axiom` as a standalone
            # token — we reject regardless of whether it's syntactically
            # meaningful, because forbidden tokens are policy-blocked.
            "aux_signature": "theorem uses_axiom : 0 = axiom := by sorry",
            "compose_hint": "uses axiom keyword",
        },
    ]
    client = FakeClient(_factor_response(aux))
    out = lfv2.factor_long_theorem_v2(
        paper_id="p",
        theorem_name="t",
        lean_statement="theorem t (n : ℕ) : 0 ≤ n := by sorry",
        paper_theory_hint="",
        exported_symbols="",
        client=client,
    )
    assert len(out) == 1
    assert any("forbidden_token_in_target" in flag for flag in out[0]["rejected"])


def test_factor_v2_rejects_aux_with_paperclaim_target() -> None:
    aux = [
        {
            "aux_name": "trivial_claim",
            "aux_signature": "theorem trivial_claim : PaperClaim := by sorry",
            "compose_hint": "trivial",
        },
    ]
    client = FakeClient(_factor_response(aux))
    out = lfv2.factor_long_theorem_v2(
        paper_id="p",
        theorem_name="t",
        lean_statement="theorem t : True := by sorry",
        paper_theory_hint="",
        exported_symbols="",
        client=client,
    )
    assert len(out) == 1
    assert out[0]["rejected"]


# --- v2 missing client / empty input gates -------------------------------


def test_factor_v2_missing_client_returns_empty() -> None:
    out = lfv2.factor_long_theorem_v2(
        paper_id="p",
        theorem_name="t",
        lean_statement="theorem t (n : ℕ) : 0 ≤ n := by sorry",
        paper_theory_hint="",
        exported_symbols="",
        client=None,
    )
    assert out == []


def test_factor_v2_empty_statement_returns_empty() -> None:
    client = FakeClient(_factor_response([]))
    out = lfv2.factor_long_theorem_v2(
        paper_id="p",
        theorem_name="t",
        lean_statement="   ",
        paper_theory_hint="",
        exported_symbols="",
        client=client,
    )
    assert out == []
    # The gate should fire BEFORE the LLM call.
    assert client.chat.calls == []


def test_factor_v2_refuse_verdict_returns_empty() -> None:
    client = FakeClient(_factor_response([], verdict="REFUSE"))
    out = lfv2.factor_long_theorem_v2(
        paper_id="p",
        theorem_name="t",
        lean_statement="theorem t : 1 = 1 := by sorry",
        paper_theory_hint="",
        exported_symbols="",
        client=client,
    )
    assert out == []


def test_factor_v2_transport_error_returns_empty() -> None:
    class _RaisingChat:
        def complete(self, **_: object) -> object:
            raise RuntimeError("simulated transport failure")

    client = types.SimpleNamespace(chat=_RaisingChat())
    out = lfv2.factor_long_theorem_v2(
        paper_id="p",
        theorem_name="t",
        lean_statement="theorem t (n : ℕ) : 0 ≤ n := by sorry",
        paper_theory_hint="",
        exported_symbols="",
        client=client,
    )
    assert out == []


# --- Prompt includes binder block + exports + examples -------------------


def test_v2_prompt_includes_binder_block_and_exports() -> None:
    prompt = lfv2.build_user_prompt(
        paper_id="p",
        theorem_name="thm_xyz",
        lean_statement=(
            "theorem thm_xyz (a b : ℝ) (hab : a < b) : a ≤ b := by sorry"
        ),
        paper_theory_hint="def f : ℝ → ℝ := id",
        exported_symbols="f g h",
    )
    assert "(a b : ℝ)" in prompt
    assert "(hab : a < b)" in prompt
    assert "f g h" in prompt
    assert "def f : ℝ → ℝ := id" in prompt


def test_v2_system_prompt_includes_in_context_examples() -> None:
    # The two curated examples must be present in the system prompt.
    sys_prompt = lfv2.SYSTEM_PROMPT_V2
    assert "remark_20_param_roles" in sys_prompt
    assert "admissible_intro_split" in sys_prompt
    # Each example shows aux lemmas that repeat the parent binder block.
    assert "0 < theta_val ∧ theta_val < 1" in sys_prompt


# --- JSONL writer round-trip ---------------------------------------------


def test_write_lemma_factor_v2_jsonl_round_trips(tmp_path: Path) -> None:
    aux = [
        {
            "aux_name": "a",
            "aux_signature": "theorem a (n : ℕ) : 0 ≤ n := by sorry",
            "compose_hint": "c1",
        },
        {
            "aux_name": "b",
            "aux_signature": "theorem b (n : ℕ) : n + 0 = n := by sorry",
            "compose_hint": "c2",
        },
    ]
    client = FakeClient(_factor_response(aux))
    out = lfv2.factor_long_theorem_v2(
        paper_id="2604.21583",
        theorem_name="thm_parent",
        lean_statement="theorem thm_parent (n : ℕ) : 0 ≤ n ∧ n + 0 = n := by sorry",
        paper_theory_hint="",
        exported_symbols="",
        client=client,
    )
    assert len(out) == 2
    target = tmp_path / "lemma_factor_v2_candidates.jsonl"
    written = lfv2.write_lemma_factor_v2_jsonl(
        candidates=out, output_path=target, append=False
    )
    assert written == 2
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 2
    assert all(r["row_id"].startswith("2604.21583::thm_parent::") for r in rows)
    assert all(r["protocol"] == "lemma_factor_v2" for r in rows)
