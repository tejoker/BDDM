#!/usr/bin/env python3
"""Assert quality-gate thresholds from report JSON artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def main() -> int:
    p = argparse.ArgumentParser(description="Assert quality gates")
    p.add_argument("--translation-report", required=True)
    p.add_argument("--linkage-report", required=True)
    p.add_argument("--min-translation-f1", type=float, default=0.8)
    p.add_argument("--min-linkage-f1", type=float, default=0.5)
    p.add_argument("--weekly-report", default="", help="Optional weekly_report_*.json for release gate checks")
    p.add_argument("--require-weekly-release-gate", action="store_true")
    p.add_argument("--kg-db", default="", help="Optional path to kg_index.db for dual-metric reporting")
    args = p.parse_args()

    tr = _load(Path(args.translation_report))
    lr = _load(Path(args.linkage_report))
    t_f1 = float(tr.get("avg_fidelity_f1", 0.0))
    l_f1 = float(lr.get("f1", 0.0))
    ok = True
    if t_f1 < float(args.min_translation_f1):
        print(f"[gate_fail] translation avg_fidelity_f1={t_f1:.4f} < {args.min_translation_f1:.4f}")
        ok = False
    if l_f1 < float(args.min_linkage_f1):
        print(f"[gate_fail] linkage f1={l_f1:.4f} < {args.min_linkage_f1:.4f}")
        ok = False
    if args.require_weekly_release_gate:
        wr = _load(Path(args.weekly_report)) if args.weekly_report else {}
        rg = wr.get("release_gate", {}) if isinstance(wr, dict) else {}
        weekly_ok = bool(rg.get("go_for_controlled_release", False))
        if not weekly_ok:
            print(
                "[gate_fail] weekly release gate not passed "
                f"(go_for_controlled_release={rg.get('go_for_controlled_release', False)})"
            )
            ok = False

    # Dual-metric reporting: always print statements_formalized vs proofs_closed.
    # This is informational — it never fails the gate, but makes the distinction
    # visible in every CI run so "proven" is never silently conflated with "formalized".
    if args.kg_db:
        kg_db = Path(args.kg_db)
        if kg_db.exists():
            try:
                import sqlite3 as _sq
                from collections import defaultdict as _dd
                _con = _sq.connect(str(kg_db))
                _sc: dict[str, int] = _dd(int)
                _papers: set[str] = set()
                for _stat, _pid, _cnt in _con.execute(
                    "SELECT status, paper_id, COUNT(*) FROM kg_nodes GROUP BY status, paper_id"
                ):
                    _papers.add(_pid)
                    _sc[_stat] += _cnt
                _stmts = sum(_sc[s] for s in ("FULLY_PROVEN", "AXIOM_BACKED", "INTERMEDIARY_PROVEN"))
                _closed = _sc.get("FULLY_PROVEN", 0)
                _backed = _sc.get("AXIOM_BACKED", 0)
                _rate = round(_closed / max(1, _stmts), 4)
                print(
                    f"[dual_metric] papers={len(_papers)} "
                    f"statements_formalized={_stmts} "
                    f"proofs_closed={_closed} "
                    f"axiom_backed={_backed} "
                    f"proof_closure_rate={_rate}"
                )
            except Exception as _e:
                print(f"[dual_metric] error reading KG: {_e}")

    if ok:
        print(
            f"[gate_ok] translation_f1={t_f1:.4f} linkage_f1={l_f1:.4f} "
            f"(thresholds: {args.min_translation_f1:.4f}, {args.min_linkage_f1:.4f})"
        )
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
