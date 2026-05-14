"""Hermetic tests for scripts.per_paper_tactic_priors."""

from __future__ import annotations

import json
from pathlib import Path

import per_paper_tactic_priors as ppp


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def test_record_outcome_appends_records(tmp_path: Path) -> None:
    store = tmp_path / "priors.jsonl"
    ppp.record_outcome(
        paper_id="2604.21884",
        theorem_name="lemma_a",
        tactic="linarith",
        closed=True,
        store_path=store,
    )
    ppp.record_outcome(
        paper_id="2604.21884",
        theorem_name="lemma_b",
        tactic="omega",
        closed=False,
        store_path=store,
    )
    rows = _read_jsonl(store)
    assert len(rows) == 2
    assert rows[0]["paper_id"] == "2604.21884"
    assert rows[0]["tactic"] == "linarith"
    assert rows[0]["closed"] is True
    assert rows[1]["tactic"] == "omega"
    assert rows[1]["closed"] is False
    # Each record carries an integer timestamp.
    assert isinstance(rows[0]["ts"], int)


def test_record_outcome_creates_parent_dir(tmp_path: Path) -> None:
    store = tmp_path / "nested" / "subdir" / "priors.jsonl"
    assert not store.parent.exists()
    ppp.record_outcome(
        paper_id="p1",
        theorem_name="t1",
        tactic="ring",
        closed=True,
        store_path=store,
    )
    assert store.exists()


def test_record_outcome_drops_empty_paper_or_tactic(tmp_path: Path) -> None:
    store = tmp_path / "priors.jsonl"
    ppp.record_outcome(
        paper_id="",
        theorem_name="t1",
        tactic="linarith",
        closed=True,
        store_path=store,
    )
    ppp.record_outcome(
        paper_id="p1",
        theorem_name="t1",
        tactic="",
        closed=True,
        store_path=store,
    )
    assert _read_jsonl(store) == []


def test_load_paper_priors_aggregates_correctly(tmp_path: Path) -> None:
    store = tmp_path / "priors.jsonl"
    # paper P: linarith 3 closed / 5 attempted = 0.6
    for closed in [True, True, True, False, False]:
        ppp.record_outcome(
            paper_id="P",
            theorem_name="t",
            tactic="linarith",
            closed=closed,
            store_path=store,
        )
    # paper P: omega 0 / 4 = 0.0
    for _ in range(4):
        ppp.record_outcome(
            paper_id="P",
            theorem_name="t",
            tactic="omega",
            closed=False,
            store_path=store,
        )
    # other paper's records must not leak into P's aggregation.
    ppp.record_outcome(
        paper_id="Q",
        theorem_name="t",
        tactic="linarith",
        closed=False,
        store_path=store,
    )
    priors = ppp.load_paper_priors(paper_id="P", store_path=store)
    assert priors == {"linarith": 0.6, "omega": 0.0}


def test_load_paper_priors_empty_when_no_history(tmp_path: Path) -> None:
    store = tmp_path / "priors.jsonl"
    assert ppp.load_paper_priors(paper_id="P", store_path=store) == {}
    # Even with rows for another paper, target paper has none.
    ppp.record_outcome(
        paper_id="Q",
        theorem_name="t",
        tactic="aesop",
        closed=True,
        store_path=store,
    )
    assert ppp.load_paper_priors(paper_id="P", store_path=store) == {}


def test_load_paper_priors_skips_corrupt_lines(tmp_path: Path) -> None:
    store = tmp_path / "priors.jsonl"
    ppp.record_outcome(
        paper_id="P",
        theorem_name="t",
        tactic="linarith",
        closed=True,
        store_path=store,
    )
    # Append a garbage line + a blank line; both should be ignored.
    with store.open("a", encoding="utf-8") as fh:
        fh.write("not a json line\n\n")
    ppp.record_outcome(
        paper_id="P",
        theorem_name="t",
        tactic="linarith",
        closed=False,
        store_path=store,
    )
    priors = ppp.load_paper_priors(paper_id="P", store_path=store)
    assert priors == {"linarith": 0.5}


def test_rank_tactics_promotes_high_success(tmp_path: Path) -> None:
    store = tmp_path / "priors.jsonl"
    # Catalog order puts omega first, then linarith.
    candidates = ["omega", "linarith", "nlinarith", "aesop"]
    # Make linarith strong, omega weak for paper P.
    for _ in range(5):
        ppp.record_outcome(
            paper_id="P",
            theorem_name="t",
            tactic="linarith",
            closed=True,
            store_path=store,
        )
    for _ in range(5):
        ppp.record_outcome(
            paper_id="P",
            theorem_name="t",
            tactic="omega",
            closed=False,
            store_path=store,
        )
    ranked = ppp.rank_tactics(paper_id="P", candidates=candidates, store_path=store)
    # linarith (1.0) before omega (0.0); both come before unscored entries.
    assert ranked.index("linarith") < ranked.index("omega")
    # nlinarith / aesop have no history → they trail prior-ranked entries
    # but preserve their relative catalog order.
    assert ranked.index("omega") < ranked.index("nlinarith")
    assert ranked.index("nlinarith") < ranked.index("aesop")


def test_rank_tactics_preserves_order_when_no_history(tmp_path: Path) -> None:
    store = tmp_path / "priors.jsonl"
    candidates = ["omega", "linarith", "nlinarith", "aesop"]
    ranked = ppp.rank_tactics(paper_id="P", candidates=candidates, store_path=store)
    assert ranked == candidates


def test_rank_tactics_preserves_order_for_other_paper(tmp_path: Path) -> None:
    """A paper without its own history must not inherit another paper's priors."""
    store = tmp_path / "priors.jsonl"
    for _ in range(5):
        ppp.record_outcome(
            paper_id="P",
            theorem_name="t",
            tactic="linarith",
            closed=True,
            store_path=store,
        )
    candidates = ["omega", "linarith", "aesop"]
    ranked = ppp.rank_tactics(paper_id="Q", candidates=candidates, store_path=store)
    assert ranked == candidates


def test_rank_tactics_ignores_stale_tactics(tmp_path: Path) -> None:
    """A tactic recorded historically but absent from the current catalog
    must be silently dropped from re-ranking."""
    store = tmp_path / "priors.jsonl"
    for _ in range(5):
        ppp.record_outcome(
            paper_id="P",
            theorem_name="t",
            tactic="some_deprecated_tac",
            closed=True,
            store_path=store,
        )
    for _ in range(3):
        ppp.record_outcome(
            paper_id="P",
            theorem_name="t",
            tactic="linarith",
            closed=True,
            store_path=store,
        )
    candidates = ["omega", "linarith", "aesop"]
    ranked = ppp.rank_tactics(paper_id="P", candidates=candidates, store_path=store)
    # linarith promoted; the deprecated tactic must NOT appear in the output.
    assert "some_deprecated_tac" not in ranked
    assert ranked[0] == "linarith"
    # candidates without history keep their relative order after the priors.
    assert ranked.index("omega") < ranked.index("aesop")


def test_rank_tactics_ties_break_on_original_order(tmp_path: Path) -> None:
    """Two tactics with identical success rate keep catalog order."""
    store = tmp_path / "priors.jsonl"
    for tac in ("omega", "linarith"):
        for _ in range(3):
            ppp.record_outcome(
                paper_id="P",
                theorem_name="t",
                tactic=tac,
                closed=True,
                store_path=store,
            )
    candidates = ["linarith", "omega", "aesop"]
    ranked = ppp.rank_tactics(paper_id="P", candidates=candidates, store_path=store)
    # Both have rate 1.0; original order had linarith first.
    assert ranked == ["linarith", "omega", "aesop"]


def test_rank_tactics_empty_candidates(tmp_path: Path) -> None:
    store = tmp_path / "priors.jsonl"
    assert ppp.rank_tactics(paper_id="P", candidates=[], store_path=store) == []


def test_store_path_from_env(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "custom" / "priors.jsonl"
    monkeypatch.setenv("BDDM_TACTIC_PRIORS_PATH", str(target))
    assert ppp.store_path_from_env() == target
    monkeypatch.delenv("BDDM_TACTIC_PRIORS_PATH", raising=False)
    default = tmp_path / "default.jsonl"
    assert ppp.store_path_from_env(default=default) == default
