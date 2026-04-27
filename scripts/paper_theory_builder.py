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
    for sym in symbols:
        decl = sym.declaration.strip()
        if not decl:
            continue
        if decl.startswith(("def ", "abbrev ", "opaque ")):
            definitions.append(decl)
        elif decl.startswith(("lemma ", "theorem ")):
            lemmas.append(decl)
        else:
            axioms.append(decl)
    if symbols:
        notes.append(f"declared {len(symbols)} paper-local symbol(s) from inventory")
        notes.append("definition stubs are notation grounding only and are not proof-countable")
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

    definitions_block = "\n\n".join(plan.definitions) + ("\n\n" if plan.definitions else "")
    lemmas_block = "\n\n".join(plan.lemmas) + ("\n\n" if plan.lemmas else "")
    axioms_block = "\n\n".join(plan.axioms) + ("\n" if plan.axioms else "")
    exported_names: list[str] = []
    for decl in [*plan.definitions, *plan.lemmas, *plan.axioms]:
        name = declaration_name(decl)
        if name:
            exported_names.append(name)
    export_block = ""
    if exported_names:
        export_block = "\nexport " + plan.module_name + " (" + " ".join(dict.fromkeys(exported_names)) + ")\n"

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
        f"-- Local lemmas / theorem-like facts.\n"
        f"{lemmas_block}"
        f"-- Explicit axioms / unresolved paper assumptions.\n"
        f"{axioms_block}\n"
        f"end {plan.module_name}\n"
        f"{export_block}",
        encoding="utf-8",
    )
    manifest_path = out.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(plan.manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


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

