#!/usr/bin/env python3
"""Per-paper deterministic micro-prover tactic priors.

The deterministic micro-prover catalog in ``prove_arxiv_batch`` is already
domain-stratified (analysis / probability / algebra / combinatorics). This
module adds a *finer* per-(paper, tactic) layer: if paper ``2604.21884`` has
historically closed 5/8 of its theorems with ``linarith [Real.exp_pos]``, we
want ``linarith`` to be attempted earlier *for that paper specifically*.

Design constraints (cf. AGENTS.md):

* Standards-positive — priors only RE-ORDER the candidate list; they never
  change which tactics get tried. The full catalog still runs (up to timeout),
  so adopting priors cannot regress closure rate.
* Storage is append-only JSONL. ``record_outcome`` is cheap and ledger-friendly;
  ``load_paper_priors`` reads once per row.
* Cold-start safe — papers without history fall back to the catalog's existing
  domain-stratified order.
* Stale-data safe — tactics no longer present in the candidate list are
  silently ignored when re-ranking.
* No LLM calls; purely deterministic bookkeeping.

The store schema (one JSON object per line)::

    {"paper_id": "2604.21884",
     "theorem_name": "Paper_2604_21884.lemma_3_1",
     "tactic": "linarith",
     "closed": true,
     "ts": 1715692800}

Records are append-only; ``load_paper_priors`` aggregates by tactic to compute
the success rate.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterable


def _ensure_parent(path: Path) -> None:
    parent = path.parent
    if str(parent) and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)


def record_outcome(
    *,
    paper_id: str,
    theorem_name: str,
    tactic: str,
    closed: bool,
    store_path: Path,
) -> None:
    """Append a (paper, tactic, theorem, outcome) record to a JSONL store.

    The operation is intentionally cheap: a single ``open(..., "a")`` + line
    write. Concurrent writers may interleave records, but each record is a
    single line so readers stay consistent.
    """

    if not paper_id or not tactic:
        # Empty paper_id / tactic carry no information; silently drop so the
        # caller doesn't have to guard every call site.
        return
    row = {
        "paper_id": str(paper_id),
        "theorem_name": str(theorem_name or ""),
        "tactic": str(tactic),
        "closed": bool(closed),
        "ts": int(time.time()),
    }
    _ensure_parent(store_path)
    with store_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _iter_store_rows(store_path: Path) -> Iterable[dict]:
    if not store_path.exists():
        return
    try:
        with store_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    # Corrupt line — skip rather than blow up the caller.
                    continue
                if isinstance(row, dict):
                    yield row
    except FileNotFoundError:
        return


def load_paper_priors(
    *,
    paper_id: str,
    store_path: Path,
) -> dict[str, float]:
    """Return ``{tactic: success_rate}`` for ``paper_id`` from ``store_path``.

    ``success_rate`` is ``closed_count / attempted_count`` aggregated across
    the entire store. Tactics with zero attempts (only present implicitly via
    other papers) are excluded.
    """

    if not paper_id:
        return {}
    closed: dict[str, int] = {}
    attempted: dict[str, int] = {}
    for row in _iter_store_rows(store_path):
        if str(row.get("paper_id", "")) != str(paper_id):
            continue
        tac = str(row.get("tactic", "") or "").strip()
        if not tac:
            continue
        attempted[tac] = attempted.get(tac, 0) + 1
        if bool(row.get("closed", False)):
            closed[tac] = closed.get(tac, 0) + 1
    out: dict[str, float] = {}
    for tac, n in attempted.items():
        if n <= 0:
            continue
        out[tac] = float(closed.get(tac, 0)) / float(n)
    return out


def rank_tactics(
    *,
    paper_id: str,
    candidates: list[str],
    store_path: Path,
) -> list[str]:
    """Re-order ``candidates`` by descending paper-specific success rate.

    Tactics with no history fall to their original order *after* the
    prior-ranked ones, preserving the catalog's domain-stratified ordering as
    the cold-start fallback.

    Stale priors (tactics no longer in ``candidates``) are silently ignored.
    """

    if not candidates:
        return []
    priors = load_paper_priors(paper_id=paper_id, store_path=store_path)
    if not priors:
        return list(candidates)

    # Preserve original order for tie-breaking (Python's sort is stable).
    original_index = {tac: i for i, tac in enumerate(candidates)}

    def _key(tac: str) -> tuple[int, float, int]:
        # 1st key: has-prior flag (1 if known, 0 otherwise) — descending.
        # 2nd key: success rate — descending.
        # 3rd key: original catalog position — ascending (stable fallback).
        if tac in priors:
            return (-1, -priors[tac], original_index[tac])
        return (0, 0.0, original_index[tac])

    return sorted(candidates, key=_key)


def store_path_from_env(default: Path | None = None) -> Path:
    """Resolve the prior store path, honoring ``BDDM_TACTIC_PRIORS_PATH``.

    The default lives at ``data/tactic_priors.jsonl`` relative to the repo
    root. ``data/`` is gitignored.
    """

    env = os.environ.get("BDDM_TACTIC_PRIORS_PATH", "").strip()
    if env:
        return Path(env)
    if default is not None:
        return default
    return Path("data") / "tactic_priors.jsonl"


__all__ = [
    "record_outcome",
    "load_paper_priors",
    "rank_tactics",
    "store_path_from_env",
]
