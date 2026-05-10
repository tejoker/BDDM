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
    "audit_axioms.py": {
        "tier": "reporting",
        "category": "review",
        "summary": "Per-paper paper-local axiom-budget audit; classifies rows as release_eligible/axiom_backed/intermediary based on axiom_debt + gate_failures.",
    },
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
    "apply_reviews_to_ledger.py": {
        "tier": "reporting",
        "category": "review",
        "summary": "Propagates reviewed_statement_corpus reviews back into output/verification_ledgers/<paper>.json so downstream gates see the LLM signal across reruns.",
    },
    "apply_translation_repairs.py": {
        "tier": "reporting",
        "category": "repair",
        "summary": "Re-applies translation repairs (via build_repair_pack) to an existing ledger without re-running the full formalize pipeline.",
    },
    "apply_statement_fidelity_reviews.py": {
        "tier": "reporting",
        "category": "review",
        "summary": "Applies reviewed statement-fidelity adjudications to corpus rows.",
    },
    "assist_statement_review_adjudication.py": {
        "tier": "reporting",
        "category": "review",
        "summary": "Conservatively assists reviewed-exact statement adjudication.",
    },
    "axiom_debt_burndown.py": {
        "tier": "reporting",
        "category": "reporting",
        "summary": "Summarizes paper-local axiom debt and burndown opportunities.",
    },
    "backfill_provenance.py": {
        "tier": "dev_tool",
        "category": "review",
        "summary": "Retroactively populates `provenance` (paper_id + label) on legacy ledger entries so the `provenance_linked` promotion gate can pass.",
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
    "build_alignment_review_queue.py": {
        "tier": "reporting",
        "category": "review",
        "summary": "Builds review queues for weak or ambiguous source alignment.",
    },
    "build_gold_proof_queue.py": {
        "tier": "reporting",
        "category": "proof_search",
        "summary": "Ranks strict proof-production candidates without changing proof metrics.",
    },
    "build_identity_review_queue.py": {
        "tier": "reporting",
        "category": "review",
        "summary": "Builds semantic identity and novelty review queues.",
    },
    "build_release_index.py": {
        "tier": "reporting",
        "category": "reporting",
        "summary": "Indexes canonical release artifacts and generated-output drift status.",
    },
    "build_statement_fidelity_queue.py": {
        "tier": "reporting",
        "category": "review",
        "summary": "Builds statement-fidelity review queues for partial or low-confidence rows.",
    },
    "build_statement_repair_queue.py": {
        "tier": "reporting",
        "category": "review",
        "summary": "Builds statement repair queues for rows blocked before exact review.",
    },
    "build_statement_review_batch.py": {
        "tier": "reporting",
        "category": "review",
        "summary": "Builds span-bound reviewed-exact statement adjudication batches.",
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
    "corpus_release_metadata.py": {
        "tier": "internal_support",
        "category": "support",
        "summary": "Shared release metadata, checksum, and audit validation helpers.",
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
    "leanstral_cot_judge.py": {
        "tier": "official_support",
        "category": "translation",
        "summary": "Chain-of-Thought Leanstral judge that reasons step-by-step (quantifiers/hypotheses/conclusion/abstraction) before issuing a verdict; accepts adequate-but-weaker translations.",
    },
    "leanstral_judge.py": {
        "tier": "official_support",
        "category": "translation",
        "summary": "Leanstral-powered claim equivalence judge for the FULLY_PROVEN gate.",
    },
    "leanstral_stub_recovery.py": {
        "tier": "official_support",
        "category": "translation",
        "summary": "Re-translates placeholder `theorem foo : False := by sorry` rows by calling Leanstral with explicit recovery hints derived from the BLOCKED reason.",
    },
    "rewrite_lean_from_ledger.py": {
        "tier": "dev_tool",
        "category": "translation",
        "summary": "Rewrites placeholder theorems in output/<paper>.lean using full signatures stored in the verification ledger; gives proof search a chance to attempt translator-rejected (false-positive) signatures.",
    },
    "paper_area_classifier.py": {
        "tier": "official_support",
        "category": "translation",
        "summary": "Classifies a paper into a math area (analysis / probability / algebra / combinatorics / numbertheory / generic) by keyword-matching its source LaTeX. Drives area-aware CoT prompts and per-area paper-theory generation.",
    },
    "mark_ghost_translation_failures.py": {
        "tier": "dev_tool",
        "category": "review",
        "summary": "Marks UNRESOLVED ledger rows whose `output/<paper>.lean` is missing as TRANSLATION_LIMITED — honest accounting for paper-id queue ghosts.",
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
    "export_corpus.py": {
        "tier": "reporting",
        "category": "kg",
        "summary": "Exports stable theorem-level corpus rows from paper artifacts.",
    },
    "export_corpus_dataset.py": {
        "tier": "dev_tool",
        "category": "reporting",
        "summary": "HF-Datasets-shaped export of the BDDM corpus with provenance + status. Local-only; not a publication step.",
    },
    "export_finetune_dataset.py": {
        "tier": "dev_tool",
        "category": "reporting",
        "summary": "Generates SFT (supervised fine-tuning) jsonl from corpus rows: translation, equivalence-judging, and tactic-suggestion examples.",
    },
    "export_curated_corpus.py": {
        "tier": "reporting",
        "category": "kg",
        "summary": "Exports curated gold, alignment, silver, and excluded corpus surfaces.",
    },
    "export_silver_repair_dataset.py": {
        "tier": "reporting",
        "category": "repair",
        "summary": "Exports paper-agnostic silver repair data with explicit positive and negative labels.",
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
    "novelty_dedup.py": {
        "tier": "reporting",
        "category": "reporting",
        "summary": "Annotates ledger statements with novelty and deduplication evidence.",
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
    "regenerate_paper_imports_anchor.py": {
        "tier": "official_support",
        "category": "lean_backend",
        "summary": "Regenerates Desol/PaperImportsAnchor.lean (REPL fallback) so MCTS sees paper-theory namespaces when the per-paper output .lean fails to elaborate.",
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
    "repair_paper_theory_exports.py": {
        "tier": "dev_tool",
        "category": "lean_backend",
        "summary": "Filters Desol/PaperTheory/Paper_*.lean export lines to drop names not actually defined as top-level decls (idempotent).",
    },
    "repair_extracted_theorem_spans.py": {
        "tier": "reporting",
        "category": "review",
        "summary": "Attaches extractor-native source spans to legacy theorem extraction artifacts.",
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
    "run_auto_alignment_review.py": {
        "tier": "official_support",
        "category": "review",
        "summary": "Runs structured auto alignment review and triage for statement-review batches.",
    },
    "run_paper_agnostic_suite.py": {
        "tier": "official_pipeline",
        "category": "orchestration",
        "summary": "Canonical fixed-config suite runner for paper-agnostic evidence.",
    },
    "run_gold_proof_queue.py": {
        "tier": "official_support",
        "category": "proof_search",
        "summary": "Runs or dry-runs strict gold-proof queue proof-search commands.",
    },
    "run_proof_candidate_factory.py": {
        "tier": "official_support",
        "category": "proof_search",
        "summary": "End-to-end proof-candidate factory: statement repair → auto alignment review → gold proof queue.",
    },
    "run_review_to_gold_proof_bridge.py": {
        "tier": "official_support",
        "category": "proof_search",
        "summary": "Bridges conservative reviewed-exact statement rows into strict gold proof queues.",
    },
    "run_statement_repair_worker.py": {
        "tier": "official_support",
        "category": "repair",
        "summary": "Processes statement-repair queue rows produced by the hard statement-fidelity gate.",
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
    "source_evidence_resolver.py": {
        "tier": "internal_support",
        "category": "review",
        "summary": "Shared conservative resolver for source evidence and source-span repair.",
    },
    "statement_translator.py": {
        "tier": "official_support",
        "category": "translation",
        "summary": "Translates extracted LaTeX statements to Lean candidates.",
    },
    "statement_alignment.py": {
        "tier": "official_support",
        "category": "translation",
        "summary": "Provides deterministic LaTeX-to-Lean statement alignment helpers.",
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
    "sync_release_mirrors.py": {
        "tier": "reporting",
        "category": "reporting",
        "summary": "Synchronizes existing generated mirrors from canonical release bundle artifacts.",
    },
    "onboard_arxiv_paper.py": {
        "tier": "official_support",
        "category": "orchestration",
        "summary": "Single-command end-to-end arxiv-paper onboarding: translate → lint → paper-theory → anchor → prove → CoT review → bridge → audit → publish. Scalable to any paper.",
    },
    "onboard_curated_batch.py": {
        "tier": "dev_tool",
        "category": "orchestration",
        "summary": "Batch-onboards a curated paper list (data/curated_easy_corpus.txt) via onboard_arxiv_paper. Used for the closure-rate dilution play.",
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
    "translation_linter.py": {
        "tier": "official_support",
        "category": "translation",
        "summary": "Lints paper→Lean translations for recurring bugs (typeclass-in-existential, latex-leak tokens, placeholder targets, false-target fallbacks). Pre-prover hook.",
    },
    "translation_autorepair.py": {
        "tier": "official_support",
        "category": "translation",
        "summary": "Auto-repair pass for translator bugs: typeclass-in-existential → top-level binders, LaTeX subscript/superscript braces → Lean-native form. Idempotent.",
    },
    "theorem_extractor.py": {
        "tier": "official_support",
        "category": "ingestion",
        "summary": "Extracts theorem-like LaTeX environments and aliases.",
    },
    "upgrade_existing_paper_theory_stubs.py": {
        "tier": "dev_tool",
        "category": "lean_backend",
        "summary": "Retroactively appends auto-emitted typeclass instances and aesop attributes to existing Desol/PaperTheory/Paper_*.lean stubs (idempotent).",
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
