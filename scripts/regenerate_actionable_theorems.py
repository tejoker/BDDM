#!/usr/bin/env python3
"""Regenerate a strict actionable theorem set from a verification ledger."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from equivalence_repair import _build_client_and_model, attempt_equivalence_repair


_NOISE_RE = re.compile(
    r"(literal_schema_translation|schema_(assumption|claim)|\{ \{ll|Missing theorem statement|^\s*--)",
    re.IGNORECASE | re.MULTILINE,
)


def _safe_id(paper_id: str) -> str:
    return paper_id.replace("/", "_").replace(":", "_")


def _load_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
        return [r for r in raw["entries"] if isinstance(r, dict)]
    return []


def _assumption_placeholder_count(row: dict[str, Any]) -> int:
    assumptions = row.get("assumptions", [])
    if not isinstance(assumptions, list):
        return 0
    c = 0
    for a in assumptions:
        if not isinstance(a, dict):
            continue
        trust = str(a.get("trust_class", "")).upper()
        grounding = str(a.get("grounding", "")).upper()
        if trust == "TRUST_PLACEHOLDER" or grounding in {"UNGROUNDED", "UNKNOWN", ""}:
            c += 1
    return c


def _to_sorry_statement(stmt: str) -> str:
    s = (stmt or "").strip()
    if not s:
        return ""
    if not re.match(r"^\s*(theorem|lemma)\b", s):
        return ""
    s = re.sub(r":=\s*by\b.*$", "", s, flags=re.DOTALL).rstrip()
    s = re.sub(r":=\s*sorry\s*$", "", s, flags=re.DOTALL).rstrip()
    s = re.sub(r":=\s*$", "", s).rstrip()
    return f"{s} := by\n  sorry"


def _is_nontrivial_theorem_statement(stmt: str) -> bool:
    s = " ".join((stmt or "").split())
    if not s:
        return False
    low = s.lower()
    if re.search(r"^\s*(theorem|lemma)\s+schema_", low):
        return False
    if re.search(r"^\s*(theorem|lemma)\s+literal_schema_", low):
        return False
    if re.search(r":\s*true\s*(?::=|$)", low):
        return False
    if re.search(r":\s*\(?\s*0\s*:\s*ℕ\s*\)?\s*=\s*0\s*(?::=|$)", s):
        return False
    if re.search(r"→\s*\(?\s*0\s*:\s*ℕ\s*\)?\s*=\s*0\s*(?::=|$)", s):
        return False
    if re.search(r":\s*p_c\d+\s*(?::=|$)", s):
        return False
    if "schema_translation" in low or "schema_fallback" in low:
        return False
    if "literal_schema_translation" in low:
        return False
    if not re.match(r"^\s*(theorem|lemma)\b", s):
        return False
    # Require theorem-like mathematical signal.
    if not any(tok in s for tok in ("→", "->", "↔", "=", "≤", "≥", "<", ">", "∃", "∀", "∧")):
        return False
    return True


def _looks_obviously_non_actionable(stmt: str) -> bool:
    """Reject signatures that are almost surely translation noise.

    Current hard blocker pattern:
      theorem ... (a : T) ... : a = <ground expression not mentioning a>
    This is rarely a valid theorem without extra hypotheses and repeatedly burns proof budget.
    """
    s = " ".join((stmt or "").split())
    if not s:
        return True
    # Find terminal proposition shape from the declaration head.
    m = re.search(r"\)\s*:\s*([A-Za-z_][A-Za-z0-9_']*)\s*=\s*(.+?)\s*(?::=|$)", s)
    if not m:
        return False
    lhs = m.group(1).strip()
    rhs = m.group(2).strip()
    if not lhs or not rhs:
        return False
    # If lhs variable never appears on rhs and no obvious hypothesis arrow, reject.
    if re.search(rf"\b{re.escape(lhs)}\b", rhs):
        return False
    if ("→" in rhs) or ("->" in rhs):
        return False
    return True


def _looks_underconstrained_implication(stmt: str) -> bool:
    """Heuristic: reject implication goals introducing unconstrained target predicates.

    Typical failure pattern:
      (H_unique : ... ABV_packets ...) -> (... ABV_packets ... -> A_packets ...)
    where `A_packets` never appears in assumptions, making proof impossible.
    """
    s = " ".join((stmt or "").split())
    if not s:
        return False
    m = re.search(r"\)\s*:\s*(.+?)\s*(?::=|$)", s)
    if not m:
        m = re.search(r"^\s*(theorem|lemma)\s+[A-Za-z_][A-Za-z0-9_'.]*\s*:\s*(.+?)\s*(?::=|$)", s)
    if not m:
        return False
    prop = m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(1)
    if ("→" not in prop) and ("->" not in prop):
        return False
    # Pattern: P x -> Q x where Q is never constrained earlier in the proposition.
    pair = re.search(r"\b([A-Za-z_][A-Za-z0-9_']*)\s+([a-zA-Z][A-Za-z0-9_']*)\s*→\s*([A-Za-z_][A-Za-z0-9_']*)\s+\2\b", prop)
    if pair:
        pred_p, var, pred_q = pair.group(1), pair.group(2), pair.group(3)
        if pred_p != pred_q and pred_p.endswith("_packets") and pred_q.endswith("_packets"):
            prefix = prop[: pair.start()]
            if not re.search(rf"\b{re.escape(pred_q)}\b", prefix):
                return True
    # Split at the last implication: premise ... -> consequent
    if "→" in prop:
        premise, consequent = prop.rsplit("→", 1)
    else:
        premise, consequent = prop.rsplit("->", 1)
    premise = premise.strip()
    consequent = consequent.strip()
    if not premise or not consequent:
        return False
    # Predicate-like identifiers in consequent that do not occur in premise are suspicious.
    names = set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_']*)\b", consequent))
    for n in sorted(names):
        if n in {"Prop", "Type", "True", "False", "Nat", "Int", "Finset", "Multiset"}:
            continue
        if n.endswith("_packets") and not re.search(rf"\b{re.escape(n)}\b", premise):
            return True
    return False


def regenerate(
    *,
    paper_id: str,
    ledger_root: Path,
    out_path: Path,
    project_root: Path,
    equivalence_repair: bool = False,
    apply_repairs_to_ledger: bool = False,
    repair_model: str = "",
    repair_max_items: int = 25,
) -> dict[str, Any]:
    ledger_path = ledger_root / f"{_safe_id(paper_id)}.json"
    rows = _load_entries(ledger_path)
    kept: list[str] = []
    skipped: list[dict[str, str]] = []
    repaired_rows: list[dict[str, Any]] = []
    tier_counts = {"tier_a_direct": 0, "tier_b_salvaged": 0, "tier_c_rejected": 0}
    repair_attempts = 0
    repair_successes = 0
    client = None
    model = ""
    if equivalence_repair:
        client, model = _build_client_and_model(model_override=repair_model)
    seen: set[str] = set()
    seen_cleaned: set[str] = set()
    for r in rows:
        theorem_name = str(r.get("theorem_name", "")).strip()
        stmt = str(r.get("lean_statement", "") or "")
        gates = r.get("validation_gates", {})
        if not isinstance(gates, dict):
            gates = {}
        if not theorem_name:
            skipped.append({"theorem_name": "", "reason": "missing_name"})
            continue
        if theorem_name in seen:
            skipped.append({"theorem_name": theorem_name, "reason": "duplicate"})
            tier_counts["tier_c_rejected"] += 1
            continue
        if _NOISE_RE.search(stmt):
            skipped.append({"theorem_name": theorem_name, "reason": "schema_noise"})
            tier_counts["tier_c_rejected"] += 1
            continue
        translation_ok = bool(gates.get("translation_fidelity_ok", False))
        equiv_verdict = str(r.get("claim_equivalence_verdict", "") or "").strip().lower()
        claim_ok = bool(gates.get("claim_equivalent", False)) and equiv_verdict == "equivalent"
        nontrivial_ok = _is_nontrivial_theorem_statement(stmt)
        # Early hard-drop for known unprovable underconstrained implication shapes.
        # We reject these before any salvage/prove work to keep actionable queues clean.
        if _looks_underconstrained_implication(stmt):
            skipped.append({"theorem_name": theorem_name, "reason": "underconstrained_implication_signature"})
            tier_counts["tier_c_rejected"] += 1
            continue
        needs_salvage = (not nontrivial_ok) or (not claim_ok)
        if (
            needs_salvage
            and equivalence_repair
            and translation_ok
            and client is not None
            and repair_attempts < max(0, int(repair_max_items))
        ):
            repair_attempts += 1
            outcome = attempt_equivalence_repair(
                row=r,
                project_root=project_root,
                client=client,
                model=model,
                retrieval_index_path=os.environ.get("DESOL_RETRIEVAL_INDEX", "data/mathlib_embeddings"),
            )
            if outcome.repaired and outcome.repaired_signature:
                repair_successes += 1
                stmt = outcome.repaired_signature
                nontrivial_ok = _is_nontrivial_theorem_statement(stmt)
                claim_ok = True
                gates["claim_equivalent"] = True
                gates["translation_fidelity_ok"] = True
                r["lean_statement"] = stmt
                r["claim_equivalence_verdict"] = "equivalent"
                notes = r.get("claim_equivalence_notes", [])
                if not isinstance(notes, list):
                    notes = []
                notes = [str(x) for x in notes]
                notes.append("equivalent_after_repair")
                notes.extend(outcome.notes or [])
                r["claim_equivalence_notes"] = list(dict.fromkeys(notes))
                r["validation_gates"] = gates
                repaired_rows.append(
                    {
                        "theorem_name": theorem_name,
                        "equivalence_confidence": outcome.confidence,
                        "notes": outcome.notes or [],
                    }
                )

        if not nontrivial_ok:
            skipped.append({"theorem_name": theorem_name, "reason": "nontrivial_gate"})
            tier_counts["tier_c_rejected"] += 1
            continue
        if _looks_obviously_non_actionable(stmt):
            skipped.append({"theorem_name": theorem_name, "reason": "obviously_non_actionable_signature"})
            tier_counts["tier_c_rejected"] += 1
            continue
        if _looks_underconstrained_implication(stmt):
            skipped.append({"theorem_name": theorem_name, "reason": "underconstrained_implication_signature"})
            tier_counts["tier_c_rejected"] += 1
            continue
        if not translation_ok:
            skipped.append({"theorem_name": theorem_name, "reason": "translation_fidelity_gate"})
            tier_counts["tier_c_rejected"] += 1
            continue
        if not claim_ok:
            skipped.append({"theorem_name": theorem_name, "reason": "claim_equivalence_gate"})
            tier_counts["tier_c_rejected"] += 1
            continue
        if not bool(gates.get("assumptions_grounded", False)):
            skipped.append({"theorem_name": theorem_name, "reason": "assumptions_grounded_gate"})
            tier_counts["tier_c_rejected"] += 1
            continue
        if not bool(gates.get("dependency_trust_complete", False)):
            skipped.append({"theorem_name": theorem_name, "reason": "dependency_trust_gate"})
            tier_counts["tier_c_rejected"] += 1
            continue
        if _assumption_placeholder_count(r) > 0:
            skipped.append({"theorem_name": theorem_name, "reason": "placeholder_assumptions"})
            tier_counts["tier_c_rejected"] += 1
            continue
        cleaned = _to_sorry_statement(stmt)
        if not cleaned:
            skipped.append({"theorem_name": theorem_name, "reason": "non_actionable_statement"})
            tier_counts["tier_c_rejected"] += 1
            continue
        if cleaned in seen_cleaned:
            skipped.append({"theorem_name": theorem_name, "reason": "duplicate_cleaned_statement"})
            tier_counts["tier_c_rejected"] += 1
            continue
        seen.add(theorem_name)
        seen_cleaned.add(cleaned)
        kept.append(cleaned)
        if any(x.get("theorem_name") == theorem_name for x in repaired_rows):
            tier_counts["tier_b_salvaged"] += 1
        else:
            tier_counts["tier_a_direct"] += 1

    if apply_repairs_to_ledger and repaired_rows:
        try:
            payload = {"entries": rows} if isinstance(json.loads(ledger_path.read_text(encoding="utf-8")), dict) else rows
        except Exception:
            payload = {"entries": rows}
        ledger_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = [
        "import Mathlib",
        "",
        "namespace ArxivPaperActionable",
        "",
    ]
    for st in kept:
        body.append(st)
        body.append("")
    body.append("end ArxivPaperActionable")
    out_path.write_text("\n".join(body), encoding="utf-8")
    return {
        "paper_id": paper_id,
        "ledger_path": str(ledger_path),
        "out_lean": str(out_path),
        "kept_count": len(kept),
        "skipped_count": len(skipped),
        "tier_counts": tier_counts,
        "repair_attempts": repair_attempts,
        "repair_successes": repair_successes,
        "repaired_rows": repaired_rows,
        "kept_theorems": kept,
        "skipped": skipped,
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Regenerate actionable theorem-only Lean file from ledger")
    p.add_argument("--paper-id", required=True)
    p.add_argument("--ledger-root", default="output/verification_ledgers")
    p.add_argument("--out", default="")
    p.add_argument("--project-root", default=".")
    p.add_argument("--equivalence-repair", action="store_true")
    p.add_argument("--apply-repairs-to-ledger", action="store_true")
    p.add_argument("--repair-model", default="")
    p.add_argument("--repair-max-items", type=int, default=25)
    return p


def main() -> int:
    load_dotenv()
    args = _build_parser().parse_args()
    out = Path(args.out) if args.out else Path("output") / f"{_safe_id(args.paper_id)}_actionable.lean"
    payload = regenerate(
        paper_id=args.paper_id,
        ledger_root=Path(args.ledger_root),
        out_path=out,
        project_root=Path(args.project_root).resolve(),
        equivalence_repair=bool(args.equivalence_repair),
        apply_repairs_to_ledger=bool(args.apply_repairs_to_ledger),
        repair_model=str(args.repair_model or ""),
        repair_max_items=max(0, int(args.repair_max_items)),
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
