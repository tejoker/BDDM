from __future__ import annotations

import json
from pathlib import Path

from corpus_release_metadata import (
    CORPUS_RELEASE_SCHEMA_VERSION,
    artifact_entry,
    artifact_summary,
    build_release_audit,
    lake_dependencies,
    validate_release_manifest,
)
from build_release_index import build_release_index
from formalize_paper_full import _publish_reproducibility_bundle
from release_readiness import check_corpus_release_manifests, check_public_claims_manifests, check_release_artifact_drift


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _minimal_project(root: Path) -> None:
    (root / "lean-toolchain").write_text("leanprover/lean4:v4.29.0-rc7\n", encoding="utf-8")
    (root / "lakefile.toml").write_text(
        '\n'.join(
            [
                'name = "desol-test"',
                "",
                "[[require]]",
                'name = "mathlib"',
                'git = "https://github.com/leanprover-community/mathlib4.git"',
                'rev = "abc123"',
            ]
        ),
        encoding="utf-8",
    )


def test_lake_dependencies_extracts_mathlib_pin(tmp_path: Path) -> None:
    _minimal_project(tmp_path)

    deps = lake_dependencies(tmp_path)

    assert deps == [
        {
            "name": "mathlib",
            "git": "https://github.com/leanprover-community/mathlib4.git",
            "rev": "abc123",
        }
    ]


def test_artifact_entry_hashes_and_reports_schema(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.json"
    _write_json(artifact, {"schema_version": "9.9.9", "ok": True})

    row = artifact_entry(tmp_path, "example", artifact, paper_id="p", required=True)
    summary = artifact_summary([row])

    assert row["path"] == "artifact.json"
    assert row["paper_id"] == "p"
    assert row["required"] is True
    assert row["schema_version"] == "9.9.9"
    assert len(row["sha256"]) == 64
    assert summary["missing_required_count"] == 0
    assert summary["checksum_coverage"] == 1.0


def test_release_manifest_validation_requires_audit_fields(tmp_path: Path) -> None:
    _minimal_project(tmp_path)
    artifact = tmp_path / "bundle" / "suite_report.json"
    _write_json(artifact, {"schema_version": "1.0.0"})
    artifacts = [artifact_entry(tmp_path, "report", artifact, required=True)]
    manifest = {
        "schema_version": CORPUS_RELEASE_SCHEMA_VERSION,
        "artifacts": artifacts,
        "release_audit": build_release_audit(project_root=tmp_path, artifacts=artifacts),
    }
    manifest["release_audit"]["repository"]["git_commit"] = "abc123"

    assert validate_release_manifest(manifest, project_root=tmp_path) == []
    assert "missing release_audit block" in validate_release_manifest({"artifacts": artifacts}, project_root=tmp_path)
    manifest["release_audit"]["repository"]["git_commit"] = "unknown"
    assert "release_audit.repository.git_commit missing" in validate_release_manifest(manifest, project_root=tmp_path)


def test_publish_reproducibility_bundle_writes_audited_manifest(tmp_path: Path) -> None:
    _minimal_project(tmp_path)
    report = tmp_path / "output" / "suite_report.json"
    ledger = tmp_path / "output" / "ledger.json"
    unresolved = tmp_path / "output" / "unresolved.json"
    for path in (report, ledger, unresolved):
        _write_json(path, {"schema_version": "1.0.0", "name": path.name})

    paths = _publish_reproducibility_bundle(
        project_root=tmp_path,
        paper_id="2300.00001",
        report_out=report,
        ledger_path=ledger,
        unresolved_out=unresolved,
    )

    manifest = json.loads(Path(paths["manifest"]).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == CORPUS_RELEASE_SCHEMA_VERSION
    assert manifest["release_audit"]["toolchain"]["lean_toolchain"] == "leanprover/lean4:v4.29.0-rc7"
    assert manifest["release_audit"]["toolchain"]["mathlib"]["rev"] == "abc123"
    assert manifest["release_audit"]["artifact_summary"]["missing_required_count"] == 0
    assert {row["role"] for row in manifest["artifacts"]} == {"report", "ledger", "unresolved"}
    assert all(row["sha256"] for row in manifest["artifacts"])


def test_release_readiness_rejects_legacy_bundle_manifest(tmp_path: Path, capsys) -> None:
    _minimal_project(tmp_path)
    manifest = tmp_path / "reproducibility" / "full_paper_reports" / "2300.00001" / "manifest.json"
    _write_json(manifest, {"schema_version": "1.0.0", "files": {}})

    assert check_corpus_release_manifests(tmp_path) is False
    assert "missing release_audit block" in capsys.readouterr().out


def test_release_readiness_validates_public_claims_manifest(tmp_path: Path, capsys) -> None:
    _minimal_project(tmp_path)
    artifact = tmp_path / "output" / "reproducibility" / "public_claims" / "full_report.json"
    _write_json(artifact, {"schema_version": "1.0.0"})
    artifacts = [artifact_entry(tmp_path, "full_report", artifact, required=True)]
    manifest = {
        "schema_version": CORPUS_RELEASE_SCHEMA_VERSION,
        "artifacts": artifacts,
        "all_required_artifacts_present": True,
        "release_audit": build_release_audit(project_root=tmp_path, artifacts=artifacts),
    }
    manifest["release_audit"]["repository"]["git_commit"] = "unknown"
    _write_json(artifact.parent / "manifest.json", manifest)

    assert check_public_claims_manifests(tmp_path) is False
    assert "git_commit missing" in capsys.readouterr().out

    manifest["release_audit"]["repository"]["git_commit"] = "abc123"
    _write_json(artifact.parent / "manifest.json", manifest)

    assert check_public_claims_manifests(tmp_path) is True


def test_release_index_reports_duplicate_drift(tmp_path: Path, capsys) -> None:
    _minimal_project(tmp_path)
    canonical = tmp_path / "reproducibility" / "full_paper_reports" / "2300.00001" / "verification_ledger.json"
    duplicate = tmp_path / "output" / "verification_ledgers" / "2300.00001.json"
    _write_json(canonical, {"paper_id": "2300.00001", "entries": [{"status": "FULLY_PROVEN"}]})
    _write_json(duplicate, {"paper_id": "2300.00001", "entries": [{"status": "UNRESOLVED"}]})

    index = build_release_index(tmp_path)

    assert index["duplicate_drift_count"] == 1
    assert index["drift_status_counts"]["duplicate_drift"] == 1
    assert check_release_artifact_drift(tmp_path) is False
    assert "drift from generated duplicates" in capsys.readouterr().out

    _write_json(duplicate, {"paper_id": "2300.00001", "entries": [{"status": "FULLY_PROVEN"}]})
    assert check_release_artifact_drift(tmp_path) is True
