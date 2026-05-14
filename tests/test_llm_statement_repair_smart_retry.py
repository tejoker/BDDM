"""Hermetic tests for the smart-retry prompt enhancement in `llm_statement_repair`.

The retry loop landed in commit `ee5dcea` got a 0% rescue rate in smoke testing
because the LLM repeated the same structural mistake across rounds when shown
only the raw Lean error tail. The smart-retry layer extracts (identifier,
error_kind) anchors from the error tail and surfaces matching paper-theory
entries verbatim into the follow-up prompt.

These tests cover:
  1. `_extract_lean_error_anchors` correctly parses each supported error kind.
  2. The anchor matcher finds paper-theory entries when the symbol is in the
     hint, and reports "no matching paper-theory entry" when it's missing.
  3. The smart-retry prompt embeds the anchor info verbatim.
  4. The smart-retry prompt is wired into the retry loop end-to-end (the
     round-2 user message contains the anchor section).
"""
from __future__ import annotations

import json
from typing import Any

import llm_statement_repair as lsr


# --- Scripted Mistral fake (mirrors test_llm_statement_repair_retry shape) -


class _Msg:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]


class _ScriptedChat:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> _Resp:
        self.calls.append(kwargs)
        if not self._responses:
            raise RuntimeError("scripted_chat_exhausted")
        return _Resp(self._responses.pop(0))


class ScriptedClient:
    def __init__(self, responses: list[str]) -> None:
        self.chat = _ScriptedChat(responses)


def _ok_response(decl: str, *, reasoning: str = "encodes claim", confidence: float = 0.8) -> str:
    return json.dumps(
        {
            "verdict": "SIGNATURE",
            "lean_signature": decl,
            "reasoning": reasoning,
            "confidence": confidence,
        }
    )


class _ScriptedValidator:
    def __init__(self, verdicts: list[tuple[bool, str]]) -> None:
        self._verdicts = list(verdicts)
        self.calls: list[str] = []

    def __call__(self, decl: str) -> tuple[bool, str]:
        self.calls.append(decl)
        if not self._verdicts:
            raise RuntimeError("scripted_validator_exhausted")
        return self._verdicts.pop(0)


# --- Sample paper-theory hint mirroring Paper_2304_09598.lean --------------


SAMPLE_HINT = """\
abbrev Multisegment : Type
instance : LE Multisegment
instance : Preorder Multisegment
def dual (α : Multisegment) : Multisegment
def L_alpha (_α : Multisegment) : ℕ
def PaperSymbolName (x : Multisegment) : Prop
axiom PaperSymbolName_inv (h : PaperSymbolName α) : True
"""


# --- Anchor-extraction unit tests ------------------------------------------


def test_extract_unknown_identifier_anchor() -> None:
    """`unknown identifier 'PaperSymbolName'` is extracted with the right symbol."""
    error_tail = (
        "error: type mismatch\n"
        "  ...\n"
        "error: unknown identifier 'PaperSymbolName'"
    )
    anchors = lsr._extract_lean_error_anchors(error_tail, SAMPLE_HINT)
    assert "unknown_identifier" in anchors["kinds"]
    matching = [a for a in anchors["anchors"] if a["kind"] == "unknown_identifier"]
    assert len(matching) == 1
    assert matching[0]["symbol"] == "PaperSymbolName"
    # Paper-theory hint mentions both `PaperSymbolName` and `PaperSymbolName_inv`;
    # the anchor matcher surfaces both lines.
    matches = matching[0]["matches"]
    assert any("PaperSymbolName" in m for m in matches)
    assert matching[0]["no_match_reason"] == ""


def test_extract_synth_instance_anchor() -> None:
    """`synthInstanceFailed: HasSubset Multisegment` parses class + type-arg."""
    error_tail = (
        "error: failed to synthesize instance\n"
        "  HasSubset Multisegment"
    )
    anchors = lsr._extract_lean_error_anchors(error_tail, SAMPLE_HINT)
    kinds = anchors["kinds"]
    assert "synth_instance_failed" in kinds
    primary = [a for a in anchors["anchors"] if a["kind"] == "synth_instance_failed"][0]
    assert primary["symbol"] == "HasSubset"
    assert primary["extra"]["type_args"] == ["Multisegment"]
    # `HasSubset` is not in the paper-theory hint — surfaces the translator-gap signal.
    assert primary["matches"] == []
    assert "translator-side gap" in primary["no_match_reason"]
    # The type-arg sub-anchor surfaces the Multisegment-related instance lines.
    type_arg_anchors = [
        a for a in anchors["anchors"] if a["kind"] == "synth_instance_failed_type_arg"
    ]
    assert any(a["symbol"] == "Multisegment" and a["matches"] for a in type_arg_anchors)


def test_extract_type_mismatch_anchor_preserves_expected_and_got() -> None:
    """A `type mismatch` error captures both expected and got types verbatim."""
    error_tail = (
        "error: type mismatch\n"
        "  L_alpha α\n"
        "has type\n"
        "  ℕ\n"
        "but is expected to have type\n"
        "  ℝ"
    )
    anchors = lsr._extract_lean_error_anchors(error_tail, SAMPLE_HINT)
    assert "type_mismatch" in anchors["kinds"]
    entry = [a for a in anchors["anchors"] if a["kind"] == "type_mismatch"][0]
    assert entry["extra"]["expected"] == "ℝ"
    assert entry["extra"]["got"] == "ℕ"


def test_extract_invalid_field_anchor() -> None:
    """`Invalid field 'segments'... does not contain `Nat.segments`` parses
    the field and the host-type from the missing-constant clause. This kind
    was added after the live smoke caught `Prop_Actions` looping all 3 rounds
    on `Invalid field 'segments'` with the original retry-only prompt."""
    error_tail = (
        "4:137: error(lean.invalidField): Invalid field `segments`: "
        "The environment does not contain `Nat.segments`, so it is not "
        "possible to project the field `segments`"
    )
    anchors = lsr._extract_lean_error_anchors(error_tail, SAMPLE_HINT)
    assert "invalid_field" in anchors["kinds"]
    entry = [a for a in anchors["anchors"] if a["kind"] == "invalid_field"][0]
    assert entry["symbol"] == "segments"
    assert entry["extra"]["host_type"] == "Nat"
    assert entry["extra"]["missing_constant"] == "Nat.segments"


def test_smart_retry_prompt_surfaces_invalid_field_anchor() -> None:
    """The smart-retry prompt names the field and the host type so the LLM
    knows to stop dot-projecting on the raw `abbrev`."""
    error_tail = (
        "4:137: error(lean.invalidField): Invalid field `segments`: "
        "The environment does not contain `Nat.segments`, so it is not "
        "possible to project the field `segments`"
    )
    prompt = lsr._render_smart_retry_prompt(
        theorem_name="Prop_Actions",
        source_latex="claim about multisegment segments",
        paper_theory_hint=SAMPLE_HINT,
        prev_round=1,
        max_rounds=3,
        prev_candidate="theorem Prop_Actions (α : Multisegment) : α.segments = α.segments := by sorry",
        lean_error_tail=error_tail,
    )
    assert "Invalid field 'segments'" in prompt
    assert "Nat" in prompt
    # The suggestion advises against dot-projection on the abbrev.
    assert "paper-theory function" in prompt or "function form" in prompt


def test_extract_function_expected_anchor() -> None:
    """`function expected at <name>` surfaces the symbol for binder retyping."""
    error_tail = "error: function expected at\n  L_alpha\nterm has type"
    anchors = lsr._extract_lean_error_anchors(error_tail, SAMPLE_HINT)
    assert "function_expected" in anchors["kinds"]
    entry = [a for a in anchors["anchors"] if a["kind"] == "function_expected"][0]
    assert entry["symbol"] == "L_alpha"
    # `L_alpha` IS in the hint, so the matcher should surface its def line.
    assert any("L_alpha" in m for m in entry["matches"])


def test_anchor_matcher_signals_translator_gap_when_symbol_missing() -> None:
    """When the offending identifier is not in the paper-theory hint at all,
    the anchor records a translator-gap reason (not a fixable LLM issue)."""
    error_tail = "error: unknown identifier 'DefinitelyMissingSymbol_xyz'"
    anchors = lsr._extract_lean_error_anchors(error_tail, SAMPLE_HINT)
    entry = [a for a in anchors["anchors"] if a["kind"] == "unknown_identifier"][0]
    assert entry["matches"] == []
    assert "translator-side gap" in entry["no_match_reason"]


def test_anchor_matcher_finds_entries_when_symbol_present() -> None:
    """When the symbol IS in the hint, matching lines are returned (no reason)."""
    error_tail = "error: unknown identifier 'L_alpha'"
    anchors = lsr._extract_lean_error_anchors(error_tail, SAMPLE_HINT)
    entry = [a for a in anchors["anchors"] if a["kind"] == "unknown_identifier"][0]
    assert any("L_alpha" in m for m in entry["matches"])
    assert entry["no_match_reason"] == ""


def test_empty_error_tail_returns_empty_anchors() -> None:
    """Defensive: empty / whitespace error tails produce an empty anchor list."""
    for tail in ("", "   ", "\n\n"):
        anchors = lsr._extract_lean_error_anchors(tail, SAMPLE_HINT)
        assert anchors["kinds"] == []
        assert anchors["anchors"] == []


# --- Smart-retry prompt rendering tests ------------------------------------


def test_smart_retry_prompt_includes_unknown_identifier_anchor_verbatim() -> None:
    """The smart-retry prompt embeds the anchor and its paper-theory matches."""
    prompt = lsr._render_smart_retry_prompt(
        theorem_name="Cor_Quant",
        source_latex="Some LaTeX claim.",
        paper_theory_hint=SAMPLE_HINT,
        prev_round=1,
        max_rounds=3,
        prev_candidate="theorem Cor_Quant : PaperSymbolName 0 := by sorry",
        lean_error_tail="error: unknown identifier 'PaperSymbolName'",
    )
    assert "SMART-RETRY ANCHORS" in prompt
    assert "`unknown identifier 'PaperSymbolName'`" in prompt
    # The matching paper-theory entry is surfaced.
    assert "PaperSymbolName" in prompt
    assert "Use ONE of these directly" in prompt


def test_smart_retry_prompt_omits_anchor_section_when_no_anchors() -> None:
    """A vanilla `unsolved goals` error has no anchorable identifiers — the
    anchor section is omitted cleanly rather than rendering an empty header."""
    prompt = lsr._render_smart_retry_prompt(
        theorem_name="foo",
        source_latex="claim",
        paper_theory_hint=SAMPLE_HINT,
        prev_round=1,
        max_rounds=3,
        prev_candidate="theorem foo : True := by sorry",
        lean_error_tail="error: unsolved goals",
    )
    assert "SMART-RETRY ANCHORS" not in prompt
    # The rest of the retry envelope is unchanged.
    assert "Lean elaboration error:" in prompt
    assert "Fix the SPECIFIC issue" in prompt


def test_smart_retry_prompt_surfaces_synth_instance_anchor() -> None:
    """For `synthInstanceFailed`, the prompt lists the class + type-arg and
    any paper-theory entries that mention either side."""
    prompt = lsr._render_smart_retry_prompt(
        theorem_name="foo",
        source_latex="claim",
        paper_theory_hint=SAMPLE_HINT,
        prev_round=1,
        max_rounds=3,
        prev_candidate="theorem foo (α : Multisegment) : α ⊆ α := by sorry",
        lean_error_tail="error: failed to synthesize instance\n  HasSubset Multisegment",
    )
    assert "SMART-RETRY ANCHORS" in prompt
    assert "HasSubset" in prompt
    assert "Multisegment" in prompt


def test_smart_retry_prompt_surfaces_function_expected_anchor() -> None:
    """`function expected at <name>` produces a typed-binder suggestion."""
    prompt = lsr._render_smart_retry_prompt(
        theorem_name="foo",
        source_latex="claim",
        paper_theory_hint=SAMPLE_HINT,
        prev_round=1,
        max_rounds=3,
        prev_candidate="theorem foo (α : Multisegment) : L_alpha α α = 0 := by sorry",
        lean_error_tail="error: function expected at\n  L_alpha\nterm has type ℕ",
    )
    assert "function expected at L_alpha" in prompt
    assert "_ → _" in prompt


def test_smart_retry_prompt_surfaces_type_mismatch_expected_and_got() -> None:
    """`type mismatch` surfaces expected/got types verbatim in the prompt."""
    error_tail = (
        "error: type mismatch\n"
        "  L_alpha α\n"
        "has type\n"
        "  ℕ\n"
        "but is expected to have type\n"
        "  ℝ"
    )
    prompt = lsr._render_smart_retry_prompt(
        theorem_name="foo",
        source_latex="claim",
        paper_theory_hint=SAMPLE_HINT,
        prev_round=1,
        max_rounds=3,
        prev_candidate="theorem foo (α : Multisegment) : L_alpha α = (0 : ℝ) := by sorry",
        lean_error_tail=error_tail,
    )
    assert "type mismatch" in prompt
    assert "expected: `ℝ`" in prompt
    assert "got: `ℕ`" in prompt


# --- End-to-end retry-loop wiring tests ------------------------------------


def test_smart_retry_anchor_section_appears_in_round_two_user_prompt() -> None:
    """End-to-end: a round-1 elaboration failure with an `unknown identifier`
    drives the round-2 user prompt to include the smart-retry anchor block."""
    bad = "theorem Cor_Quant (α : Multisegment) : PaperSymbolName α := by sorry"
    good = "theorem Cor_Quant (α β : Multisegment) (h : PaperSymbolName α) (h2 : α ≤ β) : PaperSymbolName β := by sorry"
    client = ScriptedClient([_ok_response(bad), _ok_response(good)])
    error_tail = (
        "error: unknown identifier 'PaperSymbolName' at line 3"
    )
    validator = _ScriptedValidator([(False, error_tail), (True, "")])

    result = lsr.generate_llm_repair_candidate(
        source_latex="Corollary 4.2: For any simple multisegment, PaperSymbolName holds.",
        paper_id="2304.09598",
        theorem_name="Cor_Quant",
        paper_theory_hint=SAMPLE_HINT,
        client=client,
        max_repair_rounds=3,
        validate_elaboration=validator,
    )

    assert result is not None
    assert result["rejected"] == []
    assert result["retry_rounds"] == 2

    # Round-2 user prompt must include the anchor section AND the matching
    # paper-theory entry verbatim.
    round2_user = client.chat.calls[1]["messages"][-1]["content"]
    assert "SMART-RETRY ANCHORS" in round2_user
    assert "`unknown identifier 'PaperSymbolName'`" in round2_user
    assert "PaperSymbolName" in round2_user
    # The original Lean error tail is still present (not replaced).
    assert "unknown identifier 'PaperSymbolName'" in round2_user
    # The original LaTeX claim is preserved.
    assert "Corollary 4.2" in round2_user


def test_smart_retry_prompt_signals_translator_gap_when_symbol_missing() -> None:
    """When the error references a symbol that is NOT in the paper-theory hint,
    the round-2 prompt surfaces the translator-gap reason explicitly so the
    LLM doesn't keep inventing variants that can never work."""
    bad = "theorem foo (α : Multisegment) : DefinitelyMissingSymbol α := by sorry"
    good = "theorem foo (α β : Multisegment) (h : α ≤ β) : L_alpha α ≤ L_alpha β := by sorry"
    client = ScriptedClient([_ok_response(bad), _ok_response(good)])
    error_tail = "error: unknown identifier 'DefinitelyMissingSymbol'"
    validator = _ScriptedValidator([(False, error_tail), (False, error_tail), (False, error_tail)])

    lsr.generate_llm_repair_candidate(
        source_latex="A claim about a thing.",
        paper_id="p",
        theorem_name="foo",
        paper_theory_hint=SAMPLE_HINT,
        client=client,
        max_repair_rounds=3,
        validate_elaboration=validator,
    )

    round2_user = client.chat.calls[1]["messages"][-1]["content"]
    assert "translator-side gap" in round2_user
