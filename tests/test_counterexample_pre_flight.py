"""Hermetic tests for scripts/run_counterexample_pre_flight.py.

Every test uses a MOCKED Mistral client — no real API calls. We exercise:

  (a) statement with no free vars                    → fast-path no_counterexample
  (b) statement with free var properly bound          → LLM mocked to return no_counterexample
  (c) statement with unconstrained free var           → LLM mocked to return counterexample_found
  (d) malformed Leanstral response                    → inconclusive
  (e) ledger-walk happy path on a tmp_path ledger
  (f) `--write-gate-failure` flag appends gate_failures
  (g) function-space ∀ quantifier flagged             → matches the 2604.21884 POC shape
  (h) dry-run mode never calls the LLM
  (i) empty lean_statement                            → inconclusive (no probe)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from run_counterexample_pre_flight import (
    CounterexampleVerdict,
    _parse_probe_response,
    analyze_free_variables,
    probe_counterexample,
    run_preflight_on_paper,
)


# ---------------------------------------------------------------------------
# Mock Mistral client (mirrors the pattern in tests/test_leanstral_cot_judge.py)
# ---------------------------------------------------------------------------


class _MockMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _MockChoice:
    def __init__(self, content: str) -> None:
        self.message = _MockMessage(content)


class _MockResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_MockChoice(content)]


class _MockChatAPI:
    """Records every call so tests can assert call count."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> _MockResponse:
        self.calls.append(kwargs)
        return _MockResponse(self._content)


class _MockClient:
    def __init__(self, content: str = "") -> None:
        self.chat = _MockChatAPI(content)


def _probe_json(
    verdict: str,
    witness: dict[str, Any] | None = None,
    reasoning: str = "ok",
    blocking_hypothesis: str | None = None,
) -> str:
    """Build a mocked Leanstral probe response.

    Notes
    -----
    The current probe schema is v1.1: it requires a `blocking_hypothesis`
    field on `no_counterexample` verdicts so we can audit WHICH hypothesis
    rules out a falsifier. When verdict is `no_counterexample` and the
    caller didn't supply a blocking hypothesis, default to a plausible
    one so the Stage-B enforcement doesn't flip the verdict in tests
    that mean to test the `no_counterexample` codepath. Tests that
    INTEND to exercise Stage-B enforcement should pass
    `blocking_hypothesis=None` explicitly.
    """
    if verdict == "no_counterexample" and blocking_hypothesis is None:
        blocking_hypothesis = "<default-for-test>"
    return json.dumps({
        "verdict": verdict,
        "witness": witness,
        "reasoning": reasoning,
        "blocking_hypothesis": blocking_hypothesis,
    })


# ---------------------------------------------------------------------------
# (a) Statement with no free vars → fast-path no_counterexample, no LLM call
# ---------------------------------------------------------------------------


def test_fast_path_no_free_vars_skips_llm() -> None:
    # Every binder is hypothesis-constrained or fully ground.
    lean = (
        "theorem t (n : ℕ) (hn : 0 < n) : n * 1 = n := by sorry"
    )
    # The body `n * 1 = n` references `n` which IS declared but constrained
    # by `hn : 0 < n`. Free-var scan should mark zero unconstrained vars.
    client = _MockClient(_probe_json("counterexample_found"))  # never called
    result = probe_counterexample(
        theorem_name="t",
        lean_statement=lean,
        client=client,
        model="labs-leanstral-2603",
    )
    assert result.verdict == "no_counterexample"
    assert result.queried_llm is False
    assert client.chat.calls == []


# ---------------------------------------------------------------------------
# (b) Statement with free var properly bound → LLM mocked to return no_counterexample
# ---------------------------------------------------------------------------


def test_unconstrained_free_var_calls_llm_and_returns_no_counterexample() -> None:
    # `alpha` is declared as a free var with no hypothesis. We deliberately
    # mock the LLM to say `no_counterexample` (e.g. the conclusion is
    # trivially true even on unconstrained alpha).
    lean = (
        "theorem t (alpha : ℝ) : alpha = alpha := by sorry"
    )
    client = _MockClient(_probe_json("no_counterexample", reasoning="conclusion is reflexive"))
    result = probe_counterexample(
        theorem_name="t",
        lean_statement=lean,
        client=client,
        model="labs-leanstral-2603",
    )
    assert result.verdict == "no_counterexample"
    assert result.queried_llm is True
    # The LLM was called exactly once.
    assert len(client.chat.calls) == 1
    # The prompt should include the flagged variable name.
    user_msg = client.chat.calls[0]["messages"][1]["content"]
    assert "alpha" in user_msg


# ---------------------------------------------------------------------------
# (c) Statement with free var unbound → LLM mocked counterexample_found + witness
# ---------------------------------------------------------------------------


def test_unconstrained_free_var_can_return_counterexample_with_witness() -> None:
    lean = (
        "theorem prop_det_contraction (i j : ℕ) (h_ne : i ≠ j) "
        "(alpha theta : ℝ) (htheta : 0 < theta) : "
        "DyadicBlockBound i j alpha ≤ (i : ℝ) ^ (3 - 4 * alpha) := by sorry"
    )
    # `alpha` is declared but NOT hypothesis-constrained (no `halpha`).
    # `i = 0` is the dangerous corner case — (0 : ℝ)^exp = 0.
    witness = {"i": "0", "alpha": "1"}
    client = _MockClient(_probe_json(
        "counterexample_found",
        witness=witness,
        reasoning="i=0 gives (0:ℝ)^_ = 0, RHS=0; LHS positive constant violates",
    ))
    result = probe_counterexample(
        theorem_name="prop_det_contraction",
        lean_statement=lean,
        client=client,
        model="labs-leanstral-2603",
    )
    assert result.verdict == "counterexample_found"
    assert result.witness == witness
    assert "i=0" in result.reasoning
    assert result.queried_llm is True


# ---------------------------------------------------------------------------
# (d) Malformed Leanstral response → inconclusive
# ---------------------------------------------------------------------------


def test_malformed_leanstral_response_is_inconclusive() -> None:
    lean = "theorem t (alpha : ℝ) : ∃ C : ℝ, alpha ≤ C := by sorry"
    # Not JSON, not parseable.
    client = _MockClient("This isn't JSON at all — just prose from the model.")
    result = probe_counterexample(
        theorem_name="t",
        lean_statement=lean,
        client=client,
        model="labs-leanstral-2603",
    )
    assert result.verdict == "inconclusive"
    assert result.witness is None
    assert "malformed" in result.reasoning.lower() or "leanstral" in result.reasoning.lower()
    assert result.queried_llm is True


def test_invalid_verdict_label_normalizes_to_inconclusive() -> None:
    """If Leanstral returns a verdict we don't recognize, fail safe to inconclusive."""
    lean = "theorem t (alpha : ℝ) : ∃ C : ℝ, alpha ≤ C := by sorry"
    client = _MockClient(json.dumps(
        {"verdict": "DEFINITELY_FALSE", "witness": None, "reasoning": "made-up label"}
    ))
    result = probe_counterexample(
        theorem_name="t",
        lean_statement=lean,
        client=client,
        model="labs-leanstral-2603",
    )
    assert result.verdict == "inconclusive"


# ---------------------------------------------------------------------------
# (e) Ledger-walk happy path on tmp_path ledger
# ---------------------------------------------------------------------------


def _write_fake_ledger(tmp_path: Path, paper_id: str, entries: list[dict[str, Any]]) -> Path:
    ledger_dir = tmp_path / "output" / "verification_ledgers"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = ledger_dir / f"{paper_id}.json"
    ledger_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False),
                            encoding="utf-8")
    return ledger_path


def test_ledger_walk_writes_counterexample_preflight_field(tmp_path: Path) -> None:
    paper_id = "9999.99999"
    entries = [
        # UR row with unconstrained free var
        {
            "theorem_name": "thm_cex",
            "status": "UNRESOLVED",
            "lean_statement": (
                "theorem thm_cex (alpha : ℝ) : ∃ C : ℝ, alpha ≤ C := by sorry"
            ),
        },
        # UR row with constrained free var (fast-path)
        {
            "theorem_name": "thm_safe",
            "status": "UNRESOLVED",
            "lean_statement": (
                "theorem thm_safe (n : ℕ) (hn : 0 < n) : n = n := by sorry"
            ),
        },
        # Non-UR row should be skipped entirely
        {
            "theorem_name": "thm_proven",
            "status": "FULLY_PROVEN",
            "lean_statement": "theorem thm_proven : 1 = 1 := rfl",
        },
    ]
    _write_fake_ledger(tmp_path, paper_id, entries)
    client = _MockClient(_probe_json(
        "counterexample_found",
        witness={"alpha": "-1"},
        reasoning="alpha unbounded below; no C upper-bounds all of ℝ",
    ))
    summary = run_preflight_on_paper(
        paper_id=paper_id,
        project_root=tmp_path,
        client=client,
        model="labs-leanstral-2603",
    )
    # Both UR rows probed; FULLY_PROVEN row skipped.
    assert summary["rows_probed"] == 2
    assert summary["verdict_counts"]["counterexample_found"] == 1
    assert summary["verdict_counts"]["no_counterexample"] == 1
    assert summary["llm_calls"] == 1
    assert summary["fast_path_skips"] == 1
    # Verify ledger was rewritten with the preflight field on the UR rows.
    written = json.loads(
        (tmp_path / "output" / "verification_ledgers" / f"{paper_id}.json")
        .read_text(encoding="utf-8")
    )
    cex_row = next(e for e in written if e["theorem_name"] == "thm_cex")
    safe_row = next(e for e in written if e["theorem_name"] == "thm_safe")
    proven_row = next(e for e in written if e["theorem_name"] == "thm_proven")
    assert cex_row["counterexample_preflight"]["verdict"] == "counterexample_found"
    assert cex_row["counterexample_preflight"]["witness"] == {"alpha": "-1"}
    assert safe_row["counterexample_preflight"]["verdict"] == "no_counterexample"
    # Non-UR row untouched.
    assert "counterexample_preflight" not in proven_row
    # Default behavior is metadata-only — gate_failures unchanged.
    assert cex_row.get("gate_failures", []) == []


# ---------------------------------------------------------------------------
# (f) --write-gate-failure flag appends to gate_failures
# ---------------------------------------------------------------------------


def test_write_gate_failure_appends_when_counterexample_found(tmp_path: Path) -> None:
    paper_id = "9999.99998"
    entries = [
        {
            "theorem_name": "thm_cex",
            "status": "UNRESOLVED",
            "gate_failures": ["existing_failure"],
            "lean_statement": (
                "theorem thm_cex (alpha : ℝ) : ∃ C : ℝ, alpha ≤ C := by sorry"
            ),
        },
    ]
    _write_fake_ledger(tmp_path, paper_id, entries)
    client = _MockClient(_probe_json(
        "counterexample_found", witness={"alpha": "-1"}, reasoning="ok"
    ))
    run_preflight_on_paper(
        paper_id=paper_id,
        project_root=tmp_path,
        client=client,
        model="labs-leanstral-2603",
        write_gate_failure=True,
    )
    written = json.loads(
        (tmp_path / "output" / "verification_ledgers" / f"{paper_id}.json")
        .read_text(encoding="utf-8")
    )
    row = written[0]
    assert "counterexample_found" in row["gate_failures"]
    assert "existing_failure" in row["gate_failures"]  # preserved
    # Idempotent: re-running shouldn't duplicate the marker.
    run_preflight_on_paper(
        paper_id=paper_id,
        project_root=tmp_path,
        client=client,
        model="labs-leanstral-2603",
        write_gate_failure=True,
    )
    written2 = json.loads(
        (tmp_path / "output" / "verification_ledgers" / f"{paper_id}.json")
        .read_text(encoding="utf-8")
    )
    assert written2[0]["gate_failures"].count("counterexample_found") == 1


# ---------------------------------------------------------------------------
# (g) Function-space ∀ quantifier flagged (matches 2604.21884 POC shape)
# ---------------------------------------------------------------------------


def test_function_space_quantifier_is_flagged_as_unconstrained() -> None:
    """The canonical `∃ C, ∀ w : ℝ → ℝ, ‖w‖ ≤ C` shape: w ranges over an
    unconstrained function space and uniform bound is impossible. The
    free-variable analyzer must flag `w` so the LLM is consulted."""
    lean = (
        "theorem thm_operator_main (alpha eps : ℝ) "
        "(halpha : 0 < alpha) (heps : 0 < eps) : "
        "∃ C : ℝ, 0 < C ∧ ∀ w : ℝ → ℝ, ‖w‖ ≤ C := by sorry"
    )
    report = analyze_free_variables(lean)
    assert "w" in report.unconstrained_in_body
    # alpha/eps are hypothesis-bound — must NOT be flagged.
    assert "alpha" not in report.unconstrained_in_body
    assert "eps" not in report.unconstrained_in_body


def test_function_space_quantifier_routes_to_llm(tmp_path: Path) -> None:
    """End-to-end: a 2604.21884-shape statement should call the LLM and
    a `counterexample_found` mock should propagate to the preflight field."""
    paper_id = "9999.99997"
    entries = [
        {
            "theorem_name": "thm_operator_main",
            "status": "UNRESOLVED",
            "lean_statement": (
                "theorem thm_operator_main (alpha : ℝ) (halpha : 0 < alpha) : "
                "∃ C : ℝ, ∀ w : ℝ → ℝ, ‖w‖ ≤ C := by sorry"
            ),
        },
    ]
    _write_fake_ledger(tmp_path, paper_id, entries)
    client = _MockClient(_probe_json(
        "counterexample_found",
        witness={"w": "fun _ => C + 1"},
        reasoning="w = constant C+1 has ‖w‖ > C; no uniform bound exists",
    ))
    summary = run_preflight_on_paper(
        paper_id=paper_id,
        project_root=tmp_path,
        client=client,
        model="labs-leanstral-2603",
    )
    assert summary["llm_calls"] == 1
    assert summary["verdict_counts"]["counterexample_found"] == 1


# ---------------------------------------------------------------------------
# (h) Dry-run mode never calls the LLM and never writes the ledger
# ---------------------------------------------------------------------------


def test_dry_run_never_calls_llm_and_does_not_write_ledger(tmp_path: Path) -> None:
    paper_id = "9999.99996"
    entries = [
        {
            "theorem_name": "thm_cex",
            "status": "UNRESOLVED",
            "lean_statement": (
                "theorem thm_cex (alpha : ℝ) : ∃ C : ℝ, alpha ≤ C := by sorry"
            ),
        },
    ]
    ledger_path = _write_fake_ledger(tmp_path, paper_id, entries)
    before = ledger_path.read_text(encoding="utf-8")
    # Pass client=None and dry_run=True. Inside the function it should
    # never construct a client and never call .chat.complete.
    summary = run_preflight_on_paper(
        paper_id=paper_id,
        project_root=tmp_path,
        client=None,
        model="labs-leanstral-2603",
        dry_run=True,
    )
    after = ledger_path.read_text(encoding="utf-8")
    assert before == after  # ledger untouched
    # The row WAS probed (we still emit a summary), but its verdict is
    # `inconclusive` because we can't actually check without a client.
    assert summary["rows_probed"] == 1
    assert summary["llm_calls"] == 0


# ---------------------------------------------------------------------------
# (i) Empty lean_statement → inconclusive without LLM call
# ---------------------------------------------------------------------------


def test_empty_lean_statement_returns_inconclusive_without_llm() -> None:
    client = _MockClient(_probe_json("counterexample_found"))  # never called
    result = probe_counterexample(
        theorem_name="thm_empty",
        lean_statement="",
        client=client,
        model="labs-leanstral-2603",
    )
    assert result.verdict == "inconclusive"
    assert result.queried_llm is False
    assert client.chat.calls == []


# ---------------------------------------------------------------------------
# JSON-parsing robustness (additional micro-tests)
# ---------------------------------------------------------------------------


def test_parse_probe_response_strips_markdown_fences() -> None:
    raw = "```json\n" + json.dumps({"verdict": "no_counterexample", "witness": None,
                                     "reasoning": "ok"}) + "\n```"
    parsed = _parse_probe_response(raw)
    assert parsed["verdict"] == "no_counterexample"


def test_parse_probe_response_handles_prose_around_json() -> None:
    raw = (
        "Sure! Here's my analysis:\n\n"
        + json.dumps({"verdict": "counterexample_found",
                       "witness": {"x": "0"}, "reasoning": "x=0"})
        + "\n\nLet me know if you want more detail."
    )
    parsed = _parse_probe_response(raw)
    assert parsed["verdict"] == "counterexample_found"
    assert parsed["witness"] == {"x": "0"}


def test_parse_probe_response_returns_empty_on_no_json() -> None:
    assert _parse_probe_response("no json here") == {}
    assert _parse_probe_response("") == {}


# ---------------------------------------------------------------------------
# Stage-B enforcement (prompt v1.1): no_counterexample WITHOUT a named
# blocking hypothesis must be auto-promoted to counterexample_found.
# This is the recall-raising lever — the previous prompt under-flagged
# the 2604.21884 false-as-stated rows because the model could vote
# `safe` without justification.
# ---------------------------------------------------------------------------


def test_stage_b_enforcement_flips_unjustified_safe_verdict() -> None:
    """When Leanstral votes `no_counterexample` but names NO blocking
    hypothesis, the probe must flip the verdict to `counterexample_found`.
    """
    lean = (
        "theorem thm_operator_main (alpha : ℝ) (halpha : 0 < alpha) : "
        "∃ C : ℝ, ∀ w : ℝ → ℝ, ‖w‖ ≤ C := by sorry"
    )
    # Model votes no_counterexample but blocking_hypothesis is null/empty.
    client = _MockClient(json.dumps({
        "verdict": "no_counterexample",
        "witness": None,
        "reasoning": "all free variables are constrained",
        "blocking_hypothesis": None,
    }))
    result = probe_counterexample(
        theorem_name="thm_operator_main",
        lean_statement=lean,
        client=client,
        model="labs-leanstral-2603",
    )
    assert result.verdict == "counterexample_found"
    assert "promoted by Stage-B enforcement" in result.reasoning


def test_stage_b_enforcement_keeps_safe_verdict_when_hypothesis_named() -> None:
    """When Leanstral names a blocking hypothesis, `no_counterexample` stands."""
    lean = (
        "theorem t (alpha : ℝ) (halpha : 0 < alpha) : alpha + 1 > 0 := by sorry"
    )
    client = _MockClient(json.dumps({
        "verdict": "no_counterexample",
        "witness": None,
        "reasoning": "halpha rules out alpha ≤ -1",
        "blocking_hypothesis": "halpha",
    }))
    result = probe_counterexample(
        theorem_name="t",
        lean_statement=lean,
        client=client,
        model="labs-leanstral-2603",
    )
    assert result.verdict == "no_counterexample"
    assert "promoted" not in result.reasoning


def test_stage_b_enforcement_treats_empty_string_as_null_blocker() -> None:
    """`blocking_hypothesis: ""` is null-like — must still trigger promotion."""
    lean = (
        "theorem t (alpha : ℝ) : ∃ C : ℝ, alpha ≤ C := by sorry"
    )
    client = _MockClient(json.dumps({
        "verdict": "no_counterexample",
        "witness": None,
        "reasoning": "looks fine",
        "blocking_hypothesis": "",
    }))
    result = probe_counterexample(
        theorem_name="t",
        lean_statement=lean,
        client=client,
        model="labs-leanstral-2603",
    )
    assert result.verdict == "counterexample_found"


def test_stage_b_enforcement_treats_string_none_as_null_blocker() -> None:
    """Leanstral sometimes emits literal `"None"`/`"null"` strings — those
    must also trigger Stage-B promotion."""
    lean = (
        "theorem t (alpha : ℝ) : ∃ C : ℝ, alpha ≤ C := by sorry"
    )
    for sentinel in ("None", "null", "N/A"):
        client = _MockClient(json.dumps({
            "verdict": "no_counterexample",
            "witness": None,
            "reasoning": "looks fine",
            "blocking_hypothesis": sentinel,
        }))
        result = probe_counterexample(
            theorem_name="t",
            lean_statement=lean,
            client=client,
            model="labs-leanstral-2603",
        )
        assert result.verdict == "counterexample_found", (
            f"Expected promotion for sentinel={sentinel!r}, got "
            f"verdict={result.verdict!r}"
        )


def test_probe_prompt_includes_two_stage_protocol_instructions() -> None:
    """Pin the prompt-shape contract: the system prompt MUST mention the
    two-stage protocol AND the user message MUST instruct the model to
    name the blocking hypothesis. This guards against accidental prompt
    regressions that silently drop the recall-raising design.
    """
    lean = "theorem t (alpha : ℝ) : ∃ C : ℝ, alpha ≤ C := by sorry"
    client = _MockClient(_probe_json("counterexample_found",
                                       witness={"alpha": "-1"}))
    probe_counterexample(
        theorem_name="t",
        lean_statement=lean,
        client=client,
        model="labs-leanstral-2603",
    )
    call = client.chat.calls[0]
    system_msg = call["messages"][0]["content"]
    user_msg = call["messages"][1]["content"]
    # System prompt covenants:
    assert "STAGE A" in system_msg or "Stage A" in system_msg
    assert "STAGE B" in system_msg or "Stage B" in system_msg
    assert "blocking_hypothesis" in system_msg
    # User-message covenants:
    assert "blocking hypothesis" in user_msg.lower()


def test_counterexample_found_witness_may_be_null() -> None:
    """For a parametric-family counterexample, the model may return a
    null `witness` and still vote `counterexample_found`. The verdict
    must stand (we do NOT require a concrete witness to flag)."""
    lean = (
        "theorem thm_unbounded (alpha : ℝ) (halpha : 0 < alpha) : "
        "∃ C : ℝ, ∀ w : ℝ → ℝ, ‖w‖ ≤ C := by sorry"
    )
    client = _MockClient(json.dumps({
        "verdict": "counterexample_found",
        "witness": None,
        "reasoning": "parametric family w_n = const_n with const_n → ∞ violates any C",
        "blocking_hypothesis": None,
    }))
    result = probe_counterexample(
        theorem_name="thm_unbounded",
        lean_statement=lean,
        client=client,
        model="labs-leanstral-2603",
    )
    assert result.verdict == "counterexample_found"
    assert result.witness is None
    assert "parametric family" in result.reasoning


# ---------------------------------------------------------------------------
# Free-variable analysis edge cases
# ---------------------------------------------------------------------------


def test_nested_paren_in_hypothesis_type_is_parsed_correctly() -> None:
    """A hypothesis like `(halpha : (3:ℝ)/4 < alpha)` contains a nested
    paren. The walker must still recognize it as a hypothesis binder and
    extract `alpha` as constrained — NOT flag it as unconstrained."""
    lean = (
        "theorem t (alpha : ℝ) (halpha : (3:ℝ)/4 < alpha) : alpha > 0 := by sorry"
    )
    report = analyze_free_variables(lean)
    assert "halpha" in report.hypothesis_binder_names
    assert "alpha" not in report.unconstrained_in_body


def test_existential_with_conjunctive_body_routes_to_llm() -> None:
    """The `cor_safe_range` shape: `∃ ε > 0, P(ε) ∧ Q(ε) ∧ R(ε)`. Static
    analysis can't decide joint satisfiability; analyzer must flag the
    existential bound variable so the LLM is consulted."""
    lean = (
        "theorem cor_safe_range (alpha : ℝ) (halpha : 0 < alpha) :\n"
        "  ∃ epsilon : ℝ, 0 < epsilon ∧\n"
        "    epsilon < alpha ∧\n"
        "    alpha + epsilon < 1 := by sorry"
    )
    report = analyze_free_variables(lean)
    assert "epsilon" in report.unconstrained_in_body
