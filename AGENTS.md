# AGENTS.md — DESol repo guide

DESol is a paper-to-Lean formalization pipeline: it ingests arxiv papers, extracts theorems, translates them to Lean 4, runs proof search via MCTS + Leanstral, and produces a verification ledger of FULLY_PROVEN / INTERMEDIARY_PROVEN / AXIOM_BACKED / UNRESOLVED / FLAWED rows per paper.

## Pipeline entry points (in order)

1. **`scripts/arxiv_to_lean.py`** — ingest, extract, translate one paper to a `.lean` file under `output/<paper_id>.lean`. Acceptance gate at line ~1228 catches placeholder / raw-LaTeX / shape-mismatch translations.
2. **`scripts/paper_theory_builder.py`** — write `Desol/PaperTheory/Paper_<id>.lean` with paper-local definitions, axioms, and auto-emitted typeclass instances + aesop attributes on axioms.
3. **`scripts/regenerate_paper_imports_anchor.py`** — regenerate `Desol/PaperImportsAnchor.lean` (REPL fallback) so MCTS can see paper-theory namespaces even when a per-paper `.lean` fails to elaborate.
4. **`scripts/prove_arxiv_batch.py`** — proof loop. Tries `_run_deterministic_file_micro_prover` first (line ~2407 catalog: `aesop / simp_all / omega / linarith / norm_cast / gcongr / field_simp / polyrith / interval_cases`), then `_run_deterministic_micro_prover`, then state-MCTS with REPL.
5. **`scripts/build_claim_equivalence_review_queue.py`** → **`scripts/adjudicate_claim_equivalence.py`** → **`scripts/apply_claim_equivalence_adjudications.py`** — flip `claim_equivalence_verdict` on UNRESOLVED rows.
6. **`scripts/run_auto_alignment_review.py`** + **`scripts/run_review_to_gold_proof_bridge.py`** — produce reviewed-equivalent rows; bridge admits LLM-confirmed long/complex statements via the assisted-review fast-path.
7. **`scripts/apply_reviews_to_ledger.py`** — propagate reviewed_* fields into `output/verification_ledgers/<id>.json` (without this round-trip the LLM signal is invisible across reruns).
8. **`scripts/formalize_paper_full.py`** — canonical orchestrator that chains 1-7, plus `_publish_reproducibility_bundle()` which copies the ephemeral ledger to `reproducibility/full_paper_reports/<id>/verification_ledger.json` (the committed evidence path).

## Where state lives

- **Ephemeral**: `output/verification_ledgers/<id>.json` — proof search writes here. Many `_smoke / _actionable / _fdcheck / _patchcheck / _rflguard` variants exist; only the bare `<id>.json` is canonical.
- **Committed**: `reproducibility/full_paper_reports/<id>/verification_ledger.json` — only `formalize_paper_full.py` (or a manual `cp`) updates this.
- **Corpus exports**: `output/corpus/stable_corpus.jsonl`, `statement_review_batch.jsonl`, `auto_alignment_reviews.jsonl`, `assisted_reviewed_statement_alignment.v1.jsonl`, `reviewed_statement_corpus.jsonl`, `gold_proof_growth_queue.jsonl`.

## Promotion gates

- `pipeline_status.evaluate_promotion_gates()` decides FULLY_PROVEN ↔ INTERMEDIARY_PROVEN ↔ AXIOM_BACKED. Axiom debt → at most AXIOM_BACKED. Missing `claim_equivalent` AND `independent_semantic_equivalence_evidence` AND `provenance_linked` → INTERMEDIARY_PROVEN.
- `_is_release_eligible()` requires `reviewer_type ∈ {human, hybrid}` AND `review_policy == release_eligible`. LLM verdicts are blocked by design.
- `statement_validity.statement_fidelity_gate()` line ~604: special case `claim_review_pending` + `reviewed_ok` → `proof_eligible=True` even when `claim_equivalence_verdict='unclear'`.

## Test conventions

- 1,600+ tests in `tests/`. Run with `pytest tests/ -x -q`. Full suite ~5 min.
- Mark slow tests (lake/REPL/Mistral/HTTP) with `@pytest.mark.slow`; skip via `pytest -m 'not slow'`. Marker registered in `pytest.ini`.
- Most unit tests are hermetic (use `tmp_path`); some live tests read `output/corpus/*.jsonl` and skip when those files are absent.

## Common gotchas

- `output/2304.09598.lean` and friends are NOT in git — they're rebuilt by `arxiv_to_lean.py`. Don't manually edit them; rerun the pipeline.
- The REPL bootstrap returns `env: 0` — that IS the post-load env in the protocol, not a missing env. Don't conflate with bootstrap failure.
- `open` directives from anchor files do NOT carry over into REPL elaboration; `mcts/_state.py` replays them after bootstrap.
- Auto-LLM `reviewed_by='auto_llm:alignment-review'` is filtered as not-release-eligible by `_is_release_eligible_review` in the bridge — this is by design; auto-LLM provides alignment evidence but requires a hybrid-bridge wrapper for proof eligibility.

## Quick references

- `docs/PAPER_AGNOSTIC_PIPELINE.md` — high-level pipeline overview.
- `docs/SCRIPT_MATURITY.md` — registry of which scripts are official vs experimental.
- `docs/REPRODUCIBILITY_CONTRACT.md` — what the committed ledger guarantees.
- `reproducibility/SESSION_CAMPAIGN_REPORT.md` — round-by-round closure trajectory and remaining-margin enumeration.
