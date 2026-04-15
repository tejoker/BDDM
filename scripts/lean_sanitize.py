"""Shared sanitization helpers for Lean source generation."""

from __future__ import annotations

import re


def escape_lean_comment(text: str, *, max_len: int = 1000) -> str:
    """Sanitize untrusted text before embedding in Lean line comments.

    Removes LaTeX control sequences and non-printable/newline content so
    metadata cannot escape comment context in generated `.lean` files.
    """
    if not text:
        return ""

    # Drop LaTeX commands like \foo or \foo{...} that can add noisy payloads.
    text = re.sub(r"\\[a-zA-Z@]+(?:\{[^}]*\})?", "", text)

    # Flatten to single line (line comments are newline-sensitive).
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")

    # Keep printable ASCII only; trim to predictable bound.
    text = "".join(ch for ch in text if 32 <= ord(ch) <= 126)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]
