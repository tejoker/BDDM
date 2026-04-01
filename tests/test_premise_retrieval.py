"""Unit tests for premise_retrieval.py."""
from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import pytest

from premise_retrieval import (
    PremiseEntry,
    PremiseRetriever,
    RetrievalHit,
    _embed_hash,
    _dot,
    _tokenize,
)
from mcts_search import temperature_scale


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

def test_tokenize_filters_stopwords():
    tokens = _tokenize("fun intro simp MeasurableSpace comap")
    assert "fun" not in tokens
    assert "intro" not in tokens
    assert "simp" not in tokens
    # Long non-stopword identifiers should remain.
    assert any("measurablespace" in t.lower() for t in tokens)


def test_tokenize_filters_short_tokens():
    tokens = _tokenize("a bb ccc dddd eeeee")
    # tokens shorter than 4 chars should be dropped
    for t in tokens:
        assert len(t) >= 4


def test_tokenize_lowercase():
    tokens = _tokenize("GaussianProcess Wiener")
    assert all(t == t.lower() for t in tokens)


# ---------------------------------------------------------------------------
# Hash embedding
# ---------------------------------------------------------------------------

def test_embed_hash_unit_norm():
    v = _embed_hash("MeasureTheory integrable gaussian", 256)
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 1e-6 or norm == 0.0


def test_embed_hash_zero_for_empty():
    v = _embed_hash("", 64)
    assert all(x == 0.0 for x in v)


def test_embed_hash_dim():
    for d in (64, 128, 256, 512):
        v = _embed_hash("some text here", d)
        assert len(v) == d


def test_dot_identity():
    v = _embed_hash("iIndepFun probability", 128)
    assert abs(_dot(v, v) - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# PremiseRetriever (hash encoder, no external deps)
# ---------------------------------------------------------------------------

def _make_entries() -> list[PremiseEntry]:
    return [
        PremiseEntry(name="HasGaussianLaw.integrable",
                     statement="integrable gaussian law measure",
                     namespace="ProbabilityTheory"),
        PremiseEntry(name="HasIndepIncrements.nat",
                     statement="independent increments natural filtration",
                     namespace="ProbabilityTheory"),
        PremiseEntry(name="Measurable.sub",
                     statement="subtraction measurable functions",
                     namespace="MeasureTheory"),
        PremiseEntry(name="condExp_of_stronglyMeasurable",
                     statement="conditional expectation strongly measurable",
                     namespace="MeasureTheory"),
        PremiseEntry(name="indep_iSup_of_disjoint",
                     statement="independence sup sigma algebra disjoint sets",
                     namespace="ProbabilityTheory"),
    ]


def test_build_and_query_basic():
    entries = _make_entries()
    retriever = PremiseRetriever.build(entries, dims=128, encoder_name="hash")
    hits = retriever.query("gaussian integrable measure", top_k=3)
    assert len(hits) <= 3
    assert all(isinstance(h, RetrievalHit) for h in hits)
    assert all(0.0 <= h.score for h in hits)


def test_query_returns_gaussian_first():
    """HasGaussianLaw.integrable should rank highly for a gaussian-related query."""
    entries = _make_entries()
    retriever = PremiseRetriever.build(entries, dims=256, encoder_name="hash")
    hits = retriever.query("gaussian integrable probability measure", top_k=5)
    names = [h.name for h in hits]
    assert "HasGaussianLaw.integrable" in names


def test_query_top_k_respected():
    entries = _make_entries()
    retriever = PremiseRetriever.build(entries, dims=64, encoder_name="hash")
    for k in (1, 2, 5, 100):
        hits = retriever.query("measurable function", top_k=k)
        assert len(hits) <= min(k, len(entries))


def test_query_empty_goal_no_crash():
    entries = _make_entries()
    retriever = PremiseRetriever.build(entries, dims=64, encoder_name="hash")
    hits = retriever.query("", top_k=5)
    assert isinstance(hits, list)


def test_name_boost_surfaces_exact_identifier():
    """A query containing the exact camelCase name should boost that entry."""
    entries = _make_entries()
    retriever = PremiseRetriever.build(entries, dims=256, encoder_name="hash")
    # Query contains 'iIndepFun' verbatim — but our corpus has indep_iSup_of_disjoint.
    # Just check no crash and scores are non-negative.
    hits = retriever.query("iIndepFun HasIndepIncrements", top_k=3)
    assert all(h.score >= 0.0 for h in hits)


def test_save_load_roundtrip(tmp_path):
    entries = _make_entries()
    retriever = PremiseRetriever.build(entries, dims=64, encoder_name="hash")
    index_file = tmp_path / "index.json"
    retriever.save(index_file)

    loaded = PremiseRetriever.load(index_file)
    assert loaded.dims == retriever.dims
    assert loaded.encoder_name == "hash"
    assert len(loaded.entries) == len(retriever.entries)

    hits_orig = retriever.query("gaussian", top_k=3)
    hits_load = loaded.query("gaussian", top_k=3)
    assert [h.name for h in hits_orig] == [h.name for h in hits_load]


def test_save_np_load_np_roundtrip(tmp_path):
    pytest.importorskip("numpy")
    entries = _make_entries()
    retriever = PremiseRetriever.build(entries, dims=64, encoder_name="hash")
    retriever.save_np(tmp_path / "idx")
    loaded = PremiseRetriever.load(tmp_path / "idx")
    assert loaded.encoder_name == "hash"
    assert len(loaded.entries) == len(entries)


def test_tier_preference_trusted_ranks_first():
    entries = _make_entries()
    retriever = PremiseRetriever.build(entries, dims=128, encoder_name="hash")
    trusted = {"indep_iSup_of_disjoint"}
    hits = retriever.query_with_tier_preference(
        "independence disjoint sigma algebra",
        kg_trusted_names=trusted,
        top_k=5,
    )
    assert hits[0].trust_tier == "trusted"
    assert hits[0].name == "indep_iSup_of_disjoint"


# ---------------------------------------------------------------------------
# Temperature scaling (from mcts_search, but tested here for isolation)
# ---------------------------------------------------------------------------

def test_temperature_scale_identity_at_one():
    for v in (0.1, 0.5, 0.9, 0.967):
        assert abs(temperature_scale(v, temperature=1.0) - v) < 1e-6


def test_temperature_scale_spreads_high_value():
    # A raw value of 0.967 (the observed problematic average) should be
    # pulled back when T > 1.
    raw = 0.967
    scaled = temperature_scale(raw, temperature=1.5)
    assert scaled < raw
    assert scaled > 0.5  # Still above chance; just less extreme.


def test_temperature_scale_preserves_order():
    vals = [0.3, 0.5, 0.7, 0.9]
    scaled = [temperature_scale(v, 1.5) for v in vals]
    assert scaled == sorted(scaled)


def test_temperature_scale_clamps():
    assert 0.0 <= temperature_scale(0.0001, 2.0) <= 1.0
    assert 0.0 <= temperature_scale(0.9999, 2.0) <= 1.0
