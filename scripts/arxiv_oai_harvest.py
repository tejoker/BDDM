#!/usr/bin/env python3
"""Harvest arXiv paper IDs via OAI-PMH for use with arxiv_cycle / daemon queues.

Uses https://export.arxiv.org/oai2 (same service as arXiv bulk metadata).
Respects optional delays between paginated requests.

Examples:
  python scripts/arxiv_oai_harvest.py --set math.NT --out data/arxiv_queue_math_nt.txt
  python scripts/arxiv_oai_harvest.py --set math --from 2024-01-01 --until 2024-02-01 \\
      --out /tmp/math_jan_2024.txt --delay 3.0
  python scripts/arxiv_oai_harvest.py --set cs.LG --max-records 500 --probe-tex \\
      --probe-delay 2.0 --out data/queue_cs_lg_tex.txt

See README (ArXiv corpus scale-out) for combining with queue splitting and workers.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

_OAI_BASE = "https://export.arxiv.org/oai2"
_NS = {"o": "http://www.openarchives.org/OAI/2.0/"}

# OAI identifier: oai:arXiv.org:2301.01221v2  or  oai:arXiv.org:math/0501234v1
_ID_TAIL = re.compile(
    r"(?:arxiv:)?(?P<new>\d{4}\.\d{4,5})(?:v\d+)?$|"
    r"(?P<old>[\w.-]+/\d{7})(?:v\d+)?$",
    re.IGNORECASE,
)


def _fetch_oai(params: dict[str, str], *, timeout: float = 120.0) -> bytes:
    q = urllib.parse.urlencode(params)
    url = f"{_OAI_BASE}?{q}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "DESol/arxiv_oai_harvest (open-source; OAI-PMH; polite crawl)",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _parse_identifier(ident: str) -> str | None:
    """Return canonical paper id (no version) or None."""
    ident = ident.strip()
    if not ident:
        return None
    tail = ident.split(":")[-1].strip()
    m = _ID_TAIL.match(tail)
    if not m:
        return None
    if m.group("new"):
        return m.group("new")
    if m.group("old"):
        return m.group("old").replace("arXiv:", "").strip()
    return None


def _list_identifiers_page(
    *,
    metadata_prefix: str,
    oai_set: str,
    from_date: str,
    until_date: str,
    resumption_token: str,
) -> tuple[list[str], str]:
    """One OAI ListIdentifiers page. Returns (raw identifiers, next resumption token)."""
    params: dict[str, str] = {"verb": "ListIdentifiers", "metadataPrefix": metadata_prefix}
    if resumption_token:
        params["resumptionToken"] = resumption_token
    else:
        if oai_set:
            params["set"] = oai_set
        if from_date:
            params["from"] = from_date
        if until_date:
            params["until"] = until_date

    raw = _fetch_oai(params)
    root = ET.fromstring(raw)

    err = root.find("o:error", _NS)
    if err is not None:
        code = err.get("code", "")
        text = (err.text or "").strip()
        raise RuntimeError(f"OAI error code={code!r} message={text!r}")

    idents: list[str] = []
    for h in root.findall(".//o:header/o:identifier", _NS):
        if h.text:
            idents.append(h.text.strip())

    token_el = root.find(".//o:resumptionToken", _NS)
    next_token = (token_el.text or "").strip() if token_el is not None else ""
    return idents, next_token


def harvest_identifiers(
    *,
    oai_set: str,
    metadata_prefix: str,
    from_date: str,
    until_date: str,
    delay_s: float,
    max_records: int,
) -> list[str]:
    """Paginate ListIdentifiers until exhausted or max_records reached."""
    out: list[str] = []
    seen: set[str] = set()
    token = ""

    while True:
        page, token = _list_identifiers_page(
            metadata_prefix=metadata_prefix,
            oai_set=oai_set,
            from_date=from_date,
            until_date=until_date,
            resumption_token=token,
        )
        for ident in page:
            pid = _parse_identifier(ident)
            if not pid or pid in seen:
                continue
            seen.add(pid)
            out.append(pid)
            if max_records > 0 and len(out) >= max_records:
                return out

        if not token:
            break
        if delay_s > 0:
            time.sleep(delay_s)

    return out


def _probe_tex_available(paper_id: str, *, timeout: float = 60.0) -> bool:
    """Return True if arXiv source tarball is readable and contains a .tex file."""
    import io
    import tarfile

    url = f"https://arxiv.org/e-print/{paper_id}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "DESol/arxiv_oai_harvest (source probe)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except Exception:
        return False
    try:
        tf = tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz")
    except tarfile.TarError:
        try:
            tf = tarfile.open(fileobj=io.BytesIO(raw), mode="r:*")
        except tarfile.TarError:
            return False
    try:
        with tf:
            for m in tf.getmembers():
                if m.isfile() and m.name.lower().endswith(".tex"):
                    return True
    except Exception:
        return False
    return False


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Harvest arXiv IDs via OAI-PMH into a queue file")
    p.add_argument(
        "--set",
        default="math",
        help="OAI set name (e.g. math, math.NT, cs.LG). Default: math",
    )
    p.add_argument(
        "--metadata-prefix",
        default="arXiv",
        help="OAI metadataPrefix (arXiv default is arXiv)",
    )
    p.add_argument("--from", dest="from_date", default="", help="OAI from=YYYY-MM-DD (optional)")
    p.add_argument("--until", dest="until_date", default="", help="OAI until=YYYY-MM-DD (optional)")
    p.add_argument("--out", required=True, help="Output queue file (one ID per line)")
    p.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="Seconds to sleep between OAI pages when a resumptionToken is used (default: 3)",
    )
    p.add_argument(
        "--max-records",
        type=int,
        default=0,
        help="Stop after this many new IDs (0 = no limit)",
    )
    p.add_argument(
        "--probe-tex",
        action="store_true",
        help="After OAI harvest, keep only IDs whose e-print tarball contains at least one .tex",
    )
    p.add_argument(
        "--probe-delay",
        type=float,
        default=2.0,
        help="Seconds between source probes when --probe-tex (default: 2)",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()
    try:
        ids = harvest_identifiers(
            oai_set=args.set,
            metadata_prefix=args.metadata_prefix,
            from_date=args.from_date,
            until_date=args.until_date,
            delay_s=max(0.0, args.delay),
            max_records=max(0, args.max_records),
        )
    except Exception as exc:
        print(f"[fail] OAI harvest: {exc}", file=sys.stderr)
        return 1

    if args.probe_tex:
        kept: list[str] = []
        for i, pid in enumerate(ids, start=1):
            ok = _probe_tex_available(pid)
            print(f"[probe {i}/{len(ids)}] {pid} tex={'yes' if ok else 'no'}")
            if ok:
                kept.append(pid)
            if args.probe_delay > 0 and i < len(ids):
                time.sleep(args.probe_delay)
        ids = kept

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write("# Generated by arxiv_oai_harvest.py\n")
        fh.write(f"# set={args.set} from={args.from_date!r} until={args.until_date!r}\n")
        for pid in ids:
            fh.write(f"{pid}\n")

    print(f"[ok] wrote {len(ids)} id(s) to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
