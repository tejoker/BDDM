#!/usr/bin/env python3
"""Rescue TRANSLATION_LIMITED rows whose `lean_statement` pre-dates a
translator improvement.

Some TL rows were emitted by a translator that wrote vacuous claims like
`(0 : ℕ) = 0` when LaTeX clauses extracted no content. Commits e3b0f63
(schema-fallback refusal) and 622e6d3 (backfill source_latex) closed
that path — but rows produced BEFORE those commits stayed TL because the
gate-flip logic in `formalize_paper_full._translation_limited_reason`
still flags their stored statement as `trivial_nat0eq0_target`.

This rescue tool re-runs the current deterministic translator
(`translator._translate.build_typed_statement_translation`) on each TL
row's `source_latex`. For each candidate:

  1. If the typed-IR refuses (returns None) OR the new statement is
     still trivialized OR the `_translation_limited_reason` is still
     non-empty → the row STAYS TL (standards-positive: a vacuous
     re-translation is not a rescue).
  2. Else we run the isolated-elaboration probe (the same
     `_run_isolated_file_check` used by the proof-search gate). A
     candidate that elaborates is demoted TL → UNRESOLVED with a
     `translation_rescue` audit field; the new `lean_statement` is
     written back. Proof search is a SEPARATE step — the rescue does
     NOT claim the row is proved.
  3. Else (non-trivial but does not elaborate) the row stays TL with an
     `attempted_rescue` audit note recording the failed elaboration.

By design the tool only consults the deterministic translator path
(no Mistral). The downstream `repair_bad_translations.py` flow already
covers the LLM-driven repair for rows that need it; this is the
zero-cost first sweep.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from translator._translate import (  # noqa: E402
    _extract_literal_schema,
    _is_trivialized_signature,
    build_typed_statement_translation,
)
from formalize_paper_full import _translation_limited_reason  # noqa: E402


@dataclass
class RescueOutcome:
    paper_id: str
    theorem_name: str
    action: str  # "demoted_to_unresolved" | "stays_tl_trivial" | "stays_tl_no_elaborate" | "stays_tl_translator_refused" | "skipped"
    lean_statement_old: str
    lean_statement_new: str
    elaborates: bool | None
    elab_detail: str
    tl_reason: str
    trivial: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "theorem_name": self.theorem_name,
            "action": self.action,
            "lean_statement_old": self.lean_statement_old,
            "lean_statement_new": self.lean_statement_new,
            "elaborates": self.elaborates,
            "elab_detail": self.elab_detail,
            "tl_reason": self.tl_reason,
            "trivial": self.trivial,
        }


def _retranslate(latex: str, *, paper_id: str, theorem_name: str) -> tuple[str | None, dict[str, Any]]:
    """Run the deterministic typed-IR translator. Returns (decl_or_None, schema)."""
    schema = _extract_literal_schema(latex)
    structured = build_typed_statement_translation(
        latex_statement=latex,
        schema=schema,
        theorem_name=theorem_name,
        paper_id=paper_id,
    )
    if structured is None:
        return None, schema
    return str(structured.get("lean_declaration", "") or ""), schema


def _elaborates(
    *, project_root: Path, paper_id: str, decl: str, timeout_s: int
) -> tuple[bool, str]:
    """Run the isolated-file elaboration probe. Lazy import so this module
    is hermetic in unit tests that don't have lake in PATH."""
    from prove_arxiv_batch import _run_isolated_file_check  # noqa: E402

    source = project_root / "output" / f"{paper_id}.lean"
    return _run_isolated_file_check(
        project_root=project_root,
        source_file=source,
        theorem_decl=decl,
        timeout_s=timeout_s,
    )


def _apply_demotion_to_row(row: dict[str, Any], *, new_decl: str) -> None:
    """Mutate `row` in place: TL → UNRESOLVED with rescue audit fields.

    Preserves the stored proof_text and any review evidence; only the
    status/lean_statement/failure_kind axis is touched.
    """
    audit = {
        "schema_version": "rescue_translation_limited.v1",
        "previous_status": "TRANSLATION_LIMITED",
        "previous_lean_statement": str(row.get("lean_statement", "") or ""),
        "previous_failure_kind": str(row.get("failure_kind", "") or ""),
        "previous_error_message": str(row.get("error_message", "") or ""),
        "rescue_method": "deterministic_typed_ir_retranslation",
    }
    row["translation_rescue"] = audit
    row["lean_statement"] = new_decl
    row["status"] = "UNRESOLVED"
    row["proved"] = False
    row["proof_method"] = "rescued_translation"
    row["failure_kind"] = "proof_search_unattempted"
    row["failure_origin"] = "PROOF_SEARCH_ERROR"
    row["error_message"] = ""
    # Clear claim_scope and result_label so the row reads as an honest UR
    # candidate, not a "translation-limited excluded" residue.
    row.pop("claim_scope", None)
    row["result_label"] = "translation_rescued_pending_proof_search"
    vg = row.get("validation_gates") if isinstance(row.get("validation_gates"), dict) else {}
    vg["translation_fidelity_ok"] = True
    # The translated statement still needs proof search; lean_proof_closed stays False.
    vg["lean_proof_closed"] = False
    row["validation_gates"] = vg
    failures = [str(x) for x in (row.get("gate_failures") or [])]
    failures = [f for f in failures if f != "translation_limited_statement"]
    row["gate_failures"] = failures
    notes = [str(x) for x in (row.get("claim_equivalence_notes") or [])]
    notes.append("translation_rescue:deterministic_retranslation")
    row["claim_equivalence_notes"] = list(dict.fromkeys(notes))


def _attach_attempted_audit(row: dict[str, Any], *, outcome: RescueOutcome) -> None:
    """Record a failed-rescue attempt on the row without changing its status."""
    audit = {
        "schema_version": "rescue_translation_limited.v1",
        "attempted_lean_statement": outcome.lean_statement_new,
        "elaborates": outcome.elaborates,
        "elab_detail": outcome.elab_detail,
        "tl_reason": outcome.tl_reason,
        "trivial": outcome.trivial,
        "result": outcome.action,
    }
    row["translation_rescue_attempt"] = audit


def rescue_row(
    row: dict[str, Any],
    *,
    project_root: Path,
    timeout_s: int = 60,
    skip_elaboration: bool = False,
) -> RescueOutcome:
    """Assess one TL row. Returns a RescueOutcome describing what happens.

    Does NOT mutate `row` — call `_apply_demotion_to_row` or
    `_attach_attempted_audit` based on the returned action.
    """
    paper_id = str(row.get("paper_id", "") or row.get("arxiv_id", "") or "")
    theorem_name = str(row.get("theorem_name", "") or "")
    old_decl = str(row.get("lean_statement", "") or "")
    latex = str(row.get("source_latex", "") or "")

    if not latex.strip():
        return RescueOutcome(
            paper_id=paper_id,
            theorem_name=theorem_name,
            action="skipped",
            lean_statement_old=old_decl,
            lean_statement_new="",
            elaborates=None,
            elab_detail="no_source_latex",
            tl_reason="",
            trivial=False,
        )

    new_decl, _schema = _retranslate(latex, paper_id=paper_id, theorem_name=theorem_name)
    if new_decl is None:
        return RescueOutcome(
            paper_id=paper_id,
            theorem_name=theorem_name,
            action="stays_tl_translator_refused",
            lean_statement_old=old_decl,
            lean_statement_new="",
            elaborates=None,
            elab_detail="translator_returned_none",
            tl_reason="",
            trivial=False,
        )

    triv_sig = _is_trivialized_signature(new_decl)
    tl_reason = _translation_limited_reason(new_decl)
    if triv_sig or tl_reason:
        return RescueOutcome(
            paper_id=paper_id,
            theorem_name=theorem_name,
            action="stays_tl_trivial",
            lean_statement_old=old_decl,
            lean_statement_new=new_decl,
            elaborates=None,
            elab_detail="",
            tl_reason=tl_reason,
            trivial=triv_sig,
        )

    if skip_elaboration:
        return RescueOutcome(
            paper_id=paper_id,
            theorem_name=theorem_name,
            action="skipped",
            lean_statement_old=old_decl,
            lean_statement_new=new_decl,
            elaborates=None,
            elab_detail="elaboration_skipped",
            tl_reason="",
            trivial=False,
        )

    ok, detail = _elaborates(
        project_root=project_root,
        paper_id=paper_id,
        decl=new_decl,
        timeout_s=timeout_s,
    )
    if ok:
        return RescueOutcome(
            paper_id=paper_id,
            theorem_name=theorem_name,
            action="demoted_to_unresolved",
            lean_statement_old=old_decl,
            lean_statement_new=new_decl,
            elaborates=True,
            elab_detail="",
            tl_reason="",
            trivial=False,
        )
    return RescueOutcome(
        paper_id=paper_id,
        theorem_name=theorem_name,
        action="stays_tl_no_elaborate",
        lean_statement_old=old_decl,
        lean_statement_new=new_decl,
        elaborates=False,
        elab_detail=detail[-300:],
        tl_reason="",
        trivial=False,
    )


def rescue_ledger_file(
    ledger_path: Path,
    *,
    project_root: Path,
    write: bool = False,
    targets: set[tuple[str, str]] | None = None,
    timeout_s: int = 60,
    skip_elaboration: bool = False,
) -> list[RescueOutcome]:
    """Walk a ledger file, rescue every TL row (or only `targets` when
    provided). Writes back when `write=True` AND at least one demotion or
    attempted-audit applied."""
    if not ledger_path.exists():
        return []
    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    entries = data if isinstance(data, list) else data.get("entries", [])
    outcomes: list[RescueOutcome] = []
    changed = False
    # When the ledger row does not record `paper_id` (older schema), fall
    # back to the ledger file's stem so target-filtering works against
    # canonical (`reproducibility/.../verification_ledger.json`) ledgers
    # too — those use the parent directory's paper id, not a per-row field.
    fallback_pid = ledger_path.parent.name if ledger_path.name == "verification_ledger.json" else ledger_path.stem
    for row in entries:
        if str(row.get("status", "")) != "TRANSLATION_LIMITED":
            continue
        pid = str(row.get("paper_id", "") or row.get("arxiv_id", "") or fallback_pid)
        name = str(row.get("theorem_name", "") or "")
        if targets is not None and (pid, name) not in targets:
            continue
        if not row.get("paper_id"):
            row["paper_id"] = pid
        outcome = rescue_row(
            row,
            project_root=project_root,
            timeout_s=timeout_s,
            skip_elaboration=skip_elaboration,
        )
        outcomes.append(outcome)
        if outcome.action == "demoted_to_unresolved":
            _apply_demotion_to_row(row, new_decl=outcome.lean_statement_new)
            changed = True
        elif outcome.action in {"stays_tl_no_elaborate", "stays_tl_trivial", "stays_tl_translator_refused"}:
            _attach_attempted_audit(row, outcome=outcome)
            changed = True
    if write and changed:
        payload = data if isinstance(data, list) else {**data, "entries": entries}
        ledger_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return outcomes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument(
        "--ledger-dir",
        type=Path,
        default=Path("output/verification_ledgers"),
    )
    parser.add_argument(
        "--papers",
        nargs="*",
        default=None,
        help="Paper IDs to rescue (default: every ledger in --ledger-dir).",
    )
    parser.add_argument(
        "--theorem-names",
        nargs="*",
        default=None,
        help=(
            "If provided alongside --papers, restrict to specific "
            "(paper_id/theorem_name) tuples passed as `<pid>/<name>` strings."
        ),
    )
    parser.add_argument("--write", action="store_true", help="Apply demotions; default is dry-run")
    parser.add_argument(
        "--timeout-s", type=int, default=60, help="Per-row elaboration timeout"
    )
    parser.add_argument(
        "--skip-elaboration",
        action="store_true",
        help="Run only the deterministic re-translation (no `lake env lean` probe). Useful for hermetic dry runs.",
    )
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    targets: set[tuple[str, str]] | None = None
    if args.theorem_names:
        # `--theorem-names` accepts `<pid>/<name>` tuples.
        targets = set()
        for raw in args.theorem_names:
            if "/" in raw:
                pid, _, name = raw.partition("/")
                targets.add((pid, name))
    paper_ids = args.papers or [
        p.stem for p in sorted(args.ledger_dir.glob("*.json"))
    ]
    all_outcomes: list[RescueOutcome] = []
    for pid in paper_ids:
        ledger_path = args.ledger_dir / f"{pid}.json"
        outcomes = rescue_ledger_file(
            ledger_path,
            project_root=project_root,
            write=args.write,
            targets=targets,
            timeout_s=args.timeout_s,
            skip_elaboration=args.skip_elaboration,
        )
        all_outcomes.extend(outcomes)

    counts: dict[str, int] = {}
    for o in all_outcomes:
        counts[o.action] = counts.get(o.action, 0) + 1
    summary = {
        "schema_version": "rescue_translation_limited.v1",
        "dry_run": not args.write,
        "counts": counts,
        "outcomes": [o.to_dict() for o in all_outcomes],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
