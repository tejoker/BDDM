from __future__ import annotations

import pytest

import arxiv_fetcher


def test_fetch_source_accepts_single_file_tex(monkeypatch, tmp_path):
    raw = b"\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n"
    monkeypatch.setattr(arxiv_fetcher, "_fetch_bytes", lambda url: raw)

    paths = arxiv_fetcher.fetch_source("2604.fake", tmp_path)

    assert len(paths) == 1
    assert paths[0].name == "source.tex"
    assert paths[0].read_bytes() == raw


def test_fetch_source_rejects_pdf_only_response(monkeypatch, tmp_path):
    monkeypatch.setattr(arxiv_fetcher, "_fetch_bytes", lambda url: b"%PDF-1.7\n...")

    with pytest.raises(RuntimeError, match="not a tar archive"):
        arxiv_fetcher.fetch_source("2604.pdfonly", tmp_path)
