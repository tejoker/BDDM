# Reproducibility Contract

DESol claims are only current when the repository can point to committed,
machine-readable evidence for them.

## Toolchain

The current Lean toolchain is:

```text
leanprover/lean4:v4.29.0-rc7
```

This value is authoritative because it comes from `lean-toolchain`. Documentation,
claim registries, benchmark artifacts, and paper ledgers must not describe a
different toolchain as current.

## Claim Levels

`current` claims are allowed in the README and project summary. They must have:

- a committed artifact path under `reproducibility/`, `paper_*`, or `docs/`;
- a recorded Lean toolchain matching `lean-toolchain` when Lean is involved;
- the exact command or script needed to regenerate the artifact;
- enough schema metadata to distinguish a fresh run from a historical one.

`historical` claims may remain in the repo for context, but they must say why
they are not current. A toolchain mismatch, missing full per-problem output, or
missing rerun command is enough to mark a result historical.

`unsupported` claims should not appear in the README. Keep them in internal notes
until evidence exists.

## Paper-Level Evidence

For each paper-level result, commit or publish the following evidence bundle:

- theorem inventory JSON;
- normalized LaTeX statement JSON;
- translation candidate JSON with confidence and validation status;
- generated Lean file or curated Lean file with provenance;
- proof attempt trace JSON;
- verification ledger JSON;
- blocker taxonomy JSON;
- KG promotion manifest, when promoted to the KG.

If a Lean file is hand-translated or hand-curated, the evidence bundle must say
so explicitly. Hand-curated case studies are useful, but they are not automatic
pipeline proof of paper-agnostic behavior.

## Corpus Release Manifests

Corpus release manifests use `corpus_release_schema_version` `1.1.0`. A current
release manifest must include:

- `release_audit.repository.git_commit`;
- `release_audit.toolchain.lean_toolchain`, matching `lean-toolchain`;
- `release_audit.toolchain.lean_version` and `python_version`;
- `release_audit.toolchain.mathlib.rev`, copied from `lakefile.toml`;
- `release_audit.provenance_policy` with source traceability, license, and trust
  tier notes;
- an `artifacts` inventory with role, path, existence, required/optional status,
  size, and SHA-256 for every existing file;
- an artifact summary that reports checksum coverage and missing required files.

Per-paper bundles under `reproducibility/full_paper_reports/<paper-id>/` and the
aggregate `reproduce_public_claims.py` manifest must use the same audit fields.
Missing optional diagnostics may be listed in `missing_artifacts`, but missing
required release artifacts fail release readiness.

## Corpus Row Schemas

Release manifests describe bundles. The theorem-level corpus JSONL uses a
separate row schema: `corpus_row.v1`, with summaries using
`corpus_export_summary.v1`. These schemas live under `schemas/` and are validated
by the corpus exporter/tests.

Every row must carry the source identifiers, toolchain hash, source text,
Lean statement, status, proof method, trust tier, provenance, artifact paths,
alignment metadata, and explicit `dataset_tier` / `training_tier` fields. Raw
`FULLY_PROVEN` remains a status count only; consumers should use `gold_proof`
and `verified_proven` fields for proof-quality filtering.

## Benchmark Evidence

miniF2F and similar benchmarks are proof-search calibration artifacts. They are
not the main project claim. A benchmark result should include:

- split, problem count, `k`, solved count, and pass rate;
- model name and relevant decoding/search parameters;
- Lean toolchain and Python version;
- git commit;
- retrieval index version or digest;
- full or sampled per-problem results;
- failure taxonomy.

The historical `minif2f_test_244_results.json` artifact records Lean
`v4.30.0-rc1`; the repo is pinned to `v4.29.0-rc7`. Treat it as historical until
rerun under the current pin.
