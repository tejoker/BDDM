# Reproducibility

## miniF2F benchmark — 28.7% pass@1 (244 test problems)

**Pinned result**: [minif2f_test_244_results.json](minif2f_test_244_results.json)

Run date: 2026-04-02 | Model: `labs-leanstral-2603` | Lean toolchain: `v4.30.0-rc1`

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

Expected output:
```
miniF2F benchmark — split=test k=1 n=244
  pass@1 : 28.7%  (70/244 solved)
  elapsed: ~1.8h
```

### Baseline comparison

| System | pass@1 |
|--------|--------|
| **DeSol** (this result) | **28.7%** |
| ReProver (GPT-4 + best-first) | 27.3% |
| LLM-Step (Llama-2) | 22.0% |
| Aesop (no LLM) | 4.0% |
| Raw LeanStral (no search) | 0.0% |
| HyperTree Proof Search (Meta) | 33.0% |

### Ablation

| Configuration | pass@1 (50 problems) |
|---------------|----------------------|
| Raw LeanStral, no feedback | 0.0% |
| DeSol + Mistral Large, no retrieval | 14.0% |
| DeSol + LeanStral, no retrieval | 22.0% |
| DeSol + LeanStral + retrieval | 18–22% (n=50 noise) |
| **DeSol + LeanStral + retrieval (n=244)** | **28.7%** |

### Known variance sources

- `lake build` is not perfectly deterministic across Lean patch versions
- Mistral API responses have non-zero temperature variance at `temperature=0`
- Expected range: ±2% (±5 problems) across reruns

### Artifact sanity check

Run the baseline readiness checker (includes pinned benchmark artifact schema checks):

```bash
python3 scripts/release_readiness.py
```
