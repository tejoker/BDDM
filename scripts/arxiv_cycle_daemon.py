#!/usr/bin/env python3
"""Continuous arXiv paper processing daemon.

Downloads papers from a queue, attempts proofs, promotes proven theorems to
the KG, then deletes intermediate files and moves to the next paper.

Runs in parallel with the miniF2F MCTS benchmark — uses its own output dirs
and does not touch mcts_244/ or the benchmark state.

Algorithm:
  while queue not empty:
    paper_id = pop_next(queue)
    if already_processed(paper_id): continue
    try:
      run_pipeline(paper_id, timeout=30min)
      proven = [t for t in results if FULLY_PROVEN]
      if proven: kg_writer.promote(proven)
      log(f"{paper_id}: {len(proven)} promoted")
    except Exception as e:
      log(f"{paper_id}: error — {e}")
    finally:
      cleanup(paper_id)   # remove .tex, temp .lean, keep ledger + KG
    sleep(60)

Usage:
    # Run in background alongside the MCTS benchmark:
    nohup python scripts/arxiv_cycle_daemon.py \\
        --queue data/arxiv_queue_curated.txt \\
        --project-root . \\
        --model labs-leanstral-2603 \\
        --mode mcts-draft \\
        --paper-timeout 1800 \\
        --sleep 60 \\
        > output/daemon_run.log 2>&1 &

    # Or dry-run to see what would be processed:
    python scripts/arxiv_cycle_daemon.py --queue data/arxiv_queue_curated.txt --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [daemon] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

_STOP = False  # set by SIGTERM/SIGINT handler


def _handle_signal(sig, frame):
    global _STOP
    logger.info("Signal %s received — stopping after current paper", sig)
    _STOP = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ---------------------------------------------------------------------------
# Queue management
# ---------------------------------------------------------------------------

def load_queue(queue_path: str) -> list[str]:
    """Load paper IDs from the queue file. Lines starting with # are comments."""
    ids = []
    with open(queue_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Support "2312.13098  # comment" format.
            paper_id = line.split()[0].replace("arxiv:", "").strip()
            if paper_id:
                ids.append(paper_id)
    return ids


def _processed_path(out_root: Path) -> Path:
    return out_root / "daemon_processed.json"


def load_processed(out_root: Path) -> set[str]:
    p = _processed_path(out_root)
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return set()


def mark_processed(out_root: Path, paper_id: str, result: dict) -> None:
    p = _processed_path(out_root)
    try:
        existing = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        if isinstance(existing, list):
            existing = {k: {} for k in existing}
    except Exception:
        existing = {}
    existing[paper_id] = result
    p.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


def processed_ids(out_root: Path) -> set[str]:
    p = _processed_path(out_root)
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return set(data.keys())
        return set(data)
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# Pre-flight validation
# ---------------------------------------------------------------------------

def preflight_check(paper_id: str) -> tuple[bool, str]:
    """Verify a paper is suitable for the pipeline before spending time on it.

    Checks (in order, fast-fail):
    1. arXiv tarball exists and contains at least one .tex file.
    2. At least one .tex file contains a LaTeX theorem environment
       (\\begin{theorem}, \\begin{lemma}, \\begin{proposition}, \\begin{corollary}).

    Returns (ok, reason).  reason is empty when ok=True.
    """
    import re
    import tarfile
    import tempfile
    import urllib.request

    tarball_url = f"https://arxiv.org/src/{paper_id}"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tarball_path = Path(tmp) / f"{paper_id}.tar.gz"
            try:
                urllib.request.urlretrieve(tarball_url, tarball_path)
            except Exception as exc:
                return False, f"download failed: {exc}"

            # Try to open as tar
            try:
                with tarfile.open(tarball_path, "r:*") as tf:
                    tex_members = [m for m in tf.getmembers()
                                   if m.name.endswith(".tex") and m.isfile()]
            except tarfile.TarError as exc:
                return False, f"not a valid tarball (PDF-only?): {exc}"

            if not tex_members:
                return False, "tarball contains no .tex files"

            # Check for theorem environments in any .tex file
            thm_pattern = re.compile(
                r"\\begin\{(theorem|lemma|proposition|corollary)\}", re.IGNORECASE
            )
            with tarfile.open(tarball_path, "r:*") as tf:
                for member in tex_members[:10]:  # check first 10 tex files at most
                    try:
                        f = tf.extractfile(member)
                        if f is None:
                            continue
                        content = f.read(65536).decode("utf-8", errors="ignore")
                        if thm_pattern.search(content):
                            return True, ""
                    except Exception:
                        continue

            return False, "no LaTeX theorem environments found in .tex files"

    except Exception as exc:
        return False, f"preflight exception: {exc}"


# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------

def run_paper_pipeline(
    paper_id: str,
    *,
    project_root: Path,
    out_dir: Path,
    model: str,
    mode: str,
    mcts_iterations: int,
    lean_timeout: int,
    paper_timeout: int,
    retrieval_index: str,
    api_key: str,
    dry_run: bool = False,
) -> dict:
    """Run the full arxiv→Lean pipeline for one paper.

    Returns a result dict with keys: proven_count, total_count, status, elapsed_s.
    """
    t0 = time.time()
    paper_out = out_dir / paper_id

    if dry_run:
        logger.info("[dry-run] would process %s", paper_id)
        return {"proven_count": 0, "total_count": 0, "status": "dry-run", "elapsed_s": 0.0}

    paper_out.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, str(project_root / "scripts" / "arxiv_to_lean.py"),
        paper_id,
        "--out", str(paper_out),
        "--model", model,
        "--prove-mode", mode,
        "--mcts-iterations", str(mcts_iterations),
        "--dojo-timeout", str(lean_timeout),
        "--retrieval-index", retrieval_index,
    ]

    env = os.environ.copy()
    env["MISTRAL_API_KEY"] = api_key
    env["DESOL_FORCE_REPL_DOJO"] = "1"

    logger.info("Starting pipeline for %s (timeout=%ds)", paper_id, paper_timeout)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(project_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=paper_timeout,
        )
        elapsed = round(time.time() - t0, 1)
        if proc.returncode != 0:
            logger.warning(
                "Pipeline returned %d for %s:\n%s",
                proc.returncode, paper_id, (proc.stderr or "")[:500],
            )
            return {"proven_count": 0, "total_count": 0, "status": "pipeline_error", "elapsed_s": elapsed}

        # Count proven theorems from ledger.
        ledger_dir = project_root / "output" / "verification_ledgers"
        proven, total = _count_proven(ledger_dir, paper_id)
        logger.info("%s: %d/%d FULLY_PROVEN (%.1fs)", paper_id, proven, total, elapsed)
        return {"proven_count": proven, "total_count": total, "status": "ok", "elapsed_s": elapsed}

    except subprocess.TimeoutExpired:
        elapsed = round(time.time() - t0, 1)
        logger.warning("Pipeline TIMEOUT for %s after %.0fs", paper_id, elapsed)
        return {"proven_count": 0, "total_count": 0, "status": "timeout", "elapsed_s": elapsed}
    except Exception as exc:
        elapsed = round(time.time() - t0, 1)
        logger.error("Pipeline EXCEPTION for %s: %s", paper_id, exc)
        return {"proven_count": 0, "total_count": 0, "status": f"exception:{exc}", "elapsed_s": elapsed}


def _count_proven(ledger_dir: Path, paper_id: str) -> tuple[int, int]:
    """Count FULLY_PROVEN theorems in the ledger for a given paper."""
    total = proven = 0
    for p in ledger_dir.glob(f"{paper_id}*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            entries = data.get("entries", data) if isinstance(data, dict) else data
            if isinstance(entries, list):
                for e in entries:
                    if isinstance(e, dict):
                        total += 1
                        if e.get("status") == "FULLY_PROVEN":
                            proven += 1
        except Exception:
            pass
    return proven, total


# ---------------------------------------------------------------------------
# KG promotion
# ---------------------------------------------------------------------------

def promote_to_kg(paper_id: str, project_root: Path) -> int:
    """Run kg_writer.py on the paper's ledger to promote FULLY_PROVEN theorems."""
    cmd = [
        sys.executable, str(project_root / "scripts" / "kg_writer.py"),
        "--ledger-dir", str(project_root / "output" / "verification_ledgers"),
        "--paper", paper_id,
    ]
    try:
        proc = subprocess.run(cmd, cwd=str(project_root), capture_output=True, text=True, timeout=60)
        if proc.returncode == 0:
            # Parse promoted count from output.
            for line in proc.stdout.splitlines():
                if "trusted=" in line:
                    try:
                        val = int(line.split("trusted=")[1].split()[0])
                        return val
                    except Exception:
                        pass
        return 0
    except Exception as exc:
        logger.warning("KG promotion failed for %s: %s", paper_id, exc)
        return 0


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def cleanup_paper(paper_id: str, out_dir: Path, project_root: Path) -> None:
    """Delete downloaded .tex source and temp .lean files; keep ledger and KG."""
    # Remove paper-specific output dir (contains .tex, .lean working files).
    paper_out = out_dir / paper_id
    if paper_out.exists():
        shutil.rmtree(paper_out, ignore_errors=True)
        logger.debug("Deleted %s", paper_out)

    # Remove temp .lean files from Desol/ (pattern: _tmp_prove_*.lean).
    for p in (project_root / "Desol").glob(f"*{paper_id}*.lean"):
        try:
            p.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_daemon(
    *,
    queue_path: str,
    project_root: Path,
    out_dir: Path,
    model: str,
    mode: str,
    mcts_iterations: int,
    lean_timeout: int,
    paper_timeout: int,
    sleep_between: int,
    retrieval_index: str,
    api_key: str,
    dry_run: bool,
    max_papers: int,
) -> None:
    queue = load_queue(queue_path)
    done = processed_ids(out_dir)
    remaining = [p for p in queue if p not in done]

    logger.info(
        "Daemon starting: %d papers in queue, %d already processed, %d to go",
        len(queue), len(done), len(remaining),
    )

    processed_this_run = 0
    total_proven = 0

    for paper_id in remaining:
        if _STOP:
            logger.info("Stop signal received — exiting cleanly")
            break
        if max_papers > 0 and processed_this_run >= max_papers:
            logger.info("Reached --max-papers %d — stopping", max_papers)
            break

        logger.info("=" * 60)
        logger.info("Processing %s (%d/%d)", paper_id, processed_this_run + 1, len(remaining))

        # Pre-flight: verify paper has LaTeX source with theorem environments
        if not dry_run:
            ok, reason = preflight_check(paper_id)
            if not ok:
                logger.warning("SKIP %s — pre-flight failed: %s", paper_id, reason)
                mark_processed(out_dir, paper_id, {
                    "proven_count": 0, "total_count": 0,
                    "status": f"preflight_fail:{reason}", "elapsed_s": 0.0,
                })
                processed_this_run += 1
                continue

        result = run_paper_pipeline(
            paper_id,
            project_root=project_root,
            out_dir=out_dir,
            model=model,
            mode=mode,
            mcts_iterations=mcts_iterations,
            lean_timeout=lean_timeout,
            paper_timeout=paper_timeout,
            retrieval_index=retrieval_index,
            api_key=api_key,
            dry_run=dry_run,
        )

        # Promote to KG if any theorems proven.
        if result.get("proven_count", 0) > 0 and not dry_run:
            promoted = promote_to_kg(paper_id, project_root)
            result["kg_promoted"] = promoted
            total_proven += promoted
            logger.info("%s: promoted %d theorems to KG trusted layer", paper_id, promoted)

        # Cleanup intermediate files.
        if not dry_run:
            cleanup_paper(paper_id, out_dir, project_root)

        mark_processed(out_dir, paper_id, result)
        processed_this_run += 1

        logger.info(
            "Summary so far: %d papers processed, %d theorems in KG trusted layer",
            processed_this_run, total_proven,
        )

        if not _STOP and processed_this_run < len(remaining):
            logger.info("Sleeping %ds before next paper...", sleep_between)
            time.sleep(sleep_between)

    logger.info(
        "Daemon finished: %d papers processed, %d theorems promoted to KG",
        processed_this_run, total_proven,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Continuous arXiv paper processing daemon",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--queue", default="data/arxiv_queue_curated.txt", help="Paper ID queue file")
    p.add_argument("--project-root", default=".", help="Lean project root")
    p.add_argument("--out-dir", default="output/daemon", help="Daemon working directory")
    p.add_argument("--model", default=os.environ.get("MISTRAL_MODEL", "labs-leanstral-2603"))
    p.add_argument(
        "--mode", choices=["full-draft", "mcts-draft", "hierarchical", "state-mcts", "hierarchical-state"],
        default="state-mcts",
        help="Proof search mode per theorem (default: state-mcts)",
    )
    p.add_argument("--mcts-iterations", type=int, default=10)
    p.add_argument("--lean-timeout", type=int, default=120, help="Seconds per lake build call")
    p.add_argument(
        "--paper-timeout", type=int, default=1800,
        help="Max seconds for the full pipeline on one paper (default 30min)",
    )
    p.add_argument("--sleep", type=int, default=60, help="Seconds between papers")
    p.add_argument(
        "--retrieval-index", default="data/mathlib_embeddings",
        help="Mathlib embedding index path",
    )
    p.add_argument(
        "--api-key", default=os.environ.get("MISTRAL_API_KEY", ""),
        help="Mistral API key",
    )
    p.add_argument("--dry-run", action="store_true", help="Print what would be processed without running")
    p.add_argument(
        "--max-papers", type=int, default=0,
        help="Stop after this many papers (0 = process entire queue)",
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()

    if not args.api_key and not args.dry_run:
        logger.error("MISTRAL_API_KEY not set and --api-key not provided")
        sys.exit(1)

    project_root = Path(args.project_root).resolve()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    run_daemon(
        queue_path=args.queue,
        project_root=project_root,
        out_dir=out_dir,
        model=args.model,
        mode=args.mode,
        mcts_iterations=args.mcts_iterations,
        lean_timeout=args.lean_timeout,
        paper_timeout=args.paper_timeout,
        sleep_between=args.sleep,
        retrieval_index=args.retrieval_index,
        api_key=args.api_key,
        dry_run=args.dry_run,
        max_papers=args.max_papers,
    )


if __name__ == "__main__":
    main()
