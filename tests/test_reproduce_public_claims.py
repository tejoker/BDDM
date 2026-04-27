from __future__ import annotations

import json
from pathlib import Path

import reproduce_public_claims as rpc


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_build_public_claim_artifacts_indexes_hashes(monkeypatch, tmp_path: Path) -> None:
    suite = tmp_path / "reproducibility" / "paper_agnostic_golden10.json"
    paper_id = "2300.00001"
    safe = paper_id
    _write_json(suite, {"papers": [{"paper_id": paper_id, "domain": "algebra"}]})
    (tmp_path / "lean-toolchain").write_text("leanprover/lean4:v4.29.0-rc7\n", encoding="utf-8")

    ingestion = tmp_path / "reproducibility" / "paper_agnostic_golden10_results"
    _write_json(ingestion / safe / "fetch.json", {"paper_id": paper_id, "fetch_ok": True})
    _write_json(
        ingestion / safe / "extracted_theorems.json",
        {"paper_id": paper_id, "theorem_count": 1, "entries": [{"name": "t1", "statement": "True"}]},
    )

    translation = tmp_path / "reproducibility" / "paper_agnostic_golden10_translation"
    _write_json(translation / safe / "translation_run.json", {"paper_id": paper_id, "returncode": 0})
    _write_json(translation / safe / "ledger.json", {"paper_id": paper_id, "entries": []})
    _write_json(translation / safe / "pipeline_checkpoint.json", {"done": ["t1"]})

    full_reports = tmp_path / "reproducibility" / "full_paper_reports"
    ledger = {
        "paper_id": paper_id,
        "entries": [
            {
                "theorem_name": "t1",
                "status": "FULLY_PROVEN",
                "lean_statement": "theorem t1 : True",
                "validation_gates": {"lean_elaboration": True},
                "step_obligations": [{"verified": True}],
            }
        ],
    }
    _write_json(full_reports / safe / "verification_ledger.json", ledger)
    _write_json(full_reports / safe / "suite_report.json", {"paper_id": paper_id, "pass_history": [], "steps": []})
    _write_json(full_reports / safe / "manifest.json", {"paper_id": paper_id})

    def fake_run(cmd: list[str], *, cwd: Path, timeout_s: int = 0) -> dict[str, object]:
        out_json = Path(cmd[cmd.index("--out-json") + 1])
        _write_json(out_json, {"papers_evaluated": 1, "theorems_evaluated": 1})
        return {"cmd": cmd, "returncode": 0, "elapsed_s": 0.0, "stdout_tail": "", "stderr_tail": ""}

    monkeypatch.setattr(rpc, "_run", fake_run)

    paths = rpc.build_public_claim_artifacts(
        project_root=tmp_path,
        suite_path=suite,
        out_root=tmp_path / "output" / "public_claims",
        papers=[{"paper_id": paper_id, "domain": "algebra"}],
        stage_rows=[],
        ingestion_root=ingestion,
        translation_root=translation,
        full_reports_root=full_reports,
        generated_at="2026-04-26T00:00:00Z",
        command=["python", "scripts/reproduce_public_claims.py"],
        mode="smoke",
    )

    for role in rpc.PUBLIC_ARTIFACTS:
        assert paths[role].exists()

    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert manifest["all_required_artifacts_present"] is True
    assert any(row["role"] == "fetched_paper_metadata" and row["sha256"] for row in manifest["artifacts"])
    assert manifest["missing_artifacts"][0]["role"] == "proof_attempts"

    lean_validation = json.loads(paths["lean_validation"].read_text(encoding="utf-8"))
    assert lean_validation["rows"][0]["entries"][0]["validation_gates"]["lean_elaboration"] is True


def test_selected_papers_honors_max_papers() -> None:
    suite = Path(__file__).resolve().parent.parent / "reproducibility" / "paper_agnostic_golden10.json"
    papers = rpc._selected_papers(suite, max_papers=1)
    assert [paper["paper_id"] for paper in papers] == ["2304.09598"]
