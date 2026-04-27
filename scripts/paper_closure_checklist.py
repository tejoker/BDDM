#!/usr/bin/env python3
"""Strict paper-closure checklist runner (theorem-by-theorem).

Evaluates each theorem in a paper ledger against seven hard gates:
1) translation fidelity
2) goal actionability
3) proof synthesis closure
4) theorem context/dependency grounding
5) bridge candidate quality
6) retrieval/memory retry evidence
7) final acceptance gate
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from claim_equivalence_review import build_review_queue, summarize_review_queue


_SCHEMA_NOISE_RE = re.compile(
    r"(literal_schema_translation|schema_(assumption|claim)|\\\{ \{ll|Missing theorem statement)",
    re.IGNORECASE,
)
_THEOREM_START_RE = re.compile(
    r"^\s*(?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma|def)\s+([A-Za-z_][A-Za-z0-9_'.]*)\b"
)
_THEOREM_NEXT_RE = re.compile(
    r"^\s*(?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma|def|end|namespace)\b"
)


def _safe_id(paper_id: str) -> str:
    return paper_id.replace("/", "_").replace(":", "_")


def _load_entries(ledger_path: Path) -> list[dict[str, Any]]:
    if not ledger_path.exists():
        return []
    try:
        raw = json.loads(ledger_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
        return [r for r in raw.get("entries", []) if isinstance(r, dict)]
    return []


def _canonical_theorem_key(name: str) -> str:
    n = str(name or "").strip()
    if n.startswith("ArxivPaper."):
        return n.split(".", 1)[1]
    if n.startswith("ArxivPaperActionable."):
        return n.split(".", 1)[1]
    return n


def _extract_decl_for_theorem(lean_text: str, theorem_name: str) -> str:
    if not lean_text or not theorem_name:
        return ""
    short = theorem_name.rsplit(".", 1)[-1]
    lines = lean_text.splitlines()
    start = -1
    for i, ln in enumerate(lines):
        m = _THEOREM_START_RE.match(ln)
        if not m:
            continue
        nm = m.group(1).rsplit(".", 1)[-1]
        if nm == short:
            start = i
            break
    if start < 0:
        return ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if _THEOREM_NEXT_RE.match(lines[j]):
            end = j
            break
    return "\n".join(lines[start:end]).strip()


def _normalize_signature(sig: str, theorem_name: str) -> str:
    s = (sig or "").strip()
    if not s:
        return ""
    s = re.sub(r"(?s):=\s*by\b.*$", "", s).strip()
    s = re.sub(r"(?s):=\s*sorry\b.*$", "", s).strip()
    short = theorem_name.rsplit(".", 1)[-1]
    s = re.sub(
        r"^\s*((?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma|def)\s+)([A-Za-z_][A-Za-z0-9_'.]*)",
        rf"\1{short}",
        s,
        count=1,
    )
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _signature_stable(row: dict[str, Any], lean_text_by_file: dict[str, str]) -> tuple[bool, str]:
    theorem_name = str(row.get("theorem_name", "")).strip()
    if not theorem_name:
        return False, "missing_theorem_name"
    lean_file = str(row.get("lean_file", "")).strip()
    if not lean_file:
        return False, "missing_lean_file"
    source_stmt = str(row.get("lean_statement", "") or "").strip()
    if not source_stmt:
        return False, "missing_source_signature"
    lean_text = lean_text_by_file.get(lean_file, "")
    if not lean_text:
        return False, "lean_file_unreadable"
    current_decl = _extract_decl_for_theorem(lean_text, theorem_name)
    if not current_decl:
        return False, "current_declaration_missing"
    base = _normalize_signature(source_stmt, theorem_name)
    cur = _normalize_signature(current_decl, theorem_name)
    if not base or not cur:
        return False, "signature_parse_failed"
    if base == cur:
        return True, ""
    return False, "signature_changed"


def _dedupe_by_canonical_theorem(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rank = {"FULLY_PROVEN": 4, "INTERMEDIARY_PROVEN": 3, "FLAWED": 2, "UNRESOLVED": 1}
    best: dict[str, dict[str, Any]] = {}
    for row in entries:
        if not isinstance(row, dict):
            continue
        key = _canonical_theorem_key(str(row.get("theorem_name", "")))
        if not key:
            continue
        cur = best.get(key)
        if cur is None:
            best[key] = row
            continue
        cur_status = str(cur.get("status", "")).upper()
        row_status = str(row.get("status", "")).upper()
        cur_score = (
            rank.get(cur_status, 0),
            1 if bool(cur.get("promotion_gate_passed", False)) else 0,
            1 if bool((cur.get("validation_gates", {}) or {}).get("status_alignment_ok", False)) else 0,
        )
        row_score = (
            rank.get(row_status, 0),
            1 if bool(row.get("promotion_gate_passed", False)) else 0,
            1 if bool((row.get("validation_gates", {}) or {}).get("status_alignment_ok", False)) else 0,
        )
        if row_score > cur_score:
            best[key] = row
    return list(best.values())


def _assumption_stats(row: dict[str, Any]) -> dict[str, int]:
    assumptions = row.get("assumptions", [])
    if not isinstance(assumptions, list):
        assumptions = []
    total = 0
    grounded = 0
    placeholders = 0
    for a in assumptions:
        if not isinstance(a, dict):
            continue
        total += 1
        g = str(a.get("grounding", "")).upper()
        trust = str(a.get("trust_class", "")).upper()
        if g.startswith("GROUNDED"):
            grounded += 1
        if trust == "TRUST_PLACEHOLDER" or g in {"UNGROUNDED", "UNKNOWN", ""}:
            placeholders += 1
    return {
        "total": total,
        "grounded": grounded,
        "placeholders": placeholders,
    }


def _evaluate_row(row: dict[str, Any], *, lean_text_by_file: dict[str, str]) -> dict[str, Any]:
    theorem_name = str(row.get("theorem_name", "")).strip()
    status = str(row.get("status", "")).strip().upper()
    gates = row.get("validation_gates", {})
    if not isinstance(gates, dict):
        gates = {}
    stats = _assumption_stats(row)
    lean_statement = str(row.get("lean_statement", "") or "")
    error_message = str(row.get("error_message", "") or "")
    failure_origin = str(row.get("failure_origin", "") or "UNKNOWN").strip().upper()
    claim_equiv = str(row.get("claim_equivalence_verdict", "unclear") or "unclear").strip().lower()
    semantic_artifact = row.get("semantic_equivalence_artifact")
    if isinstance(semantic_artifact, dict):
        claim_equiv = str(
            semantic_artifact.get("equivalence_verdict", claim_equiv) or claim_equiv
        ).strip().lower()
    independent_semantic = bool(
        gates.get(
            "independent_semantic_equivalence_evidence",
            bool(semantic_artifact.get("independent_semantic_evidence"))
            if isinstance(semantic_artifact, dict)
            else False,
        )
    )
    review_required = bool(row.get("review_required", False))
    rounds_used = int(row.get("rounds_used", 0) or 0)
    step_obligations = row.get("step_obligations", [])
    if not isinstance(step_obligations, list):
        step_obligations = []
    has_schema_noise = bool(_SCHEMA_NOISE_RE.search(lean_statement) or _SCHEMA_NOISE_RE.search(error_message))
    no_placeholders = stats["placeholders"] == 0
    signature_stable, signature_reason = _signature_stable(row, lean_text_by_file)

    checklist: dict[str, bool] = {
        "translation_fidelity": bool(gates.get("translation_fidelity_ok", False)) and not has_schema_noise,
        "claim_equivalence": claim_equiv == "equivalent" and independent_semantic,
        "theorem_signature_stable": signature_stable,
        "goal_actionability": bool(gates.get("assumptions_grounded", False))
        and stats["grounded"] > 0
        and no_placeholders,
        "proof_synthesis": bool(gates.get("lean_proof_closed", False))
        and bool(gates.get("step_verdict_verified", False)),
        "context_grounding": bool(gates.get("dependency_trust_complete", False)) and no_placeholders,
        "bridge_candidate_quality": bool(gates.get("assumptions_grounded", False))
        and failure_origin not in {"UNKNOWN", "FORMALIZATION_ERROR"}
        and "missing theorem statement" not in error_message.lower(),
        "retrieval_learning_loop": rounds_used > 0 and len(step_obligations) > 0,
        "final_acceptance": status == "FULLY_PROVEN"
        and bool(row.get("promotion_gate_passed", False))
        and bool(gates.get("status_alignment_ok", False))
        and claim_equiv == "equivalent"
        and signature_stable,
    }

    reasons: dict[str, str] = {}
    if not checklist["translation_fidelity"]:
        reasons["translation_fidelity"] = (
            "translation_fidelity_ok=false or schema/noise markers present in statement/error"
        )
    if not checklist["claim_equivalence"]:
        reasons["claim_equivalence"] = (
            f"claim equivalence verdict is '{claim_equiv}' and independent_semantic={independent_semantic}, "
            "expected equivalent with independent semantic evidence"
        )
    if not checklist["theorem_signature_stable"]:
        reasons["theorem_signature_stable"] = signature_reason or "current declaration differs from source signature"
    if not checklist["goal_actionability"]:
        reasons["goal_actionability"] = (
            f"assumption grounding incomplete (grounded={stats['grounded']}/{max(1,stats['total'])}, "
            f"placeholders={stats['placeholders']})"
        )
    if not checklist["proof_synthesis"]:
        reasons["proof_synthesis"] = "lean_proof_closed=false or step_verdict_verified=false"
    if not checklist["context_grounding"]:
        reasons["context_grounding"] = (
            f"dependency trust incomplete or placeholder assumptions remain ({stats['placeholders']})"
        )
    if not checklist["bridge_candidate_quality"]:
        reasons["bridge_candidate_quality"] = (
            f"failure_origin={failure_origin} or unresolved actionable bridge candidate evidence"
        )
    if not checklist["retrieval_learning_loop"]:
        reasons["retrieval_learning_loop"] = (
            f"no retry evidence (rounds_used={rounds_used}, step_obligations={len(step_obligations)})"
        )
    if not checklist["final_acceptance"]:
        reasons["final_acceptance"] = (
            f"status={status or 'UNKNOWN'} or promotion/status-alignment gates failed"
        )

    passed = sum(1 for v in checklist.values() if v)
    return {
        "theorem_name": theorem_name,
        "status": status,
        "proof_method": str(row.get("proof_method", "unknown") or "unknown").lower(),
        "claim_equivalence_verdict": claim_equiv,
        "review_required": review_required,
        "failure_origin": failure_origin,
        "error_message": error_message,
        "assumption_stats": stats,
        "checklist": checklist,
        "failed_checks": [k for k, v in checklist.items() if not v],
        "reasons": reasons,
        "closure_score": round(passed / max(1, len(checklist)), 4),
    }


def run_checklist(*, paper_id: str, ledger_root: Path) -> dict[str, Any]:
    ledger_path = ledger_root / f"{_safe_id(paper_id)}.json"
    entries = _dedupe_by_canonical_theorem(_load_entries(ledger_path))
    lean_text_by_file: dict[str, str] = {}
    for row in entries:
        lf = str(row.get("lean_file", "")).strip()
        if not lf or lf in lean_text_by_file:
            continue
        try:
            lean_text_by_file[lf] = Path(lf).read_text(encoding="utf-8")
        except Exception:
            lean_text_by_file[lf] = ""
    theorem_reports = [_evaluate_row(r, lean_text_by_file=lean_text_by_file) for r in entries]
    gap_counter: Counter[str] = Counter()
    gap_pass: Counter[str] = Counter()
    status_counter: Counter[str] = Counter()
    origin_counter: Counter[str] = Counter()
    for t in theorem_reports:
        status_counter[t["status"]] += 1
        origin_counter[t["failure_origin"]] += 1
        for gap, ok in t["checklist"].items():
            gap_counter[gap] += 1
            if ok:
                gap_pass[gap] += 1

    total = len(theorem_reports)
    fully = int(status_counter.get("FULLY_PROVEN", 0))
    faithful = sum(1 for t in theorem_reports if bool((t.get("checklist", {}) or {}).get("final_acceptance", False)))
    # lean_verified_proven: only theorems whose proof was confirmed by lake build.
    # Excludes auto_closed (schema placeholders) and reconcile_promoted (file-scan).
    _lean_verified_methods = {"lean_verified"}
    lean_verified_proven = sum(
        1 for t in theorem_reports
        if t.get("status") == "FULLY_PROVEN"
        and str(t.get("proof_method", "unknown")).lower() in _lean_verified_methods
    )
    # auto_closed: trivial placeholders closed without lake; excluded from verified rate
    auto_closed_count = sum(
        1 for t in theorem_reports
        if t.get("status") in {"FULLY_PROVEN", "TRANSLATION_LIMITED"}
        and str(t.get("proof_method", "unknown")).lower() in {"auto_closed", "reconcile_promoted"}
    )
    # translation_limited: statements excluded from denominator (no real math content)
    translation_limited_count = int(status_counter.get("TRANSLATION_LIMITED", 0))
    total_provable = total - translation_limited_count
    blockers = Counter(check for t in theorem_reports for check in t["failed_checks"])
    ordered = sorted(theorem_reports, key=lambda x: (x["closure_score"], x["theorem_name"]))
    review_queue = [
        {
            "review_queue_id": str(t.get("theorem_name", "")),
            "theorem_name": str(t.get("theorem_name", "")),
            "status": str(t.get("status", "")),
            "claim_equivalence_verdict": str(t.get("claim_equivalence_verdict", "unclear")),
            "failed_checks": list(t.get("failed_checks", [])),
            "reason": "; ".join(str(v) for v in (t.get("reasons", {}) or {}).values())[:500],
        }
        for t in theorem_reports
        if bool(t.get("review_required", False)) or ("claim_equivalence" in set(t.get("failed_checks", [])))
    ]
    claim_equivalence_queue = build_review_queue(
        ledger_payload={"paper_id": paper_id, "entries": entries},
        paper_id=paper_id,
        source_ledger=str(ledger_path),
    )
    claim_equivalence_review = summarize_review_queue(claim_equivalence_queue)

    return {
        "paper_id": paper_id,
        "ledger_path": str(ledger_path),
        "total_theorems": total,
        "total_provable": total_provable,
        "translation_limited_count": translation_limited_count,
        # operational_fully_proven: all FULLY_PROVEN regardless of proof_method (legacy compat)
        "operational_fully_proven": fully,
        "operational_closure_rate": round((fully / max(1, total)), 4),
        # verified_proven: lake-confirmed only — the authoritative quality signal
        "verified_proven": lean_verified_proven,
        "verified_closure_rate": round((lean_verified_proven / max(1, total_provable)), 4),
        "auto_closed_count": auto_closed_count,
        "faithful_fully_accepted": faithful,
        "faithful_closure_rate": round((faithful / max(1, total)), 4),
        "fully_proven": fully,
        "closure_rate": round((fully / max(1, total)), 4),
        "measurable": total > 0,
        "status_counts": dict(status_counter),
        "failure_origin_counts": dict(origin_counter),
        "gap_pass_rates": {
            gap: {
                "passed": int(gap_pass.get(gap, 0)),
                "total": int(gap_counter.get(gap, 0)),
                "rate": round(float(gap_pass.get(gap, 0)) / max(1, gap_counter.get(gap, 0)), 4),
            }
            for gap in sorted(gap_counter.keys())
        },
        "top_blockers": [{"check": k, "count": int(v)} for k, v in blockers.most_common(10)],
        "review_queue_count": len(review_queue),
        "review_queue": review_queue[:5000],
        "claim_equivalence_review": claim_equivalence_review,
        "theorem_reports": ordered,
    }


def _render_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# Paper Closure Checklist: {payload['paper_id']}")
    lines.append("")
    lines.append(f"- total theorems: {payload['total_theorems']}")
    lines.append(f"- total provable (excl. translation-limited): {payload.get('total_provable', payload['total_theorems'])}")
    lines.append(f"- translation limited (excluded): {payload.get('translation_limited_count', 0)}")
    lines.append(f"- **verified proven (lake-confirmed)**: {payload.get('verified_proven', 0)}")
    lines.append(f"- **verified closure rate**: {payload.get('verified_closure_rate', 0.0):.2%}")
    lines.append(f"- auto closed (not lake-verified): {payload.get('auto_closed_count', 0)}")
    lines.append(f"- operational fully proven (all methods): {payload.get('operational_fully_proven', payload['fully_proven'])}")
    lines.append(f"- operational closure rate: {payload.get('operational_closure_rate', payload['closure_rate']):.2%}")
    lines.append(f"- faithful fully accepted: {payload.get('faithful_fully_accepted', payload['fully_proven'])}")
    lines.append(f"- faithful closure rate: {payload.get('faithful_closure_rate', payload['closure_rate']):.2%}")
    lines.append(f"- measurable: {payload['measurable']}")
    lines.append("")
    lines.append("## Gap Pass Rates")
    for gap, item in payload.get("gap_pass_rates", {}).items():
        lines.append(f"- {gap}: {item['passed']}/{item['total']} ({item['rate']:.2%})")
    lines.append("")
    lines.append("## Top Blockers")
    for b in payload.get("top_blockers", []):
        lines.append(f"- {b['check']}: {b['count']}")
    lines.append("")
    claim_review = payload.get("claim_equivalence_review") if isinstance(payload.get("claim_equivalence_review"), dict) else {}
    if claim_review:
        lines.append("## Claim Equivalence Review")
        lines.append(f"- pending review: {claim_review.get('pending_review_count', 0)}")
        lines.append(f"- high-potential targets: {claim_review.get('high_potential_review_count', 0)}")
        lines.append(f"- would promote if equivalent: {claim_review.get('would_promote_if_equivalent_count', 0)}")
        for target in claim_review.get("top_review_targets", [])[:5]:
            blockers = ", ".join(target.get("remaining_blockers_after_adjudication", [])) or "none"
            lines.append(
                f"- {target.get('theorem_name','')}: score={float(target.get('promotion_potential_score', 0.0)):.1f}, "
                f"tier={target.get('promotion_potential_tier','')}, remaining={blockers}"
            )
        if claim_review.get("remaining_blocker_counts"):
            blockers = ", ".join(f"{k}={v}" for k, v in claim_review.get("remaining_blocker_counts", {}).items())
            lines.append(f"- remaining blocker counts: {blockers}")
        lines.append("")
    lines.append("## Theorem-by-Theorem (strict)")
    lines.append("| theorem | score | status | failed checks |")
    lines.append("|---|---:|---|---|")
    for t in payload.get("theorem_reports", []):
        failed = ", ".join(t.get("failed_checks", []))
        lines.append(
            f"| {t.get('theorem_name','')} | {float(t.get('closure_score',0.0)):.2f} | "
            f"{t.get('status','')} | {failed} |"
        )
    lines.append("")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run strict paper-closure checklist against one paper ledger")
    p.add_argument("--paper-id", required=True, help="arXiv paper ID, e.g. 2304.09598")
    p.add_argument("--ledger-root", default="output/verification_ledgers")
    p.add_argument("--out-json", default="")
    p.add_argument("--out-md", default="")
    p.add_argument("--out-review-queue", default="")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    payload = run_checklist(paper_id=args.paper_id, ledger_root=Path(args.ledger_root))

    out_json = Path(args.out_json) if args.out_json else Path("output/reports/full_paper") / f"{_safe_id(args.paper_id)}_closure_checklist.json"
    out_md = Path(args.out_md) if args.out_md else Path("output/reports/full_paper") / f"{_safe_id(args.paper_id)}_closure_checklist.md"
    out_review = (
        Path(args.out_review_queue)
        if args.out_review_queue
        else Path("output/reports/review_queue") / f"{_safe_id(args.paper_id)}_review_queue.json"
    )

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(_render_markdown(payload), encoding="utf-8")
    out_review.parent.mkdir(parents=True, exist_ok=True)
    out_review.write_text(
        json.dumps(
            {
                "paper_id": payload["paper_id"],
                "generated_at_unix": int(time.time()),
                "review_queue_count": payload.get("review_queue_count", 0),
                "claim_equivalence_review": payload.get("claim_equivalence_review", {}),
                "review_queue": payload.get("review_queue", []),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "paper_id": payload["paper_id"],
                "total_theorems": payload["total_theorems"],
                "total_provable": payload.get("total_provable", payload["total_theorems"]),
                "translation_limited_count": payload.get("translation_limited_count", 0),
                "verified_proven": payload.get("verified_proven", 0),
                "verified_closure_rate": payload.get("verified_closure_rate", 0.0),
                "auto_closed_count": payload.get("auto_closed_count", 0),
                "operational_fully_proven": payload.get("operational_fully_proven", payload["fully_proven"]),
                "operational_closure_rate": payload.get("operational_closure_rate", payload["closure_rate"]),
                "faithful_fully_accepted": payload.get("faithful_fully_accepted", payload["fully_proven"]),
                "faithful_closure_rate": payload.get("faithful_closure_rate", payload["closure_rate"]),
                "fully_proven": payload["fully_proven"],
                "closure_rate": payload["closure_rate"],
                "measurable": payload["measurable"],
                "out_json": str(out_json),
                "out_md": str(out_md),
                "out_review_queue": str(out_review),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
