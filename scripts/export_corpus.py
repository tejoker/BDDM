#!/usr/bin/env python3
"""Export stable theorem-level corpus rows from DESol paper artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from canonicalization import canonical_record
from source_evidence_resolver import resolve_evidence_row
from statement_alignment import classify_row_alignment
from theorem_extractor import extract_theorems


SCHEMA_VERSION = "corpus_row.v1"
SUMMARY_SCHEMA_VERSION = "corpus_export_summary.v1"
DATASET_FAMILY = "desol_stable_corpus"
DEFAULT_RELEASE_BUNDLE_DIR = Path("reproducibility/full_paper_reports")
DEFAULT_LEDGER_DIR = DEFAULT_RELEASE_BUNDLE_DIR
DEFAULT_REPORT_DIR = DEFAULT_RELEASE_BUNDLE_DIR
DEFAULT_EVIDENCE_DIR = Path("reproducibility/paper_agnostic_golden10_results")
DEFAULT_OUT_JSONL = Path("output/corpus/stable_corpus.jsonl")
DEFAULT_OUT_SUMMARY = Path("output/corpus/stable_corpus_summary.json")

_DECL_RE = re.compile(
    r"(?ms)^\s*(?:theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_'.]*)\b.*?(?=^\s*(?:theorem|lemma|def|axiom|namespace|end|section)\b|^\s*--\s*\[|\Z)"
)
_ARXIV_RE = re.compile(r"(\d{4}\.\d{4,5}(?:v\d+)?)")
_SCHEMA_ROOT = Path(__file__).resolve().parent.parent / "schemas"
_ROW_REQUIRED_FIELDS = {
    "row_id",
    "arxiv_id",
    "theorem_id",
    "toolchain_hash",
    "schema_version",
    "dataset_family",
    "dataset_tier",
    "training_tier",
    "source_latex",
    "lean_statement",
    "status",
    "proof_method",
    "trust_tier",
    "source_span",
    "artifact_paths",
    "provenance",
}
_SUMMARY_REQUIRED_FIELDS = {
    "schema_version",
    "dataset_family",
    "rows",
    "papers",
    "status_counts",
    "dataset_tier_counts",
    "training_tier_counts",
    "gold_proof_count",
    "verified_proven_count",
    "warnings",
}


def _read_json(path: Path, warnings: list[str] | None = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        if warnings is not None:
            warnings.append(f"json_unreadable:{path}:{type(exc).__name__}")
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def _schema_path(schema_version: str) -> Path:
    if schema_version == SCHEMA_VERSION:
        return _SCHEMA_ROOT / "corpus_row.v1.schema.json"
    if schema_version == SUMMARY_SCHEMA_VERSION:
        return _SCHEMA_ROOT / "corpus_export_summary.v1.schema.json"
    return _SCHEMA_ROOT / f"{schema_version}.schema.json"


def _json_type_ok(value: Any, expected: Any) -> bool:
    types = expected if isinstance(expected, list) else [expected]
    for typ in types:
        if typ == "string" and isinstance(value, str):
            return True
        if typ == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if typ == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if typ == "boolean" and isinstance(value, bool):
            return True
        if typ == "object" and isinstance(value, dict):
            return True
        if typ == "array" and isinstance(value, list):
            return True
        if typ == "null" and value is None:
            return True
    return False


def validate_against_schema(payload: dict[str, Any], schema_path: Path) -> list[str]:
    """Validate the required/type subset of the checked-in JSON Schemas."""
    schema = _read_json(schema_path)
    if not isinstance(schema, dict):
        return [f"schema_unreadable:{schema_path}"]
    errors: list[str] = []
    for key in schema.get("required", []):
        if key not in payload:
            errors.append(f"missing_required:{key}")
    properties = schema.get("properties", {})
    if isinstance(properties, dict):
        for key, spec in properties.items():
            if key not in payload or not isinstance(spec, dict) or "type" not in spec:
                continue
            if not _json_type_ok(payload[key], spec["type"]):
                errors.append(f"type_mismatch:{key}:expected_{spec['type']}")
    return errors


def validate_corpus_export(rows: list[dict[str, Any]], summary: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    row_schema = _schema_path(SCHEMA_VERSION)
    summary_schema = _schema_path(SUMMARY_SCHEMA_VERSION)
    for idx, row in enumerate(rows):
        missing = sorted(_ROW_REQUIRED_FIELDS - set(row))
        for key in missing:
            errors.append(f"row[{idx}]:missing_required:{key}")
        for error in validate_against_schema(row, row_schema):
            errors.append(f"row[{idx}]:{error}")
    for key in sorted(_SUMMARY_REQUIRED_FIELDS - set(summary)):
        errors.append(f"summary:missing_required:{key}")
    for error in validate_against_schema(summary, summary_schema):
        errors.append(f"summary:{error}")
    return errors


def _safe_id(value: str) -> str:
    return str(value).replace("/", "_").replace(":", "_")


def _safe_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _normalize_ws(text: str, *, limit: int = 20000) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())[:limit]


def _entries(payload: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if isinstance(payload, list):
        return {"schema_version": "legacy"}, [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        rows = payload.get("entries", [])
        if isinstance(rows, list):
            return {k: v for k, v in payload.items() if k != "entries"}, [row for row in rows if isinstance(row, dict)]
    return {"schema_version": "unreadable"}, []


def _paper_id(meta: dict[str, Any], path: Path) -> str:
    for key in ("paper_id", "arxiv_id", "source_paper"):
        value = str(meta.get(key, "")).strip()
        if value:
            return value
    match = _ARXIV_RE.search(str(path))
    return match.group(1) if match else path.stem


def _ledger_files(paths: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for root in paths:
        if not root.exists():
            continue
        if root.is_file():
            files.append(root)
        else:
            files.extend(sorted(root.glob("*.json")))
            files.extend(sorted(root.rglob("verification_ledger.json")))
    return sorted(dict.fromkeys(files), key=lambda p: str(p))


def _mathlib_pin(project_root: Path) -> dict[str, str]:
    path = project_root / "lakefile.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"name": "mathlib", "git": "", "rev": ""}

    for block in re.split(r"(?m)^\s*\[\[require\]\]\s*$", text):
        if re.search(r'(?m)^\s*name\s*=\s*"mathlib"\s*$', block):
            git = re.search(r'(?m)^\s*git\s*=\s*"([^"]*)"', block)
            rev = re.search(r'(?m)^\s*rev\s*=\s*"([^"]*)"', block)
            return {
                "name": "mathlib",
                "git": git.group(1) if git else "",
                "rev": rev.group(1) if rev else "",
            }
    return {"name": "mathlib", "git": "", "rev": ""}


def toolchain_metadata(project_root: Path, *, pipeline_commit: str = "") -> dict[str, Any]:
    try:
        lean_toolchain = (project_root / "lean-toolchain").read_text(encoding="utf-8").strip()
    except OSError:
        lean_toolchain = ""
    metadata: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "lean_toolchain": lean_toolchain,
        "mathlib": _mathlib_pin(project_root),
    }
    if pipeline_commit:
        metadata["pipeline_commit"] = pipeline_commit
    digest_payload = {
        "schema_version": metadata["schema_version"],
        "lean_toolchain": metadata["lean_toolchain"],
        "mathlib": metadata["mathlib"],
    }
    metadata["toolchain_hash"] = hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()[:24]
    return metadata


def parse_imports(lean_file: Path) -> list[str]:
    try:
        text = lean_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    imports: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("import "):
            module = stripped.split("--", 1)[0].strip()[len("import ") :].strip()
            if module and module not in imports:
                imports.append(module)
    return imports


def _decl_name(theorem_name: str) -> str:
    return (theorem_name or "").strip().rsplit(".", 1)[-1]


def _extract_decl_from_file(lean_file: str, theorem_name: str) -> str:
    path = Path(lean_file)
    if not theorem_name or not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    want = _decl_name(theorem_name)
    for match in _DECL_RE.finditer(text):
        found = match.group(1).rsplit(".", 1)[-1]
        if found == want:
            return match.group(0).strip()
    return ""


def _proof_from_decl(decl: str) -> str:
    marker = ":= by"
    if marker not in decl:
        return ""
    proof = decl.split(marker, 1)[1].strip()
    if not proof or re.search(r"\bsorry\b", proof):
        return ""
    return proof


def _line_col(text: str, offset: int) -> tuple[int, int]:
    prefix = text[:offset]
    line = prefix.count("\n") + 1
    last_newline = prefix.rfind("\n")
    if last_newline < 0:
        return line, offset + 1
    return line, offset - last_newline


def _span_for_match(text: str, source_file: str, start: int, end: int, confidence: str) -> dict[str, Any]:
    start_line, start_col = _line_col(text, start)
    end_line, end_col = _line_col(text, end)
    return {
        "source_file": source_file,
        "start_byte": len(text[:start].encode("utf-8")),
        "end_byte": len(text[:end].encode("utf-8")),
        "start_line": start_line,
        "start_col": start_col,
        "end_line": end_line,
        "end_col": end_col,
        "span_confidence": confidence,
    }


def _source_span_from_evidence(row: dict[str, Any]) -> dict[str, Any]:
    span = row.get("source_span")
    if isinstance(span, dict) and span:
        out = dict(span)
        out.setdefault("span_confidence", "exact_extractor")
        return out
    return {}


def _string_recovered_span_from_evidence(row: dict[str, Any]) -> dict[str, Any]:
    source_file = _safe_text(row.get("source_file"))
    statement = _safe_text(row.get("statement"))
    if not source_file or not statement:
        return {}
    path = Path(source_file)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    idx = text.find(statement)
    if idx < 0:
        return {}
    return _span_for_match(text, source_file, idx, idx + len(statement), "string_recovered_exact")


def _source_span_to_dict(span: Any) -> dict[str, Any]:
    if span is None:
        return {}
    return {
        "source_file": str(getattr(span, "source_file", "")),
        "start_byte": int(getattr(span, "start_byte", -1)),
        "end_byte": int(getattr(span, "end_byte", -1)),
        "start_line": int(getattr(span, "start_line", -1)),
        "start_col": int(getattr(span, "start_col", -1)),
        "end_line": int(getattr(span, "end_line", -1)),
        "end_col": int(getattr(span, "end_col", -1)),
    }


def _reverified_extractor_span(evidence_row: dict[str, Any], source_latex: str) -> dict[str, Any]:
    source_file = _safe_text(evidence_row.get("source_file")).strip()
    if not source_file:
        return {}
    path = Path(source_file)
    if not path.exists():
        return {}
    try:
        extracted = extract_theorems(path)
    except Exception:
        return {}
    if not extracted:
        return {}
    candidates = []
    for idx, entry in enumerate(extracted):
        candidates.append(
            {
                "resolver_index": idx,
                "kind": entry.kind,
                "name": entry.name,
                "label": entry.label,
                "statement": entry.statement,
                "source_file": entry.source_file,
                "env_name": entry.env_name,
                "source_span_id": entry.source_span_id,
            }
        )
    selected, match_evidence = resolve_evidence_row(
        paper_id="",
        ledger_row=evidence_row,
        source_latex=_safe_text(evidence_row.get("statement")).strip() or source_latex,
        evidence_rows=candidates,
    )
    if match_evidence.get("match_status") != "matched":
        return {}
    entry = extracted[int(selected["resolver_index"])]
    span = _source_span_to_dict(entry.source_span)
    if not span:
        return {}
    span["span_confidence"] = "exact_extractor_reverified"
    span["source_span_id"] = entry.source_span_id
    span["env_name"] = entry.env_name
    span["label"] = entry.label
    span["span_start"] = entry.span_start
    span["span_end"] = entry.span_end
    span["body_start"] = entry.body_start
    span["body_end"] = entry.body_end
    return span


def _missing_span(source_file: str = "") -> dict[str, Any]:
    return {
        "source_file": source_file,
        "start_byte": -1,
        "end_byte": -1,
        "start_line": -1,
        "start_col": -1,
        "end_line": -1,
        "end_col": -1,
        "span_confidence": "missing",
    }


class EvidenceIndex:
    def __init__(self) -> None:
        self.by_paper: dict[str, list[dict[str, Any]]] = {}
        self.evidence_paths: dict[str, str] = {}

    @classmethod
    def from_roots(cls, roots: Iterable[Path]) -> "EvidenceIndex":
        index = cls()
        for root in roots:
            if not root.exists():
                continue
            candidates: list[Path]
            if root.is_file():
                candidates = [root]
            else:
                candidates = sorted(root.glob("*/extracted_theorems.json"))
                direct = root / "extracted_theorems.json"
                if direct.exists():
                    candidates.append(direct)
            for path in candidates:
                payload = _read_json(path)
                if not isinstance(payload, dict):
                    continue
                pid = str(payload.get("paper_id", "") or path.parent.name).strip()
                rows = payload.get("entries", [])
                if not pid or not isinstance(rows, list):
                    continue
                clean = [row for row in rows if isinstance(row, dict)]
                index.by_paper.setdefault(pid, []).extend(clean)
                index.evidence_paths[pid] = str(path)
        return index

    def match_with_evidence(self, paper_id: str, ledger_row: dict[str, Any], source_latex: str) -> tuple[dict[str, Any], dict[str, Any]]:
        rows = self.by_paper.get(paper_id, [])
        row, evidence = resolve_evidence_row(
            paper_id=paper_id,
            ledger_row=ledger_row,
            source_latex=source_latex,
            evidence_rows=rows,
        )
        return row, evidence

    def match(self, paper_id: str, ledger_row: dict[str, Any], source_latex: str) -> dict[str, Any]:
        row, _evidence = self.match_with_evidence(paper_id, ledger_row, source_latex)
        return row


def _report_index(report_roots: Iterable[Path]) -> dict[str, tuple[Path, dict[str, Any]]]:
    reports: dict[str, tuple[Path, dict[str, Any]]] = {}
    for root in report_roots:
        if not root.exists():
            continue
        if root.is_file():
            candidates = [root]
        else:
            candidates = [
                *sorted(root.glob("*.json")),
                *sorted(root.rglob("suite_report.json")),
                *sorted(root.rglob("*_suite_report.json")),
            ]
        for path in candidates:
            payload = _read_json(path)
            if not isinstance(payload, dict):
                continue
            pid = str(payload.get("paper_id", "")).strip()
            if not pid:
                match = _ARXIV_RE.search(path.name)
                pid = match.group(1) if match else ""
            if pid and pid not in reports:
                reports[pid] = (path, payload)
    return reports


def _row_rank(row: dict[str, Any]) -> tuple[int, int, int, str]:
    artifacts = row.get("artifact_paths") if isinstance(row.get("artifact_paths"), dict) else {}
    ledger = str(artifacts.get("ledger", ""))
    status = str(row.get("status", ""))
    proof_method = str(row.get("proof_method", ""))
    release_rank = 1 if "reproducibility/full_paper_reports" in ledger else 0
    verified_rank = 1 if status == "FULLY_PROVEN" and proof_method == "lean_verified" else 0
    source_rank = 1 if str(row.get("source_latex", "")).strip() else 0
    return (release_rank, verified_rank, source_rank, ledger)


def _row_conflict(a: dict[str, Any], b: dict[str, Any]) -> bool:
    for key in ("status", "lean_statement", "canonical_statement", "proof_method", "proof_text"):
        if str(a.get(key, "")) != str(b.get(key, "")):
            return True
    return False


def _dedupe_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("row_id", "")), []).append(row)

    out: list[dict[str, Any]] = []
    conflict_examples: list[dict[str, Any]] = []
    duplicate_row_id_count = 0
    conflict_count = 0
    for row_id, group in grouped.items():
        if len(group) == 1:
            out.append(group[0])
            continue
        duplicate_row_id_count += len(group) - 1
        selected = sorted(group, key=_row_rank, reverse=True)[0]
        conflicts = [row for row in group if row is not selected and _row_conflict(selected, row)]
        if conflicts:
            conflict_count += 1
        discarded = [row for row in group if row is not selected]
        selected = dict(selected)
        selected["deduplication"] = {
            "logical_key": "arxiv_id,theorem_id,toolchain_hash",
            "discarded_duplicate_count": len(discarded),
            "conflict": bool(conflicts),
            "discarded_artifacts": [
                {
                    "ledger": str((row.get("artifact_paths") or {}).get("ledger", "")),
                    "status": str(row.get("status", "")),
                    "proof_method": str(row.get("proof_method", "")),
                }
                for row in discarded[:20]
            ],
        }
        if conflicts and len(conflict_examples) < 20:
            conflict_examples.append(
                {
                    "row_id": row_id,
                    "arxiv_id": selected.get("arxiv_id", ""),
                    "theorem_id": selected.get("theorem_id", ""),
                    "selected_ledger": str((selected.get("artifact_paths") or {}).get("ledger", "")),
                    "selected_canonical_statement": str(selected.get("canonical_statement", ""))[:500],
                    "discarded": [
                        {
                            "ledger": str((row.get("artifact_paths") or {}).get("ledger", "")),
                            "status": str(row.get("status", "")),
                            "canonical_statement": str(row.get("canonical_statement", ""))[:500],
                        }
                        for row in discarded[:5]
                    ],
                    "discarded_count": len(discarded),
                }
            )
        out.append(selected)

    out.sort(key=lambda row: (row["arxiv_id"], row["theorem_id"], row["toolchain_hash"], row["row_id"]))
    return out, {
        "input_rows_before_dedup": len(rows),
        "duplicate_row_id_count": duplicate_row_id_count,
        "conflict_count": conflict_count,
        "conflict_examples": conflict_examples,
    }


def _artifact_paths(
    *,
    ledger_path: Path,
    report_path: Path | None,
    report: dict[str, Any],
    evidence_path: str = "",
    lean_file: str = "",
) -> dict[str, Any]:
    artifacts: dict[str, Any] = {"ledger": str(ledger_path)}
    if report_path is not None:
        artifacts["report"] = str(report_path)
    if evidence_path:
        artifacts["extracted_theorems"] = evidence_path
    if lean_file:
        artifacts["lean_file"] = lean_file

    keys = (
        "out_lean",
        "results_file",
        "unresolved_pack",
        "missing_lemma_subledger_path",
        "axiom_debt_burndown_path",
        "statement_validity_path",
        "proof_repair_cohort_path",
        "generated_paper_theory_file",
        "paper_theory_manifest",
    )
    for key in keys:
        value = _safe_text(report.get(key)).strip()
        if value:
            artifacts[key] = value

    repro = report.get("reproducibility_bundle")
    if isinstance(repro, dict):
        artifacts["reproducibility_bundle"] = {
            str(k): str(v) for k, v in sorted(repro.items()) if str(v).strip()
        }
    curated = report.get("curated_paper_package")
    if isinstance(curated, dict) and curated:
        artifacts["curated_paper_package"] = curated
    auto_core = report.get("auto_reliable_core")
    if isinstance(auto_core, dict) and auto_core:
        artifacts["auto_reliable_core"] = auto_core
    return artifacts


def _source_fields(row: dict[str, Any], evidence_row: dict[str, Any]) -> tuple[str, str]:
    artifact = row.get("semantic_equivalence_artifact") if isinstance(row.get("semantic_equivalence_artifact"), dict) else {}
    context = row.get("context_pack") if isinstance(row.get("context_pack"), dict) else {}
    source_latex = (
        _safe_text(artifact.get("original_latex_theorem")).strip()
        or _safe_text(context.get("original_latex_theorem")).strip()
        or _safe_text(evidence_row.get("statement")).strip()
    )
    normalized = (
        _safe_text(artifact.get("normalized_natural_language_theorem")).strip()
        or _normalize_ws(source_latex)
    )
    return source_latex, normalized


def _lean_statement(row: dict[str, Any]) -> str:
    artifact = row.get("semantic_equivalence_artifact") if isinstance(row.get("semantic_equivalence_artifact"), dict) else {}
    return _safe_text(artifact.get("lean_statement")).strip() or _safe_text(row.get("lean_statement")).strip()


def _theorem_id(row: dict[str, Any], canonical_id: str) -> tuple[str, str]:
    provenance = row.get("provenance") if isinstance(row.get("provenance"), dict) else {}
    label = str(provenance.get("label", "")).strip()
    if label:
        return label, "provenance.label"
    name = str(row.get("theorem_name", "")).strip()
    if name:
        return name, "theorem_name"
    return canonical_id, "canonical_theorem_id"


def _build_source_span(
    *,
    evidence_row: dict[str, Any],
    source_latex: str,
) -> dict[str, Any]:
    span = _source_span_from_evidence(evidence_row)
    if span:
        return span
    span = _reverified_extractor_span(evidence_row, source_latex)
    if span:
        return span
    span = _string_recovered_span_from_evidence(evidence_row)
    if span:
        return span
    source_file = _safe_text(evidence_row.get("source_file")).strip()
    if source_file and source_latex:
        path = Path(source_file)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if text:
            idx = text.find(source_latex)
            if idx >= 0:
                return _span_for_match(text, source_file, idx, idx + len(source_latex), "string_recovered_exact")
    return _missing_span(source_file)


def _coerce_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _has_paper_claim(row: dict[str, Any]) -> bool:
    joined = "\n".join(
        str(row.get(key, "") or "")
        for key in ("lean_statement", "canonical_statement", "proof_text", "trust_reference", "failure_kind")
    )
    return "PaperClaim" in joined or "paper_claim" in joined.lower()


def _axiom_debt(row: dict[str, Any]) -> list[str]:
    debt = row.get("axiom_debt")
    return _coerce_list(debt)


def _gold_blockers(row: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if str(row.get("status", "")).strip() != "FULLY_PROVEN":
        blockers.append("status_not_fully_proven")
    if str(row.get("proof_method", "")).strip() != "lean_verified":
        blockers.append("proof_method_not_lean_verified")
    if _has_paper_claim(row):
        blockers.append("paper_claim_artifact")
    debt = _axiom_debt(row)
    if debt:
        blockers.append("axiom_or_paper_theory_debt")
    gate_failures = _coerce_list(row.get("gate_failures"))
    if any("domain_assumption" in item or "paper_local" in item for item in gate_failures):
        blockers.append("domain_or_paper_local_gate_failure")
    proof_text = str(row.get("proof_text", "") or "").strip()
    trust_reference = str(row.get("trust_reference", "") or "")
    has_audited_replacement = (
        str(row.get("ledger_role", "")) == "audited_core_replacement"
        and "audited_core_replacement" in trust_reference
        and bool(row.get("proof_countable", True))
    )
    has_direct_proof_text = bool(proof_text) and not re.search(r"\b(?:sorry|admit)\b", proof_text)
    if not has_direct_proof_text and not has_audited_replacement:
        blockers.append("missing_checked_proof_payload")
    return blockers


def _is_verified_proven_row(row: dict[str, Any]) -> bool:
    return (
        str(row.get("status", "")) == "FULLY_PROVEN"
        and str(row.get("proof_method", "")) == "lean_verified"
    )


def _verified_proven_scope(row: dict[str, Any]) -> str:
    if not _is_verified_proven_row(row):
        return "not_verified_proven"
    scope = str(row.get("equivalence_scope", "") or "").strip()
    role = str(row.get("ledger_role", "") or "").strip()
    if scope == "full_source_claim":
        return "full_source_claim"
    if role == "audited_core_replacement":
        return "audited_component"
    if scope:
        return f"scoped:{scope}"
    return "direct_or_unknown_scope"


def _training_tier(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    blockers = _gold_blockers(row)
    if not blockers:
        return "gold_proof", {
            "gold_eligible": True,
            "gold_blockers": [],
            "headline_metric": "verified_proven",
        }
    status = str(row.get("status", "")).strip()
    if status in {"FLAWED", "TRANSLATION_LIMITED"} or str(row.get("failure_kind", "")).strip():
        tier = "diagnostic"
    elif status in {"UNRESOLVED", "AXIOM_BACKED", "INTERMEDIARY_PROVEN"} or _axiom_debt(row):
        tier = "blocker"
    elif str(row.get("lean_statement", "")).strip():
        tier = "diagnostic"
    else:
        tier = "unknown"
    return tier, {
        "gold_eligible": False,
        "gold_blockers": blockers,
        "headline_metric": "verified_proven",
    }


def _novelty_split(row: dict[str, Any]) -> tuple[str, str]:
    status = str(row.get("novelty_status", "") or "unknown").strip() or "unknown"
    mathlib = str(row.get("mathlib_novelty_status", "") or "").strip()
    corpus = str(row.get("corpus_duplicate_status", "") or "").strip()
    if not mathlib:
        if status == "mathlib_overlap":
            mathlib = "mathlib_overlap"
        elif status == "new_candidate":
            mathlib = "new_candidate"
        else:
            mathlib = "unknown"
    if not corpus:
        if status == "duplicate_in_corpus":
            corpus = "exact_duplicate"
        elif status == "semantic_near_duplicate":
            corpus = "semantic_near_duplicate"
        else:
            corpus = "unique_or_unknown"
    return corpus, mathlib


def _mathlib_checks_run(row: dict[str, Any]) -> list[str]:
    novelty_evidence = row.get("novelty_evidence") if isinstance(row.get("novelty_evidence"), dict) else {}
    mathlib = novelty_evidence.get("mathlib") if isinstance(novelty_evidence.get("mathlib"), dict) else {}
    checks = [str(item) for item in mathlib.get("checks_run", [])] if isinstance(mathlib.get("checks_run"), list) else []
    identity_evidence = row.get("identity_evidence") if isinstance(row.get("identity_evidence"), dict) else {}
    checks.extend(
        str(item)
        for item in identity_evidence.get("mathlib_checks_run", [])
        if isinstance(identity_evidence.get("mathlib_checks_run"), list)
    )
    return sorted(set(check for check in checks if check))


def _downgrade_unsupported_novelty(row: dict[str, Any]) -> None:
    if str(row.get("novelty_status", "")) != "new_candidate":
        return
    if _mathlib_checks_run(row):
        return
    evidence = row.get("novelty_evidence") if isinstance(row.get("novelty_evidence"), dict) else {}
    row["novelty_status"] = "unknown"
    row["novelty_evidence"] = {
        **evidence,
        "original_novelty_status": "new_candidate",
        "method": "downgraded_unsupported_new_candidate",
        "reason": "new_candidate requires Mathlib evidence; no Mathlib checks were recorded",
        "mathlib": evidence.get("mathlib", {"checks_run": []}) if isinstance(evidence.get("mathlib"), dict) else {"checks_run": []},
    }


def _identity_defaults(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    status = str(row.get("identity_status", "") or "").strip()
    evidence = row.get("identity_evidence") if isinstance(row.get("identity_evidence"), dict) else {}
    if status:
        return status, evidence
    novelty_status = str(row.get("novelty_status", "") or "unknown")
    mathlib_evidence = row.get("novelty_evidence", {}).get("mathlib", {}) if isinstance(row.get("novelty_evidence"), dict) else {}
    checks_run = [str(item) for item in mathlib_evidence.get("checks_run", [])] if isinstance(mathlib_evidence, dict) else []
    if novelty_status in {"mathlib_overlap", "duplicate_in_corpus"}:
        status = "same_statement"
    elif novelty_status == "semantic_near_duplicate":
        status = "near_duplicate"
    elif novelty_status == "new_candidate" and checks_run:
        status = "distinct_candidate"
    else:
        status = "unknown"
    return status, {
        "schema_version": "1.0.0",
        "canonical_fingerprint_match": novelty_status in {"mathlib_overlap", "duplicate_in_corpus"},
        "semantic_near_duplicate": novelty_status == "semantic_near_duplicate",
        "mathlib_fingerprint_check": "mathlib_fingerprint" in checks_run,
        "mathlib_semantic_check": "mathlib_semantic_index" in checks_run,
        "mathlib_checks_run": checks_run,
        "human_review_required": status in {"near_duplicate", "unknown"},
    }


def _alignment_payload(
    *,
    row: dict[str, Any],
    paper_id: str,
    canonical_theorem_id: str,
    evidence_row: dict[str, Any],
    source_match_evidence: dict[str, Any],
    source_span: dict[str, Any],
) -> dict[str, Any]:
    decision = classify_row_alignment({**row, "canonical_theorem_id": canonical_theorem_id}, paper_id=paper_id)
    alignment_class = decision.alignment_class.value
    reasons = list(decision.reasons)
    match_status = str(source_match_evidence.get("match_status", "missing"))
    if not str(row.get("source_latex", "")).strip() and match_status == "missing":
        alignment_class = "unknown"
        reasons.append("source_text_missing")
    if alignment_class == "exact" and match_status in {"ambiguous", "missing"}:
        alignment_class = "partial"
        reasons.append(f"source_match_{match_status}")
    confidence = float(decision.confidence)
    if match_status == "ambiguous":
        confidence = min(confidence, 0.35)
    elif match_status == "missing":
        confidence = min(confidence, 0.45)
    span_confidence = str(source_span.get("span_confidence", ""))
    if match_status == "ambiguous":
        source_span_quality = "ambiguous"
    elif span_confidence in {"exact_extractor", "exact_extractor_reverified", "reviewed"}:
        source_span_quality = "reviewed" if span_confidence == "reviewed" else "extractor_native"
    elif span_confidence.startswith("string_recovered"):
        source_span_quality = "string_recovered"
    elif span_confidence == "missing" or not span_confidence:
        source_span_quality = "missing"
    else:
        source_span_quality = "unknown"
    alignment_review_required = (
        match_status in {"ambiguous", "missing"}
        or source_span_quality in {"string_recovered", "missing", "ambiguous", "unknown"}
    )
    alignment_gold_eligible = (
        alignment_class == "exact"
        and source_span_quality in {"extractor_native", "reviewed"}
        and match_status == "matched"
        and confidence >= 0.75
    )
    if alignment_gold_eligible:
        alignment_tier = "alignment_gold"
    elif alignment_class in {"exact", "partial", "weaker", "stronger"} and not alignment_review_required:
        alignment_tier = "alignment_candidate"
    elif alignment_review_required:
        alignment_tier = "alignment_review_required"
    else:
        alignment_tier = "alignment_diagnostic"
    return {
        "statement_alignment_class": alignment_class,
        "alignment_confidence": round(confidence, 4),
        "source_span_quality": source_span_quality,
        "alignment_tier": alignment_tier,
        "alignment_gold_eligible": alignment_gold_eligible,
        "alignment_review_required": alignment_review_required,
        "paper_statement_id": str(row.get("paper_statement_id") or decision.paper_statement_id),
        "alignment_pair_id": str(row.get("alignment_pair_id") or decision.alignment_pair_id),
        "alignment_evidence": {
            "source_match": source_match_evidence,
            "source_span_confidence": str(source_span.get("span_confidence", "")),
            "source_theorem_name": str(evidence_row.get("name", "") or ""),
            "matched_source_statement": _safe_text(evidence_row.get("statement")).strip(),
            "reasons": list(dict.fromkeys(reasons)),
            "evidence_sources": list(decision.evidence_sources),
            "paper_text_coverage": decision.paper_text_coverage,
            "lean_text_coverage": decision.lean_text_coverage,
            "conclusion_relation": decision.conclusion_relation,
        },
    }


def build_corpus_rows(
    *,
    ledger_paths: Iterable[Path],
    project_root: Path,
    report_roots: Iterable[Path] = (),
    evidence_roots: Iterable[Path] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    report_by_paper = _report_index(report_roots)
    evidence = EvidenceIndex.from_roots(evidence_roots)
    raw_rows: list[dict[str, Any]] = []
    warnings: list[str] = []

    for ledger_path in _ledger_files(ledger_paths):
        payload = _read_json(ledger_path, warnings)
        meta, entries = _entries(payload)
        arxiv_id = _paper_id(meta, ledger_path)
        if not entries:
            warnings.append(f"ledger_entries_empty:{ledger_path}")
        report_path, report = report_by_paper.get(arxiv_id, (None, {}))
        if not isinstance(report, dict):
            report = {}
        pipeline_commit = str(meta.get("pipeline_commit", "") or report.get("pipeline_commit", "") or "")
        toolchain = toolchain_metadata(project_root, pipeline_commit=pipeline_commit)

        for entry in entries:
            source_preview, _normalized_preview = _source_fields(entry, {})
            evidence_row, source_match_evidence = evidence.match_with_evidence(arxiv_id, entry, source_preview)
            source_latex, normalized_text = _source_fields(entry, evidence_row)
            lean_statement = _lean_statement(entry)
            canonical = canonical_record(
                lean_statement=lean_statement,
                theorem_name=str(entry.get("theorem_name", "")),
                paper_id=arxiv_id,
            )
            theorem_id, theorem_id_source = _theorem_id(entry, str(canonical["canonical_theorem_id"]))
            lean_file = _safe_text(entry.get("lean_file")).strip() or _safe_text(report.get("out_lean")).strip()
            decl = _extract_decl_from_file(lean_file, str(entry.get("theorem_name", ""))) if lean_file else ""
            proof_text = _safe_text(entry.get("proof_text")).strip() or _proof_from_decl(decl)
            imports = parse_imports(Path(lean_file)) if lean_file else []
            if lean_file and not imports:
                warnings.append(f"imports_missing:{arxiv_id}:{lean_file}")
            source_span = _build_source_span(evidence_row=evidence_row, source_latex=source_latex)
            if source_match_evidence.get("match_status") == "ambiguous":
                warnings.append(f"ambiguous_source_match:{arxiv_id}:{entry.get('theorem_name', '')}")
            artifacts = _artifact_paths(
                ledger_path=ledger_path,
                report_path=report_path,
                report=report,
                evidence_path=evidence.evidence_paths.get(arxiv_id, ""),
                lean_file=lean_file,
            )
            provenance = entry.get("provenance") if isinstance(entry.get("provenance"), dict) else {}
            row_id_payload = {
                "arxiv_id": arxiv_id,
                "theorem_id": theorem_id,
                "toolchain_hash": toolchain["toolchain_hash"],
            }
            row_id = hashlib.sha256(
                json.dumps(row_id_payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
            ).hexdigest()[:32]
            row = {
                    "schema_version": SCHEMA_VERSION,
                    "dataset_family": DATASET_FAMILY,
                    "row_id": row_id,
                    "arxiv_id": arxiv_id,
                    "theorem_id": theorem_id,
                    "theorem_id_source": theorem_id_source,
                    "canonical_theorem_id": canonical["canonical_theorem_id"],
                    "canonical_statement": canonical["canonical_statement"],
                    "claim_shape": canonical["claim_shape"],
                    "toolchain_hash": toolchain["toolchain_hash"],
                    "lean_toolchain": toolchain["lean_toolchain"],
                    "mathlib_pin": toolchain["mathlib"],
                    "pipeline_commit": pipeline_commit,
                    "source_latex": source_latex,
                    "normalized_text": normalized_text,
                    "lean_statement": lean_statement,
                    "generated_lean_declaration": decl,
                    "proof_text": proof_text,
                    "status": str(entry.get("status", "")).strip(),
                    "trust_tier": str(entry.get("trust_class", "")).strip(),
                    "trust_reference": str(entry.get("trust_reference", "")).strip(),
                    "proof_method": str(entry.get("proof_method", "")).strip(),
                    "failure_origin": str(entry.get("failure_origin", "")).strip(),
                    "failure_kind": str(entry.get("failure_kind", "")).strip(),
                    "ledger_role": str(entry.get("ledger_role", "")).strip(),
                    "equivalence_scope": str(entry.get("equivalence_scope", "")).strip(),
                    "closure_claim": str(entry.get("closure_claim", "")).strip(),
                    "claim_equivalence_verdict": str(entry.get("claim_equivalence_verdict", "")).strip(),
                    "novelty_status": str(entry.get("novelty_status", "") or "unknown").strip(),
                    "novelty_evidence": entry.get("novelty_evidence") if isinstance(entry.get("novelty_evidence"), dict) else {},
                    "corpus_duplicate_status": "",
                    "mathlib_novelty_status": "",
                    "identity_status": "",
                    "identity_evidence": entry.get("identity_evidence") if isinstance(entry.get("identity_evidence"), dict) else {},
                    "superseded_by_row_id": str(entry.get("superseded_by_row_id", "")).strip(),
                    "replaces_generated_theorem": str(entry.get("replaces_generated_theorem", "")).strip(),
                    "proof_countable": bool(entry.get("proof_countable", True)),
                    "axiom_debt": entry.get("axiom_debt") if isinstance(entry.get("axiom_debt"), list) else [],
                    "validation_gates": entry.get("validation_gates") if isinstance(entry.get("validation_gates"), dict) else {},
                    "gate_failures": entry.get("gate_failures") if isinstance(entry.get("gate_failures"), list) else [],
                    "imports": imports,
                    "source_span": source_span,
                    "provenance": provenance,
                    "artifact_paths": artifacts,
                    "exported_at_unix": int(time.time()),
                }
            tier, tier_evidence = _training_tier(row)
            row["dataset_tier"] = tier
            row["training_tier"] = tier
            row["tier_evidence"] = tier_evidence
            _downgrade_unsupported_novelty(row)
            corpus_dup, mathlib_novelty = _novelty_split({**entry, **row})
            row["corpus_duplicate_status"] = corpus_dup
            row["mathlib_novelty_status"] = mathlib_novelty
            identity_status, identity_evidence = _identity_defaults({**entry, **row})
            row["identity_status"] = identity_status
            row["identity_evidence"] = identity_evidence
            row.update(
                _alignment_payload(
                    row=row,
                    paper_id=arxiv_id,
                    canonical_theorem_id=str(canonical["canonical_theorem_id"]),
                    evidence_row=evidence_row,
                    source_match_evidence=source_match_evidence,
                    source_span=source_span,
                )
            )
            raw_rows.append(row)

    rows, dedupe_summary = _dedupe_rows(raw_rows)
    status_counts = Counter(str(row.get("status", "")) for row in rows)
    trust_counts = Counter(str(row.get("trust_tier", "")) for row in rows)
    dataset_tier_counts = Counter(str(row.get("dataset_tier", "")) for row in rows)
    training_tier_counts = Counter(str(row.get("training_tier", "")) for row in rows)
    alignment_counts = Counter(str(row.get("statement_alignment_class", "")) for row in rows)
    alignment_tier_counts = Counter(str(row.get("alignment_tier", "")) for row in rows)
    source_span_quality_counts = Counter(str(row.get("source_span_quality", "")) for row in rows)
    novelty_counts = Counter(str(row.get("novelty_status", "unknown") or "unknown") for row in rows)
    corpus_duplicate_counts = Counter(str(row.get("corpus_duplicate_status", "unknown") or "unknown") for row in rows)
    mathlib_novelty_counts = Counter(str(row.get("mathlib_novelty_status", "unknown") or "unknown") for row in rows)
    identity_status_counts = Counter(str(row.get("identity_status", "unknown") or "unknown") for row in rows)
    verified_scope_counts = Counter(_verified_proven_scope(row) for row in rows if _is_verified_proven_row(row))
    verified_role_counts = Counter(str(row.get("ledger_role", "") or "direct_or_unknown") for row in rows if _is_verified_proven_row(row))
    span_counts = Counter(str((row.get("source_span") or {}).get("span_confidence", "")) for row in rows)
    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "dataset_family": DATASET_FAMILY,
        "rows": len(rows),
        **dedupe_summary,
        "papers": len({row["arxiv_id"] for row in rows}),
        "status_counts": dict(status_counts),
        "trust_tier_counts": dict(trust_counts),
        "dataset_tier_counts": dict(dataset_tier_counts),
        "training_tier_counts": dict(training_tier_counts),
        "gold_proof_count": int(dataset_tier_counts.get("gold_proof", 0)),
        "verified_proven_count": sum(
            1
            for row in rows
            if _is_verified_proven_row(row)
        ),
        "verified_proven_scope_counts": dict(verified_scope_counts),
        "verified_proven_role_counts": dict(verified_role_counts),
        "verified_proven_full_source_claim": int(verified_scope_counts.get("full_source_claim", 0)),
        "verified_proven_audited_component": int(verified_scope_counts.get("audited_component", 0)),
        "alignment_counts": dict(alignment_counts),
        "alignment_tier_counts": dict(alignment_tier_counts),
        "alignment_gold_count": int(alignment_tier_counts.get("alignment_gold", 0)),
        "alignment_review_required_count": int(alignment_tier_counts.get("alignment_review_required", 0)),
        "source_span_quality_counts": dict(source_span_quality_counts),
        "ambiguous_source_match_count": sum(
            1
            for row in rows
            if str(((row.get("alignment_evidence") or {}).get("source_match") or {}).get("match_status", ""))
            == "ambiguous"
        ),
        "novelty_status_counts": dict(novelty_counts),
        "corpus_duplicate_status_counts": dict(corpus_duplicate_counts),
        "mathlib_novelty_status_counts": dict(mathlib_novelty_counts),
        "identity_status_counts": dict(identity_status_counts),
        "span_confidence_counts": dict(span_counts),
        "rows_with_source_latex": sum(1 for row in rows if str(row.get("source_latex", "")).strip()),
        "rows_with_normalized_text": sum(1 for row in rows if str(row.get("normalized_text", "")).strip()),
        "rows_with_lean_statement": sum(1 for row in rows if str(row.get("lean_statement", "")).strip()),
        "rows_with_proof_text": sum(1 for row in rows if str(row.get("proof_text", "")).strip()),
        "rows_with_imports": sum(1 for row in rows if row.get("imports")),
        "warnings": sorted(set(warnings))[:200],
    }
    return rows, summary


def export_corpus(
    *,
    ledger_paths: Iterable[Path],
    project_root: Path,
    report_roots: Iterable[Path],
    evidence_roots: Iterable[Path],
    out_jsonl: Path,
    out_summary: Path,
    validate_schema: bool = False,
) -> dict[str, Any]:
    rows, summary = build_corpus_rows(
        ledger_paths=ledger_paths,
        project_root=project_root,
        report_roots=report_roots,
        evidence_roots=evidence_roots,
    )
    validation_errors = validate_corpus_export(rows, summary) if validate_schema else []
    if validation_errors:
        summary = {**summary, "schema_validation_errors": validation_errors[:200]}
    _write_jsonl(out_jsonl, rows)
    _write_json(out_summary, {**summary, "out_jsonl": str(out_jsonl), "out_summary": str(out_summary)})
    result = {**summary, "out_jsonl": str(out_jsonl), "out_summary": str(out_summary)}
    if validation_errors:
        result["schema_validation_errors"] = validation_errors[:200]
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export stable theorem-level corpus rows from DESol artifacts")
    parser.add_argument("--project-root", default=".", help="DESol project root")
    parser.add_argument(
        "--ledger-path",
        action="append",
        default=[],
        help="Ledger file or directory. Can be passed more than once.",
    )
    parser.add_argument(
        "--report-root",
        action="append",
        default=[],
        help="Full-paper report file or directory. Can be passed more than once.",
    )
    parser.add_argument(
        "--evidence-root",
        action="append",
        default=[],
        help="Ingestion evidence root or extracted_theorems.json. Can be passed more than once.",
    )
    parser.add_argument("--out-jsonl", default=str(DEFAULT_OUT_JSONL))
    parser.add_argument("--out-summary", default=str(DEFAULT_OUT_SUMMARY))
    parser.add_argument("--validate-schema", action="store_true", help="Validate rows and summary against checked-in schemas")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    project_root = Path(args.project_root).resolve()
    ledger_paths = [Path(p) for p in args.ledger_path] or [project_root / DEFAULT_LEDGER_DIR]
    report_roots = [Path(p) for p in args.report_root] or [project_root / DEFAULT_REPORT_DIR]
    evidence_roots = [Path(p) for p in args.evidence_root] or [project_root / DEFAULT_EVIDENCE_DIR]
    result = export_corpus(
        ledger_paths=ledger_paths,
        project_root=project_root,
        report_roots=report_roots,
        evidence_roots=evidence_roots,
        out_jsonl=Path(args.out_jsonl),
        out_summary=Path(args.out_summary),
        validate_schema=bool(args.validate_schema),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result.get("schema_validation_errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
