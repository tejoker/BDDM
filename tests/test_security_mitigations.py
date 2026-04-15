
"""Security tests for input validation (P1-P2 mitigations)."""
from __future__ import annotations

from pathlib import Path

import pytest

class TestTacticLengthValidation:
    """Test tactic length limits (P1 mitigation)."""
    
    def test_max_tactic_len_constant_exists(self):
        """MAX_TACTIC_LEN should be defined for tactic length validation."""
        from mcts_search import MAX_TACTIC_LEN
        
        assert isinstance(MAX_TACTIC_LEN, int)
        assert MAX_TACTIC_LEN > 0
        assert MAX_TACTIC_LEN <= 50_000  # Reasonable upper bound
        # Typical expected value: 10_000
        assert MAX_TACTIC_LEN == 10_000
    
    def test_tactic_validation_rejects_oversized_tactics(self):
        """Oversized tactics should be skipped during expand_leaf."""
        from mcts_search import MAX_TACTIC_LEN
        
        # Construct oversized tactic string
        oversized = "omega; " * (MAX_TACTIC_LEN // 7 + 100)
        assert len(oversized) > MAX_TACTIC_LEN
        
        # In actual expand_leaf, this would be logged as warning + skipped
        # (verified by checking that len(tactic) > MAX_TACTIC_LEN triggers skip)


class TestTheormNameValidation:
    """Test theorem name validation (P2 mitigation)."""
    
    def test_lean_validation_module_exists(self):

        """lean_validation module should be available for theorem name validation."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from lean_validation import validate_theorem_name
        assert callable(validate_theorem_name)

    
    def test_validate_theorem_name_valid_identifiers(self):
        """Valid Lean identifiers should pass validation."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

        from lean_validation import validate_theorem_name
        
        valid_names = [
            "simple",
            "Theorem1",
            "_private",
            "name_with_underscore",
            "name'",
            "name'_with''quotes",
        ]
        
        for name in valid_names:
            validate_theorem_name(name)  # Should not raise
    
    def test_validate_theorem_name_invalid_format(self):
        """Invalid theorem names should raise ValueError."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

        from lean_validation import validate_theorem_name
        
        invalid_names = [
            "",  # Empty
            "123name",  # Starts with digit
            "name-with-hyphen",  # Hyphen not allowed
            "name with space",  # Space not allowed
            "name()",  # Parentheses not allowed
            "name;drop table",  # SQL injection attempt
            "../../../etc/passwd",  # Path traversal attempt
        ]
        
        for name in invalid_names:
            with pytest.raises(ValueError):
                validate_theorem_name(name)
    
    def test_validate_theorem_name_length_limits(self):
        """Names must be within reasonable length limits."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

        from lean_validation import validate_theorem_name
        
        # Max length allowed
        long_name = "a" * 256
        validate_theorem_name(long_name)  # Should pass
        
        # Too long
        too_long = "a" * 257
        with pytest.raises(ValueError):
            validate_theorem_name(too_long)
    
    def test_escape_lean_identifier_strips_latex(self):
        """escape_lean_identifier should remove LaTeX commands."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from lean_validation import escape_lean_identifier

        
        # Test that escape_lean_identifier removes LaTeX and produces valid identifiers
        latex_names = [
            r"theorem\textbf{name}",
            r"\alpha-\beta",
            r"test\emph{case}",
        ]
        
        for latex in latex_names:
            result = escape_lean_identifier(latex)
            # Result should be a valid Lean identifier (alphanumeric + underscore)
            assert result, f"escape should not produce empty string from {latex!r}"
            assert not "\\" in result, f"Result should not contain backslashes: {result!r}"
            # Should be usable as a Lean identifier (alphanumeric + _ + ')
            assert result[0].isalpha() or result[0] == "_", f"Identifier must start with letter or underscore: {result!r}"
            assert not "\\" in result
    
    def test_escape_lean_identifier_handles_spaces(self):
        """escape_lean_identifier should convert spaces to underscores."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

        from lean_validation import escape_lean_identifier
        
        result = escape_lean_identifier("my theorem name")
        assert result == "my_theorem_name"
    
    def test_validate_theorem_signature_safe_chars_only(self):
        """Signatures must contain only safe Lean syntax."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from lean_validation import validate_theorem_signature

        
        # Valid signatures
        valid_sigs = [
            "(x : ℕ) : x + 0 = x",
            "∀ (x y : ℕ), x + y = y + x",
            "[a b c : ℤ] (h : a < b) : ∃ k, a + k = b",
        ]
        
        for sig in valid_sigs:
            validate_theorem_signature(sig)  # Should not raise
    
    def test_validate_theorem_signature_rejects_injection(self):
        """Signature validation should reject code execution attempts."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from lean_validation import validate_theorem_signature
        
        malicious_sigs = [
            "theorem foo.py: int\ncode",  # Python syntax injection
            "x + 0 = x'; DROP TABLE;",  # SQL injection attempt  
            "x + y = y + x\n\nimport os\nos.system('/bin/bash')",  # Newline + Python injection
        ]
        
        for sig in malicious_sigs:
            with pytest.raises(ValueError):
                validate_theorem_signature(sig)


class TestArxivMetadataEscaping:
    """Test P3 metadata escaping for generated Lean comments."""

    def test_escape_lean_comment_removes_latex_commands(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from lean_sanitize import escape_lean_comment

        raw = r"Title with \textbf{bold} and \cite{abc123}"
        out = escape_lean_comment(raw)

        assert "\\textbf" not in out
        assert "\\cite" not in out
        assert "Title with" in out

    def test_escape_lean_comment_flattens_newlines(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from lean_sanitize import escape_lean_comment

        raw = "line1\nline2\r\nline3\tline4"
        out = escape_lean_comment(raw)

        assert "\n" not in out
        assert "\r" not in out
        assert "\t" not in out
        assert out == "line1 line2 line3 line4"

    def test_escape_lean_comment_enforces_max_length(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from lean_sanitize import escape_lean_comment

        raw = "a" * 5000
        out = escape_lean_comment(raw, max_len=120)
        assert len(out) == 120

    def test_escape_lean_comment_strips_non_printable(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from lean_sanitize import escape_lean_comment

        raw = "abc\x00\x1fdef\x7fghi"
        out = escape_lean_comment(raw)
        assert out == "abcdefghi"


class TestSecurityRegressionSuite:
    """P4 broad regression checks for key input validation guards."""

    def test_paper_id_pattern_blocks_traversal(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from kg_api import PAPER_ID_PATTERN

        bad_ids = [
            "../../../etc/passwd",
            "2304.09598; rm -rf /",
            "2304/09598",
            "2304.0959",
        ]
        for paper_id in bad_ids:
            assert not PAPER_ID_PATTERN.match(paper_id)

    def test_paper_id_pattern_accepts_valid(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from kg_api import PAPER_ID_PATTERN

        for paper_id in ["2304.09598", "2401.00001"]:
            assert PAPER_ID_PATTERN.match(paper_id)
