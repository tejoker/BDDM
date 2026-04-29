from __future__ import annotations

import json
from pathlib import Path

from build_release_index import build_release_index
from sync_release_mirrors import sync_release_mirrors


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_sync_release_mirrors_copies_canonical_to_existing_drifted_mirror(tmp_path: Path) -> None:
    canonical = tmp_path / "reproducibility" / "full_paper_reports" / "2300.00001" / "verification_ledger.json"
    mirror = tmp_path / "output" / "verification_ledgers" / "2300.00001.json"
    _write_json(canonical, {"paper_id": "2300.00001", "entries": [{"status": "FULLY_PROVEN"}]})
    _write_json(mirror, {"paper_id": "2300.00001", "entries": [{"status": "UNRESOLVED"}]})

    dry = sync_release_mirrors(tmp_path, write=False)
    assert dry["before_duplicate_drift_count"] == 1
    assert dry["after_duplicate_drift_count"] == 1
    assert dry["actions"][0]["written"] is False

    written = sync_release_mirrors(tmp_path, write=True)
    assert written["before_duplicate_drift_count"] == 1
    assert written["after_duplicate_drift_count"] == 0
    assert written["actions"][0]["written"] is True
    assert build_release_index(tmp_path)["drift_status_counts"]["duplicate_matches"] == 1
