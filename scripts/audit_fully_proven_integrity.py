#!/usr/bin/env python3
"""Audit FULLY_PROVEN ledger rows against the on-disk Lean file.

A row's promotion to FULLY_PROVEN is FRAUDULENT when:
  - the ledger claims `proved=True`, `step_verdict='VERIFIED'`,
    `validation_gates.lean_proof_closed=True`;
  - but the theorem's body in `output/<paper>.lean` is `sorry`.

The bypass lives in the gate-flipping path
`scripts/claim_equivalence_review.apply_adjudication_to_row` (called by
`apply_reviews_to_ledger.py`), which feeds `evaluate_promotion_gates` with
`run_independent_verify=False`. With that flag false, the gate
`lean_proof_closed = proved`, and `proved` is itself derived from
`_candidate_full_status` reading back `validation_gates.lean_proof_closed`
— a circular bypass: once a row is marked closed, no on-disk check
re-validates it.

This audit walks the canonical and ephemeral ledgers, parses each FP row's
theorem body out of `output/<paper>.lean`, and demotes any row whose body
is `sorry`. The proof_text is preserved in `audit_demotion.captured_proof`
for forensics; the row's `status`, `proved`, `step_verdict`,
`failure_kind`, and `validation_gates` are reset to honest values.

Rows excluded from demotion:
  - `__audited_core` replacements (curated proofs in
    `Desol/PaperProofs/...`; their proof text is in the ledger row, not the
    sorry-bearing `output/<id>.lean`);
  - term-mode rows whose `:= by` block is absent (e.g. `:= rfl`,
    `:= Iff.rfl`) — Lean has already accepted these at compile time.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_LEDGER_DIR = Path("output/verification_ledgers")
DEFAULT_LEAN_DIR = Path("output")
DEFAULT_REPRO_DIR = Path("reproducibility/full_paper_reports")


# Theorem name suffix / ledger_role / proof_mode markers for curated rows
# whose proof source is NOT `output/<pid>.lean`. These are validated
# elsewhere (PaperProofs/Auto/...); the audit deliberately leaves them
# alone so curated work isn't downgraded by mistake.
AUDITED_CORE_SUFFIX = "__audited_core"
AUDITED_CORE_ROLES = {"audited_core_replacement"}
AUDITED_CORE_MODES = {"audited-core-replacement"}


@dataclass
class Demotion:
    paper_id: str
    theorem_name: str
    captured_proof_text: str
    file_body_preview: str
    reason: str


@dataclass
class AuditResult:
    paper_id: str
    fp_pre: int = 0
    fp_post: int = 0
    demoted: int = 0
    audited_core_skipped: int = 0
    term_mode_skipped: int = 0
    not_found_skipped: int = 0
    validated_clean: int = 0
    demotions: list[Demotion] = field(default_factory=list)


def _is_audited_core_row(entry: dict[str, Any]) -> bool:
    """A row is curated (audited-core) when:
      - its name ends in the `__audited_core` suffix, OR
      - its ledger_role/proof_mode marks it as the replacement, OR
      - it is the `generated_diagnostic` source row that has been
        superseded by an audited-core replacement (kept only for trace).

    The superseded-source case is critical: those rows still carry
    `status=FULLY_PROVEN` for ledger continuity, but their generated
    `prop_sharpness`-style theorem may not even exist in
    `output/<id>.lean` — the verified claim lives on the replacement row.
    Demoting them would mistakenly re-flag a row that has already been
    retired through the audited-core path.
    """
    name = str(entry.get("theorem_name", "") or "")
    if name.endswith(AUDITED_CORE_SUFFIX):
        return True
    role = str(entry.get("ledger_role", "") or "")
    if role in AUDITED_CORE_ROLES:
        return True
    mode = str(entry.get("proof_mode", "") or "")
    if mode in AUDITED_CORE_MODES:
        return True
    if bool(entry.get("superseded_by_audited_core")):
        return True
    return False


def _theorem_body_in_file(lean_src: str, theorem_name: str) -> str | None:
    """Return the body that follows `theorem <name> ... := by` in lean_src.

    Returns None when the theorem isn't found by name, OR when the
    declaration uses term-mode (`:= rfl`, `:= Iff.rfl`, etc.) without a
    `:= by` tactic block — the audit only applies to tactic-mode proofs.

    The regex tolerates multi-line signatures and arbitrary whitespace
    between `:=` and `by`, AND a same-line body (`:= by sorry`). The
    `Beta := False := by sorry` form on a single line is a tactic-mode
    proof with body `sorry` — it must still be classified as such.
    """
    pat = re.compile(
        r"theorem\s+" + re.escape(theorem_name) + r"\b[\s\S]*?:=\s*by\b[ \t]*([\s\S]{1,4000}?)(?=\n(?:theorem|lemma|def|end|--|namespace|section)\b|\Z)",
        re.MULTILINE,
    )
    m = pat.search(lean_src)
    if not m:
        return None
    body = m.group(1)
    # Strip leading blank/whitespace lines, keep the meaningful prefix.
    return body.lstrip("\n").lstrip()


def _body_is_sorry(body: str) -> bool:
    """A body is 'sorry' when its first meaningful token is `sorry`."""
    if not body:
        return False
    stripped = body.strip()
    if not stripped:
        return False
    # First non-comment, non-blank line should NOT be `sorry` (or start with
    # `sorry` followed only by whitespace/comments).
    for line in stripped.splitlines():
        line = line.strip()
        if not line or line.startswith("--"):
            continue
        return line == "sorry" or line.startswith("sorry ")
    return False


def _demote_entry(entry: dict[str, Any], *, captured: str, file_preview: str) -> None:
    """Mutate `entry` in place: reset FP claims to honest UNRESOLVED state."""
    entry["status"] = "UNRESOLVED"
    entry["proved"] = False
    entry["step_verdict"] = "INCOMPLETE"
    entry["failure_origin"] = "PROOF_SEARCH_ERROR"
    entry["failure_kind"] = "proof_search_unattempted"
    entry["proof_method"] = "unattempted"
    # The previously-stored proof_text was never validated against the file.
    # Move it to an audit field; clear the live field.
    entry["audit_demotion"] = {
        "schema_version": "audit_fully_proven_integrity.v1",
        "previous_status": "FULLY_PROVEN",
        "captured_proof_text": captured,
        "file_body_preview": file_preview,
        "reason": "file_body_is_sorry_but_ledger_claimed_closed",
    }
    entry["proof_text"] = ""
    vg = entry.get("validation_gates") if isinstance(entry.get("validation_gates"), dict) else {}
    vg["lean_proof_closed"] = False
    vg["step_verdict_verified"] = False
    entry["validation_gates"] = vg
    failures = [str(x) for x in (entry.get("gate_failures") or [])]
    for f in ("lean_proof_closed", "step_verdict_verified"):
        if f not in failures:
            failures.append(f)
    entry["gate_failures"] = failures
    # Notes for traceability.
    notes = [str(x) for x in (entry.get("claim_equivalence_notes") or [])]
    notes.append("audit_demotion:fully_proven_integrity")
    entry["claim_equivalence_notes"] = list(dict.fromkeys(notes))


_PROOF_CLAIMING_STATUSES: tuple[str, ...] = (
    "FULLY_PROVEN",
    "AXIOM_BACKED",
    "INTERMEDIARY_PROVEN",
)


def _claims_lean_proof_closed(entry: dict[str, Any]) -> bool:
    """A row claims its proof is closed if the validation_gates flag is True
    OR the status is one of the proof-claiming tiers (FP/AB/IP) regardless of
    the gate flag's stored value (the flag is the bypass we're trying to
    catch — never trust it alone)."""
    gates = entry.get("validation_gates", {})
    if isinstance(gates, dict) and gates.get("lean_proof_closed") is True:
        return True
    status = str(entry.get("status", "") or "")
    return status in _PROOF_CLAIMING_STATUSES


def audit_ledger_entries(
    entries: list[dict[str, Any]],
    *,
    paper_id: str,
    lean_src: str,
    statuses: tuple[str, ...] = ("FULLY_PROVEN",),
) -> AuditResult:
    """Walk one ledger's entries; demote rows that claim proof closure but
    whose actual .lean file body is `sorry`. Returns AuditResult.

    `statuses` controls which status tiers are audited. Default is just
    FULLY_PROVEN (backward-compatible). Pass `_PROOF_CLAIMING_STATUSES`
    (i.e., ('FULLY_PROVEN', 'AXIOM_BACKED', 'INTERMEDIARY_PROVEN')) to also
    catch bypass-IPs and bypass-ABs — the same circular gate bypass that
    inflated FP can inflate AB/IP. The AuditResult's `fp_*` fields are
    misnamed in expanded mode (they count all audited rows, not just FP).
    """
    audit_set = set(statuses)
    result = AuditResult(paper_id=paper_id)
    for entry in entries:
        st = str(entry.get("status", "") or "")
        if st not in audit_set:
            continue
        # In expanded-status mode we only audit rows that actually claim
        # proof closure; non-claiming IP rows (e.g. status downgrade after
        # a review-evidence-only promotion) are skipped.
        if st != "FULLY_PROVEN" and not _claims_lean_proof_closed(entry):
            continue
        result.fp_pre += 1
        if _is_audited_core_row(entry):
            result.audited_core_skipped += 1
            continue
        name = str(entry.get("theorem_name", "") or "")
        if not name:
            result.not_found_skipped += 1
            continue
        body = _theorem_body_in_file(lean_src, name)
        if body is None:
            # No `:= by` block found — either term-mode proof (e.g. `:= rfl`)
            # or the theorem isn't in this file at all.
            if re.search(
                r"theorem\s+" + re.escape(name) + r"\b[\s\S]*?:=\s*(?!by\b)",
                lean_src,
            ):
                # Term-mode declaration: Lean has compiled it.
                result.term_mode_skipped += 1
            else:
                result.not_found_skipped += 1
            continue
        if _body_is_sorry(body):
            captured = str(entry.get("proof_text", "") or "")
            file_preview = body[:120].rstrip()
            _demote_entry(entry, captured=captured, file_preview=file_preview)
            result.demoted += 1
            result.demotions.append(
                Demotion(
                    paper_id=paper_id,
                    theorem_name=name,
                    captured_proof_text=captured[:80],
                    file_body_preview=file_preview[:60],
                    reason="file_body_is_sorry",
                )
            )
        else:
            result.validated_clean += 1
    result.fp_post = result.fp_pre - result.demoted
    return result


def audit_ledger_file(
    ledger_path: Path,
    lean_path: Path,
    *,
    paper_id: str,
    write: bool = False,
    statuses: tuple[str, ...] = ("FULLY_PROVEN",),
) -> AuditResult:
    """Audit one ledger file against one Lean source file.

    Returns an AuditResult. Writes back to `ledger_path` only when
    `write=True` AND at least one demotion occurred. `statuses` defaults to
    just FULLY_PROVEN; pass the broader tuple to also audit AB/IP rows
    whose stored `lean_proof_closed=True` flag came from the circular bypass.
    """
    if not ledger_path.exists() or not lean_path.exists():
        return AuditResult(paper_id=paper_id)
    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    entries = data if isinstance(data, list) else data.get("entries", [])
    lean_src = lean_path.read_text(encoding="utf-8")
    result = audit_ledger_entries(
        entries, paper_id=paper_id, lean_src=lean_src, statuses=statuses,
    )
    if write and result.demoted > 0:
        payload = data if isinstance(data, list) else {**data, "entries": entries}
        ledger_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return result


def audit_paper(
    paper_id: str,
    *,
    ledger_dir: Path,
    lean_dir: Path,
    repro_dir: Path,
    write: bool = False,
    statuses: tuple[str, ...] = ("FULLY_PROVEN",),
) -> dict[str, Any]:
    """Audit both ephemeral and canonical ledgers for one paper.

    Both ledgers are audited against the SAME `output/<paper>.lean` file, so
    the demotion verdict is consistent across them. Returns a summary dict
    suitable for JSON output. `statuses` defaults to just FULLY_PROVEN; pass
    `_PROOF_CLAIMING_STATUSES` to also catch bypass-IPs and bypass-ABs.
    """
    lean_path = lean_dir / f"{paper_id}.lean"
    ephem = ledger_dir / f"{paper_id}.json"
    canonical = repro_dir / paper_id / "verification_ledger.json"
    out: dict[str, Any] = {"paper_id": paper_id, "lean_file": str(lean_path)}
    if not lean_path.exists():
        out["skipped"] = "lean_file_missing"
        return out
    for label, path in (("ephemeral", ephem), ("canonical", canonical)):
        if not path.exists():
            out[label] = {"missing": True}
            continue
        r = audit_ledger_file(
            path, lean_path, paper_id=paper_id, write=write, statuses=statuses,
        )
        out[label] = {
            "path": str(path),
            "fp_pre": r.fp_pre,
            "fp_post": r.fp_post,
            "demoted": r.demoted,
            "audited_core_skipped": r.audited_core_skipped,
            "term_mode_skipped": r.term_mode_skipped,
            "not_found_skipped": r.not_found_skipped,
            "validated_clean": r.validated_clean,
            "demotions": [
                {
                    "theorem_name": d.theorem_name,
                    "captured_proof_text": d.captured_proof_text,
                    "file_body_preview": d.file_body_preview,
                    "reason": d.reason,
                }
                for d in r.demotions
            ],
        }
    return out


def _papers_from_ledger_dir(ledger_dir: Path) -> list[str]:
    if not ledger_dir.exists():
        return []
    ids: list[str] = []
    for p in sorted(ledger_dir.glob("*.json")):
        name = p.stem
        # Skip non-canonical variants used by smoke/probe runs.
        if any(s in name for s in ("_smoke", "_actionable", "_fdcheck", "_patchcheck", "_rflguard", "_repair", "_reliable", "ab_repair", "_fast")):
            continue
        ids.append(name)
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger-dir", type=Path, default=DEFAULT_LEDGER_DIR)
    parser.add_argument("--lean-dir", type=Path, default=DEFAULT_LEAN_DIR)
    parser.add_argument("--repro-dir", type=Path, default=DEFAULT_REPRO_DIR)
    parser.add_argument(
        "--papers",
        nargs="*",
        default=None,
        help="Specific paper IDs to audit (default: every canonical ledger)",
    )
    parser.add_argument("--write", action="store_true", help="Apply demotions; default is dry-run")
    parser.add_argument(
        "--include-ip-ab",
        action="store_true",
        help=(
            "Also audit AXIOM_BACKED and INTERMEDIARY_PROVEN rows (the same "
            "circular bypass that inflated FP can inflate AB/IP). Default OFF "
            "for backward compat; turning ON gives a fuller integrity sweep."
        ),
    )
    args = parser.parse_args()

    statuses: tuple[str, ...] = (
        _PROOF_CLAIMING_STATUSES if args.include_ip_ab else ("FULLY_PROVEN",)
    )

    paper_ids = args.papers if args.papers else _papers_from_ledger_dir(args.ledger_dir)
    summary: dict[str, Any] = {
        "schema_version": "audit_fully_proven_integrity.v1",
        "dry_run": not args.write,
        "lean_dir": str(args.lean_dir),
        "ledger_dir": str(args.ledger_dir),
        "repro_dir": str(args.repro_dir),
        "audited_statuses": list(statuses),
        "papers": {},
    }
    totals = {"fp_pre": 0, "fp_post": 0, "demoted": 0, "audited_core_skipped": 0, "term_mode_skipped": 0, "validated_clean": 0}
    for pid in paper_ids:
        result = audit_paper(
            pid,
            ledger_dir=args.ledger_dir,
            lean_dir=args.lean_dir,
            repro_dir=args.repro_dir,
            write=args.write,
            statuses=statuses,
        )
        summary["papers"][pid] = result
        # Totals are computed from the canonical ledger when present, else
        # the ephemeral one (the two should agree post-write).
        for label in ("canonical", "ephemeral"):
            sub = result.get(label) if isinstance(result.get(label), dict) else None
            if sub and "fp_pre" in sub:
                totals["fp_pre"] += sub["fp_pre"]
                totals["fp_post"] += sub["fp_post"]
                totals["demoted"] += sub["demoted"]
                totals["audited_core_skipped"] += sub["audited_core_skipped"]
                totals["term_mode_skipped"] += sub["term_mode_skipped"]
                totals["validated_clean"] += sub["validated_clean"]
                break
    summary["totals"] = totals
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
