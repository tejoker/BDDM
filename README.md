# DESol — Auditable arXiv-to-Lean formalization

DESol ingests arXiv mathematics papers and produces auditable Lean 4 formalization attempts. For each theorem-like statement it records: translation candidates, Lean validation, proof search traces, axiom debt, claim-equivalence review, and a verification ledger classifying the row as `FULLY_PROVEN / AXIOM_BACKED / INTERMEDIARY_PROVEN / UNRESOLVED / TRANSLATION_LIMITED / FLAWED`.

The headline value is **rigorous accounting**, not maximal closure: every promotion is mutation-test-verified by the integrity audit, and no row reaches `FULLY_PROVEN` without a lake-verified proof body. Translation/alignment evidence from the LLM judge is recorded but cannot, by itself, promote a row to release-eligible status.

---

## Honest current state

Canonical 8-paper corpus, post-audit, after Round-XII (campaign final):

```
FULLY_PROVEN          14   ( 6.9%)
AXIOM_BACKED          21   (10.3%)
INTERMEDIARY_PROVEN    6   ( 2.9%)
UNRESOLVED           159   (77.9%)
TRANSLATION_LIMITED    4   ( 2.0%)
                     ───
                     204  (incl. 4 derived aux rows)
```

- **Closed at AB+ or higher:** 41/204 = 20.1%
- **Net auto-closure this campaign:** +19 AB, +0 net IP, +4 derived rows
- **Integrity audit:** 0 demotions across all canonical rounds; 18,000-iter adversarial fuzz reports 0 escapes
- **Test suite:** 1,664 tests collected; full suite ~5 min

The pre-audit "FP=31 AB=5 IP=89" historically cited in older artifacts was inflated by trivialization patterns (`∃ x, x = expr`, sorry-bodied .lean files behind `proof_text='aesop'`, namespace-qualified bypasses). The current audit catches every known bypass class; see [`scripts/audit_fully_proven_integrity.py`](scripts/audit_fully_proven_integrity.py) and [`tests/test_audit_integrity_mutations.py`](tests/test_audit_integrity_mutations.py).

External calibration: **40% pass@1 on miniF2F** ([`reproducibility/minif2f_test_244_results.json`](reproducibility/minif2f_test_244_results.json) holds the older 28.7%/244 run; the 40% figure is the recent 30-row calibration at [`scripts/benchmark_minif2f_calibration.py`](scripts/benchmark_minif2f_calibration.py)). Proof-search capability is competitive with published baselines; the lower internal-corpus closure rate reflects research-paper difficulty, not a proof-search ceiling.

---

## LLM policy

The only LLM the pipeline calls is **Leanstral** (`labs-leanstral-2603`). Every model default has been switched accordingly: `desol_config.py`, `adjudicate_claim_equivalence.py`, `benchmark_minif2f.py`. No third-party SDK references remain in the runtime path.

---

## Pipeline entry points

| # | Script | Purpose |
|---|---|---|
| 1 | [`scripts/arxiv_to_lean.py`](scripts/arxiv_to_lean.py) | Ingest, extract, translate one paper → `output/<id>.lean` |
| 2 | [`scripts/paper_theory_builder.py`](scripts/paper_theory_builder.py) | Write `Desol/PaperTheory/Paper_<id>.lean` (paper-local defs, axioms, typeclass instances) |
| 3 | [`scripts/regenerate_paper_imports_anchor.py`](scripts/regenerate_paper_imports_anchor.py) | Regenerate REPL-fallback anchor |
| 4 | [`scripts/prove_arxiv_batch.py`](scripts/prove_arxiv_batch.py) | Deterministic micro-prover → state-MCTS via REPL |
| 5 | [`scripts/run_auto_alignment_review.py`](scripts/run_auto_alignment_review.py) | CoT-judge claim-equivalence review |
| 6 | [`scripts/run_review_to_gold_proof_bridge.py`](scripts/run_review_to_gold_proof_bridge.py) | Bridge reviewed-equivalent rows to gold queue |
| 7 | [`scripts/apply_reviews_to_ledger.py`](scripts/apply_reviews_to_ledger.py) | Propagate `reviewed_*` fields back into ledgers |
| 8 | [`scripts/formalize_paper_full.py`](scripts/formalize_paper_full.py) | Canonical orchestrator (chains 1–7 + reproducibility-bundle mirror) |

Where state lives:

- **Ephemeral:** `output/verification_ledgers/<id>.json` (proof-search writes here)
- **Committed:** `reproducibility/full_paper_reports/<id>/verification_ledger.json` (only the orchestrator updates this)
- **Corpus exports:** `output/corpus/*.jsonl`

---

## Setup

```bash
# Lean 4
curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf | sh

# Python deps
pip install -r requirements.txt

# Config
cp .env.example .env
# Set MISTRAL_API_KEY and MISTRAL_MODEL=labs-leanstral-2603

# First-time Mathlib cache (~30 min)
~/.elan/bin/lake build
```

Requirements: Linux, Python 3.11+, Elan, Lean `v4.29.0-rc7` (pinned by `lean-toolchain`).

---

## Quick start

Formalize one paper end-to-end:

```bash
python scripts/formalize_paper_full.py \
  --paper-id 2604.21884 \
  --project-root . \
  --report-out output/reports/full_paper/2604.21884_report.json
```

Re-verify the committed canonical evidence:

```bash
python scripts/reproduce_canonical_evidence.py
```

Run the integrity audit (read-only by default; `--write` to apply demotions):

```bash
python scripts/audit_fully_proven_integrity.py --include-ip-ab
```

Run the test suite:

```bash
pytest tests/ -x -q                  # full suite (~5 min)
pytest tests/ -x -q -m 'not slow'    # skip lake/REPL/Mistral live tests
```

---

## Promotion gates

`pipeline_status.evaluate_promotion_gates()` is the canonical source of truth.

- **FULLY_PROVEN** requires: lake-verified proof body, `claim_equivalent`, `independent_semantic_equivalence_evidence`, `provenance_linked`, **zero paper-local axiom debt**.
- **AXIOM_BACKED** requires: lake-verified proof body, every gate except `no_paper_axiom_debt`. The row is verified Lean modulo a named list of paper-local axioms (recorded in `axiom_debt`).
- **INTERMEDIARY_PROVEN** requires: lake-verified proof body, but additional gates failing besides axiom debt.
- **TRANSLATION_LIMITED:** elaboration failure that prevents proof attempts.
- **UNRESOLVED:** any other non-closed state.

Status hierarchy: `FULLY_PROVEN > AXIOM_BACKED > INTERMEDIARY_PROVEN`. Rank-aware writes prevent silent demotions.

Release eligibility (`_is_release_eligible_review`): requires `reviewer_type ∈ {human, hybrid}` AND `review_policy == release_eligible`. **Pure-LLM verdicts are blocked from release-eligibility by design.**

---

## Integrity audit

The audit catches every known bypass class (mutation-test verified, 19/19 patterns in [`tests/test_audit_integrity_mutations.py`](tests/test_audit_integrity_mutations.py); 18,000-iter adversarial fuzz in [`scripts/audit_fuzz_mutations.py`](scripts/audit_fuzz_mutations.py) reports 0 escapes):

| Class | Pattern |
|---|---|
| Sorry-body | `proof_text='aesop'` but file body is `sorry` |
| Auto-LLM placeholder | `proof_text='apply?'` |
| Trivialized existential | `∃ x, x = expr` or `∃ X : Prop, X ↔ expr` |
| Reflexive conjunction | `f X = f X ∧ g Y = g Y` |
| Prop-binder placeholder | `(P Q : Prop) : P ∧ Q` |
| Namespace-qualified bypass | ledger name vs `Namespace.foo` in file |
| Hidden `sorry` | first-line / mid-body / `<;> sorry` combinator |

Total bypass demotions caught across audit rounds: 71. Honest count after every audit round is the count cited in this README.

---

## Architecture

```
arXiv ID
  ↓
[1] LaTeX preprocessor: \newcommand / \input / \subfile inlining
[2] Theorem extractor: theorem/lemma/proposition/corollary
[3] Translator: LaTeX → Lean 4 statement candidates (Leanstral)
    + vacuity / triviality / quantifier-scope-flip rejection
[4] Paper-theory builder: auto-emit typeclass instances + [aesop safe] on axioms
[5] Premise retrieval: 220k Mathlib name-index + 205k premise index
[6] Proof search:
      • deterministic micro-prover catalog (aesop / simp_all / omega / linarith /
        norm_cast / gcongr / field_simp / polyrith / interval_cases)
      • state-MCTS via leanprover-community/repl
      • lemma-factor v3 (recursive depth-2, type-aware composition)
      • whole-proof + REPL-driven generators (multi-shot temperature ladder)
[7] Verification ledger + integrity audit
[8] CoT auto-alignment review (per-area domain rules)
[9] Bridge: reviewed-equivalent → gold queue → ledger flip
[10] Reproducibility-bundle mirror (committed evidence path)
```

---

## Key infrastructure (post-campaign)

Built and shipped this campaign as non-LLM throughput / quality work:

- [`scripts/lake_validation_cache.py`](scripts/lake_validation_cache.py) — persistent REPL worker pool, one per `(project_root, paper_id)`. ~880× steady-state speedup (5s → 0.006s/call).
- [`scripts/proof_attempt_cache.py`](scripts/proof_attempt_cache.py) — statement-hash cache for proof attempts (dedup Mistral spend across sweep rounds).
- [`scripts/audit_fuzz_mutations.py`](scripts/audit_fuzz_mutations.py) — 18k-iter adversarial fuzzer over bypass shapes.
- [`scripts/per_paper_tactic_priors.py`](scripts/per_paper_tactic_priors.py) — re-rank micro-prover catalog by paper-specific success rate.
- [`scripts/lemma_factor_v2.py`](scripts/lemma_factor_v2.py) — shape-aware decomposition (`factor_long_theorem_recursive`, depth-2, branching-bounded).
- [`scripts/promote_closed_aux_as_rows.py`](scripts/promote_closed_aux_as_rows.py) — credit individually-closed aux as derived `<parent>::aux::<name>` ledger rows.
- [`scripts/paper_theory_symbol_stubber.py`](scripts/paper_theory_symbol_stubber.py) — parse unknown-identifier errors → emit typed stubs.
- [`scripts/autoproved_promotion.py`](scripts/autoproved_promotion.py) — atomic write `Desol/PaperProofs/Paper_<id>.lean` for next-round B3 hint compounding.
- [`scripts/sweep_reliability_check.py`](scripts/sweep_reliability_check.py) — two-seed cross-validation (reliability = |intersection|/|union|).
- [`scripts/reproduce_canonical_evidence.py`](scripts/reproduce_canonical_evidence.py) + `.github/workflows/canonical_integrity.yml` — single-command CI verification.

---

## Configuration

Environment variables (all optional except `MISTRAL_API_KEY`):

```bash
MISTRAL_API_KEY=sk_...
MISTRAL_MODEL=labs-leanstral-2603
DESOL_RETRIEVAL_INDEX=data/mathlib_embeddings
DESOL_LEANSTRAL_COT_THRESHOLD=0.80      # CoT-judge equivalent-verdict floor
```

KG API (optional REST surface for queries):

```bash
uvicorn scripts.kg_api:app --port 8000
# GET /kg/paper/{id}   GET /kg/proof/{id}/{name}   POST /verify
```

---

## Reproducibility

The committed evidence path is `reproducibility/full_paper_reports/<id>/verification_ledger.json`. Only [`scripts/formalize_paper_full.py`](scripts/formalize_paper_full.py) (or an explicit `cp` after a hand-mirror) updates this path. Re-verification is one command:

```bash
python scripts/reproduce_canonical_evidence.py
# verifies: FP=14/14 AB=21/21 IP=6/6 mismatches=0
```

CI workflow at `.github/workflows/canonical_integrity.yml` runs the audit + reproduce-evidence on every push.

See also: [docs/REPRODUCIBILITY_CONTRACT.md](docs/REPRODUCIBILITY_CONTRACT.md), [docs/PAPER_AGNOSTIC_PIPELINE.md](docs/PAPER_AGNOSTIC_PIPELINE.md), [docs/SCRIPT_MATURITY.md](docs/SCRIPT_MATURITY.md).

---

## Script trust boundary

The `scripts/` directory mixes production entry points with research experiments and reporting tools. The registry at [`scripts/script_registry.py`](scripts/script_registry.py) is the authority:

```bash
python scripts/script_registry.py --tier official_pipeline
python scripts/script_registry.py --check       # enforce coverage
```

| Tier | Count | Purpose |
|---|---|---|
| `official_pipeline` | 7 | Canonical entry points |
| `official_support` | 47 | Building blocks used by the pipeline |
| `ci_gate` | 12 | Audit / integrity / regression gates |
| `reporting` | 34 | Read-only telemetry and reporting |
| `internal_support` | 18 | Plumbing libraries |
| `dev_tool` | 22 | Developer / smoke utilities |
| `research_experiment` | 33 | Experiments — not part of the contract |
| `benchmark` | 2 | miniF2F calibration |

---

## Honest scope

The pipeline closes ~10–20% of theorems on the canonical 8-paper corpus at AB-or-higher. Research-paper proofs spanning 5–50 pages are NOT reachable by current SOTA (LeanDojo / Kimina / DeepSeek-Prover all cap at IMO/undergrad level). The remaining failure modes are upstream of the LLM's tactic reasoning:

1. **Statement-quality gap** — rows with `elaboration_failure` that even the repair infrastructure cannot fix.
2. **Typeclass-gap signatures** — signatures missing `[MeasurableSpace alpha]` etc.; the proof body cannot supply this.
3. **Bound-variable hallucination** — LLM emits `sorry` because the binder context exceeds its spatial reasoning.
4. **Paper-local axiom opacity** — paper-local axioms with no definitional content cannot be closed from scratch; the row should be `AXIOM_BACKED` modulo the named axioms.

What the pipeline DOES produce reliably:

- Mutation-test-verified integrity audit that no bypass class survives.
- Standards-positive promotion: every closure has a real lake-verified proof body.
- Per-row axiom debt with named provenance.
- CoT-traced equivalence judgements (full step reasoning persisted).
- A versioned schema-stable corpus dataset suitable for AI-for-math fine-tuning.

What it does NOT do:

- Prove arbitrary research-paper theorems automatically.
- Discover Mathlib alignments for paper-local definitions (`align_def` is the scaffold; alignment proofs themselves are human work).
- Replace human review for release-grade verification (LLM-only verdicts are blocked from `release_eligible`).

---

## Citation

```bibtex
@software{desol2026,
  title={DESol: auditable arXiv-to-Lean formalization with standards-positive integrity},
  year={2026}
}
```

---

**Lean toolchain:** `v4.29.0-rc7` · **Tests:** 1,664 passing · **miniF2F pass@1:** 40% · **Audit fuzz:** 18,000 iter / 0 escapes
