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
import hashlib
import json
import re
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from canonicalization import build_manual_conflict_queue, canonical_record

try:
    from statement_retrieval import (
        build_statement_index as _build_statement_index,
        query_statement_index as _query_statement_index,
        statement_text_from_row as _statement_text_from_row,
    )
except ModuleNotFoundError:
    _build_statement_index = None
    _query_statement_index = None
    _statement_text_from_row = None

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
    # Dual-metric tracking: statements vs proofs are tracked separately so
    # downstream reports can distinguish "we formalized it" from "we proved it".
    statements_formalized: int = 0   # FULLY_PROVEN + AXIOM_BACKED + INTERMEDIARY_PROVEN
    proofs_closed: int = 0           # FULLY_PROVEN only (closes from axioms without sorry)
    axiom_backed: int = 0            # correct statement; proof delegates to domain axiom
    domain_library_blocked: int = 0  # theorems blocked by missing Mathlib library
    citation_edges: int = 0
    relation_edges: int = 0
    taxonomy_edges: int = 0
    edge_evidence_links: int = 0
    entity_nodes: int = 0
    math_nodes: int = 0
    evidence_nodes: int = 0
    semantic_edges: int = 0
    canonical_groups: int = 0
    canonical_duplicates: int = 0
    canonical_near_duplicates: int = 0
    statement_index: str = ""
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


def canonical_relation_id(*, src: str, dst: str, edge_type: str) -> str:
    raw = f"{edge_type}|{src}|{dst}"
    return "crl_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


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
            canonical_relation_id TEXT NOT NULL DEFAULT '',
            src_kind TEXT NOT NULL DEFAULT 'theorem',
            dst_kind TEXT NOT NULL DEFAULT 'theorem',
            confidence REAL NOT NULL DEFAULT 0.0,
            evidence_ids_json TEXT NOT NULL DEFAULT '[]',
            provenance_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (src_theorem, dst_theorem, edge_type)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS kg_entities (
            entity_id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            label TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    # Backward-compatible migration for existing dbs created before edge metadata.
    node_cols = {
        str(r[1])
        for r in con.execute("PRAGMA table_info(kg_nodes)").fetchall()
    }
    if "promotion_gate_passed" not in node_cols:
        con.execute("ALTER TABLE kg_nodes ADD COLUMN promotion_gate_passed INTEGER NOT NULL DEFAULT 0")
    if "transitive_ungrounded" not in node_cols:
        con.execute("ALTER TABLE kg_nodes ADD COLUMN transitive_ungrounded INTEGER NOT NULL DEFAULT 0")
    if "ungrounded_assumption_count" not in node_cols:
        con.execute("ALTER TABLE kg_nodes ADD COLUMN ungrounded_assumption_count INTEGER NOT NULL DEFAULT 0")
    if "proof_mode" not in node_cols:
        con.execute("ALTER TABLE kg_nodes ADD COLUMN proof_mode TEXT NOT NULL DEFAULT ''")
    if "rounds_used" not in node_cols:
        con.execute("ALTER TABLE kg_nodes ADD COLUMN rounds_used INTEGER NOT NULL DEFAULT 0")
    if "time_s" not in node_cols:
        con.execute("ALTER TABLE kg_nodes ADD COLUMN time_s REAL NOT NULL DEFAULT 0.0")
    if "timestamp" not in node_cols:
        con.execute("ALTER TABLE kg_nodes ADD COLUMN timestamp TEXT NOT NULL DEFAULT ''")

    cols = {
        str(r[1])
        for r in con.execute("PRAGMA table_info(kg_edges)").fetchall()
    }
    if "src_kind" not in cols:
        con.execute("ALTER TABLE kg_edges ADD COLUMN src_kind TEXT NOT NULL DEFAULT 'theorem'")
    if "canonical_relation_id" not in cols:
        con.execute("ALTER TABLE kg_edges ADD COLUMN canonical_relation_id TEXT NOT NULL DEFAULT ''")
    if "dst_kind" not in cols:
        con.execute("ALTER TABLE kg_edges ADD COLUMN dst_kind TEXT NOT NULL DEFAULT 'theorem'")
    if "confidence" not in cols:
        con.execute("ALTER TABLE kg_edges ADD COLUMN confidence REAL NOT NULL DEFAULT 0.0")
    if "evidence_ids_json" not in cols:
        con.execute("ALTER TABLE kg_edges ADD COLUMN evidence_ids_json TEXT NOT NULL DEFAULT '[]'")
    if "provenance_json" not in cols:
        con.execute("ALTER TABLE kg_edges ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}'")
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_kg_nodes_layer ON kg_nodes(layer)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_kg_nodes_status ON kg_nodes(status)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_kg_edges_crid ON kg_edges(canonical_relation_id)"
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
                        INSERT OR IGNORE INTO kg_edges(
                            src_theorem, dst_theorem, edge_type, canonical_relation_id
                        )
                        VALUES (?, ?, 'transitive_dep', ?)
                        """,
                        (
                            theorem_name,
                            dep,
                            canonical_relation_id(src=theorem_name, dst=dep, edge_type="transitive_dep"),
                        ),
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
                evidence_ids = []
                eid = str(node.get("evidence_id", "")).strip()
                if eid:
                    evidence_ids.append(eid)
                cur = con.execute(
                    """
                    INSERT OR REPLACE INTO kg_edges(
                        src_theorem, dst_theorem, edge_type,
                        canonical_relation_id, src_kind, dst_kind, confidence, evidence_ids_json, provenance_json
                    )
                    VALUES (?, ?, 'cites_arxiv', ?, 'theorem', 'paper', ?, ?, ?)
                    """,
                    (
                        src,
                        tid,
                        canonical_relation_id(src=src, dst=tid, edge_type="cites_arxiv"),
                        0.8,
                        json.dumps(evidence_ids, ensure_ascii=False),
                        json.dumps({"source": "provenance.cited_refs"}, ensure_ascii=False),
                    ),
                )
                inserted += int(cur.rowcount or 0)
    con.close()
    return inserted


def _sqlite_merge_relation_edges(
    db_path: Path,
    relation_edges: list[dict[str, Any]],
) -> int:
    """Replace heuristic relation rows in kg_edges."""
    if not db_path.exists():
        return 0
    con = sqlite3.connect(str(db_path), timeout=30.0)
    inserted = 0
    relation_types = (
        "equivalent_to",
        "generalizes",
        "specializes",
        "implies",
        "semantically_similar_to",
        "uses_definition",
        "proved_by",
        "bridge_by",
    )
    with con:
        placeholders = ", ".join("?" for _ in relation_types)
        con.execute(f"DELETE FROM kg_edges WHERE edge_type IN ({placeholders})", relation_types)
        for edge in relation_edges:
            src = str(edge.get("src_theorem", "")).strip()
            dst = str(edge.get("dst_theorem", "")).strip()
            edge_type = str(edge.get("edge_type", "")).strip()
            if not src or not dst or not edge_type:
                continue
            cur = con.execute(
                """
                INSERT OR REPLACE INTO kg_edges(
                    src_theorem, dst_theorem, edge_type,
                    canonical_relation_id, src_kind, dst_kind, confidence, evidence_ids_json, provenance_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    src,
                    dst,
                    edge_type,
                    str(edge.get("canonical_relation_id", canonical_relation_id(src=src, dst=dst, edge_type=edge_type))),
                    str(edge.get("src_kind", "theorem")),
                    str(edge.get("dst_kind", "theorem")),
                    float(edge.get("confidence", 0.0)),
                    json.dumps(edge.get("evidence_ids", []), ensure_ascii=False),
                    json.dumps(edge.get("provenance", {}), ensure_ascii=False),
                ),
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


def _math_view_node(node: dict[str, Any]) -> dict[str, Any]:
    """Public clean math view (no raw proof/evidence payload)."""
    return {
        "paper_id": node.get("paper_id", ""),
        "theorem_name": node.get("theorem_name", ""),
        "canonical_theorem_id": node.get("canonical_theorem_id", ""),
        "claim_shape": node.get("claim_shape", "unknown"),
        "status": node.get("status", "UNRESOLVED"),
        "layer": node.get("layer", ""),
        "trust_class": node.get("trust_class", "TRUST_PLACEHOLDER"),
        "promotion_gate_passed": bool(node.get("promotion_gate_passed", False)),
        "proof_mode": node.get("proof_mode", ""),
        "time_s": node.get("time_s", 0.0),
    }


def query_math_kg(
    db_path: Path,
    *,
    layer: str | None = None,
    paper_id: str | None = None,
    status: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Query KG and return clean public math nodes only."""
    nodes = query_kg(
        db_path,
        layer=layer,
        paper_id=paper_id,
        status=status,
        limit=limit,
    )
    return [_math_view_node(n) for n in nodes]


def query_kg_edges(
    db_path: Path,
    *,
    edge_type: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Query KG edges for clean graph traversal endpoints."""
    if not db_path.exists():
        return []
    con = sqlite3.connect(str(db_path), timeout=10.0)
    con.row_factory = sqlite3.Row
    clauses: list[str] = []
    params: list[Any] = []
    if edge_type:
        clauses.append("edge_type = ?")
        params.append(edge_type)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    cols = {
        str(r[1])
        for r in con.execute("PRAGMA table_info(kg_edges)").fetchall()
    }
    has_meta = "src_kind" in cols and "evidence_ids_json" in cols
    if has_meta:
        rows = con.execute(
            (
                "SELECT src_theorem, dst_theorem, edge_type, canonical_relation_id, src_kind, dst_kind, "
                "confidence, evidence_ids_json, provenance_json "
                f"FROM kg_edges {where} LIMIT ?"
            ),
            params + [limit],
        ).fetchall()
    else:
        rows = con.execute(
            f"SELECT src_theorem, dst_theorem, edge_type FROM kg_edges {where} LIMIT ?",
            params + [limit],
        ).fetchall()
    con.close()
    out: list[dict[str, Any]] = []
    for r in rows:
        evidence_ids: list[str] = []
        provenance: dict[str, Any] = {}
        if has_meta:
            try:
                evidence_ids = json.loads(str(r["evidence_ids_json"]))
            except Exception:
                evidence_ids = []
            try:
                provenance = json.loads(str(r["provenance_json"]))
            except Exception:
                provenance = {}
        out.append(
            {
                "src_theorem": str(r["src_theorem"]),
                "dst_theorem": str(r["dst_theorem"]),
                "edge_type": str(r["edge_type"]),
                "canonical_relation_id": (
                    str(r["canonical_relation_id"])
                    if has_meta and "canonical_relation_id" in r.keys() and str(r["canonical_relation_id"]).strip()
                    else canonical_relation_id(
                        src=str(r["src_theorem"]),
                        dst=str(r["dst_theorem"]),
                        edge_type=str(r["edge_type"]),
                    )
                ),
                "src_kind": str(r["src_kind"]) if has_meta else "theorem",
                "dst_kind": str(r["dst_kind"]) if has_meta else "theorem",
                "confidence": float(r["confidence"]) if has_meta else 0.0,
                "evidence_ids": evidence_ids,
                "provenance": provenance,
            }
        )
    return out


_CANON_TOKEN_RE = re.compile(r"[A-Za-z0-9_']+")


def _canon_tokens(stmt: str) -> set[str]:
    toks = {t.lower() for t in _CANON_TOKEN_RE.findall(stmt or "") if len(t) >= 3}
    stop = {"theorem", "lemma", "prop", "type", "forall", "exists", "true", "false"}
    return {t for t in toks if t not in stop}


def _node_ref(node: dict[str, Any]) -> str:
    return f"{str(node.get('paper_id', '')).strip()}|{str(node.get('theorem_name', '')).strip()}"


def _edge_record(
    *,
    src: str,
    dst: str,
    edge_type: str,
    evidence_ids: list[str],
    confidence: float,
    src_kind: str = "theorem",
    dst_kind: str = "theorem",
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "src_theorem": src,
        "dst_theorem": dst,
        "edge_type": edge_type,
        "canonical_relation_id": canonical_relation_id(src=src, dst=dst, edge_type=edge_type),
        "src_kind": src_kind,
        "dst_kind": dst_kind,
        "confidence": max(0.0, min(1.0, float(confidence))),
        "evidence_ids": sorted({e for e in evidence_ids if e}),
        "provenance": provenance or {},
    }


def _build_canonical_merge_report(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        cid = str(node.get("canonical_theorem_id", "")).strip()
        if cid:
            groups[cid].append(node)

    report_groups: list[dict[str, Any]] = []
    duplicate_total = 0
    for cid, members in groups.items():
        if len(members) <= 1:
            continue
        duplicate_total += len(members) - 1
        members_sorted = sorted(
            members,
            key=lambda m: (
                str(m.get("paper_id", "")),
                str(m.get("theorem_name", "")),
            ),
        )
        representative = members_sorted[0]
        report_groups.append(
            {
                "canonical_theorem_id": cid,
                "representative": {
                    "paper_id": representative.get("paper_id", ""),
                    "theorem_name": representative.get("theorem_name", ""),
                },
                "members": [
                    {
                        "paper_id": m.get("paper_id", ""),
                        "theorem_name": m.get("theorem_name", ""),
                    }
                    for m in members_sorted
                ],
            }
        )

    report_groups.sort(key=lambda g: (-len(g["members"]), g["canonical_theorem_id"]))
    return {
        "canonical_groups": len(report_groups),
        "canonical_duplicates": duplicate_total,
        "groups": report_groups,
    }


def _extract_relation_edges(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Heuristic relation scaffold over canonical statements.

    Emits (src, dst, edge_type) tuples for:
    - equivalent_to (same canonical theorem id)
    - generalizes/specializes (token-subset heuristic in same claim shape)
    - implies (high-overlap directional relation in same claim shape)
    """
    edge_map: dict[tuple[str, str, str], dict[str, Any]] = {}
    keyed: list[tuple[str, str, str, set[str], str, list[str]]] = []
    for n in nodes:
        paper_id = str(n.get("paper_id", "")).strip()
        theorem_name = str(n.get("theorem_name", "")).strip()
        if not paper_id or not theorem_name:
            continue
        node_id = f"{paper_id}|{theorem_name}"
        cid = str(n.get("canonical_theorem_id", "")).strip()
        toks = _canon_tokens(str(n.get("canonical_statement", "")))
        shape = str(n.get("claim_shape", "unknown"))
        eids = [str(n.get("evidence_id", "")).strip()] if n.get("evidence_id") else []
        keyed.append((node_id, cid, shape, toks, theorem_name, eids))

    def _merge_edge(edge: dict[str, Any]) -> None:
        k = (edge["src_theorem"], edge["dst_theorem"], edge["edge_type"])
        if k not in edge_map:
            edge_map[k] = edge
            return
        cur = edge_map[k]
        cur["confidence"] = max(float(cur.get("confidence", 0.0)), float(edge.get("confidence", 0.0)))
        cur["evidence_ids"] = sorted(
            set(cur.get("evidence_ids", [])).union(edge.get("evidence_ids", []))
        )

    # 1) Equivalence by canonical id.
    by_cid: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
    for node_id, cid, _shape, _toks, _name, eids in keyed:
        if cid:
            by_cid[cid].append((node_id, eids))
    for members in by_cid.values():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                (a, ae), (b, be) = members[i], members[j]
                _merge_edge(
                    _edge_record(
                        src=a,
                        dst=b,
                        edge_type="equivalent_to",
                        evidence_ids=ae + be,
                        confidence=0.99,
                    )
                )
                _merge_edge(
                    _edge_record(
                        src=b,
                        dst=a,
                        edge_type="equivalent_to",
                        evidence_ids=ae + be,
                        confidence=0.99,
                    )
                )

    # 2) Generalization/specialization and implication heuristics.
    n = len(keyed)
    for i in range(n):
        src_id, _src_cid, src_shape, src_toks, _src_name, src_eids = keyed[i]
        if not src_toks:
            continue
        for j in range(i + 1, n):
            dst_id, _dst_cid, dst_shape, dst_toks, _dst_name, dst_eids = keyed[j]
            if src_shape != dst_shape or not dst_toks:
                continue
            inter = len(src_toks & dst_toks)
            if inter == 0:
                continue
            src_cover = inter / max(1, len(src_toks))
            dst_cover = inter / max(1, len(dst_toks))

            # Near-containment => one statement likely generalizes the other.
            if src_cover >= 0.95 and len(src_toks) < len(dst_toks):
                _merge_edge(
                    _edge_record(
                        src=src_id,
                        dst=dst_id,
                        edge_type="generalizes",
                        evidence_ids=src_eids + dst_eids,
                        confidence=0.86,
                    )
                )
                _merge_edge(
                    _edge_record(
                        src=dst_id,
                        dst=src_id,
                        edge_type="specializes",
                        evidence_ids=src_eids + dst_eids,
                        confidence=0.86,
                    )
                )
                _merge_edge(
                    _edge_record(
                        src=dst_id,
                        dst=src_id,
                        edge_type="implies",
                        evidence_ids=src_eids + dst_eids,
                        confidence=0.82,
                    )
                )
            elif dst_cover >= 0.95 and len(dst_toks) < len(src_toks):
                _merge_edge(
                    _edge_record(
                        src=dst_id,
                        dst=src_id,
                        edge_type="generalizes",
                        evidence_ids=src_eids + dst_eids,
                        confidence=0.86,
                    )
                )
                _merge_edge(
                    _edge_record(
                        src=src_id,
                        dst=dst_id,
                        edge_type="specializes",
                        evidence_ids=src_eids + dst_eids,
                        confidence=0.86,
                    )
                )
                _merge_edge(
                    _edge_record(
                        src=src_id,
                        dst=dst_id,
                        edge_type="implies",
                        evidence_ids=src_eids + dst_eids,
                        confidence=0.82,
                    )
                )
            elif src_cover >= 0.90 and dst_cover >= 0.90:
                # Strong overlap but not identical cid => soft implication both ways.
                _merge_edge(
                    _edge_record(
                        src=src_id,
                        dst=dst_id,
                        edge_type="implies",
                        evidence_ids=src_eids + dst_eids,
                        confidence=0.74,
                    )
                )
                _merge_edge(
                    _edge_record(
                        src=dst_id,
                        dst=src_id,
                        edge_type="implies",
                        evidence_ids=src_eids + dst_eids,
                        confidence=0.74,
                    )
                )

    return [edge_map[k] for k in sorted(edge_map.keys())]


def _extract_semantic_similarity_edges(
    nodes: list[dict[str, Any]],
    *,
    statement_index: Path,
    threshold: float = 0.55,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Create theorem-neighbor edges from the extracted-statement retriever."""
    if _query_statement_index is None or not statement_index.exists():
        return []

    theorem_keys = {_node_ref(n) for n in nodes if n.get("paper_id") and n.get("theorem_name")}
    node_by_ref = {_node_ref(n): n for n in nodes if _node_ref(n) in theorem_keys}
    edge_map: dict[tuple[str, str, str], dict[str, Any]] = {}

    for node in nodes:
        src = _node_ref(node)
        if not src or src not in theorem_keys:
            continue
        query_text = str(
            node.get("semantic_statement_text")
            or node.get("canonical_statement")
            or node.get("lean_statement")
            or ""
        ).strip()
        if not query_text:
            continue
        try:
            hits = _query_statement_index(
                statement_index,
                query_text,
                top_k=max(1, top_k + 1),
                exclude_statement_id=src,
                overfetch=4,
            )
        except Exception:
            continue
        for hit in hits:
            dst = str(hit.get("statement_id", "")).strip()
            if not dst or dst == src or dst not in theorem_keys:
                continue
            score = float(hit.get("score", 0.0) or 0.0)
            if score < threshold:
                continue
            dst_node = node_by_ref.get(dst, {})
            evidence_ids = []
            src_eid = str(node.get("evidence_id", "")).strip()
            dst_eid = str(dst_node.get("evidence_id", "") or hit.get("evidence_id", "") or "").strip()
            if src_eid:
                evidence_ids.append(src_eid)
            if dst_eid:
                evidence_ids.append(dst_eid)
            edge = _edge_record(
                src=src,
                dst=dst,
                edge_type="semantically_similar_to",
                evidence_ids=evidence_ids,
                confidence=score,
                provenance={
                    "source": "statement_retrieval",
                    "statement_index": str(statement_index),
                    "score": score,
                    "threshold": threshold,
                    "encoder": _statement_index_encoder(statement_index),
                },
            )
            key = (edge["src_theorem"], edge["dst_theorem"], edge["edge_type"])
            if key not in edge_map or float(edge_map[key].get("confidence", 0.0)) < edge["confidence"]:
                edge_map[key] = edge

    return [edge_map[k] for k in sorted(edge_map.keys())]


def _statement_index_encoder(statement_index: Path) -> str:
    meta_path = statement_index / "meta.json"
    if not meta_path.exists():
        return ""
    try:
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return str(raw.get("encoder_name", "") or "")


_TYPE_TOKEN_RE = re.compile(r"\b[A-Z][A-Za-z0-9_']+\b")
_DEF_TOKEN_RE = re.compile(r"\b(definition|define|defined as)\b", re.IGNORECASE)
_CONCEPT_PHRASE_RE = re.compile(r"\b([a-z][a-z0-9_-]*(?:\s+[a-z][a-z0-9_-]*){0,3})\b")


def _load_concept_map() -> dict[str, str]:
    candidates = [
        Path("output/tc_graph.json"),
        Path("data/tc_graph.json"),
    ]
    for p in candidates:
        if not p.exists():
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(raw, dict):
            cmap = raw.get("concept_map", {})
            if isinstance(cmap, dict):
                out: dict[str, str] = {}
                for k, v in cmap.items():
                    ks = str(k).strip()
                    vs = str(v).strip() if v is not None else ""
                    if ks and vs:
                        out[ks.lower()] = vs
                if out:
                    return out
    return {}


def _extract_entity_graph(nodes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract entity taxonomy and typed edges from theorem evidence rows."""
    entities: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    theorem_keys = {_node_ref(n) for n in nodes if n.get("paper_id") and n.get("theorem_name")}
    concept_map = _load_concept_map()

    for node in nodes:
        src = _node_ref(node)
        if not src or src == "|":
            continue
        eids = [str(node.get("evidence_id", "")).strip()] if node.get("evidence_id") else []

        # Entity extraction from assumptions and statement.
        assumptions = node.get("assumptions", [])
        if not isinstance(assumptions, list):
            assumptions = []
        stmt = str(node.get("canonical_statement", "")) + " " + str(node.get("lean_statement", ""))
        lower_stmt = stmt.lower()
        candidate_types = set(_TYPE_TOKEN_RE.findall(stmt))
        for a in assumptions:
            if isinstance(a, dict):
                candidate_types.update(_TYPE_TOKEN_RE.findall(str(a.get("lean_expr", ""))))

        for tok in sorted(candidate_types):
            etype = "definition" if _DEF_TOKEN_RE.search(stmt) else "object_type"
            eid = f"entity:{etype}:{tok}"
            entities[eid] = {
                "entity_id": eid,
                "entity_type": etype,
                "label": tok,
                "payload": {"origin": "heuristic_token", "token": tok},
            }
            edges.append(
                _edge_record(
                    src=src,
                    dst=eid,
                    edge_type="uses_definition",
                    src_kind="theorem",
                    dst_kind="entity",
                    evidence_ids=eids,
                    confidence=0.55,
                    provenance={"source": "assumptions_or_statement_tokens"},
                )
            )

        # Concept extraction from concept_map aliases and noun phrases.
        concept_hits: set[tuple[str, str]] = set()
        for alias, target in concept_map.items():
            if alias and alias in lower_stmt:
                concept_hits.add((alias, target))
        for ph in _CONCEPT_PHRASE_RE.findall(lower_stmt):
            phr = " ".join(ph.split()).strip()
            if len(phr) < 4:
                continue
            if phr in concept_map:
                concept_hits.add((phr, concept_map[phr]))
        for alias, target in sorted(concept_hits):
            cid = f"entity:concept:{target}"
            entities[cid] = {
                "entity_id": cid,
                "entity_type": "concept",
                "label": target,
                "payload": {"origin": "concept_map", "alias": alias},
            }
            edges.append(
                _edge_record(
                    src=src,
                    dst=cid,
                    edge_type="uses_definition",
                    src_kind="theorem",
                    dst_kind="entity",
                    evidence_ids=eids,
                    confidence=0.68,
                    provenance={"source": "concept_map_match", "alias": alias},
                )
            )

        # proved_by / bridge_by from assumption grounding_source where available.
        for a in assumptions:
            if not isinstance(a, dict):
                continue
            gsrc = str(a.get("grounding_source", "")).strip()
            if not gsrc:
                continue
            if gsrc in theorem_keys:
                edges.append(
                    _edge_record(
                        src=src,
                        dst=gsrc,
                        edge_type="proved_by",
                        evidence_ids=eids,
                        confidence=0.75,
                        provenance={"source": "assumptions.grounding_source"},
                    )
                )
            elif gsrc.lower().startswith("bridge:"):
                dst = gsrc.split(":", 1)[1].strip()
                if dst:
                    edges.append(
                        _edge_record(
                            src=src,
                            dst=dst,
                            edge_type="bridge_by",
                            evidence_ids=eids,
                            confidence=0.65,
                            provenance={"source": "assumptions.grounding_source"},
                        )
                    )

        # Add theorem entity wrapper.
        thm_entity = f"entity:theorem:{src}"
        entities[thm_entity] = {
            "entity_id": thm_entity,
            "entity_type": "theorem",
            "label": str(node.get("theorem_name", "")),
            "payload": {
                "paper_id": str(node.get("paper_id", "")),
                "canonical_theorem_id": str(node.get("canonical_theorem_id", "")),
            },
        }

    return sorted(entities.values(), key=lambda e: str(e.get("entity_id", ""))), edges


def _sqlite_merge_entities(db_path: Path, entities: list[dict[str, Any]]) -> int:
    if not db_path.exists():
        return 0
    con = sqlite3.connect(str(db_path), timeout=30.0)
    inserted = 0
    with con:
        for e in entities:
            cur = con.execute(
                """
                INSERT OR REPLACE INTO kg_entities(entity_id, entity_type, label, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    str(e.get("entity_id", "")),
                    str(e.get("entity_type", "")),
                    str(e.get("label", "")),
                    json.dumps(e.get("payload", {}), ensure_ascii=False),
                ),
            )
            inserted += int(cur.rowcount or 0)
    con.close()
    return inserted


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
    if status in {"INTERMEDIARY_PROVEN", "AXIOM_BACKED"}:
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


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    return [text] if text else []


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
    canon = canonical_record(
        lean_statement=str(row.get("lean_statement", "")),
        theorem_name=str(row.get("theorem_name", "")),
        paper_id=paper_id,
    )
    assumptions = row.get("assumptions", [])
    ungrounded_count = _count_ungrounded(assumptions if isinstance(assumptions, list) else [])
    artifact = row.get("semantic_equivalence_artifact")
    if not isinstance(artifact, dict):
        artifact = {}
    context = row.get("context_pack")
    if not isinstance(context, dict):
        context = {}
    statement_text = ""
    if _statement_text_from_row is not None:
        try:
            statement_text = _statement_text_from_row(row)
        except Exception:
            statement_text = ""
    if not statement_text:
        statement_text = str(row.get("lean_statement", ""))
    original_latex = str(
        artifact.get("original_latex_theorem")
        or row.get("original_latex_theorem")
        or context.get("original_latex_theorem")
        or ""
    )
    normalized_nl = str(
        artifact.get("normalized_natural_language_theorem")
        or row.get("normalized_natural_language_theorem")
        or ""
    )
    extracted_assumptions = _as_str_list(
        artifact.get("extracted_assumptions")
        or row.get("extracted_assumptions")
    )
    extracted_conclusion = str(
        artifact.get("extracted_conclusion")
        or row.get("extracted_conclusion")
        or ""
    )
    return {
        "evidence_id": f"ev:{paper_id}|{row.get('theorem_name', '')}",
        "paper_id": paper_id,
        "theorem_name": row.get("theorem_name", ""),
        "canonical_theorem_id": canon["canonical_theorem_id"],
        "canonical_statement": canon["canonical_statement"],
        "claim_shape": canon["claim_shape"],
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
        "semantic_statement": {
            "original_latex_theorem": original_latex,
            "normalized_natural_language_theorem": normalized_nl,
            "extracted_assumptions": extracted_assumptions,
            "extracted_conclusion": extracted_conclusion,
            "retrieval_text_hash": hashlib.sha256(statement_text.encode("utf-8")).hexdigest()[:24],
        },
        "semantic_statement_text": statement_text,
        # Evidence payload fields kept internal for debug/replay.
        "provenance": row.get("provenance", {}),
        "validation_gates": row.get("validation_gates", {}),
        "gate_failures": row.get("gate_failures", []),
        "error_message": row.get("error_message", ""),
        "proof_text": row.get("proof_text", ""),
    }


def build_kg(
    *,
    ledger_dir: Path,
    kg_root: Path,
    paper: str = "",
    statement_index: Path | None = None,
    build_statement_index: bool = False,
    semantic_edge_threshold: float = 0.55,
    semantic_top_k: int = 5,
    statement_encoder: str | None = None,
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
            status = str(row.get("status", "UNRESOLVED"))

            # Dual-metric: statements_formalized vs proofs_closed
            if status in {"FULLY_PROVEN", "AXIOM_BACKED", "INTERMEDIARY_PROVEN"}:
                summary.statements_formalized += 1
            if status == "FULLY_PROVEN":
                summary.proofs_closed += 1
            if status == "AXIOM_BACKED":
                summary.axiom_backed += 1
                payload = row.get("payload_json") or row.get("payload", {})
                if isinstance(payload, str):
                    try:
                        payload = __import__("json").loads(payload)
                    except Exception:
                        payload = {}
                if payload.get("domain_library_needed"):
                    summary.domain_library_blocked += 1

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
    math_path = kg_root / "math" / "theorems.jsonl"
    evidence_path = kg_root / "evidence" / "theorems.jsonl"

    _jsonl_write(trusted_path, trusted_nodes)
    _jsonl_write(conditional_path, conditional_nodes)
    _jsonl_write(diagnostics_path, diagnostic_nodes)
    all_nodes = trusted_nodes + conditional_nodes + diagnostic_nodes
    for n in all_nodes:
        n["layer"] = _classification({"status": n.get("status", ""), "promotion_gate_passed": n.get("promotion_gate_passed", False), "adversarial_flags": n.get("adversarial_flags", [])})
    math_nodes = [_math_view_node(n) for n in all_nodes]
    _jsonl_write(math_path, math_nodes)
    _jsonl_write(evidence_path, all_nodes)
    summary.math_nodes = len(math_nodes)
    summary.evidence_nodes = len(all_nodes)
    merge_report = _build_canonical_merge_report(all_nodes)
    summary.canonical_groups = int(merge_report.get("canonical_groups", 0))
    summary.canonical_duplicates = int(merge_report.get("canonical_duplicates", 0))
    conflict_queue = build_manual_conflict_queue(all_nodes)
    summary.canonical_near_duplicates = int(conflict_queue.get("items_total", 0))

    summary.files_written.extend(
        [
            str(trusted_path),
            str(conditional_path),
            str(diagnostics_path),
            str(math_path),
            str(evidence_path),
        ]
    )

    db_path = kg_root / "kg_index.db"
    _sqlite_write(db_path, trusted_nodes, "trusted")
    _sqlite_write(db_path, conditional_nodes, "conditional")
    _sqlite_write(db_path, diagnostic_nodes, "diagnostics")
    summary.files_written.append(str(db_path))

    try:
        summary.citation_edges = _sqlite_merge_citation_edges(db_path, all_nodes)
    except Exception:
        summary.citation_edges = 0
    if summary.citation_edges:
        print(f"[info] wrote {summary.citation_edges} citation edge row(s) (cites_arxiv)")
    semantic_edges: list[dict[str, Any]] = []
    if statement_index is not None:
        summary.statement_index = str(statement_index)
        if build_statement_index or not statement_index.exists():
            if _build_statement_index is not None:
                try:
                    meta = _build_statement_index(
                        ledger_dir=ledger_dir,
                        out_dir=statement_index,
                        paper=paper,
                        encoder_name=statement_encoder,
                    )
                    if int(meta.get("count", 0) or 0) > 0:
                        summary.files_written.append(str(statement_index))
                except Exception as exc:
                    print(f"[warn] statement index build failed: {exc}")
        if statement_index.exists():
            semantic_edges = _extract_semantic_similarity_edges(
                all_nodes,
                statement_index=statement_index,
                threshold=semantic_edge_threshold,
                top_k=semantic_top_k,
            )

    relation_edges = _extract_relation_edges(all_nodes)
    entities, taxonomy_edges = _extract_entity_graph(all_nodes)
    combined_edges = relation_edges + taxonomy_edges + semantic_edges
    try:
        summary.relation_edges = _sqlite_merge_relation_edges(db_path, combined_edges)
    except Exception:
        summary.relation_edges = 0
    if summary.relation_edges:
        print(f"[info] wrote {summary.relation_edges} relation edge row(s)")
    summary.taxonomy_edges = len(taxonomy_edges)
    summary.semantic_edges = len(semantic_edges)
    summary.edge_evidence_links = sum(
        len(e.get("evidence_ids", []))
        for e in combined_edges
    )
    try:
        summary.entity_nodes = _sqlite_merge_entities(db_path, entities)
    except Exception:
        summary.entity_nodes = 0
    entity_path = kg_root / "math" / "entities.jsonl"
    _jsonl_write(entity_path, entities)
    summary.files_written.append(str(entity_path))

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
        "math_nodes": summary.math_nodes,
        "evidence_nodes": summary.evidence_nodes,
        "canonical_groups": summary.canonical_groups,
        "canonical_duplicates": summary.canonical_duplicates,
        "canonical_near_duplicates": summary.canonical_near_duplicates,
        "relation_edges": summary.relation_edges,
        "taxonomy_edges": summary.taxonomy_edges,
        "semantic_edges": summary.semantic_edges,
        "edge_evidence_links": summary.edge_evidence_links,
        "entity_nodes": summary.entity_nodes,
        "citation_edges": summary.citation_edges,
        "statement_index": summary.statement_index,
        "paper_manifests": sorted(per_paper_manifests.keys()),
    }
    all_manifest_path = manifest_dir / "promotion_manifest_all.json"
    all_manifest_path.write_text(json.dumps(all_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    summary.files_written.append(str(all_manifest_path))
    merge_report_path = manifest_dir / "canonical_merge_report.json"
    merge_report_path.write_text(json.dumps(merge_report, indent=2, ensure_ascii=False), encoding="utf-8")
    summary.files_written.append(str(merge_report_path))
    conflict_queue_path = manifest_dir / "canonical_conflict_queue.json"
    conflict_queue_path.write_text(json.dumps(conflict_queue, indent=2, ensure_ascii=False), encoding="utf-8")
    summary.files_written.append(str(conflict_queue_path))

    return summary


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build KG layers and promotion manifests from verification ledgers")
    p.add_argument("--ledger-dir", default="output/verification_ledgers", help="Ledger directory")
    p.add_argument("--kg-root", default="output/kg", help="Output KG root")
    p.add_argument("--paper", default="", help="Optional paper id (e.g. 2304.09598)")
    p.add_argument(
        "--statement-index",
        default="",
        help="Optional extracted-statement retrieval index directory for semantic KG edges",
    )
    p.add_argument(
        "--build-statement-index",
        action="store_true",
        help="Build or refresh --statement-index from the selected ledgers before creating semantic edges",
    )
    p.add_argument(
        "--statement-encoder",
        default=None,
        help="Statement index encoder name, or 'hash' for deterministic offline embeddings",
    )
    p.add_argument(
        "--semantic-edge-threshold",
        type=float,
        default=0.55,
        help="Minimum retrieval score for semantically_similar_to KG edges",
    )
    p.add_argument(
        "--semantic-top-k",
        type=int,
        default=5,
        help="Semantic neighbors queried per theorem when --statement-index is set",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()
    ledger_dir = Path(args.ledger_dir)
    kg_root = Path(args.kg_root)

    if not ledger_dir.exists():
        print(f"[fail] ledger directory not found: {ledger_dir}")
        return 1

    statement_index = Path(args.statement_index) if args.statement_index else None
    summary = build_kg(
        ledger_dir=ledger_dir,
        kg_root=kg_root,
        paper=args.paper,
        statement_index=statement_index,
        build_statement_index=bool(args.build_statement_index),
        semantic_edge_threshold=float(args.semantic_edge_threshold),
        semantic_top_k=int(args.semantic_top_k),
        statement_encoder=args.statement_encoder,
    )
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
    print(
        "[info] canonical="
        f"groups={summary.canonical_groups} duplicates={summary.canonical_duplicates} near={summary.canonical_near_duplicates}"
    )
    print(
        "[info] edges="
        f"relations={summary.relation_edges} taxonomy={summary.taxonomy_edges} "
        f"semantic={summary.semantic_edges} citations={summary.citation_edges} evidence_links={summary.edge_evidence_links}"
    )
    if summary.statement_index:
        print(f"[info] statement_index={summary.statement_index}")
    print(f"[info] entities={summary.entity_nodes}")
    for path in summary.files_written:
        print(f"[info] wrote {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
