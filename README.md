# DESol — Automated Lean 4 Formalization of arXiv Mathematics

DESol is an evidence-preserving pipeline for turning arXiv LaTeX papers into auditable Lean 4 formalization attempts. Given an arXiv paper ID, it extracts theorem-like statements, translates them into Lean signatures, searches for machine-checked proofs, and writes a verification ledger plus Knowledge Graph (KG) entries that explain what closed, what remained conditional, and why.

**To use it:** install Lean 4 via Elan and Python 3.11+, set `MISTRAL_API_KEY` in `.env`, then run `python scripts/arxiv_to_lean.py <arxiv-id> --out output/papers/`. A verification ledger and KG entry are written when configured. For a full-paper closure run (bootstrap → batch prove/bridge passes → JSON report), use `python scripts/formalize_paper_full.py --paper-id <arxiv-id> --project-root .` and optionally `--report-out output/reports/full_paper/<id>_suite_report.json`. Suite-scale runs default progress under `output/reports/full_paper/` via `run_paper_agnostic_suite.py`. One-command public-claims reproduction (full API runs or CI **smoke** re-index): `python scripts/reproduce_public_claims.py --smoke` or omit `--smoke` for full mode.

To query the KG, start the REST API with `uvicorn scripts/kg_api:app --port 8000` and use `GET /kg/paper/{arxiv-id}`, `GET /kg/stats`, math-layer and evidence routes (see [Query the KG](#query-the-kg-via-rest-api)).

**Main contribution:** a paper-agnostic arXiv-to-Lean workflow that records theorem inventory, translation attempts, Lean validation, proof search traces, axiom debt tiers, claim-equivalence review hooks, and blocker taxonomy for arbitrary LaTeX-source arXiv papers. miniF2F is kept as a calibration benchmark for the proof-search component, not as the headline claim. See [docs/REPRODUCIBILITY_CONTRACT.md](docs/REPRODUCIBILITY_CONTRACT.md), [docs/PAPER_AGNOSTIC_PIPELINE.md](docs/PAPER_AGNOSTIC_PIPELINE.md), and [docs/SCRIPT_MATURITY.md](docs/SCRIPT_MATURITY.md).

**Script trust boundary:** `scripts/` mixes production entrypoints, support modules, and experiments. The **official pipeline** surface (enforced by `tests/test_script_registry.py`) is: `arxiv_to_lean.py`, `formalize_paper_full.py`, `reproduce_public_claims.py`, `run_paper_agnostic_suite.py`, `arxiv_cycle.py`, `arxiv_cycle_daemon.py`, `pipeline_worker.py`. List them anytime with `python scripts/script_registry.py --tier official_pipeline`.

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

```bash
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
[3] Statement translation (statement_translator.py)
    LaTeX → Lean 4 signature candidates
    vacuity check (lake env lean + trivial)
    round-trip verifier (back-translate → LLM equivalence judge)
    translation_fidelity_score gates promotion at 0.80
      |
      v
[4] Premise retrieval (premise_retrieval.py)
    136k Mathlib4 lemmas, sentence-transformers BAAI/bge-small-en-v1.5
    exact-name boosting (1.5×) + namespace heuristics
    self-compounding KG retrieval (proven internal lemmas injected)
      |
      v
[5] Proof search (prove_with_ponder.py / mcts_search.py)
    ┌── state-MCTS (default) ───────────────────────────────────────┐
    │  each node = Lean tactic state via leanprover-community/repl  │
    │  UCB1 selection · tactic policy reranking (sft/rl weights)   │
    │  distributed proof cache (SQLite WAL, cross-worker dedup)     │
    └────────────────────────────────────────────────────────────────┘
    ┌── full-draft + repair ─────────────────────────────────────────┐
    │  Leanstral → REPLDojo → classify_lean_error → repair hint     │
    │  error_class in {name-resolution, type-mismatch,              │
    │    rewrite-mismatch, incomplete-progress, resource-timeout}    │
    └───────────────────────────────────────────────────────────────┘
    parallel workers: each gets isolated project copy (no .lake/ conflict)
      |
      v
[6] Verification ledger (pipeline_status.py, pipeline_status_models.py)
    FULLY_PROVEN / AXIOM_BACKED / INTERMEDIARY_PROVEN / TRANSLATION_LIMITED /
    FLAWED / UNRESOLVED
    proof_method distinguishes lake-verified closure vs domain-axiom IOUs
    assumption grounding: Mathlib → internal KG → cited refs → UNGROUNDED
    translation_fidelity_score gated at 0.80 for promotion
      |
      v
[7] KG build (kg_writer.py)
    trusted / conditional / diagnostics JSONL + SQLite index
    deduplication + transitive ungroundedness propagation
    promotion manifests per paper + all-papers summary
      |
      v
[8] KG API (kg_api.py) — FastAPI REST gateway
    GET /kg/query · /kg/stats · /kg/math/* · /evidence/* · /ops/*
    GET /kg/paper/{id} · GET /kg/proof/{id}/{name} · POST /verify
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
│   ├── PaperTheory/                # Per-paper theory modules (e.g. Paper_2304_09598.lean)
│   ├── PaperTheory/Repair/         # Repair-scaffold Lean for pipeline iterations
│   └── PaperProofs/                # Generated / curated paper proofs (incl. Auto/)
├── scripts/
│   ├── script_registry.py          # Authoritative script maturity registry
│   ├── arxiv_to_lean.py            # Official single-paper pipeline
│   ├── formalize_paper_full.py     # Official full-paper report harness
│   ├── reproduce_public_claims.py  # Official one-shot public-claims reproduction
│   ├── run_paper_agnostic_suite.py # Official suite runner
│   ├── arxiv_cycle.py              # Official curated queue batch runner
│   ├── arxiv_cycle_daemon.py       # Official queue daemon
│   ├── pipeline_worker.py          # Official worker for queued jobs
│   └── ...                         # Support, CI/reporting, benchmarks, experiments
│
├── tests/                          # Unit + integration test suite
├── paper_2304.09598/               # First ingested paper (clean public output)
│   ├── proofs.lean                 # 25 theorems, 0 errors, 0 sorry
│   └── README.md                   # What is proven, what isn't, and why
├── reproducibility/
│   ├── README.md
│   └── minif2f_test_244_results.json
├── docs/
│   └── translation_error_log_2304.09598.md
├── data/
│   └── mathlib_embeddings/
├── output/
│   ├── reports/full_paper/         # Suite progress + per-paper suite_report.json (typical)
│   ├── kg/
│   │   ├── trusted/theorems.jsonl
│   │   ├── conditional/theorems.jsonl
│   │   ├── diagnostics/theorems.jsonl
│   │   ├── kg_index.db             # SQLite index with dedup + edge queries
│   │   └── manifests/
│   ├── orchestrator/               # Optional pipeline orchestrator state (API / verify)
│   ├── proof_cache.db              # Distributed proof result cache
│   └── research/
│       └── tactic_policy/
│           ├── sft_weights.npy     # Optional; produced by tactic_training.py
│           └── rl_weights.npy      # Optional; produced by tactic_training.py
├── lakefile.toml
└── requirements.txt
```

---

## Key Scripts

| Script | Purpose | Status |
|--------|---------|--------|
| `mcts_search.py` | State-MCTS (default) + draft-MCTS + per-worker isolation | ✅ |
| `prove_with_ponder.py` | Full-draft + repair + error classifier wired in both modes | ✅ |
| `lean_repl_server.py` | Persistent REPL for state-level tactic execution | ✅ |
| `lean_repl_dojo.py` | REPLDojo: incremental `lake build` proof checker | ✅ |
| `proof_backend.py` | Backend selection (auto/leandojo/repldojo), startup diagnostics | ✅ |
| `ponder_loop.py` | Structured reasoning, goal classification, confidence halting | ✅ |
| `premise_retrieval.py` | 136k Mathlib4 lemmas, ST encoder, exact-name boosting | ✅ |
| `tactic_training.py` | Export triples → SFT → RL policy; weights used by state-MCTS | ✅ |
| `distributed_proof_cache.py` | SQLite WAL proof cache; integrated in arxiv_to_lean | ✅ |
| `latex_preprocessor.py` | `\newcommand/\def/\edef/\let` expansion + include inlining | ✅ |
| `statement_translator.py` | LaTeX→Lean 4 candidates, vacuity check, round-trip verifier | ✅ |
| `arxiv_to_lean.py` | arXiv→Lean pipeline, macro expansion, fidelity-gated proofs | ✅ |
| `reproduce_public_claims.py` | One command: full suite reproduction or `--smoke` evidence indexing | ✅ |
| `pipeline_worker.py` | Worker for queued verification jobs | ✅ |
| `kg_writer.py` | KG layers + SQLite index (dedup + transitive edges) + manifests | ✅ |
| `kg_api.py` | FastAPI REST gateway (query/verify endpoints) | ✅ |
| `pipeline_status.py` | Verification ledger, status taxonomy, assumption grounding | ✅ |
| `paper_agnostic_report.py` | Paper-level status and blocker summaries from ledgers | ✅ |
| `step_entailment_checker.py` | Proof obligation parser + SMT step checker | ✅ |
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
| `provenance` | Paper, section, cited refs |
| `proof_text` | Verified Lean 4 proof or best partial attempt |
| `adversarial_flags` | Vacuity and round-trip checker flags |
| `transitive_ungrounded` | True if trusted node depends on conditional results |

A theorem is `FULLY_PROVEN` only if: (a) proof steps verified by Lean, (b) all assumptions grounded, (c) translation fidelity ≥ 0.80, (d) vacuity check passed, (e) round-trip equivalence judge not flagged.

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

**Last Updated**: April 27, 2026 | Lean toolchain: `v4.29.0-rc7` | miniF2F kept as proof-search calibration artifact
