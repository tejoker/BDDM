#!/usr/bin/env python3
"""Score paper formalization readiness from verification ledger quality signals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


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


def _assumption_placeholder_rate(row: dict[str, Any]) -> float:
    assumptions = row.get("assumptions", [])
    if not isinstance(assumptions, list) or not assumptions:
        return 0.0
    bad = 0
    total = 0
    for a in assumptions:
        if not isinstance(a, dict):
            continue
        total += 1
        trust = str(a.get("trust_class", "")).upper()
        grounding = str(a.get("grounding", "")).upper()
        if trust == "TRUST_PLACEHOLDER" or grounding in {"UNGROUNDED", "UNKNOWN", ""}:
            bad += 1
    return float(bad) / max(1, total)


def score_readiness(*, paper_id: str, ledger_root: Path) -> dict[str, Any]:
    path = ledger_root / f"{_safe_id(paper_id)}.json"
    rows = _load_entries(path)
    total = len(rows)
    if total == 0:
        return {
            "paper_id": paper_id,
            "ledger_path": str(path),
            "total_theorems": 0,
            "readiness_score": 0.0,
            "readiness_class": "C",
            "reasons": ["no_ledger_entries"],
        }

    fidelity_ok = 0
    grounded_ok = 0
    trust_ok = 0
    non_noise = 0
    avg_placeholder = 0.0
    for r in rows:
        gates = r.get("validation_gates", {})
        if not isinstance(gates, dict):
            gates = {}
        stmt = str(r.get("lean_statement", "") or "")
        if bool(gates.get("translation_fidelity_ok", False)):
            fidelity_ok += 1
        if bool(gates.get("assumptions_grounded", False)):
            grounded_ok += 1
        if bool(gates.get("dependency_trust_complete", False)):
            trust_ok += 1
        if "literal_schema_translation" not in stmt and "schema_assumption" not in stmt:
            non_noise += 1
        avg_placeholder += _assumption_placeholder_rate(r)
    avg_placeholder /= max(1, total)

    fidelity_rate = fidelity_ok / total
    grounded_rate = grounded_ok / total
    trust_rate = trust_ok / total
    non_noise_rate = non_noise / total
    placeholder_quality = 1.0 - min(1.0, avg_placeholder)

    score = (
        0.30 * fidelity_rate
        + 0.25 * grounded_rate
        + 0.20 * trust_rate
        + 0.15 * non_noise_rate
        + 0.10 * placeholder_quality
    )
    if score >= 0.70:
        cls = "A"
    elif score >= 0.45:
        cls = "B"
    else:
        cls = "C"

    return {
        "paper_id": paper_id,
        "ledger_path": str(path),
        "total_theorems": total,
        "readiness_score": round(score, 4),
        "readiness_class": cls,
        "metrics": {
            "translation_fidelity_rate": round(fidelity_rate, 4),
            "assumptions_grounded_rate": round(grounded_rate, 4),
            "dependency_trust_rate": round(trust_rate, 4),
            "non_schema_noise_rate": round(non_noise_rate, 4),
            "placeholder_quality": round(placeholder_quality, 4),
        },
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Compute paper readiness score for reliable formalization lane")
    p.add_argument("--paper-id", required=True)
    p.add_argument("--ledger-root", default="output/verification_ledgers")
    p.add_argument("--out", default="")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    payload = score_readiness(paper_id=args.paper_id, ledger_root=Path(args.ledger_root))
    txt = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(txt, encoding="utf-8")
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

