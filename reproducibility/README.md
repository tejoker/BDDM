# Reproducibility

The repository is pinned to Lean `leanprover/lean4:v4.29.0-rc7` via `lean-toolchain`.
Any metric cited as a current DESol result must be backed by a committed artifact
whose recorded Lean toolchain matches that pin.

## miniF2F Calibration Artifact

**Historical result**: [minif2f_test_244_results.json](minif2f_test_244_results.json)

Run date: 2026-04-02 | Model: `labs-leanstral-2603` | Recorded Lean toolchain: `v4.30.0-rc1`

This artifact is retained as evidence of a previous proof-search calibration run:
`70/244` solved (`28.7% pass@1`). It should not be cited as a current
reproducible result until rerun under the repository's current Lean `v4.29.0-rc7`
toolchain.

### Reproduce in one command

Requirements: a server with the repo's pinned Lean toolchain (`lean-toolchain`) + Mathlib cache built, Python 3.11,
`MISTRAL_API_KEY` in `.env`.

```bash
git clone <this repo>
cd DESol
pip install -r requirements.txt
# Build Mathlib cache (~30 min first time)
lake build
# Run full benchmark (~2h, workers=1 required to avoid lake cache conflicts)
python scripts/benchmark_minif2f.py \
  --split test \
  --k 1 \
  --workers 1 \
  --model labs-leanstral-2603 \
  --retrieval-index data/mathlib_embeddings \
  --retrieval-top-k 12 \
  --project-root . \
  --lean-timeout 120 \
  --out-dir output/full_minif2f_test
```

Historical output:
```
miniF2F benchmark — split=test k=1 n=244
  pass@1 : 28.7%  (70/244 solved)
  elapsed: ~1.8h
```

### Current Claim Policy

- miniF2F is a calibration benchmark for proof search, not the headline project claim.
- The `27.5%` MCTS result is not listed here because no matching committed artifact is present.
- Translation-quality percentages must cite an evaluation artifact under `reproducibility/`.
- Paper-level claims must cite a verification ledger, generated Lean file, and blocker report.

### Known variance sources

- `lake build` is not perfectly deterministic across Lean patch versions
- Mistral API responses have non-zero temperature variance at `temperature=0`
- Expected range: ±2% (±5 problems) across reruns

### Artifact sanity check

Run the baseline readiness checker:

```bash
python3 scripts/release_readiness.py
```

## Golden10 Paper-Agnostic Ingestion Run

The small paper-agnostic suite is committed at
[paper_agnostic_golden10.json](paper_agnostic_golden10.json). Its first evidence
run is committed under
[paper_agnostic_golden10_results/summary.json](paper_agnostic_golden10_results/summary.json).

Regenerate the fetch and theorem-extraction artifacts with:

```bash
python scripts/paper_ingestion_evidence.py \
  --suite-json reproducibility/paper_agnostic_golden10.json \
  --evidence-root reproducibility/paper_agnostic_golden10_results \
  --source-root output/paper_sources/golden10
```

Current recorded result: 10 papers attempted, 8 TeX sources fetched, 7 theorem
inventories extracted, 242 theorem-like statements found. Translation and proof
search were not run because the recorded environment had no `MISTRAL_API_KEY`.
