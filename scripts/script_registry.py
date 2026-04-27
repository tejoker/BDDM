#!/usr/bin/env python3
"""Authoritative registry for top-level scripts.

The registry is intentionally small-data and import-safe. It lets docs, tests,
and humans answer the same question: which scripts are official pipeline
surface, which are support code, and which are experiments or one-offs?
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable


VALID_TIERS = {
    "official_pipeline",
    "official_support",
    "ci_gate",
    "reporting",
    "benchmark",
    "research_experiment",
    "legacy_one_off",
    "internal_support",
    "dev_tool",
}

VALID_CATEGORIES = {
    "benchmark",
    "bridge",
    "ci",
    "configuration",
    "ingestion",
    "kg",
    "lean_backend",
    "orchestration",
    "proof_search",
    "reliability",
    "repair",
    "reporting",
    "research",
    "review",
    "support",
    "translation",
}


OFFICIAL_PIPELINE_SCRIPTS = {
    "arxiv_to_lean.py",
    "formalize_paper_full.py",
    "reproduce_public_claims.py",
    "run_paper_agnostic_suite.py",
    "arxiv_cycle.py",
    "arxiv_cycle_daemon.py",
    "pipeline_worker.py",
}


SCRIPT_REGISTRY: dict[str, dict[str, str]] = {
    "ab_world_model_vs_baseline.py": {
        "tier": "research_experiment",
        "category": "bridge",
        "summary": "Compares world-model bridge behavior against a baseline.",
    },
    "aggregate_domain_blockers.py": {
        "tier": "reporting",
        "category": "reporting",
        "summary": "Aggregates blocker evidence by mathematical domain.",
    },
    "arxiv_cycle.py": {
        "tier": "official_pipeline",
        "category": "orchestration",
        "summary": "Batch runner for curated arXiv queues and KG rebuilds.",
    },
    "arxiv_cycle_daemon.py": {
        "tier": "official_pipeline",
        "category": "orchestration",
        "summary": "Long-running arXiv queue daemon with preflight checks.",
    },
    "arxiv_fetcher.py": {
        "tier": "official_support",
        "category": "ingestion",
        "summary": "Downloads arXiv source bundles for the paper pipeline.",
    },
    "arxiv_oai_harvest.py": {
        "tier": "official_support",
        "category": "ingestion",
        "summary": "Builds arXiv ID queues from OAI-PMH metadata.",
    },
    "arxiv_queue_split.py": {
        "tier": "official_support",
        "category": "orchestration",
        "summary": "Splits paper queues for parallel arXiv workers.",
    },
    "arxiv_rollout_manager.py": {
        "tier": "research_experiment",
        "category": "reliability",
        "summary": "Exercises orchestrator rollout behavior for experiments.",
    },
    "arxiv_to_lean.py": {
        "tier": "official_pipeline",
        "category": "orchestration",
        "summary": "Canonical single-paper arXiv-to-Lean pipeline entrypoint.",
    },
    "adjudicate_claim_equivalence.py": {
        "tier": "research_experiment",
        "category": "review",
        "summary": "Optionally writes structured LLM claim-equivalence adjudications.",
    },
    "apply_claim_equivalence_adjudications.py": {
        "tier": "reporting",
        "category": "review",
        "summary": "Applies claim-equivalence adjudications to verification ledgers.",
    },
    "axiom_debt_burndown.py": {
        "tier": "reporting",
        "category": "reporting",
        "summary": "Summarizes paper-local axiom debt and burndown opportunities.",
    },
    "benchmark_bridge_world_model.py": {
        "tier": "research_experiment",
        "category": "bridge",
        "summary": "Benchmarks bridge/world-model proof ideas.",
    },
    "benchmark_minif2f.py": {
        "tier": "benchmark",
        "category": "benchmark",
        "summary": "Runs miniF2F proof-search calibration benchmarks.",
    },
    "bridge_proofs.py": {
        "tier": "internal_support",
        "category": "bridge",
        "summary": "Ranks and verifies bridge-proof candidates.",
    },
    "build_nontrivial_gold50.py": {
        "tier": "research_experiment",
        "category": "benchmark",
        "summary": "Builds an experimental nontrivial gold translation set.",
    },
    "build_paper_agnostic_suite_from_arxiv.py": {
        "tier": "research_experiment",
        "category": "ingestion",
        "summary": "Constructs candidate paper-agnostic suites from arXiv lists.",
    },
    "build_reliable_paper_core.py": {
        "tier": "official_support",
        "category": "orchestration",
        "summary": "Builds the reliable core artifact for full-paper reports.",
    },
    "build_claim_equivalence_review_queue.py": {
        "tier": "reporting",
        "category": "review",
        "summary": "Builds JSONL review queues for claim-equivalence blockers.",
    },
    "build_repair_flywheel.py": {
        "tier": "research_experiment",
        "category": "repair",
        "summary": "Creates experimental repair flywheel datasets and reports.",
    },
    "build_tc_graph.py": {
        "tier": "research_experiment",
        "category": "research",
        "summary": "Builds Mathlib typeclass graph research artifacts.",
    },
    "build_tiny_gold_set.py": {
        "tier": "research_experiment",
        "category": "benchmark",
        "summary": "Creates a small translation-fidelity gold set.",
    },
    "canonicalization.py": {
        "tier": "internal_support",
        "category": "translation",
        "summary": "Normalizes statements and metadata for comparison.",
    },
    "ci_assert_bridge_progress.py": {
        "tier": "ci_gate",
        "category": "ci",
        "summary": "Asserts bridge-progress thresholds in scheduled CI.",
    },
    "ci_assert_quality_gates.py": {
        "tier": "ci_gate",
        "category": "ci",
        "summary": "Asserts translation and linkage gate thresholds.",
    },
    "ci_bootstrap_gates.py": {
        "tier": "ci_gate",
        "category": "ci",
        "summary": "Bootstraps deterministic KG fixtures for CI gates.",
    },
    "claim_equivalence_review.py": {
        "tier": "internal_support",
        "category": "review",
        "summary": "Shared schema and merge helpers for claim-equivalence review artifacts.",
    },
    "conjecture_generator.py": {
        "tier": "research_experiment",
        "category": "research",
        "summary": "Generates conjecture candidates for research workflows.",
    },
    "daily_blocker_report.py": {
        "tier": "reporting",
        "category": "reporting",
        "summary": "Produces daily blocker and fidelity reports.",
    },
    "desol_config.py": {
        "tier": "internal_support",
        "category": "configuration",
        "summary": "Centralizes DESol runtime configuration helpers.",
    },
    "diagnose_grounding_bottlenecks.py": {
        "tier": "research_experiment",
        "category": "reliability",
        "summary": "Diagnoses grounding bottlenecks in experimental runs.",
    },
    "distributed_proof_cache.py": {
        "tier": "official_support",
        "category": "proof_search",
        "summary": "Provides the SQLite proof-result cache used by workers.",
    },
    "equivalence_repair.py": {
        "tier": "research_experiment",
        "category": "repair",
        "summary": "Repairs statement-equivalence failures experimentally.",
    },
    "eval_translation_fidelity.py": {
        "tier": "ci_gate",
        "category": "ci",
        "summary": "Evaluates translation fidelity against a gold set.",
    },
    "external_method_benchmark.py": {
        "tier": "reporting",
        "category": "benchmark",
        "summary": "Compares DESol evidence against adjacent arXiv methods.",
    },
    "export_april_repair_dataset.py": {
        "tier": "reporting",
        "category": "repair",
        "summary": "Exports the DESol compiler-feedback repair dataset from ledgers and logs.",
    },
    "focus_blocker_loop.py": {
        "tier": "research_experiment",
        "category": "repair",
        "summary": "Runs an experimental loop focused on dominant blockers.",
    },
    "formalize_paper_full.py": {
        "tier": "official_pipeline",
        "category": "orchestration",
        "summary": "Canonical full-paper reproducibility and closure harness.",
    },
    "formalize_reliable_lane.py": {
        "tier": "research_experiment",
        "category": "orchestration",
        "summary": "Experimental reliable-lane orchestration variant.",
    },
    "gold_linkage_eval.py": {
        "tier": "ci_gate",
        "category": "ci",
        "summary": "Evaluates KG linkage precision and recall gates.",
    },
    "import_validator.py": {
        "tier": "internal_support",
        "category": "lean_backend",
        "summary": "Validates Lean import availability and hygiene.",
    },
    "ingest_2304_09598_to_kg.py": {
        "tier": "legacy_one_off",
        "category": "kg",
        "summary": "One-off KG ingestion for the 2304.09598 paper artifact.",
    },
    "kg_api.py": {
        "tier": "official_support",
        "category": "kg",
        "summary": "FastAPI service for KG queries and verification enqueueing.",
    },
    "kg_writer.py": {
        "tier": "official_support",
        "category": "kg",
        "summary": "Builds KG layers, SQLite index, and promotion manifests.",
    },
    "latex_preprocessor.py": {
        "tier": "official_support",
        "category": "ingestion",
        "summary": "Expands LaTeX macros and include trees before extraction.",
    },
    "lean_repl_dojo.py": {
        "tier": "official_support",
        "category": "lean_backend",
        "summary": "REPLDojo Lean backend for incremental proof checking.",
    },
    "lean_repl_server.py": {
        "tier": "official_support",
        "category": "lean_backend",
        "summary": "Persistent Lean REPL server used by proof search.",
    },
    "lean_sanitize.py": {
        "tier": "internal_support",
        "category": "lean_backend",
        "summary": "Sanitizes generated Lean snippets before checking.",
    },
    "lean_validation.py": {
        "tier": "official_support",
        "category": "lean_backend",
        "summary": "Validates generated Lean statements and proofs.",
    },
    "library_first_bootstrap.py": {
        "tier": "research_experiment",
        "category": "orchestration",
        "summary": "Experimental library-first bootstrap for domain gaps.",
    },
    "mathlib_contrib.py": {
        "tier": "dev_tool",
        "category": "research",
        "summary": "Checks novelty and generates Mathlib contribution skeletons.",
    },
    "mcts_core_types.py": {
        "tier": "internal_support",
        "category": "proof_search",
        "summary": "Shared types for MCTS proof-search implementations.",
    },
    "mcts_policy.py": {
        "tier": "internal_support",
        "category": "proof_search",
        "summary": "Tactic policy helpers used by state-MCTS.",
    },
    "mcts_search.py": {
        "tier": "official_support",
        "category": "proof_search",
        "summary": "Lower-level theorem proof-search CLI and implementation.",
    },
    "merge_worker_results.py": {
        "tier": "internal_support",
        "category": "orchestration",
        "summary": "Merges outputs produced by parallel workers.",
    },
    "merlean_compare.py": {
        "tier": "research_experiment",
        "category": "benchmark",
        "summary": "Compares DESol outputs with MerLean-style checks.",
    },
    "paper_agnostic_consistency_gate.py": {
        "tier": "research_experiment",
        "category": "ci",
        "summary": "Experimental consistency gate for paper-agnostic runs.",
    },
    "paper_agnostic_report.py": {
        "tier": "reporting",
        "category": "reporting",
        "summary": "Summarizes paper-level behavior from verification ledgers.",
    },
    "paper_closure_checklist.py": {
        "tier": "reporting",
        "category": "reporting",
        "summary": "Builds closure checklists for paper formalization attempts.",
    },
    "paper_ingestion_evidence.py": {
        "tier": "reporting",
        "category": "ingestion",
        "summary": "Runs fetch/extraction evidence passes for paper suites.",
    },
    "paper_readiness_score.py": {
        "tier": "reporting",
        "category": "reporting",
        "summary": "Scores paper readiness from available artifacts.",
    },
    "paper_symbol_inventory.py": {
        "tier": "reporting",
        "category": "reporting",
        "summary": "Inventories paper-local symbols for theory and axiom-debt analysis.",
    },
    "paper_theory_builder.py": {
        "tier": "official_support",
        "category": "translation",
        "summary": "Builds paper-local theory modules for full-paper runs.",
    },
    "pipeline_orchestrator.py": {
        "tier": "official_support",
        "category": "orchestration",
        "summary": "File-backed queue, checkpoints, and drift snapshots.",
    },
    "pipeline_status.py": {
        "tier": "official_support",
        "category": "support",
        "summary": "Computes verification status and ledger entries.",
    },
    "pipeline_status_classification.py": {
        "tier": "internal_support",
        "category": "support",
        "summary": "Shared status-classification helpers.",
    },
    "pipeline_status_models.py": {
        "tier": "official_support",
        "category": "support",
        "summary": "Typed status, provenance, and ledger data models.",
    },
    "pipeline_worker.py": {
        "tier": "official_pipeline",
        "category": "orchestration",
        "summary": "Worker process that executes queued pipeline jobs.",
    },
    "ponder_loop.py": {
        "tier": "internal_support",
        "category": "proof_search",
        "summary": "Structured proof-planning loop used by proof search.",
    },
    "premise_retrieval.py": {
        "tier": "official_support",
        "category": "proof_search",
        "summary": "Retrieves Mathlib and KG premises for proof search.",
    },
    "statement_retrieval.py": {
        "tier": "official_support",
        "category": "kg",
        "summary": "Builds and queries theorem-level semantic indexes from extracted statements.",
    },
    "proof_backend.py": {
        "tier": "official_support",
        "category": "lean_backend",
        "summary": "Selects and wraps available Lean proof backends.",
    },
    "prove_arxiv_batch.py": {
        "tier": "official_support",
        "category": "proof_search",
        "summary": "Batch proof-search pass used by full-paper orchestration.",
    },
    "prove_with_ponder.py": {
        "tier": "official_support",
        "category": "proof_search",
        "summary": "Full-draft and repair proof-search driver.",
    },
    "prove_with_ponder_exec.py": {
        "tier": "internal_support",
        "category": "proof_search",
        "summary": "Execution helper for ponder-based proof search.",
    },
    "prove_with_ponder_repo.py": {
        "tier": "internal_support",
        "category": "proof_search",
        "summary": "Repository-isolated helper for ponder proof runs.",
    },
    "quality_gates_report.py": {
        "tier": "reporting",
        "category": "reporting",
        "summary": "Generates verification quality-gate reports.",
    },
    "regenerate_actionable_theorems.py": {
        "tier": "research_experiment",
        "category": "repair",
        "summary": "Regenerates actionable theorem sets for repair loops.",
    },
    "release_readiness.py": {
        "tier": "ci_gate",
        "category": "ci",
        "summary": "Runs baseline release-readiness checks in CI.",
    },
    "reproduce_public_claims.py": {
        "tier": "official_pipeline",
        "category": "orchestration",
        "summary": "One-command harness for rebuilding public claim artifacts.",
    },
    "replay_hard_failures.py": {
        "tier": "research_experiment",
        "category": "reliability",
        "summary": "Replays difficult failures for bottleneck analysis.",
    },
    "repair_bad_translations.py": {
        "tier": "official_support",
        "category": "repair",
        "summary": "Repairs invalid translations during full-paper runs.",
    },
    "repair_feedback_dataset.py": {
        "tier": "internal_support",
        "category": "repair",
        "summary": "Shared schema helpers for compiler-feedback repair datasets.",
    },
    "research.py": {
        "tier": "research_experiment",
        "category": "research",
        "summary": "Research CLI for conjecture generation and promotion.",
    },
    "retranslate_5_theorems.py": {
        "tier": "legacy_one_off",
        "category": "translation",
        "summary": "One-off retranslation utility for five theorem artifacts.",
    },
    "reliability_soak.py": {
        "tier": "ci_gate",
        "category": "reliability",
        "summary": "Scheduled queue reliability soak test.",
    },
    "run_benchmark_audit_bundle.py": {
        "tier": "reporting",
        "category": "benchmark",
        "summary": "Runs and packages benchmark audit artifacts.",
    },
    "run_closure_slices.py": {
        "tier": "research_experiment",
        "category": "orchestration",
        "summary": "Runs early-stopping closure slices over full-paper harnesses.",
    },
    "run_golden10_translation.py": {
        "tier": "research_experiment",
        "category": "translation",
        "summary": "Runs translation-only experiments over the golden10 suite.",
    },
    "run_paper_agnostic_suite.py": {
        "tier": "official_pipeline",
        "category": "orchestration",
        "summary": "Canonical fixed-config suite runner for paper-agnostic evidence.",
    },
    "run_stratified_bottleneck_suite.py": {
        "tier": "research_experiment",
        "category": "reliability",
        "summary": "Runs stratified bottleneck experiments over hard cases.",
    },
    "script_registry.py": {
        "tier": "dev_tool",
        "category": "support",
        "summary": "Lists and validates the script maturity registry.",
    },
    "seed_kg_from_mathlib.py": {
        "tier": "dev_tool",
        "category": "kg",
        "summary": "Seeds local KG data from Mathlib artifacts.",
    },
    "semantic_fidelity_audit.py": {
        "tier": "research_experiment",
        "category": "translation",
        "summary": "Audits semantic fidelity for experimental runs.",
    },
    "smoke_test.py": {
        "tier": "ci_gate",
        "category": "ci",
        "summary": "No-API smoke test for installed repository basics.",
    },
    "statement_translator.py": {
        "tier": "official_support",
        "category": "translation",
        "summary": "Translates extracted LaTeX statements to Lean candidates.",
    },
    "statement_validity.py": {
        "tier": "official_support",
        "category": "translation",
        "summary": "Classifies generated Lean statements and emits proof-repair cohorts.",
    },
    "statement_retrieval.py": {
        "tier": "official_support",
        "category": "proof_search",
        "summary": "Builds and queries theorem-level semantic statement retrieval indexes.",
    },
    "step_entailment_checker.py": {
        "tier": "internal_support",
        "category": "proof_search",
        "summary": "Parses and checks proof-step entailment obligations.",
    },
    "tactic_training.py": {
        "tier": "research_experiment",
        "category": "proof_search",
        "summary": "Exports triples and trains tactic-ranking policies.",
    },
    "validate_statement_cohort.py": {
        "tier": "official_support",
        "category": "translation",
        "summary": "Writes statement-validity reports and proof-repair-only cohorts.",
    },
    "theorem_extractor.py": {
        "tier": "official_support",
        "category": "ingestion",
        "summary": "Extracts theorem-like LaTeX environments and aliases.",
    },
    "weekly_benchmark_report.py": {
        "tier": "reporting",
        "category": "reporting",
        "summary": "Builds scheduled benchmark and bridge-progress reports.",
    },
    "world_model_bridge.py": {
        "tier": "research_experiment",
        "category": "bridge",
        "summary": "Experimental world-model bridge implementation.",
    },
    "__init__.py": {
        "tier": "internal_support",
        "category": "support",
        "summary": "Package marker for importable script modules.",
    },
}


def top_level_script_names(scripts_dir: Path | None = None) -> list[str]:
    root = scripts_dir or Path(__file__).resolve().parent
    return sorted(path.name for path in root.glob("*.py"))


def unregistered_scripts(scripts_dir: Path | None = None) -> list[str]:
    return [name for name in top_level_script_names(scripts_dir) if name not in SCRIPT_REGISTRY]


def registry_rows(
    *,
    tier: str | None = None,
    category: str | None = None,
) -> list[tuple[str, dict[str, str]]]:
    rows = sorted(SCRIPT_REGISTRY.items())
    if tier:
        rows = [(name, row) for name, row in rows if row["tier"] == tier]
    if category:
        rows = [(name, row) for name, row in rows if row["category"] == category]
    return rows


def _format_text(rows: Iterable[tuple[str, dict[str, str]]]) -> str:
    lines = ["script                              tier                 category        summary"]
    lines.append("-" * 110)
    for name, row in rows:
        lines.append(f"{name:<35} {row['tier']:<20} {row['category']:<15} {row['summary']}")
    return "\n".join(lines)


def _format_markdown(rows: Iterable[tuple[str, dict[str, str]]]) -> str:
    lines = ["| Script | Tier | Category | Summary |", "|---|---|---|---|"]
    for name, row in rows:
        lines.append(f"| `{name}` | `{row['tier']}` | `{row['category']}` | {row['summary']} |")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="List or validate DESol script maturity classifications.")
    parser.add_argument("--tier", choices=sorted(VALID_TIERS), default="")
    parser.add_argument("--category", choices=sorted(VALID_CATEGORIES), default="")
    parser.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    parser.add_argument("--check", action="store_true", help="Fail if top-level scripts are missing registry rows.")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.check:
        missing = unregistered_scripts()
        if missing:
            print(json.dumps({"ok": False, "missing": missing}, indent=2))
            return 1
        print(json.dumps({"ok": True, "registered": len(SCRIPT_REGISTRY)}, indent=2))
        return 0

    rows = registry_rows(tier=args.tier or None, category=args.category or None)
    if args.format == "json":
        print(json.dumps({name: row for name, row in rows}, indent=2, sort_keys=True))
    elif args.format == "markdown":
        print(_format_markdown(rows))
    else:
        print(_format_text(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
