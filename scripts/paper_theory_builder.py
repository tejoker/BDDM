#!/usr/bin/env python3
"""Paper Theory Builder: generate a paper-local Lean theory module.

Writes a Lean module under `Desol/PaperTheory/` so other generated files can:
  import Desol.PaperTheory.Paper_<safe_id>

This is intentionally conservative: it only declares *explicit* paper-local
symbols as axioms/stubs when they are clearly referenced by translations.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from domain_packs import get_domain_pack
from paper_symbol_inventory import (
    PaperSymbolDecl,
    build_symbol_inventory,
    declaration_name,
    load_inventory_json,
    symbols_to_manifest,
)


def _safe_id(paper_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", (paper_id or "").strip())


def _module_name(paper_id: str) -> str:
    return f"Paper_{_safe_id(paper_id)}"


@dataclass(frozen=True)
class PaperTheoryPlan:
    paper_id: str
    domain: str
    module_name: str
    imports: list[str]
    open_scopes: list[str]
    definitions: list[str]
    lemmas: list[str]
    axioms: list[str]
    symbols: list[PaperSymbolDecl]
    manifest: dict[str, object]
    notes: list[str]


def plan_paper_theory(
    *,
    paper_id: str,
    domain: str,
    seed_text: str = "",
    inventory: list[PaperSymbolDecl] | None = None,
    glossary: dict[str, str] | None = None,
    schemas: list[dict] | None = None,
    entries: list[object] | None = None,
    real_definitions: list[str] | None = None,
) -> PaperTheoryPlan:
    pack = get_domain_pack(domain)
    module = _module_name(paper_id)
    notes: list[str] = []
    symbols = inventory if inventory is not None else build_symbol_inventory(
        seed_text=seed_text,
        glossary=glossary,
        schemas=schemas,
        entries=entries,
    )

    # De-duplicate while preserving order
    def _dedupe(xs: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for x in xs:
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
        return out

    definitions: list[str] = []
    lemmas: list[str] = []
    axioms: list[str] = []

    # Real definitions extracted from the paper take priority — inject them first
    # so they shadow any LLM-invented axiom stubs for the same names.
    _real_def_names: set[str] = set()
    for raw_sig in (real_definitions or []):
        sig = raw_sig.strip()
        if not sig:
            continue
        # Rewrite theorem/lemma → noncomputable def with sorry body for type-only stubs.
        if re.match(r"^(theorem|lemma)\s+", sig):
            sig = re.sub(r"^(theorem|lemma)\s+", "noncomputable def ", sig, count=1)
            if ":= by" not in sig and ":= sorry" not in sig:
                sig = re.sub(r"\s*:=\s*by\s*$", " := sorry", sig)
                if ":= sorry" not in sig:
                    sig = sig.rstrip() + " := sorry"
        elif not re.match(r"^(noncomputable\s+)?(def|abbrev|opaque)\s+", sig):
            sig = "noncomputable def " + sig
        name = declaration_name(sig)
        if name:
            _real_def_names.add(name)
        definitions.append(sig)
    if _real_def_names:
        notes.append(f"{len(_real_def_names)} real definition(s) from paper extraction (grounded)")

    for sym in symbols:
        decl = sym.declaration.strip()
        if not decl:
            continue
        # Skip if a real extracted definition already covers this name.
        sym_name = declaration_name(decl)
        if sym_name and sym_name in _real_def_names:
            continue
        if decl.startswith(("def ", "abbrev ", "opaque ", "noncomputable ")):
            definitions.append(decl)
        elif decl.startswith(("lemma ", "theorem ")):
            lemmas.append(decl)
        else:
            axioms.append(decl)
    if symbols:
        notes.append(f"declared {len(symbols)} paper-local symbol(s) from inventory")
        notes.append("definition stubs are notation grounding only and are not proof-countable")

    # Per-area starter definitions and lemmas. Pre-emitted at the TOP of the
    # paper-theory file so the translator can use the area-typical names
    # (e.g., `analysisSeminorm`, `probAlmostSure`) without introducing fresh
    # axiom-form symbols. Only fires when the domain pack has populated these;
    # the default pack has empty starter lists.
    pack_starter_defs = list(getattr(pack, "starter_definitions", None) or [])
    pack_starter_lemmas = list(getattr(pack, "starter_lemmas", None) or [])
    if pack_starter_defs:
        # Prepend so they shadow any later axiom-form declarations of the
        # same name. The de-dupe below preserves the first occurrence.
        definitions = pack_starter_defs + definitions
        notes.append(
            f"emitted {len(pack_starter_defs)} area-typical starter def(s) "
            f"from domain pack '{pack.name}'"
        )
    if pack_starter_lemmas:
        lemmas = pack_starter_lemmas + lemmas
        notes.append(
            f"emitted {len(pack_starter_lemmas)} area-typical starter lemma(s) "
            f"from domain pack '{pack.name}'"
        )

    manifest = symbols_to_manifest(paper_id=paper_id, module_name=module, symbols=symbols)

    return PaperTheoryPlan(
        paper_id=paper_id,
        domain=pack.name,
        module_name=module,
        imports=_dedupe(pack.imports),
        open_scopes=_dedupe(pack.open_scopes),
        definitions=_dedupe([a for a in definitions if a.strip()]),
        lemmas=_dedupe([a for a in lemmas if a.strip()]),
        axioms=_dedupe([a for a in axioms if a.strip()]),
        symbols=symbols,
        manifest=manifest,
        notes=_dedupe(notes),
    )


# Per-underlying-type allowlist of Mathlib typeclasses that we know elaborate
# via `inferInstance` for the named base type. Extending this list is safe;
# entries here just enable auto-emission of the paper-local instance lines.
# If a paper introduces an `abbrev Foo := T` where `T` is not a key in this
# map, no instances are auto-emitted and the stub remains the same as before
# this change.
#
# Common-to-all-numeric-bases classes (LE, LT, Preorder, PartialOrder,
# LinearOrder, DecidableEq, Zero, One, Inhabited, Add, Mul, MeasurableSpace)
# are shared via _COMMON_NUMERIC_CLASSES below.
_COMMON_NUMERIC_CLASSES: tuple[str, ...] = (
    "LE", "LT", "Preorder", "PartialOrder", "LinearOrder",
    "DecidableEq", "Zero", "One", "Inhabited", "Add", "Mul",
    "MeasurableSpace",
)
_AUTO_INSTANCE_BY_UNDERLYING: dict[str, tuple[str, ...]] = {
    "ℕ": _COMMON_NUMERIC_CLASSES,
    "Nat": _COMMON_NUMERIC_CLASSES,
    "ℤ": _COMMON_NUMERIC_CLASSES + ("Neg", "Sub", "Ring", "CommRing"),
    "Int": _COMMON_NUMERIC_CLASSES + ("Neg", "Sub", "Ring", "CommRing"),
    "ℝ": _COMMON_NUMERIC_CLASSES + (
        "Neg", "Sub", "Div", "Inv", "Field", "Norm",
        "NormedField", "NormedAddCommGroup", "TopologicalSpace",
    ),
    "Real": _COMMON_NUMERIC_CLASSES + (
        "Neg", "Sub", "Div", "Inv", "Field", "Norm",
        "NormedField", "NormedAddCommGroup", "TopologicalSpace",
    ),
    "ℚ": _COMMON_NUMERIC_CLASSES + ("Neg", "Sub", "Div", "Inv", "Field"),
    "Rat": _COMMON_NUMERIC_CLASSES + ("Neg", "Sub", "Div", "Inv", "Field"),
}
# Legacy aliases preserved so callers/tests that import these still work.
_INSTANCE_BEARING_UNDERLYING_TYPES = tuple(_AUTO_INSTANCE_BY_UNDERLYING.keys())
_AUTO_INSTANCE_CLASSES = (
    "LE", "LT", "Preorder", "PartialOrder", "DecidableEq",
)
_ABBREV_TYPE_RE = re.compile(
    r"^\s*(?:noncomputable\s+)?abbrev\s+([A-Za-z_][A-Za-z0-9_']*)\s*:\s*Type\s*:=\s*(.+?)\s*$",
    re.MULTILINE,
)


def _underlying_carries_instances(underlying: str) -> bool:
    underlying = underlying.strip().rstrip(".").strip()
    return any(
        underlying == t or underlying.startswith(t + " ")
        for t in _AUTO_INSTANCE_BY_UNDERLYING
    )


def _classes_for_underlying(underlying: str) -> tuple[str, ...]:
    underlying = underlying.strip().rstrip(".").strip()
    for base, classes in _AUTO_INSTANCE_BY_UNDERLYING.items():
        if underlying == base or underlying.startswith(base + " "):
            return classes
    return ()


def _auto_instance_lines(definitions: list[str]) -> list[str]:
    """Emit `instance : <Class> <Name> := inferInstance` for every type abbrev
    whose underlying type is a known instance-bearing Mathlib type. Generalises
    the manually-curated 5-instance block in Paper_2304_09598.lean to all papers.

    Class coverage is per-underlying-type (see `_AUTO_INSTANCE_BY_UNDERLYING`)
    so we only emit instances Mathlib actually provides — e.g., `Field` for ℝ
    but not for ℕ."""
    lines: list[str] = []
    for decl in definitions:
        for match in _ABBREV_TYPE_RE.finditer(decl):
            name, underlying = match.group(1), match.group(2)
            classes = _classes_for_underlying(underlying)
            if not classes:
                continue
            for cls in classes:
                lines.append(f"instance : {cls} {name} := inferInstance")
    return lines


def _aesop_attribute_lines(axioms: list[str]) -> list[str]:
    """Tag every paper-local axiom with `attribute [aesop safe apply]` so that
    when proof search runs `aesop`, the axiom is in the apply set. Without this,
    bare `axiom X : ...` declarations are invisible to tactic-based search."""
    lines: list[str] = []
    for decl in axioms:
        name = declaration_name(decl)
        if not name:
            continue
        if not re.match(r"^\s*axiom\s+", decl):
            continue
        lines.append(f"attribute [aesop safe apply] {name}")
    return lines


def write_paper_theory(*, project_root: Path, plan: PaperTheoryPlan) -> Path:
    out_dir = project_root / "Desol" / "PaperTheory"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{plan.module_name}.lean"

    imports_block = "\n".join(f"import {imp}" for imp in plan.imports if imp.strip()) or "import Mathlib"
    open_block = ""
    if plan.open_scopes:
        open_block = "open " + " ".join(plan.open_scopes) + "\n\n"

    notes_block = ""
    if plan.notes:
        notes_block = "\n".join(f"-- note: {n}" for n in plan.notes) + "\n\n"

    auto_instance_lines = _auto_instance_lines(plan.definitions)
    auto_instance_block = ("\n".join(auto_instance_lines) + "\n\n") if auto_instance_lines else ""
    definitions_block = "\n\n".join(plan.definitions) + ("\n\n" if plan.definitions else "")
    lemmas_block = "\n\n".join(plan.lemmas) + ("\n\n" if plan.lemmas else "")
    axioms_block = "\n\n".join(plan.axioms) + ("\n" if plan.axioms else "")
    aesop_attribute_lines = _aesop_attribute_lines(plan.axioms)
    aesop_attribute_block = ("\n".join(aesop_attribute_lines) + "\n") if aesop_attribute_lines else ""
    exported_names: list[str] = []
    defined_names: set[str] = set()
    for decl in [*plan.definitions, *plan.lemmas, *plan.axioms]:
        name = declaration_name(decl)
        if name:
            exported_names.append(name)
            defined_names.add(name)
    # Defensive filter: drop any export entry that is not actually a `def`/
    # `abbrev`/`axiom`/`theorem`/`lemma` in the rendered file. Without this,
    # a name that diverges from the declaration form (e.g., `ξ` exported
    # while only `ξ'` is defined) crashes `lake build` with an "Unknown
    # constant" error and silently knocks the whole paper out of the
    # PaperImportsAnchor fallback. Pure safety: never adds, only removes.
    exported_names = [n for n in dict.fromkeys(exported_names) if n in defined_names]
    export_block = ""
    if exported_names:
        export_block = "\nexport " + plan.module_name + " (" + " ".join(exported_names) + ")\n"

    out.write_text(
        f"-- Auto-generated paper theory module\n"
        f"-- paper_id: {plan.paper_id}\n"
        f"-- domain: {plan.domain}\n\n"
        f"{imports_block}\n\n"
        f"{open_block}"
        f"namespace {plan.module_name}\n\n"
        f"{notes_block}"
        f"-- ------------------------------------------------------------------\n"
        f"-- Paper-local definitions and explicit axiom debt\n"
        f"-- Any result depending on this module must be reported as proved\n"
        f"-- modulo any paper-local axioms below, not as unconditional closure.\n"
        f"-- ------------------------------------------------------------------\n\n"
        f"-- Definition stubs ground paper-local identifiers before proof search.\n"
        f"-- They are transparent Lean definitions for elaboration, not hidden proofs of paper claims.\n"
        f"{definitions_block}"
        f"{('-- Standard typeclass instances inherited from the underlying Mathlib type.\n' + auto_instance_block) if auto_instance_block else ''}"
        f"-- Local lemmas / theorem-like facts.\n"
        f"{lemmas_block}"
        f"-- Explicit axioms / unresolved paper assumptions.\n"
        f"{axioms_block}\n"
        f"{('-- Aesop tactic registration for paper-local axioms.\n' + aesop_attribute_block + '\n') if aesop_attribute_block else ''}"
        f"end {plan.module_name}\n"
        f"{export_block}",
        encoding="utf-8",
    )
    manifest_path = out.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(plan.manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


_AREA_TO_DOMAIN = {
    "analysis": "analysis",
    "probability": "probability",
    "algebra": "algebra",
    "combinatorics": "combinatorics",
    "numbertheory": "number_theory",
    "generic": "",  # falls back to default pack (full Mathlib)
}


def _auto_classify_domain(paper_id: str, project_root: Path) -> str:
    """When the caller doesn't pass an explicit domain, use the area
    classifier to pick a domain pack. The classifier returns a math area
    (e.g. `analysis`) which we map to the domain-pack registry's name.

    Falls back to "" (default pack) on any classification failure."""
    if not paper_id:
        return ""
    try:
        import sys
        scripts_dir = Path(__file__).resolve().parent
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from paper_area_classifier import classify_paper
        result = classify_paper(paper_id, project_root)
        area = str(result.get("area", "generic") or "generic")
        return _AREA_TO_DOMAIN.get(area, "")
    except Exception:
        return ""


def build_paper_theory_module(
    *,
    project_root: Path,
    paper_id: str,
    domain: str = "",
    seed_text: str = "",
    inventory: list[PaperSymbolDecl] | None = None,
    glossary: dict[str, str] | None = None,
    schemas: list[dict] | None = None,
    entries: list[object] | None = None,
) -> tuple[PaperTheoryPlan, Path]:
    # If no domain passed, auto-classify from extracted_theorems.json content.
    # This routes paper-local symbol generation through the area-specific
    # domain pack (open scopes, micro-tactics) so paper-theory files match
    # the paper's mathematical content.
    if not (domain or "").strip():
        domain = _auto_classify_domain(paper_id, project_root)
    plan = plan_paper_theory(
        paper_id=paper_id,
        domain=domain,
        seed_text=seed_text,
        inventory=inventory,
        glossary=glossary,
        schemas=schemas,
        entries=entries,
    )
    return plan, write_paper_theory(project_root=project_root, plan=plan)


def paper_theory_import_header(imports: str, *, module_name: str) -> str:
    """Insert a PaperTheory import/open into a Lean import header deterministically."""
    effective = imports.strip()
    module = f"Desol.PaperTheory.{module_name}"
    if not effective:
        effective = "import Mathlib\nimport Aesop"
    lines = effective.splitlines()
    if f"import {module}" not in lines:
        insert_at = 0
        for i, ln in enumerate(lines):
            if ln.strip().startswith("import "):
                insert_at = i + 1
        lines.insert(insert_at, f"import {module}")
        lines.insert(insert_at + 1, f"open {module_name}")
    return "\n".join(lines).rstrip() + "\n"


def build_paper_theory(*, project_root: Path, module_name: str, timeout_s: int = 60) -> dict[str, object]:
    module = f"Desol.PaperTheory.{module_name}"
    try:
        proc = subprocess.run(
            ["lake", "build", module],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        return {
            "ok": proc.returncode == 0,
            "module": module,
            "returncode": proc.returncode,
            "output_tail": output[-1200:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "module": module, "returncode": -1, "output_tail": f"timeout_after_{timeout_s}s"}
    except Exception as exc:
        return {"ok": False, "module": module, "returncode": -1, "output_tail": str(exc)}


def main() -> int:
    p = argparse.ArgumentParser(description="Build a paper-local Lean theory module")
    p.add_argument("paper_id")
    p.add_argument("--project-root", default=".")
    p.add_argument("--domain", default="")
    p.add_argument("--seed-lean", default="", help="Optional Lean file to scan for symbols")
    p.add_argument("--seed-json", default="", help="Optional symbol inventory JSON/manifest")
    args = p.parse_args()

    project_root = Path(args.project_root).resolve()
    seed_text = ""
    if args.seed_lean:
        try:
            seed_text = Path(args.seed_lean).read_text(encoding="utf-8")
        except Exception:
            seed_text = ""
    inventory = load_inventory_json(args.seed_json) if args.seed_json else None
    plan = plan_paper_theory(
        paper_id=str(args.paper_id),
        domain=str(args.domain),
        seed_text=seed_text,
        inventory=inventory,
    )
    out = write_paper_theory(project_root=project_root, plan=plan)
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

