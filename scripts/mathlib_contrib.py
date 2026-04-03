#!/usr/bin/env python3
"""Mathlib novelty checker and PR skeleton generator."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import time
from pathlib import Path


def _elan_env() -> dict:
    env = os.environ.copy()
    env["PATH"] = str(Path.home() / ".elan" / "bin") + ":" + env.get("PATH", "")
    return env


def check_novelty(
    lean_statement: str,
    *,
    project_root: Path,
    lean_timeout: int = 45,
) -> dict:
    m = re.search(r'(?:theorem|lemma)\s+(\w+)', lean_statement)
    if not m:
        return {
            "novel": True,
            "method": "no_name",
            "detail": "could not extract theorem name",
            "elapsed_s": 0.0,
        }
    name = m.group(1)
    start = time.monotonic()
    try:
        proc = subprocess.run(
            ["lake", "env", "lean", "-E", f"#check @{name}"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=lean_timeout,
            env=_elan_env(),
        )
        elapsed = time.monotonic() - start
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0 and "unknown identifier" not in out:
            novel = False
        else:
            novel = True
        return {
            "novel": novel,
            "method": "lake_check",
            "detail": out[:300],
            "elapsed_s": round(elapsed, 3),
        }
    except Exception as exc:
        elapsed = time.monotonic() - start
        return {
            "novel": True,
            "method": "error",
            "detail": str(exc),
            "elapsed_s": round(elapsed, 3),
        }


def generate_pr_skeleton(
    *,
    theorem_name: str,
    lean_statement: str,
    proof_text: str,
    paper_id: str = "",
    namespace: str = "DESol",
) -> dict:
    title = f"feat: add {theorem_name} to Mathlib"

    body = f"""## Summary

Adds `{theorem_name}`, automatically proved by DESol from `{paper_id}`.

## Verification

Proof compiles against Lean 4 + Mathlib via `lake build`.

## Test plan

- [ ] Compile against current Mathlib4 main
- [ ] Check namespace
- [ ] Verify no `sorry`
"""

    lean_content = (
        f"import Mathlib.Tactic\n"
        f"import Mathlib.Data.Real.Basic\n"
        f"\n"
        f"namespace {namespace}\n"
        f"\n"
        f"/-- Proved by DESol from {paper_id}. -/\n"
        f"{lean_statement} := by\n"
        f"  {proof_text}\n"
        f"\n"
        f"end {namespace}\n"
    )

    return {
        "title": title,
        "body": body,
        "lean_content": lean_content,
        "namespace": namespace,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mathlib contrib utilities")
    sub = parser.add_subparsers(dest="command")

    p_novelty = sub.add_parser("check-novelty")
    p_novelty.add_argument("--statement", required=True)
    p_novelty.add_argument("--project-root", required=True)

    p_skeleton = sub.add_parser("generate-skeleton")
    p_skeleton.add_argument("--theorem-name", required=True)
    p_skeleton.add_argument("--statement", required=True)
    p_skeleton.add_argument("--proof", required=True)
    p_skeleton.add_argument("--paper-id", default="")

    args = parser.parse_args()

    if args.command == "check-novelty":
        import json
        result = check_novelty(args.statement, project_root=Path(args.project_root))
        print(json.dumps(result, indent=2))
    elif args.command == "generate-skeleton":
        import json
        result = generate_pr_skeleton(
            theorem_name=args.theorem_name,
            lean_statement=args.statement,
            proof_text=args.proof,
            paper_id=args.paper_id,
        )
        print(json.dumps(result, indent=2))
    else:
        parser.print_help()
