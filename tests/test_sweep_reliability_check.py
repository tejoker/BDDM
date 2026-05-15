"""Hermetic tests for `sweep_reliability_check`.

We exercise the pure helpers (`_row_closed`, `extract_closures`,
`compute_reliability`) directly, and we drive
`reliability_check(...)` against a stub sweep runner that just writes
canned summaries to disk — no subprocesses, no Mistral, no lake calls.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

import sweep_reliability_check as rel


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


def test_row_closed_detects_first_pass_validated() -> None:
    row = {"theorem": "t", "stages": [{"stage": "first_pass_validated"}]}
    assert rel._row_closed(row) is True


def test_row_closed_detects_composed_and_routed() -> None:
    assert rel._row_closed({"stages": [{"stage": "composed"}]}) is True
    assert rel._row_closed({"stages": [{"stage": "routed_to_axiom_backed"}]}) is True


def test_row_closed_rejects_non_closure_stages() -> None:
    row = {
        "stages": [
            {"stage": "first_pass_isolated_check_failed"},
            {"stage": "factor_below_min_aux"},
            "dry_run_skip",
        ],
    }
    assert rel._row_closed(row) is False


def test_row_closed_accepts_plain_string_stage() -> None:
    assert rel._row_closed({"stages": ["composed"]}) is True
    assert rel._row_closed({"stages": ["nothing"]}) is False


def test_extract_closures_keys_are_paper_double_colon_theorem() -> None:
    summary = {
        "papers": [
            {
                "paper_id": "1234.5678",
                "details": [
                    {"theorem": "Foo.bar", "stages": [{"stage": "first_pass_validated"}]},
                    {"theorem": "Foo.baz", "stages": [{"stage": "factor_transport_error"}]},
                ],
            },
            {
                "paper_id": "9999.0001",
                "details": [
                    {"theorem": "X.y", "stages": [{"stage": "composed"}]},
                ],
            },
        ],
    }
    out = rel.extract_closures(summary)
    assert out == {
        "1234.5678::Foo.bar": "first_pass_validated",
        "9999.0001::X.y": "composed",
    }


def test_compute_reliability_basic_overlap() -> None:
    a = {"r1": "first_pass_validated", "r2": "composed", "r3": "first_pass_validated"}
    b = {"r2": "composed", "r3": "first_pass_validated", "r4": "composed"}
    res = rel.compute_reliability(a, b)
    assert res["both"] == ["r2", "r3"]
    assert res["a_only"] == ["r1"]
    assert res["b_only"] == ["r4"]
    assert res["counts"] == {
        "a_total": 3, "b_total": 3, "both": 2,
        "a_only": 1, "b_only": 1, "union": 4,
    }
    # 2 / 4 = 0.5 reliability.
    assert res["reliability_rate"] == pytest.approx(0.5)


def test_compute_reliability_perfect_agreement() -> None:
    a = {"x": "composed", "y": "first_pass_validated"}
    b = {"x": "composed", "y": "first_pass_validated"}
    res = rel.compute_reliability(a, b)
    assert res["reliability_rate"] == pytest.approx(1.0)
    assert res["both"] == ["x", "y"]
    assert res["a_only"] == [] and res["b_only"] == []


def test_compute_reliability_no_overlap() -> None:
    a = {"x": "composed"}
    b = {"y": "composed"}
    res = rel.compute_reliability(a, b)
    assert res["reliability_rate"] == pytest.approx(0.0)
    assert res["both"] == []
    assert res["a_only"] == ["x"] and res["b_only"] == ["y"]


def test_compute_reliability_empty_inputs() -> None:
    res = rel.compute_reliability({}, {})
    assert res["reliability_rate"] == 0.0
    assert res["counts"]["union"] == 0


# --------------------------------------------------------------------------
# reliability_check end-to-end with a stub sweep runner
# --------------------------------------------------------------------------


def _make_stub_runner(
    tmp_path: Path,
    *,
    seed_a: int,
    seed_b: int,
    closures_a: list[tuple[str, str, str]],
    closures_b: list[tuple[str, str, str]],
) -> list[str]:
    """Write a tiny python program that, when invoked with the same CLI
    surface as `sweep_lemma_factor_v2.py`, writes a summary JSON whose
    closures match the seed-keyed lookup table.

    The stub reads MISTRAL_SEED to pick which closures to emit, and
    writes to the `--summary` path.
    """
    script = tmp_path / "fake_sweep.py"
    table = {str(seed_a): closures_a, str(seed_b): closures_b}
    script.write_text(
        "import json, os, sys\n"
        "table = " + json.dumps(table) + "\n"
        "seed = os.environ.get('MISTRAL_SEED', '0')\n"
        "args = sys.argv[1:]\n"
        "out = None\n"
        "papers = []\n"
        "i = 0\n"
        "while i < len(args):\n"
        "    if args[i] == '--summary':\n"
        "        out = args[i+1]; i += 2; continue\n"
        "    if args[i] == '--paper':\n"
        "        papers.append(args[i+1]); i += 2; continue\n"
        "    i += 1\n"
        "rows = table.get(seed, [])\n"
        "by_paper = {}\n"
        "for (pid, thm, stage) in rows:\n"
        "    by_paper.setdefault(pid, []).append({'theorem': thm, 'stages': [{'stage': stage}]})\n"
        "summary = {'papers': [{'paper_id': pid, 'details': details} for pid, details in by_paper.items()]}\n"
        "from pathlib import Path\n"
        "Path(out).parent.mkdir(parents=True, exist_ok=True)\n"
        "Path(out).write_text(json.dumps(summary), encoding='utf-8')\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


def test_reliability_check_end_to_end_partial_overlap(tmp_path) -> None:
    runner = _make_stub_runner(
        tmp_path,
        seed_a=11, seed_b=22,
        closures_a=[
            ("p1", "T.shared", "first_pass_validated"),
            ("p1", "T.aOnly", "composed"),
        ],
        closures_b=[
            ("p1", "T.shared", "first_pass_validated"),
            ("p1", "T.bOnly", "first_pass_validated"),
        ],
    )
    fake_canonical = tmp_path / "canonical.json"
    fake_canonical.write_text(
        json.dumps({"sentinel": "before"}), encoding="utf-8",
    )
    result = rel.reliability_check(
        seed_a=11, seed_b=22,
        paper_ids=["p1"], max_candidates=3,
        workdir=tmp_path / "wk",
        canonical_summary=fake_canonical,
        sweep_runner=runner,
    )
    assert result["counts"]["both"] == 1
    assert result["counts"]["a_only"] == 1
    assert result["counts"]["b_only"] == 1
    assert result["counts"]["union"] == 3
    assert result["reliability_rate"] == pytest.approx(1 / 3)
    assert "p1::T.shared" in result["both"]
    assert result["a_only"] == ["p1::T.aOnly"]
    assert result["b_only"] == ["p1::T.bOnly"]
    assert result["sweep_a"]["returncode"] == 0
    assert result["sweep_b"]["returncode"] == 0
    # Canonical summary must be RESTORED (still has the original sentinel).
    restored = json.loads(fake_canonical.read_text(encoding="utf-8"))
    assert restored == {"sentinel": "before"}


def test_reliability_check_handles_perfect_agreement(tmp_path) -> None:
    runner = _make_stub_runner(
        tmp_path,
        seed_a=1, seed_b=2,
        closures_a=[("p1", "T.x", "composed")],
        closures_b=[("p1", "T.x", "composed")],
    )
    fake_canonical = tmp_path / "canonical.json"
    result = rel.reliability_check(
        seed_a=1, seed_b=2,
        paper_ids=["p1"], max_candidates=3,
        workdir=tmp_path / "wk",
        canonical_summary=fake_canonical,
        sweep_runner=runner,
    )
    assert result["reliability_rate"] == pytest.approx(1.0)
    assert result["both"] == ["p1::T.x"]


def test_reliability_check_handles_no_closures(tmp_path) -> None:
    runner = _make_stub_runner(
        tmp_path,
        seed_a=1, seed_b=2,
        closures_a=[], closures_b=[],
    )
    fake_canonical = tmp_path / "canonical.json"
    result = rel.reliability_check(
        seed_a=1, seed_b=2,
        paper_ids=["p1"], max_candidates=3,
        workdir=tmp_path / "wk",
        canonical_summary=fake_canonical,
        sweep_runner=runner,
    )
    assert result["reliability_rate"] == 0.0
    assert result["counts"]["union"] == 0


def test_reliability_check_does_not_mutate_canonical(tmp_path) -> None:
    """Even when canonical doesn't exist before, calling reliability_check
    must not leave behind a partial summary at that path."""
    runner = _make_stub_runner(
        tmp_path,
        seed_a=1, seed_b=2,
        closures_a=[("p", "t", "composed")],
        closures_b=[("p", "t", "composed")],
    )
    fake_canonical = tmp_path / "does-not-exist-yet.json"
    assert not fake_canonical.exists()
    _ = rel.reliability_check(
        seed_a=1, seed_b=2,
        paper_ids=["p"], max_candidates=1,
        workdir=tmp_path / "wk",
        canonical_summary=fake_canonical,
        sweep_runner=runner,
    )
    # No backup existed, so the file remains absent — we don't fabricate it.
    assert not fake_canonical.exists()
