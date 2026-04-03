#!/usr/bin/env python3
"""Build a Mathlib4 type class hierarchy graph.

Two-phase pipeline:
  Phase 1 (fast, exact): parse Mathlib .lean source for class/structure declarations
            and `extends` relationships → exact TC hierarchy DAG.
  Phase 2 (LLM, optional): run HyDRA / ontopipe on Mathlib docstrings to extract
            informal concept synonyms (e.g. "geodesic space" → MetricSpace).

Output: data/mathlib_tc_graph.json with:
  {
    "classes": {NAME: {module, kind, extends, params, docstring}},
    "hierarchy": {NAME: [all transitive ancestors]},
    "implied_by": {NAME: [all descendants]},
    "concept_map": {informal_name: lean_replacement}
  }

Usage:
    # Phase 1 only (fast, no API key needed):
    python3 scripts/build_tc_graph.py

    # Phase 1 + Phase 2 (HyDRA concept extraction):
    python3 scripts/build_tc_graph.py --hydra

    # Quick test with limited files:
    python3 scripts/build_tc_graph.py --max-files 200

    # Print system prompt rules from the graph:
    python3 scripts/build_tc_graph.py --print-rules
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Phase 1: direct extraction from Lean source
# ---------------------------------------------------------------------------

# Matches: class Foo [params] [extends Bar, Baz] where
# Groups: (kind, name, rest_of_header)
_DECL_RE = re.compile(
    r"^[ \t]*(?:@\[[^\]]*\]\s*)*"
    r"(class|structure)\s+"
    r"([A-Z]\w*)"         # name must start uppercase
    r"([^\n]*)",          # rest of line (params + optional extends inline)
    re.MULTILINE,
)

# `extends` clause that may appear on the same or following line
_EXTENDS_RE = re.compile(r"\bextends\s+([A-Z][\w,\s.]*?)(?=where|\n|\{|:=|$)")

# Docstring just before a declaration
_DOC_RE = re.compile(r"/--\s*([\s\S]*?)\s*-/\s*$", re.MULTILINE)


def _parse_parents(extends_str: str) -> list[str]:
    """Parse comma-separated parent names from an extends clause."""
    parents = []
    for part in extends_str.split(","):
        name = part.strip().split()[0] if part.strip() else ""
        # Keep only valid Lean idents starting with uppercase.
        if re.match(r"^[A-Z]\w*$", name):
            parents.append(name)
    return parents


def scan_lean_files(mathlib_root: Path, max_files: int = 0) -> dict[str, dict]:
    """Return {class_name: {module, kind, extends, docstring}} from Lean source."""
    lean_files = sorted(mathlib_root.rglob("*.lean"))
    if max_files:
        lean_files = lean_files[:max_files]

    classes: dict[str, dict] = {}
    count = 0

    for fpath in lean_files:
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        # Module name: e.g. .lake/packages/mathlib/Mathlib/Algebra/Group/Basic.lean
        # → Mathlib.Algebra.Group.Basic
        try:
            # Walk up to find the Mathlib dir in the path parts.
            parts = fpath.parts
            ml_idx = next(
                (i for i, p in enumerate(parts) if p == "Mathlib"),
                None,
            )
            if ml_idx is not None:
                module = ".".join(parts[ml_idx:]).removesuffix(".lean")
            else:
                module = fpath.stem
        except Exception:
            module = fpath.stem

        for m in _DECL_RE.finditer(text):
            kind = m.group(1)
            name = m.group(2)
            rest = m.group(3)

            # Collect extends from rest-of-line and next few lines.
            context = rest
            # Look ahead up to 5 lines for extends / where.
            after_match = text[m.end():m.end() + 400]
            # Stop at 'where' keyword.
            where_idx = after_match.find("where")
            if where_idx > 0:
                context += " " + after_match[:where_idx]

            parents: list[str] = []
            em = _EXTENDS_RE.search(context)
            if em:
                parents = _parse_parents(em.group(1))

            # Docstring search (last /-- ... -/ before this declaration).
            doc = ""
            before = text[max(0, m.start() - 600):m.start()]
            dm = _DOC_RE.search(before)
            if dm:
                raw_doc = dm.group(1)
                # Take first sentence / 300 chars.
                doc = raw_doc.split("\n\n")[0].strip()[:300]

            entry = {
                "module": module,
                "kind": kind,
                "extends": parents,
                "docstring": doc,
            }

            # Keep first-seen definition; prefer entries that have parents.
            if name not in classes or (not classes[name]["extends"] and parents):
                classes[name] = entry

        count += 1
        if count % 1000 == 0:
            print(f"  [{count}/{len(lean_files)}] {len(classes)} classes", file=sys.stderr)

    return classes


def build_ancestor_map(classes: dict[str, dict]) -> dict[str, list[str]]:
    """BFS transitive closure: {name: [all ancestors]}."""
    memo: dict[str, list[str]] = {}

    def ancestors(name: str, stack: frozenset[str] = frozenset()) -> list[str]:
        if name in memo:
            return memo[name]
        if name in stack:
            return []
        stack = stack | {name}
        result: list[str] = []
        seen: set[str] = set()
        for parent in classes.get(name, {}).get("extends", []):
            if parent not in seen:
                seen.add(parent)
                result.append(parent)
            for anc in ancestors(parent, stack):
                if anc not in seen:
                    seen.add(anc)
                    result.append(anc)
        memo[name] = result
        return result

    for name in classes:
        ancestors(name)
    return memo


# ---------------------------------------------------------------------------
# Phase 2: HyDRA concept synonym extraction (optional)
# ---------------------------------------------------------------------------

# Hardcoded concept map — known non-Mathlib concept names → Mathlib replacements.
# These are the high-confidence entries we already know from the manual TC map.
_HARDCODED_CONCEPT_MAP: dict[str, str | None] = {
    # Geometry
    "GeodesicSpace": "[MetricSpace α]",
    "LengthSpace": "[MetricSpace α]",
    "CBA": "[MetricSpace α]",
    "CatSpace": "[MetricSpace α]",
    "AlexandrovSpace": "[MetricSpace α]",
    "GeodesicMetricSpace": "[MetricSpace α]",
    "RiemannianManifold": None,   # not in Mathlib4
    "AnalyticManifold": None,
    "DifferentiableManifold": "[SmoothManifoldWithCorners I M]",
    # Algebra
    "ProfiniteGroup": "[Group G] [TopologicalGroup G] [CompactSpace G] [T2Space G]",
    "StronglyComplete": None,
    "ResiduallyFinite": None,
    "IsNest": None,
    "FreeIndep": None,
    "VectorSpace": "[AddCommGroup E] [Module k E]",
    "LinearSpace": "[AddCommGroup E] [Module k E]",
    # Analysis
    "HilbertSpace": "[NormedAddCommGroup E] [InnerProductSpace ℝ E] [CompleteSpace E]",
    "BanachSpace": "[NormedAddCommGroup E] [NormedSpace ℝ E] [CompleteSpace E]",
    "FrechetSpace": None,
    "SobolevSpace": None,
    "LocallyLipschitz": "(h : LocallyLipschitz f)",   # predicate
    "StronglyConvex": "(hConv : StronglyConvexOn ℝ s f)",
    # Measure / Probability
    "IsSigmaAlgebra": "[MeasurableSpace Ω]",
    "SigmaAlgebra": "[MeasurableSpace Ω]",
    "ProbabilitySpace": "[MeasureSpace Ω] [IsProbabilityMeasure (volume : Measure Ω)]",
    # Graph / Combinatorics
    "GraphClass": "(G : SimpleGraph V)",
    "Hypergraph": None,
    # Matrix / Control
    "PositiveDefinite": "(h : M.PosDef)",
    "PositiveSemidefinite": "(h : M.PosSemidef)",
}


def run_hydra_extraction(
    mathlib_root: Path,
    cache_path: Path,
    sample_files: int = 50,
    batch_size: int = 10,
) -> dict[str, str | None]:
    """Extract informal concept synonyms from Mathlib docstrings via Mistral API.

    Prompts the configured Mistral model to identify informal math concept names
    (e.g. "HilbertSpace", "BanachSpace") and their Lean 4 / Mathlib4 equivalents
    from batches of docstring text. No external dependencies beyond mistralai.

    Returns {ConceptName: lean_replacement_or_None} to merge into concept_map.
    Requires MISTRAL_API_KEY in environment or .env file.
    """
    import os

    # Resolve API key and model from environment / .env.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    api_key = os.environ.get("MISTRAL_API_KEY", "")
    if not api_key:
        print("[hydra] MISTRAL_API_KEY not set — skipping Phase 2.", file=sys.stderr)
        return {}

    model = os.environ.get("MISTRAL_MODEL", "labs-leanstral-2603")

    try:
        from mistralai import Mistral
    except ImportError:
        try:
            from mistralai.client import MistralClient as Mistral  # type: ignore
        except ImportError:
            print("[hydra] mistralai package not installed — skipping Phase 2.", file=sys.stderr)
            return {}

    cache_path.mkdir(parents=True, exist_ok=True)
    cache_file = cache_path / "hydra_synonyms.json"
    if cache_file.exists():
        print("[hydra] Loading cached synonyms from prior run...", file=sys.stderr)
        return json.loads(cache_file.read_text(encoding="utf-8"))

    # Collect docstrings from Mathlib source.
    lean_files = sorted(mathlib_root.rglob("*.lean"))[:sample_files]
    doc_chunks: list[str] = []
    doc_re = re.compile(r"/--\s*([\s\S]*?)\s*-/", re.MULTILINE)
    for fpath in lean_files:
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in doc_re.finditer(text):
            chunk = m.group(1).strip()
            if len(chunk) > 50:
                doc_chunks.append(chunk[:600])

    if not doc_chunks:
        print("[hydra] No docstrings found in mathlib_root — skipping.", file=sys.stderr)
        return {}

    print(
        f"[hydra] Extracting concept synonyms from {len(doc_chunks)} docstring chunks "
        f"using {model} ...",
        file=sys.stderr,
    )

    client = Mistral(api_key=api_key)
    synonyms: dict[str, str | None] = {}

    _SYSTEM = (
        "You are a Lean 4 / Mathlib expert. "
        "Given informal math docstring text, extract pairs of (InformalName, Lean4Name) "
        "where InformalName is a math concept name that appears in the text and "
        "Lean4Name is its Mathlib4 typeclass or structure name. "
        "If a concept has no direct Mathlib4 equivalent, use null for Lean4Name. "
        "Reply with a JSON object only: {\"InformalName\": \"Lean4Name or null\", ...}. "
        "No explanation, no markdown fences."
    )

    for i in range(0, len(doc_chunks), batch_size):
        batch = doc_chunks[i : i + batch_size]
        user_text = "\n\n---\n\n".join(batch)
        try:
            resp = client.chat.complete(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": user_text},
                ],
                temperature=0.0,
                max_tokens=512,
            )
            raw = resp.choices[0].message.content.strip()
            # Strip markdown fences if model wraps them.
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    if isinstance(k, str) and k not in synonyms:
                        synonyms[k] = str(v) if v is not None else None
        except Exception as exc:
            print(f"[hydra] batch {i//batch_size} failed: {exc}", file=sys.stderr)
            continue

    print(f"[hydra] Extracted {len(synonyms)} concept synonyms.", file=sys.stderr)
    cache_file.write_text(json.dumps(synonyms, indent=2, ensure_ascii=False), encoding="utf-8")
    return synonyms


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def generate_system_prompt_rules(graph: dict) -> str:
    """Generate the FORBIDDEN/REPLACEMENTS section for statement_translator.py system prompt."""
    concept_map: dict = graph.get("concept_map", {})
    hierarchy: dict = graph.get("hierarchy", {})
    classes: dict = graph.get("classes", {})

    forbidden = [k for k, v in concept_map.items() if v is None]
    replacements = [
        f"{k} → {v}"
        for k, v in concept_map.items()
        if v is not None and not v.startswith("(h")  # exclude predicates
    ]
    predicates = [
        f"{k} → use `{v}` as hypothesis"
        for k, v in concept_map.items()
        if v is not None and v.startswith("(h")
    ]

    # Key hierarchy rules (what's already implied).
    key_hierarchy: list[str] = []
    important = [
        "MetricSpace", "NormedSpace", "InnerProductSpace", "NormedAddCommGroup",
        "TopologicalGroup", "CompactSpace", "Field", "CommRing",
    ]
    for cls in important:
        ancs = [a for a in hierarchy.get(cls, []) if a in classes][:5]
        if ancs:
            key_hierarchy.append(
                f"`[{cls}]` already implies: {', '.join(ancs)}"
            )

    lines = [
        "FORBIDDEN (not in Mathlib4): " + ", ".join(forbidden),
        "REPLACEMENTS:",
    ] + [f"  {r}" for r in replacements] + [
        "PREDICATES (use as explicit hypothesis, not typeclass):",
    ] + [f"  {p}" for p in predicates] + [
        "TC HIERARCHY (already implied — do NOT repeat separately):",
    ] + [f"  {h}" for h in key_hierarchy]

    return "\n".join(lines)


def build_graph(mathlib_root: Path, use_hydra: bool = False,
                max_files: int = 0) -> dict:
    print("Phase 1: scanning Lean source files...", file=sys.stderr)
    classes = scan_lean_files(mathlib_root, max_files=max_files)
    print(f"  Found {len(classes)} class/structure declarations", file=sys.stderr)

    print("Computing transitive ancestor map...", file=sys.stderr)
    hierarchy = build_ancestor_map(classes)

    # implied_by: reverse of hierarchy (descendants).
    implied_by: dict[str, list[str]] = defaultdict(list)
    for name, ancs in hierarchy.items():
        for anc in ancs:
            implied_by[anc].append(name)

    # Concept map: start with hardcoded, optionally extend with HyDRA.
    concept_map: dict[str, str | None] = dict(_HARDCODED_CONCEPT_MAP)

    if use_hydra:
        print("Phase 2: running HyDRA concept extraction...", file=sys.stderr)
        hydra_synonyms = run_hydra_extraction(
            mathlib_root=mathlib_root,
            cache_path=mathlib_root.parent.parent.parent / "data" / "hydra_cache",
        )
        for k, v in hydra_synonyms.items():
            if k not in concept_map:
                concept_map[k] = v

    return {
        "meta": {
            "total_classes": len(classes),
            "total_with_extends": sum(1 for e in classes.values() if e["extends"]),
            "concept_map_entries": len(concept_map),
        },
        "classes": {
            k: {
                "module": v["module"],
                "kind": v["kind"],
                "extends": v["extends"],
                "docstring": v["docstring"],
            }
            for k, v in classes.items()
        },
        "hierarchy": hierarchy,
        "implied_by": {k: sorted(set(v)) for k, v in implied_by.items()},
        "concept_map": concept_map,
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="Build Mathlib4 TC hierarchy graph")
    p.add_argument(
        "--mathlib-root",
        default=".lake/packages/mathlib/Mathlib",
        help="Path to Mathlib source directory",
    )
    p.add_argument(
        "--output",
        default="data/mathlib_tc_graph.json",
    )
    p.add_argument(
        "--hydra",
        action="store_true",
        help="Run Phase 2: HyDRA concept synonym extraction (requires ontopipe + API key)",
    )
    p.add_argument(
        "--print-rules",
        action="store_true",
        help="Print generated system prompt rules and exit (requires existing graph JSON)",
    )
    p.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Limit to N files (0 = all); useful for testing",
    )
    args = p.parse_args()

    if args.print_rules:
        out = Path(args.output)
        if not out.exists():
            print(f"[error] {out} not found — run without --print-rules first", file=sys.stderr)
            return 1
        graph = json.loads(out.read_text())
        print(generate_system_prompt_rules(graph))
        return 0

    mathlib_root = Path(args.mathlib_root).resolve()
    if not mathlib_root.exists():
        print(f"[error] Mathlib root not found: {mathlib_root}", file=sys.stderr)
        return 1

    graph = build_graph(
        mathlib_root=mathlib_root,
        use_hydra=args.hydra,
        max_files=args.max_files,
    )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")

    size_kb = out.stat().st_size // 1024
    print(f"\nOutput: {out} ({size_kb} KB)", file=sys.stderr)
    print(f"  Classes:          {graph['meta']['total_classes']}", file=sys.stderr)
    print(f"  With extends:     {graph['meta']['total_with_extends']}", file=sys.stderr)
    print(f"  Concept map:      {graph['meta']['concept_map_entries']}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
