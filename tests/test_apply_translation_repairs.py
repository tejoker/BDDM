from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_ledger(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"entries": entries}, indent=2), encoding="utf-8")


def test_apply_translation_repairs_swaps_in_validated_candidate(tmp_path: Path, monkeypatch) -> None:
    """When a repair candidate elaborates cleanly, the ledger row's
    `lean_statement` is replaced with the repaired version. This is the
    surgical path that lifts elaboration-failure FLAWED rows toward UNRESOLVED."""
    ledger = tmp_path / "verification_ledgers" / "9999.99999.json"
    _write_ledger(ledger, [
        {
            "theorem_name": "broken_thm",
            "status": "FLAWED",
            "failure_kind": "elaboration_failure",
            "lean_statement": "theorem broken_thm : x^2 = x * x",  # raw LaTeX leak
            "source_statement": "x squared equals x times x",
            "axiom_debt": [],
        },
    ])
    lean_file = tmp_path / "9999.99999.lean"
    lean_file.write_text("namespace ArxivPaper\ntheorem broken_thm : x^2 = x * x := by sorry\nend ArxivPaper\n", encoding="utf-8")

    fake_payload = {
        "repair_candidates": [{
            "theorem_name": "broken_thm",
            "repaired_decl": "theorem broken_thm (x : ℝ) : x ^ 2 = x * x := by ring",
            "changes": ["latex_superscript_braces:fixed"],
            "lean_validation": {"ok": True},
            "repair_quality": {"ok": True},
        }],
        "symbols": [],
    }

    import apply_translation_repairs as ATR
    import repair_bad_translations as RBT
    monkeypatch.setattr(RBT, "build_repair_pack", lambda **_kw: fake_payload)

    result = ATR.apply_translation_repairs(
        paper_id="9999.99999",
        project_root=tmp_path,
        lean_file=lean_file,
        ledger_path=ledger,
        out_dir=tmp_path / "translation_repairs",
        validate_candidates=False,
    )
    assert result["updated_count"] == 1
    after = json.loads(ledger.read_text())["entries"][0]
    assert "x ^ 2 = x * x" in after["lean_statement"]


def test_apply_translation_repairs_skips_unvalidated_candidates(tmp_path: Path, monkeypatch) -> None:
    """A candidate whose `lean_validation.ok` is False MUST NOT be applied —
    it'd replace the ledger statement with broken Lean."""
    ledger = tmp_path / "verification_ledgers" / "9999.99998.json"
    _write_ledger(ledger, [{
        "theorem_name": "broken",
        "status": "FLAWED",
        "lean_statement": "theorem broken : True",
    }])
    lean_file = tmp_path / "9999.99998.lean"
    lean_file.write_text("theorem broken : True := by trivial\n", encoding="utf-8")

    fake_payload = {
        "repair_candidates": [{
            "theorem_name": "broken",
            "repaired_decl": "theorem broken : Garbage",
            "changes": ["bad_repair"],
            "lean_validation": {"ok": False, "error": "elaboration failed"},
            "repair_quality": {"ok": True},
        }],
        "symbols": [],
    }

    import apply_translation_repairs as ATR
    import repair_bad_translations as RBT
    monkeypatch.setattr(RBT, "build_repair_pack", lambda **_kw: fake_payload)

    result = ATR.apply_translation_repairs(
        paper_id="9999.99998",
        project_root=tmp_path,
        lean_file=lean_file,
        ledger_path=ledger,
        out_dir=tmp_path / "translation_repairs",
        validate_candidates=False,
    )
    assert result["updated_count"] == 0
    after = json.loads(ledger.read_text())["entries"][0]
    assert after["lean_statement"] == "theorem broken : True"


def test_apply_translation_repairs_no_op_when_no_candidates(tmp_path: Path, monkeypatch) -> None:
    """Empty repair pack → ledger untouched; idempotency precondition."""
    ledger = tmp_path / "verification_ledgers" / "9999.99997.json"
    _write_ledger(ledger, [{"theorem_name": "ok_thm", "status": "FULLY_PROVEN"}])
    lean_file = tmp_path / "9999.99997.lean"
    lean_file.write_text("theorem ok_thm : True := by trivial\n", encoding="utf-8")

    import apply_translation_repairs as ATR
    import repair_bad_translations as RBT
    monkeypatch.setattr(RBT, "build_repair_pack", lambda **_kw: {"repair_candidates": [], "symbols": []})

    first_mtime = ledger.stat().st_mtime
    result = ATR.apply_translation_repairs(
        paper_id="9999.99997",
        project_root=tmp_path,
        lean_file=lean_file,
        ledger_path=ledger,
        out_dir=tmp_path / "translation_repairs",
        validate_candidates=False,
    )
    assert result["updated_count"] == 0
    # Ledger file untouched (no rewrite).
    assert ledger.stat().st_mtime == first_mtime
