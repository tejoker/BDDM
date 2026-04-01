#!/usr/bin/env python3
"""End-to-end pipeline test across diverse arxiv math papers.

Tests translation success rate (--translate-only by default) across multiple
mathematical domains.  Proof search is opt-in via --prove.

Usage:
    python3 scripts/test_pipeline.py
    python3 scripts/test_pipeline.py --domains probability analysis --max-theorems 5
    python3 scripts/test_pipeline.py --paper 1906.00188 --max-theorems 10
    python3 scripts/test_pipeline.py --prove --max-theorems 3 --domains probability

Exit code: 0 if overall translation rate >= --min-rate (default 0.5), else 1.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# ---------------------------------------------------------------------------
# Paper catalogue — representative papers across math domains.
# Chosen for: public LaTeX source on arxiv, dense theorem environments,
# elementary enough for Leanstral to translate.
# ---------------------------------------------------------------------------

CATALOGUE: dict[str, list[dict]] = {
    "probability": [
        {
            "id": "2010.16280",
            "title": "Lectures on Probability Theory",
            "note": "Random variables, martingales, Markov chains",
        },
        {
            "id": "2204.09735",
            "title": "Probability Theory lecture notes",
            "note": "Measure-theoretic probability, LLN, CLT, conditional expectation",
        },
    ],
    "analysis": [
        {
            "id": "2112.11166",
            "title": "Functional Analysis (van Neerven)",
            "note": "Banach/Hilbert spaces, spectral theory, semigroups",
        },
        {
            "id": "2508.19405",
            "title": "Univariate Real Analysis",
            "note": "Sequences, series, continuity, derivatives, integration",
        },
    ],
    "number_theory": [
        {
            "id": "2407.17820",
            "title": "Analytic Number Theory and Algebraic Asymptotic Analysis",
            "note": "Primes, Dirichlet series, PNT",
        },
        {
            "id": "2301.07022",
            "title": "Graphic sequences — combinatorial number theory",
            "note": "Graph degree sequences, sum-distinct sets, combinatorial bounds",
        },
    ],
    "algebra": [
        {
            "id": "2206.09283",
            "title": "Linear algebra and group theory (Banica)",
            "note": "Determinants, unitary groups, Weingarten integration",
        },
        {
            "id": "2304.09598",
            "title": "Algebra lecture notes (rings, modules, fields)",
            "note": "Rings, ideals, modules, field extensions — standard theorem envs",
        },
    ],
    "combinatorics": [
        {
            "id": "1409.2562",
            "title": "Algebraic and geometric methods in enumerative combinatorics",
            "note": "Polytopes, matroids, symmetric functions",
        },
        {
            "id": "2106.11565",
            "title": "Combinatorics lecture notes",
            "note": "Graphs, pigeonhole, counting — standard theorem envs",
        },
    ],
    "topology": [
        {
            "id": "1610.02592",
            "title": "An Introduction to Geometric Topology (Martelli)",
            "note": "Mostow rigidity, thick-thin decomposition, Thurston classification",
        },
        {
            "id": "1306.6926",
            "title": "Set theory and topology — Fundamental notions",
            "note": "General topology, axiomatic foundations",
        },
    ],
    "linear_algebra": [
        {
            "id": "2305.01583",
            "title": "Twisted conjugacy and separability in groups",
            "note": "Residually finite groups, conjugacy separability — dense lemma/theorem envs",
        },
        {
            "id": "2303.07241",
            "title": "Algebra and linear algebra over rings",
            "note": "Modules, homomorphisms, standard theorem envs",
        },
    ],
    "differential_geometry": [
        {
            "id": "1903.08539",
            "title": "Alexandrov geometry: foundations",
            "note": "Comparison geometry, geodesics, curvature bounds",
        },
        {
            "id": "2112.08114",
            "title": "An introduction to infinite-dimensional differential geometry",
            "note": "Lie groups, weak Riemannian geometry, manifolds of mappings",
        },
    ],
}


@dataclass
class PaperResult:
    domain: str
    paper_id: str
    theorems_found: int = 0
    translated: int = 0
    proved: int = 0
    errors: list[str] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""
    elapsed: float = 0.0

    @property
    def translation_rate(self) -> float:
        return self.translated / self.theorems_found if self.theorems_found else 0.0

    @property
    def proof_rate(self) -> float:
        return self.proved / self.theorems_found if self.theorems_found else 0.0


def _make_client(api_key: str):
    try:
        from mistralai import Mistral
    except ImportError:
        from mistralai.client import Mistral  # type: ignore[no-redef]
    return Mistral(api_key=api_key)


def run_paper(
    *,
    paper_id: str,
    domain: str,
    project_root: Path,
    work_dir: Path,
    client: object,
    model: str,
    max_theorems: int,
    translate_only: bool,
    repair_rounds: int,
    retrieval_index_path: str,
    temperature: float,
    dojo_timeout: int,
    parallel_theorems: int = 4,
    rate_limiter: object = None,
) -> PaperResult:
    from arxiv_to_lean import run_pipeline, _RateLimiter

    result = PaperResult(domain=domain, paper_id=paper_id)
    out_lean = (
        project_root / "output" / "tests"
        / f"{domain}_{paper_id.replace('/', '_')}.lean"
    )

    t0 = time.time()
    try:
        pipe_results = run_pipeline(
            paper_id=paper_id,
            project_root=project_root,
            out_lean=out_lean,
            work_dir=work_dir / paper_id.replace("/", "_"),
            client=client,
            model=model,
            kinds={"theorem", "lemma", "proposition", "corollary"},
            max_theorems=max_theorems,
            translate_only=translate_only,
            repair_rounds=repair_rounds,
            retrieval_index_path=retrieval_index_path,
            retrieval_top_k=12,
            imports="",   # auto — baseline + premise-index expansion
            temperature=temperature,
            dojo_timeout=dojo_timeout,
            parallel_theorems=parallel_theorems,
            rate_limiter=rate_limiter,
        )
        result.theorems_found = len(pipe_results)
        result.translated = sum(1 for r in pipe_results if r.translation.validated)
        result.proved = sum(1 for r in pipe_results if r.proved)
        for r in pipe_results:
            if not r.translation.validated and r.translation.last_error:
                result.errors.append(r.translation.last_error[:120])
    except Exception as exc:
        result.skipped = True
        result.skip_reason = str(exc)[:200]

    result.elapsed = time.time() - t0
    return result


def _print_result(r: PaperResult) -> None:
    if r.skipped:
        print(f"  SKIP  [{r.domain}] {r.paper_id} — {r.skip_reason[:80]}")
        return
    rate_pct = f"{r.translation_rate * 100:.0f}%"
    proof_str = f"  proved={r.proved}/{r.theorems_found}" if r.proved > 0 else ""
    status = "OK  " if r.translation_rate >= 0.5 else "WARN"
    print(
        f"  {status} [{r.domain}] {r.paper_id}"
        f"  found={r.theorems_found}"
        f"  translated={r.translated}/{r.theorems_found} ({rate_pct})"
        f"{proof_str}"
        f"  {r.elapsed:.1f}s"
    )
    for i, err in enumerate(r.errors[:2], 1):
        print(f"       err[{i}]: {err[:100]}")


def _ascii_bar(pct: float, width: int = 20) -> str:
    filled = int(pct / 100 * width)
    return "#" * filled + "-" * (width - filled)


def _print_summary(results: list[PaperResult]) -> dict:
    total_found = sum(r.theorems_found for r in results)
    total_translated = sum(r.translated for r in results)
    total_proved = sum(r.proved for r in results)
    skipped = sum(1 for r in results if r.skipped)
    total_elapsed = sum(r.elapsed for r in results)
    rate = total_translated / total_found if total_found else 0.0

    print("\n" + "=" * 64)
    print("SUMMARY")
    print("=" * 64)
    print(f"  Papers tested :  {len(results)}  ({skipped} skipped / fetch failed)")
    print(f"  Theorems found:  {total_found}")
    print(f"  Translated    :  {total_translated}/{total_found}  ({rate * 100:.1f}%)")
    if total_proved:
        print(f"  Proved        :  {total_proved}/{total_found}  ({total_proved / total_found * 100:.1f}%)")
    print(f"  Total time    :  {total_elapsed:.1f}s")

    # Per-domain breakdown
    by_domain: dict[str, dict] = {}
    for r in results:
        d = by_domain.setdefault(r.domain, {"found": 0, "translated": 0})
        d["found"] += r.theorems_found
        d["translated"] += r.translated

    print("\nBy domain:")
    for domain, stats in sorted(by_domain.items()):
        f, t = stats["found"], stats["translated"]
        pct = t / f * 100 if f else 0.0
        bar = _ascii_bar(pct)
        print(f"  {domain:<24} [{bar}] {pct:5.1f}%  ({t}/{f})")

    return {
        "translation_rate": rate,
        "total_found": total_found,
        "total_translated": total_translated,
        "total_proved": total_proved,
        "papers_tested": len(results),
        "papers_skipped": skipped,
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Pipeline test across diverse arxiv math papers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--domains",
        nargs="+",
        default=[],
        metavar="DOMAIN",
        help="Domains to test (default: all). Available: " + ", ".join(sorted(CATALOGUE)),
    )
    p.add_argument(
        "--paper",
        default="",
        metavar="ARXIV_ID",
        help="Test a single arxiv paper ID instead of the catalogue",
    )
    p.add_argument("--paper-domain", default="custom", help="Domain label for --paper")
    p.add_argument(
        "--first-paper-only",
        action="store_true",
        help="Only test the first paper per domain (faster)",
    )
    p.add_argument("--max-theorems", type=int, default=8, help="Theorems per paper (0=all)")
    p.add_argument(
        "--prove",
        action="store_true",
        help="Also attempt proof search (slow — requires Dojo)",
    )
    p.add_argument("--repair-rounds", type=int, default=5)
    p.add_argument(
        "--retrieval-index",
        default="data/mathlib_embeddings",
        help="Premise retrieval index path",
    )
    p.add_argument("--model", default="", help="Mistral model (default: MISTRAL_MODEL env)")
    p.add_argument("--temperature", type=float, default=0.2)
    p.add_argument("--dojo-timeout", type=int, default=300)
    p.add_argument(
        "--parallel-papers",
        type=int,
        default=2,
        help="Number of papers to process in parallel (default: 2)",
    )
    p.add_argument(
        "--parallel-theorems",
        type=int,
        default=4,
        help="Number of theorems per paper to process in parallel (default: 4)",
    )
    p.add_argument(
        "--api-rate",
        type=float,
        default=4.0,
        help="Max API calls per second across all threads (default: 4.0)",
    )
    p.add_argument(
        "--min-rate",
        type=float,
        default=0.5,
        help="Minimum overall translation rate for exit code 0 (default: 0.5)",
    )
    p.add_argument(
        "--output-json",
        default="",
        metavar="PATH",
        help="Write detailed results to a JSON file",
    )
    p.add_argument("--project-root", default=".", help="Lean project root")
    return p


def main() -> int:
    load_dotenv()
    args = _build_parser().parse_args()

    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        print("[fail] MISTRAL_API_KEY not set", file=sys.stderr)
        return 1

    model = args.model.strip() or os.getenv("MISTRAL_MODEL", "labs-leanstral-2603").strip()
    project_root = Path(args.project_root).resolve()
    work_dir = Path("/tmp/arxiv_test_pipeline")
    work_dir.mkdir(parents=True, exist_ok=True)
    client = _make_client(api_key)

    # Build paper list.
    if args.paper:
        papers = [{"id": args.paper, "domain": args.paper_domain}]
    else:
        domains = args.domains if args.domains else list(CATALOGUE.keys())
        unknown = [d for d in domains if d not in CATALOGUE]
        if unknown:
            print(f"[warn] unknown domains (ignored): {unknown}", file=sys.stderr)
        papers = []
        for domain in domains:
            if domain not in CATALOGUE:
                continue
            entries = CATALOGUE[domain][:1] if args.first_paper_only else CATALOGUE[domain]
            for entry in entries:
                papers.append({"id": entry["id"], "domain": domain})

    print(f"Testing {len(papers)} papers")
    print(f"  model             : {model}")
    print(f"  max_theorems      : {args.max_theorems}")
    print(f"  translate_only    : {not args.prove}")
    print(f"  retrieval_index   : {args.retrieval_index}")
    print(f"  parallel_papers   : {args.parallel_papers}")
    print(f"  parallel_theorems : {args.parallel_theorems}")
    print(f"  api_rate          : {args.api_rate} calls/s")
    print()

    from arxiv_to_lean import _RateLimiter
    shared_rl = _RateLimiter(rate=args.api_rate)

    def _run_one(paper: dict) -> PaperResult:
        print(f"[{paper['domain']}] {paper['id']} ...")
        sys.stdout.flush()
        r = run_paper(
            paper_id=paper["id"],
            domain=paper["domain"],
            project_root=project_root,
            work_dir=work_dir,
            client=client,
            model=model,
            max_theorems=args.max_theorems,
            translate_only=not args.prove,
            repair_rounds=args.repair_rounds,
            retrieval_index_path=args.retrieval_index,
            temperature=args.temperature,
            dojo_timeout=args.dojo_timeout,
            parallel_theorems=args.parallel_theorems,
            rate_limiter=shared_rl,
        )
        _print_result(r)
        sys.stdout.flush()
        return r

    results: list[PaperResult] = []
    parallel_papers = max(1, args.parallel_papers)
    if parallel_papers == 1:
        for paper in papers:
            results.append(_run_one(paper))
    else:
        paper_futures: dict = {}
        with ThreadPoolExecutor(max_workers=parallel_papers) as executor:
            for paper in papers:
                paper_futures[executor.submit(_run_one, paper)] = paper
            for future in as_completed(paper_futures):
                results.append(future.result())

    summary = _print_summary(results)

    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "summary": summary,
            "model": model,
            "max_theorems": args.max_theorems,
            "translate_only": not args.prove,
            "papers": [
                {
                    "domain": r.domain,
                    "paper_id": r.paper_id,
                    "theorems_found": r.theorems_found,
                    "translated": r.translated,
                    "proved": r.proved,
                    "translation_rate": round(r.translation_rate, 3),
                    "proof_rate": round(r.proof_rate, 3),
                    "skipped": r.skipped,
                    "skip_reason": r.skip_reason,
                    "elapsed": round(r.elapsed, 1),
                    "errors": r.errors[:5],
                }
                for r in results
            ],
        }
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nResults written to {args.output_json}")

    passed = summary["translation_rate"] >= args.min_rate
    print(f"\n{'PASS' if passed else 'FAIL'} — translation rate {summary['translation_rate'] * 100:.1f}% (min {args.min_rate * 100:.0f}%)")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
