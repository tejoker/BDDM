"""TC knowledge loading + system prompts for statement translation.

Split from statement_translator.py (lines 42-298).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Resolve SCRIPT_DIR as the parent of the translator/ package (i.e. scripts/).
SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# ---------------------------------------------------------------------------
# Mathlib TC knowledge: loaded from two sources (merged at runtime):
#   1. data/mathlib_tc_graph.json  — built by build_tc_graph.py from Lean source
#      Contains: classes, hierarchy, implied_by, concept_map
#   2. data/mathlib_tc_map.json    — manually curated per-entry hints with hint text
# ---------------------------------------------------------------------------
_TC_MAP: dict[str, dict] = {}      # from mathlib_tc_map.json (hint text entries)
_TC_GRAPH: dict = {}               # from mathlib_tc_graph.json (full graph)
_TC_MAP_LOADED = False
_TC_GRAPH_LOADED = False


def _load_tc_map() -> dict[str, dict]:
    global _TC_MAP, _TC_MAP_LOADED
    if _TC_MAP_LOADED:
        return _TC_MAP
    _TC_MAP_LOADED = True
    search = SCRIPT_DIR.parent / "data" / "mathlib_tc_map.json"
    if search.exists():
        try:
            raw = json.loads(search.read_text(encoding="utf-8"))
            _TC_MAP = {k: v for k, v in raw.items() if not k.startswith("_")}
        except Exception:
            _TC_MAP = {}
    return _TC_MAP


def _load_tc_graph() -> dict:
    """Load the Mathlib TC hierarchy graph built by build_tc_graph.py."""
    global _TC_GRAPH, _TC_GRAPH_LOADED
    if _TC_GRAPH_LOADED:
        return _TC_GRAPH
    _TC_GRAPH_LOADED = True
    search = SCRIPT_DIR.parent / "data" / "mathlib_tc_graph.json"
    if search.exists():
        try:
            _TC_GRAPH = json.loads(search.read_text(encoding="utf-8"))
        except Exception:
            _TC_GRAPH = {}
    return _TC_GRAPH


def _get_lean_replacement(class_name: str) -> str | None:
    """Return the Lean replacement string for a non-Mathlib class name.

    Checks the TC graph concept_map first (from Lean source analysis),
    then falls back to the manual TC map.
    """
    graph = _load_tc_graph()
    concept_map = graph.get("concept_map", {})
    if class_name in concept_map:
        return concept_map[class_name]  # may be None (no replacement exists)

    # Fallback: manual TC map.
    tc_map = _load_tc_map()
    entry = tc_map.get(class_name, {})
    return entry.get("lean_replacement") if isinstance(entry, dict) else None


def _get_class_hint(class_name: str) -> str:
    """Return the natural-language fix hint for a non-Mathlib class name."""
    # Manual TC map has richer hint text.
    tc_map = _load_tc_map()
    entry = tc_map.get(class_name, {})
    if isinstance(entry, dict) and entry.get("hint"):
        return entry["hint"]

    # Generate a hint from the graph concept_map.
    replacement = _get_lean_replacement(class_name)
    if replacement is None:
        return (
            f"`{class_name}` does not exist in Mathlib4 and has no known Lean equivalent. "
            "Remove this type class and express the property as a plain hypothesis `(h : ...)`."
        )
    if replacement.startswith("(h"):
        return (
            f"`{class_name}` is a PREDICATE (Prop), not a type class. "
            f"Use `{replacement}` as an explicit hypothesis, not in `[...]`."
        )
    return (
        f"`{class_name}` does not exist in Mathlib4. "
        f"Replace with: {replacement}"
    )


def _get_hierarchy_rules(graph: dict | None = None) -> str:
    """Return TC hierarchy rules derived from the graph (what's already implied)."""
    if graph is None:
        graph = _load_tc_graph()
    hierarchy = graph.get("hierarchy", {})
    classes = graph.get("classes", {})

    important = [
        "MetricSpace", "NormedSpace", "InnerProductSpace", "NormedAddCommGroup",
        "TopologicalGroup", "CompactSpace", "Field", "CommRing", "EMetricSpace",
        "UniformSpace",
    ]
    rules = []
    for cls in important:
        ancs = [a for a in hierarchy.get(cls, []) if a in classes][:4]
        if ancs:
            rules.append(f"`[{cls}]` already implies {', '.join(ancs)}")
    return "; ".join(rules) if rules else ""


def _get_forbidden_and_replacements(graph: dict | None = None) -> tuple[str, str]:
    """Return (forbidden_list, replacements_str) from the TC graph concept_map."""
    if graph is None:
        graph = _load_tc_graph()
    concept_map: dict = graph.get("concept_map", {})

    forbidden = [k for k, v in concept_map.items() if v is None]
    replacements = [
        f"{k} → {v}" for k, v in concept_map.items()
        if v is not None and not str(v).startswith("(h")
    ]
    return ", ".join(forbidden), "; ".join(replacements)

_LEAN_BLOCK_RE = re.compile(r"```(?:lean|lean4)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_SIGNATURE_TAG_RE = re.compile(r"<signature>(.*?)</signature>", re.IGNORECASE | re.DOTALL)

_TRANSLATE_SYSTEM_BASE = (
    "You are Leanstral in statement-translation mode. "
    "Given a LaTeX mathematical statement, output the corresponding Lean 4 theorem signature "
    "inside <signature>...</signature> tags.\n"
    "STRICT RULES:\n"
    "1. Output ONLY the declaration — no imports, no `variable`, no `open`, no prose, no markdown.\n"
    "2. Start with `theorem` or `lemma` followed by a NAME, end with `:= by`. "
    "NEVER write `theorem {{` or `theorem (` — the name comes first: `theorem my_name {{α ...`.\n"
    "   NEVER start with `def`, `structure`, `class`, `instance`, or `noncomputable def`. "
    "   If the LaTeX input is a definition (not a proposition), translate the PROPERTY it implies as a theorem.\n"
    "3. Square brackets `[...]` are ONLY for type class instances that Lean can synthesize automatically "
    "(e.g. `[MeasurableSpace α]`, `[TopologicalSpace α]`, `[Fintype α]`). "
    "NEVER put propositions or predicates in `[...]` — they MUST be explicit hypotheses `(h : P)`.\n"
    "4. Use Lean 4 syntax (not Lean 3): `∑ i ∈ S, f i` (NOT `∑ i in S`), "
    "`∀ x ∈ S, P x` (NOT `∀ x in S`), `fun x => f x` (NOT `λ x, f x` or `λ x => f x` with `λ`). "
    "The symbol `λ` is a Lean 3 keyword — NEVER use it. "
    "NEVER use `λ` as a variable name (e.g. `(λ : ℝ)`) — rename to `lam` or `eigenval`.\n"
    "5. Big-O / little-o notation: use Mathlib infix `f =O[l] g` and `f =o[l] g` "
    "(where `l : Filter α`). NEVER write `O(n)`, `o(n)`, `Θ(n)` — these are not Lean expressions. "
    "NEVER use `o(1)`, `o(n)`, or `O(n)` as a VALUE or factor in an arithmetic expression "
    "(e.g. `(c + o(1)) * n^k` is INVALID — `o(1)` is not a number). "
    "Instead, introduce an explicit error function: "
    "`∃ ε : ℕ → ℝ, ε =o[Filter.atTop] (fun _ => (1 : ℝ)) ∧ f n = (c + ε n) * n^k`. "
    "The pattern `(1 + o(1))` means 'a quantity approaching 1' — express as "
    "`∃ δ : ℕ → ℝ, δ =o[Filter.atTop] (fun _ => (1 : ℝ)) ∧ f n = (1 + δ n) * g n`.\n"
    "6. Filters: `Filter.atTop`, `nhds x`, `Filter.atBot` are filter values, not functions. "
    "Do not apply them to arguments: write `f =o[Filter.atTop] g`, not `f =o[Filter.atTop x] g`.\n"
    "7. {RULE7_CLASSES}\n"
    "8. Use standard Mathlib4 names: `Real.log` (not `ln`), `Nat.card`, `Finset.sum`, "
    "`Asymptotics.IsLittleO`, `Asymptotics.IsBigO`, `Filter.Tendsto`, "
    "`n.factorial` or `Nat.factorial n` (NOT `n!` — the `!` postfix is invalid Lean 4).\n"
    "9. Use universe-polymorphic types `{α : Type*}`.\n"
    "10. Control theory / matrix notation:\n"
    "    - POSITIVE DEFINITE: `M ≻ 0` (LaTeX) → `M.PosDef` (Prop) in Mathlib4. "
    "      Use `(hM : M.PosDef)` as explicit hypothesis.\n"
    "    - POSITIVE SEMIDEFINITE: `M ⪰ 0` → `M.PosSemidef`, use `(hM : M.PosSemidef)`.\n"
    "    - EIGENVALUES / SPECTRAL RADIUS: `λ_max(M)`, `ρ(M)`, `σ_max(M)` are NOT valid Lean. "
    "      Use `(hλ : ∀ v, ‖Matrix.mulVec M v‖ ≤ c * ‖v‖)` or introduce the spectral norm as a hypothesis "
    "      `(hspec : Matrix.spectralNorm M ≤ c)`. DO NOT write `λ_max M` or `ρ(M)` — these are not Lean expressions. "
    "      The symbol `λ` is ALWAYS the Lean 3 lambda in Lean parsing — never use it for eigenvalues.\n"
    "    - MATRIX TRANSPOSE: `A^⊤` or `Aᵀ` in Lean 4 (use `Aᵀ`, the superscript ᵀ Unicode).\n"
    "    - LMI (Linear Matrix Inequality): `[A B; C D] ≻ 0` style block matrices must be flattened "
    "      into a `Matrix (Fin n) (Fin n) ℝ` hypothesis. "
    "      E.g. `(hLMI : (Matrix.fromBlocks A B C D).PosDef)`.\n"
    "11. Graph theory notation:\n"
    "    - Graphs: use `(G : SimpleGraph V)` where `V : Type*` is the vertex type.\n"
    "    - Graph Laplacian: `G.laplacian` does not exist in Mathlib4. Use a hypothesis `(L : Matrix V V ℝ)` "
    "      with `(hL : L = SimpleGraph.laplacianMatrix G ...)` or treat it as an abstract matrix.\n"
    "    - Adjacency matrix: `SimpleGraph.adjacencyMatrix G ℝ` is valid Mathlib4.\n"
    "    - Chromatic number: `G.chromaticNumber` is valid Mathlib4.\n"
    "    - Clique number: `G.cliqueNum` — use `G.cliqueFree` or state as `∃ s : Finset V, G.IsClique s ∧ s.card = k`."
)

_TRANSLATE_SYSTEM: str | None = None

_RULE7_STATIC = (
    "Only use type class names that exist in Mathlib4. "
    "Valid classes: MetricSpace, TopologicalSpace, MeasurableSpace, NormedAddCommGroup, "
    "NormedSpace, InnerProductSpace, CompleteSpace, Fintype, Finite, DecidableEq, Ring, Field, "
    "LinearOrder, LinearOrderedField, LinearOrderedCommRing, "
    "Module, Algebra, Group, CommGroup, TopologicalGroup, CompactSpace, T2Space. "
    "FORBIDDEN classes (do NOT use — they do not exist in Mathlib4): "
    "LinearOrderedRing (→ use LinearOrderedCommRing), "
    "OrderedRing (→ use StrictOrderedRing), "
    "GeodesicSpace, LengthSpace, CatSpace, CBA, AlexandrovSpace, GeodesicMetricSpace, "
    "ProfiniteGroup, StronglyComplete, ResiduallyFinite, IsNest, StronglyResiduallyFinite, "
    "IsSigmaAlgebra, SigmaAlgebra, ProbabilitySpace, HilbertSpace, BanachSpace, "
    "FrechetSpace, VectorSpace, LinearSpace, SobolevSpace, RiemannianManifold, "
    "AnalyticManifold, FreeIndep, GraphClass, Hypergraph, RandomVariable, IndependentRV. "
    "REPLACEMENTS for forbidden classes: "
    "LinearOrderedRing → `[LinearOrderedCommRing α]`; "
    "OrderedRing → `[StrictOrderedRing α]`; "
    "GeodesicSpace/LengthSpace/CBA/CatSpace → `[MetricSpace α]`; "
    "ProfiniteGroup → `[Group G] [TopologicalGroup G] [CompactSpace G] [T2Space G]`; "
    "HilbertSpace → `[NormedAddCommGroup E] [InnerProductSpace ℝ E] [CompleteSpace E]`; "
    "BanachSpace → `[NormedAddCommGroup E] [NormedSpace ℝ E] [CompleteSpace E]`; "
    "IsSigmaAlgebra → `[MeasurableSpace Ω]`; "
    "VectorSpace/LinearSpace → `[AddCommGroup E] [Module k E]`. "
    "TC HIERARCHY — do NOT list implied classes: "
    "`[NormedSpace 𝕜 E]` already implies NormedAddCommGroup, SeminormedAddCommGroup; "
    "`[MetricSpace α]` already implies TopologicalSpace, UniformSpace, PseudoMetricSpace; "
    "`[InnerProductSpace 𝕜 E]` already implies NormedSpace; "
    "`[LinearOrderedField α]` already implies LinearOrder, Field, CommRing. "
    "PREDICATES vs CLASSES: `LocallyLipschitz f`, `StronglyConvexOn ℝ s f`, `MeasurableSet s`, "
    "`IsClosed s`, `IsOpen s` are PROPOSITIONS — use `(h : LocallyLipschitz f)`, not `[LocallyLipschitz f]`. "
    "σ-algebras: use `[MeasurableSpace Ω]` (not `IsSigmaAlgebra`). "
    "DOMAIN TYPES: if the paper's mathematical domain (e.g. multisegments, quiver representations, "
    "p-adic groups) has no Mathlib4 formalization, use `axiom MyType : Type` stubs rather than "
    "trying to fit the domain into existing Mathlib classes. Prefer correct axioms over wrong classes."
)


def _get_translate_system() -> str:
    """Return the translation system prompt, enriching rule 7 from the TC graph if available."""
    global _TRANSLATE_SYSTEM
    if _TRANSLATE_SYSTEM is not None:
        return _TRANSLATE_SYSTEM

    graph = _load_tc_graph()
    if graph:
        # Build rule 7 dynamically from the graph.
        forbidden, replacements = _get_forbidden_and_replacements(graph)
        hierarchy_rules = _get_hierarchy_rules(graph)
        concept_map = graph.get("concept_map", {})
        predicates = [
            k for k, v in concept_map.items()
            if v is not None and str(v).startswith("(h")
        ]
        rule7 = (
            "Only use type class names that exist in Mathlib4. "
            "Valid classes: MetricSpace, TopologicalSpace, MeasurableSpace, NormedAddCommGroup, "
            "NormedSpace, InnerProductSpace, CompleteSpace, Fintype, Finite, DecidableEq, Ring, Field, "
            "LinearOrder, LinearOrderedField, LinearOrderedCommRing, "
            "Module, Algebra, Group, CommGroup, TopologicalGroup, CompactSpace, T2Space. "
            "ALWAYS FORBIDDEN (Lean 3 names absent from Mathlib4): "
            "LinearOrderedRing (→ LinearOrderedCommRing), OrderedRing (→ StrictOrderedRing). "
        )
        # Merge graph-derived forbidden with hard-coded Lean3 names.
        hard_forbidden = {"LinearOrderedRing", "OrderedRing"}
        all_forbidden = hard_forbidden | set(forbidden.split(", ") if forbidden else [])
        effective_forbidden = ", ".join(sorted(all_forbidden - {""}))
        if effective_forbidden:
            rule7 += f"FORBIDDEN (do NOT use — not in Mathlib4): {effective_forbidden}. "
        if replacements:
            rule7 += f"REPLACEMENTS: {replacements}. "
        if predicates:
            rule7 += (
                "PREDICATES vs CLASSES: "
                + ", ".join(f"`{p}`" for p in predicates[:8])
                + " are PROPOSITIONS — use `(h : ...)`, not `[...]`. "
            )
        if hierarchy_rules:
            rule7 += f"TC HIERARCHY — do NOT list implied classes: {hierarchy_rules}."
    else:
        rule7 = _RULE7_STATIC

    _TRANSLATE_SYSTEM = _TRANSLATE_SYSTEM_BASE.replace("{RULE7_CLASSES}", rule7)
    return _TRANSLATE_SYSTEM

_REPAIR_SYSTEM = (
    "You are Leanstral in statement-repair mode. "
    "The previous Lean 4 signature failed to elaborate. "
    "Fix ONLY the specific error and output the corrected declaration inside <signature>...</signature> tags. "
    "No imports, no `variable`, no prose — just the declaration starting with `theorem` or `lemma`.\n"
    "KEY RULES to remember:\n"
    "- `[...]` is ONLY for Lean type class instances. Propositions/predicates go in `(h : P)`, not `[P]`.\n"
    "- Use `∑ i ∈ S, f i` and `∀ x ∈ S, P x` with the ∈ symbol (not the word `in`).\n"
    "- Big-O: `f =O[Filter.atTop] g`, little-o: `f =o[Filter.atTop] g`. Never use O(n) notation.\n"
    "- Only use type classes that exist in standard Mathlib4.\n"
    "- Remove duplicate ':= by' at the end."
)

