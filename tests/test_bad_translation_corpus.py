from __future__ import annotations

import json
from pathlib import Path

from arxiv_to_lean import translation_acceptance_gate
from statement_translator import TranslationResult
from theorem_extractor import TheoremEntry


def test_bad_translation_corpus_is_blocked_by_acceptance_gate() -> None:
    corpus = Path("reproducibility/bad_translation_corpus.jsonl")
    rows = [json.loads(line) for line in corpus.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert rows
    for row in rows:
        gate = translation_acceptance_gate(
            entry=TheoremEntry(
                kind="theorem",
                name=row["id"],
                statement=row["source_statement"],
                proof="",
                source_file="bad_translation_corpus.jsonl",
            ),
            translation=TranslationResult(
                lean_signature=row["lean_signature"],
                validated=True,
                rounds_used=1,
                last_error="",
                confidence=0.9,
            ),
        )

        assert gate.accepted is False, row["id"]
        assert gate.reason.startswith(row["expected_reason_prefix"]), row["id"]
