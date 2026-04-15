#!/usr/bin/env python3
"""Thin LaTeX preprocessing wrapper for arxiv theorem extraction.

Goals:
- expand \input / \subfile include trees
- expand simple macro definitions (newcommand/renewcommand/def/DeclareMathOperator)
- collect theorem-like environment aliases from \newtheorem

This is intentionally a wrapper around pylatexenc parsing primitives, not a
full LaTeX engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pylatexenc.latexwalker import (
    LatexCharsNode,
    LatexCommentNode,
    LatexEnvironmentNode,
    LatexGroupNode,
    LatexMacroNode,
    LatexWalker,
)


@dataclass(frozen=True)
class MacroDefinition:
    name: str
    num_args: int
    replacement: str


_NEWCOMMAND_RE = re.compile(r"\\(?:re)?newcommand\*?\s*\{\\([A-Za-z@]+)\}", re.DOTALL)
_DECLARE_MATH_OP_RE = re.compile(r"\\DeclareMathOperator\*?\s*\{\\([A-Za-z@]+)\}")
_NEWTHM_RE = re.compile(r"\\newtheorem\*?\s*\{([A-Za-z@]+)\}")
_INPUT_RE = re.compile(r"\\(?:input|subfile)\s*\{([^}]+)\}")


def _read_braced(text: str, start: int, open_char: str = "{", close_char: str = "}") -> tuple[str, int]:
    if start >= len(text) or text[start] != open_char:
        raise ValueError("expected braced group")
    depth = 0
    idx = start
    while idx < len(text):
        ch = text[idx]
        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return text[start + 1 : idx], idx + 1
        idx += 1
    raise ValueError("unterminated braced group")


def _read_optional(text: str, start: int) -> tuple[str | None, int]:
    if start >= len(text) or text[start] != "[":
        return None, start
    depth = 0
    idx = start
    while idx < len(text):
        ch = text[idx]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start + 1 : idx], idx + 1
        idx += 1
    raise ValueError("unterminated optional group")


def _substitute_template(template: str, args: list[str]) -> str:
    rendered = template
    for idx, arg in enumerate(args, start=1):
        rendered = rendered.replace(f"#{idx}", arg)
    return rendered


def _collect_definitions(text: str) -> tuple[dict[str, MacroDefinition], dict[str, str]]:
    macros: dict[str, MacroDefinition] = {}
    env_aliases: dict[str, str] = {}

    idx = 0
    while idx < len(text):
        if text.startswith("\\newcommand", idx) or text.startswith("\\renewcommand", idx):
            match = _NEWCOMMAND_RE.match(text, idx)
            if not match:
                idx += 1
                continue
            name = match.group(1)
            pos = match.end()
            num_args = 0
            opt_num, pos = _read_optional(text, pos)
            if opt_num and opt_num.isdigit():
                num_args = int(opt_num)
            replacement = ""
            try:
                replacement, pos = _read_braced(text, pos)
            except Exception:
                pass
            macros[name] = MacroDefinition(name=name, num_args=num_args, replacement=replacement)
            idx = pos
            continue

        if text.startswith("\\DeclareMathOperator", idx):
            match = _DECLARE_MATH_OP_RE.match(text, idx)
            if not match:
                idx += 1
                continue
            name = match.group(1)
            pos = match.end()
            replacement = ""
            try:
                replacement, pos = _read_braced(text, pos)
            except Exception:
                pass
            macros[name] = MacroDefinition(name=name, num_args=0, replacement=replacement)
            idx = pos
            continue

        if text.startswith("\\newtheorem", idx):
            match = _NEWTHM_RE.match(text, idx)
            if not match:
                idx += 1
                continue
            env_name = match.group(1)
            env_aliases[env_name] = "theorem"
            idx = match.end()
            continue

        if text.startswith("\\edef", idx):
            # \edef\name{body} — treat like \def; body pre-expansion is deferred.
            pos = idx + len("\\edef")
            while pos < len(text) and text[pos].isspace():
                pos += 1
            if pos >= len(text) or text[pos] != "\\":
                idx += 1
                continue
            pos += 1
            name_start = pos
            while pos < len(text) and (text[pos].isalpha() or text[pos] in "@"):
                pos += 1
            name = text[name_start:pos]
            body = ""
            try:
                body_start = text.find("{", pos)
                if body_start != -1:
                    body, pos = _read_braced(text, body_start)
            except Exception:
                pass
            macros[name] = MacroDefinition(name=name, num_args=0, replacement=body)
            idx = pos
            continue

        if text.startswith("\\let", idx):
            # \let\newname=\oldname  or  \let\newname\oldname
            # Registers \newname as alias for \oldname's current definition (if known).
            pos = idx + len("\\let")
            while pos < len(text) and text[pos].isspace():
                pos += 1
            if pos >= len(text) or text[pos] != "\\":
                idx += 1
                continue
            pos += 1
            new_start = pos
            while pos < len(text) and (text[pos].isalpha() or text[pos] in "@"):
                pos += 1
            new_name = text[new_start:pos]
            # Skip optional =
            while pos < len(text) and text[pos] in (" ", "\t", "="):
                pos += 1
            if pos < len(text) and text[pos] == "\\":
                pos += 1
                old_start = pos
                while pos < len(text) and (text[pos].isalpha() or text[pos] in "@"):
                    pos += 1
                old_name = text[old_start:pos]
                if old_name in macros:
                    macros[new_name] = MacroDefinition(
                        name=new_name,
                        num_args=macros[old_name].num_args,
                        replacement=macros[old_name].replacement,
                    )
            idx = pos
            continue

        if text.startswith("\\def", idx):
            pos = idx + len("\\def")
            while pos < len(text) and text[pos].isspace():
                pos += 1
            if pos >= len(text) or text[pos] != "\\":
                idx += 1
                continue
            pos += 1
            name_start = pos
            while pos < len(text) and (text[pos].isalpha() or text[pos] in "@"):
                pos += 1
            name = text[name_start:pos]
            arg_count = text[pos : text.find("{", pos)].count("#") if "{" in text[pos:] else 0
            body = ""
            try:
                body_start = text.find("{", pos)
                if body_start != -1:
                    body, pos = _read_braced(text, body_start)
            except Exception:
                pass
            macros[name] = MacroDefinition(name=name, num_args=max(0, arg_count), replacement=body)
            idx = pos
            continue

        idx += 1

    return macros, env_aliases


def collect_definitions(files: list[Path]) -> tuple[dict[str, MacroDefinition], dict[str, str]]:
    macro_defs: dict[str, MacroDefinition] = {}
    env_aliases: dict[str, str] = {}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        defs, aliases = _collect_definitions(text)
        macro_defs.update(defs)
        env_aliases.update(aliases)
    return macro_defs, env_aliases


def register_env_aliases(env_aliases: dict[str, str]) -> None:
    if not env_aliases:
        return
    from theorem_extractor import register_environment_aliases

    register_environment_aliases(env_aliases)


def _resolve_include(current_path: Path, include_arg: str, source_root: Path) -> Path | None:
    candidate = include_arg.strip()
    if not candidate:
        return None
    paths: list[Path] = []
    if candidate.endswith((".tex", ".sty", ".cls", ".def")):
        paths.append((current_path.parent / candidate).resolve())
    else:
        paths.append((current_path.parent / candidate).with_suffix(".tex").resolve())
        paths.append((current_path.parent / candidate).resolve())
    for path in paths:
        try:
            path.relative_to(source_root)
        except Exception:
            continue
        if path.exists():
            return path
    return None


def expand_include_tree(
    text: str,
    *,
    current_path: Path,
    source_root: Path,
    seen: set[Path] | None = None,
) -> str:
    seen = seen or set()
    current_path = current_path.resolve()
    if current_path in seen:
        return ""
    seen.add(current_path)

    def _replace(match: re.Match[str]) -> str:
        include_path = _resolve_include(current_path, match.group(1), source_root)
        if include_path is None:
            return match.group(0)
        try:
            include_text = include_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return match.group(0)
        return expand_include_tree(
            include_text,
            current_path=include_path,
            source_root=source_root,
            seen=seen,
        )

    return _INPUT_RE.sub(_replace, text)


def _render_macro(node: LatexMacroNode, macro_defs: dict[str, MacroDefinition], source_text: str) -> str:
    spec = macro_defs.get(node.macroname)
    if spec is None:
        return node.latex_verbatim()

    args: list[str] = []
    for arg_node in getattr(node.nodeargd, "argnlist", []) or []:
        if arg_node is None:
            args.append("")
            continue
        args.append(expand_latex_macros(arg_node.latex_verbatim(), macro_defs))

    rendered = _substitute_template(spec.replacement, args)
    if rendered == spec.replacement:
        rendered = expand_latex_macros(rendered, macro_defs)
    return rendered


def _render_node(node, source_text: str, macro_defs: dict[str, MacroDefinition]) -> str:
    if isinstance(node, LatexCharsNode):
        return node.chars
    if isinstance(node, LatexCommentNode):
        return node.latex_verbatim()
    if isinstance(node, LatexMacroNode):
        return _render_macro(node, macro_defs, source_text)
    if isinstance(node, LatexGroupNode):
        inner = _render_nodes(node.nodelist or [], source_text, macro_defs)
        left, right = node.delimiters
        return f"{left}{inner}{right}"
    if isinstance(node, LatexEnvironmentNode):
        children = node.nodelist or []
        if not children:
            return node.latex_verbatim()
        body = _render_nodes(children, source_text, macro_defs)
        start = node.pos
        body_start = min(child.pos for child in children)
        body_end = max(child.pos + child.len for child in children)
        prefix = source_text[start:body_start]
        suffix = source_text[body_end : start + node.len]
        return f"{prefix}{body}{suffix}"
    return node.latex_verbatim() if hasattr(node, "latex_verbatim") else str(node)


def _render_nodes(nodes, source_text: str, macro_defs: dict[str, MacroDefinition]) -> str:
    if not nodes:
        return ""
    out: list[str] = []
    cursor = nodes[0].pos if hasattr(nodes[0], "pos") else 0
    for node in nodes:
        node_start = getattr(node, "pos", cursor)
        node_end = node_start + getattr(node, "len", 0)
        if node_start > cursor:
            out.append(source_text[cursor:node_start])
        out.append(_render_node(node, source_text, macro_defs))
        cursor = max(cursor, node_end)
    return "".join(out)


def expand_latex_macros(text: str, macro_defs: dict[str, MacroDefinition]) -> str:
    if not text.strip() or not macro_defs:
        return text
    walker = LatexWalker(text)
    nodes, _pos, _len = walker.get_latex_nodes()
    return _render_nodes(nodes, text, macro_defs)


def collect_root_tex_paths(tex_paths: list[Path], main_tex: Path | None = None) -> list[Path]:
    tex_set = {p.resolve() for p in tex_paths}
    if not tex_set:
        return []
    main = (main_tex or next(iter(tex_set))).resolve()
    source_root = main.parent

    try:
        main_text = main.read_text(encoding="utf-8", errors="replace")
    except OSError:
        main_text = ""

    seen: set[Path] = set()
    expanded = expand_include_tree(main_text, current_path=main, source_root=source_root, seen=seen)
    # Roots: main file plus any tex files not in the include tree.
    roots = [main]
    for path in sorted(tex_set):
        if path == main or path in seen:
            continue
        roots.append(path)
    return roots


def write_expanded_roots(
    *,
    root_tex_paths: list[Path],
    source_root: Path,
    output_root: Path,
    macro_defs: dict[str, MacroDefinition],
) -> list[Path]:
    output_root.mkdir(parents=True, exist_ok=True)
    expanded_paths: list[Path] = []
    for root in root_tex_paths:
        try:
            raw = root.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        expanded_includes = expand_include_tree(raw, current_path=root, source_root=source_root)
        expanded = expand_latex_macros(expanded_includes, macro_defs)
        out_path = output_root / root.name
        out_path.write_text(expanded, encoding="utf-8")
        expanded_paths.append(out_path)
    return expanded_paths