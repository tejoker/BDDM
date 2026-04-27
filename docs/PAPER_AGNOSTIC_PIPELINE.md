# Paper-Agnostic Pipeline Contract

DESol should be paper-agnostic in its behavior, not in an unrealistic promise
that every arXiv theorem will be fully proven. For any arXiv paper with LaTeX
source, the pipeline should produce an auditable formalization attempt and a
clear explanation of what blocked full verification.

## Target Behavior

For each paper, the pipeline should produce:

- fetched source metadata and TeX availability status;
- expanded LaTeX roots after include and macro preprocessing;
- theorem-like environment inventory;
- normalized statement text for every theorem-like item;
- Lean signature candidates;
- Lean validation results for each candidate;
- proof-search attempts and best partial proof traces;
- final verification ledger;
- blocker taxonomy;
- KG promotion manifest when eligible.

## Official Entry Points

The official script surface is intentionally small; see
`docs/SCRIPT_MATURITY.md` for the enforced registry. For paper-agnostic evidence,
use:

```bash
python scripts/arxiv_to_lean.py <paper-id> --out output/papers/
python scripts/formalize_paper_full.py --paper-id <paper-id> --project-root .
python scripts/run_paper_agnostic_suite.py --suite-json reproducibility/paper_agnostic_golden10.json
```

Other orchestration, repair, bridge, reliability, and proof-search scripts are
support modules, CI/reporting tools, benchmarks, experiments, or legacy one-offs
unless classified as `official_pipeline` in `scripts/script_registry.py`.

## Status Taxonomy

Use these statuses at the paper boundary:

- `FULLY_PROVEN`: Lean proof closes with no `sorry`, no ungrounded assumptions,
  and validated translation provenance.
- `AXIOM_BACKED`: Lean proof closes using named domain axioms that represent
  explicit mathematical IOUs.
- `VALID_STATEMENT_UNPROVEN`: Lean statement validates, but proof search did
  not close it.
- `TRANSLATION_UNCERTAIN`: extraction succeeded, but the Lean statement failed
  validation or semantic-fidelity checks.
- `EXTRACTION_FAILED`: the theorem-like item could not be reliably isolated from
  the source.
- `OUT_OF_SCOPE_DOMAIN`: the paper requires missing domain infrastructure that
  should be tracked as library work, not hidden as proof-search failure.

Existing internal statuses such as `INTERMEDIARY_PROVEN`, `FLAWED`, and
`UNRESOLVED` can still be used in lower-level ledgers, but paper-level summaries
should map them into the taxonomy above.

## Blocker Taxonomy

Every non-`FULLY_PROVEN` theorem should identify the dominant blocker:

- `missing_latex_source`;
- `latex_preprocessing`;
- `theorem_extraction`;
- `statement_translation`;
- `lean_elaboration`;
- `missing_mathlib_definition`;
- `missing_domain_library`;
- `proof_search_exhausted`;
- `api_or_runtime_failure`;
- `manual_review_required`.

The point is to make failure actionable. A low proof rate is acceptable for hard
papers if the ledger shows where the frontier is.

## Golden Paper Suite

Use a small committed suite of diverse arXiv papers to measure paper-agnostic
behavior before optimizing proof rate. The suite should include papers with
clean LaTeX, custom macros, many theorem aliases, domain-specific notation, and
at least one paper expected to require substantial new Mathlib infrastructure.

Success metrics for this suite should start with:

- source fetch success;
- theorem inventory recall on a manually checked sample;
- statement validation rate;
- blocker attribution coverage;
- ledger schema completeness.

Proof closure is important, but it should not be the first paper-agnostic metric.

The current committed seed suite is:

```bash
reproducibility/paper_agnostic_golden10.json
```

Run the fetch and theorem-extraction evidence pass with:

```bash
python scripts/paper_ingestion_evidence.py \
  --suite-json reproducibility/paper_agnostic_golden10.json \
  --evidence-root reproducibility/paper_agnostic_golden10_results \
  --source-root output/paper_sources/golden10
```

This produces one compact evidence bundle per paper:

- `fetch.json`
- `extracted_theorems.json`
- `summary.json`

The first committed run attempted 10 papers, fetched TeX for 8, extracted
theorem inventories for 7, and found 242 theorem-like statements. Translation
and proof search were not run in that environment because `MISTRAL_API_KEY` was
not set; that blocker is recorded in the JSON results rather than hidden.

## Reporting Command

After a suite run has produced verification ledgers, summarize paper-level
behavior without rerunning proof search:

```bash
python scripts/paper_agnostic_report.py \
  --ledger-dir output/verification_ledgers \
  --suite-json reproducibility/paper_agnostic_suite_seed.json \
  --out-json output/reports/paper_agnostic_report.json
```

The report records the active Lean toolchain, paper-level status counts, blocker
counts, and schema completeness for each ledger.

## External Method Comparison

DESol tracks three adjacent arXiv methods as method-level baselines, not as
leaderboard claims:

- `arXiv:2602.05216` (semantic theorem search): compare theorem extraction,
  theorem-level semantic surrogates/slogans, and retrieval readiness.
- `arXiv:2602.02990` (APRIL proof repair): compare whether failed Lean attempts
  are captured as compiler-feedback repair tuples.
- `arXiv:2603.17075` (CircuitBuilder): compare whether symbolic search runs
  expose verifier-backed state/action/reward traces.

Generate the local comparison artifact with:

```bash
python scripts/external_method_benchmark.py \
  --out-json output/reports/external_method_benchmark.json
```

This benchmark is deliberately conservative: it reports which DESol evidence is
currently comparable, which pieces are only partially implemented, and which
external-scale claims remain out of scope until a matching DESol artifact exists.

The DESol-local compiler-feedback corpus is emitted as:

```bash
python scripts/export_april_repair_dataset.py \
  --run-root output/flywheel/runs \
  --out-jsonl output/flywheel/compiler_feedback_repair_dataset.jsonl \
  --out-summary output/flywheel/compiler_feedback_repair_dataset_summary.json
```

Rows keep the APRIL-style core fields (`failing_lean`, `error_message`,
`local_context`, `previous_attempt`, `successful_repair`) while preserving DESol
provenance such as paper ID, theorem name, stage, failure class, and source
artifact. Live validation writes first to run-local files under
`output/flywheel/runs/<run_id>/`; the exporter merges those rows with ledger
evidence, deduplicates by stable `row_id`, and emits the canonical corpus.

## Claim Equivalence Review

Strict promotion requires auditable evidence that the Lean statement matches the
paper theorem. DESol treats claim equivalence as an artifact, not as a confidence
threshold or environment-variable relaxation.

Generate a review queue for unresolved semantic blockers:

```bash
python scripts/build_claim_equivalence_review_queue.py \
  --ledger output/verification_ledgers/2604.21884.json \
  --report output/reports/full_paper/2604.21884_suite_report.json \
  --out-jsonl output/claim_equivalence/review_queue/2604.21884.jsonl
```

Human, hybrid, or optional LLM adjudications are written to
`output/claim_equivalence/adjudications/<paper_id>.jsonl`. Apply them with:

```bash
python scripts/apply_claim_equivalence_adjudications.py \
  --ledger output/verification_ledgers/2604.21884.json \
  --adjudications output/claim_equivalence/adjudications/2604.21884.jsonl \
  --out-json output/claim_equivalence/merged/2604.21884_ledger.json
```

LLM adjudications are triage only. They can prioritize review and record a
structured opinion, but they must use `reviewer_type: "llm"` and
`review_policy: "requires_human_for_release"`; they cannot by themselves add
release-grade independent semantic evidence or promote a theorem to headline
`FULLY_PROVEN`.

Release-grade adjudications require `reviewer_type: "human"` or
`reviewer_type: "hybrid"`, complete matched assumption alignment, matched
conclusion alignment, no blocking risk flags, and the existing Lean/provenance
promotion gates. Conflicting release-eligible reviews block promotion until the
conflict is resolved.
