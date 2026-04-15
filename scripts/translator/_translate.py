"""Main translation logic for LaTeX → Lean 4 theorem signatures.

Split from statement_translator.py (lines 299-EOF).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Resolve SCRIPT_DIR as scripts/ (parent of this translator/ package).
SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from ponder_loop import _chat_complete  # noqa: E402
from lean_validation import (  # noqa: E402
    escape_lean_identifier,
    validate_theorem_name,
    validate_theorem_signature,
    validate_and_sanitize_signature,
)
from translator._knowledge import (  # noqa: E402
    _LEAN_BLOCK_RE,
    _SIGNATURE_TAG_RE,
    _REPAIR_SYSTEM,
    _get_class_hint,
    _get_lean_replacement,
    _get_translate_system,
)


@dataclass
class TranslationResult:
    lean_signature: str     # final signature (may contain sorry if all rounds failed)
    validated: bool         # True if `#check` succeeded
    rounds_used: int
    last_error: str
    confidence: float = 0.0
    uncertainty_flags: list[str] = None
    adversarial_flags: list[str] = None  # issues found by adversarial check
    roundtrip_back_translation: str | None = None
    roundtrip_flags: list[str] = None
    decomposition_stubs: list[dict] = None  # sorry-backed stubs for missing types

    def __post_init__(self) -> None:
        if self.uncertainty_flags is None:
            self.uncertainty_flags = []
        if self.adversarial_flags is None:
            self.adversarial_flags = []
        if self.roundtrip_flags is None:
            self.roundtrip_flags = []
        if self.decomposition_stubs is None:
            self.decomposition_stubs = []


def _confidence_from_translation_state(
    *,
    validated: bool,
    rounds_used: int,
    last_error: str,
    signature: str,
) -> tuple[float, list[str]]:
    """Heuristic confidence score and uncertainty tags for translation outputs."""
    flags: list[str] = []
    err_l = (last_error or "").lower()

    if not validated:
        flags.append("formalization_unvalidated")
        if "unknown identifier" in err_l or "unknown constant" in err_l:
            flags.append("unknown_symbol")
        if "type mismatch" in err_l:
            flags.append("type_mismatch")
        if "unexpected token" in err_l:
            flags.append("syntax_error")
        return 0.20, flags

    # Validated signatures start high, then penalize repeated repair rounds.
    confidence = 0.95
    if rounds_used >= 2:
        confidence -= 0.10
        flags.append("repaired_once")
    if rounds_used >= 3:
        confidence -= 0.10
        flags.append("multi_repair")
    if rounds_used >= 4:
        confidence -= 0.10
        flags.append("high_repair_count")

    sig_l = signature.lower()
    if "sorry" in sig_l:
        confidence -= 0.25
        flags.append("contains_sorry")
    if "theorem" not in sig_l and "lemma" not in sig_l:
        confidence -= 0.20
        flags.append("non_theorem_declaration")

    confidence = max(0.0, min(1.0, confidence))
    return confidence, flags


_DECL_START_RE = re.compile(
    r"^(noncomputable\s+)?(private\s+)?(protected\s+)?"
    r"(theorem|lemma|def|abbrev|structure|class|instance)\b",
    re.MULTILINE,
)


def _extract_signature(text: str) -> str:
    """Extract a Lean theorem/lemma signature from model output.

    Handles:
      - <signature>...</signature> tags (with or without closing tag)
      - ```lean / ```lean4 code blocks (strips leading import/variable lines)
      - Raw text fallback (scans for first theorem/lemma/def declaration)

    Always strips leading import statements and variable/open declarations
    so the returned string starts at the actual declaration.
    """
    candidate = ""

    # 1. Try closed <signature> tag.
    m = _SIGNATURE_TAG_RE.search(text)
    if m:
        candidate = m.group(1).strip()
    else:
        # 2. Try unclosed <signature> tag — take everything after it.
        open_tag = re.search(r"<signature>\s*", text, re.IGNORECASE)
        if open_tag:
            candidate = text[open_tag.end():].strip()
        else:
            # 3. Try lean code blocks.
            blocks = [b.strip() for b in _LEAN_BLOCK_RE.findall(text) if b.strip()]
            if blocks:
                candidate = blocks[0]
            else:
                candidate = text.strip()

    # Strip leading import / open / variable / section / namespace lines so
    # that the signature starts at the actual declaration.
    lines = candidate.splitlines()
    start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("import ", "open ", "variable ", "section ", "namespace ", "set_option ")):
            start = i + 1
        elif stripped == "" and i == start:
            start = i + 1
        else:
            break
    candidate = "\n".join(lines[start:]).strip()

    # If there's a declaration keyword somewhere further in (model put prose first),
    # jump to it.
    dm = _DECL_START_RE.search(candidate)
    if dm and dm.start() > 0:
        candidate = candidate[dm.start():].strip()

    # Detect model refusal ("I cannot provide...", "This statement requires...", etc.)
    # and return empty so the repair loop gets a fresh attempt.
    refusal_phrases = (
        "i cannot provide", "i can't provide", "i'm unable", "i am unable",
        "this statement requires", "does not exist in mathlib", "not formalizable",
        "cannot be formalized",
    )
    if any(p in candidate.lower()[:200] for p in refusal_phrases):
        return ""

    # If the model output two declarations, keep only the first one.
    # Find the second declaration start (if any) after the first character.
    second = _DECL_START_RE.search(candidate, 1)
    if second:
        candidate = candidate[:second.start()].strip()

    return candidate


_DEFAULT_IMPORTS = """\
import Desol.SDE.Basic

open MeasureTheory ProbabilityTheory
"""

# Baseline import: uses the project's own module which is always compiled and
# transitively imports the probability/measure-theory/analysis core of Mathlib.
# Additional modules are added automatically if their oleans are present.
_BASELINE_IMPORTS = """\
import Desol.SDE.Basic

open MeasureTheory ProbabilityTheory
"""

# Patterns that indicate a missing Lean identifier in error output.
_UNKNOWN_IDENT_RE = re.compile(
    r"unknown identifier '([^']+)'|"
    r"unknown constant '([^']+)'|"
    r"unknown namespace '([^']+)'|"
    r"failed to synthesize\s+\n?\s*(\S+)|"
    r"application type mismatch.*?'([A-Z][A-Za-z0-9_.]+)'",
    re.MULTILINE,
)

# Extract the concrete type-class instance that Lean couldn't synthesize.
_SYNTH_INSTANCE_RE = re.compile(
    r"failed to synthesize[^\n]*\n\s*([^\n]+)",
    re.MULTILINE,
)

# Extract the name of a term used as a function incorrectly.
_FUNC_EXPECTED_RE = re.compile(r"Function expected at\s+(\S+)", re.MULTILINE)

# Lean identifiers that are likely user-defined (not in Mathlib) —
# used to decide whether to auto-stub.
_LEAN_IDENT_RE = re.compile(r"\b([A-Z][A-Za-z0-9_']*)\b")

# Cached name→source_file index (loaded once per process).
_name_module_cache: dict[str, str] | None = None
_name_module_cache_path: str = ""


def _load_name_module_index(retrieval_index_path: str) -> dict[str, str]:
    """Build name → Mathlib import path mapping from the premise index entries.jsonl."""
    global _name_module_cache, _name_module_cache_path
    if _name_module_cache is not None and _name_module_cache_path == retrieval_index_path:
        return _name_module_cache

    entries_file = Path(retrieval_index_path) / "entries.jsonl"
    if not entries_file.exists():
        _name_module_cache = {}
        _name_module_cache_path = retrieval_index_path
        return _name_module_cache

    index: dict[str, str] = {}
    with entries_file.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = entry.get("name", "")
            src = entry.get("source_file", "")
            if name and src:
                # Index by full name and by short (last component) name.
                index[name.lower()] = src
                short = name.split(".")[-1].lower()
                if short not in index:
                    index[short] = src
    _name_module_cache = index
    _name_module_cache_path = retrieval_index_path
    return index


def _olean_exists(module: str, project_root: Path) -> bool:
    """Return True if the compiled olean for `module` exists in the lake build cache."""
    rel = module.replace(".", "/") + ".olean"
    # Check in the project's mathlib package build cache.
    olean = project_root / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean" / rel
    return olean.exists()


def _resolve_missing_imports(
    error: str,
    name_index: dict[str, str],
    project_root: Path,
) -> list[str]:
    """Return import paths inferred from error messages that also have built oleans."""
    candidates: set[str] = set()
    for m in _UNKNOWN_IDENT_RE.finditer(error):
        ident = next((g for g in m.groups() if g), None)
        if not ident:
            continue
        for key in [ident.lower(), ident.split(".")[-1].lower()]:
            src = name_index.get(key)
            if src and _olean_exists(src, project_root):
                candidates.add(src)
                break
    return sorted(candidates)


def _extract_unknown_idents(
    error: str,
    name_index: dict[str, str],
    imports: str = "",
    project_root: Path | None = None,
) -> list[str]:
    """Return identifiers that appear as 'unknown' in the error and are NOT in Mathlib."""
    found = []
    for m in re.finditer(r"unknown (?:identifier|constant|namespace) '([^']+)'", error):
        ident = m.group(1)
        short = ident.split(".")[-1].lower()
        if ident.lower() in name_index or short in name_index:
            continue
        # Final gate: verify it truly doesn't exist in current imports.
        if imports and project_root and _lean_name_exists(ident, imports, project_root):
            continue
        found.append(ident)
    return found


def _build_stubs(idents: list[str]) -> str:
    """Generate sorry-backed stubs for unknown identifiers so the signature can elaborate."""
    lines = []
    for ident in idents:
        # Only stub names that look like user-defined types (start uppercase).
        # Lowercase names are Lean/Mathlib lemmas/defs — stubbing them causes
        # "already declared" conflicts even when the olean check misses them.
        base = ident.split(".")[-1]
        if not base or not base[0].isupper():
            continue
        safe = re.sub(r"[^A-Za-z0-9_]", "_", ident)
        lines.append(f"noncomputable def {safe} : Type* := sorry")
    return "\n".join(lines)


_lean_check_cache: dict[str, bool] = {}


def _lean_name_exists(name: str, imports: str, project_root: Path) -> bool:
    """Return True if `name` resolves under the given imports (via #check @name)."""
    if name in _lean_check_cache:
        return _lean_check_cache[name]
    safe = re.sub(r"[^A-Za-z0-9_.]", "_", name)
    src = f"{imports}\n\n#check @{safe}\n"
    ok, _ = _run_lean(src, project_root, timeout=20)
    _lean_check_cache[name] = ok
    return ok


def _extract_unknown_classes(
    error: str,
    imports: str,
    project_root: Path,
) -> list[str]:
    """Return type-class names from synthInstanceFailed errors that do NOT exist in Mathlib.

    Verified by running `#check @ClassName` — avoids stubbing existing classes like UniformSpace.
    """
    candidates = []
    for m in _SYNTH_INSTANCE_RE.finditer(error):
        instance_line = m.group(1).strip()
        class_name = instance_line.split()[0] if instance_line else ""
        if not class_name or not class_name[0].isupper():
            continue
        candidates.append(class_name)
    found = []
    for name in dict.fromkeys(candidates):  # deduplicate
        if not _lean_name_exists(name, imports, project_root):
            found.append(name)
    return found


def _build_class_stubs(class_names: list[str]) -> str:
    """Generate opaque class stubs + universal instances for type classes not in Mathlib."""
    lines = []
    for name in class_names:
        safe = re.sub(r"[^A-Za-z0-9_]", "_", name)
        lines.append(f"class {safe} (α : Type*) : Prop where")
        lines.append(f"instance {{α : Type*}} : {safe} α := ⟨⟩")
    return "\n".join(lines)


_MATHLIB_TC_ALLOWLIST: frozenset[str] = frozenset({
    "Add", "Mul", "Sub", "Div", "Mod", "Pow", "Neg", "Inv", "Zero", "One",
    "HAdd", "HMul", "HSub", "HDiv", "HPow", "HMod",
    "AddZeroClass", "MulOneClass", "AddMonoid", "Monoid", "AddGroup", "Group",
    "AddCommMonoid", "CommMonoid", "AddCommGroup", "CommGroup",
    "Ring", "CommRing", "Field", "DivisionRing", "EuclideanDomain",
    "Semiring", "CommSemiring", "NonUnitalRing", "NonAssocRing",
    "Module", "Algebra", "AlgebraMap",
    "SMul", "Scalar", "MulAction", "DistribMulAction",
    "OrderedSemiring", "OrderedRing", "OrderedField",
    "LinearOrder", "Preorder", "PartialOrder", "SemilatticeSup", "SemilatticeInf",
    "Lattice", "DistribLattice", "BooleanAlgebra", "CompleteLattice",
    "LE", "LT", "GE", "GT",
    "Fintype", "Finite", "Infinite", "Countable", "Uncountable",
    "DecidableEq", "Decidable", "DecidablePred", "Inhabited", "Nonempty",
    "Unique", "Subsingleton",
    "TopologicalSpace", "T0Space", "T1Space", "T2Space", "T3Space",
    "RegularSpace", "NormalSpace", "CompactSpace", "LocallyCompactSpace",
    "SecondCountableTopology", "SeparableSpace", "FirstCountableTopology",
    "MetrizableSpace", "PseudoMetrizableSpace",
    "UniformSpace", "MetricSpace", "PseudoMetricSpace", "EMetricSpace",
    "PseudoEMetricSpace", "ProperSpace",
    "TopologicalGroup", "TopologicalAddGroup", "TopologicalRing",
    "CompleteSpace",
    "NormedAddCommGroup", "SeminormedAddCommGroup", "NormedGroup",
    "NormedSpace", "NormedField", "NormedRing",
    "InnerProductSpace",
    "MeasurableSpace", "MeasurableSingletonClass",
    "MeasureSpace", "SigmaFinite", "IsFiniteMeasure", "IsProbabilityMeasure",
    "GroupWithZero", "MonoidWithZero", "MulZeroClass",
    "NoZeroDivisors", "IsDomain", "GCDMonoid", "UniqueFactorizationMonoid",
    "Nontrivial", "CharZero", "CharP", "NeZero", "Fact",
})


def _fix_invalid_binders(sig: str, error: str) -> str:
    """Automatically rewrite `[X args]` → `(h_X : X args)` for non-class binders.

    Uses hardcoded allowlist — no Lean invocations.
    """
    binder_re = re.compile(r"\[([^\[\]]+)\]")

    def rewrite_binder(m: re.Match) -> str:
        content = m.group(1).strip()
        tokens = content.split()
        if not tokens:
            return m.group(0)
        first_word = tokens[0]
        if first_word in _MATHLIB_TC_ALLOWLIST:
            return m.group(0)
        if not first_word or not first_word[0].isupper():
            return m.group(0)
        safe_name = re.sub(r"[^A-Za-z0-9_]", "_", first_word).lower()
        return f"(h_{safe_name} : {content})"

    return binder_re.sub(rewrite_binder, sig)


_ADVERSARIAL_SYSTEM = (
    "You are a formal verification critic. "
    "Given an informal LaTeX theorem and its Lean 4 formalization, identify semantic mismatches. "
    "Look for: dropped hypotheses, wrong quantifier order, weaker/stronger conclusion than stated, "
    "vacuously true statements, or type mismatches that change meaning. "
    "Also check if the Lean statement is trivially true (provable by `rfl`, `trivial`, or `decide` alone). "
    "Reply with a compact JSON object:\n"
    '{"issues": ["issue1", ...], "trivially_true": true/false, "verdict": "ok" | "suspicious" | "wrong"}\n'
    "If no issues, reply: {\"issues\": [], \"trivially_true\": false, \"verdict\": \"ok\"}"
)

_ROUNDTRIP_BACK_TRANSLATION_SYSTEM = (
    "You convert Lean theorem signatures into concise mathematical LaTeX statements. "
    "Output plain text only, no markdown and no commentary."
)

_ROUNDTRIP_EQUIV_SYSTEM = (
    "You compare two mathematical statements for semantic equivalence. "
    "Return only JSON with keys equivalent (bool) and notes (array of short strings)."
)


def adversarial_translation_check(
    *,
    latex_statement: str,
    lean_signature: str,
    client: object,
    model: str,
    api_log_hook: object = None,
) -> list[str]:
    """Use Leanstral to find semantic mismatches between LaTeX and Lean 4 formalization.

    Returns a list of adversarial flags (empty = no issues found).
    Uses only the Leanstral API — no external calls.
    """
    user_msg = (
        f"LaTeX theorem:\n{latex_statement}\n\n"
        f"Lean 4 formalization:\n{lean_signature}\n\n"
        "Identify any semantic mismatches or trivially-true issues."
    )
    try:
        _, raw = _chat_complete(
            client=client,
            model=model,
            messages=[
                {"role": "system", "content": _ADVERSARIAL_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0,
            max_tokens=256,
            purpose="adversarial_check",
            api_log_hook=api_log_hook,
        )
    except Exception:
        return []

    # Extract JSON from response — may be wrapped in markdown.
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not json_match:
        return []
    try:
        parsed = json.loads(json_match.group(0))
    except (ValueError, KeyError):
        return []

    flags: list[str] = []
    issues = parsed.get("issues", [])
    if isinstance(issues, list):
        flags.extend(str(i) for i in issues if i)
    if parsed.get("trivially_true"):
        flags.append("trivially_true")
    verdict = parsed.get("verdict", "ok")
    if verdict in ("suspicious", "wrong"):
        flags.append(f"verdict:{verdict}")
    return flags


def roundtrip_translation_check(
    *,
    latex_statement: str,
    lean_signature: str,
    client: object,
    model: str,
    api_log_hook: object = None,
) -> tuple[str | None, list[str]]:
    """Back-translate Lean to LaTeX and check semantic equivalence."""
    user_back = (
        "Convert this Lean theorem signature to one concise LaTeX math statement.\n\n"
        f"Lean 4 theorem:\n{lean_signature}\n"
    )
    try:
        _, back_text = _chat_complete(
            client=client,
            model=model,
            messages=[
                {"role": "system", "content": _ROUNDTRIP_BACK_TRANSLATION_SYSTEM},
                {"role": "user", "content": user_back},
            ],
            temperature=0.0,
            max_tokens=256,
            purpose="roundtrip_backtranslate",
            api_log_hook=api_log_hook,
        )
    except Exception as exc:
        return None, [f"roundtrip_backtranslate_error:{exc}"]

    back_translation = back_text.strip()
    if not back_translation:
        return None, ["roundtrip_empty_backtranslation"]

    user_eq = (
        "Original statement:\n"
        f"{latex_statement}\n\n"
        "Back-translated statement:\n"
        f"{back_translation}\n"
    )
    try:
        _, raw = _chat_complete(
            client=client,
            model=model,
            messages=[
                {"role": "system", "content": _ROUNDTRIP_EQUIV_SYSTEM},
                {"role": "user", "content": user_eq},
            ],
            temperature=0.0,
            max_tokens=220,
            purpose="roundtrip_equivalence",
            api_log_hook=api_log_hook,
        )
    except Exception as exc:
        return back_translation, [f"roundtrip_equivalence_error:{exc}"]

    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not json_match:
        return back_translation, ["roundtrip_invalid_json"]

    try:
        parsed = json.loads(json_match.group(0))
    except ValueError:
        return back_translation, ["roundtrip_invalid_json"]

    flags: list[str] = []
    if not bool(parsed.get("equivalent", False)):
        flags.append("roundtrip_semantic_mismatch")
    notes = parsed.get("notes")
    if isinstance(notes, list):
        flags.extend(str(n) for n in notes if str(n).strip())
    return back_translation, flags


def _build_repair_hint(error: str) -> str:
    """Return a targeted repair hint based on the Lean error category."""
    hint_parts = []

    # 1. Exact missing type class instance.
    synth_matches = _SYNTH_INSTANCE_RE.findall(error)
    if synth_matches:
        instances = [m.strip() for m in synth_matches[:3]]
        inst_str = ", ".join(f"[{i}]" for i in instances)

        # Look up each failing class in the TC graph / concept map for a targeted fix.
        specific_fixes: list[str] = []
        for inst in instances:
            class_name = inst.split()[0] if inst else ""
            if class_name:
                hint = _get_class_hint(class_name)
                if hint:
                    specific_fixes.append(hint)

        specific_str = (
            "\nSPECIFIC FIXES for the failing classes:\n"
            + "\n".join(f"  - {f}" for f in specific_fixes)
            if specific_fixes
            else ""
        )

        hint_parts.append(
            f"Type class synthesis failed for: {inst_str}. "
            "Likely causes and fixes:\n"
            "(a) REDUNDANT instances: Mathlib has a type class hierarchy — do NOT list implied classes. "
            "Rules: `[NormedSpace 𝕜 E]` implies `[NormedAddCommGroup E]` and `[SeminormedAddCommGroup E]`; "
            "`[InnerProductSpace 𝕜 E]` implies `[NormedSpace 𝕜 E]`; "
            "`[MetricSpace α]` implies `[TopologicalSpace α]`, `[UniformSpace α]`, `[PseudoMetricSpace α]`; "
            "`[NormedAddCommGroup E]` implies `[AddCommGroup E]`, `[TopologicalAddGroup E]`. "
            "KEEP ONLY the most specific class — remove all implied ones.\n"
            "(b) NON-MATHLIB classes: If the class doesn't exist in Mathlib "
            "(e.g. GeodesicSpace, LengthSpace, Cat κ, CBA, StronglyConvex, LocallyLipschitz as a class), "
            "DO NOT use it at all. Replace with the closest standard Mathlib alternative "
            "or use `{X : Type*}` with explicit hypothesis `(h : SomeCondition X)` instead."
            + specific_str
        )

    # 2. Term used as function.
    func_expected = _FUNC_EXPECTED_RE.findall(error)
    if func_expected:
        names = ", ".join(f"`{n}`" for n in func_expected[:2])
        # Check if the failing term looks like an asymptotic expression
        asym_hint = ""
        if any(n in ("o", "O", "Θ", "IsLittleO", "IsBigO") for n in func_expected):
            asym_hint = (
                " ASYMPTOTIC EXPRESSIONS: `o(n)`, `O(n)`, `(1 + o(1))`, `o[Filter.atTop]` are NOT values. "
                "You CANNOT use them in arithmetic like `(c + o(1)) * n^k`. "
                "Instead, introduce an explicit error function: "
                "`∃ ε : ℕ → ℝ, ε =o[Filter.atTop] (fun _ => (1:ℝ)) ∧ f n = (c + ε n) * n^k`."
            )
        hint_parts.append(
            f"{names} is a term (a type or a constant), not a function — you cannot apply it to arguments. "
            "Common causes: "
            "(a) Big-O/little-o notation: `o(n)`, `O(n)`, `Θ(n)` are not valid Lean 4. "
            "Use Mathlib's infix relation instead: `f =o[Filter.atTop] g` for little-o, `f =O[Filter.atTop] g` for big-O, "
            "where `f g : ℕ → ℝ`. For asymptotic equalities like `G n = (c + o(1)) * 4^n / n^(3/4)`, "
            "express as: `∃ ε : ℕ → ℝ, (ε =o[Filter.atTop] (fun _ => 1)) ∧ G n = (c + ε n) * 4^n / n^(3/4)`. "
            "(b) A type used as a value — use it as a type annotation with `:`. "
            "(c) A measure `μ` used as a function — use `μ.toFun` or write `μ.measure_of`."
            + asym_hint
        )

    # 3. Application type mismatch — extract what type was expected.
    if "Application type mismatch" in error or "Type mismatch" in error:
        # Extract "has type X" and "expected to have type Y" from error.
        has_type = re.search(r"has type\n?\s*([^\n]+)", error)
        expected_type = re.search(r"expected to have type\n?\s*([^\n]+)", error)
        mismatch_detail = ""
        if has_type and expected_type:
            mismatch_detail = (
                f" The argument has type `{has_type.group(1).strip()}` "
                f"but type `{expected_type.group(1).strip()}` was expected."
            )
        # Detect Fin-related mismatches for targeted guidance.
        fin_hint = ""
        arg_text = has_type.group(1).strip() if has_type else ""
        exp_text = expected_type.group(1).strip() if expected_type else ""
        if "Fin" in arg_text or "Fin" in exp_text or "Fin" in error[:300]:
            fin_hint = (
                " Fin-index fix: `j : Fin k` cannot be passed where `ℕ` is expected — use `j.val` or `↑j`. "
                "Conversely, `n : ℕ` cannot be passed where `Fin k` is expected — use `⟨n, hn⟩` with a proof `hn : n < k`. "
                "AVOID inline proofs like `⟨0, Nat.zero_lt_succ n⟩` in signatures — they are fragile. "
                "Instead add `(hn : 0 < n)` as a hypothesis and use `⟨0, hn⟩`, or use `[NeZero n]`."
            )
        hint_parts.append(
            f"Type mismatch error.{mismatch_detail}{fin_hint} "
            "Common fixes: "
            "(a) coerce with `(x : ExpectedType)` or use `↑x` for numeric coercions (e.g. `↑j` converts `Fin k` to `ℕ`); "
            "(b) if passing a function where a type is expected, you may need `fun x => f x` instead; "
            "(c) if the universe is wrong (`Type` vs `Type*`), add universe polymorphism; "
            "(d) for `ℝ`/`ℕ`/`ℤ` mismatches, use `(n : ℝ)` or `Int.ofNat n` coercions; "
            "(e) for `Set α` used where `Type` is expected, the issue is a universe level — "
            "use `Subtype` instead: `{x : α // x ∈ S}` instead of `(S : Set α)`. "
            "(f) for product/pair mismatches `(a, b)`, check that both components have the right type."
        )

    # 4. unexpected token ':=' — where clause or definition syntax.
    if "unexpected token ':='" in error:
        hint_parts.append(
            "Do not use `:=` inside a `where` clause or after a `:` type annotation. "
            "In Lean 4 theorem signatures, only use `(x : T)` binders. "
            "Move any definitions outside the theorem signature."
        )

    # 4. unexpected token in binder.
    if "invalid binder annotation" in error or "type is not a class" in error:
        hint_parts.append(
            "Square brackets `[...]` are only for type class instances. "
            "Use `(x : T)` for ordinary hypotheses and `{x : T}` for implicit arguments."
        )

    # 5. unexpected token 'in' — Lean 3 syntax used instead of Lean 4
    if "unexpected token 'in'" in error:
        hint_parts.append(
            "You are using Lean 3 syntax. In Lean 4, `in` is never used in binders or sum notation. "
            "Replacements: "
            "(a) `∀ x in S, P x` → `∀ x ∈ S, P x` (use ∈ symbol, not `in`); "
            "(b) `∑ i in S, f i` → `∑ i ∈ S, f i` (use ∈ symbol); "
            "(c) `∏ i in S, f i` → `∏ i ∈ S, f i`; "
            "(d) `∃ x in S, P x` → `∃ x ∈ S, P x`. "
            "Replace ALL occurrences of ` in ` inside binders and sum/product notation."
        )

    # 6. unexpected token '!' — model wrote n! (factorial notation, not Lean 4)
    if "unexpected token '!'" in error:
        hint_parts.append(
            "The `!` postfix (factorial) is not valid Lean 4 syntax. "
            "Replace `n!` with `n.factorial` or `Nat.factorial n`. "
            "Similarly, `(n k)!` should be written as `Nat.factorial (n - k)`."
        )

    # 7. unexpected token '↔' — model put ↔ inside a binder or where it expects :=
    if "unexpected token '↔'" in error:
        hint_parts.append(
            "Do not use `↔` directly in a binder. "
            "The return type of a theorem should use `↔` in the *type* position after `:`, "
            "e.g. `theorem foo : P ↔ Q := by ...`."
        )

    # 8. unexpected token 'λ' — Lean 3 lambda or eigenvalue notation
    if "unexpected token 'λ'" in error or "unexpected token 'lambda'" in error:
        hint_parts.append(
            "The symbol `λ` is invalid in Lean 4. TWO common causes:\n"
            "(a) Lean 3 lambda syntax `λ x, ...` → replace with `fun x => ...`.\n"
            "(b) Eigenvalue notation `λ_max(M)`, `λ_i(M)` etc. — these are NOT valid Lean 4. "
            "Replace eigenvalue expressions with hypotheses: "
            "instead of writing `λ_max(M)` directly, add a parameter `(c : ℝ)` and hypothesis "
            "`(hspec : ∀ v, ‖M.mulVec v‖ ≤ c * ‖v‖)` or use `(hspec : Matrix.spectralNorm M ≤ c)`. "
            "Replace ALL occurrences of `λ` — check both uses."
        )

    # 9. unexpected token '[' expected ',' — model used [...] in tuple or wrong position
    if "unexpected token '['" in error and "expected ','" in error:
        hint_parts.append(
            "Square brackets `[...]` appeared where a comma or tuple element was expected. "
            "Do NOT use `[...]` for tuples or product types — use `(a, b)` for pairs. "
            "Square brackets are ONLY for type class instances in binders."
        )

    # 10. unexpected token 'where' — model tried to use a where clause in the signature
    if "unexpected token 'where'" in error:
        hint_parts.append(
            "Do not use a `where` clause inside a theorem signature. "
            "In Lean 4 theorem signatures, all binders must appear before the `:` return type. "
            "Move any helper definitions outside the theorem, or use `let` inside the proof body."
        )

    # 11. unexpected token '|' — model used pattern matching or inductive syntax in signature
    if "unexpected token '|'" in error:
        hint_parts.append(
            "The `|` character is not valid in a theorem signature. "
            "Do not use pattern matching or inductive case syntax in the signature. "
            "Express case distinctions as disjunctions in the statement type, e.g. `P ∨ Q` or `∃ n, ...`."
        )

    # 12. unexpected token 'with' — Lean 3 match/with syntax
    if "unexpected token 'with'" in error:
        hint_parts.append(
            "The `with` keyword is not valid here. "
            "In Lean 4, match expressions use `match x with | ...`, but this belongs in the proof, "
            "not in the theorem signature. Remove `with` from the signature."
        )

    # 13. unexpected token ',' — misplaced comma, often from ∀ x, y : T
    if "unexpected token ','" in error:
        hint_parts.append(
            "Unexpected comma in signature. "
            "In Lean 4, `∀ (x y : T), P` is correct (comma after the binder group, not between variables). "
            "Do NOT write `∀ x, y : T` — write `∀ (x y : T)` or `∀ x : T, ∀ y : T`. "
            "Also check that you are not using Lean 3 lambda syntax `λ x, ...` — use `fun x => ...`."
        )

    # 14. unexpected token '(' / '{' expected id — missing theorem name
    if ("unexpected token '('" in error or "unexpected token '{'" in error) and "expected id" in error:
        hint_parts.append(
            "The theorem is missing a name. In Lean 4, `theorem` and `lemma` must be followed by an identifier. "
            "Add a name: `theorem my_theorem_name {α : Type*} ...` — do NOT start with `theorem {` or `theorem (`."
        )

    # 15. unexpected token 'fun' — `fun` used in a type position inside the return type
    if "unexpected token 'fun'" in error:
        hint_parts.append(
            "The keyword `fun` appeared in a type position. "
            "In a theorem *signature*, the return type (after `:`) must be a proposition (Prop), not a function. "
            "Common causes: (a) you wrote `∀ x, fun y => ...` — this is wrong; use `∀ x y, ...` instead. "
            "(b) you used a lambda in the type — replace with a universally quantified statement. "
            "(c) you confused `:= fun x => ...` (a definition body) with the type."
        )

    # 16. `don't know how to synthesize implicit` — variant of synthInstanceFailed
    if "don't know how to synthesize" in error or "cannot synthesize" in error:
        hint_parts.append(
            "Lean cannot synthesize an implicit argument. "
            "Make the argument explicit: if Lean can't infer a type or class, add it as an explicit binder `(x : T)` or `[C α]`. "
            "Check that all type class instances needed are listed in the signature."
        )

    # 17. invalidField — method/field access on wrong type
    if "invalidField" in error or "Invalid field" in error:
        field_match = re.search(r"field '([^']+)' .* '([^']+)'", error)
        field_hint = ""
        if field_match:
            field_hint = f" `{field_match.group(2)}` has no field `{field_match.group(1)}`."
        hint_parts.append(
            f"Invalid field access.{field_hint} "
            "Common causes: (a) using `.PosDef` on a scalar — PosDef is only for Matrix; "
            "(b) using `.card` on a Finset when you need `Finset.card s`; "
            "(c) using `M.spectralNorm` — the correct Lean4 name is `Matrix.spectralNorm M`; "
            "(d) the type is abstract (`α : Type*`) and has no such field — use a hypothesis instead."
        )

    # 18. overloaded notation errors — ambiguous notation resolved to multiple failures
    if "overloaded, errors" in error:
        hint_parts.append(
            "There is an ambiguous notation that Lean cannot resolve. "
            "Common causes: "
            "(a) Unicode operators used incorrectly — `≺`, `≻`, `⊆`, `⊂` etc. must be applied to compatible types. "
            "For matrix positive definiteness, use `M.PosDef` (a Prop), not `M ≻ 0`. "
            "(b) `∑` or `∏` with wrong argument types — check the index type matches `Finset` or `Fintype`. "
            "(c) Coercion missing — add explicit cast like `(n : ℝ)` or `(k : ℤ)`. "
            "Simplify the notation or make types explicit."
        )

    # unexpected '∧' — conjunction in wrong position (often inside ∃ binder instead of body)
    if "unexpected token '∧'" in error:
        hint_parts.append(
            "The `∧` (and) operator appeared in an unexpected position. "
            "Most likely cause: `∃ x, P ∧ Q` is correct, but if `∧` appears inside a binder "
            "like `∃ (x : T ∧ U)`, that is wrong — binders take a type, not a conjunction. "
            "Fix: move all conjuncts into the BODY after the `,`: `∃ (x : T), P x ∧ Q x`. "
            "For existentials with multiple conditions: `∃ (x : T) (y : S), cond1 ∧ cond2`. "
            "Do NOT nest `∧` inside a binder type annotation."
        )

    # unexpected '‖' — norm notation used as a binder name or in wrong position
    if "unexpected token '‖'" in error:
        hint_parts.append(
            "The norm notation `‖·‖` cannot be used as a variable name or binder. "
            "Common causes: "
            "(a) Writing `∃ ‖·‖ : E → ℝ, ...` to say 'there exists a norm' — "
            "instead use `∃ _ : NormedAddCommGroup E, ...` or add `[NormedAddCommGroup E]` as a typeclass. "
            "(b) The norm bars `‖x‖` appearing in a binder type — move them to the hypothesis body. "
            "For 'E is normable', use `[NormedAddCommGroup E]` directly."
        )

    # generic unexpected token fallback
    if "unexpected token" in error and not hint_parts:
        hint_parts.append(
            "There is a syntax error. Check that all parentheses and brackets are balanced, "
            "that `:` is used for type annotations, and that the declaration ends with `:= by`."
        )

    if not hint_parts:
        hint_parts.append(
            "Fix the type error and output the corrected signature."
        )

    return " ".join(hint_parts)


def _is_irrecoverable(error: str, stubs: str) -> bool:
    """Return True when the error cannot be fixed by further model repair rounds.

    Criteria: the failing name is a stub we already created (not a Mathlib name).
    Spending more API calls asking the model to "fix" something outside Mathlib is wasteful.
    """
    # "type expected, got (X : Type..." — model used a type class as a value expression
    if "type expected, got" in error:
        return True
    # "Function expected at X" — X is a stub (already tried upgrading in Try 4)
    func_names = _FUNC_EXPECTED_RE.findall(error)
    for fname in func_names:
        safe = re.sub(r"[^A-Za-z0-9_]", "_", fname)
        if f"def {safe}" in stubs or f"class {safe}" in stubs:
            return True
    return False


def _run_lean(lean_src: str, project_root: Path, timeout: int) -> tuple[bool, str]:
    """Write lean_src to a temp file, run lake env lean, return (ok, error)."""
    import uuid
    tmp_name = f"_tmp_validate_{uuid.uuid4().hex[:8]}.lean"
    tmp_path = project_root / "Desol" / tmp_name
    try:
        tmp_path.write_text(lean_src, encoding="utf-8")
        lake_bin = shutil.which("lake") or os.path.expanduser("~/.elan/bin/lake")
        proc = subprocess.run(
            [lake_bin, "env", "lean", str(tmp_path)],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        combined = (proc.stderr or "") + (proc.stdout or "")
        if proc.returncode == 0 and "error:" not in combined:
            return True, ""
        return False, combined.strip()
    except subprocess.TimeoutExpired:
        return False, f"lake env lean timed out after {timeout}s"
    finally:
        tmp_path.unlink(missing_ok=True)


def _check_vacuous(
    signature: str,
    *,
    project_root: Path,
    imports: str = "",
    timeout: int = 30,
) -> bool:
    """Return True if the statement is trivially provable by `trivial` or `simp`.

    A statement that compiles as `theorem _vac : <type> := by trivial` is
    almost certainly wrong — it either maps to `True`, a tautology, or was
    mistranslated into a weaker claim than intended.  We run this as a cheap
    deterministic check before the LLM round-trip judge.
    """
    sig = re.sub(r":=\s*by\b.*$", "", signature, flags=re.DOTALL).strip()
    sig = re.sub(r":=\s*$", "", sig).strip()
    if not sig:
        return False

    # Replace the theorem name with a fixed sentinel so naming never collides.
    vac_sig = re.sub(r"^(theorem|lemma)\s+\S+", r"\1 _desol_vacuity_check", sig, count=1)
    lean_src = f"{imports}\n\n{vac_sig} := by trivial\n" if imports.strip() else f"{vac_sig} := by trivial\n"

    try:
        ok, _ = _run_lean(lean_src, project_root=project_root, timeout=timeout)
        return ok
    except Exception:
        return False


def _validate_signature(
    signature: str,
    *,
    project_root: Path,
    imports: str = "",
    timeout: int = 60,
    retrieval_index_path: str = "",
) -> tuple[bool, str]:
    """Return (ok, error_message).

    Automatically expands imports when the error indicates a missing identifier:
    looks up the name in the premise index to find the correct Mathlib module,
    then retries.  Falls back to returning the last error after _MAX_IMPORT_EXPANSIONS.
    """
    _MAX_IMPORT_EXPANSIONS = 4

    # Strip any existing body so we can attach sorry cleanly.
    sig = re.sub(r":=\s*by\b.*$", "", signature, flags=re.DOTALL).strip()
    sig = re.sub(r":=\s*$", "", sig).strip()

    # Gate: reject non-proposition declarations immediately — don't waste a Lean round-trip.
    first_kw = sig.strip().split()[0] if sig.strip() else ""
    if first_kw in ("def", "noncomputable", "structure", "class", "instance", "abbrev"):
        return False, (
            f"declaration starts with `{first_kw}` — only `theorem` or `lemma` are accepted. "
            "The input LaTeX is a definition; translate it as a proposition about its properties."
        )

    # Pre-validate fixes: deterministic rewrites that don't need a Lean round-trip to detect.

    # Fix 1: Missing theorem name — `theorem {` or `theorem (` → `theorem _anon {`
    sig = re.sub(r"^(theorem|lemma)\s+([{(])", r"\1 _anon \2", sig, flags=re.MULTILINE)

    # Fix 2: `λ` used as a variable name (e.g. `(λ : ℝ)`) → rename to `lam`.
    sig = re.sub(r"\(λ\s*:", "(lam :", sig)
    sig = re.sub(r"\{λ\s*:", "{lam :", sig)

    # Fix 3: `noncomputable` theorem with body — strip `noncomputable` from theorem/lemma decl.
    # (noncomputable is valid only on defs, not theorems)
    sig = re.sub(r"^noncomputable\s+(theorem|lemma)\b", r"\1", sig, flags=re.MULTILINE)

    # P2 Security Validation: theorem name and signature format (before Lean compilation)
    try:
        # Extract theorem name (first identifier after theorem/lemma keyword)
        match = re.search(r"^(theorem|lemma)\s+([a-z_][a-z0-9_']*)", sig, re.MULTILINE | re.IGNORECASE)
        if match:
            theorem_name = match.group(2)
            # Validate theorem name format
            validate_theorem_name(theorem_name)
        
        # Validate full signature for injection attempts
        validate_theorem_signature(sig)
    except ValueError as e:
        # If validation fails, try to sanitize (e.g., remove LaTeX sequences)
        try:
            sig = validate_and_sanitize_signature(sig)
        except ValueError as sanitize_err:
            return False, f"Signature validation failed (P2): {e}\nSanitization also failed: {sanitize_err}"

    # Choose starting import header.
    if imports.strip():
        current_imports = imports.strip()
    else:
        # Use broad baseline, not just SDE.Basic, so arbitrary papers can elaborate.
        current_imports = _BASELINE_IMPORTS.strip()

    # Load name→module index once if available.
    name_index: dict[str, str] = {}
    if retrieval_index_path:
        try:
            name_index = _load_name_module_index(retrieval_index_path)
        except Exception:
            pass

    added_modules: set[str] = set()
    stubs: str = ""

    for _attempt in range(_MAX_IMPORT_EXPANSIONS + 1):
        lean_src = f"{current_imports}\n\n{stubs}\n{sig} := by sorry\n"
        ok, err = _run_lean(lean_src, project_root, timeout)
        if ok:
            return True, "", False

        if _attempt >= _MAX_IMPORT_EXPANSIONS:
            return False, err, _is_irrecoverable(err, stubs)

        # Try 1: resolve missing identifiers to Mathlib modules via olean check.
        new_modules = [
            m for m in _resolve_missing_imports(err, name_index, project_root)
            if m not in added_modules
        ]
        if new_modules:
            for mod in new_modules:
                added_modules.add(mod)
                if f"import {mod}" not in current_imports:
                    current_imports = f"import {mod}\n{current_imports}"
            continue

        # Try 2: auto-stub unknown identifiers not in Mathlib at all.
        unknown = _extract_unknown_idents(err, name_index, current_imports, project_root)
        if unknown:
            new_stubs = _build_stubs(unknown)
            if new_stubs and new_stubs not in stubs:
                stubs = stubs + "\n" + new_stubs if stubs else new_stubs
                continue

        # Try 3a: rewrite non-Mathlib classes using the concept map / TC graph.
        synth_class_matches = _SYNTH_INSTANCE_RE.findall(err)
        rewritten = False
        for inst_line in synth_class_matches:
            class_name = inst_line.strip().split()[0] if inst_line.strip() else ""
            replacement = _get_lean_replacement(class_name)
            if replacement and not replacement.startswith("(h"):
                # Replace `[ClassNameX ...]` binder in sig with replacement.
                binder_pattern = re.compile(
                    r"\[" + re.escape(class_name) + r"[^\]]*\]"
                )
                new_sig = binder_pattern.sub(replacement, sig)
                if new_sig != sig:
                    sig = new_sig
                    rewritten = True
        if rewritten:
            continue

        # Try 3b: auto-stub type classes that failed synthesis and are not in Mathlib.
        unknown_classes = _extract_unknown_classes(err, current_imports, project_root)
        if unknown_classes:
            new_stubs = _build_class_stubs(unknown_classes)
            if new_stubs and new_stubs not in stubs:
                stubs = stubs + "\n" + new_stubs if stubs else new_stubs
                continue

        # Try 4: "Function expected at X" on a previously no-arg stubbed def — upgrade to 1-arg.
        func_names = _FUNC_EXPECTED_RE.findall(err)
        upgraded = False
        for fname in func_names:
            safe = re.sub(r"[^A-Za-z0-9_]", "_", fname)
            old_stub = f"noncomputable def {safe} : Type* := sorry"
            new_stub = f"noncomputable def {safe} (α : Type*) : Type* := sorry"
            if old_stub in stubs:
                stubs = stubs.replace(old_stub, new_stub)
                upgraded = True
        if upgraded:
            continue

        # Try 5: `invalid binder annotation` — rewrite offending [P] → (h_P : P) in signature.
        if "invalid binder annotation" in err or "type is not a class" in err:
            new_sig = _fix_invalid_binders(sig, err)
            if new_sig != sig:
                sig = new_sig
                continue

        # Try 6: Lean 3 lambda syntax `λ x,` → `fun x =>` (deterministic text rewrite).
        # Also handles eigenvalue notation like `λ_max`, `λ_min`, `λ_i` which Lean parses as lambda.
        if "unexpected token 'λ'" in err or "unexpected token 'lambda'" in err:
            # First: replace eigenvalue notation λ_max/λ_min/λ_i with a descriptive identifier.
            new_sig = re.sub(r"λ_max\s*\(([^)]+)\)", r"Matrix.eigenvalues_max (\1)", sig)
            new_sig = re.sub(r"λ_min\s*\(([^)]+)\)", r"Matrix.eigenvalues_min (\1)", new_sig)
            new_sig = re.sub(r"λ_(\w+)\s*\(([^)]+)\)", r"eigenvalue_\1 (\2)", new_sig)
            # Then: replace any remaining Lean 3 lambda syntax.
            new_sig = re.sub(r"λ\s+([^,\n]+),\s*", lambda m: f"fun {m.group(1).strip()} => ", new_sig)
            new_sig = new_sig.replace("λ ", "fun ")
            if new_sig != sig:
                sig = new_sig
                continue

        # Try 7: `in` keyword in binders — Lean 3 `∑ i in S` → `∑ i ∈ S`
        if "unexpected token 'in'" in err:
            new_sig = re.sub(r"\b(∑|∏|∀|∃)\s+(\w+)\s+in\s+", r"\1 \2 ∈ ", sig)
            if new_sig != sig:
                sig = new_sig
                continue

        # No progress possible via imports or stubs.
        # Check if the error is irrecoverable (stub-based failure) — signal early exit.
        irrecoverable = _is_irrecoverable(err, stubs)
        return False, err, irrecoverable

    return False, err, False


def translate_statement(
    *,
    latex_statement: str,
    latex_proof_hint: str = "",
    client: object,
    model: str,
    project_root: Path,
    imports: str = "",
    max_repair_rounds: int = 3,
    temperature: float = 0.2,
    api_log_hook: object = None,
    retrieval_index_path: str = "data/mathlib_embeddings",
    run_adversarial_check: bool = True,
    run_roundtrip_check: bool = True,
) -> TranslationResult:
    """Translate a LaTeX statement to a validated Lean 4 signature.

    imports: if empty, the broad _BASELINE_IMPORTS is used and auto-expanded as needed.
    retrieval_index_path: premise index used to resolve missing identifiers to Mathlib modules.
    """
    user_parts = [f"LaTeX statement:\n{latex_statement}"]
    if latex_proof_hint.strip():
        user_parts.append(f"Informal proof context:\n{latex_proof_hint.strip()}")
    user_parts.append(
        "Output the Lean 4 theorem signature inside <signature>...</signature>. "
        "Use standard Mathlib4 naming and type class conventions."
    )

    messages = [
        {"role": "system", "content": _get_translate_system()},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]

    last_error = ""
    signature = ""

    for round_idx in range(1, max_repair_rounds + 2):  # +1 for initial attempt
        _, text = _chat_complete(
            client=client,
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=2000,
            purpose=f"translate_round_{round_idx}",
            api_log_hook=api_log_hook,
        )
        signature = _extract_signature(text)

        ok, last_error, irrecoverable = _validate_signature(
            signature,
            project_root=project_root,
            imports=imports,
            retrieval_index_path=retrieval_index_path,
        )

        if ok:
            # Vacuity check: if `trivial` closes the goal, the statement is too
            # weak — it likely maps to True or was mistranslated to a tautology.
            # This is a cheap deterministic Lean call that runs before any LLM
            # judge and is the most reliable false-positive filter.
            if project_root is not None and _check_vacuous(
                signature,
                project_root=project_root,
                imports=imports,
            ):
                ok = False
                last_error = "vacuity: statement is trivially provable by `trivial` — likely mistranslated to True or a tautology"
                irrecoverable = False
                # Feed the vacuity signal back as a repair hint and continue.
                messages.append({"role": "assistant", "content": text})
                messages.append({
                    "role": "user",
                    "content": (
                        "The generated signature is trivially true (provable by `trivial` alone). "
                        "This means the translation is too weak — it likely collapses to `True` or "
                        "a tautology and does not capture the actual mathematical content.\n\n"
                        "Re-translate the original LaTeX statement into a non-trivial Lean 4 theorem "
                        "that faithfully encodes the mathematical claim. "
                        "Output the corrected signature inside <signature>...</signature>."
                    ),
                })
                continue

            confidence, flags = _confidence_from_translation_state(
                validated=True,
                rounds_used=round_idx,
                last_error="",
                signature=signature,
            )
            adv_flags: list[str] = []
            if run_adversarial_check and latex_statement.strip():
                adv_flags = adversarial_translation_check(
                    latex_statement=latex_statement,
                    lean_signature=signature,
                    client=client,
                    model=model,
                    api_log_hook=api_log_hook,
                )
                if adv_flags:
                    # Penalise confidence when adversarial check finds issues.
                    penalty = min(0.30, 0.10 * len(adv_flags))
                    confidence = max(0.0, confidence - penalty)
                    if "trivially_true" in adv_flags:
                        flags.append("trivially_true_detected")
                    if any(f.startswith("verdict:") for f in adv_flags):
                        flags.append("adversarial_mismatch")

            roundtrip_back_translation: str | None = None
            roundtrip_flags: list[str] = []
            if run_roundtrip_check and latex_statement.strip() and signature.strip():
                roundtrip_back_translation, roundtrip_flags = roundtrip_translation_check(
                    latex_statement=latex_statement,
                    lean_signature=signature,
                    client=client,
                    model=model,
                    api_log_hook=api_log_hook,
                )
                if roundtrip_flags:
                    penalty = min(0.25, 0.08 * len(roundtrip_flags))
                    confidence = max(0.0, confidence - penalty)
                    flags.append("roundtrip_semantic_risk")
            return TranslationResult(
                lean_signature=signature,
                validated=True,
                rounds_used=round_idx,
                last_error="",
                confidence=confidence,
                uncertainty_flags=flags,
                adversarial_flags=adv_flags,
                roundtrip_back_translation=roundtrip_back_translation,
                roundtrip_flags=roundtrip_flags,
            )

        if round_idx > max_repair_rounds or irrecoverable:
            break

        # Repair: targeted hint derived from the specific Lean error.
        hint = _build_repair_hint(last_error)
        messages.append({"role": "assistant", "content": text})
        messages.append({
            "role": "user",
            "content": (
                f"The signature failed to elaborate:\n{last_error}\n\n"
                f"{hint}\n\n"
                "Output the corrected signature inside <signature>...</signature>. "
                "Only the theorem/lemma declaration — no imports, no variable blocks."
            ),
        })

    # All rounds exhausted — return last attempt as a sorry stub.
    sorry_stub = signature if signature else f"-- TRANSLATION FAILED: {latex_statement[:80]}"
    confidence, flags = _confidence_from_translation_state(
        validated=False,
        rounds_used=max_repair_rounds + 1,
        last_error=last_error,
        signature=sorry_stub,
    )
    # Generate decomposition stubs for missing types/identifiers.
    stubs: list[dict] = []
    if last_error and client is not None:
        stubs = generate_decomposition_stubs(
            lean_signature=sorry_stub,
            lean_error=last_error,
            client=client,
            model=model,
            api_log_hook=api_log_hook,
        )
    return TranslationResult(
        lean_signature=sorry_stub,
        validated=False,
        rounds_used=max_repair_rounds + 1,
        last_error=last_error,
        confidence=confidence,
        uncertainty_flags=flags,
        decomposition_stubs=stubs,
    )


_STUB_SYSTEM = (
    "You are a Lean 4 type stub generator. "
    "Given a Lean 4 theorem signature that failed to elaborate because of unknown types or structures, "
    "identify the missing definitions and generate minimal `sorry`-backed stubs. "
    "Output a JSON list of stub objects:\n"
    '[{"name": "TypeName", "kind": "structure|def|abbrev|class", '
    '"lean_stub": "-- Stub for missing type\\nstructure TypeName where\\n  sorry_field : Unit := ()"}]\n'
    "Keep stubs minimal. Use `sorry`-backed implementations. "
    "If no stubs are needed, output an empty list []."
)

# Regex to find unknown identifier errors from Lean: "unknown identifier 'Foo'"
_UNKNOWN_ID_RE = re.compile(r"unknown (?:identifier|constant|type)\s+'([^']+)'")


def generate_decomposition_stubs(
    *,
    lean_signature: str,
    lean_error: str,
    client: object,
    model: str,
    api_log_hook: object = None,
) -> list[dict]:
    """When translation fails due to unknown types/identifiers, generate sorry-backed stubs.

    Each stub is a minimal Lean 4 definition that satisfies the type-checker enough
    for the theorem statement to elaborate. These become KG seed entries with
    UNGROUNDED status — targets for later proof search.

    Returns a list of {name, kind, lean_stub} dicts.
    """
    # Quick check: does the error mention unknown identifiers?
    known_missing = _UNKNOWN_ID_RE.findall(lean_error)

    user_msg = (
        f"Lean 4 theorem signature:\n{lean_signature}\n\n"
        f"Elaboration error:\n{lean_error}\n\n"
    )
    if known_missing:
        user_msg += f"Unknown identifiers detected: {', '.join(known_missing)}\n\n"
    user_msg += "Generate minimal sorry-backed stubs for all missing definitions."

    try:
        _, raw = _chat_complete(
            client=client,
            model=model,
            messages=[
                {"role": "system", "content": _STUB_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=600,
            purpose="generate_decomposition_stubs",
            api_log_hook=api_log_hook,
        )
    except Exception:
        return []

    # Extract JSON list from response.
    json_match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not json_match:
        return []
    try:
        stubs = json.loads(json_match.group(0))
        if not isinstance(stubs, list):
            return []
        return [s for s in stubs if isinstance(s, dict) and "name" in s and "lean_stub" in s]
    except (ValueError, KeyError):
        return []


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Translate LaTeX theorem statement to Lean 4")
    p.add_argument("--statement", required=True, help="LaTeX statement text")
    p.add_argument("--proof-hint", default="", help="Optional informal proof hint")
    p.add_argument("--project-root", default=".", help="Lean project root for validation")
    p.add_argument(
        "--imports",
        default="",
        help="Override Lean import header (default: broad baseline + auto-expansion)",
    )
    p.add_argument(
        "--retrieval-index",
        default="data/mathlib_embeddings",
        help="Premise index directory for auto-import resolution",
    )
    p.add_argument("--model", default="", help="Mistral model (defaults to MISTRAL_MODEL env)")
    p.add_argument("--max-repair-rounds", type=int, default=3)
    p.add_argument("--temperature", type=float, default=0.2)
    return p


def main() -> int:
    load_dotenv()
    args = _build_parser().parse_args()

    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        print("[fail] MISTRAL_API_KEY is not set", file=sys.stderr)
        return 1

    model = args.model.strip() or os.getenv("MISTRAL_MODEL", "labs-leanstral-2603").strip()

    try:
        from mistralai import Mistral
    except ImportError:
        from mistralai.client import Mistral  # type: ignore[no-redef]

    client = Mistral(api_key=api_key)

    result = translate_statement(
        latex_statement=args.statement,
        latex_proof_hint=args.proof_hint,
        client=client,
        model=model,
        project_root=Path(args.project_root).resolve(),
        imports=args.imports,
        retrieval_index_path=args.retrieval_index,
        max_repair_rounds=args.max_repair_rounds,
        temperature=args.temperature,
    )

    status = "validated" if result.validated else "unvalidated"
    print(f"[{status}] rounds={result.rounds_used}")
    if result.last_error:
        print(f"[last_error] {result.last_error[:200]}")
    print("=== SIGNATURE ===")
    print(result.lean_signature)
    return 0 if result.validated else 1


if __name__ == "__main__":
    sys.exit(main())
