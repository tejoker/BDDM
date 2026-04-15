#!/usr/bin/env python3
"""Download and extract LaTeX source from an arxiv paper.

Usage:
    python3 arxiv_fetcher.py 2301.04567
    python3 arxiv_fetcher.py 2301.04567 --out /tmp/paper

Outputs a directory containing all .tex files extracted from the arxiv tarball.

**PDF-only submissions**: many arXiv records ship only as PDF (no TeX tarball).
``fetch_source`` then raises ``RuntimeError``; the DESol LaTeX pipeline cannot
ingest those until a separate PDF→structure path exists. Use
``scripts/arxiv_oai_harvest.py --probe-tex`` to filter harvest queues to
tarballs that contain at least one ``.tex`` file.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import tarfile
import urllib.request
from pathlib import Path

_ARXIV_SOURCE_URL = "https://arxiv.org/e-print/{paper_id}"
_ARXIV_ABS_URL = "https://arxiv.org/abs/{paper_id}"

# arxiv returns the tarball with various content-type headers; accept all.
_HEADERS = {
    "User-Agent": "LeanResearcher/0.1 (arxiv source fetch; contact: open-source project)",
}


def _fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def fetch_source(paper_id: str, out_dir: Path) -> list[Path]:
    """Download arxiv source tarball for *paper_id* and extract .tex files to *out_dir*.

    Returns a list of extracted .tex file paths (relative to out_dir).
    Raises RuntimeError if the source is not a gzip tar archive (e.g. single-file PDF submissions).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    url = _ARXIV_SOURCE_URL.format(paper_id=paper_id)
    print(f"[fetch] {url}")
    raw = _fetch_bytes(url)

    try:
        tf = tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz")
    except tarfile.TarError:
        try:
            tf = tarfile.open(fileobj=io.BytesIO(raw), mode="r:*")
        except tarfile.TarError as exc:
            raise RuntimeError(
                f"arxiv source for {paper_id!r} is not a tar archive "
                f"(possibly a single-file PDF submission): {exc}"
            ) from exc

    source_paths: list[Path] = []
    allowed_exts = {".tex", ".sty", ".cls", ".def"}
    with tf:
        for member in tf.getmembers():
            suffix = Path(member.name).suffix.lower()
            if suffix not in allowed_exts:
                continue
            member_path = out_dir / member.name
            member_path.parent.mkdir(parents=True, exist_ok=True)
            f = tf.extractfile(member)
            if f is None:
                continue
            member_path.write_bytes(f.read())
            source_paths.append(member_path)
            print(f"  extracted: {member.name}")

    tex_paths = [p for p in source_paths if p.suffix.lower() == ".tex"]
    if not tex_paths:
        raise RuntimeError(
            f"No .tex files found in arxiv source for {paper_id!r}. "
            "The submission may be PDF-only or use a non-standard structure."
        )

    return tex_paths


def find_main_tex(tex_paths: list[Path]) -> Path:
    """Heuristic: return the .tex file most likely to be the main document.

    Priority:
    1. Any file containing \\documentclass
    2. Any file named main.tex / paper.tex / article.tex
    3. The largest .tex file
    """
    for p in tex_paths:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "\\documentclass" in text:
            return p

    preferred_names = {"main.tex", "paper.tex", "article.tex", "manuscript.tex"}
    for p in tex_paths:
        if p.name.lower() in preferred_names:
            return p

    return max(tex_paths, key=lambda p: p.stat().st_size)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Download arxiv LaTeX source")
    p.add_argument("paper_id", help="arxiv paper ID, e.g. 2301.04567 or arxiv:2301.04567")
    p.add_argument(
        "--out",
        default="",
        help="Output directory (default: /tmp/arxiv_<paper_id>)",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()
    paper_id = args.paper_id.removeprefix("arxiv:").strip()
    out_dir = Path(args.out) if args.out else Path(f"/tmp/arxiv_{paper_id.replace('/', '_')}")

    try:
        tex_paths = fetch_source(paper_id, out_dir)
    except Exception as exc:
        print(f"[fail] {exc}", file=sys.stderr)
        return 1

    main_tex = find_main_tex(tex_paths)
    print(f"[ok] {len(tex_paths)} .tex file(s) extracted to {out_dir}")
    print(f"[main] {main_tex}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
