"""Shared pytest fixtures for DESol tests."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure scripts/ is importable without installation.
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
