from __future__ import annotations

import json
from pathlib import Path

import ponder_loop


class DummyClient:
    pass


def test_repair_full_proof_draft_injects_repair_examples(monkeypatch, tmp_path: Path) -> None:
    dataset = tmp_path / "april.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "failure_class": "assumption_mismatch",
                "error_message": "Tactic `assumption` failed",
                "previous_attempt": "assumption",
                "successful_repair": "constructor <;> assumption",
                "failing_lean": "theorem t : A ∧ B := by assumption",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DESOL_REPAIR_FEEDBACK_DATASET", str(dataset))
    captured: dict[str, object] = {}

    def fake_chat_complete(**kwargs):
        captured.update(kwargs)
        return object(), "<draft>constructor <;> assumption</draft>"

    monkeypatch.setattr(ponder_loop, "_chat_complete", fake_chat_complete)

    draft = ponder_loop.repair_full_proof_draft(
        lean_state="hA : A\nhB : B\n⊢ A ∧ B",
        current_draft="assumption",
        error_feedback="line=1; message=Tactic `assumption` failed",
        client=DummyClient(),
        model="dummy",
    )

    assert draft == "constructor <;> assumption"
    user_prompt = captured["messages"][1]["content"]  # type: ignore[index]
    assert "Similar successful DESol repair examples" in user_prompt
    assert "constructor <;> assumption" in user_prompt


def test_repair_full_proof_draft_warns_against_intro_on_non_binder(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_chat_complete(**kwargs):
        captured.update(kwargs)
        return object(), "<draft>exact h</draft>"

    monkeypatch.setattr(ponder_loop, "_chat_complete", fake_chat_complete)

    draft = ponder_loop.repair_full_proof_draft(
        lean_state="h : P\n⊢ P",
        current_draft="intro h1",
        error_feedback="line=1; message=intro tactic failed, target is not an implication and has no additional binders",
        client=DummyClient(),
        model="dummy",
    )

    assert draft == "exact h"
    user_prompt = captured["messages"][1]["content"]  # type: ignore[index]
    assert "Do not use `intro`" in user_prompt
    assert "exact h" in user_prompt
