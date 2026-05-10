"""Tests for `\\newtheorem` display-name → kind classification.

The latex preprocessor scans for `\\newtheorem{env}{Display}` declarations
and registers env→kind aliases for the theorem extractor. Before this
fix, every declaration mapped to kind="theorem" — silently
mis-classifying `\\begin{definition}` blocks as theorems and routing
them through the (wrong) theorem-translation loop instead of the
definition-pass loop. The wrong routing produced
`theorem foo : False := by sorry` placeholders in the output `.lean`.
"""

from __future__ import annotations

from latex_preprocessor import _collect_definitions


def test_newtheorem_definition_display_classifies_as_definition() -> None:
    """`\\newtheorem{definition}{Definition}` must register
    env="definition" → kind="definition", not "theorem"."""
    text = r"\newtheorem{definition}{Definition}"
    _macros, aliases = _collect_definitions(text)
    assert aliases.get("definition") == "definition"


def test_newtheorem_lemma_display_classifies_as_lemma() -> None:
    text = r"\newtheorem{lemma}{Lemma}"
    _macros, aliases = _collect_definitions(text)
    assert aliases.get("lemma") == "lemma"


def test_newtheorem_corollary_display_classifies_as_corollary() -> None:
    text = r"\newtheorem{cor}{Corollary}"
    _macros, aliases = _collect_definitions(text)
    assert aliases.get("cor") == "corollary"


def test_newtheorem_proposition_display_classifies_as_proposition() -> None:
    text = r"\newtheorem{prop}{Proposition}"
    _macros, aliases = _collect_definitions(text)
    assert aliases.get("prop") == "proposition"


def test_newtheorem_remark_display_classifies_as_remark() -> None:
    text = r"\newtheorem{rem}{Remark}"
    _macros, aliases = _collect_definitions(text)
    assert aliases.get("rem") == "remark"


def test_newtheorem_with_counter_classifies_correctly() -> None:
    """`\\newtheorem{foo}[counter]{Display}` — the counter argument
    must not break parsing of the display name."""
    text = r"\newtheorem{foo}[section]{Definition}"
    _macros, aliases = _collect_definitions(text)
    assert aliases.get("foo") == "definition"


def test_newtheorem_with_trailing_counter_classifies_correctly() -> None:
    """`\\newtheorem{foo}{Display}[counter]` — the trailing shared-counter
    arg must not affect classification of the display name."""
    text = r"\newtheorem{foo}{Lemma}[section]"
    _macros, aliases = _collect_definitions(text)
    assert aliases.get("foo") == "lemma"


def test_newtheorem_unknown_display_falls_back_to_theorem() -> None:
    """Unknown display names default to "theorem" — preserves backwards
    compatibility for envs that don't match a known category."""
    text = r"\newtheorem{customenv}{SomeCustomDisplayName}"
    _macros, aliases = _collect_definitions(text)
    assert aliases.get("customenv") == "theorem"


def test_newtheorem_starred_form_with_alphabetic_env_name() -> None:
    """`\\newtheorem*{problem}{Problem}` — the un-numbered form is parsed
    correctly when the env name is plain alphabetic."""
    text = r"\newtheorem*{problem}{Problem}"
    _macros, aliases = _collect_definitions(text)
    # "Problem" is not a standard math kind → falls back to theorem.
    assert aliases.get("problem") == "theorem"


def test_real_2401_04567_preamble_classifies_definition_correctly() -> None:
    """The 2401.04567 paper has `\\newtheorem{definition}{Definition}` in its
    preamble. With the fix, env="definition" must be classified as
    kind="definition" (not theorem), so the def-pass picks them up
    instead of the (placeholder-emitting) theorem loop."""
    preamble = r"""
\newtheorem{definition}{Definition}
\newtheorem{lemma}{Lemma}
\newtheorem*{problem*}{Problem}
\newtheorem{remark}{Remark}
"""
    _macros, aliases = _collect_definitions(preamble)
    assert aliases["definition"] == "definition"
    assert aliases["lemma"] == "lemma"
    assert aliases["remark"] == "remark"
