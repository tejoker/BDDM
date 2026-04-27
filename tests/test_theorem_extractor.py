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
