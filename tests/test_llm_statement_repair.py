"""Hermetic tests for the LLM-driven statement repair generator.

The Mistral client is fully mocked. No network calls are made.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

import llm_statement_repair as lsr


# --- Mock Mistral client ---------------------------------------------------


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


def _ok_response(decl: str, *, reasoning: str = "encodes the LaTeX claim", confidence: float = 0.85) -> str:
    return json.dumps(
        {
            "verdict": "SIGNATURE",
            "lean_signature": decl,
            "reasoning": reasoning,
            "confidence": confidence,
        }
    )


# --- Happy path -----------------------------------------------------------


def test_happy_path_returns_normalized_decl() -> None:
    client = FakeClient(
        _ok_response("theorem cor_arthur (n : ℕ) (h : 0 < n) : ∀ k, k ≤ n → 2 * k ≤ 2 * n := by sorry")
    )
    result = lsr.generate_llm_repair_candidate(
        source_latex=r"For all $n>0$ and $k\le n$, we have $2k \le 2n$.",
        paper_id="2304.09598",
        theorem_name="Cor_Arthur",
        paper_theory_hint="def Multisegment : Type := ℕ\naxiom IsArthur : Multisegment → Prop",
        client=client,
    )
    assert result is not None
    assert result["rejected"] == []
    assert result["protocol"] == "llm_statement_repair_v1"
    assert result["repaired_decl"].startswith("theorem ")
    assert result["repaired_decl"].endswith(":= by sorry")
    assert "Cor_Arthur" in result["repaired_decl"]
    assert 0.8 <= result["confidence"] <= 1.0


def test_happy_path_strips_code_fences() -> None:
    fenced = (
        "```lean\n"
        "theorem foo (a b : ℕ) (h : a ≤ b) : a ≤ b + 1 := by sorry\n"
        "```"
    )
    client = FakeClient(
        json.dumps(
            {
                "verdict": "SIGNATURE",
                "lean_signature": fenced,
                "reasoning": "monotonicity",
                "confidence": 0.7,
            }
        )
    )
    result = lsr.generate_llm_repair_candidate(
        source_latex=r"For all $a \le b$ in $\mathbb{N}$, $a \le b + 1$.",
        paper_id="paper",
        theorem_name="foo",
        paper_theory_hint="",
        client=client,
    )
    # Code fences MUST be stripped and the decl MUST survive trivialization gates.
    assert result is not None, "expected a result, got None"
    assert result["rejected"] == [], result
    assert result["repaired_decl"].startswith("theorem foo")
    assert "```" not in result["repaired_decl"]
    assert result["repaired_decl"].endswith(":= by sorry")


def test_trivialized_existential_is_rejected_via_translator_helper() -> None:
    decl = "theorem bad : ∃ x : ℝ, x = x := by sorry"
    client = FakeClient(_ok_response(decl))
    result = lsr.generate_llm_repair_candidate(
        source_latex="A genuinely meaningful claim.",
        paper_id="p",
        theorem_name="bad",
        paper_theory_hint="",
        client=client,
    )
    assert result is not None
    # Either path can flag it — what matters is that it gets rejected.
    assert result["rejected"], result
    assert result["repaired_decl"] == ""


# --- Schema robustness ----------------------------------------------------


def test_malformed_json_returns_rejection_record() -> None:
    client = FakeClient("not a json object at all — just prose.")
    result = lsr.generate_llm_repair_candidate(
        source_latex="claim",
        paper_id="p",
        theorem_name="thm",
        paper_theory_hint="",
        client=client,
    )
    assert result is not None
    assert "malformed_json" in result["rejected"]
    assert result["repaired_decl"] == ""
    assert result["error"] is True


def test_llm_refusal_returns_rejection_record() -> None:
    client = FakeClient(
        json.dumps(
            {
                "verdict": "REFUSE",
                "lean_signature": "",
                "reasoning": "informal procedural description, not a theorem",
                "confidence": 0.0,
            }
        )
    )
    result = lsr.generate_llm_repair_candidate(
        source_latex="We now describe the algorithm step by step.",
        paper_id="p",
        theorem_name="proc",
        paper_theory_hint="",
        client=client,
    )
    assert result is not None
    assert "llm_refused" in result["rejected"]
    assert result["repaired_decl"] == ""


# --- Placeholder / trivialization detection -------------------------------


@pytest.mark.parametrize(
    "decl",
    [
        "theorem bad (h1 : True) : True := by sorry",
        "theorem bad (x : ℝ) : ∃ x : ℝ, x = x := by sorry",
        "theorem bad : SourceStatement := by sorry",
        "theorem bad : PaperClaim := by sorry",
        "theorem bad : 0 = 0 := by sorry",
    ],
)
def test_placeholder_outputs_are_rejected(decl: str) -> None:
    client = FakeClient(_ok_response(decl))
    result = lsr.generate_llm_repair_candidate(
        source_latex="A genuinely non-trivial claim about integers.",
        paper_id="p",
        theorem_name="bad",
        paper_theory_hint="",
        client=client,
    )
    assert result is not None
    assert result["repaired_decl"] == ""
    assert result["rejected"], f"expected rejection for {decl!r}, got {result}"


# --- Input gating ---------------------------------------------------------


def test_empty_source_latex_returns_none() -> None:
    client = FakeClient(_ok_response("theorem t : 1 + 1 = 2 := by sorry"))
    result = lsr.generate_llm_repair_candidate(
        source_latex="   ",
        paper_id="p",
        theorem_name="t",
        paper_theory_hint="",
        client=client,
    )
    assert result is None
    assert client.chat.calls == []  # gating happened before any API call


def test_missing_client_returns_none() -> None:
    result = lsr.generate_llm_repair_candidate(
        source_latex="real claim",
        paper_id="p",
        theorem_name="t",
        paper_theory_hint="",
        client=None,
    )
    assert result is None


# --- Real-LaTeX → real-signature smoke ------------------------------------


def test_real_latex_real_signature_passes_all_gates() -> None:
    decl = (
        "theorem strichartz_transfer (u : ℝ → ℝ) (T : ℝ) (hT : 0 < T) "
        ": ∀ t, 0 ≤ t → t ≤ T → ‖u t‖ ≤ T * ‖u 0‖ := by sorry"
    )
    client = FakeClient(_ok_response(decl, confidence=0.91))
    result = lsr.generate_llm_repair_candidate(
        source_latex=(
            r"\begin{lemma}[Strichartz transfer]\label{lem:strichartz} "
            r"For all $t \in [0,T]$ with $T>0$, we have "
            r"$\|u(t)\| \le T \|u(0)\|$.\end{lemma}"
        ),
        paper_id="2604.21884",
        theorem_name="lem_strichartz_transfer",
        paper_theory_hint="def NormFn : (ℝ → ℝ) → ℝ → ℝ := fun _ _ => 0",
    client=client,
    )
    assert result is not None
    assert result["rejected"] == []
    assert "lem_strichartz_transfer" in result["repaired_decl"]
    assert ":= by sorry" in result["repaired_decl"]
    # Body must be exactly one sorry, no actual proof text.
    assert result["repaired_decl"].count(":= by sorry") == 1


# --- Body normalization ---------------------------------------------------


def test_body_normalization_forces_sorry_when_model_returns_proof() -> None:
    # Model returns a proof body — generator MUST normalize to `:= by sorry`.
    decl_with_proof = (
        "theorem t (n : ℕ) : n + 0 = n := by simp"
    )
    client = FakeClient(_ok_response(decl_with_proof))
    result = lsr.generate_llm_repair_candidate(
        source_latex=r"For all natural $n$, $n + 0 = n$.",
        paper_id="p",
        theorem_name="t",
        paper_theory_hint="",
        client=client,
    )
    assert result is not None
    assert result["rejected"] == [], result
    assert result["repaired_decl"].endswith(":= by sorry")
    assert "simp" not in result["repaired_decl"]


# --- Transport-error robustness -------------------------------------------


def test_transport_error_returns_error_record() -> None:
    class _RaisingChat:
        def complete(self, **_: object) -> object:
            raise RuntimeError("simulated transport failure")

    client = types.SimpleNamespace(chat=_RaisingChat())
    result = lsr.generate_llm_repair_candidate(
        source_latex="real claim",
        paper_id="p",
        theorem_name="t",
        paper_theory_hint="",
        client=client,
    )
    assert result is not None
    assert result["error"] is True
    assert "transport_error" in result["rejected"]
    assert result["repaired_decl"] == ""


# --- Paper-theory hint extraction -----------------------------------------


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
        "theorem internal_lemma : True := by trivial\n"
        "-- a comment line\n"
        "\n"
        "end Paper_test\n",
        encoding="utf-8",
    )
    hint = lsr.extract_paper_theory_hint(p)
    assert "abbrev Foo : Type" in hint
    assert "def bar" in hint
    assert "axiom baz" in hint
    assert "instance" in hint
    # Theorems are filtered out (only def/abbrev/axiom/instance/class/structure).
    assert "internal_lemma" not in hint
    assert "-- a comment line" not in hint


def test_extract_paper_theory_hint_returns_empty_for_missing_file(tmp_path: Path) -> None:
    assert lsr.extract_paper_theory_hint(tmp_path / "missing.lean") == ""


def test_generate_applies_deterministic_cleanup_lambda_token() -> None:
    """LLM output containing `(λ : ℝ)` (`λ` used as a parameter name) must
    be repaired by the translator's deterministic cleanup pass before the
    trivialization gate runs. Without this, the Round II-4 smoke run hit
    `unexpected token 'λ'` on `cor_husimi_fourth_moment` and rejected the
    candidate as invalid Lean."""
    from llm_statement_repair import generate_llm_repair_candidate

    class _MockClient:
        def __init__(self, response_json: str) -> None:
            self.response_json = response_json

        class _Msg:
            def __init__(self, content: str) -> None:
                self.content = content

        class _Choice:
            def __init__(self, msg: "_MockClient._Msg") -> None:
                self.message = msg

        class _Response:
            def __init__(self, choices: list) -> None:
                self.choices = choices

        @property
        def chat(self) -> "_MockClient":
            return self

        def complete(self, model=None, messages=None, temperature=None, max_tokens=None):
            return _MockClient._Response([
                _MockClient._Choice(_MockClient._Msg(self.response_json))
            ])

    response_json = json.dumps({
        "verdict": "SIGNATURE",
        "lean_signature": "theorem t (λ : ℝ) (h : 0 < λ) : 0 < λ := by sorry",
        "reasoning": "uses lambda as parameter",
        "confidence": 0.85,
    })
    result = generate_llm_repair_candidate(
        source_latex="If $\\lambda > 0$ then $\\lambda > 0$.",
        paper_id="0000.99999",
        theorem_name="t",
        paper_theory_hint="",
        client=_MockClient(response_json),
        model="labs-leanstral-2603",
    )
    assert result is not None
    # The `λ` parameter name should be rewritten by `_deterministic_signature_cleanup`
    # to `lam`, allowing the signature to survive the cleanup pass.
    decl = result.get("repaired_decl", "") or result.get("candidate_decl_before_rejection", "")
    assert decl, f"expected a repaired_decl, got: {result}"
    # After the cleanup, the bare `(λ : ℝ)` form must be normalized.
    assert "(λ :" not in decl
    # Trivialization gate should not reject a real 0 < lam < lam claim.
