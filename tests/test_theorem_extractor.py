from __future__ import annotations

from pathlib import Path

from theorem_extractor import extract_theorems, register_environment_aliases


def test_extract_theorem_preserves_body_label_and_following_proof(tmp_path: Path) -> None:
    tex = tmp_path / "main.tex"
    tex.write_text(
        r"""
        \begin{theorem}
        \label{thm:add-zero}
        For every $n$, $n + 0 = n$.
        \end{theorem}
        \begin{proof}
        By induction.
        \end{proof}
        """,
        encoding="utf-8",
    )

    [entry] = extract_theorems(tex)

    assert entry.kind == "theorem"
    assert entry.name == "thm:add-zero"
    assert "For every $n$" in entry.statement
    assert entry.statement.rstrip().endswith("$n + 0 = n$.")
    assert entry.proof == "By induction."
    assert entry.source_file == str(tex)
    assert entry.env_name == "theorem"
    assert entry.label == "thm:add-zero"
    assert entry.span_start >= 0
    assert entry.span_end > entry.span_start
    assert entry.body_start >= entry.span_start
    assert entry.body_end <= entry.span_end
    assert entry.start_line == 2
    assert entry.end_line == 5
    assert entry.source_span_id.startswith("srcspan_")
    assert entry.source_span is not None
    assert entry.source_span.source_file == str(tex)
    assert entry.source_span.start_byte < entry.source_span.end_byte
    assert entry.source_span.start_line == 3
    assert entry.proof_span is not None
    assert entry.proof_span.start_line == 7


def test_extract_compact_environment_does_not_drop_last_character(tmp_path: Path) -> None:
    tex = tmp_path / "compact.tex"
    tex.write_text(r"\begin{lemma}ABC\end{lemma}", encoding="utf-8")

    [entry] = extract_theorems(tex)

    assert entry.kind == "lemma"
    assert entry.name == "lemma_1"
    assert entry.statement == "ABC"


def test_registered_environment_alias_is_extracted(tmp_path: Path) -> None:
    register_environment_aliases({"mainthm": "theorem"})
    tex = tmp_path / "alias.tex"
    tex.write_text(r"\begin{mainthm}Aliased theorem.\end{mainthm}", encoding="utf-8")

    [entry] = extract_theorems(tex)

    assert entry.kind == "theorem"
    assert entry.name == "mainthm_1"
    assert entry.statement == "Aliased theorem."


def test_nested_same_name_environment_uses_balanced_span(tmp_path: Path) -> None:
    tex = tmp_path / "nested.tex"
    tex.write_text(
        r"""
        \begin{lemma}
        Outer statement starts.
        \begin{lemma}Inner statement.\end{lemma}
        Outer statement ends.
        \end{lemma}
        """,
        encoding="utf-8",
    )

    entries = extract_theorems(tex)

    assert len(entries) == 2
    assert "Outer statement ends." in entries[0].statement
    assert entries[0].span_end > entries[1].span_end
