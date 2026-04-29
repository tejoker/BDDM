#!/usr/bin/env python3
"""Statement novelty and deduplication helpers.

This module classifies generated Lean statements for "first formalization"
claims. It is deliberately conservative: proof status is never changed, and
uncertain or unavailable comparisons are recorded as evidence instead of being
silently treated as novelty.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from canonicalization import (
    canonical_claim_shape,
    canonical_theorem_id,
    canonicalize_lean_statement,
)
from premise_retrieval import PremiseEntry, PremiseRetriever
from statement_retrieval import statement_text_from_row


NOVELTY_STATUSES = (
    "new_candidate",
    "mathlib_overlap",
    "duplicate_in_corpus",
    "semantic_near_duplicate",
    "unknown",
)
IDENTITY_STATUSES = (
    "same_statement",
    "near_duplicate",
    "distinct_candidate",
    "unknown",
)

SCHEMA_VERSION = "1.0.0"

_DECL_RE = re.compile(r"^\s*(?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma)\s+\S+", re.MULTILINE)
_LINE_COMMENT_RE = re.compile(r"^\s*--.*$", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/-.*?-/", re.DOTALL)
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class StatementRecord:
    paper_id: str
    theorem_name: str
    source_index: int
    canonical_statement: str
    statement_fingerprint: str
    canonical_theorem_id: str
    claim_shape: str
    retrieval_text: str
    lean_statement: str = ""
    source_ledger: str = ""

    @property
    def statement_id(self) -> str:
        return f"{self.paper_id}|{self.theorem_name}|{self.source_index}"

    @property
    def short_ref(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "theorem_name": self.theorem_name,
            "statement_id": self.statement_id,
            "statement_fingerprint": self.statement_fingerprint,
        }


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _ledger_entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        rows = payload.get("entries") or payload.get("rows") or payload.get("results") or []
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _ledger_paper_id(path: Path, payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("paper_id", "arxiv_id", "source_paper"):
            value = str(payload.get(key, "") or "").strip()
            if value:
                return value
    match = re.search(r"(\d{4}\.\d{4,5}(?:v\d+)?)", str(path.parent))
    if match:
        return match.group(1)
    match = re.search(r"(\d{4}\.\d{4,5}(?:v\d+)?)", str(path))
    if match:
        return match.group(1)
    return path.stem


def _normalize_source_text(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip())


def _strip_comments(text: str) -> str:
    no_blocks = _BLOCK_COMMENT_RE.sub(" ", text or "")
    return _LINE_COMMENT_RE.sub("", no_blocks)


def _extract_decl(text: str) -> str:
    """Return the theorem/lemma declaration portion if one is present."""
    cleaned = _strip_comments(text or "")
    match = _DECL_RE.search(cleaned)
    if not match:
        return _normalize_source_text(cleaned)
    decl = cleaned[match.start() :].strip()
    decl = re.sub(r":=\s*by\b.*$", "", decl, flags=re.DOTALL).strip()
    decl = re.sub(r":=\s*$", "", decl).strip()
    return decl


def _artifact(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("semantic_equivalence_artifact")
    return value if isinstance(value, dict) else {}


def lean_statement_for_novelty(row: dict[str, Any]) -> str:
    """Pick the best Lean declaration-like statement available on a row."""
    artifact = _artifact(row)
    candidates = [
        str(artifact.get("lean_statement", "") or ""),
        str(row.get("lean_statement", "") or ""),
    ]
    for candidate in candidates:
        if _DECL_RE.search(candidate or ""):
            return _extract_decl(candidate)
    return _extract_decl(candidates[0] or candidates[1])


def _source_fallback_text(row: dict[str, Any]) -> str:
    artifact = _artifact(row)
    context = row.get("context_pack")
    if not isinstance(context, dict):
        context = {}
    parts = [
        str(artifact.get("normalized_natural_language_theorem", "") or row.get("normalized_natural_language_theorem", "") or ""),
        str(artifact.get("extracted_conclusion", "") or row.get("extracted_conclusion", "") or ""),
        str(artifact.get("original_latex_theorem", "") or row.get("original_latex_theorem", "") or context.get("original_latex_theorem", "") or ""),
    ]
    assumptions = artifact.get("extracted_assumptions") or row.get("extracted_assumptions") or []
    if isinstance(assumptions, list):
        parts.extend(str(x) for x in assumptions if str(x).strip())
    elif str(assumptions).strip():
        parts.append(str(assumptions))
    return _normalize_source_text(" ".join(part for part in parts if part))


def canonical_statement_for_row(row: dict[str, Any]) -> tuple[str, str, str]:
    """Return (canonical statement, Lean candidate, canonicalization method)."""
    lean = lean_statement_for_novelty(row)
    canonical = canonicalize_lean_statement(lean)
    if canonical and _DECL_RE.search(lean or ""):
        return canonical, lean, "lean_canonicalization"
    fallback = _source_fallback_text(row)
    if fallback:
        return f"source_claim: {fallback}", lean, "source_artifact_fallback"
    return "", lean, "unavailable"


def statement_fingerprint(canonical_statement: str) -> str:
    return hashlib.sha256((canonical_statement or "").encode("utf-8")).hexdigest()


def record_from_row(
    row: dict[str, Any],
    *,
    paper_id: str,
    source_index: int,
    source_ledger: str = "",
) -> StatementRecord | None:
    theorem_name = str(row.get("theorem_name", "") or row.get("name", "") or f"row_{source_index}").strip()
    canonical, lean, _method = canonical_statement_for_row(row)
    if not theorem_name or not canonical:
        return None
    retrieval_text = statement_text_from_row(row) or canonical
    return StatementRecord(
        paper_id=paper_id,
        theorem_name=theorem_name,
        source_index=source_index,
        canonical_statement=canonical,
        statement_fingerprint=statement_fingerprint(canonical),
        canonical_theorem_id=canonical_theorem_id(
            lean_statement=lean or canonical,
            theorem_name=theorem_name,
            paper_id=paper_id,
        ),
        claim_shape=canonical_claim_shape(lean or canonical),
        retrieval_text=retrieval_text,
        lean_statement=lean,
        source_ledger=source_ledger,
    )


def load_corpus_records(
    ledger_dir: str | Path,
    *,
    include_papers: set[str] | None = None,
    exclude_papers: set[str] | None = None,
) -> list[StatementRecord]:
    root = Path(ledger_dir)
    if not root.exists():
        return []
    include_papers = include_papers or set()
    exclude_papers = exclude_papers or set()
    records: list[StatementRecord] = []
    for path in sorted(root.glob("*.json")):
        payload = _read_json(path)
        paper_id = _ledger_paper_id(path, payload)
        if include_papers and paper_id not in include_papers:
            continue
        if paper_id in exclude_papers:
            continue
        for idx, row in enumerate(_ledger_entries(payload)):
            rec = record_from_row(row, paper_id=paper_id, source_index=idx, source_ledger=str(path))
            if rec is not None:
                records.append(rec)
    return records


def novelty_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(row.get("novelty_status", "unknown") or "unknown") for row in rows if isinstance(row, dict))
    corpus_counts = Counter(str(row.get("corpus_duplicate_status", "unknown") or "unknown") for row in rows if isinstance(row, dict))
    mathlib_counts = Counter(str(row.get("mathlib_novelty_status", "unknown") or "unknown") for row in rows if isinstance(row, dict))
    identity_counts = Counter(str(row.get("identity_status", "unknown") or "unknown") for row in rows if isinstance(row, dict))
    for status in NOVELTY_STATUSES:
        counts.setdefault(status, 0)
    for status in IDENTITY_STATUSES:
        identity_counts.setdefault(status, 0)
    examples: dict[str, list[dict[str, Any]]] = {status: [] for status in NOVELTY_STATUSES}
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = str(row.get("novelty_status", "unknown") or "unknown")
        if status not in examples or len(examples[status]) >= 5:
            continue
        evidence = row.get("novelty_evidence") if isinstance(row.get("novelty_evidence"), dict) else {}
        examples[status].append(
            {
                "theorem_name": str(row.get("theorem_name", "") or ""),
                "statement_fingerprint": str(row.get("statement_fingerprint", "") or ""),
                "reason": str(evidence.get("reason", "") or ""),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "counts": {status: int(counts.get(status, 0)) for status in NOVELTY_STATUSES},
        "corpus_duplicate_status_counts": dict(corpus_counts),
        "mathlib_novelty_status_counts": dict(mathlib_counts),
        "identity_status_counts": dict(identity_counts),
        "total": sum(int(counts.get(status, 0)) for status in NOVELTY_STATUSES),
        "examples": examples,
    }


def _split_novelty_status(status: str) -> tuple[str, str]:
    value = str(status or "unknown")
    if value == "mathlib_overlap":
        return "unique_or_unknown", "mathlib_overlap"
    if value == "new_candidate":
        return "unique_or_unknown", "new_candidate"
    if value == "duplicate_in_corpus":
        return "exact_duplicate", "unknown"
    if value == "semantic_near_duplicate":
        return "semantic_near_duplicate", "unknown"
    return "unknown", "unknown"


def _identity_payload(
    *,
    status: str,
    method: str,
    matches: list[dict[str, Any]],
    mathlib_evidence: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    checks_run = {str(item) for item in mathlib_evidence.get("checks_run", []) or []}
    identity_status = "unknown"
    if status in {"mathlib_overlap", "duplicate_in_corpus"}:
        identity_status = "same_statement"
    elif status == "semantic_near_duplicate":
        identity_status = "near_duplicate"
    elif status == "new_candidate" and checks_run:
        identity_status = "distinct_candidate"
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "canonical_fingerprint_match": method in {"canonical_fingerprint", "mathlib_fingerprint"},
        "semantic_near_duplicate": status == "semantic_near_duplicate",
        "mathlib_fingerprint_check": "mathlib_fingerprint" in checks_run,
        "mathlib_semantic_check": "mathlib_semantic_index" in checks_run,
        "mathlib_checks_run": sorted(checks_run),
        "matches": matches[:5],
        "human_review_required": identity_status in {"near_duplicate", "unknown"},
    }
    return identity_status, evidence


class MathlibEvidence:
    def __init__(
        self,
        *,
        project_root: Path | None = None,
        mathlib_seed: Path | None = None,
        mathlib_index: Path | None = None,
        run_lean_exact: bool = False,
        lean_timeout_s: int = 10,
        semantic_threshold: float = 0.86,
        enable_semantic_index: bool = False,
    ) -> None:
        self.project_root = project_root
        self.mathlib_seed = mathlib_seed
        self.mathlib_index = mathlib_index
        self.run_lean_exact = run_lean_exact
        self.lean_timeout_s = lean_timeout_s
        self.semantic_threshold = semantic_threshold
        self.enable_semantic_index = enable_semantic_index
        self._exact_by_fingerprint: dict[str, list[dict[str, str]]] | None = None
        self._retriever: PremiseRetriever | None = None
        self._index_available: bool | None = None

    def _load_exact(self) -> dict[str, list[dict[str, str]]]:
        if self._exact_by_fingerprint is not None:
            return self._exact_by_fingerprint
        grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
        for path in [self.mathlib_seed, self.mathlib_index / "entries.jsonl" if self.mathlib_index else None]:
            if path is None or not path.exists():
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except Exception:
                    continue
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("theorem_name") or raw.get("name") or "").strip()
                stmt = str(raw.get("lean_statement") or raw.get("statement") or "").strip()
                canonical = canonicalize_lean_statement(stmt)
                if not name or not canonical:
                    continue
                grouped[statement_fingerprint(canonical)].append(
                    {
                        "theorem_name": name,
                        "source": str(path),
                        "canonical_statement": canonical,
                    }
                )
        self._exact_by_fingerprint = grouped
        return grouped

    def _load_retriever(self) -> PremiseRetriever | None:
        if self._index_available is False:
            return None
        if self._retriever is not None:
            return self._retriever
        if self.mathlib_index is None or not self.mathlib_index.exists():
            self._index_available = False
            return None
        try:
            self._retriever = PremiseRetriever.load(self.mathlib_index)
            self._index_available = True
            return self._retriever
        except Exception:
            self._index_available = False
            return None

    def check(self, record: StatementRecord) -> dict[str, Any]:
        checks_run: list[str] = []
        exact_source_available = any(
            path is not None and path.exists()
            for path in [self.mathlib_seed, self.mathlib_index / "entries.jsonl" if self.mathlib_index else None]
        )
        if exact_source_available:
            checks_run.append("mathlib_fingerprint")
        exact_hits = self._load_exact().get(record.statement_fingerprint, [])
        if exact_hits:
            return {
                "matched": True,
                "method": "mathlib_fingerprint",
                "reason": "canonical statement fingerprint matched Mathlib seed/index",
                "matches": exact_hits[:5],
                "checks_run": checks_run,
            }

        if self.run_lean_exact and self.project_root is not None and record.lean_statement:
            try:
                from mathlib_contrib import check_novelty

                result = check_novelty(
                    record.lean_statement,
                    project_root=self.project_root,
                    lean_timeout=self.lean_timeout_s,
                    run_exact_search=True,
                    run_semantic_check=False,
                )
                checks_run.append("lean_exact_check")
                if result.get("novel") is False:
                    return {
                        "matched": True,
                        "method": f"lean_{result.get('method', 'mathlib_check')}",
                        "reason": str(result.get("detail", "")),
                        "stages": result.get("stages", {}),
                        "checks_run": checks_run,
                    }
            except Exception as exc:
                return {
                    "matched": False,
                    "method": "lean_mathlib_check_error",
                    "reason": str(exc),
                    "unavailable": True,
                    "checks_run": checks_run,
                }

        retriever = self._load_retriever() if self.enable_semantic_index else None
        if retriever is not None:
            checks_run.append("mathlib_semantic_index")
            try:
                hits = retriever.query(record.retrieval_text or record.canonical_statement, top_k=3)
            except Exception:
                hits = []
            filtered = [
                {
                    "theorem_name": h.name,
                    "score": round(float(h.score), 4),
                    "statement": h.statement[:500],
                }
                for h in hits
                if float(h.score) >= self.semantic_threshold
            ]
            if filtered:
                return {
                    "matched": True,
                    "method": "mathlib_semantic_index",
                    "reason": "semantic retrieval score crossed Mathlib overlap threshold",
                    "threshold": self.semantic_threshold,
                    "matches": filtered,
                    "checks_run": checks_run,
                }

        unavailable: list[str] = []
        if self.mathlib_seed is None or not self.mathlib_seed.exists():
            unavailable.append("mathlib_seed_missing")
        if self.mathlib_index is None or not self.mathlib_index.exists():
            unavailable.append("mathlib_index_missing")
        if not self.run_lean_exact:
            unavailable.append("lean_exact_check_disabled")
        if not self.enable_semantic_index:
            unavailable.append("mathlib_semantic_index_disabled")
        return {
            "matched": False,
            "method": "mathlib_no_overlap_found",
            "reason": "no Mathlib overlap detected by available checks",
            "unavailable_checks": unavailable,
            "checks_run": checks_run,
        }


def _same_statement(a: StatementRecord, b: StatementRecord) -> bool:
    return a.paper_id == b.paper_id and a.theorem_name == b.theorem_name and a.source_index == b.source_index


def _semantic_corpus_hits(
    targets: list[StatementRecord],
    corpus: list[StatementRecord],
    *,
    threshold: float,
    top_k: int,
    encoder_name: str | None,
) -> dict[str, list[dict[str, Any]]]:
    if not targets or len(corpus) <= 1:
        return {}
    entries = [
        PremiseEntry(
            name=record.statement_id,
            statement=record.retrieval_text or record.canonical_statement,
            namespace=record.paper_id,
            source_file=record.source_ledger,
        )
        for record in corpus
    ]
    try:
        retriever = PremiseRetriever.build(entries, encoder_name=encoder_name)
    except Exception:
        if encoder_name == "hash":
            return {}
        retriever = PremiseRetriever.build(entries, encoder_name="hash")

    by_id = {record.statement_id: record for record in corpus}
    out: dict[str, list[dict[str, Any]]] = {}
    for record in targets:
        hits = retriever.query(record.retrieval_text or record.canonical_statement, top_k=max(top_k + 5, top_k * 3))
        selected: list[dict[str, Any]] = []
        for hit in hits:
            other = by_id.get(hit.name)
            if other is None or _same_statement(record, other):
                continue
            if other.statement_fingerprint == record.statement_fingerprint:
                continue
            if float(hit.score) < threshold:
                continue
            selected.append({**other.short_ref, "score": round(float(hit.score), 4)})
            if len(selected) >= top_k:
                break
        if selected:
            out[record.statement_id] = selected
    return out


def annotate_entries(
    entries: list[dict[str, Any]],
    *,
    paper_id: str,
    corpus_records: list[StatementRecord] | None = None,
    project_root: str | Path | None = None,
    ledger_dir: str | Path | None = None,
    mathlib_seed: str | Path | None = None,
    mathlib_index: str | Path | None = None,
    run_lean_mathlib_check: bool = False,
    semantic_threshold: float = 0.78,
    mathlib_semantic_threshold: float = 0.86,
    enable_mathlib_semantic_index: bool = False,
    encoder_name: str | None = "hash",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Attach novelty fields to ledger rows and return (rows, summary)."""
    target_records: list[StatementRecord] = []
    target_by_id: dict[str, StatementRecord] = {}
    for idx, row in enumerate(entries):
        if not isinstance(row, dict):
            continue
        rec = record_from_row(row, paper_id=paper_id, source_index=idx)
        if rec is not None:
            target_records.append(rec)
            target_by_id[rec.statement_id] = rec

    extra_records = list(corpus_records or [])
    if not extra_records and ledger_dir is not None:
        extra_records = load_corpus_records(ledger_dir, exclude_papers={paper_id})
    corpus = [*extra_records, *target_records]

    exact_groups: dict[str, list[StatementRecord]] = defaultdict(list)
    for record in corpus:
        exact_groups[record.statement_fingerprint].append(record)

    semantic_hits = _semantic_corpus_hits(
        target_records,
        corpus,
        threshold=semantic_threshold,
        top_k=3,
        encoder_name=encoder_name,
    )

    root = Path(project_root) if project_root is not None else None
    mathlib = MathlibEvidence(
        project_root=root,
        mathlib_seed=Path(mathlib_seed) if mathlib_seed else (root / "output/kg/trusted/mathlib_seed.jsonl" if root else None),
        mathlib_index=Path(mathlib_index) if mathlib_index else (root / "data/mathlib_embeddings" if root else None),
        run_lean_exact=run_lean_mathlib_check,
        semantic_threshold=mathlib_semantic_threshold,
        enable_semantic_index=enable_mathlib_semantic_index,
    )

    annotated: list[dict[str, Any]] = []
    for idx, row in enumerate(entries):
        out = dict(row)
        rec = record_from_row(row, paper_id=paper_id, source_index=idx)
        if rec is None:
            out["novelty_status"] = "unknown"
            out["corpus_duplicate_status"] = "unknown"
            out["mathlib_novelty_status"] = "unknown"
            out["identity_status"] = "unknown"
            out["identity_evidence"] = {
                "schema_version": SCHEMA_VERSION,
                "canonical_fingerprint_match": False,
                "semantic_near_duplicate": False,
                "mathlib_fingerprint_check": False,
                "mathlib_semantic_check": False,
                "mathlib_checks_run": [],
                "matches": [],
                "human_review_required": True,
            }
            out["novelty_evidence"] = {
                "schema_version": SCHEMA_VERSION,
                "reason": "statement_unavailable",
            }
            annotated.append(out)
            continue

        out["canonical_statement"] = rec.canonical_statement
        out["statement_fingerprint"] = rec.statement_fingerprint
        out["canonical_theorem_id"] = rec.canonical_theorem_id
        out["claim_shape"] = rec.claim_shape

        method = "unknown"
        status = "unknown"
        reason = ""
        matches: list[dict[str, Any]] = []
        mathlib_evidence = mathlib.check(rec)

        if mathlib_evidence.get("matched"):
            status = "mathlib_overlap"
            method = str(mathlib_evidence.get("method", "mathlib_overlap"))
            reason = str(mathlib_evidence.get("reason", "Mathlib overlap detected"))
            matches = list(mathlib_evidence.get("matches", []) or [])
        else:
            exact_others = [other for other in exact_groups.get(rec.statement_fingerprint, []) if not _same_statement(rec, other)]
            if exact_others:
                status = "duplicate_in_corpus"
                method = "canonical_fingerprint"
                reason = "canonical statement fingerprint matched another corpus statement"
                matches = [other.short_ref for other in exact_others[:5]]
            elif semantic_hits.get(rec.statement_id):
                status = "semantic_near_duplicate"
                method = "statement_retrieval"
                reason = "semantic corpus retrieval score crossed near-duplicate threshold"
                matches = semantic_hits[rec.statement_id]
            elif rec.canonical_statement and mathlib_evidence.get("checks_run"):
                status = "new_candidate"
                method = "no_overlap_detected"
                reason = "no Mathlib, exact corpus, or semantic corpus overlap detected by available checks"
            elif rec.canonical_statement:
                status = "unknown"
                method = "mathlib_checks_unavailable"
                reason = "novelty cannot be claimed because no Mathlib overlap check ran"
            else:
                status = "unknown"
                method = "statement_unavailable"
                reason = "no canonical statement available"

        out["novelty_status"] = status
        corpus_status, mathlib_status = _split_novelty_status(status)
        out["corpus_duplicate_status"] = corpus_status
        out["mathlib_novelty_status"] = mathlib_status
        identity_status, identity_evidence = _identity_payload(
            status=status,
            method=method,
            matches=matches,
            mathlib_evidence=mathlib_evidence,
        )
        out["identity_status"] = identity_status
        out["identity_evidence"] = identity_evidence
        out["novelty_evidence"] = {
            "schema_version": SCHEMA_VERSION,
            "method": method,
            "reason": reason,
            "matches": matches,
            "mathlib": mathlib_evidence,
            "thresholds": {
                "semantic_near_duplicate": semantic_threshold,
                "mathlib_semantic_overlap": mathlib_semantic_threshold,
            },
        }
        annotated.append(out)

    return annotated, novelty_summary(annotated)


def _write_ledger_like(original: Any, entries: list[dict[str, Any]]) -> Any:
    if isinstance(original, dict):
        out = dict(original)
        if isinstance(original.get("entries"), list):
            out["entries"] = entries
        elif isinstance(original.get("rows"), list):
            out["rows"] = entries
        elif isinstance(original.get("results"), list):
            out["results"] = entries
        else:
            out["entries"] = entries
        return out
    return entries


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Annotate verification ledgers with statement novelty evidence")
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--paper-id", default="")
    parser.add_argument("--ledger-dir", default="")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--mathlib-seed", default="")
    parser.add_argument("--mathlib-index", default="")
    parser.add_argument("--run-lean-mathlib-check", action="store_true")
    parser.add_argument("--enable-mathlib-semantic-index", action="store_true")
    parser.add_argument("--encoder", default="hash")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    payload = _read_json(args.ledger)
    entries = _ledger_entries(payload)
    paper_id = args.paper_id or _ledger_paper_id(args.ledger, payload)
    annotated, summary = annotate_entries(
        entries,
        paper_id=paper_id,
        project_root=args.project_root,
        ledger_dir=args.ledger_dir or None,
        mathlib_seed=args.mathlib_seed or None,
        mathlib_index=args.mathlib_index or None,
        run_lean_mathlib_check=bool(args.run_lean_mathlib_check),
        enable_mathlib_semantic_index=bool(args.enable_mathlib_semantic_index),
        encoder_name=args.encoder,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(_write_ledger_like(payload, annotated), indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"ok": True, "paper_id": paper_id, "rows": len(annotated), "novelty_summary": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
