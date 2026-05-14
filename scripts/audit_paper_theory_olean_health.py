#!/usr/bin/env python3
"""Audit Desol/PaperTheory/*.lean (and Repair/*.lean) for olean health.

Each `Desol/PaperTheory/Paper_<id>.lean` module compiles to an `.olean`
under `.lake/build/lib/lean/`. Every per-paper `.lean` file in
`output/<id>.lean` `import`s its paper-theory module — when that module's
olean is missing or stale, EVERY downstream row from that paper fails to
elaborate, which silently demotes proven rows to UNRESOLVED/TRANSLATION_-
LIMITED.

The existing self-heal in `regenerate_paper_imports_anchor._try_build_-
missing_olean` (commit 0c05413) fires LATE in the pipeline — only when
the REPL bootstrap anchor is regenerated. This audit is a standalone,
fail-fast health check that walks both `Desol/PaperTheory/*.lean` and
`Desol/PaperTheory/Repair/*.lean`, runs `lake build <module>` on each
with a 240s timeout, and reports the per-module status:

  - `ok`            : `lake build` returned 0.
  - `fail`          : `lake build` returned non-zero.
  - `timed_out`     : the build exceeded the timeout (cold-cache initial
                      Mathlib compile can be slow, hence the long default).
  - `not_attempted` : reserved for invocation modes that skip a module
                      (e.g. `--papers <id>` filters).

`--write` appends the failures to a JSON summary at
`output/audits/paper_theory_olean_health.json`. `--regenerate` kicks off
`paper_theory_builder.build_paper_theory` on the failing paper as the
cleanest recovery path (regenerating the module from inventory rebuilds
the .lean AND immediately retries the build with the same 240s timeout).

This module is also imported by
`formalize_paper_full._publish_reproducibility_bundle` as a pre-publish
gate (mirroring the FP integrity audit landed in commit 8eba181). The
publish manifest records the audit result under `paper_theory_olean_-
health` next to `fully_proven_integrity_audit`, so a reviewer can see at
a glance which paper-theory modules were healthy at publish time.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable


DEFAULT_TIMEOUT_S = 240
PAPER_THEORY_DIR = Path("Desol/PaperTheory")
REPAIR_SUBDIR = "Repair"
DEFAULT_SUMMARY_OUT = Path("output/audits/paper_theory_olean_health.json")


@dataclass
class ModuleHealth:
    module: str
    source_path: str
    olean_path: str
    olean_present: bool
    status: str  # "ok" | "fail" | "timed_out" | "not_attempted"
    returncode: int = 0
    duration_s: float = 0.0
    output_tail: str = ""
    paper_id: str = ""


@dataclass
class AuditSummary:
    schema_version: str = "paper_theory_olean_health.v1"
    timeout_s: int = DEFAULT_TIMEOUT_S
    paper_theory_dir: str = str(PAPER_THEORY_DIR)
    modules: list[ModuleHealth] = field(default_factory=list)

    def totals(self) -> dict[str, int]:
        counts = {"ok": 0, "fail": 0, "timed_out": 0, "not_attempted": 0}
        for m in self.modules:
            counts[m.status] = counts.get(m.status, 0) + 1
        counts["total"] = len(self.modules)
        return counts

    def failing(self) -> list[ModuleHealth]:
        return [m for m in self.modules if m.status in {"fail", "timed_out"}]


def _module_for_path(path: Path, *, project_root: Path) -> str:
    """Given an absolute or project-relative `.lean` path, return its dotted
    Lean module name. `Desol/PaperTheory/Repair/Paper_X.lean` becomes
    `Desol.PaperTheory.Repair.Paper_X`."""
    rel = path.relative_to(project_root) if path.is_absolute() else path
    return ".".join(rel.with_suffix("").parts)


def _olean_for_module(project_root: Path, module: str) -> Path:
    return project_root / ".lake" / "build" / "lib" / "lean" / Path(*module.split(".")).with_suffix(".olean")


def _paper_id_from_module(module: str) -> str:
    """`Desol.PaperTheory.Paper_2604_21583` → `2604.21583`.

    Also handles the Repair subdirectory: `Desol.PaperTheory.Repair.Paper_X`.
    Returns an empty string when the module name doesn't match the
    `Paper_<digits>_<digits>` convention.
    """
    bare = module.rsplit(".", 1)[-1]
    if not bare.startswith("Paper_"):
        return ""
    stem = bare[len("Paper_") :]
    # The Lean module name uses `_` as the separator; the canonical paper id
    # uses `.`. Replace the LAST `_` with `.` (paper ids are `YYMM.NNNNN`).
    if "_" not in stem:
        return ""
    head, sep, tail = stem.rpartition("_")
    return f"{head}.{tail}" if head and tail else ""


def _enumerate_modules(project_root: Path) -> list[tuple[str, Path]]:
    """Walk `Desol/PaperTheory/` and `Desol/PaperTheory/Repair/`. Returns a
    deterministic (module, source_path) list, sorted: top-level first, then
    Repair (so a reviewer reading the report sees core modules before their
    repair siblings)."""
    base = project_root / PAPER_THEORY_DIR
    results: list[tuple[str, Path]] = []
    if not base.exists():
        return results
    # Top-level Paper_*.lean (NOT recursive).
    for src in sorted(base.glob("Paper_*.lean")):
        results.append((_module_for_path(src, project_root=project_root), src))
    # Repair/Paper_*.lean.
    repair = base / REPAIR_SUBDIR
    if repair.exists():
        for src in sorted(repair.glob("Paper_*.lean")):
            results.append((_module_for_path(src, project_root=project_root), src))
    return results


def build_one_module(
    *,
    project_root: Path,
    module: str,
    source_path: Path,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    runner: Callable[..., Any] = subprocess.run,
) -> ModuleHealth:
    """Run `lake build <module>` once. Returns a `ModuleHealth` row.

    `runner` is injected so unit tests can stub the subprocess call. Real
    callers pass `subprocess.run` (the default).
    """
    olean = _olean_for_module(project_root, module)
    olean_present_pre = olean.exists()
    paper_id = _paper_id_from_module(module)
    import time as _time
    started = _time.time()
    try:
        proc = runner(
            ["lake", "build", module],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return ModuleHealth(
            module=module,
            source_path=str(source_path),
            olean_path=str(olean),
            olean_present=olean.exists(),
            status="timed_out",
            returncode=-1,
            duration_s=round(_time.time() - started, 2),
            output_tail=f"timeout_after_{timeout_s}s",
            paper_id=paper_id,
        )
    except Exception as exc:  # pragma: no cover — runner-level errors
        return ModuleHealth(
            module=module,
            source_path=str(source_path),
            olean_path=str(olean),
            olean_present=olean_present_pre,
            status="fail",
            returncode=-1,
            duration_s=round(_time.time() - started, 2),
            output_tail=f"{type(exc).__name__}:{exc}"[:1200],
            paper_id=paper_id,
        )
    output = ((getattr(proc, "stdout", "") or "") + (getattr(proc, "stderr", "") or "")).strip()
    rc = int(getattr(proc, "returncode", 1) or 0)
    status = "ok" if rc == 0 else "fail"
    return ModuleHealth(
        module=module,
        source_path=str(source_path),
        olean_path=str(olean),
        olean_present=olean.exists(),
        status=status,
        returncode=rc,
        duration_s=round(_time.time() - started, 2),
        output_tail=output[-1200:],
        paper_id=paper_id,
    )


def audit_paper_theory(
    *,
    project_root: Path,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    runner: Callable[..., Any] = subprocess.run,
    papers: list[str] | None = None,
) -> AuditSummary:
    """Walk and probe every module under `Desol/PaperTheory/`. When `papers`
    is provided, only modules whose paper_id is in that list are actually
    built; the rest receive `status='not_attempted'` for traceability."""
    summary = AuditSummary(timeout_s=timeout_s)
    paper_filter = set(papers) if papers else None
    for module, src in _enumerate_modules(project_root):
        pid = _paper_id_from_module(module)
        if paper_filter is not None and pid not in paper_filter:
            olean = _olean_for_module(project_root, module)
            summary.modules.append(
                ModuleHealth(
                    module=module,
                    source_path=str(src),
                    olean_path=str(olean),
                    olean_present=olean.exists(),
                    status="not_attempted",
                    paper_id=pid,
                )
            )
            continue
        summary.modules.append(
            build_one_module(
                project_root=project_root,
                module=module,
                source_path=src,
                timeout_s=timeout_s,
                runner=runner,
            )
        )
    return summary


def regenerate_failing_papers(
    summary: AuditSummary,
    *,
    project_root: Path,
) -> dict[str, dict[str, Any]]:
    """For each failing module, call `paper_theory_builder.build_paper_theory`
    on its paper_id (regenerates the module + re-runs `lake build`). Returns
    a `{paper_id: build_result}` map. Skips Repair/* modules — those are
    targeted hand-patched stubs that the regenerator would overwrite.
    """
    import sys as _sys
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in _sys.path:
        _sys.path.insert(0, str(scripts_dir))
    from paper_theory_builder import build_paper_theory  # noqa: E402

    out: dict[str, dict[str, Any]] = {}
    for m in summary.failing():
        # Skip Repair/* — those are hand-patched and must not be overwritten.
        if f".{REPAIR_SUBDIR}." in m.module:
            out[m.module] = {"skipped": "repair_module_not_regenerated"}
            continue
        pid = m.paper_id
        if not pid:
            out[m.module] = {"skipped": "no_paper_id_inferable"}
            continue
        module_name = m.module.rsplit(".", 1)[-1]
        result = build_paper_theory(
            project_root=project_root,
            module_name=module_name,
            timeout_s=DEFAULT_TIMEOUT_S,
        )
        out[pid] = dict(result)
    return out


def _summary_to_dict(summary: AuditSummary) -> dict[str, Any]:
    return {
        "schema_version": summary.schema_version,
        "timeout_s": summary.timeout_s,
        "paper_theory_dir": summary.paper_theory_dir,
        "totals": summary.totals(),
        "failing_modules": [m.module for m in summary.failing()],
        "modules": [asdict(m) for m in summary.modules],
    }


def write_summary(summary: AuditSummary, *, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(_summary_to_dict(summary), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument(
        "--timeout-s",
        type=int,
        default=DEFAULT_TIMEOUT_S,
        help="Per-module lake-build timeout (default: 240s; cold cache may be slow)",
    )
    parser.add_argument(
        "--papers",
        nargs="*",
        default=None,
        help="Only build modules for these paper IDs (others are reported as not_attempted)",
    )
    parser.add_argument(
        "--write",
        type=Path,
        nargs="?",
        const=DEFAULT_SUMMARY_OUT,
        default=None,
        help=(
            "Write a JSON summary to PATH (default "
            f"{DEFAULT_SUMMARY_OUT}). Without --write, prints the summary to stdout."
        ),
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Re-run paper_theory_builder on each failing paper (cleanest recovery)",
    )
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    summary = audit_paper_theory(
        project_root=project_root,
        timeout_s=int(args.timeout_s),
        papers=list(args.papers) if args.papers else None,
    )

    out: dict[str, Any] = _summary_to_dict(summary)
    if args.regenerate and summary.failing():
        out["regeneration_results"] = regenerate_failing_papers(
            summary, project_root=project_root,
        )

    if args.write:
        write_path = args.write if args.write.is_absolute() else (project_root / args.write)
        write_summary(summary, out_path=write_path)
        out["written_to"] = str(write_path)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if not summary.failing() else 1


if __name__ == "__main__":
    raise SystemExit(main())
