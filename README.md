# BDDM - Mathematical Dataset Builder

Large-scale mathematics dataset collection tool for theorem-proof pairs, Q&A, formal proofs, and research papers.

## What's New in v3.0

v3.0 introduces dump-based parsing, replacing the previous web scraping approach. This results in significantly faster collection times and more complete data coverage.

Key improvements:
- Collection time reduced from 96 days to 19 hours (120x faster)
- Dataset size increased from 1.1M to 1.66M items (50% more)
- No more rate limits or API restrictions
- Perfect reproducibility (same dumps = same results)
- 11 data sources (4 new formal math sources added)

## Data Sources

### Primary Sources (Dump-Based)

| Source | Type | Items | Time |
|--------|------|-------|------|
| Stack Exchange | Q&A | 500k | 6h |
| MathOverflow | Research Q&A | 150k | 2h |
| ArXiv (Kaggle) | Papers | 400k | 4h |
| OEIS | Sequences | 370k | 2h |
| Lean Mathlib | Formal theorems | 150k | 3h |
| Wikipedia | Encyclopedia | 50k | 1h |
| Metamath | Formal proofs | 40k | 30m |
| Proof-Pile | Mixed formal | 20k | 1h |
| Isabelle AFP | Formal proofs | 10k+ | 2h |
| Coq | Constructive proofs | 5k+ | 1h |
| zbMATH Open | Research metadata | 4M | API |

## Quick Start

### Installation

```bash
git clone https://github.com/tejoker/BDDM.git
cd BDDM
./install.sh
```

### Step 1: Download Data Dumps

This is a one-time operation that downloads approximately 100GB of data.

```bash
./download_dumps.sh
```

Downloads include Stack Exchange XML dumps, MathOverflow dumps, ArXiv Kaggle dataset, OEIS database, Lean Mathlib, Metamath database, Proof-Pile, Isabelle AFP, and Coq sources.

### Step 2: Parse Dumps

Choose a collection size based on your needs:

```bash
# Small test - 10k items (~30 minutes)
./math/bin/python collect_dumps.py small

# Medium dataset - 50k items (~2 hours)
./math/bin/python collect_dumps.py medium

# Large dataset - 200k items (~8 hours)
./math/bin/python collect_dumps.py large

# Maximum dataset - 1.66M items (~19 hours)
./math/bin/python collect_dumps.py max
```

### Step 3: Access Your Data

Data is stored in `samples_en/` with source-specific subdirectories:

```bash
ls samples_en/raw/
cat samples_en/raw/stackexchange/batch_*.json | jq
```

## Collection Presets

### small (30 min, ~10k items)
Good for testing all parsers and validating your setup.

### medium (2h, ~50k items)
Balanced dataset suitable for initial model training.

### large (8h, ~200k items)
Comprehensive dataset for serious training work.

### max (19h, ~1.66M items)
Complete dataset with all available data from each source.

### Custom Configuration

You can specify exact counts per source:

```bash
# Format: SE MO Wiki ArXiv OEIS ProofPile Lean Metamath Isabelle Coq zbMATH
./math/bin/python collect_dumps.py 1000 500 200 2000 5000 100 500 200 100 50 0
```

Or select specific sources:

```bash
# Only Stack Exchange and MathOverflow
./math/bin/python collect_dumps.py se mo

# Only formal proof sources
./math/bin/python collect_dumps.py lean mm isa coq pp
```

Source codes: `se` (Stack Exchange), `mo` (MathOverflow), `wiki` (Wikipedia), `arxiv` (ArXiv), `oeis` (OEIS), `pp` (Proof-Pile), `lean` (Lean Mathlib), `mm` (Metamath), `isa` (Isabelle AFP), `coq` (Coq), `zbm` (zbMATH)

## Output Structure

```
samples_en/
├── index.json              # Master index for duplicate tracking
├── checkpoint.json         # Resume checkpoint
└── raw/                    # Raw data by source
    ├── stackexchange/
    ├── mathoverflow/
    ├── wikipedia/
    ├── arxiv/
    ├── oeis/
    ├── lean/
    ├── metamath/
    ├── proofpile/
    ├── isabelle/
    ├── coq/
    └── zbmath/
```

### Data Format

Q&A format (Stack Exchange, MathOverflow):
```json
{
  "id": "se_12345",
  "source": "stackexchange",
  "title": "Prove by induction that...",
  "question": "Full question text...",
  "answer": "Full answer with proof...",
  "tags": ["induction", "proof-writing"],
  "score": 42,
  "url": "https://math.stackexchange.com/questions/12345"
}
```

Theorem-proof format (Lean, Metamath, Isabelle, Coq):
```json
{
  "id": "lean_12345",
  "source": "lean_mathlib",
  "title": "theorem_name",
  "theorem": "Statement of theorem...",
  "proof": "Complete formal proof...",
  "file": "Mathlib/Algebra/Group/Basic.lean"
}
```

Paper format (ArXiv):
```json
{
  "id": "arxiv_1234.5678",
  "source": "arxiv",
  "title": "Paper Title",
  "abstract": "Paper abstract...",
  "authors": ["Author 1", "Author 2"],
  "categories": ["math.NT", "math.AG"],
  "url": "https://arxiv.org/abs/1234.5678"
}
```

## Advanced Usage

### Resume Collection

Collection can be resumed if interrupted:

```bash
./math/bin/python collect_dumps.py large
# If interrupted, resume with:
./math/bin/python collect_dumps.py --resume
```

## Storage Requirements

| Collection | Items | Raw JSON | Compressed |
|------------|-------|----------|------------|
| Small | 10k | ~1 GB | ~300 MB |
| Medium | 50k | ~5 GB | ~1.5 GB |
| Large | 200k | ~15 GB | ~4.5 GB |
| Maximum | 1.66M | ~60 GB | ~18 GB |

Dump storage requires approximately 100GB (one-time download).

## Legacy Web Scraping

The previous web scraping approach is still available but deprecated:

```bash
# Small test (~275 items, ~5 minutes)
./math/bin/python collect_samples.py 50 30 100 20 50 5 20

# Medium collection (~2,550 items, ~3-5 hours)
./math/bin/python collect_samples.py 1000 500 200 200 500 50 100
```

This method is not recommended due to:
- 120x slower performance
- Rate limits and API restrictions
- Incomplete data coverage
- Network dependency
- Non-reproducible results

## Next Steps

Once you've collected your mathematical dataset, the next step is training models on this data. Check out [LeanLM](https://github.com/tejoker/LeanLM.git) for the continuation of this work, which focuses on training language models for translation into Lean 4

## Documentation

- [DUMP_MIGRATION_GUIDE.md](DUMP_MIGRATION_GUIDE.md) - v2 to v3 migration guide
- [ARCHITECTURE.md](ARCHITECTURE.md) - Technical architecture and design
- [CHANGELOG.md](CHANGELOG.md) - Version history

## Troubleshooting

**"Dumps not found" error:**
```bash
./download_dumps.sh
```

**Missing dependencies:**
```bash
./math/bin/pip install -r requirements.txt
```

**Out of disk space:**
- Free up space (dumps need ~100GB)
- Use smaller collection preset

**Parser crashes:**
- Check logs in `parser.log`
- Report issues at https://github.com/tejoker/BDDM/issues

## Performance Tips

- Use SSD storage for significantly faster parsing
- Run multiple parsers simultaneously when possible
- Compress output with gzip to save 70% disk space
- Only parse the sources you need

## Contributing

To add a new parser:

1. Create `parsers/new_source_parser.py`
2. Inherit from `BaseDumpParser`
3. Implement `parse()` method
4. Add to `collect_dumps.py`
5. Update documentation

See existing parsers for examples.

## License & Attribution

Data sources have different licenses:
- Stack Exchange/MathOverflow: CC BY-SA 4.0
- Wikipedia: CC BY-SA 3.0
- ArXiv: Various (check individual papers)
- OEIS: CC BY-SA 3.0
- Lean Mathlib: Apache 2.0
- Metamath: Public Domain
- Isabelle AFP: BSD License
- Coq: LGPL
- Proof-Pile: Various

Please respect licenses and provide proper attribution when using collected data.

## Support

- Issues: https://github.com/tejoker/BDDM/issues
- Discussions: https://github.com/tejoker/BDDM/discussions
- Repository: https://github.com/tejoker/BDDM
