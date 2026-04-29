#!/usr/bin/env python3
"""Synchronize generated output mirrors from canonical release bundle artifacts."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from build_release_index import build_release_index


def sync_release_mirrors(repo_root: Path, *, write: bool = False) -> dict[str, Any]:
    index = build_release_index(repo_root)
    actions: list[dict[str, Any]] = []
    for artifact in index.get("artifacts", []):
        if not isinstance(artifact, dict) or artifact.get("drift_status") != "duplicate_drift":
            continue
        canonical = repo_root / str(artifact.get("canonical_path", ""))
        if not canonical.exists():
            continue
        for duplicate in artifact.get("duplicate_artifacts", []):
            if not isinstance(duplicate, dict) or duplicate.get("matches_canonical"):
                continue
            target = repo_root / str(duplicate.get("path", ""))
            if not target.exists():
                continue
            action = {
                "paper_id": artifact.get("paper_id", ""),
                "role": artifact.get("role", ""),
                "canonical_path": str(artifact.get("canonical_path", "")),
                "mirror_path": str(duplicate.get("path", "")),
                "previous_mirror_sha256": str(duplicate.get("sha256", "")),
                "canonical_sha256": str(artifact.get("canonical_sha256", "")),
                "written": False,
            }
            if write:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(canonical, target)
                action["written"] = True
            actions.append(action)
    after = build_release_index(repo_root) if write else index
    return {
        "schema_version": "release_mirror_sync.v1",
        "write": bool(write),
        "actions": actions,
        "action_count": len(actions),
        "before_duplicate_drift_count": int(index.get("duplicate_drift_count", 0) or 0),
        "after_duplicate_drift_count": int(after.get("duplicate_drift_count", 0) or 0),
        "after_drift_status_counts": after.get("drift_status_counts", {}),
        "policy": "canonical release artifacts are copied to existing output mirrors only; no drift checks are disabled.",
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync generated output mirrors from canonical release bundles")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--write", action="store_true", help="Copy canonical bytes into existing drifted output mirrors")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = sync_release_mirrors(args.project_root, write=bool(args.write))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
