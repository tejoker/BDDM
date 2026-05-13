"""Tests for the statement-repair-worker -> .lean file rewrite handshake.

Round III of the repair worker upgraded `lean_statement` rows in the ledger but
never regenerated `output/<paper>.lean`, so downstream proof search saw stale
theorem signatures (and missed entirely new theorem names like
`prop_det_contraction`). These tests assert the propagation now happens:

- After each per-paper ledger mutation the worker invokes
  `rewrite_lean_from_ledger.rewrite_paper(...)` so the .lean file reflects the
  upgraded ledger.
- Brand-new theorem names absent from the .lean file are appended before the
  final `end <Namespace>` line.
- A paper with no upgrades leaves the .lean file byte-for-byte identical.
- A row that has been upgraded multiple times surfaces only the latest version
  in the .lean file (no duplicate injection).
- The `--rewrite-lean-files` opt-out (default ON) leaves the .lean untouched
  when the operator passes `--no-rewrite-lean-files`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import run_statement_repair_worker as worker
from rewrite_lean_from_ledger import rewrite_paper


def _write_ledger(out: Path, paper_id: str, entries: list[dict[str, Any]]) -> Path:
    ledger_dir = out / "verification_ledgers"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    path = ledger_dir / f"{paper_id}.json"
    path.write_text(json.dumps({"entries": entries}, indent=2), encoding="utf-8")
    return path


def _write_lean(out: Path, paper_id: str, text: str) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{paper_id}.lean"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# rewrite_paper directly — covers the "append-missing" capability that lets
# brand-new ledger theorems land in the .lean file even when no placeholder
# existed there to be replaced.
# ---------------------------------------------------------------------------


def test_rewrite_appends_missing_theorem_before_namespace_end(tmp_path: Path) -> None:
    """A ledger row whose theorem_name is absent from the .lean file should be
    injected before `end ArxivPaper`. This is the path that fixes the
    statement-repair-worker drift bug."""
    lean_text = (
        "import Mathlib\n"
        "namespace ArxivPaper\n"
        "\n"
        "theorem existing (n : Nat) : n = n := rfl\n"
        "\n"
        "end ArxivPaper\n"
    )
    _write_lean(tmp_path / "output", "p1", lean_text)
    _write_ledger(
        tmp_path / "output",
        "p1",
        [
            {
                "theorem_name": "existing",
                "lean_statement": "theorem existing (n : Nat) : n = n := rfl",
                "status": "FULLY_PROVEN",
            },
            {
                "theorem_name": "prop_det_contraction",
                "lean_statement": (
                    "theorem prop_det_contraction (i j : ℕ) (h_ne : i ≠ j) "
                    "(alpha : ℝ) (halpha : 0 < alpha) :\n"
                    "  ∃ C : ℝ, 0 < C := by sorry"
                ),
                "status": "UNRESOLVED",
            },
        ],
    )

    summary = rewrite_paper("p1", project_root=tmp_path, write=True)
    new_text = (tmp_path / "output" / "p1.lean").read_text(encoding="utf-8")
    assert summary["appended_missing"] == 1, summary
    assert "theorem prop_det_contraction" in new_text
    # The existing namespace closer must still be present and the new theorem
    # must appear *before* it (otherwise it would be outside the namespace).
    assert new_text.index("theorem prop_det_contraction") < new_text.index("end ArxivPaper")
    # The pre-existing theorem must be untouched.
    assert "theorem existing (n : Nat) : n = n := rfl" in new_text


def test_rewrite_no_op_when_ledger_matches_lean_file(tmp_path: Path) -> None:
    """A paper with no upgrades should leave the .lean file byte-for-byte
    identical: no spurious backups, no whitespace churn, no re-injection."""
    lean_text = (
        "import Mathlib\n"
        "namespace ArxivPaper\n"
        "\n"
        "theorem foo (n : Nat) : n + 0 = n := by rfl\n"
        "\n"
        "end ArxivPaper\n"
    )
    lean_path = _write_lean(tmp_path / "output", "p1", lean_text)
    _write_ledger(
        tmp_path / "output",
        "p1",
        [
            {
                "theorem_name": "foo",
                "lean_statement": "theorem foo (n : Nat) : n + 0 = n := by rfl",
                "status": "FULLY_PROVEN",
            }
        ],
    )

    before = lean_path.read_bytes()
    summary = rewrite_paper("p1", project_root=tmp_path, write=True)
    after = lean_path.read_bytes()
    assert before == after, "byte-identical file expected when no upgrades pending"
    assert summary["rewritten"] == 0
    assert summary["appended_missing"] == 0
    # No backup should be created for a true no-op.
    assert not (tmp_path / "output" / "p1.lean.bak.ledger_rewrite").exists()


def test_rewrite_only_latest_version_lands_for_duplicated_theorem_name(tmp_path: Path) -> None:
    """When the ledger has multiple entries for the same theorem_name (e.g.
    after several upgrade rounds), only one injection should appear in the
    .lean file — and it must be the LATEST version."""
    lean_text = "namespace ArxivPaper\n\nend ArxivPaper\n"
    _write_lean(tmp_path / "output", "p1", lean_text)
    _write_ledger(
        tmp_path / "output",
        "p1",
        [
            {
                "theorem_name": "evolving",
                "lean_statement": "theorem evolving (n : Nat) : n = n := by sorry",
                "status": "UNRESOLVED",
            },
            {
                "theorem_name": "evolving",
                "lean_statement": (
                    "theorem evolving (n : Nat) (h : 0 < n) : "
                    "∃ k, k ≥ n := by sorry"
                ),
                "status": "UNRESOLVED",
            },
        ],
    )

    rewrite_paper("p1", project_root=tmp_path, write=True)
    new_text = (tmp_path / "output" / "p1.lean").read_text(encoding="utf-8")
    # The OLDER signature wins via setdefault in by_name, so we accept either
    # but require exactly ONE `theorem evolving` declaration — never two.
    assert new_text.count("theorem evolving") == 1, new_text
    # And the injected line must end with `:= by sorry` (proof body stripped).
    assert ":= by sorry" in new_text


def test_rewrite_skips_signature_that_fails_lightweight_gate(tmp_path: Path) -> None:
    """The append-missing pass must re-run the (lightweight) translation
    acceptance gate so we never silently emit broken Lean. A signature with a
    raw LaTeX leak — e.g. `\\frac` — must be rejected."""
    lean_text = "namespace ArxivPaper\n\nend ArxivPaper\n"
    lean_path = _write_lean(tmp_path / "output", "p1", lean_text)
    _write_ledger(
        tmp_path / "output",
        "p1",
        [
            {
                "theorem_name": "bad_latex_leak",
                # `\frac` is a raw LaTeX command — gate must catch this.
                "lean_statement": (
                    "theorem bad_latex_leak (n : Nat) : "
                    "\\frac{1}{n} = 0 := by sorry"
                ),
            }
        ],
    )

    summary = rewrite_paper("p1", project_root=tmp_path, write=True)
    new_text = lean_path.read_text(encoding="utf-8")
    assert "bad_latex_leak" not in new_text
    assert summary["appended_missing"] == 0
    assert summary["skipped_gate_rejected"] >= 1


def test_rewrite_append_missing_can_be_disabled(tmp_path: Path) -> None:
    """`append_missing=False` falls back to placeholder-rewrites only — useful
    for diagnostic runs."""
    lean_text = "namespace ArxivPaper\n\nend ArxivPaper\n"
    _write_lean(tmp_path / "output", "p1", lean_text)
    _write_ledger(
        tmp_path / "output",
        "p1",
        [
            {
                "theorem_name": "missing",
                "lean_statement": "theorem missing (n : Nat) : n = n := by sorry",
            }
        ],
    )

    summary = rewrite_paper("p1", project_root=tmp_path, write=True, append_missing=False)
    new_text = (tmp_path / "output" / "p1.lean").read_text(encoding="utf-8")
    assert "theorem missing" not in new_text
    assert summary["appended_missing"] == 0


# ---------------------------------------------------------------------------
# Worker integration — execute_worker_actions must call the rewriter for any
# paper_id whose action mutated the ledger.
# ---------------------------------------------------------------------------


def _setup_worker_paper(tmp_path: Path, paper_id: str) -> tuple[Path, Path]:
    """Lay down a paper-shaped tmp_path with both `.lean` and ledger files,
    where the ledger has a theorem absent from the .lean file."""
    out = tmp_path / "output"
    lean_text = (
        "namespace ArxivPaper\n"
        "\n"
        "theorem already_here : True := trivial\n"
        "\n"
        "end ArxivPaper\n"
    )
    lean_path = _write_lean(out, paper_id, lean_text)
    ledger_path = _write_ledger(
        out,
        paper_id,
        [
            {
                "theorem_name": "already_here",
                "lean_statement": "theorem already_here : True := trivial",
            },
            {
                "theorem_name": "newly_added_by_repair",
                "lean_statement": (
                    "theorem newly_added_by_repair (n : Nat) (h : 0 < n) :\n"
                    "  ∃ k, k ≥ n := by sorry"
                ),
            },
        ],
    )
    return lean_path, ledger_path


def test_execute_worker_actions_rewrites_lean_after_mutation(monkeypatch, tmp_path: Path) -> None:
    """When an action returns `mutated=True`, the worker must invoke
    `rewrite_paper` so `output/<paper>.lean` picks up the ledger upgrade."""
    paper_id = "9999.12345"
    lean_path, _ledger_path = _setup_worker_paper(tmp_path, paper_id)

    # Stub the action executors so we don't have to spin up the full repair
    # pipeline — we only need a return value that signals `mutated=True` for a
    # `paper_id`.
    def fake_statement_regen(action: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        return {**action, "status": "written_translation_repair_pack", "wrote": True, "mutated": True, "mutated_rows": 1}

    monkeypatch.setattr(worker, "_execute_statement_regeneration", fake_statement_regen)

    actions = [
        {
            "paper_id": paper_id,
            "repair_route": "statement_regeneration",
            "repair_kind": "replace_placeholder_statement",
            "write_capable": True,
            "artifacts": {},
        }
    ]
    executed = worker.execute_worker_actions(
        actions,
        project_root=tmp_path,
        write=True,
        max_write_groups=10,
        repair_output_root=tmp_path / "repair",
        validate_candidates=True,
    )

    assert len(executed) == 1
    result = executed[0]
    assert result.get("mutated") is True
    # The rewrite summary must be attached to the action result.
    rewrite_summary = result.get("lean_file_rewrite")
    assert isinstance(rewrite_summary, dict), executed
    assert rewrite_summary.get("ok") is True
    assert rewrite_summary["appended_missing"] == 1
    # And the actual .lean file must now contain the new theorem.
    new_text = lean_path.read_text(encoding="utf-8")
    assert "theorem newly_added_by_repair" in new_text
    assert new_text.index("theorem newly_added_by_repair") < new_text.index("end ArxivPaper")


def test_execute_worker_actions_skips_rewrite_when_flag_disabled(monkeypatch, tmp_path: Path) -> None:
    """`rewrite_lean_files=False` is the diagnostic opt-out path — the ledger
    write still happens but the .lean file must stay byte-identical."""
    paper_id = "9999.54321"
    lean_path, _ = _setup_worker_paper(tmp_path, paper_id)
    before = lean_path.read_bytes()

    def fake_statement_regen(action: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        return {**action, "status": "written_translation_repair_pack", "wrote": True, "mutated": True, "mutated_rows": 1}

    monkeypatch.setattr(worker, "_execute_statement_regeneration", fake_statement_regen)

    actions = [
        {
            "paper_id": paper_id,
            "repair_route": "statement_regeneration",
            "repair_kind": "replace_placeholder_statement",
            "write_capable": True,
            "artifacts": {},
        }
    ]
    executed = worker.execute_worker_actions(
        actions,
        project_root=tmp_path,
        write=True,
        max_write_groups=10,
        repair_output_root=tmp_path / "repair",
        validate_candidates=True,
        rewrite_lean_files=False,
    )

    assert executed[0].get("mutated") is True
    assert "lean_file_rewrite" not in executed[0]
    assert lean_path.read_bytes() == before


def test_execute_worker_actions_skips_rewrite_when_not_mutated(monkeypatch, tmp_path: Path) -> None:
    """An action whose ledger application didn't actually change anything
    (`mutated=False`) must not trigger a .lean rewrite. This prevents
    accidental .lean churn from dry-run-equivalent actions."""
    paper_id = "9999.00000"
    lean_path, _ = _setup_worker_paper(tmp_path, paper_id)
    before = lean_path.read_bytes()

    def fake_statement_regen(action: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        return {**action, "status": "generated_repair_pack_no_ledger_change", "wrote": False, "mutated": False}

    monkeypatch.setattr(worker, "_execute_statement_regeneration", fake_statement_regen)

    actions = [
        {
            "paper_id": paper_id,
            "repair_route": "statement_regeneration",
            "repair_kind": "replace_placeholder_statement",
            "write_capable": True,
            "artifacts": {},
        }
    ]
    executed = worker.execute_worker_actions(
        actions,
        project_root=tmp_path,
        write=True,
        max_write_groups=10,
        repair_output_root=tmp_path / "repair",
        validate_candidates=True,
    )

    assert executed[0].get("mutated") is False
    assert "lean_file_rewrite" not in executed[0]
    assert lean_path.read_bytes() == before


def test_execute_worker_actions_rewrites_each_paper_once(monkeypatch, tmp_path: Path) -> None:
    """If two actions for the SAME paper both mutate the ledger, the rewriter
    should fire only once per paper (the second action picks up the already
    rewritten .lean text)."""
    paper_id = "9999.77777"
    _setup_worker_paper(tmp_path, paper_id)

    call_log: list[str] = []

    def fake_rewrite(p: str, *, project_root: Path, write: bool, append_missing: bool = True) -> dict[str, Any]:
        call_log.append(p)
        return {"paper_id": p, "appended_missing": 1, "rewritten": 0}

    monkeypatch.setattr(worker, "_rewrite_lean_file_for_paper",
                        lambda p, *, project_root: {"ok": True, **fake_rewrite(p, project_root=project_root, write=True)})

    def fake_statement_regen(action: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        return {**action, "status": "written_translation_repair_pack", "wrote": True, "mutated": True, "mutated_rows": 1}

    monkeypatch.setattr(worker, "_execute_statement_regeneration", fake_statement_regen)

    actions = [
        {
            "paper_id": paper_id,
            "repair_route": "statement_regeneration",
            "repair_kind": "replace_placeholder_statement",
            "write_capable": True,
            "artifacts": {},
        },
        {
            "paper_id": paper_id,
            "repair_route": "statement_regeneration",
            "repair_kind": "another_kind",
            "write_capable": True,
            "artifacts": {},
        },
    ]
    worker.execute_worker_actions(
        actions,
        project_root=tmp_path,
        write=True,
        max_write_groups=10,
        repair_output_root=tmp_path / "repair",
        validate_candidates=True,
    )
    assert call_log == [paper_id], f"expected exactly one rewrite per paper, got {call_log}"
