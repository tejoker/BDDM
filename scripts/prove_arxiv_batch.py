#!/usr/bin/env python3
"""Batch proof search over translated arxiv theorems.

Reads .lean output files from the translation pipeline, finds theorems with
`sorry` bodies (validated statements), and runs prove_with_full_draft_repair
on each one, replacing sorry with actual proofs where successful.

Usage:
    # Prove all sorry theorems from a specific paper:
    python3 scripts/prove_arxiv_batch.py --lean-file output/tests/algebra_2304.09598.lean

    # Prove theorems from all papers in a domain:
    python3 scripts/prove_arxiv_batch.py --domain algebra

    # Prove everything (slow):
    python3 scripts/prove_arxiv_batch.py --all

    # Dry-run (list theorems without proving):
    python3 scripts/prove_arxiv_batch.py --domain algebra --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from dataclasses import dataclass, field

from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

logger = logging.getLogger(__name__)

try:
    from bridge_proofs import collect_bridge_retry_targets
except Exception:
    try:
        from scripts.bridge_proofs import collect_bridge_retry_targets
    except Exception:
        collect_bridge_retry_targets = None


# ---------------------------------------------------------------------------
# Parse validated .lean files to extract sorry theorems
# ---------------------------------------------------------------------------

# Matches theorem/lemma/def declarations with sorry body.
_DECL_RE = re.compile(
    r"^((?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma|def)\s+(\w+)[^:=]*:=[^\n]*\n"
    r"(?:(?!^(?:theorem|lemma|def|end|namespace)\b)[^\n]*\n)*?"
    r"\s*sorry\s*\n?)",
    re.MULTILINE,
)

# Also match single-line: `theorem foo ... := by\n  sorry`
_SINGLE_SORRY_RE = re.compile(
    r"^((?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma)\s+(\w+)[^\n]*:= by\s*\n\s*sorry)",
    re.MULTILINE,
)


@dataclass
class SorryTheorem:
    name: str          # unqualified name as it appears in the file
    full_name: str     # namespace-qualified name
    declaration: str   # full declaration text with sorry
    lean_file: Path    # path to the .lean file


def _extract_sorry_theorems(lean_file: Path) -> list[SorryTheorem]:
    """Return all theorems with sorry bodies in the given .lean file."""
    text = lean_file.read_text(encoding="utf-8")

    # Detect namespace.
    ns_m = re.search(r"^namespace\s+(\w+)", text, re.MULTILINE)
    namespace = ns_m.group(1) if ns_m else ""

    results: list[SorryTheorem] = []
    seen: set[str] = set()

    for m in _SINGLE_SORRY_RE.finditer(text):
        name = m.group(2)
        if name in seen:
            continue
        seen.add(name)
        # Skip placeholder trivial stubs.
        if name.startswith("thm_") or name.startswith("prop_") or name.startswith("lemma_"):
            if ": True := trivial" in m.group(1) or ": True := by" in m.group(1):
                continue
        full_name = f"{namespace}.{name}" if namespace else name
        results.append(SorryTheorem(
            name=name,
            full_name=full_name,
            declaration=m.group(1),
            lean_file=lean_file,
        ))

    return results


# ---------------------------------------------------------------------------
# Proof result tracking
# ---------------------------------------------------------------------------

@dataclass
class ProofResult:
    theorem_name: str
    lean_file: str
    proved: bool
    proof_text: str = ""
    rounds_used: int = 0
    time_s: float = 0.0
    error: str = ""
    status: str = "UNRESOLVED"  # VerificationStatus value


def _value_samples_to_step_records(samples: list[dict]) -> list[dict]:
    """Convert MCTS value samples to ledger-compatible step records."""
    records: list[dict] = []
    for idx, sample in enumerate(samples, start=1):
        payload = {
            "raw_value": sample.get("raw_value", 0.0),
            "normalized_value": sample.get("normalized_value", 0.0),
            "tactics_estimate": sample.get("tactics_estimate", None),
            "cache_hit": bool(sample.get("cache_hit", False)),
            "source": sample.get("source", "model_fallback"),
            "state_chars": int(sample.get("state_chars", 0) or 0),
        }
        if sample.get("error"):
            payload["error"] = str(sample.get("error"))[:300]
        records.append(
            {
                "step": idx,
                "attempt": 0,
                "tactic": "__value_estimate__",
                "model_turns": 1,
                "result": "value-estimate",
                "detail": json.dumps(payload, ensure_ascii=True),
            }
        )
    return records


def _save_results(results: list[ProofResult], out_path: Path) -> None:
    data = [
        {
            "theorem": r.theorem_name,
            "file": r.lean_file,
            "proved": r.proved,
            "status": r.status,
            "rounds": r.rounds_used,
            "time_s": round(r.time_s, 1),
            "error": r.error[:200] if r.error else "",
        }
        for r in results
    ]
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _patch_proof_into_file(lean_file: Path, theorem_name: str, proof_text: str) -> bool:
    """Replace the sorry body of theorem_name with proof_text in the lean file."""
    text = lean_file.read_text(encoding="utf-8")
    # Find the sorry block for this theorem.
    pattern = re.compile(
        r"(theorem\s+" + re.escape(theorem_name) + r"[^\n]*:= by\s*\n)\s*sorry",
        re.MULTILINE,
    )
    new_text = pattern.sub(
        lambda m: m.group(1) + proof_text.rstrip() + "\n",
        text,
        count=1,
    )
    if new_text == text:
        return False  # nothing replaced
    lean_file.write_text(new_text, encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# Proof loop (calls prove_with_full_draft_repair)
# ---------------------------------------------------------------------------

def prove_one(
    thm: SorryTheorem,
    *,
    project_root: Path,
    client: object,
    model: str,
    repair_rounds: int = 5,
    retrieval_index: str = "",
    proof_mode: str = "full-draft",
    mcts_iterations: int = 12,
    mcts_repair_variants: int = 3,
    mcts_max_depth: int = 5,
    paper_id: str = "",
    dry_run: bool = False,
    verbose: bool = True,
) -> ProofResult:
    """Attempt to prove a single sorry theorem."""
    if dry_run:
        print(f"  [dry-run] {thm.full_name}")
        return ProofResult(
            theorem_name=thm.full_name,
            lean_file=str(thm.lean_file),
            proved=False,
            error="dry-run",
        )

    from pipeline_status import build_ledger_entry, upsert_ledger_entry

    start = time.time()
    if verbose:
        print(f"  proving [{proof_mode}]: {thm.full_name} ...", flush=True)

    try:
        rel_file = thm.lean_file.relative_to(project_root)
    except ValueError:
        rel_file = thm.lean_file

    proved = False
    records: list = []
    last_error = ""

    try:
        if proof_mode == "mcts-draft":
            from mcts_search import run_draft_mcts, run_mcts_fallback
            try:
                ok, raw_records, summary = run_draft_mcts(
                    project_root=project_root,
                    file_path=rel_file,
                    theorem_name=thm.full_name,
                    client=client,
                    model=model,
                    iterations=mcts_iterations,
                    repair_variants=mcts_repair_variants,
                    max_depth=mcts_max_depth,
                    retrieval_index_path=retrieval_index,
                )
                proved = ok
                records = raw_records
                last_error = summary if not ok else ""
            except Exception as mcts_exc:
                msg = str(mcts_exc)
                if "No proof backend available" in msg or "Backend initialization failed" in msg:
                    # Fall back to model-only MCTS to collect calibration traces.
                    logger.warning(f"Backend unavailable, falling back to model-only: {msg}")
                    _root, stats = run_mcts_fallback(
                        theorem_name=thm.full_name,
                        initial_state_text=thm.declaration,
                        client=client,
                        model=model,
                        retrieval_index_path=retrieval_index,
                        iterations=max(3, mcts_iterations),
                        use_tactics_estimate=True,
                    )
                    proved = False
                    records = _value_samples_to_step_records(stats.value_samples)
                    last_error = (
                        "Backend unavailable (toolchain/git issue); ran model-only fallback for value calibration traces"
                    )
                else:
                    raise
        else:
            from prove_with_ponder import prove_with_full_draft_repair
            proved, records, last_error = prove_with_full_draft_repair(
                project_root=project_root,
                file_path=rel_file,
                theorem_name=thm.full_name,
                client=client,
                model=model,
                repair_rounds=repair_rounds,
                retrieval_index_path=retrieval_index,
            )

        elapsed = time.time() - start

        proof_text = ""
        if proved:
            last_rec = records[-1] if records else None
            if last_rec is not None:
                proof_text = (
                    last_rec.get("tactic", "") if isinstance(last_rec, dict)
                    else getattr(last_rec, "tactic", "")
                )
            _patch_proof_into_file(thm.lean_file, thm.name, proof_text)
            if verbose:
                print(f"    PROVED in {elapsed:.1f}s (steps={len(records)})", flush=True)
        else:
            if verbose:
                print(f"    failed  in {elapsed:.1f}s: {last_error[:80]}", flush=True)

        ledger_entry = build_ledger_entry(
            theorem_name=thm.full_name,
            lean_file=str(thm.lean_file),
            lean_statement=thm.declaration,
            proved=proved,
            step_records=records,
            proof_text=proof_text,
            error_message=last_error,
            proof_mode=proof_mode,
            rounds_used=len(records),
            time_s=elapsed,
            had_exception=False,
        )
        if paper_id:
            upsert_ledger_entry(paper_id, ledger_entry)

        return ProofResult(
            theorem_name=thm.full_name,
            lean_file=str(thm.lean_file),
            proved=proved,
            proof_text=proof_text,
            rounds_used=len(records),
            time_s=elapsed,
            error=last_error,
            status=ledger_entry.status.value,
        )
    except Exception as e:
        elapsed = time.time() - start
        if verbose:
            print(f"    error   in {elapsed:.1f}s: {e}", flush=True)

        fallback_records: list[dict] = []
        if proof_mode == "mcts-draft":
            # Do not emit synthetic placeholder scores.
            # Try to capture real model-only value samples; if unavailable, keep empty.
            try:
                from mcts_search import run_mcts_fallback

                _root, fb_stats = run_mcts_fallback(
                    theorem_name=thm.full_name,
                    initial_state_text=thm.declaration,
                    client=client,
                    model=model,
                    retrieval_index_path=retrieval_index,
                    iterations=max(2, min(6, mcts_iterations)),
                    use_tactics_estimate=True,
                )
                if fb_stats.value_samples:
                    fallback_records = _value_samples_to_step_records(fb_stats.value_samples)
            except Exception:
                fallback_records = []

        ledger_entry = build_ledger_entry(
            theorem_name=thm.full_name,
            lean_file=str(thm.lean_file),
            lean_statement=thm.declaration,
            proved=False,
            step_records=fallback_records,
            error_message=str(e),
            proof_mode=proof_mode,
            time_s=elapsed,
            had_exception=True,
        )
        if paper_id:
            upsert_ledger_entry(paper_id, ledger_entry)

        return ProofResult(
            theorem_name=thm.full_name,
            lean_file=str(thm.lean_file),
            proved=False,
            time_s=elapsed,
            error=str(e),
            status=ledger_entry.status.value,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _collect_lean_files(
    output_dir: Path,
    lean_file: str | None,
    domain: str | None,
    all_papers: bool,
) -> list[Path]:
    if lean_file:
        return [Path(lean_file).resolve()]
    if domain:
        return sorted(output_dir.glob(f"{domain}_*.lean"))
    if all_papers:
        return sorted(output_dir.glob("*.lean"))
    return []


def _bridge_hints_from_ledger_entry(entry: dict) -> list[str]:
    out: list[str] = []
    assumptions = entry.get("assumptions", []) if isinstance(entry, dict) else []
    if not isinstance(assumptions, list):
        return out
    for a in assumptions:
        if not isinstance(a, dict):
            continue
        src = str(a.get("grounding_source", "")).strip()
        if src.startswith("bridge_candidate:"):
            name = src.split(":", 1)[1].strip()
            if name:
                out.append(name)
    # dedupe, preserve order
    return list(dict.fromkeys(out))


def _load_ledger_entry_for_theorem(paper_id: str, theorem_name: str) -> dict | None:
    if not paper_id:
        return None
    try:
        from pipeline_status import load_ledger

        rows = load_ledger(paper_id)
    except Exception:
        return None

    for row in rows:
        if isinstance(row, dict) and str(row.get("theorem_name", "")).strip() == theorem_name:
            return row
    return None


def _collect_bridge_targets(
    *,
    paper_id: str,
    theorem_name: str,
    ledger_root: Path,
    bridge_depth: int,
    bridge_max_candidates: int,
) -> list[str]:
    targets: list[str] = []

    entry = _load_ledger_entry_for_theorem(paper_id, theorem_name)
    if entry is not None:
        targets.extend(_bridge_hints_from_ledger_entry(entry))

    if collect_bridge_retry_targets is not None:
        try:
            plan = collect_bridge_retry_targets(
                target_theorem=theorem_name,
                ledger_root=ledger_root,
                max_depth=bridge_depth,
                max_candidates_per_step=bridge_max_candidates,
            )
            targets.extend(plan.ordered_candidates)
        except Exception:
            pass

    return list(dict.fromkeys(t for t in targets if t and t != theorem_name))


def main() -> int:
    load_dotenv()
    p = argparse.ArgumentParser(description="Batch proof search over translated arxiv theorems")
    p.add_argument("--lean-file", default="", help="Specific .lean file to process")
    p.add_argument("--domain", default="", help="Domain prefix (e.g. algebra, analysis)")
    p.add_argument("--all", action="store_true", help="Process all output files")
    p.add_argument("--output-dir", default="output/tests", help="Directory of translated .lean files")
    p.add_argument("--project-root", default=".", help="Lean project root")
    p.add_argument("--model", default="", help="Mistral model (defaults to MISTRAL_MODEL env)")
    p.add_argument("--repair-rounds", type=int, default=5)
    p.add_argument("--retrieval-index", default="data/mathlib_embeddings")
    p.add_argument("--results-file", default="logs/proof_batch_results.json")
    p.add_argument("--dry-run", action="store_true", help="List theorems without proving")
    p.add_argument("--max-theorems", type=int, default=0, help="Limit theorems per file (0 = all)")
    p.add_argument(
        "--mode",
        choices=["full-draft", "mcts-draft"],
        default="full-draft",
        help="Proof mode: linear repair loop or draft-level MCTS tree search",
    )
    p.add_argument("--mcts-iterations", type=int, default=12, help="MCTS iterations per theorem")
    p.add_argument("--mcts-repair-variants", type=int, default=3, help="Repair variants per MCTS node")
    p.add_argument("--mcts-max-depth", type=int, default=5, help="Max MCTS depth in repair rounds")
    p.add_argument("--paper-id", default="", help="Paper ID for verification ledger (e.g. algebra/2304.09598)")
    p.add_argument(
        "--write-kg",
        action="store_true",
        help="Build KG layers/manifests from verification ledgers after batch run",
    )
    p.add_argument(
        "--kg-root",
        default="output/kg",
        help="KG output root used with --write-kg",
    )
    p.add_argument(
        "--bridge-loop",
        action="store_true",
        help="Enable bridge-proof execution loop (prove bridge candidates, then retry target)",
    )
    p.add_argument("--bridge-rounds", type=int, default=2, help="Max bridge retry rounds per failed theorem")
    p.add_argument(
        "--bridge-depth",
        type=int,
        default=2,
        help="Bridge chain planning depth",
    )
    p.add_argument(
        "--bridge-max-candidates",
        type=int,
        default=3,
        help="Max bridge candidates considered per planning step",
    )
    args = p.parse_args()

    project_root = Path(args.project_root).resolve()
    output_dir = project_root / args.output_dir

    lean_files = _collect_lean_files(
        output_dir=output_dir,
        lean_file=args.lean_file or None,
        domain=args.domain or None,
        all_papers=args.all,
    )

    if not lean_files:
        print("[error] No lean files found. Use --lean-file, --domain, or --all.", file=sys.stderr)
        return 1

    # Collect all sorry theorems.
    all_theorems: list[SorryTheorem] = []
    for lf in lean_files:
        thms = _extract_sorry_theorems(lf)
        # Skip trivial stubs (True := trivial).
        thms = [t for t in thms if "True := trivial" not in t.declaration]
        if args.max_theorems:
            thms = thms[:args.max_theorems]
        all_theorems.extend(thms)
        print(f"  {lf.name}: {len(thms)} sorry theorems")

    print(f"\nTotal: {len(all_theorems)} theorems to prove")

    if args.dry_run:
        for t in all_theorems:
            print(f"  {t.full_name}  [{t.lean_file.name}]")
        return 0

    # Set up API client.
    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        print("[error] MISTRAL_API_KEY not set", file=sys.stderr)
        return 1
    model = args.model.strip() or os.getenv("MISTRAL_MODEL", "labs-leanstral-2603").strip()

    try:
        from mistralai import Mistral
    except ImportError:
        from mistralai.client import Mistral  # type: ignore[no-redef]
    client = Mistral(api_key=api_key)

    # Derive paper_id from lean file name if not provided explicitly.
    paper_id = args.paper_id.strip()
    if not paper_id and len(lean_files) == 1:
        # e.g. algebra_2304.09598.lean → algebra/2304.09598
        stem = lean_files[0].stem
        if "_" in stem:
            domain_part, paper_part = stem.split("_", 1)
            paper_id = f"{domain_part}/{paper_part}"

    theorem_by_name = {t.full_name: t for t in all_theorems}
    ledger_root = project_root / "output" / "verification_ledgers"

    # Run proofs with optional bridge execution loop.
    result_by_theorem: dict[str, ProofResult] = {}
    attempted: set[str] = set()
    proved_set: set[str] = set()

    def _attempt_theorem(name: str) -> ProofResult:
        thm = theorem_by_name[name]
        r_inner = prove_one(
            thm,
            project_root=project_root,
            client=client,
            model=model,
            repair_rounds=args.repair_rounds,
            retrieval_index=args.retrieval_index,
            proof_mode=args.mode,
            mcts_iterations=args.mcts_iterations,
            mcts_repair_variants=args.mcts_repair_variants,
            mcts_max_depth=args.mcts_max_depth,
            paper_id=paper_id,
            dry_run=args.dry_run,
        )
        attempted.add(name)
        result_by_theorem[name] = r_inner
        if r_inner.proved:
            proved_set.add(name)
        return r_inner

    for i, thm in enumerate(all_theorems, 1):
        print(f"\n[{i}/{len(all_theorems)}] {thm.full_name}")
        if thm.full_name in proved_set:
            print("  status: already proved in prior bridge round")
            continue

        r = _attempt_theorem(thm.full_name)
        print(f"  status: {r.status}")

        if (not args.bridge_loop) or r.proved or (not paper_id):
            continue

        for bridge_round in range(1, max(1, args.bridge_rounds) + 1):
            bridge_targets = _collect_bridge_targets(
                paper_id=paper_id,
                theorem_name=thm.full_name,
                ledger_root=ledger_root,
                bridge_depth=args.bridge_depth,
                bridge_max_candidates=args.bridge_max_candidates,
            )
            bridge_targets = [
                t_name for t_name in bridge_targets
                if t_name in theorem_by_name and t_name != thm.full_name and t_name not in proved_set
            ]

            if not bridge_targets:
                break

            print(f"  bridge round {bridge_round}: candidates={bridge_targets[:args.bridge_max_candidates]}")

            bridge_progress = False
            for candidate in bridge_targets[:args.bridge_max_candidates]:
                if candidate in attempted and candidate in proved_set:
                    continue
                print(f"    proving bridge candidate: {candidate}")
                bridge_result = _attempt_theorem(candidate)
                print(f"    bridge status: {bridge_result.status}")
                if bridge_result.proved:
                    bridge_progress = True

            if not bridge_progress:
                break

            print("  retrying original theorem after bridge progress...")
            retry_result = _attempt_theorem(thm.full_name)
            print(f"  retry status: {retry_result.status}")
            if retry_result.proved:
                break

    results = [result_by_theorem[name] for name in theorem_by_name if name in result_by_theorem]
    proved = sum(1 for r in results if r.proved)

    # Save results.
    results_path = project_root / args.results_file
    results_path.parent.mkdir(parents=True, exist_ok=True)
    _save_results(results, results_path)

    # Status summary.
    from collections import Counter
    status_counts = Counter(r.status for r in results)
    print(f"\n{'='*60}")
    print(f"PROVED: {proved}/{len(results)} theorems ({100*proved//max(len(results),1)}%)")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")
    print(f"Results: {results_path}")
    if paper_id:
        from pipeline_status import _ledger_path
        print(f"Ledger:  {_ledger_path(paper_id)}")

    if args.write_kg:
        try:
            from kg_writer import build_kg

            kg_root = project_root / args.kg_root
            if paper_id:
                kg_summary = build_kg(
                    ledger_dir=project_root / "output" / "verification_ledgers",
                    kg_root=kg_root,
                    paper=paper_id,
                )
            else:
                kg_summary = build_kg(
                    ledger_dir=project_root / "output" / "verification_ledgers",
                    kg_root=kg_root,
                    paper="",
                )
            print(
                "KG:      "
                f"trusted={kg_summary.trusted} "
                f"conditional={kg_summary.conditional} "
                f"diagnostics={kg_summary.diagnostics} "
                f"promotion_ready={kg_summary.promotion_ready}"
            )
            print(f"KG root: {kg_root}")
        except Exception as exc:
            print(f"[warn] KG build failed: {exc}")
    return 0 if proved > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
