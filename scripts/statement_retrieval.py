#!/usr/bin/env python3
"""Semantic retrieval over theorem-level statements extracted from papers."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from premise_retrieval import PremiseEntry, PremiseRetriever


METADATA_FILE = "statement_metadata.jsonl"


@dataclass
class StatementMetadata:
    statement_id: str
    paper_id: str
    theorem_name: str
    status: str = "UNRESOLVED"
    layer: str = ""
    lean_file: str = ""
    canonical_theorem_id: str = ""
    claim_shape: str = "unknown"
    original_latex_theorem: str = ""
    normalized_natural_language_theorem: str = ""
    extracted_assumptions: list[str] | None = None
    extracted_conclusion: str = ""
    lean_statement: str = ""
    evidence_id: str = ""
    source_ledger: str = ""
    text_hash: str = ""


def statement_id(paper_id: str, theorem_name: str) -> str:
    return f"{paper_id.strip()}|{theorem_name.strip()}"


def _paper_id_from_path(path: Path) -> str:
    return path.stem


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


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    return [text] if text else []


def _status_layer(status: str, promotion_gate_passed: bool = False) -> str:
    if status == "FULLY_PROVEN" and promotion_gate_passed:
        return "trusted"
    if status == "INTERMEDIARY_PROVEN":
        return "conditional"
    return "diagnostics"


def _artifact(row: dict[str, Any]) -> dict[str, Any]:
    artifact = row.get("semantic_equivalence_artifact")
    return artifact if isinstance(artifact, dict) else {}


def statement_text_from_row(row: dict[str, Any]) -> str:
    """Build the retrieval text for a ledger/KG theorem row."""
    artifact = _artifact(row)
    context = row.get("context_pack")
    if not isinstance(context, dict):
        context = {}
    schema = context.get("translation_statement_schema")
    if not isinstance(schema, dict):
        schema = context.get("statement_schema") if isinstance(context.get("statement_schema"), dict) else {}

    normalized = str(
        artifact.get("normalized_natural_language_theorem")
        or row.get("normalized_natural_language_theorem")
        or ""
    ).strip()
    original = str(
        artifact.get("original_latex_theorem")
        or row.get("original_latex_theorem")
        or context.get("original_latex_theorem")
        or ""
    ).strip()
    assumptions = _coerce_str_list(
        artifact.get("extracted_assumptions")
        or row.get("extracted_assumptions")
        or schema.get("assumptions")
    )
    conclusion = str(
        artifact.get("extracted_conclusion")
        or row.get("extracted_conclusion")
        or schema.get("claim")
        or ""
    ).strip()
    lean = str(artifact.get("lean_statement") or row.get("lean_statement") or "").strip()
    theorem_name = str(row.get("theorem_name", "")).strip()
    status = str(row.get("status", "")).strip()
    claim_shape = str(row.get("claim_shape", "") or "").strip()

    parts = [
        theorem_name,
        f"status: {status}" if status else "",
        f"claim_shape: {claim_shape}" if claim_shape else "",
        normalized,
        original,
        " ".join(assumptions),
        conclusion,
        lean,
    ]
    return "\n".join(p for p in parts if p).strip()


def metadata_from_row(
    row: dict[str, Any],
    *,
    paper_id: str,
    source_ledger: str = "",
) -> StatementMetadata | None:
    theorem_name = str(row.get("theorem_name", "")).strip()
    if not paper_id or not theorem_name:
        return None
    text = statement_text_from_row(row)
    if not text:
        return None

    artifact = _artifact(row)
    context = row.get("context_pack")
    if not isinstance(context, dict):
        context = {}
    status = str(row.get("status", "UNRESOLVED") or "UNRESOLVED")
    sid = statement_id(paper_id, theorem_name)
    return StatementMetadata(
        statement_id=sid,
        paper_id=paper_id,
        theorem_name=theorem_name,
        status=status,
        layer=str(row.get("layer", "") or _status_layer(status, bool(row.get("promotion_gate_passed", False)))),
        lean_file=str(row.get("lean_file", "") or ""),
        canonical_theorem_id=str(row.get("canonical_theorem_id", "") or ""),
        claim_shape=str(row.get("claim_shape", "unknown") or "unknown"),
        original_latex_theorem=str(
            artifact.get("original_latex_theorem")
            or row.get("original_latex_theorem")
            or context.get("original_latex_theorem")
            or ""
        ),
        normalized_natural_language_theorem=str(
            artifact.get("normalized_natural_language_theorem")
            or row.get("normalized_natural_language_theorem")
            or ""
        ),
        extracted_assumptions=_coerce_str_list(artifact.get("extracted_assumptions") or row.get("extracted_assumptions")),
        extracted_conclusion=str(artifact.get("extracted_conclusion") or row.get("extracted_conclusion") or ""),
        lean_statement=str(artifact.get("lean_statement") or row.get("lean_statement") or ""),
        evidence_id=str(row.get("evidence_id") or f"ev:{sid}"),
        source_ledger=source_ledger,
        text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:24],
    )


def iter_statement_rows(ledger_dir: str | Path, *, paper: str = "") -> list[tuple[StatementMetadata, str]]:
    """Return statement metadata and retrieval text from verification ledgers."""
    root = Path(ledger_dir)
    rows: list[tuple[StatementMetadata, str]] = []
    for ledger in _iter_ledger_files(root, paper=paper):
        _meta, entries = _load_ledger_doc(ledger)
        paper_id = _paper_id_from_path(ledger)
        for row in entries:
            text = statement_text_from_row(row)
            meta = metadata_from_row(row, paper_id=paper_id, source_ledger=str(ledger))
            if meta is None or not text:
                continue
            rows.append((meta, text))
    return rows


def _write_metadata(out_dir: Path, metadata: list[StatementMetadata]) -> None:
    with (out_dir / METADATA_FILE).open("w", encoding="utf-8") as fh:
        for row in metadata:
            fh.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")


def load_statement_metadata(index_dir: str | Path) -> dict[str, StatementMetadata]:
    path = Path(index_dir) / METADATA_FILE
    out: dict[str, StatementMetadata] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        sid = str(raw.get("statement_id", "")).strip()
        if not sid:
            continue
        out[sid] = StatementMetadata(
            statement_id=sid,
            paper_id=str(raw.get("paper_id", "") or ""),
            theorem_name=str(raw.get("theorem_name", "") or ""),
            status=str(raw.get("status", "UNRESOLVED") or "UNRESOLVED"),
            layer=str(raw.get("layer", "") or ""),
            lean_file=str(raw.get("lean_file", "") or ""),
            canonical_theorem_id=str(raw.get("canonical_theorem_id", "") or ""),
            claim_shape=str(raw.get("claim_shape", "unknown") or "unknown"),
            original_latex_theorem=str(raw.get("original_latex_theorem", "") or ""),
            normalized_natural_language_theorem=str(raw.get("normalized_natural_language_theorem", "") or ""),
            extracted_assumptions=_coerce_str_list(raw.get("extracted_assumptions")),
            extracted_conclusion=str(raw.get("extracted_conclusion", "") or ""),
            lean_statement=str(raw.get("lean_statement", "") or ""),
            evidence_id=str(raw.get("evidence_id", "") or ""),
            source_ledger=str(raw.get("source_ledger", "") or ""),
            text_hash=str(raw.get("text_hash", "") or ""),
        )
    return out


def build_statement_index(
    *,
    ledger_dir: str | Path,
    out_dir: str | Path,
    paper: str = "",
    dims: int = 384,
    encoder_name: str | None = None,
) -> dict[str, Any]:
    rows = iter_statement_rows(ledger_dir, paper=paper)
    entries = [
        PremiseEntry(
            name=meta.statement_id,
            statement=text,
            namespace=meta.paper_id,
            source_file=meta.source_ledger,
        )
        for meta, text in rows
    ]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    retriever = PremiseRetriever.build(entries, dims=dims, encoder_name=encoder_name)
    retriever.save_np(out)
    metadata = [meta for meta, _text in rows]
    _write_metadata(out, metadata)

    meta_path = out / "meta.json"
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    payload.update(
        {
            "kind": "desol_statement_index",
            "ledger_dir": str(ledger_dir),
            "paper": paper,
            "count": len(entries),
            "encoder_name": retriever.encoder_name,
            "dims": retriever.dims,
            "metadata_file": METADATA_FILE,
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    meta_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def query_statement_index(
    index_dir: str | Path,
    query: str,
    *,
    top_k: int = 10,
    paper_id: str = "",
    same_paper_only: bool = False,
    exclude_statement_id: str = "",
    overfetch: int = 5,
) -> list[dict[str, Any]]:
    if top_k < 1:
        return []
    retriever = PremiseRetriever.load(index_dir)
    metadata = load_statement_metadata(index_dir)
    requested = max(top_k, top_k * max(1, overfetch))
    hits = retriever.query(query, top_k=requested)
    out: list[dict[str, Any]] = []
    for hit in hits:
        sid = hit.name
        if exclude_statement_id and sid == exclude_statement_id:
            continue
        meta = metadata.get(sid)
        if paper_id and (meta is None or meta.paper_id != paper_id):
            continue
        payload = {
            "statement_id": sid,
            "score": float(hit.score),
            "statement": hit.statement,
            "namespace": hit.namespace,
            "source_file": hit.source_file if hasattr(hit, "source_file") else "",
        }
        if meta is not None:
            meta_payload = asdict(meta)
            payload.update(meta_payload)
            payload["kg_ref"] = meta.statement_id
        out.append(payload)
        if len(out) >= top_k:
            break
    return out


def _cmd_build(args: argparse.Namespace) -> int:
    summary = build_statement_index(
        ledger_dir=args.ledger_dir,
        out_dir=args.out,
        paper=args.paper,
        dims=args.dims,
        encoder_name=args.encoder,
    )
    print(
        f"[ok] built statement index entries={summary.get('count', 0)} "
        f"encoder={summary.get('encoder_name', 'unknown')} -> {args.out}"
    )
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    hits = query_statement_index(
        args.index,
        args.query,
        top_k=args.top_k,
        paper_id=args.paper,
        same_paper_only=args.same_paper_only,
    )
    for i, hit in enumerate(hits, start=1):
        label = hit.get("statement_id", "")
        status = hit.get("status", "")
        print(f"{i}. {label} score={float(hit.get('score', 0.0)):.4f} status={status}")
        text = str(hit.get("normalized_natural_language_theorem") or hit.get("statement") or "").strip()
        if text:
            print(f"   {text[:500]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Theorem-level semantic retrieval over extracted statements")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="Build a statement retrieval index from verification ledgers")
    p_build.add_argument("--ledger-dir", default="output/verification_ledgers", help="Ledger directory")
    p_build.add_argument("--out", default="output/statement_index", help="Output index directory")
    p_build.add_argument("--paper", default="", help="Optional single paper id")
    p_build.add_argument("--dims", type=int, default=384, help="Embedding dimension for hash encoder")
    p_build.add_argument("--encoder", default=None, help="Sentence-transformers model name, or 'hash'")
    p_build.set_defaults(func=_cmd_build)

    p_query = sub.add_parser("query", help="Query a statement retrieval index")
    p_query.add_argument("--index", default="output/statement_index", help="Index directory")
    p_query.add_argument("--query", required=True, help="Natural-language, LaTeX, or Lean query")
    p_query.add_argument("--paper", default="", help="Optional paper id filter")
    p_query.add_argument("--same-paper-only", action="store_true", help="Restrict hits to --paper")
    p_query.add_argument("--top-k", type=int, default=10)
    p_query.set_defaults(func=_cmd_query)

    return p


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
