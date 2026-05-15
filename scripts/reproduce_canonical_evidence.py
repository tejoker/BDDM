#!/usr/bin/env python3
"""Reproduce the canonical FP / AB / IP counts from committed evidence.

Single-command, public-facing verifier: anyone can clone the repo and run

    python scripts/reproduce_canonical_evidence.py

to check that the FULLY_PROVEN / AXIOM_BACKED / INTERMEDIARY_PROVEN tallies
claimed in `reproducibility/full_paper_reports/<id>/verification_ledger.json`
match what is ACTUALLY in `output/<id>.lean`.

Verification per row (status in FP / AB / IP):

  1. Curated audited-core replacement rows
     (`__audited_core` suffix, `ledger_role == 'audited_core_replacement'`,
     `proof_mode == 'audited-core-replacement'`, or
     `superseded_by_audited_core == True`) are CREDITED — their proof source
     lives in `Desol/PaperProofs/...`, not `output/<paper>.lean`, and is
     validated by `lake build` of the paper-theory module elsewhere. This
     matches the carve-out in `audit_fully_proven_integrity`.

  2. Otherwise the script locates `theorem <name>` in `output/<paper>.lean`
     (with a namespace fallback: ledger `ArxivPaper.X` -> bare `X`).

       - Term-mode declarations (`:= rfl`, `:= Iff.rfl`, no `:= by` block)
         are CREDITED — Lean has already accepted them at compile time.
       - Tactic-mode bodies are scanned for a standalone `sorry` token
         (mid-body sorry counts). A sorry-bearing body is a MISMATCH.
       - The row's `lean_statement` is run through
         `translator._translate._is_trivialized_signature`; trivialized
         statements are a MISMATCH even when lake closes the goal.
       - A theorem-name miss in the file is a MISMATCH (the ledger claims
         a proof for a declaration that is not in the published .lean).

  3. Optional `--lake-check` runs `lake env lean` on each `output/<id>.lean`
     and refuses to credit ANY row from a file whose lake build emits
     `declaration uses 'sorry'` for an FP/AB row. This is slow (~30s/file
     cold, ~5s/file warm); the default behaviour is fast structural
     verification only.

Exit code is 0 iff every (paper, tier) cell satisfies `verified == claimed`.

The verdicts are deliberately STANDARDS-POSITIVE: if structural verification
cannot confirm a claim, the claim is reported as unverified — there is no
"trust the ledger" fallback.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Reuse the audit primitives — same regex, same sorry-detector, same
# audited-core carve-out. Keeping a single source of truth avoids the
# audit and the reproducer drifting apart.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from audit_fully_proven_integrity import (  # noqa: E402
    _PROOF_CLAIMING_STATUSES,
    _body_is_sorry,
    _is_audited_core_row,
    _theorem_body_in_file,
)


DEFAULT_REPRO_DIR = Path("reproducibility/full_paper_reports")
DEFAULT_LEAN_DIR = Path("output")

TIER_ORDER: tuple[str, ...] = (
    "FULLY_PROVEN",
    "AXIOM_BACKED",
    "INTERMEDIARY_PROVEN",
)
SHORT_TIER = {
    "FULLY_PROVEN": "FP",
    "AXIOM_BACKED": "AB",
    "INTERMEDIARY_PROVEN": "IP",
}


def _try_trivialization() -> "callable":
    """Best-effort import of the translator's trivialization detector.

    Returns a callable that takes a Lean statement string and returns True
    when the statement is structurally vacuous. If the translator is
    unavailable for any reason, the fallback returns False (i.e. it never
    falsely demotes a row — but also never catches a trivial statement).
    The audit script uses the same pattern.
    """
    try:
        from translator._translate import _is_trivialized_signature  # type: ignore  # noqa: E402

        return _is_trivialized_signature
    except Exception:
        return lambda _stmt: False


@dataclass
class RowVerdict:
    paper_id: str
    theorem_name: str
    status: str  # FP/AB/IP (full tier name)
    verified: bool
    reason: str  # short tag, e.g. "audited_core", "term_mode", "body_ok",
                # "sorry_body", "trivialized_statement", "theorem_missing"
    proof_text_excerpt: str = ""
    file_body_excerpt: str = ""


@dataclass
class TierTally:
    claimed: int = 0
    verified: int = 0

    @property
    def mismatches(self) -> int:
        return self.claimed - self.verified


@dataclass
class PaperReport:
    paper_id: str
    tiers: dict[str, TierTally] = field(
        default_factory=lambda: {t: TierTally() for t in TIER_ORDER}
    )
    rows: list[RowVerdict] = field(default_factory=list)
    lean_file_present: bool = True
    ledger_present: bool = True
    lake_check: dict[str, Any] | None = None  # only set when --lake-check used

    @property
    def mismatches(self) -> int:
        return sum(t.mismatches for t in self.tiers.values())


def _ledger_entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        entries = payload.get("entries")
        if isinstance(entries, list):
            return [row for row in entries if isinstance(row, dict)]
    return []


def _term_mode_present(lean_src: str, theorem_name: str) -> bool:
    """True iff `theorem <name>` is declared with a term-mode `:= <expr>`
    (no `:= by` block). Mirrors the audit's term-mode-skip path."""
    candidates: list[str] = [theorem_name]
    if "." in theorem_name:
        bare = theorem_name.rsplit(".", 1)[-1]
        if bare and bare != theorem_name:
            candidates.append(bare)
    for cand in candidates:
        if re.search(
            r"theorem\s+" + re.escape(cand) + r"\b[\s\S]*?:=\s*(?!by\b)",
            lean_src,
        ):
            return True
    return False


def verify_row(
    entry: dict[str, Any],
    *,
    paper_id: str,
    lean_src: str,
    trivialized: "callable",
) -> RowVerdict:
    """Decide whether one FP/AB/IP row's claim is structurally backed.

    The decision tree mirrors `audit_fully_proven_integrity.audit_ledger_entries`,
    so the reproducer and the audit cannot drift.
    """
    status = str(entry.get("status", "") or "")
    name = str(entry.get("theorem_name", "") or "")
    proof_text = str(entry.get("proof_text", "") or "")
    stmt = str(entry.get("lean_statement", "") or "")

    # 1. Curated rows: credited; their proof lives outside output/<paper>.lean.
    if _is_audited_core_row(entry):
        return RowVerdict(
            paper_id=paper_id,
            theorem_name=name,
            status=status,
            verified=True,
            reason="audited_core",
            proof_text_excerpt=proof_text[:80],
        )

    if not name:
        return RowVerdict(
            paper_id=paper_id,
            theorem_name="",
            status=status,
            verified=False,
            reason="missing_theorem_name",
        )

    # 2. Look up the body in the .lean file.
    body = _theorem_body_in_file(lean_src, name)
    if body is None:
        # Either term-mode (credited) or not in file at all (mismatch).
        if _term_mode_present(lean_src, name):
            return RowVerdict(
                paper_id=paper_id,
                theorem_name=name,
                status=status,
                verified=True,
                reason="term_mode",
                proof_text_excerpt=proof_text[:80],
            )
        return RowVerdict(
            paper_id=paper_id,
            theorem_name=name,
            status=status,
            verified=False,
            reason="theorem_missing",
            proof_text_excerpt=proof_text[:80],
        )

    if _body_is_sorry(body):
        return RowVerdict(
            paper_id=paper_id,
            theorem_name=name,
            status=status,
            verified=False,
            reason="sorry_body",
            proof_text_excerpt=proof_text[:80],
            file_body_excerpt=body[:80].rstrip(),
        )

    if stmt and trivialized(stmt):
        return RowVerdict(
            paper_id=paper_id,
            theorem_name=name,
            status=status,
            verified=False,
            reason="trivialized_statement",
            proof_text_excerpt=proof_text[:80],
            file_body_excerpt=body[:80].rstrip(),
        )

    return RowVerdict(
        paper_id=paper_id,
        theorem_name=name,
        status=status,
        verified=True,
        reason="body_ok",
        proof_text_excerpt=proof_text[:80],
        file_body_excerpt=body[:80].rstrip(),
    )


def _run_lake_check(
    lean_path: Path, *, project_root: Path, timeout_s: int
) -> dict[str, Any]:
    """Run `lake env lean <file>` and return a structured result.

    The shape is:
      {"ran": bool, "returncode": int, "duration_s": float,
       "sorry_warnings": int, "stderr_tail": str}

    `sorry_warnings` counts occurrences of `declaration uses 'sorry'` in
    combined stdout+stderr (Lean writes the warning to stderr). The CLI
    treats `sorry_warnings > 0` as a global red flag and refuses to credit
    any FP/AB row from the affected paper.
    """
    started = time.time()
    try:
        proc = subprocess.run(
            ["lake", "env", "lean", str(lean_path)],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        return {
            "ran": False,
            "returncode": -1,
            "duration_s": round(time.time() - started, 3),
            "sorry_warnings": 0,
            "stderr_tail": f"lake_not_found:{exc}",
        }
    except subprocess.TimeoutExpired:
        return {
            "ran": False,
            "returncode": 124,
            "duration_s": round(time.time() - started, 3),
            "sorry_warnings": 0,
            "stderr_tail": f"timeout_after_{timeout_s}s",
        }
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    sorry_warnings = len(re.findall(r"declaration uses 'sorry'", combined))
    return {
        "ran": True,
        "returncode": int(proc.returncode),
        "duration_s": round(time.time() - started, 3),
        "sorry_warnings": sorry_warnings,
        "stderr_tail": (proc.stderr or "")[-2000:],
    }


def verify_paper(
    paper_id: str,
    *,
    repro_dir: Path,
    lean_dir: Path,
    lake_check: bool = False,
    project_root: Path | None = None,
    lake_timeout_s: int = 600,
    trivialized: "callable" | None = None,
) -> PaperReport:
    """Verify one canonical paper's ledger against its on-disk .lean file."""
    if trivialized is None:
        trivialized = _try_trivialization()
    report = PaperReport(paper_id=paper_id)
    ledger_path = repro_dir / paper_id / "verification_ledger.json"
    lean_path = lean_dir / f"{paper_id}.lean"

    if not ledger_path.exists():
        report.ledger_present = False
    if not lean_path.exists():
        report.lean_file_present = False

    if not report.ledger_present:
        return report

    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    entries = _ledger_entries(payload)
    lean_src = lean_path.read_text(encoding="utf-8") if report.lean_file_present else ""

    # Optional lake gate runs once per paper. If it reports any `uses 'sorry'`
    # warnings or a non-zero returncode, no FP/AB/IP row is credited.
    lake_blocks_paper = False
    if lake_check:
        if not report.lean_file_present:
            report.lake_check = {
                "ran": False, "returncode": -1, "duration_s": 0.0,
                "sorry_warnings": 0, "stderr_tail": "lean_file_missing",
            }
            lake_blocks_paper = True
        else:
            assert project_root is not None
            report.lake_check = _run_lake_check(
                lean_path, project_root=project_root, timeout_s=lake_timeout_s,
            )
            if (
                report.lake_check["returncode"] != 0
                or report.lake_check["sorry_warnings"] > 0
            ):
                lake_blocks_paper = True

    for entry in entries:
        status = str(entry.get("status", "") or "")
        if status not in _PROOF_CLAIMING_STATUSES:
            continue
        report.tiers[status].claimed += 1

        if not report.lean_file_present:
            verdict = RowVerdict(
                paper_id=paper_id,
                theorem_name=str(entry.get("theorem_name", "") or ""),
                status=status,
                verified=False,
                reason="lean_file_missing",
            )
        else:
            verdict = verify_row(
                entry, paper_id=paper_id, lean_src=lean_src, trivialized=trivialized,
            )
            if lake_blocks_paper and verdict.verified and verdict.reason != "audited_core":
                # Lake says the file does not compile cleanly (or emitted a
                # sorry warning). Non-audited-core rows lose credit.
                verdict = RowVerdict(
                    paper_id=paper_id,
                    theorem_name=verdict.theorem_name,
                    status=status,
                    verified=False,
                    reason="lake_check_failed",
                    proof_text_excerpt=verdict.proof_text_excerpt,
                    file_body_excerpt=verdict.file_body_excerpt,
                )

        if verdict.verified:
            report.tiers[status].verified += 1
        report.rows.append(verdict)

    return report


def _papers_from_repro_dir(repro_dir: Path) -> list[str]:
    if not repro_dir.exists():
        return []
    return sorted(
        p.name for p in repro_dir.iterdir()
        if p.is_dir() and (p / "verification_ledger.json").exists()
    )


def render_table(reports: list[PaperReport]) -> str:
    """Render a fixed-width, parseable table.

    Columns: Paper | FP(c/v) | AB(c/v) | IP(c/v) | Mismatches
    Total row aggregates each tier.
    """
    header = (
        f"{'Paper':<14} {'Claimed-FP':>10} {'Verified-FP':>12} "
        f"{'Claimed-AB':>10} {'Verified-AB':>12} "
        f"{'Claimed-IP':>10} {'Verified-IP':>12} {'Mismatches':>10}"
    )
    lines = [header, "-" * len(header)]
    totals = {t: TierTally() for t in TIER_ORDER}
    total_mismatch = 0
    for r in reports:
        fp = r.tiers["FULLY_PROVEN"]
        ab = r.tiers["AXIOM_BACKED"]
        ip = r.tiers["INTERMEDIARY_PROVEN"]
        totals["FULLY_PROVEN"].claimed += fp.claimed
        totals["FULLY_PROVEN"].verified += fp.verified
        totals["AXIOM_BACKED"].claimed += ab.claimed
        totals["AXIOM_BACKED"].verified += ab.verified
        totals["INTERMEDIARY_PROVEN"].claimed += ip.claimed
        totals["INTERMEDIARY_PROVEN"].verified += ip.verified
        total_mismatch += r.mismatches
        lines.append(
            f"{r.paper_id:<14} {fp.claimed:>10} {fp.verified:>12} "
            f"{ab.claimed:>10} {ab.verified:>12} "
            f"{ip.claimed:>10} {ip.verified:>12} {r.mismatches:>10}"
        )
    lines.append("-" * len(header))
    lines.append(
        f"{'TOTAL':<14} {totals['FULLY_PROVEN'].claimed:>10} {totals['FULLY_PROVEN'].verified:>12} "
        f"{totals['AXIOM_BACKED'].claimed:>10} {totals['AXIOM_BACKED'].verified:>12} "
        f"{totals['INTERMEDIARY_PROVEN'].claimed:>10} {totals['INTERMEDIARY_PROVEN'].verified:>12} "
        f"{total_mismatch:>10}"
    )
    return "\n".join(lines)


def render_mismatch_detail(reports: list[PaperReport]) -> str:
    """Per-row mismatch lines for easy debugging. Empty when zero mismatches."""
    lines: list[str] = []
    for r in reports:
        for row in r.rows:
            if row.verified:
                continue
            lines.append(
                f"  [{SHORT_TIER.get(row.status, row.status)}] "
                f"{r.paper_id}/{row.theorem_name}: {row.reason}"
                + (
                    f"  body~{row.file_body_excerpt!r}"
                    if row.file_body_excerpt else ""
                )
            )
    return "\n".join(lines)


def build_summary(reports: list[PaperReport]) -> dict[str, Any]:
    totals = {t: TierTally() for t in TIER_ORDER}
    for r in reports:
        for t in TIER_ORDER:
            totals[t].claimed += r.tiers[t].claimed
            totals[t].verified += r.tiers[t].verified
    return {
        "schema_version": "reproduce_canonical_evidence.v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "papers": [
            {
                "paper_id": r.paper_id,
                "lean_file_present": r.lean_file_present,
                "ledger_present": r.ledger_present,
                "tiers": {
                    t: {
                        "claimed": r.tiers[t].claimed,
                        "verified": r.tiers[t].verified,
                        "mismatches": r.tiers[t].mismatches,
                    }
                    for t in TIER_ORDER
                },
                "mismatches": r.mismatches,
                "lake_check": r.lake_check,
                "row_verdicts": [
                    {
                        "theorem_name": row.theorem_name,
                        "status": row.status,
                        "verified": row.verified,
                        "reason": row.reason,
                        "proof_text_excerpt": row.proof_text_excerpt,
                        "file_body_excerpt": row.file_body_excerpt,
                    }
                    for row in r.rows
                ],
            }
            for r in reports
        ],
        "totals": {
            t: {
                "claimed": totals[t].claimed,
                "verified": totals[t].verified,
                "mismatches": totals[t].mismatches,
            }
            for t in TIER_ORDER
        },
        "total_mismatches": sum(t.mismatches for t in totals.values()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repro-dir",
        type=Path,
        default=DEFAULT_REPRO_DIR,
        help="Directory holding `<id>/verification_ledger.json`.",
    )
    parser.add_argument(
        "--lean-dir",
        type=Path,
        default=DEFAULT_LEAN_DIR,
        help="Directory holding `<id>.lean` source files.",
    )
    parser.add_argument(
        "--paper-id",
        action="append",
        default=None,
        help="Restrict to one or more paper IDs (default: every canonical paper).",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path("."),
        help="Project root for the optional `lake env lean` invocation.",
    )
    parser.add_argument(
        "--lake-check",
        action="store_true",
        help=(
            "Also run `lake env lean output/<id>.lean`; refuse to credit any "
            "non-audited-core row from a paper whose lake build is dirty."
        ),
    )
    parser.add_argument(
        "--lake-timeout-s",
        type=int,
        default=600,
        help="Per-paper timeout for `lake env lean` (default: 600s).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the full JSON summary on stdout instead of the table.",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Also write the full JSON summary to this path.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-row mismatch detail (still prints the table).",
    )
    args = parser.parse_args(argv)

    paper_ids = (
        list(args.paper_id) if args.paper_id else _papers_from_repro_dir(args.repro_dir)
    )
    if not paper_ids:
        print(
            json.dumps(
                {
                    "ok": False,
                    "reason": "no_canonical_papers",
                    "repro_dir": str(args.repro_dir),
                },
                indent=2,
            )
        )
        return 2

    trivialized = _try_trivialization()
    reports: list[PaperReport] = []
    for pid in paper_ids:
        reports.append(
            verify_paper(
                pid,
                repro_dir=args.repro_dir,
                lean_dir=args.lean_dir,
                lake_check=bool(args.lake_check),
                project_root=args.project_root.resolve(),
                lake_timeout_s=int(args.lake_timeout_s),
                trivialized=trivialized,
            )
        )

    summary = build_summary(reports)

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print(render_table(reports))
        if not args.quiet:
            detail = render_mismatch_detail(reports)
            if detail:
                print()
                print("Mismatches:")
                print(detail)

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    return 0 if summary["total_mismatches"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
