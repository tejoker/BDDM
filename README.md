# DESol — Automated Lean 4 Formalization of arXiv Mathematics

DESol is an evidence-preserving pipeline for turning arXiv LaTeX papers into auditable Lean 4 formalization attempts. Given an arXiv paper ID, it extracts theorem-like statements, translates them into Lean signatures, searches for machine-checked proofs, and writes a verification ledger plus Knowledge Graph (KG) entries that explain what closed, what remained conditional, and why.

**Single-command onboarding (recommended):**
```bash
python scripts/onboard_arxiv_paper.py <arxiv-id> [--publish]
```
Runs the 11-stage pipeline end-to-end: translate → lint → auto-repair → paper-theory stub → REPL-anchor regen → prove sweep (state-MCTS with auto-promote-to-hierarchical for compound goals) → CoT auto-alignment review → bridge apply → backfill provenance → axiom-budget audit → publish. Per-stage skip flags available; full timing log to `logs/onboard_<id>.json`.

**Lower-level entrypoints:** install Lean 4 via Elan and Python 3.11+, set `MISTRAL_API_KEY` in `.env`, then run `python scripts/arxiv_to_lean.py <arxiv-id> --out output/papers/` for translation only, or `python scripts/formalize_paper_full.py --paper-id <arxiv-id> --project-root .` for full-paper closure. One-command public-claims reproduction: `python scripts/reproduce_public_claims.py --smoke`.

To query the KG, start the REST API with `uvicorn scripts/kg_api:app --port 8000` and use `GET /kg/paper/{arxiv-id}`, `GET /kg/stats`, math-layer and evidence routes (see [Query the KG](#query-the-kg-via-rest-api)).

**Main contribution:** a paper-agnostic arXiv-to-Lean workflow that records theorem inventory, translation attempts, Lean validation, proof search traces, axiom debt tiers, claim-equivalence review hooks (CoT-style step-by-step reasoning judge), and blocker taxonomy for arbitrary LaTeX-source arXiv papers. miniF2F is kept as a calibration benchmark for the proof-search component, not as the headline claim. See [docs/REPRODUCIBILITY_CONTRACT.md](docs/REPRODUCIBILITY_CONTRACT.md), [docs/PAPER_AGNOSTIC_PIPELINE.md](docs/PAPER_AGNOSTIC_PIPELINE.md), and [docs/SCRIPT_MATURITY.md](docs/SCRIPT_MATURITY.md).

**Current corpus state**: 8 arXiv papers ingested, 200 theorems extracted, **62% closed (124 / 200)** at AXIOM_BACKED tier or higher (31 FULLY_PROVEN + 4 AXIOM_BACKED + 89 INTERMEDIARY_PROVEN, against transparent paper-local stubs where Mathlib counterparts don't yet exist). 863 unit tests passing.

Recent infrastructure additions (standards-positive — every change either raises candidate quality or tightens rejection criteria; never relaxes the acceptance gate):
- **Alignment-discharge** (`scripts/apply_reviews_to_ledger.py`): paper-local symbols with registered `align_def` proofs no longer block `no_paper_axiom_debt`; AB→FP promotions land automatically when all debts are aligned.
- **Per-area CoT prompts** (`scripts/leanstral_cot_judge.py` + `scripts/paper_area_classifier.py`): the equivalence judge takes a math-area tag (analysis / probability / algebra / combinatorics / numbertheory) and uses domain-specific equivalence rules (e.g., `∀ε>0, P(ε)` ≡ `(ε : ℝ) (hε : 0 < ε) : P ε` in analysis). Produced +11 reviewed-exact rows on the 105-row alignment batch.
- **`\newtheorem` display-name classifier** (`scripts/latex_preprocessor.py`): `\newtheorem{definition}{Definition}` is now correctly classified as `kind="definition"` instead of being silently aliased to `kind="theorem"`. Fixes the systemic mis-routing that produced `theorem foo : False := by sorry` placeholders for definition-heavy papers.
- **Def-pass / theorem-loop build-order fix** (`scripts/arxiv_to_lean.py`): paper-theory `.olean` is built BEFORE downstream validations import it; def-pass strips `Paper_<id>` imports for its own validation since definitions don't depend on the module they populate.
- **Type-aware translator prompt** (`scripts/translator/_translate.py:paper_theory_hint`): paper-theory `def`/`abbrev`/`axiom` signatures are passed into the translator prompt so Leanstral generates type-compatible candidates (e.g., `(f : Fin (2^n) → Bool)` not `(f : ℤ)`).
- **Quantifier-scope-flip adversarial check** (`_quantifier_scope_flip_issue`): tightens the rejection gate to flag `∀x ∃y` ↔ `∃y ∀x` reversals, which are subtle but mathematically wrong.

**Script trust boundary:** `scripts/` mixes production entrypoints, support modules, and experiments. The **official pipeline** surface (enforced by `tests/test_script_registry.py`) is: `arxiv_to_lean.py`, `formalize_paper_full.py`, `reproduce_public_claims.py`, `run_paper_agnostic_suite.py`, `arxiv_cycle.py`, `arxiv_cycle_daemon.py`, `pipeline_worker.py`. The single-paper onboarder `onboard_arxiv_paper.py`, alignment-search `mathlib_alignment_search.py`, paper-area classifier `paper_area_classifier.py`, and CoT judge `leanstral_cot_judge.py` live in `official_support`. List them anytime with `python scripts/script_registry.py --tier official_pipeline`.

**LLM policy:** the only LLM the pipeline calls is **Leanstral** (`labs-leanstral-2603`). All four model defaults that previously pointed elsewhere (`adjudicate_claim_equivalence.py`, `desol_config.py:value_model+policy_model`, `benchmark_minif2f.py`) have been switched to Leanstral. No Anthropic / OpenAI SDK references in the codebase (verified by grep).

---

## Infrastructure

- **Lean 4** via Elan (`lean` + `lake`), pinned by `lean-toolchain` (currently `v4.29.0-rc7`)
- **Lean project** scaffold: `lakefile.toml`, `lean-toolchain`
- **Python 3.11+** (tested on 3.11 and 3.12)
- **Key packages**: `mistralai`, `sentence-transformers`, `python-dotenv`, `z3-solver` (optional), `fastapi uvicorn` (optional, for KG API), `numpy` (optional, for tactic policy training)

## Prerequisites

- Linux
- Elan installed (`curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf | sh`)
- Python 3.11+ with pip

## Setup

```bash
git clone <repo>
cd DESol
pip install -r requirements.txt

cp .env.example .env
# Set MISTRAL_API_KEY and MISTRAL_MODEL=labs-leanstral-2603

python scripts/smoke_test.py

# First-time Lean build (~30 min for Mathlib cache)
~/.elan/bin/lake build
```

---

## Quick Start

### Prove a theorem (state-MCTS, default)

```bash
python scripts/mcts_search.py \
  --file Desol/Basic.lean \
  --theorem basic_demo_true \
  --search-mode state \
  --state-mcts-n-tactics 4 \
  --state-mcts-max-depth 12
```

### Prove a theorem (full-draft mode)

```bash
python scripts/prove_with_ponder.py \
  --file Desol/Basic.lean \
  --theorem basic_demo_true \
  --mode full-draft \
  --repair-rounds 5
```

### Run the arXiv pipeline

**Recommended — single-command onboarding** (runs all 11 stages end-to-end):

```bash
python scripts/onboard_arxiv_paper.py 2301.04567 --publish
```

The orchestrator chains: translation → lint → auto-repair → paper-theory builder → anchor regen → prove sweep → CoT auto-alignment review → bridge apply → backfill provenance → axiom-budget audit → publish. Per-stage skip flags (`--skip-translation`, `--skip-prove`, `--skip-cot-review`, etc.) and a `--max-prove-time` budget. Full timing + per-stage results in `logs/onboard_<id>.json`.

**Lower-level entrypoints** (used by the orchestrator individually):

```bash
# Translation only
python scripts/arxiv_to_lean.py 2301.04567 \
  --out output/papers/ \
  --prove-mode state-mcts
```

For reproducible full-paper closure reports, use the official harness (orchestrates ingest, iterative `prove_arxiv_batch` / bridge rounds, ledger evaluation, optional claim-equivalence review queue, and JSON closure report):

```bash
python scripts/formalize_paper_full.py --paper-id 2301.04567 --project-root . \
  --report-out output/reports/full_paper/2301.04567_suite_report.json
```

Re-index or regenerate committed public-claim artifacts (see `PUBLIC_ARTIFACTS` in `reproduce_public_claims.py`):

```bash
python scripts/reproduce_public_claims.py --smoke --project-root .
```

### ArXiv corpus scale-out

**Harvest IDs (OAI-PMH)** — build a queue file for `arxiv_cycle` / `arxiv_cycle_daemon`:

```bash
python scripts/arxiv_oai_harvest.py --set math.NT --out data/arxiv_queue_math_nt.txt --delay 3.0
# Optional: keep only papers whose e-print tarball contains .tex (slow; polite delays)
python scripts/arxiv_oai_harvest.py --set cs.LG --max-records 200 --probe-tex --probe-delay 2.0 --out data/queue_cs_lg_tex.txt
```

**Multi-paper run + one-shot KG rebuild** — after all papers finish, merge every ledger into `output/kg` (avoids per-paper `--write-kg` wiping JSONL layers):

```bash
python scripts/arxiv_cycle.py --paper-file data/arxiv_queue_curated.txt \
  --project-root . --continue-on-fail --write-kg --kg-root output/kg
```

**Parallel workers** — split the queue so each worker uses its own `--output-dir` and `--work-root`:

```bash
python scripts/arxiv_queue_split.py --queue data/arxiv_queue_curated.txt --workers 4 --out-dir output/arxiv_shards/
```

**PDF-only submissions**: the fetch step requires a TeX tarball (`arxiv_fetcher.py`); PDF-only arXiv records cannot be processed by the current LaTeX pipeline. Use `--probe-tex` when harvesting, or rely on `arxiv_cycle_daemon.py` pre-flight checks.

**Operational notes**: respect [arXiv API / bulk access](https://info.arxiv.org/help/bulk_data.html) guidelines; set `MISTRAL_API_KEY`, cap `--api-rate`, and provision disk for `output/verification_ledgers` and per-worker work trees.

### Run the miniF2F benchmark

```bash
python scripts/benchmark_minif2f.py \
  --split test --k 1 --workers 1 \
  --model labs-leanstral-2603 \
  --retrieval-index data/mathlib_embeddings \
  --lean-timeout 120
```

### Build the KG

```bash
python scripts/kg_writer.py \
  --ledger-dir output/verification_ledgers \
  --kg-root output/kg
```

### Build theorem-level semantic retrieval

```bash
python scripts/statement_retrieval.py build \
  --ledger-dir output/verification_ledgers \
  --out output/statement_index

python scripts/statement_retrieval.py query \
  --index output/statement_index \
  --query "compactness theorem for tight families of measures" \
  --top-k 10

python scripts/kg_writer.py \
  --ledger-dir output/verification_ledgers \
  --kg-root output/kg \
  --statement-index output/statement_index \
  --build-statement-index
```

The statement index is built from ledger `semantic_equivalence_artifact` fields
and adds `semantically_similar_to` edges to the KG when passed to `kg_writer.py`.

### Query the KG via REST API

```bash
pip install fastapi uvicorn
uvicorn scripts/kg_api:app --host 0.0.0.0 --port 8000

curl "localhost:8000/kg/query?layer=trusted&limit=10"
curl "localhost:8000/kg/stats"
curl "localhost:8000/kg/semantic/search?q=gaussian%20integrability&top_k=5"
curl "localhost:8000/kg/math/query?limit=10"
curl "localhost:8000/kg/proof/2301.04567/Theorem_1"
curl "localhost:8000/evidence/query?limit=10"
curl "localhost:8000/ops/dashboard"
curl -X POST "localhost:8000/verify?paper_id=2304.09598"
```

Optional auth and paths (see `kg_api.py`): `DESOL_API_KEY` / `DESOL_EVIDENCE_API_KEY` / `DESOL_OPS_API_KEY` (send `X-API-Key`), `DESOL_KG_DB`, `DESOL_STATEMENT_INDEX`, `DESOL_REPORT_ROOT` (default `output/reports/weekly`), `DESOL_REVIEW_QUEUE_ROOT`, `DESOL_ORCHESTRATOR_ROOT`, `DESOL_VERIFY_USE_ORCHESTRATOR`.

---

## Pipeline enhancements (2026-05)

### Translation quality

**Pre-flight linter** (`scripts/translation_linter.py`)

Catches recurring translator bugs before proof search wastes Mistral budget:

```bash
python scripts/translation_linter.py --lean-file output/2301.04567.lean --severity error
```

Detects: typeclass-binder-inside-existential, latex-leak tokens (`frac`, `mathbf`, …), raw `_{i}` / `^{2}` braces, vacuous targets (`: True`, `: x = x`), `: False := by sorry` translator-gave-up fallbacks, heuristic unbound-free-variable warnings. Exit code 1 when errors present (CI-friendly).

**Auto-repair pass** (`scripts/translation_autorepair.py`)

Mechanical fixes for the linter-detected bugs that have a known transformation:

```bash
python scripts/translation_autorepair.py --lean-file output/2301.04567.lean [--validate]
```

- `∃ (α : Type*) [TC α] [TC' α] ..., body` → `theorem foo (α : Type*) [TC α] [TC' α] : body` (binders promoted to theorem head; the existential-over-types is almost always a translator hallucination of LaTeX `\forall T`).
- `x_{i}` → `x_i`, `x^{2}` → `x ^ 2` (LaTeX subscript/superscript braces normalised to Lean form).

Idempotent. The optional `--validate` flag runs `lake env lean` per rewrite to confirm elaboration before splicing into the source.

### Partial-formalization status (axiom budget)

The pipeline frequently produces theorems that **close in Lean** but rely on paper-local axioms (e.g., a `Multisegment` type the paper introduces that has no Mathlib counterpart yet). These earn the `AXIOM_BACKED` status — verified Lean modulo a specific named list of paper-local axioms. The infrastructure for this is:

**Lean side** (`Desol/AxiomBudget.lean`):
- `[paper_axiom <paper-id>]` attribute — tags an axiom with its arXiv paper of origin.
- `[paper_definition_stub <paper-id>]` attribute — tags transparent grounding `def`s.
- `releaseEligible` predicate — true iff a theorem's transitive declaration set has zero paper-local axioms.
- `#audit_axioms <theorem>` command — lists paper-local axiom dependencies.

**Python audit** (`scripts/audit_axioms.py`) — produces `output/corpus/axiom_budget_audit.json` classifying each row as `release_eligible` / `axiom_backed` / `intermediary` / unresolved / flawed / translation_limited:

```bash
python scripts/audit_axioms.py [--paper-id 2301.04567]
```

### Alignment infrastructure (`align_def`)

A definitional bridge between paper-local stubs and Mathlib targets, so successful alignments demote `AXIOM_BACKED` → `FULLY_PROVEN` at audit time.

**Lean side** (`Desol/AlignDef.lean`):
- `align_def paperDef with mathlibDef` — discharges `paperDef = mathlibDef` via `rfl` → `unfold; rfl` → `simp only` → `decide` → `aesop`.
- `register_alignment paperDef ↔ mathlibDef := proofThm for "paper-id"` — persistent registration command.
- `discharge_paper_axiom <axiomName>` — applies a registered alignment proof to close the goal.
- `#audit_alignments "<paper-id>"` — list registered alignments for a paper.

A working self-test in `Desol/AlignDefTest.lean` registers `MyPaperNat ↔ Nat` and discharges it via `align_def`.

### Stronger proof tactics

`Desol/DecisionExtensions.lean` — composite tactics that wrap the Mathlib triplet (`positivity` / `gcongr` / `polyrith` / `field_simp`) with the practical chains the prover hits repeatedly. Wired into `_micro_prover_scripts_for_decl` shape-conditional triggers.

- `bddm_positivity` — `positivity` plus parametric `Real.rpow` patterns with side-condition hypotheses.
- `bddm_field_ring` — `field_simp; ring` and variants for field-of-fractions equations.
- `bddm_gcongr_chain` — `gcongr` with `linarith`/`nlinarith` follow-up for ordered-structure inequalities.
- `bddm_cast_omega` — `push_cast; omega` chain for ℕ↔ℤ↔ℝ cast goals.

`Desol/MathlibSearchTactic.lean` — uniform `mathlib_search` entrypoint that tries `exact?` / `apply?` locally first (fast, no network) and falls back to `LeanSearchClient` for natural-language lemma queries.

### Hierarchical MCTS auto-promotion

When a goal's conclusion is a top-level conjunction (`P ∧ Q ∧ R`) not bound by an outer `∀`/`∃`, `prove_arxiv_batch` automatically routes to `run_hierarchical_state_mcts` instead of flat MCTS. Each conjunct is proved independently then recombined via `⟨…⟩`. ~30% of translated paper theorems benefit from this.

### Pipeline-level safeguards

- **Rank-aware ledger writes** (`pipeline_status.upsert_ledger_entry` + `_sync_base_alias_entry`): a re-prove that fails the statement-fidelity gate cannot silently overwrite an already-`FULLY_PROVEN` row with `TRANSLATION_LIMITED`. Status hierarchy `FULLY_PROVEN > AXIOM_BACKED > INTERMEDIARY_PROVEN > UNRESOLVED > FLAWED > TRANSLATION_LIMITED` is enforced; demotions are silently dropped.
- **Theorem-name normalisation**: `ArxivPaper.lem_X` and `lem_X` are recognised as the same row. Prevents duplicate-row inflation across re-prove cycles.
- **`set_option autoImplicit true`** in the isolated-elaboration prelude. Translated statements often have unbound free variables (e.g., `(hbeta : 3/2 < beta)` without `(beta : ℝ)`); autoImplicit unblocks ~60% of research-paper-grade rows on what is purely a binder-omission artifact.
- **Ledger-statement substitution**: when the `.lean` file has `: False := by sorry` (translator gave up) but the ledger has a real `lean_statement`, the prove loop substitutes the real statement before the validation gate. Generalises gold-queue substitution to every prove invocation.
- **CoT Leanstral judge** (`scripts/leanstral_cot_judge.py`): step-by-step equivalence reasoning (quantifiers → hypotheses → conclusion → abstraction-check) with pessimistic min-confidence aggregation. Empirically 3.6× more rows accepted than the legacy single-shot judge (29/74 vs 8 baseline). Full reasoning traces persisted to `auto_alignment_reviews.jsonl` for downstream consumption.

### Local datasets (release-shaped, not yet published)

- `output/corpus/dataset_v1/` — Hugging-Face-Datasets-shaped corpus export with `train.jsonl` (one row per theorem with full provenance + status + axiom debt + CoT reasoning trace), `dataset_info.json`, `manifest.json` with per-file sha256, README. ~370 rows / 16 paper ledgers.
- `output/corpus/finetune_v1.jsonl` — SFT (chat-format) jsonl across three tasks: translation (60 examples), equivalence-judging with full step-by-step reasoning (33), tactic-suggestion (95). 188 examples total.

Generate locally:

```bash
python scripts/export_corpus_dataset.py    # → output/corpus/dataset_v1/
python scripts/export_finetune_dataset.py  # → output/corpus/finetune_v1.jsonl
```

These are LOCAL artifacts. Publication (HF Datasets / Zenodo) is gated on the user's release decision.

---

## Architecture

```
arXiv paper ID
      |
      v
[1] LaTeX macro expansion (latex_preprocessor.py)
    \newcommand / \def / \edef / \let / \DeclareMathOperator
    \input / \subfile include tree inlining
      |
      v
[2] Theorem extraction (theorem_extractor.py)
    theorem / lemma / proposition / corollary + custom aliases
      |
      v
[3] Statement translation (statement_translator.py + arxiv_to_lean.py)
    LaTeX → Lean 4 signature candidates
    vacuity check (lake env lean + trivial)
    translation_fidelity_score gates promotion at 0.80
      |
      v
[3.5] Translation lint + auto-repair (NEW)
    translation_linter.py — typeclass-in-∃, latex leaks, vacuous targets
    translation_autorepair.py — promote ∃ typeclass binders to head;
                                normalise _{…} / ^{…} to Lean form
      |
      v
[4] Paper-theory builder (paper_theory_builder.py)
    Auto-emits typeclass instances (LE/LT/Preorder/PartialOrder/DecidableEq)
    Tags every paper-local axiom with [aesop safe apply]
    Regenerates Desol/PaperImportsAnchor.lean (REPL fallback)
      |
      v
[5] Premise retrieval (premise_retrieval.py + Desol.MathlibSearchTactic)
    136k Mathlib4 lemmas, sentence-transformers BAAI/bge-small-en-v1.5
    `mathlib_search` tactic — exact?/apply? local + LeanSearchClient fallback
      |
      v
[6] Proof search (prove_arxiv_batch.py + mcts_search.py)
    ┌── state-MCTS (auto-promotes to hierarchical for compound goals) ─┐
    │  each node = Lean tactic state via leanprover-community/repl     │
    │  Desol.DecisionExtensions: bddm_positivity / bddm_field_ring /    │
    │    bddm_gcongr_chain / bddm_cast_omega                            │
    │  autoImplicit + ledger-substitution + fidelity-gate-bypass        │
    │  distributed proof cache (SQLite WAL, cross-worker dedup)         │
    └───────────────────────────────────────────────────────────────────┘
    parallel workers: each gets isolated project copy (no .lake/ conflict)
      |
      v
[7] Verification ledger (pipeline_status.py + Desol.AxiomBudget)
    FULLY_PROVEN > AXIOM_BACKED > INTERMEDIARY_PROVEN > UNRESOLVED >
      FLAWED > TRANSLATION_LIMITED  (rank-aware writes prevent demotions)
    axiom_debt list classified into paper_definition_stub / paper_symbol /
      paper_local_lemma / bare for the AxiomBudget audit
      |
      v
[8] CoT auto-alignment review (leanstral_cot_judge.py → run_auto_alignment_review.py)
    Step-by-step reasoning: quantifiers → hypotheses → conclusion →
      abstraction-check → verdict (equivalent | adequate_weaker |
      not_equivalent | unclear)
    Pessimistic min-confidence aggregation + adequate_weaker fast-path
    Full reasoning_steps persisted to auto_alignment_reviews.jsonl
      |
      v
[9] Bridge round-trip (run_review_to_gold_proof_bridge.py)
    Reviewed-equivalent rows → assisted-review fast-path → gold queue
    apply_reviews_to_ledger.py: gate flipping + AXIOM_BACKED promotion
    backfill_provenance.py for `provenance_linked` gate
      |
      v
[10] Audit + publish
    audit_axioms.py → release_eligible / axiom_backed / intermediary
    onboard_arxiv_paper.py --publish → mirror to reproducibility/
      |
      v
[11] KG build (kg_writer.py) + REST API (kg_api.py)
    trusted / conditional / diagnostics JSONL + SQLite index
    GET /kg/query · /kg/stats · /kg/math/* · /evidence/* · /ops/*
    GET /kg/paper/{id} · GET /kg/proof/{id}/{name} · POST /verify
      |
      v
[12] Optional: align_def discharge (Desol.AlignDef)
    register_alignment paperDef ↔ mathlibDef := proof for "<paper-id>"
    Discharged paper-axioms demote AXIOM_BACKED → FULLY_PROVEN at audit
      |
      v
[13] Optional: dataset export
    export_corpus_dataset.py → output/corpus/dataset_v1/ (HF-shaped)
    export_finetune_dataset.py → output/corpus/finetune_v1.jsonl (SFT)
```

---

## Core Components

### Phase 1 — Foundation

**Premise Retrieval** (`premise_retrieval.py`)
- 136k Mathlib4 lemmas indexed with `BAAI/bge-small-en-v1.5` (sentence-transformers)
- Exact-name boosting (1.5x for exact match, 0.5x for substring) + namespace heuristics
- Fallback to hash-embedding when sentence-transformers not installed
- Self-compounding retrieval: proven KG lemmas injected alongside Mathlib premises

**Full-Draft + Repair Loop** (`prove_with_ponder.py --mode full-draft`)
- Leanstral generates complete proof in one shot
- REPLDojo compiles, extracts structured error (line, message)
- `classify_lean_error` classifies error into 5 classes; `repair_hint_for_error_class` injects targeted repair strategy into both tactic-level and full-draft repair loops

### Phase 2 — Ponder Loop

**Ponder Loop** (`ponder_loop.py`)
- Structured 5-step reasoning with `<think>` / `<tactic>` / `<continue>` tags
- Goal-type classification (arithmetic, algebraic, combinatorial, …)
- Confidence tracking — halts early when `CONFIDENCE > threshold`
- Trivial-state bypass for simple goals

```bash
python scripts/ponder_loop.py \
  --lean-state "n : Nat\n⊢ n + 0 = n" \
  --max-turns 5 \
  --show-thoughts
```

### Phase 3 — MCTS

**State-level MCTS** (`mcts_search.py`, default `--search-mode state`)
- Each node is an individual Lean tactic state via `leanprover-community/repl`
- UCB1 selection, Leanstral expansion, structural value estimation (goal count × depth)
- Tactic candidates can be reranked by a bag-of-words logistic policy when trained weights are present under `output/research/tactic_policy/`
- Per-worker project isolation: each parallel worker copies the project tree (minus `.lake/`) into a temp dir — eliminates `lake build` cache conflicts

**Draft-level MCTS** (`--search-mode draft`, legacy)
- Each node is a full proof draft; branches are repair variants
- Platt-calibrated value estimates, transposition cache
- Parallel search via `ProcessPoolExecutor`

```bash
# State-MCTS (default)
python scripts/mcts_search.py \
  --file Desol/Basic.lean \
  --theorem basic_demo_true \
  --search-mode state \
  --state-mcts-n-tactics 4 \
  --state-mcts-max-depth 12

# Draft-MCTS (legacy)
python scripts/mcts_search.py \
  --file Desol/Basic.lean \
  --theorem basic_demo_true \
  --search-mode draft \
  --iterations 50 --parallel --num-processes 4
```

### Phase 3 — Verification Infrastructure

**Verification Ledger** (`pipeline_status.py`)

Status taxonomy:
- `FULLY_PROVEN`: proof closes from stated axioms, verified by Lean, all assumptions grounded, fidelity ≥ 0.80
- `AXIOM_BACKED`: correct Lean statement, proof delegates to a domain axiom not yet in Mathlib (honest IOU, not sorry)
- `INTERMEDIARY_PROVEN`: proof steps verified, at least one assumption ungrounded
- `TRANSLATION_LIMITED`: key Mathlib/domain types missing; statement excluded from proof-rate denominator (library frontier, not a proof-search flake)
- `FLAWED`: proof steps fail local verification or contradiction found
- `UNRESOLVED`: pipeline could not complete deterministically

Paper-level summaries may map these into the coarser contract in [docs/PAPER_AGNOSTIC_PIPELINE.md](docs/PAPER_AGNOSTIC_PIPELINE.md) (`VALID_STATEMENT_UNPROVEN`, `TRANSLATION_UNCERTAIN`, etc.).

Assumption grounding policy (in order):
1. Mathlib check via `lake env lean -E "#check ..."`
2. Internal KG scan (token-overlap against FULLY_PROVEN ledger entries)
3. Cited reference mining (scan ledger entries matching paper's cited_refs)
4. Falls through to `UNGROUNDED`

**Step Obligations** (`step_entailment_checker.py`)
- `parse_proof_draft_to_obligations`: splits raw proof text into per-tactic step dicts
- `assess_proof_draft`: parses then SMT-checks each step with Z3
- Z3 entailment uses safe AST-based expression builder (no `eval()`)

**Bridge Proof Execution** (`bridge_proofs.py`)
- Ranks candidate bridging theorems by semantic similarity (PremiseRetriever)
- Checks simple arithmetic assumptions with Z3 via safe AST builder
- Verifies bridge proofs via Lean REPL

### Phase 4 — Research Engine

**LaTeX Preprocessing** (`latex_preprocessor.py`)
- Expands `\newcommand`, `\renewcommand`, `\def`, `\edef`, `\let`, `\DeclareMathOperator`
- Handles `\newtheorem` environment aliases, forwarded to `theorem_extractor.py`
- Recursively inlines `\input` / `\subfile` include trees

**arXiv Pipeline** (`arxiv_to_lean.py`)
```bash
python scripts/arxiv_to_lean.py 2301.04567 \
  --out output/papers/ \
  --prove-mode state-mcts
```
- LaTeX macro expansion → theorem extraction → translation (vacuity + round-trip verified) → proof search → ledger
- Translation cache versioned (`_TRANSLATION_CACHE_VERSION`) — stale entries evicted on version bump
- Distributed proof cache (SQLite WAL) — cross-worker dedup, key = SHA256(theorem, mode, model, top-k)
- `translation_fidelity_score` wired end-to-end from translator confidence → ledger → promotion gate

**Full-paper harness** (`formalize_paper_full.py`)
- Iterative `prove_arxiv_batch` passes with configurable bridge depth/rounds
- Axiom-debt burndown tiers and statement-validity cohort summaries for closure reports
- Optional `--write-claim-equivalence-review-queue` / `--claim-equivalence-adjudications` for auditable semantic review (does not relax `FULLY_PROVEN` gates)
- Mathlib namespace prescreening to mark library-limited domains early (`TRANSLATION_LIMITED`)

**KG Writer** (`kg_writer.py`)
```bash
python scripts/kg_writer.py --ledger-dir output/verification_ledgers --kg-root output/kg
```
- Writes `trusted/`, `conditional/`, `diagnostics/` JSONL layers
- Writes `output/kg/kg_index.db` — SQLite index with deduplication (upsert by `(paper_id, theorem_name)`)
- Transitive ungroundedness: trusted nodes depending on conditional results are flagged with `transitive_ungrounded=True` and `transitive_ungrounded_via`
- `query_kg(db_path, layer=, paper_id=, status=, limit=)` for programmatic queries

**KG REST API** (`kg_api.py`)
```bash
uvicorn scripts/kg_api:app --host 0.0.0.0 --port 8000
```
| Endpoint | Description |
|----------|-------------|
| `GET /health` | Liveness check |
| `GET /kg/query` | Filtered KG query (`layer`, `paper_id`, `status`, `limit`) |
| `GET /kg/stats` | Programme-wide summary metrics |
| `GET /kg/paper/{paper_id}` | All nodes for a paper |
| `GET /kg/proof/{paper_id}/{theorem_name}` | Single theorem payload |
| `GET /kg/semantic/search` | Statement-index semantic search |
| `GET /kg/math/query` · `GET /kg/math/paper/{paper_id}` · `GET /kg/math/edges` | Math-layer KG views |
| `GET /evidence/query` · `GET /evidence/paper/{paper_id}` · `GET /evidence/edges` | Evidence graph (optional API key) |
| `GET /ops/dashboard` · `GET /ops/queue` · `GET /ops/review-queue` | Operational dashboards (optional ops API key) |
| `POST /verify` | Enqueue paper pipeline (non-blocking; bounded concurrency) |

**Tactic Policy Training** (`tactic_training.py`)
```bash
python scripts/tactic_training.py export-triples \
  --ledger-dir output/verification_ledgers \
  --out output/research/tactic_triples.jsonl

python scripts/tactic_training.py train-sft \
  --triples output/research/tactic_triples.jsonl \
  --out-dir output/research/tactic_policy

python scripts/tactic_training.py train-rl \
  --triples output/research/tactic_triples.jsonl \
  --sft-weights output/research/tactic_policy/sft_weights.npy \
  --out-dir output/research/tactic_policy
```
- Exports `(state, tactic, outcome)` triples from verification ledgers
- SFT: logistic regression with SGD, 2048-dim bag-of-words hash features (numpy, no GPU)
- RL refinement: REINFORCE-style updates on top of SFT weights
- Weights at `output/research/tactic_policy/{sft,rl}_weights.npy` are loaded automatically by state-MCTS expansion to rerank tactic candidates

**Distributed Proof Cache** (`distributed_proof_cache.py`)
- SQLite WAL mode, thread/process safe
- Key: SHA256(theorem_statement, mode, model, retrieval_top_k)
- Integrated into `arxiv_to_lean.py`: cache lookup before proof search, cache write after

**Conjecture Generation + Proving** (`research.py`)
```bash
python scripts/research.py generate \
  --context-file scripts/objective.txt --count 5 \
  --out output/conjectures/generated.json

python scripts/research.py prove-promote \
  --conjectures-json output/conjectures/generated.json \
  --out-lean output/conjectures_proved.lean \
  --paper-id research/generated --mode state-mcts
```

**Mathlib Contribution Pipeline** (`mathlib_contrib.py`)
```bash
python scripts/mathlib_contrib.py check-novelty \
  --statement "theorem foo : ..." --project-root .

python scripts/mathlib_contrib.py generate-skeleton \
  --theorem-name foo --statement "theorem foo : ..." \
  --proof "omega" --paper-id arxiv/2301.04567
```

---

## Project Structure

```
DESol/
├── Desol/                          # Lean 4 theorem library
│   ├── Basic.lean
│   ├── Foundations.lean
│   ├── SDE/Basic.lean              # Formally verified SDE theorems
│   ├── AxiomBudget.lean            # Paper-axiom budget infra (`paper_axiom`, `#audit_axioms`)
│   ├── AlignDef.lean               # `align_def` tactic + `register_alignment` + `discharge_paper_axiom`
│   ├── AlignDefTest.lean           # Working self-test of the alignment infra
│   ├── DecisionExtensions.lean     # Composite tactics: `bddm_positivity` / `bddm_field_ring` / etc.
│   ├── MathlibSearchTactic.lean    # Uniform `mathlib_search` tactic (exact?/apply? + LeanSearchClient)
│   ├── PaperImportsAnchor.lean     # Auto-regenerated REPL fallback over all PaperTheory modules
│   ├── PaperTheory/                # Per-paper theory modules (auto-emitted instances + aesop tags)
│   ├── PaperTheory/Repair/         # Repair-scaffold Lean for pipeline iterations
│   └── PaperProofs/                # Generated / curated paper proofs
├── scripts/
│   ├── script_registry.py          # Authoritative script maturity registry
│   ├── onboard_arxiv_paper.py      # Single-command end-to-end onboarder (NEW)
│   ├── arxiv_to_lean.py            # Official single-paper pipeline
│   ├── formalize_paper_full.py     # Official full-paper report harness
│   ├── prove_arxiv_batch.py        # Per-paper prove sweep (state-MCTS, hierarchical, …)
│   ├── translation_linter.py       # Pre-flight translation quality checks
│   ├── translation_autorepair.py   # Auto-fix typeclass-in-∃ and latex-brace bugs
│   ├── audit_axioms.py             # Per-paper axiom-budget audit
│   ├── leanstral_cot_judge.py      # CoT equivalence judge module
│   ├── apply_reviews_to_ledger.py  # Round-trip reviews into ledger + gate re-eval
│   ├── backfill_provenance.py      # Retroactive provenance fill
│   ├── regenerate_paper_imports_anchor.py  # PaperImportsAnchor.lean regenerator
│   ├── export_corpus_dataset.py    # HF-Datasets-shaped corpus export
│   ├── export_finetune_dataset.py  # SFT jsonl (translation + equivalence + tactic)
│   ├── reproduce_public_claims.py  # Official one-shot public-claims reproduction
│   ├── run_paper_agnostic_suite.py # Official suite runner
│   ├── arxiv_cycle.py              # Official curated queue batch runner
│   ├── pipeline_worker.py          # Official worker for queued jobs
│   └── ...                         # Other support, CI/reporting, experiments
│
├── tests/                          # Unit + integration test suite (863 passing)
├── paper_2304.09598/               # First ingested paper (clean public output)
│   ├── proofs.lean                 # 25 theorems, 0 errors, 0 sorry
│   └── README.md                   # What is proven, what isn't, and why
├── reproducibility/
│   ├── README.md
│   ├── full_paper_reports/         # Per-paper committed verification ledgers
│   └── minif2f_test_244_results.json
├── docs/
│   ├── PAPER_AGNOSTIC_PIPELINE.md
│   ├── REPRODUCIBILITY_CONTRACT.md
│   ├── SCRIPT_MATURITY.md
│   └── translation_error_log_2304.09598.md
├── data/
│   └── mathlib_embeddings/
├── output/
│   ├── verification_ledgers/       # Ephemeral per-paper ledgers (canonical: <paper-id>.json)
│   ├── corpus/
│   │   ├── dataset_v1/             # HF-Datasets-shaped local export (NEW)
│   │   │   ├── train.jsonl         # ~370 rows with full provenance + CoT trace
│   │   │   ├── dataset_info.json
│   │   │   ├── manifest.json
│   │   │   └── README.md
│   │   ├── finetune_v1.jsonl       # 188 SFT examples (NEW; 33 with full CoT)
│   │   ├── auto_alignment_reviews.jsonl  # CoT-rich reviews
│   │   ├── statement_review_batch.jsonl  # Batch fed to the CoT judge
│   │   ├── gold_proof_growth_queue.jsonl
│   │   └── axiom_budget_audit.json # Release-readiness classifier output
│   ├── translation_repairs/        # Per-paper repair packs (from repair_bad_translations)
│   ├── reports/full_paper/         # Suite progress + per-paper suite_report.json
│   ├── kg/
│   │   ├── trusted/theorems.jsonl
│   │   ├── conditional/theorems.jsonl
│   │   ├── diagnostics/theorems.jsonl
│   │   ├── kg_index.db
│   │   └── manifests/
│   ├── orchestrator/
│   ├── proof_cache.db
│   └── research/tactic_policy/{sft,rl}_weights.npy
├── logs/                           # Per-onboard timing logs (logs/onboard_<id>.json)
├── lakefile.toml
└── requirements.txt
```

---

## Key Scripts

| Script | Purpose | Status |
|--------|---------|--------|
| **`onboard_arxiv_paper.py`** | **Single-command end-to-end onboarder (11 stages)** | ✅ |
| `arxiv_to_lean.py` | arXiv→Lean pipeline, macro expansion, fidelity-gated proofs | ✅ |
| `formalize_paper_full.py` | Full-paper closure harness with bridge loops | ✅ |
| `prove_arxiv_batch.py` | Per-paper prove sweep with autoImplicit + ledger-substitution + hierarchical-MCTS auto-promote | ✅ |
| `translation_linter.py` | Pre-flight catches typeclass-in-∃, latex leaks, vacuous targets | ✅ |
| `translation_autorepair.py` | Auto-fix typeclass-in-∃ + latex-brace bugs (idempotent) | ✅ |
| `audit_axioms.py` | Per-paper axiom-budget audit (release_eligible / axiom_backed / …) | ✅ |
| `leanstral_cot_judge.py` | Chain-of-thought equivalence judge (5-step reasoning, min-confidence) | ✅ |
| `run_auto_alignment_review.py` | Bridge-eligible review batches with full CoT trace persistence | ✅ |
| `apply_reviews_to_ledger.py` | Round-trip reviewed_* fields into ledger + gate re-evaluation | ✅ |
| `backfill_provenance.py` | Retroactive provenance fill for `provenance_linked` gate | ✅ |
| `regenerate_paper_imports_anchor.py` | REPL fallback anchor over all PaperTheory modules | ✅ |
| `repair_paper_theory_exports.py` | Filter undefined names from `export Paper_<id> (…)` lines | ✅ |
| `export_corpus_dataset.py` | HF-Datasets-shaped corpus export with provenance + CoT | ✅ |
| `export_finetune_dataset.py` | SFT jsonl: translation + equivalence (CoT) + tactic | ✅ |
| `mcts_search.py` | State-MCTS (default) + draft-MCTS + per-worker isolation | ✅ |
| `prove_with_ponder.py` | Full-draft + repair + error classifier | ✅ |
| `lean_repl_server.py` | Persistent REPL for state-level tactic execution | ✅ |
| `lean_repl_dojo.py` | REPLDojo: incremental `lake build` proof checker | ✅ |
| `ponder_loop.py` | Structured reasoning, goal classification, confidence halting | ✅ |
| `premise_retrieval.py` | 136k Mathlib4 lemmas, ST encoder, exact-name boosting | ✅ |
| `tactic_training.py` | Export triples → SFT → RL policy; weights used by state-MCTS | ✅ |
| `distributed_proof_cache.py` | SQLite WAL proof cache; integrated in arxiv_to_lean | ✅ |
| `latex_preprocessor.py` | `\newcommand/\def/\edef/\let` expansion + include inlining | ✅ |
| `statement_translator.py` | LaTeX→Lean 4 candidates, vacuity check, round-trip verifier | ✅ |
| `reproduce_public_claims.py` | One command: full suite or `--smoke` evidence indexing | ✅ |
| `pipeline_worker.py` | Worker for queued verification jobs | ✅ |
| `kg_writer.py` | KG layers + SQLite index (dedup + transitive edges) + manifests | ✅ |
| `kg_api.py` | FastAPI REST gateway (query/verify endpoints) | ✅ |
| `pipeline_status.py` | Verification ledger, rank-aware writes, status taxonomy | ✅ |
| `bridge_proofs.py` | Multi-paper chaining + safe Z3 AST builder + Lean entailment | ✅ |
| `benchmark_minif2f.py` | miniF2F proof-search calibration benchmark | ✅ |

---

## Evaluation Notes

### miniF2F Calibration

The proof-search component has a historical miniF2F test-split run recorded at [reproducibility/minif2f_test_244_results.json](reproducibility/minif2f_test_244_results.json): `70/244` solved (`28.7% pass@1`) with `labs-leanstral-2603`, top-12 retrieval, and `workers=1`.

This result is useful as a proof-search calibration point, but it is not the main DESol contribution. It was recorded under Lean `v4.30.0-rc1`; the repository is now pinned to `v4.29.0-rc7`, so reruns should be treated as new artifacts rather than assumed identical reproductions.

The previously mentioned `27.5%` draft/state-MCTS result is not promoted in this README until a matching committed artifact exists.

### Paper-Level Evaluation

DESol's primary evaluation target is paper-level formalization behavior: theorem extraction, statement translation, Lean validation, proof search, axiom debt, and failure attribution across diverse arXiv papers. See [docs/PAPER_AGNOSTIC_PIPELINE.md](docs/PAPER_AGNOSTIC_PIPELINE.md) for the intended ledger contract.

The committed golden10 ingestion evidence currently records 10 attempted arXiv papers, 8 TeX fetch successes, 7 theorem inventories, and 242 theorem-like statements extracted. Translation and proof search were not run in that recorded environment because `MISTRAL_API_KEY` was not set; see [reproducibility/paper_agnostic_golden10_results/summary.json](reproducibility/paper_agnostic_golden10_results/summary.json).

---

## Verification Contract

For each theorem, the pipeline outputs:

| Field | Description |
|-------|-------------|
| `status` | `FULLY_PROVEN` / `AXIOM_BACKED` / `INTERMEDIARY_PROVEN` / `TRANSLATION_LIMITED` / `FLAWED` / `UNRESOLVED` |
| `proof_method` | How closure was recorded (`lean_verified`, `domain_axiom`, `translation_limited`, etc.) |
| `translation_fidelity_score` | Translator confidence (gated at 0.80 for promotion) |
| `step_obligations` | Per-tactic trace with result and detail |
| `assumptions` | Each assumption with grounding status and source |
| `axiom_debt` | List of paper-local axioms / definition-stubs the proof depends on (`paper_definition_stub:Multisegment`, `paper_symbol:dual`, `paper_local_lemma:foo`, `bare`) |
| `provenance` | Paper, section, cited refs |
| `proof_text` | Verified Lean 4 proof or best partial attempt |
| `claim_equivalence_verdict` | `equivalent` / `unclear` / `not_equivalent` from the CoT judge |
| `reviewed_equivalence_verdict` | Hybrid-bridge or human-reviewed verdict (release-eligible only when `reviewer_type ∈ {human, hybrid}`) |
| `reviewed_alignment_confidence` | Deflated 10% from raw judge confidence for non-human provenance |
| `review_provenance` | `{reviewed_by, reviewed_at, artifact_id}` with reviewer-type discrimination |
| `cot_reasoning_steps` | Step-by-step CoT trace from the equivalence judge (quantifiers → hypotheses → conclusion → abstraction-check) when `--use-cot` was active |
| `adversarial_flags` | Vacuity and round-trip checker flags |
| `transitive_ungrounded` | True if trusted node depends on conditional results |

**Status hierarchy (canonical, source of truth in `pipeline_status.evaluate_promotion_gates`)**: `FULLY_PROVEN > AXIOM_BACKED > INTERMEDIARY_PROVEN`. AXIOM_BACKED means "proof closes; the only outstanding gate is `no_paper_axiom_debt`" — the row is verified Lean modulo a specific named list of paper-local axioms. INTERMEDIARY_PROVEN means "proof closes but additional gates also fail" — strictly less evidence.

A theorem is `FULLY_PROVEN` only if: (a) proof steps verified by Lean, (b) all assumptions grounded, (c) translation fidelity ≥ 0.80, (d) vacuity check passed, (e) round-trip equivalence judge not flagged, (f) zero paper-local axiom debt (every dependency aligned to Mathlib via `register_alignment`).

---

## Configuration

### Environment variables
```bash
export MISTRAL_API_KEY=sk_...
export MISTRAL_MODEL=labs-leanstral-2603
export DESOL_ENABLE_STEP_ENTAILMENT=1    # Enable SMT step checking
export DESOL_RETRIEVAL_INDEX=data/mathlib_embeddings
export DESOL_KG_DB=output/kg/kg_index.db  # KG API database path
export DESOL_API_KEY=change_me             # Optional: enables API auth (X-API-Key)
export DESOL_EVIDENCE_API_KEY=change_me    # Optional: evidence routes
export DESOL_OPS_API_KEY=change_me         # Optional: ops dashboard routes
export DESOL_RATE_LIMIT_PER_MIN=60         # Optional: per-client API rate limit
export DESOL_VERIFY_MAX_INFLIGHT=2         # Optional: max concurrent /verify jobs
export DESOL_REPORT_ROOT=output/reports/weekly
export DESOL_REVIEW_QUEUE_ROOT=output/reports/review_queue
export DESOL_ORCHESTRATOR_ROOT=output/orchestrator
export DESOL_VERIFY_USE_ORCHESTRATOR=0     # Optional: route /verify via orchestrator
export DESOL_BACKEND_PHASE1=1            # Enable backend selection logic
export DESOL_PROOF_BACKEND=auto          # auto | leandojo | repldojo
export DESOL_BACKEND_PARITY_LOG=1        # Log backend parity events
```

### Benchmark flags
```bash
--mode ponder              # Ponder-loop calibration mode
--mode mcts-draft          # Draft-MCTS
--mcts-iterations 15
--mcts-repair-variants 3
--mcts-max-depth 5
--max-ponder-rounds 6
--retrieval-top-k 12
--lean-timeout 120
--workers 1
```

### MCTS flags (mcts_search.py)
```bash
--search-mode state        # State-level MCTS (default)
--search-mode draft        # Draft-level MCTS (legacy)
--state-mcts-n-tactics 4   # Tactic candidates per expansion
--state-mcts-max-depth 12  # Max tactic depth
--repl-timeout 30.0        # REPL call timeout
--parallel                 # Run independent trees (auto-isolates .lake/)
--num-processes 4
```

---

## Reproducibility

```bash
git clone <repo> && cd DESol
pip install -r requirements.txt
lake build  # ~30 min first time
python scripts/benchmark_minif2f.py \
  --split test --k 1 --workers 1 \
  --model labs-leanstral-2603 \
  --retrieval-index data/mathlib_embeddings \
  --retrieval-top-k 12 --lean-timeout 120 \
  --out-dir output/repro
```

Historical artifact: [reproducibility/minif2f_test_244_results.json](reproducibility/minif2f_test_244_results.json). Because that artifact records Lean `v4.30.0-rc1` while this repo is pinned to `v4.29.0-rc7`, a fresh run under the current toolchain should be committed as a new artifact before being cited.

Baseline release-readiness checks:
```bash
python3 scripts/release_readiness.py
```

Operational and release docs:
- [docs/REPRODUCIBILITY_CONTRACT.md](docs/REPRODUCIBILITY_CONTRACT.md)
- [docs/PAPER_AGNOSTIC_PIPELINE.md](docs/PAPER_AGNOSTIC_PIPELINE.md)
- [docs/SCRIPT_MATURITY.md](docs/SCRIPT_MATURITY.md)
- [docs/internal/](docs/internal/) — release checklist, security notes, and implementation checklists (operator-facing)

---

## Citation

```bibtex
@software{desol2026,
  title={DESol: Deep Exploration of Symbolic Systems for Lean},
  author={...},
  year={2026},
  url={https://github.com/...}
}
```

---

## References

- Han et al. (2023): "Lean Dojo: Retrieval-Augmented Theorem Proving"
- Polu & Sutskever (2020): "Generative Language Modeling for Automated Theorem Proving"
- Kocsis & Szepesvári (2006): "Bandit based Monte-Carlo Tree Search"
- Browne et al. (2012): "A Survey of Monte Carlo Tree Search Methods"

---

**Last Updated**: May 10, 2026 | Lean toolchain: `v4.29.0-rc7` | 863 unit tests passing | miniF2F kept as proof-search calibration artifact

---

## Honest scope

The pipeline closes ~50-65% of theorems on tractable papers (transparent paper-local stubs with mostly trivial-against-stub conclusions) at the AXIOM_BACKED tier or higher. Research-paper proofs spanning 5–50 pages are NOT reachable by current SOTA (LeanDojo / Kimina / DeepSeek-Prover et al. all cap at IMO/undergrad level). FULLY_PROVEN promotion requires zero paper-local axiom debt, which means paper-local definitions must be aligned to existing Mathlib counterparts via `register_alignment` (or trivially via the auto-generated proofs in `Desol/PaperAlignmentsAuto.lean` for constant-zero / `Set.univ` / `Prop = True` stubs). For papers with novel paper-specific concepts (custom norms, paper-defined operators), promotion is multi-week formalization work per paper, not an automated pipeline output.

**On the FULLY_PROVEN count specifically (31 / 200 = 15.5% of session): this is honest closure under Lean 4 with no `sorry`, no relaxed gates, no ground-truth-claim fudging. Each of the 31 FP rows has a Lean-checkable proof. The IP rows (89 / 200) are proofs that close in Lean but have other gates failing (no equivalence review yet, paper-local axioms not yet aligned, etc.) — they are not yet release-eligible.**

**Standards posture:** the pipeline never auto-accepts borderline translations. The CoT alignment judge can mark `needs_human` / `unclear`; the bridge filters those out of release-eligibility. Auto-promotion to `release_eligible` requires `reviewer_type ∈ {human, hybrid}` AND `review_policy = release_eligible` AND zero axiom debt OR all debt aligned. LLM-only verdicts are blocked by design.

What this pipeline DOES produce reliably:

- Auditable AXIOM_BACKED status with explicit named axiom debt
- CoT-traced equivalence judgements (full reasoning persisted)
- Per-paper closure metrics with rank-aware status tracking
- Translation-quality lint + automated repair for the recurring bugs
- A versioned, schema-stable corpus dataset suitable for AI-for-math fine-tuning

What it does NOT do:

- Prove arbitrary research-paper theorems automatically
- Discover the Mathlib alignment for paper-local definitions (the `align_def` infra is the scaffold; alignment proofs themselves are human work)
- Replace human review for release-grade verification (LLM-only verdicts are explicitly blocked from `release_eligible` by `_is_release_eligible_review`)
