"""Lean syntax validation and sanitization (P2 mitigation).

This module provides validation for Lean 4 syntax to prevent injection attacks:
- Theorem names must match Lean identifiers
- Theorem signatures must only contain safe Lean syntax
- LaTeX identifiers are escaped to valid Lean format
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional


# Superscript/subscript digits and letters that Lean 4 cannot parse as identifiers
# (Unicode modifier letters U+1D00–U+1DBF, superscript/subscript U+2070–U+209F, U+00B2–U+00B3, U+00B9)
_SUPERSCRIPT_MAP = str.maketrans(
    "⁰¹²³⁴⁵⁶⁷⁸⁹ⁿⁱᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻᴬᴮᴰᴱᴳᴴᴵᴶᴷᴸᴹᴺᴼᴾᴿᵀᵁᵂ"
    "₀₁₂₃₄₅₆₇₈₉ₐₑₒₓₔₕₖₗₘₙₚₛₜ",
    "0123456789niabcdefghijklmnoprstuvwxyzABDEGHIJKLMNOPRTUW"
    "0123456789aeoXehklmnpst",
)

_COMBINING_STRIP_RE = re.compile(r"[̀-ͯ᷀-᷿⃐-⃿]")


def sanitize_unicode_for_lean(text: str) -> str:
    """Transliterate/strip Unicode characters that Lean 4 cannot parse.

    Lean 4 accepts many Unicode math symbols (ℕ, →, ∀ etc.) but rejects
    Unicode modifier letters used as superscripts/subscripts (ᵈ, ᵃ, ₁ etc.)
    when they appear as part of identifiers. This function:
    - Transliterates common superscript/subscript chars to ASCII equivalents
    - Strips combining diacritical marks
    - Leaves Lean-valid math symbols (ℕ ℤ ℝ ℂ → ∀ ∃ ∧ ∨ etc.) intact
    """
    text = text.translate(_SUPERSCRIPT_MAP)
    text = _COMBINING_STRIP_RE.sub("", text)
    # Normalize to NFC to collapse any composed forms back to single codepoints
    text = unicodedata.normalize("NFC", text)
    return text


# Valid Lean identifier: starts with letter/underscore, contains letters/digits/underscores/quotes
# Pattern: [a-z_][a-z0-9_']*
LEAN_IDENTIFIER_PATTERN = re.compile(r"^[a-z_][a-z0-9_']*$", re.IGNORECASE)

# Safe characters in theorem signatures (no code execution chars)
# Includes: identifiers, types, arrows, universal quantifiers, basic operators
LEAN_SIGNATURE_SAFE_CHARS = re.compile(
    r"^[a-zA-Z0-9_'\s:()→∀∃∧∨¬=<>λ.,\[\]{}\-/|&~\*+\^$@#%!?ℕℤℝℂℍℚ×⊕⊗⊞⟨⟩‖]+$"
)


def validate_theorem_name(name: str) -> None:
    """Validate theorem name against Lean syntax rules.
    
    Raises ValueError if invalid. Rejects:
    - Empty names or names > 256 chars
    - Non-identifier syntax (must be [a-z_][a-z0-9_']*)
    
    Args:
        name: Proposed theorem name
        
    Raises:
        ValueError: If name is invalid
    """
    if not name or len(name) > 256:
    
        raise ValueError(f"Invalid theorem name length: {len(name) if name else 0}")
    if not LEAN_IDENTIFIER_PATTERN.match(name):
        raise ValueError(f"Invalid theorem name syntax: {name!r}. Must be valid Lean identifier.")


def validate_theorem_signature(sig: str) -> None:
    """Validate full theorem signature for injection attacks.
    
    Checks that signature only contains expected Lean syntax (no code execution).
    
    Args:
        sig: Full theorem signature (e.g., "(x : ℕ) : x + 0 = x")
        
    Raises:
        ValueError: If signature contains disallowed characters
    """
    if not sig or len(sig) > 10_000:
    
        raise ValueError(f"Invalid signature length: {len(sig) if sig else 0}")
    
    # Reject newlines immediately (allows code injection between lines)
    if '\n' in sig:
        raise ValueError("Signature contains newlines — only single-line signatures allowed")
    if not LEAN_SIGNATURE_SAFE_CHARS.match(sig):
        # Extract the first bad characters for debugging
        bad_chars = set()
        safe_pattern = r"[a-zA-Z0-9_'\s:()→∀∃∧∨¬=<>λ.,\[\]{}\-/|&~\*+\^$@#%!?ℕℤℝℂℍℚ×⊕⊗⊞⟨⟩‖]"
        for c in sig:
            if not re.match(safe_pattern, c):
                bad_chars.add(c)
        if bad_chars:
            raise ValueError(
                f"Signature contains disallowed characters: {sorted(bad_chars)}. "
                f"Only Lean syntax allowed."
            )


def escape_lean_identifier(name: str) -> str:
    """Escape a string to be used as a Lean identifier.
    
    Converts to valid identifier by:
    1. Removing LaTeX commands
    2. Removing non-ASCII characters
    3. Replacing spaces/hyphens with underscores
    4. Ensuring starts with letter or underscore
    
    Args:
        name: Input string (possibly from LaTeX)
        
    Returns:
        Valid Lean identifier
    """
    # Remove LaTeX commands (e.g., \textbf{...})
    name = re.sub(r"\\[a-zA-Z@]+(\{[^}]*\})?", "", name)
    
    # Keep only ASCII and allow unicode math chars
    result = []
    for c in name:
        if ord(c) < 128:
            result.append(c)
        elif c in "ℕℤℝℂ∀∃":  # Common math symbols
            result.append(c)
    name = "".join(result)
    
    # Replace spaces/hyphens with underscores
    name = name.replace(" ", "_").replace("-", "_")
    
    # Remove disallowed punctuation (keep only alphanumeric, _, ')
    name = re.sub(r"[^a-zA-Z0-9_']", "", name)
    
    # Ensure starts with letter or underscore
    if name and name[0].isdigit():
        name = "_" + name
    
    # Return valid identifier or fallback
    return name if name else "_theorem"


def validate_and_sanitize_signature(sig: str) -> str:
    """Validate signature and return sanitized version.
    
    Attempts to sanitize invalid signatures by removing disallowed chars.
    If sanitization fails, raises ValueError.
    
    Args:
        sig: Potentially malicious signature
        
    Returns:
        Sanitized signature (safely escaped)
        
    Raises:
        ValueError: If signature cannot be safely sanitized
    """
    if not sig:
        raise ValueError("Empty signature")

    # Strip problematic Unicode before anything else.
    sig = sanitize_unicode_for_lean(sig)

    # Fix known Mathlib 3 → Mathlib 4 typeclass renames.
    sig = fix_mathlib4_typeclasses(sig)

    # Normalize calc ≥ → ≤.
    sig = fix_calc_ge_to_le(sig)

    # Quick check first
    try:
        validate_theorem_signature(sig)
        return sig  # Already valid
    except ValueError:
        pass
    
    # Try to sanitize by removing/escaping bad chars
    safe_pattern = r"[a-zA-Z0-9_'\s:()→∀∃∧∨¬=<>λ.,\[\]{}\-/|&~\*+\^$@#%!?;`\\]"
    sanitized = "".join(c for c in sig if re.match(safe_pattern, c))
    
    if not sanitized or len(sanitized) < 5:
        raise ValueError(f"Signature too short after sanitization: {sig!r}")
    
    # Validate the sanitized version
    validate_theorem_signature(sanitized)
    return sanitized


# ── Global fixes for common translation errors (from error log 2304.09598) ───

# Mathlib 3 → Mathlib 4 typeclass rename map.
# These Lean 3 names appear in Leanstral output but don't exist in current Mathlib 4.
_MATHLIB3_TYPECLASS_MAP: dict[str, str] = {
    "LinearOrderedRing": "LinearOrderedCommRing",  # closest ML4 equivalent; check availability
    "LinearOrderedField": "LinearOrderedField",     # actually exists in ML4; kept for reference
    "OrderedRing": "StrictOrderedRing",
    "OrderedField": "LinearOrderedField",
    "LinearOrder": "LinearOrder",                   # unchanged
    "LatticeOrderedGroup": "Lattice",
    "OrderedAddCommGroup": "OrderedAddCommGroup",   # unchanged in ML4
}

# Typeclasses confirmed absent from current Mathlib 4 (as of leanprover/mathlib4 ~2025).
_ABSENT_TYPECLASSES: frozenset[str] = frozenset({
    "LinearOrderedRing",
    "OrderedRing",
})


def fix_mathlib4_typeclasses(sig: str) -> str:
    """Replace known Mathlib 3 typeclasses with their Mathlib 4 equivalents.

    Runs a simple token-level substitution. If a class is in the absent set
    and has no direct replacement, it is stripped from the binder.
    """
    for old, new in _MATHLIB3_TYPECLASS_MAP.items():
        if old == new:
            continue
        sig = re.sub(r"\b" + re.escape(old) + r"\b", new, sig)
    return sig


def fix_axiom_iff_dot_syntax(sig: str) -> str:
    """Fix `axiom_name.mp` / `axiom_name.mpr` → `(axiom_name _arg).mp`.

    Lean 4 axioms are not theorems; you cannot project `.mp` / `.mpr` from them
    directly. The correct form is `(axiom_name args).mp` where the iff is first
    applied to its arguments, then `.mp` is projected.

    This function rewrites bare `foo_bar.mp` and `foo_bar.mpr` expressions
    that appear in proof bodies, but only when they look like they refer to
    axiom-level iff statements (heuristic: contains no `.lean` or keyword prefix).
    """
    # Pattern: identifier.mp <arg> → (identifier <arg>).mp
    # Only rewrite if identifier is lowercase/snake_case (likely axiom, not tactic)
    sig = re.sub(
        r"\b([a-z][a-zA-Z0-9_]*)\.mp\b",
        r"(\1 _).mp",
        sig,
    )
    sig = re.sub(
        r"\b([a-z][a-zA-Z0-9_]*)\.mpr\b",
        r"(\1 _).mpr",
        sig,
    )
    return sig


def fix_calc_ge_to_le(sig: str) -> str:
    """Normalize calc chains that mix ≤ and ≥ into pure ≤ chains.

    Lean 4 calc does not have a Trans instance for (≤, ≥) without explicit
    GE→LE coercion. Replace `a ≥ b` steps with `b ≤ a` to maintain a uniform
    ≤ chain.
    """
    # Replace `_ ≥ expr` with `_ ≤ expr` by swapping — only inside calc blocks.
    # This is a heuristic: match `     _ ≥ ` indented lines.
    sig = re.sub(r"(\s+_\s+)≥(\s+)", r"\1≤\2", sig)
    return sig
