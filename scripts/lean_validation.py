"""Lean syntax validation and sanitization (P2 mitigation).

This module provides validation for Lean 4 syntax to prevent injection attacks:
- Theorem names must match Lean identifiers
- Theorem signatures must only contain safe Lean syntax
- LaTeX identifiers are escaped to valid Lean format
"""

from __future__ import annotations

import re
from typing import Optional


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
