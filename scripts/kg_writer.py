#!/usr/bin/env python3
"""Build KG layers and promotion manifests from verification ledgers.

Layers:
- trusted: FULLY_PROVEN theorems with promotion_gate_passed=true
- conditional: INTERMEDIARY_PROVEN theorems
- diagnostics: FLAWED/UNRESOLVED theorem artifacts

Outputs (default root: output/kg):
- output/kg/trusted/theorems.jsonl
- output/kg/conditional/theorems.jsonl
- output/kg/diagnostics/theorems.jsonl
- output/kg/manifests/promotion_manifest_<paper>.json
- output/kg/manifests/promotion_manifest_all.json
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class KGSummary:
    papers: int = 0
    entries: int = 0
    trusted: int = 0
    conditional: int = 0
    diagnostics: int = 0
    promotion_ready: int = 0
    files_written: list[str] = field(default_factory=list)


def _iter_ledger_files(ledger_dir: Path, paper: str = "") -> list[Path]:
    if paper:
        safe = paper.replace("/", "_").replace(":", "_")
        p = ledger_dir / f"{safe}.json"
        return [p] if p.exists() else []
    return sorted(ledger_dir.glob("*.json"))


def _load_ledger_doc(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}, []

    if isinstance(raw, list):
        return {}, [r for r in raw if isinstance(r, dict)]

    if isinstance(raw, dict):
        rows = raw.get("entries", [])
        meta = {k: v for k, v in raw.items() if k != "entries"}
        if isinstance(rows, list):
            return meta, [r for r in rows if isinstance(r, dict)]

    return {}, []


def _jsonl_write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _paper_id_from_path(path: Path) -> str:
    return path.stem


def _adversarial_clean(row: dict[str, Any]) -> bool:
    """Return False if the adversarial translation check flagged this theorem."""
    flags = row.get("adversarial_flags", []) or []
    if not isinstance(flags, list):
        flags = [flags]
    fatal = {"trivially_true", "verdict:wrong"}
    return not any(
        f in fatal or (isinstance(f, str) and f.startswith("verdict:wrong"))
        for f in flags
    )


def _classification(row: dict[str, Any]) -> str:
    status = str(row.get("status", "UNRESOLVED"))
    promotion_ok = bool(row.get("promotion_gate_passed", False))

    if status == "FULLY_PROVEN" and promotion_ok and _adversarial_clean(row):
        return "trusted"
    if status == "INTERMEDIARY_PROVEN":
        return "conditional"
    return "diagnostics"


def _row_to_kg_node(row: dict[str, Any], paper_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "paper_id": paper_id,
        "theorem_name": row.get("theorem_name", ""),
        "lean_file": row.get("lean_file", ""),
        "lean_statement": row.get("lean_statement", ""),
        "status": row.get("status", "UNRESOLVED"),
        "step_verdict": row.get("step_verdict", "INCOMPLETE"),
        "failure_origin": row.get("failure_origin", "UNKNOWN"),
        "trust_class": row.get("trust_class", "TRUST_PLACEHOLDER"),
        "trust_reference": row.get("trust_reference", ""),
        "promotion_gate_passed": bool(row.get("promotion_gate_passed", False)),
        "adversarial_flags": row.get("adversarial_flags", []),
        "assumptions": row.get("assumptions", []),
        "first_failing_step": row.get("first_failing_step", -1),
        "proof_mode": row.get("proof_mode", ""),
        "rounds_used": row.get("rounds_used", 0),
        "time_s": row.get("time_s", 0.0),
        "timestamp": row.get("timestamp", ""),
        "schema_version": meta.get("schema_version", "legacy"),
        "pipeline_commit": meta.get("pipeline_commit", "unknown"),
    }


def build_kg(
    *,
    ledger_dir: Path,
    kg_root: Path,
    paper: str = "",
) -> KGSummary:
    summary = KGSummary()

    files = _iter_ledger_files(ledger_dir, paper=paper)
    if not files:
        return summary

    trusted_nodes: list[dict[str, Any]] = []
    conditional_nodes: list[dict[str, Any]] = []
    diagnostic_nodes: list[dict[str, Any]] = []

    per_paper_manifests: dict[str, dict[str, Any]] = {}

    for file in files:
        meta, rows = _load_ledger_doc(file)
        if not rows:
            continue

        summary.papers += 1
        paper_id = _paper_id_from_path(file)
        manifest = {
            "paper_id": paper_id,
            "ledger_file": str(file),
            "schema_version": meta.get("schema_version", "legacy"),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "pipeline_commit": meta.get("pipeline_commit", "unknown"),
            "counts": {
                "entries": 0,
                "trusted": 0,
                "conditional": 0,
                "diagnostics": 0,
                "promotion_ready": 0,
            },
            "promotion_ready_theorems": [],
        }

        for row in rows:
            summary.entries += 1
            manifest["counts"]["entries"] += 1
            layer = _classification(row)
            node = _row_to_kg_node(row, paper_id=paper_id, meta=meta)

            if layer == "trusted":
                trusted_nodes.append(node)
                summary.trusted += 1
                summary.promotion_ready += 1
                manifest["counts"]["trusted"] += 1
                manifest["counts"]["promotion_ready"] += 1
                manifest["promotion_ready_theorems"].append(node["theorem_name"])
            elif layer == "conditional":
                conditional_nodes.append(node)
                summary.conditional += 1
                manifest["counts"]["conditional"] += 1
            else:
                diagnostic_nodes.append(node)
                summary.diagnostics += 1
                manifest["counts"]["diagnostics"] += 1

        per_paper_manifests[paper_id] = manifest

    trusted_path = kg_root / "trusted" / "theorems.jsonl"
    conditional_path = kg_root / "conditional" / "theorems.jsonl"
    diagnostics_path = kg_root / "diagnostics" / "theorems.jsonl"

    _jsonl_write(trusted_path, trusted_nodes)
    _jsonl_write(conditional_path, conditional_nodes)
    _jsonl_write(diagnostics_path, diagnostic_nodes)

    summary.files_written.extend([str(trusted_path), str(conditional_path), str(diagnostics_path)])

    manifest_dir = kg_root / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)

    for paper_id, manifest in per_paper_manifests.items():
        out = manifest_dir / f"promotion_manifest_{paper_id}.json"
        out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        summary.files_written.append(str(out))

    all_manifest = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ledger_dir": str(ledger_dir),
        "kg_root": str(kg_root),
        "papers": summary.papers,
        "entries": summary.entries,
        "trusted": summary.trusted,
        "conditional": summary.conditional,
        "diagnostics": summary.diagnostics,
        "promotion_ready": summary.promotion_ready,
        "paper_manifests": sorted(per_paper_manifests.keys()),
    }
    all_manifest_path = manifest_dir / "promotion_manifest_all.json"
    all_manifest_path.write_text(json.dumps(all_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    summary.files_written.append(str(all_manifest_path))

    return summary


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build KG layers and promotion manifests from verification ledgers")
    p.add_argument("--ledger-dir", default="output/verification_ledgers", help="Ledger directory")
    p.add_argument("--kg-root", default="output/kg", help="Output KG root")
    p.add_argument("--paper", default="", help="Optional paper id (e.g. 2304.09598)")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    ledger_dir = Path(args.ledger_dir)
    kg_root = Path(args.kg_root)

    if not ledger_dir.exists():
        print(f"[fail] ledger directory not found: {ledger_dir}")
        return 1

    summary = build_kg(ledger_dir=ledger_dir, kg_root=kg_root, paper=args.paper)
    if summary.papers == 0 and summary.entries == 0:
        print("[fail] no ledgers matched")
        return 1

    print("[ok] KG build complete")
    print(f"[info] papers={summary.papers} entries={summary.entries}")
    print(
        "[info] layers="
        f"trusted={summary.trusted} conditional={summary.conditional} diagnostics={summary.diagnostics}"
    )
    print(f"[info] promotion_ready={summary.promotion_ready}")
    for path in summary.files_written:
        print(f"[info] wrote {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
