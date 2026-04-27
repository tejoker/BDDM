from __future__ import annotations

from pathlib import Path

from latex_preprocessor import (
    MacroDefinition,
    collect_definitions,
    collect_root_tex_paths,
    expand_include_tree,
    expand_latex_macros,
    write_expanded_roots,
)


def test_collect_definitions_and_expand_macros(tmp_path: Path) -> None:
    tex = tmp_path / "main.tex"
    tex.write_text(
        r"""
        \newcommand{\norm}[1]{\lVert #1 \rVert}
        \DeclareMathOperator{\Spec}{Spec}
        \newtheorem{mainthm}{Theorem}
        $\norm{x}$ and $\Spec R$.
        """,
        encoding="utf-8",
    )

    macros, aliases = collect_definitions([tex])

    assert aliases == {"mainthm": "theorem"}
    assert macros["norm"] == MacroDefinition("norm", 1, r"\lVert #1 \rVert")
    assert macros["Spec"].replacement == "Spec"
    assert r"\lVert x \rVert" in expand_latex_macros(tex.read_text(encoding="utf-8"), macros)


def test_expand_include_tree_keeps_includes_inside_source_root(tmp_path: Path) -> None:
    root = tmp_path / "paper"
    root.mkdir()
    main = root / "main.tex"
    section = root / "section.tex"
    outside = tmp_path / "outside.tex"

    main.write_text(r"A \input{section} B \input{../outside}", encoding="utf-8")
    section.write_text("included", encoding="utf-8")
    outside.write_text("should not inline", encoding="utf-8")

    expanded = expand_include_tree(
        main.read_text(encoding="utf-8"),
        current_path=main,
        source_root=root,
    )

    assert "A included B" in expanded
    assert r"\input{../outside}" in expanded


def test_write_expanded_roots_inlines_includes_and_macros(tmp_path: Path) -> None:
    root = tmp_path / "paper"
    root.mkdir()
    main = root / "main.tex"
    included = root / "defs.tex"
    output = tmp_path / "expanded"

    main.write_text(r"\newcommand{\R}{\mathbb{R}}\input{defs}", encoding="utf-8")
    included.write_text(r"\begin{theorem}$x \in \R$.\end{theorem}", encoding="utf-8")

    macros, _aliases = collect_definitions([main])
    roots = collect_root_tex_paths([main, included], main_tex=main)
    expanded_paths = write_expanded_roots(
        root_tex_paths=roots,
        source_root=root,
        output_root=output,
        macro_defs=macros,
    )

    assert expanded_paths == [output / "main.tex"]
    assert r"$x \in \mathbb{R}$" in expanded_paths[0].read_text(encoding="utf-8")
