#!/usr/bin/env python3
"""Detect paper-local axiom blocks in lake errors and route UR rows to AB.

Many UNRESOLVED rows in canonical paper ledgers are blocked by paper-local
axiom OPACITY -- they invoke paper-theory identifiers (declared as `axiom
<name> : ...` or stubby `def <name> := 0/True/sorry`) that have no
exploitable definitional content. No tactic search can close such a row
honestly: the proof must reduce to the named axiom(s), and that reduction
cannot be discovered by `unfold` / `simp` / `omega` / etc. against an
opaque declaration.

This module:
  1. Parses a row's lake error tail and pulls out tokens that look like
     paper-local axiom invocations (`unfold <name>` failed,
     `<name> has no body`, `unknown definition <name>`).
  2. Cross-references each name against `Desol/PaperTheory/Paper_<id>.lean`.
     Real `axiom <name>` declarations -> opaque.
     Stub `def <name> := 0 / True / sorry` declarations -> also opaque
     (they ground the identifier for elaboration but carry no proof of
     the paper claim, by the paper-theory builder's own documentation).
     `def <name> := <non-trivial value>` -> NOT opaque; routing skipped.
  3. If at least one matched paper-local axiom is in the error tail,
     returns the AXIOM_BACKED route with the precise axiom_debt list.

Pure analysis. No LLM calls. No Mistral budget. The audit's
`no_paper_axiom_debt` validation gate is the final arbiter -- this module
flags rows that legitimately depend on those exact paper-local axioms,
and the existing `pipeline_status.evaluate_promotion_gates` logic uses
the axiom_debt list to keep them at AXIOM_BACKED (never promoted to FP).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

DEFAULT_LEDGER_DIR = PROJECT_ROOT / "output" / "verification_ledgers"
DEFAULT_LEAN_DIR = PROJECT_ROOT / "output"
DEFAULT_PAPER_THEORY_DIR = PROJECT_ROOT / "Desol" / "PaperTheory"

CANONICAL_PAPERS = (
    "2012.09271",
    "2304.09598",
    "2401.04567",
    "2604.21314",
    "2604.21583",
    "2604.21616",
    "2604.21821",
    "2604.21884",
)


# --- Paper-theory parsing --------------------------------------------------


_AXIOM_DECL_RE = re.compile(
    r"^\s*axiom\s+([A-Za-z_][A-Za-z0-9_']*)\b",
    re.MULTILINE,
)
_DEF_DECL_RE = re.compile(
    r"^\s*(?:noncomputable\s+|private\s+)?(?:def|abbrev)\s+"
    r"([A-Za-z_][A-Za-z0-9_']*)\b[^\n]*?:=\s*(.+?)$",
    re.MULTILINE,
)

# Stub bodies that the paper-theory builder emits when an identifier has
# no extractable definitional content. These are NOT proofs of paper
# claims; they only ground the symbol so the surrounding statement
# elaborates. Treat them as opaque for the purposes of routing.
_STUB_BODY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*0\s*$"),
    re.compile(r"^\s*0\.0\s*$"),
    re.compile(r"^\s*True\s*$"),
    re.compile(r"^\s*False\s*$"),
    re.compile(r"^\s*sorry\s*$"),
    re.compile(r"^\s*Set\.univ\s*$"),
    re.compile(r"^\s*\(\s*\)\s*$"),  # Unit literal
)


@dataclass(frozen=True)
class PaperTheorySymbol:
    """A symbol declared in `Desol/PaperTheory/Paper_<id>.lean`."""

    name: str
    kind: str  # 'axiom' | 'stub_def' | 'real_def'
    raw_body: str  # def body (empty for axioms)


def parse_paper_theory_symbols(text: str) -> dict[str, PaperTheorySymbol]:
    """Parse the paper-theory file contents and classify each declared
    symbol as axiom / stub_def / real_def.

    Returns a mapping name -> PaperTheorySymbol. Multiple declarations
    with the same name are unlikely (would not elaborate), but the last
    one wins if present.
    """
    out: dict[str, PaperTheorySymbol] = {}
    for m in _AXIOM_DECL_RE.finditer(text):
        name = m.group(1)
        out[name] = PaperTheorySymbol(name=name, kind="axiom", raw_body="")
    for m in _DEF_DECL_RE.finditer(text):
        name = m.group(1)
        body = (m.group(2) or "").strip()
        kind = "real_def"
        for pat in _STUB_BODY_PATTERNS:
            if pat.match(body):
                kind = "stub_def"
                break
        out[name] = PaperTheorySymbol(name=name, kind=kind, raw_body=body)
    return out


def is_opaque_paper_axiom(sym: PaperTheorySymbol) -> bool:
    """A symbol is opaque (axiom-like) when it is either declared as
    `axiom <name>` or declared as a `def` whose body is one of the
    canonical paper-theory stubs (`0`, `True`, `False`, `sorry`,
    `Set.univ`, ...)."""
    return sym.kind in {"axiom", "stub_def"}


# --- Error-tail token extraction -------------------------------------------


# Patterns that indicate a paper-local axiom invocation was the actual block.
# Each pattern's group(1) is the candidate identifier.
#
# Identifier characters: letters, digits, underscore. Apostrophes are NOT
# included in the identifier class because Lean error messages frequently
# wrap names in single quotes (`'X'`), and a permissive class would absorb
# the closing quote into the captured name. Lean does allow `'` inside
# identifiers, but paper-theory names never use them; sacrificing that
# capability cleanly handles the quoted-name case.
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"

_ERROR_TAIL_PATTERNS: tuple[re.Pattern[str], ...] = (
    # `unfold X` failed: tactic 'unfold' failed because constant 'X' has no
    # unfolding (typical message variants).
    re.compile(
        r"unfold[^\n]*?(?:constant|identifier)?\s*['`]?(" + _IDENT + r")['`]?[^\n]*?(?:failed|has no)",
        re.IGNORECASE,
    ),
    # `unfold X` (simple form): `failed to unfold X` / `cannot unfold X`.
    re.compile(
        r"(?:failed to unfold|cannot unfold|could not unfold)\s+['`]?(" + _IDENT + r")['`]?",
        re.IGNORECASE,
    ),
    # `X has no body` / `X has no unfolding`.
    re.compile(
        r"['`]?(" + _IDENT + r")['`]?\s+(?:has no body|has no unfolding|cannot be unfolded)",
        re.IGNORECASE,
    ),
    # `unknown definition X` / `unknown identifier X` (when X is in paper-
    # theory's opaque set, this routes; when X is real-mathlib, no match).
    re.compile(
        r"unknown\s+(?:definition|identifier|constant)\s+['`]?(" + _IDENT + r")['`]?",
        re.IGNORECASE,
    ),
    # `irreducible` / `opaque` / `does not reduce`: occasionally Lean
    # surfaces `definition 'X' is irreducible` or `term 'X' is opaque`.
    re.compile(
        r"(?:definition|term|constant)\s+['`]?(" + _IDENT + r")['`]?\s+is\s+(?:irreducible|opaque)",
        re.IGNORECASE,
    ),
    # `tactic 'X' failed` style messages around opaque names.
    re.compile(
        r"tactic\s+'(?:unfold|rfl|delta)'[^\n]*?'(" + _IDENT + r")'",
        re.IGNORECASE,
    ),
)


def extract_candidate_names_from_error(lake_error: str) -> list[str]:
    """Pull paper-local-axiom-shaped identifier candidates from a lake
    error tail. Returns identifiers in first-seen order, deduplicated.

    Note: this is a broad-net extraction; the cross-reference step
    against the paper-theory file is what filters down to real
    paper-local axiom blocks.
    """
    if not lake_error:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for pat in _ERROR_TAIL_PATTERNS:
        for m in pat.finditer(lake_error):
            name = m.group(1)
            if not name or name in seen:
                continue
            seen.add(name)
            found.append(name)
    return found


# --- Detector core ----------------------------------------------------------


def detect_paper_axiom_block(
    *,
    paper_id: str,
    theorem_name: str,
    lean_statement: str,
    lake_error: str,
    paper_theory_file: Path,
) -> dict | None:
    """Identify whether `lake_error` is blocked by an opaque paper-local
    axiom for `theorem_name` in `paper_id`.

    Returns a routing dict when a paper-local axiom block is detected:

        {'route_to': 'AXIOM_BACKED',
         'axiom_debt': [<names of paper-local axioms / stubs invoked>],
         'reasoning': <human-readable explanation>}

    Returns None otherwise (the row should remain UR / IP / FLAWED per
    the calling status pipeline).

    Parameters
    ----------
    paper_id : str
        The arxiv paper id (e.g. '2304.09598'). Used only for the
        reasoning string and as a stable handle in audit_trail records.
    theorem_name : str
        The ledger row's theorem name. Used only for the reasoning string.
    lean_statement : str
        The row's `lean_statement`. Used as a secondary corpus: a name
        that appears in the statement AND in the error tail is a stronger
        match than a bare error-tail hit, but the error tail alone is
        sufficient. Empty string is fine.
    lake_error : str
        The actual `lake env lean` stderr/stdout tail (the same string
        returned by `_run_isolated_file_check` or
        `sweep_lemma_factor_v2._lake_validate_aware`). MUST come from a
        real lake invocation; do not synthesize.
    paper_theory_file : Path
        Path to `Desol/PaperTheory/Paper_<paper_id>.lean`. The file must
        exist; if missing, no routing (returns None).
    """
    if not lake_error or not lake_error.strip():
        return None
    if not paper_theory_file.exists():
        return None
    try:
        pt_text = paper_theory_file.read_text(encoding="utf-8")
    except Exception:
        return None
    symbols = parse_paper_theory_symbols(pt_text)
    if not symbols:
        return None

    candidates = extract_candidate_names_from_error(lake_error)
    if not candidates:
        return None

    matched: list[str] = []
    seen: set[str] = set()
    for cand in candidates:
        sym = symbols.get(cand)
        if sym is None:
            continue
        if not is_opaque_paper_axiom(sym):
            # Real definition with non-trivial body -- this is NOT an
            # axiom block; the proof should be discoverable through
            # ordinary tactic search.
            continue
        if cand in seen:
            continue
        seen.add(cand)
        matched.append(cand)

    if not matched:
        return None

    # Build reasoning: list the matched names with their declared kinds,
    # so reviewers can audit the route.
    kind_summary = ", ".join(
        f"{n} ({symbols[n].kind})" for n in matched
    )
    reasoning = (
        f"paper-local opaque axiom block detected in {paper_id}/{theorem_name}: "
        f"lake error references {kind_summary}; "
        f"these are paper-theory declarations with no exploitable "
        f"definitional content. Proof closes only modulo the named "
        f"axiom(s); route to AXIOM_BACKED rather than attempt full FP "
        f"closure."
    )
    return {
        "route_to": "AXIOM_BACKED",
        "axiom_debt": matched,
        "reasoning": reasoning,
    }


# --- Ledger application -----------------------------------------------------


def apply_route_to_entry(
    entry: dict[str, Any],
    *,
    route: dict[str, Any],
    paper_id: str,
    lake_error_preview: str = "",
) -> None:
    """Apply a routing decision to a ledger entry in place.

    Sets `status = AXIOM_BACKED`, merges the detected axiom names into
    `axiom_debt` using the canonical `paper_definition_stub:<name>` /
    `paper_axiom:<name>` prefix (matching `pipeline_status._detect_axiom_debt`),
    updates `validation_gates.no_paper_axiom_debt = False`, appends a
    structured `audit_trail` entry, and records the reasoning under
    `claim_equivalence_notes` for traceability.
    """
    new_status = route.get("route_to", "AXIOM_BACKED")
    detected_names: list[str] = list(route.get("axiom_debt", []))
    reasoning = str(route.get("reasoning", ""))

    # Build canonical debt labels. We use `paper_axiom:<name>` for
    # `axiom`-declared opacities and `paper_definition_stub:<name>` for
    # stub-`def`-declared opacities; the audit treats both as paper-local
    # debt.
    paper_theory_file = (
        DEFAULT_PAPER_THEORY_DIR
        / f"Paper_{paper_id.replace('.', '_')}.lean"
    )
    symbols: dict[str, PaperTheorySymbol] = {}
    if paper_theory_file.exists():
        try:
            symbols = parse_paper_theory_symbols(
                paper_theory_file.read_text(encoding="utf-8")
            )
        except Exception:
            symbols = {}

    canonical_debts: list[str] = []
    for n in detected_names:
        sym = symbols.get(n)
        if sym and sym.kind == "axiom":
            canonical_debts.append(f"paper_axiom:{n}")
        else:
            canonical_debts.append(f"paper_definition_stub:{n}")

    existing_debt = list(entry.get("axiom_debt") or [])
    merged_debt = list(dict.fromkeys(existing_debt + canonical_debts))
    entry["axiom_debt"] = merged_debt

    entry["status"] = new_status
    vg = entry.get("validation_gates") if isinstance(entry.get("validation_gates"), dict) else {}
    vg["no_paper_axiom_debt"] = False
    entry["validation_gates"] = vg
    failures = [str(x) for x in (entry.get("gate_failures") or [])]
    if "no_paper_axiom_debt" not in failures:
        failures.append("no_paper_axiom_debt")
    entry["gate_failures"] = failures

    trail = entry.get("audit_trail")
    if not isinstance(trail, list):
        trail = []
    trail.append({
        "schema_version": "route_to_axiom_backed.v1",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "route_to": new_status,
        "detected_axiom_names": detected_names,
        "canonical_axiom_debt": canonical_debts,
        "reasoning": reasoning,
        "lake_error_preview": (lake_error_preview or "")[-400:],
    })
    entry["audit_trail"] = trail

    notes = [str(x) for x in (entry.get("claim_equivalence_notes") or [])]
    notes.append("route_to_axiom_backed:paper_local_opacity")
    entry["claim_equivalence_notes"] = list(dict.fromkeys(notes))


# --- Smoke driver: scan canonical UR pool ----------------------------------


def _import_isolated_check() -> Any:
    """Lazy-import `_run_isolated_file_check` so unit tests that don't
    need lake can run hermetically."""
    try:
        from prove_arxiv_batch import _run_isolated_file_check  # noqa: E402
        return _run_isolated_file_check
    except Exception:
        return None


def _stored_error_for_row(entry: dict[str, Any]) -> str:
    """Return whichever stored error-tail-like field is populated on the
    entry. We check `lake_error_tail` first (set by some sweep drivers),
    then `error_message` (the canonical field that pipeline_status writes
    when a row fails elaboration). Either is fine for the detector since
    both originate from the same `lake env lean` output."""
    for key in ("lake_error_tail", "error_message"):
        val = entry.get(key)
        if val and isinstance(val, str) and val.strip():
            return val
    return ""


def _capture_lake_error_for_row(
    *,
    paper_id: str,
    entry: dict[str, Any],
    timeout_s: int,
) -> str:
    """Re-run the row's statement through `_run_isolated_file_check` and
    return the captured lake error tail. Returns empty string when the
    row elaborates clean (no error to route on) or when lake is
    unavailable in the environment."""
    isolated_check = _import_isolated_check()
    if isolated_check is None:
        return ""
    lean_statement = str(entry.get("lean_statement", "") or "")
    if not lean_statement.strip():
        return ""
    source_file = DEFAULT_LEAN_DIR / f"{paper_id}.lean"
    try:
        ok, err = isolated_check(
            project_root=PROJECT_ROOT,
            source_file=source_file,
            theorem_decl=lean_statement,
            timeout_s=timeout_s,
        )
    except Exception:
        return ""
    if ok:
        return ""
    return err or ""


def _iter_canonical_ur_rows(papers: Iterable[str]) -> Iterable[tuple[str, dict[str, Any], list[dict[str, Any]], Path]]:
    """Yield (paper_id, entry, all_entries, ledger_path) tuples for each
    canonical UR row."""
    for pid in papers:
        led = DEFAULT_LEDGER_DIR / f"{pid}.json"
        if not led.exists():
            continue
        data = json.loads(led.read_text(encoding="utf-8"))
        entries = data if isinstance(data, list) else data.get("entries", [])
        for entry in entries:
            if str(entry.get("status", "") or "") != "UNRESOLVED":
                continue
            yield pid, entry, entries, led


def smoke_scan(
    *,
    papers: Iterable[str] = CANONICAL_PAPERS,
    timeout_s: int = 45,
    max_rows_per_paper: int = 0,
    use_stored_error: bool = False,
) -> dict[str, Any]:
    """Scan canonical UR rows. For each row, capture its current lake
    error and run the detector. Returns a structured summary.

    Parameters
    ----------
    papers : iterable of str
        Paper ids to scan. Defaults to the canonical 8-paper subset.
    timeout_s : int
        `_run_isolated_file_check` per-row timeout.
    max_rows_per_paper : int
        0 = no cap; otherwise stop after this many rows per paper.
    use_stored_error : bool
        When True, prefer `entry.lake_error_tail` (if any) over re-running
        lake. Useful for fast offline smoke runs.
    """
    per_paper: dict[str, dict[str, Any]] = {}
    total_rows = 0
    total_matches = 0
    samples: list[dict[str, Any]] = []
    for pid in papers:
        led = DEFAULT_LEDGER_DIR / f"{pid}.json"
        if not led.exists():
            continue
        data = json.loads(led.read_text(encoding="utf-8"))
        entries = data if isinstance(data, list) else data.get("entries", [])
        ur_rows = [e for e in entries if str(e.get("status", "") or "") == "UNRESOLVED"]
        if max_rows_per_paper > 0:
            ur_rows = ur_rows[:max_rows_per_paper]
        paper_theory_file = (
            DEFAULT_PAPER_THEORY_DIR
            / f"Paper_{pid.replace('.', '_')}.lean"
        )
        per_paper[pid] = {
            "ur_rows_scanned": len(ur_rows),
            "ur_rows_matched": 0,
            "matches": [],
            "paper_theory_present": paper_theory_file.exists(),
        }
        for entry in ur_rows:
            total_rows += 1
            name = str(entry.get("theorem_name", "") or "")
            lean_stmt = str(entry.get("lean_statement", "") or "")
            if use_stored_error:
                lake_err = _stored_error_for_row(entry)
            else:
                lake_err = _stored_error_for_row(entry)
                if not lake_err:
                    lake_err = _capture_lake_error_for_row(
                        paper_id=pid, entry=entry, timeout_s=timeout_s,
                    )
            if not lake_err:
                continue
            route = detect_paper_axiom_block(
                paper_id=pid,
                theorem_name=name,
                lean_statement=lean_stmt,
                lake_error=lake_err,
                paper_theory_file=paper_theory_file,
            )
            if route is None:
                continue
            total_matches += 1
            per_paper[pid]["ur_rows_matched"] += 1
            match_record = {
                "theorem_name": name,
                "axiom_debt": route["axiom_debt"],
                "lake_error_tail_preview": lake_err[-200:],
            }
            per_paper[pid]["matches"].append(match_record)
            if len(samples) < 8:
                samples.append({"paper_id": pid, **match_record})
    return {
        "papers_scanned": list(per_paper.keys()),
        "total_ur_rows_scanned": total_rows,
        "total_matches": total_matches,
        "per_paper": per_paper,
        "samples": samples,
    }


def write_routing(
    *,
    papers: Iterable[str] = CANONICAL_PAPERS,
    timeout_s: int = 45,
    max_rows_per_paper: int = 0,
    use_stored_error: bool = False,
) -> dict[str, Any]:
    """Apply routing decisions to each canonical ledger, writing the
    updated rows back to `output/verification_ledgers/<id>.json`.

    Returns a summary mirroring `smoke_scan` plus per-paper write counts.
    Does NOT run the integrity audit; callers should invoke
    `audit_fully_proven_integrity.audit_paper` after to verify.
    """
    summary: dict[str, Any] = {
        "papers_written": [],
        "per_paper": {},
        "total_rows_rewritten": 0,
    }
    for pid in papers:
        led = DEFAULT_LEDGER_DIR / f"{pid}.json"
        if not led.exists():
            continue
        data = json.loads(led.read_text(encoding="utf-8"))
        entries = data if isinstance(data, list) else data.get("entries", [])
        is_list_form = isinstance(data, list)
        paper_theory_file = (
            DEFAULT_PAPER_THEORY_DIR
            / f"Paper_{pid.replace('.', '_')}.lean"
        )
        rewritten = 0
        rows_examined = 0
        for entry in entries:
            if str(entry.get("status", "") or "") != "UNRESOLVED":
                continue
            if max_rows_per_paper > 0 and rows_examined >= max_rows_per_paper:
                break
            rows_examined += 1
            name = str(entry.get("theorem_name", "") or "")
            lean_stmt = str(entry.get("lean_statement", "") or "")
            if use_stored_error:
                lake_err = _stored_error_for_row(entry)
            else:
                lake_err = _stored_error_for_row(entry)
                if not lake_err:
                    lake_err = _capture_lake_error_for_row(
                        paper_id=pid, entry=entry, timeout_s=timeout_s,
                    )
            if not lake_err:
                continue
            route = detect_paper_axiom_block(
                paper_id=pid,
                theorem_name=name,
                lean_statement=lean_stmt,
                lake_error=lake_err,
                paper_theory_file=paper_theory_file,
            )
            if route is None:
                continue
            apply_route_to_entry(
                entry,
                route=route,
                paper_id=pid,
                lake_error_preview=lake_err,
            )
            rewritten += 1
        summary["per_paper"][pid] = {
            "rows_examined": rows_examined,
            "rows_rewritten": rewritten,
        }
        summary["total_rows_rewritten"] += rewritten
        if rewritten > 0:
            payload = entries if is_list_form else {**data, "entries": entries}
            led.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            summary["papers_written"].append(pid)
    return summary


# --- CLI --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--paper",
        action="append",
        default=[],
        help="Scan only this paper id (repeatable). Default: 8-paper canonical.",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=45,
        help="Per-row lake timeout when re-capturing errors.",
    )
    p.add_argument(
        "--max-rows",
        type=int,
        default=0,
        help="Per-paper cap on UR rows scanned (0 = no cap).",
    )
    p.add_argument(
        "--use-stored-error",
        action="store_true",
        help=(
            "Prefer entry.lake_error_tail (if any) instead of re-running "
            "lake env lean. Useful for offline smoke."
        ),
    )
    p.add_argument(
        "--write",
        action="store_true",
        help="Mutate canonical ledgers to apply the detected routing.",
    )
    p.add_argument(
        "--summary",
        default="output/route_to_axiom_backed_summary.json",
    )
    args = p.parse_args(argv)
    papers = args.paper or list(CANONICAL_PAPERS)

    if args.write:
        result = write_routing(
            papers=papers,
            timeout_s=args.timeout,
            max_rows_per_paper=args.max_rows,
            use_stored_error=args.use_stored_error,
        )
    else:
        result = smoke_scan(
            papers=papers,
            timeout_s=args.timeout,
            max_rows_per_paper=args.max_rows,
            use_stored_error=args.use_stored_error,
        )

    out_path = PROJECT_ROOT / args.summary
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"[summary] wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
