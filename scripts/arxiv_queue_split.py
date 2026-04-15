#!/usr/bin/env python3
"""Split a line-based arXiv queue into N shards for parallel workers.

Each worker runs its own ``arxiv_cycle`` (or daemon) with ``--paper-file`` pointing
to one shard. Use disjoint ``--output-dir`` / ``--work-root`` per worker to avoid
clobbering. Merge KGs after runs with a single ``kg_writer`` over a combined
ledger directory (or copy ledgers into one project).

Example:
  python scripts/arxiv_queue_split.py --queue data/arxiv_queue_curated.txt \\
      --workers 4 --out-dir output/arxiv_shards/

  # Worker 0 (separate shell / machine):
  python scripts/arxiv_cycle.py --paper-file output/arxiv_shards/shard_00_of_04.txt \\
      --project-root . --output-dir output/arxiv_cycle_w0 --work-root /tmp/desol_w0

Environment / ops hints are documented in README under "ArXiv corpus scale-out".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _load_ids(queue_path: Path) -> list[str]:
    ids: list[str] = []
    for line in queue_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pid = line.split()[0].removeprefix("arxiv:").strip()
        if pid:
            ids.append(pid)
    dedup: list[str] = []
    seen: set[str] = set()
    for p in ids:
        if p not in seen:
            seen.add(p)
            dedup.append(p)
    return dedup


def split_queue(ids: list[str], n: int) -> list[list[str]]:
    """Partition *ids* into exactly *n* consecutive slices (last slices may be empty)."""
    if n < 1:
        raise ValueError("n must be >= 1")
    if not ids:
        return [[] for _ in range(n)]
    base, rem = divmod(len(ids), n)
    out: list[list[str]] = []
    idx = 0
    for i in range(n):
        sz = base + (1 if i < rem else 0)
        out.append(ids[idx : idx + sz])
        idx += sz
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Split arXiv queue file into worker shards")
    p.add_argument("--queue", required=True, type=Path, help="Input queue (one ID per line)")
    p.add_argument("--workers", type=int, required=True, help="Number of shards (>=1)")
    p.add_argument("--out-dir", required=True, type=Path, help="Directory for shard_*.txt")
    args = p.parse_args()

    if args.workers < 1:
        print("[fail] --workers must be >= 1", file=sys.stderr)
        return 1

    if not args.queue.is_file():
        print(f"[fail] queue not found: {args.queue}", file=sys.stderr)
        return 1

    ids = _load_ids(args.queue)
    if not ids:
        print("[fail] no paper IDs in queue", file=sys.stderr)
        return 1

    shards = split_queue(ids, args.workers)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    width = max(2, len(str(args.workers)))
    for i, part in enumerate(shards):
        name = f"shard_{i:0{width}d}_of_{args.workers:0{width}d}.txt"
        out = args.out_dir / name
        with out.open("w", encoding="utf-8") as fh:
            fh.write(f"# shard {i + 1}/{args.workers} from {args.queue.name} ({len(part)} ids)\n")
            for pid in part:
                fh.write(f"{pid}\n")
        print(f"[ok] {out} ({len(part)} ids)")

    print(f"[done] {len(ids)} total id(s) -> {len(shards)} file(s) under {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
