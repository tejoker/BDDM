#!/usr/bin/env python3
"""Post-sweep helper: mirror ephemeral ledgers to reproducibility/, then run
the FP integrity audit (with --include-ip-ab) in --write mode.

Reports FP/AB/IP/UR counts BEFORE and AFTER the audit, plus the per-paper
delta vs the supplied baseline file.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CANONICAL = [
    "2012.09271",
    "2304.09598",
    "2401.04567",
    "2604.21314",
    "2604.21583",
    "2604.21616",
    "2604.21821",
    "2604.21884",
]


def _ledger_counts(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    entries = data if isinstance(data, list) else data.get("entries", [])
    counts: dict[str, int] = {}
    for e in entries:
        s = e.get("status") or "UNKNOWN"
        counts[s] = counts.get(s, 0) + 1
    return counts


def _aggregate(counts_by_paper: dict[str, dict[str, int]]) -> dict[str, int]:
    agg: dict[str, int] = {}
    for c in counts_by_paper.values():
        for k, v in c.items():
            agg[k] = agg.get(k, 0) + v
    return agg


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mirror-only", action="store_true")
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--no-write", action="store_true", help="Dry-run the audit")
    args = parser.parse_args()

    ledger_dir = PROJECT_ROOT / "output" / "verification_ledgers"
    repro_dir = PROJECT_ROOT / "reproducibility" / "full_paper_reports"

    # 1. Snapshot canonical (pre-mirror) FP/AB/IP/UR.
    pre_canonical_counts = {pid: _ledger_counts(repro_dir / pid / "verification_ledger.json") for pid in CANONICAL}
    pre_ephemeral_counts = {pid: _ledger_counts(ledger_dir / f"{pid}.json") for pid in CANONICAL}

    print("=== Pre-mirror ===")
    print("canonical:", _aggregate(pre_canonical_counts))
    print("ephemeral:", _aggregate(pre_ephemeral_counts))

    if not args.audit_only:
        # 2. Mirror ephemeral → canonical for each paper.
        for pid in CANONICAL:
            src = ledger_dir / f"{pid}.json"
            dst_dir = repro_dir / pid
            dst = dst_dir / "verification_ledger.json"
            if not src.exists():
                print(f"[mirror] skip {pid}: no ephemeral ledger")
                continue
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"[mirror] {pid}: {src} -> {dst}")

    post_mirror_canonical = {pid: _ledger_counts(repro_dir / pid / "verification_ledger.json") for pid in CANONICAL}
    print("=== Post-mirror canonical ===")
    print("canonical:", _aggregate(post_mirror_canonical))

    if args.mirror_only:
        return 0

    # 3. Run the integrity audit (--include-ip-ab; --write unless --no-write).
    audit_cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "audit_fully_proven_integrity.py"),
        "--include-ip-ab",
        "--papers", *CANONICAL,
    ]
    if not args.no_write:
        audit_cmd.append("--write")
    print(f"\n=== Running audit: {' '.join(audit_cmd[-len(CANONICAL)-2:])} ===")
    proc = subprocess.run(audit_cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True)
    print(proc.stdout[-4000:])
    if proc.returncode != 0:
        print("[audit] returncode!=0, stderr:", proc.stderr[-1000:])

    post_audit_canonical = {pid: _ledger_counts(repro_dir / pid / "verification_ledger.json") for pid in CANONICAL}
    print("\n=== Post-audit canonical ===")
    for pid in CANONICAL:
        pre = pre_canonical_counts.get(pid, {})
        post = post_audit_canonical.get(pid, {})
        fp_delta = post.get("FULLY_PROVEN", 0) - pre.get("FULLY_PROVEN", 0)
        ab_delta = post.get("AXIOM_BACKED", 0) - pre.get("AXIOM_BACKED", 0)
        ip_delta = post.get("INTERMEDIARY_PROVEN", 0) - pre.get("INTERMEDIARY_PROVEN", 0)
        ur_delta = post.get("UNRESOLVED", 0) - pre.get("UNRESOLVED", 0)
        print(f"  {pid}: FP {pre.get('FULLY_PROVEN',0)}->{post.get('FULLY_PROVEN',0)} (Δ{fp_delta:+d}) "
              f"AB {pre.get('AXIOM_BACKED',0)}->{post.get('AXIOM_BACKED',0)} (Δ{ab_delta:+d}) "
              f"IP {pre.get('INTERMEDIARY_PROVEN',0)}->{post.get('INTERMEDIARY_PROVEN',0)} (Δ{ip_delta:+d}) "
              f"UR {pre.get('UNRESOLVED',0)}->{post.get('UNRESOLVED',0)} (Δ{ur_delta:+d})")
    print("\n=== Aggregate ===")
    print("pre  :", _aggregate(pre_canonical_counts))
    print("post :", _aggregate(post_audit_canonical))

    return 0


if __name__ == "__main__":
    sys.exit(main())
