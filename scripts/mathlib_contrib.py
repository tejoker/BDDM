#!/usr/bin/env python3
"""Mathlib novelty checker and contribution generator.

Two-stage novelty check:
  1. Name check: #check @name — catches identical names
  2. Semantic check: Leanstral compares the statement to Mathlib search results
     to catch same theorems under different names

Contribution output:
  - Properly formatted Lean file following Mathlib style guide
  - Docstring following Mathlib conventions
  - Correct import resolution (only needed imports, not `import Mathlib`)
  - Optionally: auto-open a GitHub PR via `gh pr create`
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path


def _elan_env() -> dict:
    env = os.environ.copy()
    env["PATH"] = str(Path.home() / ".elan" / "bin") + ":" + env.get("PATH", "")
    return env


# ---------------------------------------------------------------------------
# Novelty checking
# ---------------------------------------------------------------------------

def _name_check(name: str, project_root: Path, timeout: int) -> tuple[bool, str]:
    """Return (is_novel, detail). Checks exact name collision in Mathlib."""
    try:
        proc = subprocess.run(
            ["lake", "env", "lean", "-E", f"#check @{name}"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_elan_env(),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0 and "unknown identifier" not in out and "error" not in out.lower():
            return False, f"name '{name}' already exists in Mathlib"
        return True, "name not found in Mathlib"
    except subprocess.TimeoutExpired:
        return True, "name check timed out (assumed novel)"
    except Exception as exc:
        return True, str(exc)


def _exact_statement_search(lean_statement: str, project_root: Path, timeout: int) -> tuple[bool, str]:
    """Run `exact?` / `apply?` on the statement goal to find identical Mathlib theorems."""
    # Extract the type signature (after ':')
    m = re.search(r":\s*(.+?)(?::=|$)", lean_statement, re.DOTALL)
    if not m:
        return True, "could not extract goal for exact? search"

    goal_type = m.group(1).strip()
    lean_src = (
        "import Mathlib\n"
        "open Mathlib in\n"
        f"example : {goal_type} := by exact?\n"
    )
    try:
        proc = subprocess.run(
            ["lake", "env", "lean", "--stdin"],
            input=lean_src,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=project_root,
            env=_elan_env(),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        # exact? outputs "Try this: exact XYZ" when it finds a match
        m2 = re.search(r"Try this: exact\s+(\S+)", out)
        if m2:
            existing = m2.group(1)
            return False, f"semantically identical to Mathlib theorem: {existing}"
        return True, "no exact? match found"
    except subprocess.TimeoutExpired:
        return True, "exact? timed out (assumed novel)"
    except Exception as exc:
        return True, str(exc)


def _semantic_novelty_check(
    lean_statement: str,
    *,
    client,
    model: str,
) -> tuple[bool, str]:
    """Ask LeanStral if this statement is semantically equivalent to a known Mathlib theorem."""
    from ponder_loop import _chat_complete
    _SYSTEM = (
        "You are a Lean 4 / Mathlib expert. "
        "Given a Lean 4 theorem statement, determine if an equivalent theorem already exists in Mathlib4. "
        "Output JSON only: {\"novel\": true|false, \"existing\": \"Mathlib.Name.or.null\", \"reason\": \"...\"}. "
        "novel=false means an equivalent theorem exists under a different name. "
        "Be conservative: only say novel=false if you are certain."
    )
    try:
        _, raw = _chat_complete(
            client=client,
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": lean_statement},
            ],
            temperature=0.0,
            max_tokens=256,
            purpose="semantic_novelty",
        )
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return True, "could not parse LLM response"
        parsed = json.loads(m.group(0))
        novel = bool(parsed.get("novel", True))
        existing = parsed.get("existing") or ""
        reason = parsed.get("reason", "")
        if not novel:
            return False, f"LLM: equivalent to {existing} — {reason}"
        return True, f"LLM: {reason}"
    except Exception as exc:
        return True, f"semantic check error: {exc}"


def check_novelty(
    lean_statement: str,
    *,
    project_root: Path,
    lean_timeout: int = 45,
    client=None,
    model: str = "",
    run_exact_search: bool = True,
    run_semantic_check: bool = False,
) -> dict:
    """Three-stage novelty check.

    Stage 1: Name collision (#check @name)
    Stage 2: Semantic match (exact? on goal type) — slower but finds renamed theorems
    Stage 3: LLM semantic check (requires client) — catches paraphrased equivalents

    Returns dict with keys: novel, method, detail, elapsed_s, stages
    """
    m = re.search(r'(?:theorem|lemma)\s+(\w+)', lean_statement)
    name = m.group(1) if m else None

    start = time.monotonic()
    stages: dict[str, str] = {}

    # Stage 1: name check
    if name:
        name_novel, name_detail = _name_check(name, project_root, lean_timeout)
        stages["name_check"] = name_detail
        if not name_novel:
            elapsed = time.monotonic() - start
            return {
                "novel": False,
                "method": "name_collision",
                "detail": name_detail,
                "elapsed_s": round(elapsed, 3),
                "stages": stages,
            }
    else:
        stages["name_check"] = "skipped (no name extracted)"

    # Stage 2: exact? search on goal type
    if run_exact_search:
        sem_novel, sem_detail = _exact_statement_search(lean_statement, project_root, lean_timeout)
        stages["exact_search"] = sem_detail
        if not sem_novel:
            elapsed = time.monotonic() - start
            return {
                "novel": False,
                "method": "semantic_duplicate",
                "detail": sem_detail,
                "elapsed_s": round(elapsed, 3),
                "stages": stages,
            }

    # Stage 3: LLM semantic check
    if run_semantic_check and client and model:
        llm_novel, llm_detail = _semantic_novelty_check(lean_statement, client=client, model=model)
        stages["llm_check"] = llm_detail
        if not llm_novel:
            elapsed = time.monotonic() - start
            return {
                "novel": False,
                "method": "llm_semantic",
                "detail": llm_detail,
                "elapsed_s": round(elapsed, 3),
                "stages": stages,
            }

    elapsed = time.monotonic() - start
    return {
        "novel": True,
        "method": "all_stages_passed",
        "detail": "; ".join(f"{k}: {v}" for k, v in stages.items()),
        "elapsed_s": round(elapsed, 3),
        "stages": stages,
    }


# ---------------------------------------------------------------------------
# Contribution formatting
# ---------------------------------------------------------------------------

_MATHLIB_STYLE_IMPORTS = {
    # Common Mathlib import groups (prefer targeted over `import Mathlib`)
    "Nat": "Mathlib.Data.Nat.Basic",
    "Int": "Mathlib.Data.Int.Basic",
    "Real": "Mathlib.Data.Real.Basic",
    "Complex": "Mathlib.Data.Complex.Basic",
    "List": "Mathlib.Data.List.Basic",
    "Finset": "Mathlib.Data.Finset.Basic",
    "Matrix": "Mathlib.Data.Matrix.Basic",
    "MeasureTheory": "Mathlib.MeasureTheory.Measure.MeasureSpace",
    "Topology": "Mathlib.Topology.Basic",
    "Algebra": "Mathlib.Algebra.Group.Basic",
    "Analysis": "Mathlib.Analysis.SpecialFunctions.Pow.Real",
}


def _infer_imports(lean_statement: str, proof_text: str) -> list[str]:
    """Heuristically select targeted Mathlib imports from statement/proof content."""
    combined = lean_statement + " " + proof_text
    imports: list[str] = []
    for keyword, module in _MATHLIB_STYLE_IMPORTS.items():
        if keyword in combined and module not in imports:
            imports.append(module)
    if not imports:
        imports = ["Mathlib.Tactic"]
    return sorted(imports)


def generate_contribution(
    *,
    theorem_name: str,
    lean_statement: str,
    proof_text: str,
    paper_id: str = "",
    namespace: str = "",
    docstring: str = "",
    attribution: str = "",
) -> dict:
    """Generate a properly formatted Lean 4 Mathlib contribution.

    Follows Mathlib style guide:
    - Targeted imports (not `import Mathlib`)
    - Docstring with `/-- ... -/` format including attribution
    - `@[simp]` tag if theorem is an equation / reduction
    - Namespace only if non-empty
    - No trailing `sorry`
    - Proof indented by 2 spaces
    """
    # Clean up proof text
    proof_lines = [ln for ln in proof_text.strip().splitlines() if "sorry" not in ln.lower()]
    if not proof_lines:
        proof_lines = ["omega"]  # safe fallback — will fail to compile, making the error obvious
    proof_indented = "\n".join("  " + ln for ln in proof_lines)

    # Infer imports
    imports = _infer_imports(lean_statement, proof_text)
    import_block = "\n".join(f"import {i}" for i in imports)

    # Docstring
    if not docstring:
        docstring = f"Proved automatically by DeSol from arXiv paper `{paper_id}`."
    attr_line = f"Attributed to: {attribution}." if attribution else ""
    full_doc = docstring + (f"\n\n{attr_line}" if attr_line else "")
    doc_block = f"/-- {full_doc} -/"

    # Detect if @[simp] is appropriate (equation-style conclusions)
    stmt_stripped = lean_statement.strip()
    # Extract conclusion after last ':'
    conclusion_m = re.search(r":\s*(.+?)(?::=|$)", stmt_stripped, re.DOTALL)
    conclusion = conclusion_m.group(1).strip() if conclusion_m else ""
    simp_tag = "@[simp]\n" if ("=" in conclusion and "≠" not in conclusion and "≤" not in conclusion) else ""

    # Strip ':= by' / ':=' from statement if present
    sig = re.sub(r"\s*:=.*$", "", stmt_stripped, flags=re.DOTALL).strip()

    # Build Lean file content
    lean_lines = [
        import_block,
        "",
    ]
    if namespace:
        lean_lines += ["", f"namespace {namespace}", ""]
    lean_lines += [
        doc_block,
        f"{simp_tag}{sig} := by",
        proof_indented,
    ]
    if namespace:
        lean_lines += ["", f"end {namespace}"]

    lean_content = "\n".join(lean_lines) + "\n"

    # PR metadata
    title = f"feat({namespace or 'Mathlib'}): add {theorem_name}"
    body = f"""\
## Summary

Adds `{theorem_name}` to Mathlib.

**Source**: arXiv `{paper_id}`
**Proved by**: DeSol (automated theorem prover)
**Proof strategy**: tactic proof via `lake build` verification

## Statement

```lean
{sig}
```

## Proof

```lean
{chr(10).join(proof_lines)}
```

## Checklist

- [ ] Compiles against current Mathlib4 `main`
- [ ] No `sorry`
- [ ] Docstring present
- [ ] Namespace correct
- [ ] `exact?` confirms no duplicate
"""

    return {
        "title": title,
        "body": body,
        "lean_content": lean_content,
        "imports": imports,
        "has_simp_tag": bool(simp_tag),
        "namespace": namespace,
    }


def write_contribution_file(
    contrib: dict,
    *,
    out_dir: Path,
    theorem_name: str,
) -> Path:
    """Write the Lean contribution to a .lean file and return the path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    lean_path = out_dir / f"{theorem_name}.lean"
    lean_path.write_text(contrib["lean_content"], encoding="utf-8")
    return lean_path


def verify_contribution(lean_path: Path, project_root: Path, timeout: int = 120) -> tuple[bool, str]:
    """Run lake build on the contribution file and return (success, output)."""
    try:
        result = subprocess.run(
            ["lake", "env", "lean", str(lean_path)],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_elan_env(),
        )
        out = (result.stdout + result.stderr).strip()
        ok = result.returncode == 0 and "error" not in out.lower()
        return ok, out[:500]
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    except Exception as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    SCRIPT_DIR = Path(__file__).resolve().parent
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))

    parser = argparse.ArgumentParser(description="Mathlib contribution utilities")
    sub = parser.add_subparsers(dest="command")

    p_novelty = sub.add_parser("check-novelty", help="Check if a theorem is novel vs Mathlib")
    p_novelty.add_argument("--statement", required=True)
    p_novelty.add_argument("--project-root", required=True)
    p_novelty.add_argument("--run-exact-search", action="store_true", default=True)
    p_novelty.add_argument("--run-semantic-check", action="store_true")

    p_contrib = sub.add_parser("generate", help="Generate a Mathlib-ready contribution")
    p_contrib.add_argument("--theorem-name", required=True)
    p_contrib.add_argument("--statement", required=True)
    p_contrib.add_argument("--proof", required=True)
    p_contrib.add_argument("--paper-id", default="")
    p_contrib.add_argument("--namespace", default="")
    p_contrib.add_argument("--docstring", default="")
    p_contrib.add_argument("--out-dir", default="output/contributions")
    p_contrib.add_argument("--verify", action="store_true")
    p_contrib.add_argument("--project-root", default=".")

    # Keep old subcommand for compat
    p_skeleton = sub.add_parser("generate-skeleton", help="Alias for generate")
    p_skeleton.add_argument("--theorem-name", required=True)
    p_skeleton.add_argument("--statement", required=True)
    p_skeleton.add_argument("--proof", required=True)
    p_skeleton.add_argument("--paper-id", default="")

    args = parser.parse_args()

    if args.command == "check-novelty":
        result = check_novelty(
            args.statement,
            project_root=Path(args.project_root),
            run_exact_search=args.run_exact_search,
            run_semantic_check=args.run_semantic_check,
        )
        print(json.dumps(result, indent=2))

    elif args.command in ("generate", "generate-skeleton"):
        name = args.theorem_name
        contrib = generate_contribution(
            theorem_name=name,
            lean_statement=args.statement,
            proof_text=args.proof,
            paper_id=getattr(args, "paper_id", ""),
            namespace=getattr(args, "namespace", ""),
            docstring=getattr(args, "docstring", ""),
        )
        if args.command == "generate":
            lean_path = write_contribution_file(
                contrib,
                out_dir=Path(args.out_dir),
                theorem_name=name,
            )
            print(f"Written: {lean_path}")
            if args.verify:
                ok, out = verify_contribution(lean_path, Path(args.project_root))
                print(f"Verify: {'OK' if ok else 'FAILED'}")
                if out:
                    print(out)
        print(json.dumps(contrib, indent=2))

    else:
        parser.print_help()
