#!/usr/bin/env python3
"""Classify a paper into a math area for area-aware translation/proof search.

Areas correspond to Mathlib super-modules + a CoT prompt template + a
paper-theory generator template. The classifier is heuristic but stable:
it inspects the LaTeX from `extracted_theorems.json` (or the `.lean` file's
imported headers) and matches keyword patterns.

Areas (initial set):
    - analysis      → Mathlib.Analysis, MeasureTheory, PDE-style work
    - probability   → Mathlib.Probability + MeasureTheory
    - algebra       → Mathlib.Algebra (group/ring/representation theory)
    - combinatorics → Mathlib.Combinatorics (Finset, graphs, posets)
    - numbertheory  → Mathlib.NumberTheory
    - generic       → fallback when no signal dominates

The classifier is intentionally simple — it's an *area hint* for downstream
tools, not an authoritative tag. Tools should fall back to `generic` (full
Mathlib) on ambiguity.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


_AREA_KEYWORDS: dict[str, list[str]] = {
    "analysis": [
        "norm", "lipschitz", "continuous", "derivative", "integral",
        "differentiable", "PDE", "Sobolev", "Lp", "L2", "L\\^2", "Lq",
        "Strichartz", "Schrodinger", "Schrödinger", "wave", "heat",
        "operator", "spectrum", "eigenvalue", "Fourier", "convergence",
        "tendsto", "limit", "epsilon", "delta", "supremum", "infimum",
        "Banach", "Hilbert", "holomorphic", "analytic",
    ],
    "probability": [
        "almost surely", "almost-surely", "a.s.", "expectation", "variance",
        "martingale", "stationary", "Markov", "filtration", "stopping",
        "Brownian", "random variable", "probability measure", "distribution",
        "i.i.d.", "law of large", "central limit", "characteristic function",
        "in probability", "tight", "preferential attachment", "random graph",
        "Erd\\H{o}s", "Erdős", "branching", "percolation", "coupling",
    ],
    "algebra": [
        "group", "ring", "field", "ideal", "module", "homomorphism",
        "automorphism", "representation", "irreducible", "tensor product",
        "Lie algebra", "Galois", "polynomial ring", "algebraic closure",
        "scheme", "variety", "cohomology", "category", "functor",
        "multisegment", "quiver",
    ],
    "combinatorics": [
        "graph", "vertex", "edge", "tree", "matroid", "Ramsey",
        "extremal", "coloring", "matching", "Latin square", "design",
        "combinatorial", "bijection", "partition", "permutation",
        "Sperner", "poset", "antichain", "Erd\\H{o}s-Ko-Rado",
        "hypergraph", "independent set", "chromatic number",
    ],
    "numbertheory": [
        "prime", "divisor", "totient", "Euler", "Fermat", "modular",
        "quadratic residue", "Diophantine", "integer", "rational",
        "irrational", "transcendental", "p-adic", "L-function",
        "zeta", "Riemann", "arithmetic progression", "gcd", "coprime",
    ],
}


# Mathlib super-module each area maps to (used by paper-theory generator
# and translator import substitution).
AREA_TO_IMPORTS: dict[str, list[str]] = {
    "analysis": [
        "Mathlib.Analysis.Calculus.ContDiff.Basic",
        "Mathlib.Analysis.NormedSpace.Basic",
        "Mathlib.MeasureTheory.Integral.Bochner.Basic",
        "Mathlib.Topology.MetricSpace.Basic",
    ],
    "probability": [
        "Mathlib.Probability.Independence.Basic",
        "Mathlib.Probability.ConditionalProbability",
        "Mathlib.MeasureTheory.MeasurableSpace.Basic",
        "Mathlib.MeasureTheory.Measure.MeasureSpace",
    ],
    "algebra": [
        "Mathlib.Algebra.Group.Basic",
        "Mathlib.Algebra.Ring.Basic",
        "Mathlib.RingTheory.Ideal.Basic",
        "Mathlib.LinearAlgebra.Basic",
    ],
    "combinatorics": [
        "Mathlib.Combinatorics.SimpleGraph.Basic",
        "Mathlib.Data.Finset.Basic",
        "Mathlib.Order.Antichain",
    ],
    "numbertheory": [
        "Mathlib.NumberTheory.Divisors",
        "Mathlib.NumberTheory.Padics.PadicNumbers",
        "Mathlib.Data.Nat.Prime.Basic",
    ],
    "generic": [
        "Mathlib",  # fallback: full Mathlib
    ],
}


def classify_text(text: str) -> tuple[str, dict[str, int]]:
    """Score `text` against each area's keyword set and return the winning area.

    Returns (area, scores) where `scores` is the keyword-hit count per area
    (useful for telemetry / debugging ambiguous classifications)."""
    text_l = (text or "").lower()
    if not text_l.strip():
        return "generic", {}
    scores: dict[str, int] = {}
    for area, keywords in _AREA_KEYWORDS.items():
        hits = 0
        for kw in keywords:
            kw_l = kw.lower()
            # Word-boundary match for short/single-token keywords; substring for
            # multi-word phrases (which already include their own context).
            if " " in kw_l:
                hits += text_l.count(kw_l)
            else:
                hits += len(re.findall(rf"\b{re.escape(kw_l)}\b", text_l))
        if hits > 0:
            scores[area] = hits
    if not scores:
        return "generic", {}
    # Pick the area with the most hits. On ties, prefer the more-specific area
    # (probability > combinatorics > analysis > algebra > numbertheory) which
    # tends to disambiguate analysis-tinted probability papers correctly.
    preference = ["probability", "combinatorics", "numbertheory", "algebra", "analysis"]
    best_score = max(scores.values())
    candidates = [a for a, s in scores.items() if s == best_score]
    if len(candidates) == 1:
        return candidates[0], scores
    for area in preference:
        if area in candidates:
            return area, scores
    return candidates[0], scores


def classify_paper(paper_id: str, project_root: Path | None = None) -> dict[str, Any]:
    """Classify a paper by reading its extracted_theorems.json (preferred)
    or the source LaTeX in `output/paper_sources/`. Returns a dict with
    `area`, `scores`, and `evidence_path`."""
    root = project_root or Path(__file__).resolve().parent.parent
    candidates = [
        root / "reproducibility" / "paper_agnostic_golden10_results" / paper_id / "extracted_theorems.json",
        root / "reproducibility" / "full_paper_reports" / paper_id / "extracted_theorems.json",
        root / "output" / "paper_extractions" / paper_id / "extracted_theorems.json",
    ]
    text_chunks: list[str] = []
    evidence_path = ""
    for path in candidates:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            entries = data.get("entries", []) if isinstance(data, dict) else []
            for e in entries:
                if not isinstance(e, dict):
                    continue
                stmt = str(e.get("statement", "") or "")
                if stmt:
                    text_chunks.append(stmt)
            if text_chunks:
                evidence_path = str(path.relative_to(root))
                break
    if not text_chunks:
        # Fallback: read the .lean file's comment headers, which carry truncated LaTeX.
        lean_path = root / "output" / f"{paper_id}.lean"
        if lean_path.exists():
            try:
                text = lean_path.read_text(encoding="utf-8")
                for line in text.split("\n"):
                    if line.strip().startswith("-- Statement (LaTeX):"):
                        text_chunks.append(line)
                evidence_path = str(lean_path.relative_to(root))
            except Exception:
                pass
    combined = "\n".join(text_chunks)
    area, scores = classify_text(combined)
    return {
        "schema_version": "paper_area_classifier.v1",
        "paper_id": paper_id,
        "area": area,
        "scores": scores,
        "evidence_path": evidence_path,
        "evidence_chars": len(combined),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paper_id", nargs="?", help="arxiv id, e.g. 2604.21884")
    parser.add_argument("--all", action="store_true",
                        help="Classify every paper in the verification ledgers dir")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()

    if args.all:
        out: list[dict[str, Any]] = []
        ledgers = (args.project_root / "output" / "verification_ledgers").glob("*.json")
        for path in sorted(ledgers):
            name = path.stem
            if any(s in name for s in ("_smoke", "_actionable", "_repair", "_reliable", "ab_repair", "_fdcheck", "_patchcheck", "_rflguard", "_fast")):
                continue
            out.append(classify_paper(name, args.project_root))
        # Aggregate
        c = Counter(r["area"] for r in out)
        print(json.dumps({"results": out, "area_distribution": dict(c.most_common())}, indent=2, ensure_ascii=False))
    elif args.paper_id:
        result = classify_paper(args.paper_id, args.project_root)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        parser.print_help()
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
