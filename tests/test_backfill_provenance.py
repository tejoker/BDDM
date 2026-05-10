from __future__ import annotations

import json
from pathlib import Path

from backfill_provenance import (
    _CANONICAL_LEDGER_RE,
    _provenance_passes_gate,
    backfill_ledger,
)


def _write_ledger(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"entries": entries}, indent=2), encoding="utf-8")


def test_backfill_populates_minimal_provenance(tmp_path: Path) -> None:
    """A row whose `provenance` is null gets a minimal {paper_id, label} dict
    that passes the `provenance_linked` gate."""
    ledger = tmp_path / "0000.99999.json"
    _write_ledger(ledger, [
        {"theorem_name": "T1", "canonical_theorem_id": "cth_a"},
        {"theorem_name": "T2", "canonical_theorem_id": "cth_b", "provenance": None},
    ])

    summary = backfill_ledger(ledger)
    after = json.loads(ledger.read_text())["entries"]

    assert summary["backfilled"] == 2
    for r in after:
        assert _provenance_passes_gate(r["provenance"])
        assert r["provenance"]["paper_id"] == "0000.99999"
        assert r["provenance"]["label"] == r["theorem_name"]


def test_backfill_is_idempotent(tmp_path: Path) -> None:
    """Re-running on an already-backfilled ledger leaves the file untouched."""
    ledger = tmp_path / "0000.99998.json"
    _write_ledger(ledger, [{"theorem_name": "T1", "provenance": None}])

    backfill_ledger(ledger)
    first_mtime = ledger.stat().st_mtime
    summary2 = backfill_ledger(ledger)

    assert summary2["backfilled"] == 0
    assert ledger.stat().st_mtime == first_mtime


def test_backfill_preserves_existing_good_provenance(tmp_path: Path) -> None:
    """Rows whose `provenance` already passes the gate are not touched."""
    ledger = tmp_path / "0000.99997.json"
    _write_ledger(ledger, [{
        "theorem_name": "T1",
        "provenance": {
            "paper_id": "0000.99997",
            "section": "1.2",
            "label": "thm_orig_label",
            "cited_refs": ["cite_a"],
        },
    }])

    summary = backfill_ledger(ledger)
    assert summary["backfilled"] == 0
    after = json.loads(ledger.read_text())["entries"][0]
    assert after["provenance"]["section"] == "1.2"  # untouched


def test_backfill_merges_partial_provenance(tmp_path: Path) -> None:
    """A row with paper_id but missing label/section gets the missing fields filled
    in WITHOUT overwriting the paper_id that's already there."""
    ledger = tmp_path / "0000.99996.json"
    _write_ledger(ledger, [{
        "theorem_name": "MyTheorem",
        "provenance": {"paper_id": "preexisting_id"},  # paper_id only, no label
    }])

    summary = backfill_ledger(ledger)
    assert summary["backfilled"] == 1
    after = json.loads(ledger.read_text())["entries"][0]
    # paper_id from filename takes precedence (deterministic from filesystem),
    # but label gets filled in from theorem_name.
    assert after["provenance"]["label"] == "MyTheorem"
    assert _provenance_passes_gate(after["provenance"])


def test_canonical_ledger_regex_matches_arxiv_ids() -> None:
    """The strict filter must match standard arxiv IDs and reject dev variants."""
    assert _CANONICAL_LEDGER_RE.match("2304.09598")
    assert _CANONICAL_LEDGER_RE.match("2012.09271")
    assert _CANONICAL_LEDGER_RE.match("2604.21884v2")
    assert not _CANONICAL_LEDGER_RE.match("2304.09598_smoke")
    assert not _CANONICAL_LEDGER_RE.match("2604.21884_repair_candidates")
    assert not _CANONICAL_LEDGER_RE.match("ab_repair_topk0")
