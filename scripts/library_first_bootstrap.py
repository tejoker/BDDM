#!/usr/bin/env python3
"""Domain-first library bootstrap for full-paper formalization runs.

Builds a small domain scaffold file that imports targeted Mathlib modules
and compiles it before paper translation/proving starts.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path


DOMAIN_IMPORTS: dict[str, list[str]] = {
    "probability_statistics": [
        "Mathlib.Probability.ConditionalProbability",
        "Mathlib.Probability.Martingale.Basic",
        "Mathlib.MeasureTheory.Function.LpSpace.Basic",
    ],
    "analysis_pde": [
        "Mathlib.Analysis.Calculus.Deriv.Basic",
        "Mathlib.Analysis.SpecialFunctions.Log.Basic",
        "Mathlib.Analysis.Normed.Module.Basic",
    ],
    "optimization": [
        "Mathlib.Analysis.Convex.Basic",
        "Mathlib.Analysis.Convex.Function",
        "Mathlib.LinearAlgebra.FiniteDimensional.Basic",
    ],
    "algebra_number_theory": [
        "Mathlib.Algebra.Field.Defs",
        "Mathlib.RingTheory.Ideal.Basic",
        "Mathlib.NumberTheory.Basic",
    ],
    "remaining_cs_math": [
        "Mathlib.Data.SetLike.Basic",
        "Mathlib.Logic.Basic",
        "Mathlib.Computability.Primrec.List",
    ],
}


def _elan_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = str(Path.home() / ".elan" / "bin") + ":" + env.get("PATH", "")
    return env


def _safe_name(domain: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in domain.lower()).strip("_") or "domain"


def bootstrap_domain(
    *,
    project_root: Path,
    domain: str,
    extra_imports: list[str],
    timeout_s: int,
) -> dict:
    imports = list(DOMAIN_IMPORTS.get(domain, []))
    imports.extend([x for x in extra_imports if x and x not in imports])
    if not imports:
        imports = ["Mathlib"]

    stub_dir = project_root / "output" / "library_bootstrap"
    stub_dir.mkdir(parents=True, exist_ok=True)
    stub_file = stub_dir / f"bootstrap_{_safe_name(domain)}.lean"
    stub_src = "\n".join([*(f"import {imp}" for imp in imports), "", "theorem bootstrap_sanity : True := by trivial", ""])
    stub_file.write_text(stub_src, encoding="utf-8")

    t0 = time.time()
    proc = subprocess.run(
        ["lake", "env", "lean", str(stub_file)],
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=max(30, int(timeout_s)),
        env=_elan_env(),
    )
    elapsed = round(time.time() - t0, 3)
    ok = proc.returncode == 0
    return {
        "domain": domain,
        "ok": ok,
        "imports": imports,
        "stub_file": str(stub_file),
        "elapsed_s": elapsed,
        "stdout_tail": (proc.stdout or "")[-1000:],
        "stderr_tail": (proc.stderr or "")[-1000:],
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Prebuild domain mathlib imports before full-paper runs")
    p.add_argument("--project-root", default=".")
    p.add_argument("--domain", required=True, choices=sorted(DOMAIN_IMPORTS.keys()))
    p.add_argument("--extra-import", action="append", default=[])
    p.add_argument("--timeout-s", type=int, default=600)
    p.add_argument("--out", default="")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    payload = bootstrap_domain(
        project_root=Path(args.project_root).resolve(),
        domain=str(args.domain),
        extra_imports=[str(x) for x in (args.extra_import or [])],
        timeout_s=int(args.timeout_s),
    )
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload.get("ok", False) else 2


if __name__ == "__main__":
    raise SystemExit(main())

