#!/usr/bin/env python3
"""Build a canonical release index and artifact drift report for DESol bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_OUT = Path("output/reproducibility/release_index.json")
CANONICAL_ROOT = Path("reproducibility/full_paper_reports")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _duplicate_candidates(repo_root: Path, paper_id: str, role: str) -> list[Path]:
    if role == "ledger":
        return [
            repo_root / "output" / "verification_ledgers" / f"{paper_id}.json",
            repo_root / "output" / "verification_ledgers" / f"{paper_id}_verification_ledger.json",
        ]
    if role == "report":
        return [
            repo_root / "output" / "reports" / "full_paper" / f"{paper_id}_suite_report.json",
            repo_root / "output" / "reports" / "full_paper" / paper_id / "suite_report.json",
        ]
    if role == "unresolved":
        return [
            repo_root / "output" / "reports" / "full_paper" / f"{paper_id}_unresolved_pack.json",
            repo_root / "output" / "reports" / "full_paper" / paper_id / "unresolved_pack.json",
        ]
    return []


def _artifact_row(repo_root: Path, paper_id: str, role: str, canonical_path: Path) -> dict[str, Any]:
    canonical_hash = _sha256(canonical_path) if canonical_path.exists() else ""
    duplicates = [path for path in _duplicate_candidates(repo_root, paper_id, role) if path.exists()]
    duplicate_rows = []
    for duplicate in duplicates:
        duplicate_hash = _sha256(duplicate)
        duplicate_rows.append(
            {
                "path": str(duplicate.relative_to(repo_root)),
                "sha256": duplicate_hash,
                "matches_canonical": duplicate_hash == canonical_hash,
            }
        )
    if not duplicates:
        drift_status = "canonical_only"
    elif all(row["matches_canonical"] for row in duplicate_rows):
        drift_status = "duplicate_matches"
    else:
        drift_status = "duplicate_drift"
    return {
        "paper_id": paper_id,
        "role": role,
        "canonical_path": str(canonical_path.relative_to(repo_root)),
        "canonical_sha256": canonical_hash,
        "duplicate_artifacts": duplicate_rows,
        "drift_status": drift_status,
    }


def build_release_index(repo_root: Path) -> dict[str, Any]:
    canonical_root = repo_root / CANONICAL_ROOT
    artifacts: list[dict[str, Any]] = []
    if canonical_root.exists():
        for bundle in sorted(path for path in canonical_root.iterdir() if path.is_dir()):
            paper_id = bundle.name
            role_paths = {
                "ledger": bundle / "verification_ledger.json",
                "report": bundle / "suite_report.json",
                "manifest": bundle / "manifest.json",
                "unresolved": bundle / "unresolved_pack.json",
            }
            manifest = _read_json(role_paths["manifest"])
            if isinstance(manifest, dict):
                for entry in manifest.get("artifacts", []) if isinstance(manifest.get("artifacts"), list) else []:
                    if not isinstance(entry, dict):
                        continue
                    role = str(entry.get("role", "")).strip()
                    rel_path = str(entry.get("path", "")).strip()
                    if role and rel_path:
                        role_paths.setdefault(role, repo_root / rel_path)
            for role, path in sorted(role_paths.items()):
                if path.exists() and role != "manifest":
                    artifacts.append(_artifact_row(repo_root, paper_id, role, path))
    status_counts = Counter(str(row.get("drift_status", "")) for row in artifacts)
    return {
        "schema_version": "release_index.v1",
        "canonical_root": str(CANONICAL_ROOT),
        "artifacts": artifacts,
        "artifact_count": len(artifacts),
        "drift_status_counts": dict(status_counts.most_common()),
        "duplicate_drift_count": int(status_counts.get("duplicate_drift", 0)),
        "policy": "reproducibility/full_paper_reports is canonical; output/ is generated runtime cache until promoted.",
    }


def write_release_index(repo_root: Path, out: Path) -> dict[str, Any]:
    index = build_release_index(repo_root)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    return {**index, "out": str(out)}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build canonical release index and drift report")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = write_release_index(args.project_root, args.out)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result["duplicate_drift_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
