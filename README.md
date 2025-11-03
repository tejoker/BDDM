# BDDM - Mathematical Dataset Builder v3.0

Large-scale mathematics dataset collection tool for theorem-proof pairs, Q&A, formal proofs, and research papers.

[![Version](https://img.shields.io/badge/version-3.0.0-blue.svg)](https://github.com/tejoker/BDDM/releases)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## What's New in v3.0

**Revolutionary Performance Upgrade: Dump-Based Parsing**

| Metric | v2 (Web Scraping) | v3 (Dump Parsing) | Improvement |
|--------|-------------------|-------------------|-------------|
| **Collection Time** | 96 days | 19 hours | **120x faster** |
| **Total Items** | 1.1M | 1.66M | **50% more data** |
| **Rate Limits** | Frequent | None | **No limits** |
| **Data Coverage** | Partial | 100% | **Complete** |
| **Reproducibility** | Variable | Perfect | **Same dumps = same results** |

**New Features:**
- 11 dump-based parsers (vs 7 web scrapers)
- 7 new formal mathematics sources
- Offline processing after initial download
- 100% reproducible datasets
- No API keys or rate limits

---

## Data Sources (v3)

### Dump-Based Sources (Primary)

| Source | Type | Items | Quality | Time |
|--------|------|-------|---------|------|
| **Stack Exchange** | Q&A | 500k | ★★★ | 6h |
| **MathOverflow** | Research Q&A | 150k | ★★★★★ | 2h |
| **ArXiv (Kaggle)** | Papers | 400k | ★★★★★ | 4h |
| **OEIS** | Sequences | 370k | ★★★★★ | 2h |
| **Lean Mathlib** | Formal theorems | 150k | ★★★★★ | 3h |
| **Wikipedia** | Encyclopedia | 50k | ★★★★ | 1h |
| **Metamath** | Formal proofs | 40k | ★★★★★ | 30m |
| **Proof-Pile** | Mixed formal | 20k | ★★★★ | 1h |
| **Isabelle AFP** | Formal proofs | 10k+ | ★★★★★ | 2h |
| **Coq** | Constructive proofs | 5k+ | ★★★★★ | 1h |
| **zbMATH Open** | Research metadata | 4M | ★★★★ | API |

### Legacy Web Scraping (Deprecated)

| Source | Type | Items | Quality | Status |
|--------|------|-------|---------|--------|
| ProofWiki | Formal proofs | 20k | ★★★★★ | Deprecated (use Proof-Pile) |
| nLab | Advanced math | 15k | ★★★★ | Deprecated |
| Project Euler | Competition | 956 | ★★★★ | Deprecated |

---

## Quick Start

### Installation

```bash
git clone https://github.com/tejoker/BDDM.git
cd BDDM
./install.sh
```

### Step 1: Download Data Dumps (One-Time)

```bash
# Downloads ~100GB of data dumps (4-8 hours depending on connection)
./download_dumps.sh
```

This downloads:
- Stack Exchange XML dumps
- MathOverflow XML dumps
- ArXiv Kaggle dataset
- OEIS database
- Lean Mathlib git repository
- Metamath database
- Proof-Pile from HuggingFace
- Isabelle AFP git repository
- Coq sources

### Step 2: Parse Dumps

Choose a collection size:

```bash
# Small test - 10k items in ~30 minutes
./math/bin/python collect_dumps.py small

# Medium dataset - 50k items in ~2 hours
./math/bin/python collect_dumps.py medium

# Large dataset - 200k items in ~8 hours
./math/bin/python collect_dumps.py large

# Maximum dataset - 1.66M items in ~19 hours
./math/bin/python collect_dumps.py max
```

### Step 3: Use Your Data

```bash
# Data is stored in samples_en/
ls samples_en/raw/

# Each source has its own directory with JSON batches
cat samples_en/raw/stackexchange/batch_*.json | jq
```

---

## Collection Presets

### `small` - Quick Test (30 min, ~10k items)
Perfect for testing all parsers and validating your setup.

```bash
./math/bin/python collect_dumps.py small
```

**Sources:**
- Stack Exchange: 1,000
- MathOverflow: 1,000
- Wikipedia: 500
- ArXiv: 2,000
- OEIS: 3,000
- Lean: 1,000
- Metamath: 1,000
- Others: 500

### `medium` - Training Dataset (2h, ~50k items)
Good balance for initial model training.

```bash
./math/bin/python collect_dumps.py medium
```

**Sources:**
- Stack Exchange: 10,000
- MathOverflow: 5,000
- Wikipedia: 2,000
- ArXiv: 10,000
- OEIS: 15,000
- Lean: 5,000
- Metamath: 2,000
- Others: 1,000

### `large` - Comprehensive Dataset (8h, ~200k items)
Comprehensive dataset for serious training.

```bash
./math/bin/python collect_dumps.py large
```

**Sources:**
- Stack Exchange: 50,000
- MathOverflow: 20,000
- Wikipedia: 10,000
- ArXiv: 50,000
- OEIS: 40,000
- Lean: 20,000
- Metamath: 5,000
- Others: 5,000

### `max` - Complete Dataset (19h, ~1.66M items)
Maximum dataset with all available data.

```bash
./math/bin/python collect_dumps.py max
```

**Sources:**
- Stack Exchange: 500,000
- MathOverflow: 150,000
- Wikipedia: 50,000
- ArXiv: 400,000
- OEIS: 370,000
- Lean: 150,000
- Metamath: 40,000
- Others: 20,000

### Custom Configuration

```bash
# Specify exact counts for each source:
# Format: SE MO Wiki ArXiv OEIS ProofPile Lean Metamath Isabelle Coq zbMATH
./math/bin/python collect_dumps.py 1000 500 200 2000 5000 100 500 200 100 50 0
```

---

## Output Structure

```
samples_en/
├── index.json              # Master index (duplicate tracking)
├── checkpoint.json         # Resume checkpoint
└── raw/                    # Raw data by source
    ├── stackexchange/
    │   ├── batch_20251103_120000.json
    │   └── batch_20251103_123000.json
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

**Q&A Format** (Stack Exchange, MathOverflow):
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

**Theorem-Proof Format** (Lean, Metamath, Isabelle, Coq):
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

**Paper Format** (ArXiv):
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

---

## Advanced Usage

### Resume Collection

All collection scripts support resuming:

```bash
# Start collection
./math/bin/python collect_dumps.py large

# If interrupted (Ctrl+C, crash, etc.), resume:
./math/bin/python collect_dumps.py --resume
```

### Select Specific Sources

```bash
# Only parse Stack Exchange and MathOverflow:
./math/bin/python collect_dumps.py se mo

# Only parse formal proof sources:
./math/bin/python collect_dumps.py lean mm isa coq pp
```

Source codes:
- `se` - Stack Exchange
- `mo` - MathOverflow
- `wiki` - Wikipedia
- `arxiv` - ArXiv
- `oeis` - OEIS
- `pp` - Proof-Pile
- `lean` - Lean Mathlib
- `mm` - Metamath
- `isa` - Isabelle AFP
- `coq` - Coq
- `zbm` - zbMATH Open

---

## Storage Requirements

| Collection | Items | Raw JSON | Compressed |
|------------|-------|----------|------------|
| Small | 10k | ~1 GB | ~300 MB |
| Medium | 50k | ~5 GB | ~1.5 GB |
| Large | 200k | ~15 GB | ~4.5 GB |
| Maximum | 1.66M | ~60 GB | ~18 GB |

**Dump storage:** ~100 GB (one-time download)

---

## Legacy Web Scraping (Deprecated)

For compatibility, web scraping is still available but **not recommended**:

```bash
# Small test (~275 items, ~5 minutes)
./math/bin/python collect_samples.py 50 30 100 20 50 5 20

# Medium collection (~2,550 items, ~3-5 hours)
./math/bin/python collect_samples.py 1000 500 200 200 500 50 100
```

**Why deprecated:**
- 120x slower than dump parsing
- Rate limits and API restrictions
- Incomplete data coverage
- Network-dependent
- Not reproducible

**Use dump parsing instead!**

---

## Documentation

- **[DUMP_MIGRATION_GUIDE.md](DUMP_MIGRATION_GUIDE.md)** - Complete v2→v3 migration guide
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - Code structure and technical details
- **[CHANGELOG.md](CHANGELOG.md)** - Version history and breaking changes

---

## Troubleshooting

### "Dumps not found" error
```bash
# Download dumps first:
./download_dumps.sh
```

### Missing dependencies
```bash
./math/bin/pip install -r requirements.txt
```

### Out of disk space
- Free up space (dumps need ~100GB)
- Use smaller collection preset

### Parser crashes
- Check logs in `scraper.log`
- Report issues: https://github.com/tejoker/BDDM/issues

---

## Performance Tips

1. **Use SSD storage** - Significantly faster parsing
2. **Parallel parsing** - Run multiple parsers simultaneously
3. **Compress output** - Use `gzip` to save 70% disk space
4. **Selective parsing** - Only parse sources you need

---

## Contributing

To add a new parser:

1. Create `parsers/new_source_parser.py`
2. Inherit from `BaseParser`
3. Implement `parse()` method
4. Add to `collect_dumps.py`
5. Update documentation

See existing parsers for examples.

---

## License & Attribution

Data sources have different licenses:
- **Stack Exchange/MathOverflow**: CC BY-SA 4.0
- **Wikipedia**: CC BY-SA 3.0
- **ArXiv**: Various (check individual papers)
- **OEIS**: CC BY-SA 3.0
- **Lean Mathlib**: Apache 2.0
- **Metamath**: Public Domain
- **Isabelle AFP**: BSD License
- **Coq**: LGPL
- **Proof-Pile**: Various

Please respect licenses and provide proper attribution when using collected data.

---

## Support

- **Issues**: https://github.com/tejoker/BDDM/issues
- **Discussions**: https://github.com/tejoker/BDDM/discussions
- **Repository**: https://github.com/tejoker/BDDM

---

## Use Cases

- **LLM Training**: Mathematical reasoning and proof generation
- **Theorem Provers**: Training data for automated theorem proving
- **Education**: Mathematics problem banks and solutions
- **Research**: Mathematical corpus analysis
- **Benchmark Creation**: Evaluation datasets for math AI

---

Built for mathematical AI research.
