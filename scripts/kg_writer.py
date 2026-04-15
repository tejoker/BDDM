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

SQLite ``kg_edges`` also stores optional cross-reference rows with
``edge_type='cites_arxiv'``: ``src_theorem`` is ``{paper_id}|{theorem_name}``,
``dst_theorem`` is a normalized arXiv id (``YYYY.NNNNN`` or legacy ``cat/NNNNNNN``)
parsed from ledger ``provenance.cited_refs``.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_NEW_ARXIV_ID = re.compile(r"(?:arxiv:)?(\d{4}\.\d{4,5})(?:v\d+)?\b", re.IGNORECASE)
_OLD_ARXIV_ID = re.compile(r"\b([A-Za-z.-]+/\d{7})(?:v\d+)?\b")


def _extract_cited_arxiv_ids(row: dict[str, Any]) -> list[str]:
    """Pull arXiv-style ids from ``provenance.cited_refs`` (best-effort)."""
    prov = row.get("provenance")
    if not isinstance(prov, dict):
        return []
    raw = prov.get("cited_refs")
    tokens: list[str] = []
    if isinstance(raw, list):
        tokens.extend(str(x) for x in raw if x)
    elif isinstance(raw, str) and raw.strip():
        tokens.append(raw)
    out: list[str] = []
    seen: set[str] = set()
    for t in tokens:
        for m in _NEW_ARXIV_ID.finditer(t):
            aid = m.group(1)
            if aid not in seen:
                seen.add(aid)
                out.append(aid)
        for m in _OLD_ARXIV_ID.finditer(t):
            aid = m.group(1)
            if aid not in seen:
                seen.add(aid)
                out.append(aid)
    return out


@dataclass
class KGSummary:
    papers: int = 0
    entries: int = 0
    trusted: int = 0
    conditional: int = 0
    diagnostics: int = 0
    promotion_ready: int = 0
    citation_edges: int = 0
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


def _sqlite_write(db_path: Path, nodes: list[dict[str, Any]], layer: str) -> None:
    """Upsert KG nodes into SQLite index for deduplication and edge queries.

    Primary key is (paper_id, theorem_name).  Nodes in the same layer are
    merged on conflict (last-write wins).  Cross-layer edges (trusted node
    that transitively depends on a conditional node) are stored in the
    ``kg_edges`` table.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), timeout=30.0)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS kg_nodes (
            paper_id TEXT NOT NULL,
            theorem_name TEXT NOT NULL,
            layer TEXT NOT NULL,
            status TEXT,
            promotion_gate_passed INTEGER,
            transitive_ungrounded INTEGER,
            ungrounded_assumption_count INTEGER,
            proof_mode TEXT,
            rounds_used INTEGER,
            time_s REAL,
            timestamp TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (paper_id, theorem_name)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS kg_edges (
            src_theorem TEXT NOT NULL,
            dst_theorem TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            PRIMARY KEY (src_theorem, dst_theorem, edge_type)
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_kg_nodes_layer ON kg_nodes(layer)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_kg_nodes_status ON kg_nodes(status)"
    )
    with con:
        for node in nodes:
            paper_id = node.get("paper_id", "")
            theorem_name = node.get("theorem_name", "")
            if not paper_id or not theorem_name:
                continue
            con.execute(
                """
                INSERT INTO kg_nodes(
                    paper_id, theorem_name, layer, status,
                    promotion_gate_passed, transitive_ungrounded,
                    ungrounded_assumption_count, proof_mode, rounds_used,
                    time_s, timestamp, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(paper_id, theorem_name) DO UPDATE SET
                    layer=excluded.layer,
                    status=excluded.status,
                    promotion_gate_passed=excluded.promotion_gate_passed,
                    transitive_ungrounded=excluded.transitive_ungrounded,
                    ungrounded_assumption_count=excluded.ungrounded_assumption_count,
                    proof_mode=excluded.proof_mode,
                    rounds_used=excluded.rounds_used,
                    time_s=excluded.time_s,
                    timestamp=excluded.timestamp,
                    payload_json=excluded.payload_json
                """,
                (
                    paper_id,
                    theorem_name,
                    layer,
                    node.get("status", ""),
                    int(bool(node.get("promotion_gate_passed", False))),
                    int(bool(node.get("transitive_ungrounded", False))),
                    int(node.get("ungrounded_assumption_count", 0)),
                    node.get("proof_mode", ""),
                    int(node.get("rounds_used", 0)),
                    float(node.get("time_s", 0.0)),
                    node.get("timestamp", ""),
                    json.dumps(node, ensure_ascii=False),
                ),
            )
            # Persist transitive dependency edges.
            for dep in node.get("transitive_ungrounded_via", []):
                if dep:
                    con.execute(
                        """
                        INSERT OR IGNORE INTO kg_edges(src_theorem, dst_theorem, edge_type)
                        VALUES (?, ?, 'transitive_dep')
                        """,
                        (theorem_name, dep),
                    )
    con.close()


def _sqlite_merge_citation_edges(db_path: Path, nodes: list[dict[str, Any]]) -> int:
    """Replace ``cites_arxiv`` rows derived from node ``cited_arxiv_ids``."""
    if not db_path.exists():
        return 0
    con = sqlite3.connect(str(db_path), timeout=30.0)
    inserted = 0
    with con:
        con.execute("DELETE FROM kg_edges WHERE edge_type = 'cites_arxiv'")
        for node in nodes:
            paper_id = str(node.get("paper_id", "")).strip()
            thm = str(node.get("theorem_name", "")).strip()
            if not paper_id or not thm:
                continue
            src = f"{paper_id}|{thm}"
            cited = node.get("cited_arxiv_ids") or []
            if not isinstance(cited, list):
                continue
            for target in cited:
                tid = str(target).strip()
                if not tid or tid == paper_id:
                    continue
                cur = con.execute(
                    """
                    INSERT OR IGNORE INTO kg_edges(src_theorem, dst_theorem, edge_type)
                    VALUES (?, ?, 'cites_arxiv')
                    """,
                    (src, tid,),
                )
                inserted += int(cur.rowcount or 0)
    con.close()
    return inserted


def query_kg(
    db_path: Path,
    *,
    layer: str | None = None,
    paper_id: str | None = None,
    status: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Query the KG SQLite index.

    Args:
        db_path: Path to the KG SQLite database (``output/kg/kg_index.db``).
        layer: Optional layer filter — ``"trusted"``, ``"conditional"``, ``"diagnostics"``.
        paper_id: Optional paper filter.
        status: Optional status filter (e.g. ``"FULLY_PROVEN"``).
        limit: Max rows returned (default 500).

    Returns:
        List of node dicts (full payload).
    """
    if not db_path.exists():
        return []
    con = sqlite3.connect(str(db_path), timeout=10.0)
    con.row_factory = sqlite3.Row
    clauses: list[str] = []
    params: list[Any] = []
    if layer:
        clauses.append("layer = ?")
        params.append(layer)
    if paper_id:
        clauses.append("paper_id = ?")
        params.append(paper_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = con.execute(
        f"SELECT payload_json FROM kg_nodes {where} LIMIT ?",
        params + [limit],
    ).fetchall()
    con.close()
    results: list[dict[str, Any]] = []
    for row in rows:
        try:
            results.append(json.loads(row[0]))
        except Exception:
            pass
    return results


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


def _count_ungrounded(assumptions: list[Any]) -> int:
    count = 0
    for a in assumptions:
        if not isinstance(a, dict):
            continue
        g = str(a.get("grounding", "")).upper()
        if g in {"UNGROUNDED", "UNKNOWN", ""}:
            count += 1
    return count


def _propagate_ungroundedness(
    nodes: list[dict[str, Any]],
    trusted_index: dict[str, dict[str, Any]],
) -> None:
    """Annotate conditional nodes whose trust-sources are themselves conditional.

    When a FULLY_PROVEN theorem uses a GROUNDED_INTERNAL_KG assumption, the
    grounding source is another KG entry.  If that source is only
    INTERMEDIARY_PROVEN (conditional), the chain is not fully grounded.
    We mark such nodes with ``transitive_ungrounded=True`` so consumers can
    distinguish solid FULLY_PROVEN from ones that depend on conditional results.
    """
    conditional_names: set[str] = {
        n["theorem_name"] for n in nodes if n.get("theorem_name")
    }
    for node in trusted_index.values():
        assumptions = node.get("assumptions", [])
        if not isinstance(assumptions, list):
            continue
        for a in assumptions:
            if not isinstance(a, dict):
                continue
            src = str(a.get("grounding_source", ""))
            if src in conditional_names:
                node["transitive_ungrounded"] = True
                node.setdefault("transitive_ungrounded_via", []).append(src)
                break


def _row_to_kg_node(row: dict[str, Any], paper_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    assumptions = row.get("assumptions", [])
    ungrounded_count = _count_ungrounded(assumptions if isinstance(assumptions, list) else [])
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
        "assumptions": assumptions,
        "ungrounded_assumption_count": ungrounded_count,
        "transitive_ungrounded": False,
        "transitive_ungrounded_via": [],
        "first_failing_step": row.get("first_failing_step", -1),
        "proof_mode": row.get("proof_mode", ""),
        "rounds_used": row.get("rounds_used", 0),
        "time_s": row.get("time_s", 0.0),
        "timestamp": row.get("timestamp", ""),
        "schema_version": meta.get("schema_version", "legacy"),
        "pipeline_commit": meta.get("pipeline_commit", "unknown"),
        "cited_arxiv_ids": _extract_cited_arxiv_ids(row),
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

    # Propagate transitive ungroundedness: trusted nodes that depend on
    # conditional results get flagged so consumers can see the full chain.
    trusted_index = {n["theorem_name"]: n for n in trusted_nodes if n.get("theorem_name")}
    _propagate_ungroundedness(conditional_nodes, trusted_index)
    transitive_count = sum(1 for n in trusted_nodes if n.get("transitive_ungrounded"))
    if transitive_count:
        print(f"[warn] {transitive_count} trusted node(s) transitively depend on conditional results")

    trusted_path = kg_root / "trusted" / "theorems.jsonl"
    conditional_path = kg_root / "conditional" / "theorems.jsonl"
    diagnostics_path = kg_root / "diagnostics" / "theorems.jsonl"

    _jsonl_write(trusted_path, trusted_nodes)
    _jsonl_write(conditional_path, conditional_nodes)
    _jsonl_write(diagnostics_path, diagnostic_nodes)

    summary.files_written.extend([str(trusted_path), str(conditional_path), str(diagnostics_path)])

    db_path = kg_root / "kg_index.db"
    _sqlite_write(db_path, trusted_nodes, "trusted")
    _sqlite_write(db_path, conditional_nodes, "conditional")
    _sqlite_write(db_path, diagnostic_nodes, "diagnostics")
    summary.files_written.append(str(db_path))

    all_nodes = trusted_nodes + conditional_nodes + diagnostic_nodes
    try:
        summary.citation_edges = _sqlite_merge_citation_edges(db_path, all_nodes)
    except Exception:
        summary.citation_edges = 0
    if summary.citation_edges:
        print(f"[info] wrote {summary.citation_edges} citation edge row(s) (cites_arxiv)")

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
