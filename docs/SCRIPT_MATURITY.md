# Script Maturity Contract

`scripts/` contains production entrypoints, shared implementation modules,
scheduled gates, reports, and experiments. The authoritative inventory is
`scripts/script_registry.py`; this document explains how to read it.

To inspect the current registry:

```bash
python scripts/script_registry.py
python scripts/script_registry.py --tier official_pipeline
python scripts/script_registry.py --format markdown
```

`tests/test_script_registry.py` fails when a new top-level `scripts/*.py` file is
added without a registry row. That test is the trust boundary: unregistered
scripts are not allowed to silently become part of the repo surface.

## Official Pipeline Surface

Only these scripts define the public pipeline commands:

- `arxiv_to_lean.py`: canonical single-paper arXiv-to-Lean run.
- `formalize_paper_full.py`: canonical full-paper reproducibility and closure
  harness used by committed report manifests.
- `run_paper_agnostic_suite.py`: fixed-config suite runner around
  `formalize_paper_full.py`.
- `arxiv_cycle.py`: batch runner for curated paper queues and KG rebuilds.
- `arxiv_cycle_daemon.py`: long-running queue daemon with arXiv preflight checks.
- `pipeline_worker.py`: worker process for queued verification jobs.
- `reproduce_public_claims.py`: one-command public-claims reproduction harness
  (full pipeline or CI-friendly `--smoke` evidence indexing).

These commands may call many support modules, but support modules are not
separate official pipeline definitions.

## Official Support Modules

Support modules implement stable pieces of the pipeline without defining a
competing top-level workflow. Examples include ingestion
(`arxiv_fetcher.py`, `latex_preprocessor.py`, `theorem_extractor.py`),
translation and validation (`statement_translator.py`, `lean_validation.py`,
`paper_theory_builder.py`), proof search (`prove_arxiv_batch.py`,
`prove_with_ponder.py`, `mcts_search.py`, `premise_retrieval.py`), KG services
(`kg_writer.py`, `kg_api.py`), and status models (`pipeline_status.py`,
`pipeline_status_models.py`).

Use `python scripts/script_registry.py --tier official_support` for the complete
current list.

## Reports, Benchmarks, And CI Gates

Scripts classified as `reporting`, `benchmark`, or `ci_gate` produce evidence or
enforce thresholds. Public claims should cite the artifacts they emit, not just
the command names. Scheduled workflows currently use the gate scripts in
`.github/workflows/`, including `release_readiness.py`, `smoke_test.py`,
`eval_translation_fidelity.py`, `gold_linkage_eval.py`,
`ci_assert_quality_gates.py`, `weekly_benchmark_report.py`,
`ci_assert_bridge_progress.py`, and `reliability_soak.py`.

The stable repair-data reporting artifact is
`output/flywheel/compiler_feedback_repair_dataset.jsonl`, with
`output/flywheel/compiler_feedback_repair_dataset_summary.json` as its summary.
It is produced by `export_april_repair_dataset.py` from verification ledgers and
run-local captures under `output/flywheel/runs/<run_id>/`. Lean
validation/repair loops append to those per-run files; the canonical artifact is
the deduplicated merge product.

Claim-equivalence review artifacts live under `output/claim_equivalence/`.
`build_claim_equivalence_review_queue.py` and
`apply_claim_equivalence_adjudications.py` are reporting/review tools that add
auditable independent semantic evidence; they do not relax the strict promotion
gates. LLM-only adjudications are treated as triage and must be followed by
human or hybrid review before they can affect headline `FULLY_PROVEN` release
claims.

## Research And One-Off Scripts

Scripts classified as `research_experiment` or `legacy_one_off` are allowed to be
useful but are not stable public API. This includes bridge experiments,
reliability probes, repair flywheels, ad hoc retranslation utilities, and older
paper-specific ingestion helpers. Promote one only by changing its registry tier,
documenting its command contract, and adding or updating tests.

## Adding Or Promoting Scripts

When adding a new top-level script:

1. Add a row to `SCRIPT_REGISTRY` with a tier, category, and plain-English
   summary.
2. If it is a public pipeline command, update `OFFICIAL_PIPELINE_SCRIPTS` and
   this document's official surface.
3. Add tests for the behavior you want users or CI to trust.
4. Prefer moving reusable implementation into importable modules over adding
   another orchestration script.

Long term, stable implementation code should move into a Python package such as
`desol/`, with `scripts/` kept as thin CLI wrappers. Do not move files only for
cosmetics; move a script when its API is stable enough to test and document.
