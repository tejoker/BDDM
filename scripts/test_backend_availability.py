#!/usr/bin/env python3
"""
Diagnostic tool to debug LeanDojo backend availability.

Checks:
1. LeanDojo package imports
2. Required classes (Dojo, Theorem, TacticState)
3. Git connectivity to mathlib4
4. Actual repo tracing (the slowest check)

Exit codes:
- 0: All checks passed, backend is fully available
- 1: Import OK but tracing failed (likely git/network issue)
- 2: Import failed (LeanDojo not installed)
"""

import sys
import subprocess
from pathlib import Path


def check_import():
    """Check if lean_dojo package can be imported."""
    try:
        import lean_dojo
        print("[✓] lean_dojo package imports successfully")
        return True
    except ImportError as e:
        print(f"[✗] lean_dojo import failed: {e}")
        print("  Install via: pip install lean-dojo")
        return False


def check_classes():
    """Check if required classes are accessible."""
    try:
        from lean_dojo import Dojo, Theorem, TacticState
        print("[✓] Dojo, Theorem, TacticState classes available")
        return True
    except (ImportError, AttributeError) as e:
        print(f"[✗] Class import failed: {e}")
        return False


def check_git_connectivity():
    """Check git access to mathlib4."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", "https://github.com/leanprover/mathlib4.git", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            print("[✓] Git connectivity to mathlib4 OK")
            return True
        else:
            print(f"[✗] Git connectivity failed with code {result.returncode}")
            print(f"  stderr: {result.stderr[:200]}")
            return False
    except Exception as e:
        print(f"[✗] Git check failed: {e}")
        return False


def check_repo_tracing():
    """Attempt actual repo tracing (the expensive check)."""
    print("\n[*] Testing actual repo tracing (this may take ~30s)...")
    try:
        from lean_dojo import Repo
        print("[*] Attempting to clone/prepare mathlib4...")
        repo = Repo.from_github("leanprover/mathlib4", "master", timeout=60)
        print(f"[✓] Repo tracing successful, repo at: {repo.mathlib_root}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[✗] Repo tracing failed with CalledProcessError: {e}")
        if e.stderr:
            print(f"  stderr preview: {str(e.stderr)[:500]}")
        return False
    except Exception as e:
        print(f"[✗] Repo tracing failed: {type(e).__name__}: {e}")
        return False


def main():
    print("=" * 60)
    print("LeanDojo Backend Availability Diagnostic")
    print("=" * 60 + "\n")

    # Fast checks
    if not check_import():
        print("\n[RESULT] Backend unavailable: LeanDojo not installed")
        return 2

    if not check_classes():
        print("\n[RESULT] Backend unavailable: Classes not accessible")
        return 2

    if not check_git_connectivity():
        print("\n[RESULT] Backend partially available: Git connectivity issue")
        print("  Recommendation: Fix git/network access or use --fallback-mode model")
        return 1

    # Slow check (actual tracing)
    if not check_repo_tracing():
        print("\n[RESULT] Backend unavailable: Repo tracing failed")
        print("  Recommendation: Check git/network/GitHub access or use --fallback-mode model")
        return 1

    print("\n" + "=" * 60)
    print("[✓] RESULT: Backend fully available")
    print("=" * 60)
    print("\nProof execution should succeed. To test:")
    print("  python3 scripts/mcts_search.py --theorem <name> --file <path.lean> --mode leandojo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
