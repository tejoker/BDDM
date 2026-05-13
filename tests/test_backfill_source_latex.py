"""Hermetic tests for `scripts/backfill_source_latex.py`.

Covers the happy path, missing-extracted-theorems fallback, name-mismatch
behaviour, idempotency, and the `_2` disambiguation suffix that mirrors
`arxiv_to_lean._lean_name`'s post-pass in `write_paper_module`.
"""

from __future__ import annotations

import json
from pathlib import Path

from backfill_source_latex import (
    _CANONICAL_LEDGER_RE,
    _build_name_lookup,
    _lean_name,
    backfill_ledger,
)


def _write_ledger(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"entries": entries}, indent=2), encoding="utf-8")


def _write_extracted(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"paper_id": path.parent.name, "theorem_count": len(entries), "entries": entries}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_backfill_copies_statement_from_extracted(tmp_path: Path) -> None:
    """A ledger row with empty source_latex picks up the matching extracted statement."""
    ledger = tmp_path / "ledgers" / "0000.99999.json"
    extracted_root = tmp_path / "extracted"
    _write_ledger(ledger, [
        {"theorem_name": "T1", "source_latex": ""},
        {"theorem_name": "T2", "source_latex": ""},
    ])
    _write_extracted(
        extracted_root / "0000.99999" / "extracted_theorems.json",
        [
            {"name": "T1", "statement": "\\textbf{T1 statement.}"},
            {"name": "T2", "statement": "\\textbf{T2 statement.}"},
        ],
    )

    summary = backfill_ledger(ledger, extracted_roots=[extracted_root])
    after = json.loads(ledger.read_text())["entries"]

    assert summary["backfilled"] == 2
    assert summary["unmatched"] == 0
    assert after[0]["source_latex"] == "\\textbf{T1 statement.}"
    assert after[1]["source_latex"] == "\\textbf{T2 statement.}"


def test_backfill_skips_when_extracted_missing(tmp_path: Path) -> None:
    """If no extracted_theorems.json exists for the paper the ledger is untouched."""
    ledger = tmp_path / "ledgers" / "0000.99998.json"
    extracted_root = tmp_path / "extracted"  # exists but no paper subdir
    extracted_root.mkdir()
    _write_ledger(ledger, [{"theorem_name": "T1", "source_latex": ""}])

    first_mtime = ledger.stat().st_mtime
    summary = backfill_ledger(ledger, extracted_roots=[extracted_root])
    after = json.loads(ledger.read_text())["entries"]

    assert summary["backfilled"] == 0
    assert summary["unmatched"] == 1
    assert summary["extracted_path"] is None
    assert after[0]["source_latex"] == ""
    # Ledger file was not rewritten.
    assert ledger.stat().st_mtime == first_mtime


def test_backfill_leaves_unmatched_names_alone(tmp_path: Path) -> None:
    """A ledger row whose name has no match in extracted_theorems.json stays empty."""
    ledger = tmp_path / "ledgers" / "0000.99997.json"
    extracted_root = tmp_path / "extracted"
    _write_ledger(ledger, [
        {"theorem_name": "T_known", "source_latex": ""},
        {"theorem_name": "T_unknown", "source_latex": ""},
    ])
    _write_extracted(
        extracted_root / "0000.99997" / "extracted_theorems.json",
        [{"name": "T_known", "statement": "stmt"}],
    )

    summary = backfill_ledger(ledger, extracted_roots=[extracted_root])
    after = json.loads(ledger.read_text())["entries"]

    assert summary["backfilled"] == 1
    assert summary["unmatched"] == 1
    assert after[0]["source_latex"] == "stmt"
    assert after[1]["source_latex"] == ""


def test_backfill_preserves_existing_source_latex(tmp_path: Path) -> None:
    """Non-empty source_latex is never overwritten."""
    ledger = tmp_path / "ledgers" / "0000.99996.json"
    extracted_root = tmp_path / "extracted"
    _write_ledger(ledger, [
        {"theorem_name": "T1", "source_latex": "ORIGINAL"},
    ])
    _write_extracted(
        extracted_root / "0000.99996" / "extracted_theorems.json",
        [{"name": "T1", "statement": "REPLACEMENT"}],
    )

    summary = backfill_ledger(ledger, extracted_roots=[extracted_root])
    after = json.loads(ledger.read_text())["entries"]

    assert summary["backfilled"] == 0
    assert summary["already_filled"] == 1
    assert after[0]["source_latex"] == "ORIGINAL"


def test_backfill_is_idempotent(tmp_path: Path) -> None:
    """Re-running on an already-backfilled ledger is a no-op (no mtime bump)."""
    ledger = tmp_path / "ledgers" / "0000.99995.json"
    extracted_root = tmp_path / "extracted"
    _write_ledger(ledger, [{"theorem_name": "T1", "source_latex": ""}])
    _write_extracted(
        extracted_root / "0000.99995" / "extracted_theorems.json",
        [{"name": "T1", "statement": "X"}],
    )

    backfill_ledger(ledger, extracted_roots=[extracted_root])
    first_mtime = ledger.stat().st_mtime
    summary2 = backfill_ledger(ledger, extracted_roots=[extracted_root])

    assert summary2["backfilled"] == 0
    assert summary2["already_filled"] == 1
    assert ledger.stat().st_mtime == first_mtime


def test_backfill_strips_namespace_prefix(tmp_path: Path) -> None:
    """A ledger row name like `ArxivPaper.foo` matches the bare `foo` in extracted."""
    ledger = tmp_path / "ledgers" / "0000.99994.json"
    extracted_root = tmp_path / "extracted"
    _write_ledger(ledger, [
        {"theorem_name": "ArxivPaper.my_theorem", "source_latex": ""},
    ])
    _write_extracted(
        extracted_root / "0000.99994" / "extracted_theorems.json",
        [{"name": "my:theorem", "statement": "S"}],  # colon normalized to underscore
    )

    summary = backfill_ledger(ledger, extracted_roots=[extracted_root])
    after = json.loads(ledger.read_text())["entries"]

    assert summary["backfilled"] == 1
    assert after[0]["source_latex"] == "S"


def test_lean_name_disambiguates_repeated_labels(tmp_path: Path) -> None:
    """Two extracted entries with the same `_lean_name` get `_2` suffix on the second."""
    _write_extracted_called = True  # silence linter
    extracted = [
        {"name": "lem:com1", "statement": "first"},
        {"name": "lem:com1", "statement": "second"},
    ]
    lookup = _build_name_lookup(extracted)

    assert lookup["lem_com1"]["statement"] == "first"
    assert lookup["lem_com1_2"]["statement"] == "second"


def test_backfill_uses_disambiguation_suffix(tmp_path: Path) -> None:
    """A ledger row `lem_com1_2` resolves to the second extracted `lem:com1`."""
    ledger = tmp_path / "ledgers" / "0000.99993.json"
    extracted_root = tmp_path / "extracted"
    _write_ledger(ledger, [
        {"theorem_name": "lem_com1", "source_latex": ""},
        {"theorem_name": "lem_com1_2", "source_latex": ""},
    ])
    _write_extracted(
        extracted_root / "0000.99993" / "extracted_theorems.json",
        [
            {"name": "lem:com1", "statement": "first body"},
            {"name": "lem:com1", "statement": "second body"},
        ],
    )

    summary = backfill_ledger(ledger, extracted_roots=[extracted_root])
    after = json.loads(ledger.read_text())["entries"]

    assert summary["backfilled"] == 2
    assert after[0]["source_latex"] == "first body"
    assert after[1]["source_latex"] == "second body"


def test_backfill_search_order_prefers_first_root(tmp_path: Path) -> None:
    """When extracted_theorems.json exists in multiple roots, the first wins."""
    ledger = tmp_path / "ledgers" / "0000.99992.json"
    root_a = tmp_path / "rootA"
    root_b = tmp_path / "rootB"
    _write_ledger(ledger, [{"theorem_name": "T1", "source_latex": ""}])
    _write_extracted(root_a / "0000.99992" / "extracted_theorems.json", [{"name": "T1", "statement": "A"}])
    _write_extracted(root_b / "0000.99992" / "extracted_theorems.json", [{"name": "T1", "statement": "B"}])

    summary = backfill_ledger(ledger, extracted_roots=[root_a, root_b])
    after = json.loads(ledger.read_text())["entries"]

    assert after[0]["source_latex"] == "A"
    assert summary["extracted_path"].endswith("rootA/0000.99992/extracted_theorems.json")


def test_backfill_publishes_to_full_paper_reports(tmp_path: Path) -> None:
    """`--publish` copies the rewritten ledger to the reproducibility bundle path."""
    ledger = tmp_path / "ledgers" / "0000.99991.json"
    extracted_root = tmp_path / "extracted"
    publish_root = tmp_path / "full_paper_reports"
    (publish_root / "0000.99991").mkdir(parents=True)  # bundle dir must pre-exist
    _write_ledger(ledger, [{"theorem_name": "T1", "source_latex": ""}])
    _write_extracted(
        extracted_root / "0000.99991" / "extracted_theorems.json",
        [{"name": "T1", "statement": "S"}],
    )

    summary = backfill_ledger(
        ledger,
        extracted_roots=[extracted_root],
        publish=True,
        publish_root=publish_root,
    )

    target = publish_root / "0000.99991" / "verification_ledger.json"
    assert summary["published"] is True
    assert target.exists()
    published_entries = json.loads(target.read_text())["entries"]
    assert published_entries[0]["source_latex"] == "S"


def test_backfill_skips_empty_extracted_statement(tmp_path: Path) -> None:
    """An extracted entry with an empty `statement` does NOT overwrite with ''."""
    ledger = tmp_path / "ledgers" / "0000.99990.json"
    extracted_root = tmp_path / "extracted"
    _write_ledger(ledger, [{"theorem_name": "T1", "source_latex": ""}])
    _write_extracted(
        extracted_root / "0000.99990" / "extracted_theorems.json",
        [{"name": "T1", "statement": ""}],
    )

    summary = backfill_ledger(ledger, extracted_roots=[extracted_root])
    after = json.loads(ledger.read_text())["entries"]

    assert summary["backfilled"] == 0
    assert summary["unmatched"] == 1
    assert after[0]["source_latex"] == ""


def test_lean_name_normalization_matches_arxiv_to_lean() -> None:
    """The local `_lean_name` mirrors `arxiv_to_lean._lean_name` exactly on the
    cases that show up in real extracted_theorems payloads."""
    assert _lean_name("lem:com1") == "lem_com1"
    assert _lean_name("Prop:Actions") == "Prop_Actions"
    assert _lean_name("1.2.3") == "thm_1_2_3"
    assert _lean_name("") == "thm_unnamed"
    assert _lean_name("___foo___") == "foo"


def test_canonical_ledger_regex_matches_arxiv_ids() -> None:
    """Filter must match standard arxiv IDs and reject dev variants."""
    assert _CANONICAL_LEDGER_RE.match("2304.09598")
    assert _CANONICAL_LEDGER_RE.match("2604.21884v2")
    assert not _CANONICAL_LEDGER_RE.match("2304.09598_smoke")
    assert not _CANONICAL_LEDGER_RE.match("2604.21884_repair_candidates")
