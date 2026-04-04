#!/usr/bin/env python3
"""Post-proof import validation: verify the proven statement matches the original extraction.

Problem this solves:
  REPLDojo writes a temp .lean file and runs `lake build`. If the imports in that file
  differ from what the paper's theorem actually needs (wrong namespace, missing instance,
  different universe level), the proof can close a *different* type than the one extracted.
  This script performs a second-pass check: after a proof is reported as closed, re-run
  `#check` on the *original* extracted statement in a clean temp file and verify that
  the resulting type is definitionally equal to what was proven.

Usage (standalone):
    python scripts/import_validator.py \\
        --statement "theorem foo : Nat.Prime 7" \\
        --proof "by decide" \\
        --project-root . \\
        --imports "import Mathlib.Data.Nat.Prime"

Usage (programmatic):
    from import_validator import validate_proof_imports, ImportValidationResult
    result = validate_proof_imports(
        lean_statement=stmt,
        proof_text=proof,
        project_root=Path("."),
        imports=imports,
        lean_timeout=30,
    )
    if not result.ok:
        # downgrade ledger status
        ...
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_DEFAULT_IMPORTS = "import Mathlib"

_ELAN_LEAN = os.environ.get("LEAN_BIN", "lean")


def _elan_env(project_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    elan = Path.home() / ".elan" / "bin"
    if elan.exists():
        env["PATH"] = str(elan) + os.pathsep + env.get("PATH", "")
    env["LAKE_HOME"] = str(project_root)
    return env


@dataclass
class ImportValidationResult:
    ok: bool
    method: str          # "check_succeeded" | "type_mismatch" | "elaborate_failed" | "timeout"
    detail: str
    elapsed_s: float


def _build_check_file(lean_statement: str, proof_text: str, imports: str) -> str:
    """Build a minimal .lean file that checks both the statement and the proof."""
    proof_line = proof_text.strip()
    # Ensure the proof is wrapped in `by ...` if it isn't already.
    if not proof_line.startswith("by ") and not proof_line.startswith("·") and "\n" not in proof_line:
        proof_line = f"by {proof_line}"
    return f"{imports}\n\n#check ({lean_statement} := {proof_line})\n"


def _build_elaborate_file(lean_statement: str, imports: str) -> str:
    """Build a minimal .lean file that elaborates just the statement."""
    return f"{imports}\n\n#check @({lean_statement})\n"


def validate_proof_imports(
    *,
    lean_statement: str,
    proof_text: str,
    project_root: Path,
    imports: str = _DEFAULT_IMPORTS,
    lean_timeout: int = 30,
) -> ImportValidationResult:
    """Verify that the proven statement elaborates correctly under its declared imports.

    Two-phase check:
    1. Elaborate the statement alone — confirms the type is well-formed with these imports.
    2. Elaborate the statement + proof together — confirms the proof closes the right type.

    If phase 2 fails but phase 1 passes: type_mismatch (proof closes wrong goal).
    If phase 1 fails: elaborate_failed (statement itself broken with these imports).
    """
    import time

    env = _elan_env(project_root)
    tmp_dir = Path(tempfile.mkdtemp(prefix="desol_iv_"))
    uid = uuid.uuid4().hex[:8]

    t0 = time.time()
    try:
        # Phase 1: elaborate statement alone.
        elab_file = tmp_dir / f"_iv_elab_{uid}.lean"
        elab_file.write_text(_build_elaborate_file(lean_statement, imports), encoding="utf-8")
        try:
            r1 = subprocess.run(
                [_ELAN_LEAN, "--stdin"],
                input=elab_file.read_text(encoding="utf-8"),
                capture_output=True,
                text=True,
                timeout=lean_timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return ImportValidationResult(ok=False, method="timeout", detail="phase1 timeout", elapsed_s=time.time() - t0)

        if r1.returncode != 0:
            return ImportValidationResult(
                ok=False,
                method="elaborate_failed",
                detail=f"Statement does not elaborate: {(r1.stderr or r1.stdout)[:300]}",
                elapsed_s=time.time() - t0,
            )

        if not proof_text.strip():
            # No proof to check — statement-only validation passes.
            return ImportValidationResult(
                ok=True,
                method="check_succeeded",
                detail="statement elaborates; no proof provided",
                elapsed_s=time.time() - t0,
            )

        # Phase 2: elaborate statement + proof together.
        check_file = tmp_dir / f"_iv_check_{uid}.lean"
        check_file.write_text(_build_check_file(lean_statement, proof_text, imports), encoding="utf-8")
        try:
            r2 = subprocess.run(
                [_ELAN_LEAN, "--stdin"],
                input=check_file.read_text(encoding="utf-8"),
                capture_output=True,
                text=True,
                timeout=lean_timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return ImportValidationResult(ok=False, method="timeout", detail="phase2 timeout", elapsed_s=time.time() - t0)

        elapsed = time.time() - t0
        if r2.returncode == 0:
            return ImportValidationResult(ok=True, method="check_succeeded", detail="", elapsed_s=elapsed)

        return ImportValidationResult(
            ok=False,
            method="type_mismatch",
            detail=f"Proof does not close declared statement: {(r2.stderr or r2.stdout)[:300]}",
            elapsed_s=elapsed,
        )
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


def validate_ledger_entry(
    entry: dict,
    *,
    project_root: Path,
    imports: str = _DEFAULT_IMPORTS,
    lean_timeout: int = 30,
) -> dict:
    """Run import validation on a ledger entry dict. Returns updated entry.

    If validation fails, downgrades status to UNRESOLVED and sets
    failure_origin=IMPORT_MISMATCH.
    """
    stmt = entry.get("lean_statement", "")
    proof = entry.get("proof_text", "")
    status = entry.get("status", "UNRESOLVED")

    if status != "FULLY_PROVEN" or not stmt or not proof:
        return entry

    result = validate_proof_imports(
        lean_statement=stmt,
        proof_text=proof,
        project_root=project_root,
        imports=imports,
        lean_timeout=lean_timeout,
    )
    entry = dict(entry)
    entry.setdefault("validation_gates", {})
    entry["validation_gates"]["import_validated"] = result.ok
    entry["validation_gates"]["import_method"] = result.method

    if not result.ok:
        logger.warning(
            "Import validation FAILED for %s: %s",
            entry.get("theorem_name", "?"),
            result.detail,
        )
        entry["status"] = "UNRESOLVED"
        entry["failure_origin"] = "IMPORT_MISMATCH"
        entry["error_message"] = result.detail

    return entry


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Post-proof import validation for Lean 4 theorems")
    p.add_argument("--statement", required=True, help="Lean 4 theorem statement")
    p.add_argument("--proof", default="", help="Proof text (optional)")
    p.add_argument("--project-root", default=".", help="Lean project root")
    p.add_argument("--imports", default=_DEFAULT_IMPORTS, help="Lean import block")
    p.add_argument("--lean-timeout", type=int, default=30)
    return p


def main() -> None:
    args = _build_parser().parse_args()
    result = validate_proof_imports(
        lean_statement=args.statement,
        proof_text=args.proof,
        project_root=Path(args.project_root),
        imports=args.imports,
        lean_timeout=args.lean_timeout,
    )
    print(f"ok={result.ok}  method={result.method}  elapsed={result.elapsed_s:.2f}s")
    if result.detail:
        print(f"detail: {result.detail}")
    sys.exit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
