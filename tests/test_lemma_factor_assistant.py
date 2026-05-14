"""Hermetic tests for the LLM-driven lemma factoring assistant.

The Mistral client is fully mocked; no network calls are made. The
elaboration validator is a pure-Python closure when relevant."""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

import lemma_factor_assistant as lfa


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


# --- Happy path: 3 aux returned, all elaborate -----------------------------


def test_three_aux_all_elaborate() -> None:
    aux = [
        {
            "aux_name": "thm_aux_1",
            "aux_signature": "theorem thm_aux_1 (n : ℕ) : 0 ≤ n := by sorry",
            "compose_hint": "first conjunct",
        },
        {
            "aux_name": "thm_aux_2",
            "aux_signature": "theorem thm_aux_2 (n : ℕ) : n ≤ n + 1 := by sorry",
            "compose_hint": "second conjunct",
        },
        {
            "aux_name": "thm_aux_3",
            "aux_signature": "theorem thm_aux_3 (n : ℕ) : n + 0 = n := by sorry",
            "compose_hint": "third conjunct",
        },
    ]
    client = FakeClient(_factor_response(aux))

    def validate(_decl: str) -> tuple[bool, str]:
        return True, ""

    out = lfa.factor_long_theorem(
        paper_id="p1",
        theorem_name="thm",
        lean_statement=(
            "theorem thm (n : ℕ) : 0 ≤ n ∧ n ≤ n + 1 ∧ n + 0 = n := by sorry"
        ),
        paper_theory_hint="",
        client=client,
        validate_elaboration=validate,
    )
    assert len(out) == 3
    assert all(r["rejected"] == [] for r in out)
    assert all(r["elaboration_ok"] is True for r in out)
    assert {r["aux_name"] for r in out} == {"thm_aux_1", "thm_aux_2", "thm_aux_3"}


# --- 3 aux proposed, only 2 elaborate -------------------------------------


def test_two_of_three_aux_elaborate() -> None:
    aux = [
        {
            "aux_name": "t_aux_1",
            "aux_signature": "theorem t_aux_1 (n : ℕ) : 0 ≤ n := by sorry",
            "compose_hint": "a",
        },
        {
            "aux_name": "t_aux_2",
            "aux_signature": "theorem t_aux_2 (n : ℕ) : ThisDoesNotExist n := by sorry",
            "compose_hint": "b",
        },
        {
            "aux_name": "t_aux_3",
            "aux_signature": "theorem t_aux_3 (n : ℕ) : n + 0 = n := by sorry",
            "compose_hint": "c",
        },
    ]
    client = FakeClient(_factor_response(aux))

    def validate(decl: str) -> tuple[bool, str]:
        if "ThisDoesNotExist" in decl:
            return False, "unknown identifier 'ThisDoesNotExist'"
        return True, ""

    out = lfa.factor_long_theorem(
        paper_id="p1",
        theorem_name="t",
        lean_statement="theorem t (n : ℕ) : True := by sorry",
        paper_theory_hint="",
        client=client,
        validate_elaboration=validate,
    )
    kept = [r for r in out if not r["rejected"]]
    rejected = [r for r in out if r["rejected"]]
    assert len(kept) == 2
    assert len(rejected) == 1
    assert rejected[0]["aux_name"] == "t_aux_2"
    assert "elaboration_gate" in rejected[0]["rejected"]
    assert rejected[0]["elaboration_error"]


# --- Empty LLM response should not crash ----------------------------------


def test_empty_llm_response_returns_empty_list() -> None:
    client = FakeClient("")
    out = lfa.factor_long_theorem(
        paper_id="p",
        theorem_name="thm",
        lean_statement="theorem thm : True := by sorry",
        paper_theory_hint="",
        client=client,
    )
    assert out == []


def test_malformed_json_returns_empty_list() -> None:
    client = FakeClient("not a json object at all — pure prose.")
    out = lfa.factor_long_theorem(
        paper_id="p",
        theorem_name="thm",
        lean_statement="theorem thm : True := by sorry",
        paper_theory_hint="",
        client=client,
    )
    assert out == []


def test_refuse_verdict_returns_empty_list() -> None:
    client = FakeClient(_factor_response([], verdict="REFUSE"))
    out = lfa.factor_long_theorem(
        paper_id="p",
        theorem_name="thm",
        lean_statement="theorem thm : 1 = 1 := by sorry",
        paper_theory_hint="",
        client=client,
    )
    assert out == []


# --- Trivial aux is rejected via translator helper ------------------------


def test_trivial_true_body_aux_is_rejected() -> None:
    aux = [
        {
            "aux_name": "bad_aux_1",
            "aux_signature": "theorem bad_aux_1 (h : True) : True := by sorry",
            "compose_hint": "trivial",
        },
        {
            "aux_name": "good_aux_1",
            "aux_signature": "theorem good_aux_1 (n : ℕ) : 0 ≤ n := by sorry",
            "compose_hint": "ok",
        },
    ]
    client = FakeClient(_factor_response(aux))
    out = lfa.factor_long_theorem(
        paper_id="p",
        theorem_name="orig",
        lean_statement="theorem orig (n : ℕ) : 0 ≤ n := by sorry",
        paper_theory_hint="",
        client=client,
    )
    # Bad aux must be flagged as rejected (placeholder/trivialization), good kept.
    bad = [r for r in out if r["aux_name"] == "bad_aux_1"]
    good = [r for r in out if r["aux_name"] == "good_aux_1"]
    assert bad, out
    assert bad[0]["rejected"], bad[0]
    assert good, out
    assert good[0]["rejected"] == []


def test_existential_self_equality_aux_is_rejected() -> None:
    aux = [
        {
            "aux_name": "self_eq",
            "aux_signature": "theorem self_eq : ∃ x : ℝ, x = x := by sorry",
            "compose_hint": "trivial existential",
        },
    ]
    client = FakeClient(_factor_response(aux))
    out = lfa.factor_long_theorem(
        paper_id="p",
        theorem_name="orig",
        lean_statement="theorem orig : True := by sorry",
        paper_theory_hint="",
        client=client,
    )
    assert len(out) == 1
    assert out[0]["rejected"], out[0]
    # Either placeholder or trivialized.
    assert any(
        flag in out[0]["rejected"]
        for flag in ("placeholder_pattern_detected", "trivialized_signature")
    )


# --- Input gating --------------------------------------------------------


def test_empty_lean_statement_returns_empty_list() -> None:
    client = FakeClient(
        _factor_response(
            [
                {
                    "aux_name": "x",
                    "aux_signature": "theorem x : True := by sorry",
                    "compose_hint": "",
                }
            ]
        )
    )
    out = lfa.factor_long_theorem(
        paper_id="p",
        theorem_name="t",
        lean_statement="    ",
        paper_theory_hint="",
        client=client,
    )
    assert out == []
    assert client.chat.calls == []  # gate fired before the LLM call


def test_missing_client_returns_empty_list() -> None:
    out = lfa.factor_long_theorem(
        paper_id="p",
        theorem_name="t",
        lean_statement="theorem t : True := by sorry",
        paper_theory_hint="",
        client=None,
    )
    assert out == []


# --- Name sanitization & body normalization -------------------------------


def test_aux_name_is_sanitized_to_valid_identifier() -> None:
    aux = [
        {
            "aux_name": "weird name!!@@",
            "aux_signature": "theorem ignored (n : ℕ) : 0 ≤ n := by sorry",
            "compose_hint": "ok",
        },
    ]
    client = FakeClient(_factor_response(aux))
    out = lfa.factor_long_theorem(
        paper_id="p",
        theorem_name="orig",
        lean_statement="theorem orig (n : ℕ) : 0 ≤ n := by sorry",
        paper_theory_hint="",
        client=client,
    )
    assert len(out) == 1
    # Sanitized name has no special characters and is a valid Lean identifier.
    nm = out[0]["aux_name"]
    assert nm
    assert all(ch.isalnum() or ch in "_'" for ch in nm)
    # The aux_signature should carry the sanitized name (rewritten by normalize).
    assert f"theorem {nm}" in out[0]["aux_signature"]


def test_aux_body_is_normalized_to_sorry_when_model_returns_proof() -> None:
    aux = [
        {
            "aux_name": "aux_with_proof",
            "aux_signature": "theorem aux_with_proof (n : ℕ) : n + 0 = n := by simp",
            "compose_hint": "monotonicity",
        },
    ]
    client = FakeClient(_factor_response(aux))
    out = lfa.factor_long_theorem(
        paper_id="p",
        theorem_name="orig",
        lean_statement="theorem orig (n : ℕ) : n + 0 = n := by sorry",
        paper_theory_hint="",
        client=client,
    )
    assert len(out) == 1
    assert out[0]["aux_signature"].endswith(":= by sorry")
    assert "simp" not in out[0]["aux_signature"]


# --- Transport error robustness ------------------------------------------


def test_transport_error_returns_empty_list() -> None:
    class _RaisingChat:
        def complete(self, **_: object) -> object:
            raise RuntimeError("simulated transport failure")

    client = types.SimpleNamespace(chat=_RaisingChat())
    out = lfa.factor_long_theorem(
        paper_id="p",
        theorem_name="t",
        lean_statement="theorem t (n : ℕ) : 0 ≤ n := by sorry",
        paper_theory_hint="",
        client=client,
    )
    assert out == []


# --- JSONL writer ---------------------------------------------------------


def test_write_lemma_factor_jsonl_round_trips(tmp_path: Path) -> None:
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
    out = lfa.factor_long_theorem(
        paper_id="2604.21583",
        theorem_name="thm_parent",
        lean_statement="theorem thm_parent (n : ℕ) : 0 ≤ n ∧ n + 0 = n := by sorry",
        paper_theory_hint="",
        client=client,
    )
    assert len(out) == 2
    target = tmp_path / "lemma_factor_candidates.jsonl"
    written = lfa.write_lemma_factor_jsonl(
        candidates=out, output_path=target, append=False
    )
    assert written == 2
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 2
    assert all(r["row_id"].startswith("2604.21583::thm_parent::") for r in rows)
    assert all("aux_signature" in r and r["aux_signature"].endswith(":= by sorry") for r in rows)


# --- Paper-theory hint extraction ----------------------------------------


def test_extract_paper_theory_hint_filters_to_signature_lines(tmp_path: Path) -> None:
    p = tmp_path / "Paper_test.lean"
    p.write_text(
        "import Mathlib\n"
        "namespace Paper_test\n"
        "\n"
        "abbrev Foo : Type := ℕ\n"
        "def bar (x : ℕ) : ℕ := x + 1\n"
        "axiom baz : ∀ n, n + 0 = n\n"
        "instance : Inhabited Foo := ⟨0⟩\n"
        "class HasMagic (a : Type) : Type where\n"
        "  zap : a\n"
        "theorem internal : True := by trivial\n"
        "end Paper_test\n",
        encoding="utf-8",
    )
    hint = lfa.extract_paper_theory_hint(p)
    assert "abbrev Foo" in hint
    assert "def bar" in hint
    assert "axiom baz" in hint
    assert "instance" in hint
    assert "class HasMagic" in hint
    assert "internal" not in hint


def test_extract_paper_theory_hint_missing_file_returns_empty(tmp_path: Path) -> None:
    assert lfa.extract_paper_theory_hint(tmp_path / "nope.lean") == ""


# --- min_aux floor: returns audit list when below threshold ---------------


def test_min_aux_floor_returns_audit_list_when_below_threshold() -> None:
    # min_aux=3 but only 2 aux survive — generator returns ALL (including
    # rejected so caller can audit), but caller can compute surviving<min_aux.
    aux = [
        {
            "aux_name": "a",
            "aux_signature": "theorem a (n : ℕ) : 0 ≤ n := by sorry",
            "compose_hint": "ok",
        },
        {
            "aux_name": "b",
            "aux_signature": "theorem b (n : ℕ) : n + 0 = n := by sorry",
            "compose_hint": "ok",
        },
    ]
    client = FakeClient(_factor_response(aux))
    out = lfa.factor_long_theorem(
        paper_id="p",
        theorem_name="t",
        lean_statement="theorem t (n : ℕ) : 0 ≤ n ∧ n + 0 = n := by sorry",
        paper_theory_hint="",
        client=client,
        min_aux=3,
    )
    # Both kept (no rejection), but caller knows the surviving count < min_aux.
    surviving = [r for r in out if not r["rejected"]]
    assert len(surviving) == 2  # less than min_aux=3
    assert len(out) == 2


# --- Non-dict aux entries are skipped ------------------------------------


def test_non_dict_aux_entries_are_skipped() -> None:
    # Pass a list whose first entry is invalid (string); the second valid one
    # must still be returned.
    raw = json.dumps(
        {
            "verdict": "FACTOR",
            "aux_lemmas": [
                "this is a string, not a dict",
                {
                    "aux_name": "good_a",
                    "aux_signature": "theorem good_a (n : ℕ) : 0 ≤ n := by sorry",
                    "compose_hint": "ok",
                },
            ],
            "compose_strategy": "",
            "reasoning": "",
            "confidence": 0.5,
        }
    )
    client = FakeClient(raw)
    out = lfa.factor_long_theorem(
        paper_id="p",
        theorem_name="t",
        lean_statement="theorem t (n : ℕ) : 0 ≤ n := by sorry",
        paper_theory_hint="",
        client=client,
    )
    assert len(out) == 1
    assert out[0]["aux_name"] == "good_a"
    assert out[0]["rejected"] == []
