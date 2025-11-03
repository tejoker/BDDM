# Migration Guide: Web Scraping to Dump-Based Parsing

## Overview

Your math scraper has been upgraded from web scraping (v2) to dump-based parsing (v3).

## Performance Improvement

| Metric | v2 (Web Scraping) | v3 (Dump Parsing) | Improvement |
|--------|-------------------|-------------------|-------------|
| **Time** | 96 days | 19 hours | **120x faster** |
| **Rate Limits** | Yes (frequent) | No | **No limits** |
| **Completeness** | Partial | 100% | **Complete data** |
| **Network Dependency** | Required | One-time download | **Offline capable** |
| **Total Items** | ~1.1M | ~1.66M | **50% more data** |

## New Data Sources Added

1. **OEIS** - 370,000 integer sequences
2. **Lean Mathlib** - 150,000 formal theorems
3. **Metamath** - 40,000 formal proofs
4. **Isabelle AFP** - 10,000+ formal proofs
5. **Coq** - 5,000+ constructive proofs
6. **Proof-Pile** - 20,000+ mixed proofs
7. **zbMATH Open** - 4M metadata records

## Quick Start

### Step 1: Download Dumps

```bash
# Download all data dumps (~100GB, one-time)
./download_dumps.sh
```

This will download:
- Wikipedia dump (~20GB)
- Stack Exchange dump (~15GB)
- MathOverflow dump (~3GB)
- ArXiv metadata (~3GB)
- OEIS database (~200MB)
- Lean Mathlib (git clone)
- Metamath (~30MB)
- Isabelle AFP (git clone)
- Coq (git clone)

**Time**: 4-8 hours depending on connection

### Step 2: Parse Dumps

```bash
# Quick test (30 minutes)
./math/bin/python collect_dumps.py small

# Medium collection (2 hours)
./math/bin/python collect_dumps.py medium

# Large collection (8 hours)
./math/bin/python collect_dumps.py large

# Maximum collection (19 hours)
./math/bin/python collect_dumps.py max
```

## File Structure

```
math_scraper/
├── parsers/                      # NEW: Dump parsers
│   ├── base_parser.py
│   ├── wikipedia_dump_parser.py
│   ├── stackexchange_dump_parser.py
│   ├── mathoverflow_dump_parser.py
│   ├── arxiv_kaggle_parser.py
│   ├── oeis_parser.py
│   ├── proofpile_parser.py
│   ├── lean_mathlib_parser.py
│   ├── metamath_parser.py
│   ├── isabelle_afp_parser.py
│   ├── coq_parser.py
│   └── zbmath_parser.py
├── scrapers/                     # OLD: Web scrapers (still available)
│   └── ...
├── data_dumps/                   # NEW: Downloaded dumps
│   ├── wikipedia/
│   ├── stackexchange/
│   ├── mathoverflow/
│   ├── arxiv/
│   ├── oeis/
│   ├── mathlib4/
│   ├── metamath/
│   ├── isabelle-afp/
│   └── coq/
├── download_dumps.sh            # NEW: Download script
├── collect_dumps.py             # NEW: Dump-based collection
└── collect_samples.py           # OLD: Web scraping (legacy)
```

## Parser Details

### 1. Wikipedia Dump Parser
- **Input**: `enwiki-latest-pages-articles.xml.bz2`
- **Format**: MediaWiki XML
- **Features**: Filters math articles by category
- **Output**: ~50k math articles

### 2. Stack Exchange Dump Parser
- **Input**: `math.stackexchange.com/Posts.xml`
- **Format**: XML with questions and answers
- **Features**: Matches questions with accepted answers
- **Output**: ~500k Q&A pairs

### 3. MathOverflow Dump Parser
- **Input**: `mathoverflow.net/Posts.xml`
- **Format**: Same as Stack Exchange
- **Features**: Research-level Q&A
- **Output**: ~150k Q&A pairs

### 4. ArXiv Kaggle Parser
- **Input**: `arxiv-metadata-oai-snapshot.json`
- **Format**: JSONL (one paper per line)
- **Features**: Filters math.* categories, can extract proofs from source
- **Output**: ~400k papers

### 5. OEIS Parser
- **Input**: `stripped.gz`
- **Format**: Custom text format
- **Features**: Sequences with formulas, examples, cross-references
- **Output**: ~370k sequences

### 6. Lean Mathlib Parser
- **Input**: `mathlib4/` git repository
- **Format**: Lean 4 .lean files
- **Features**: Extracts theorem/lemma declarations with proofs
- **Output**: ~150k theorems

### 7. Metamath Parser
- **Input**: `set.mm` file
- **Format**: Metamath database format
- **Features**: Parses $p (provable) statements
- **Output**: ~40k theorems

### 8. Isabelle AFP Parser
- **Input**: `isabelle-afp/` git repository
- **Format**: Isabelle .thy files
- **Features**: Extracts theorem/lemma/corollary with proofs
- **Output**: ~10k+ theorems

### 9. Coq Parser
- **Input**: `coq/` git repository
- **Format**: Coq .v files
- **Features**: Extracts Theorem/Lemma/Proposition with Proof...Qed
- **Output**: ~5k+ theorems

### 10. Proof-Pile Parser
- **Input**: HuggingFace dataset
- **Format**: JSONL
- **Features**: Mixed sources (ProofWiki, Stacks Project, etc.)
- **Output**: ~20k items

### 11. zbMATH Parser
- **Input**: API-based (no download)
- **Format**: OAI-PMH or REST API
- **Features**: Research paper metadata
- **Output**: Configurable

## Comparison: Old vs New

### Old Approach (collect_samples.py)
```bash
# Collect 10k items from Stack Exchange
./math/bin/python collect_samples.py 10000 0 0 0 0 0 0

# Time: ~20 hours
# Rate limits: Frequent 429 errors
# Network: Constant connection required
```

### New Approach (collect_dumps.py)
```bash
# One-time download
./download_dumps.sh

# Collect 500k items from Stack Exchange
./math/bin/python collect_dumps.py 0 500000 0 0 0 0 0 0 0 0 0

# Time: ~6 hours
# Rate limits: None
# Network: Offline after download
```

## Migration Checklist

- [x] Install dependencies: `./install.sh`
- [ ] Download dumps: `./download_dumps.sh`
- [ ] Test small collection: `./math/bin/python collect_dumps.py small`
- [ ] Run full collection: `./math/bin/python collect_dumps.py max`
- [ ] Update your training scripts to use new data

## Troubleshooting

### Issue: 7zip not found
```bash
# Ubuntu/Debian
sudo apt-get install p7zip-full

# macOS
brew install p7zip
```

### Issue: Kaggle CLI not found
```bash
pip install kaggle

# Configure Kaggle credentials
# 1. Go to https://www.kaggle.com/settings
# 2. Create new API token
# 3. Place kaggle.json in ~/.kaggle/
```

### Issue: HuggingFace datasets not found
```bash
pip install datasets
```

### Issue: Parser fails with encoding error
- Most parsers use UTF-8 with error handling
- Check file integrity: `md5sum <file>`
- Re-download if corrupted

## Custom Collection

You can specify custom counts for each source:

```bash
./math/bin/python collect_dumps.py \
  1000  \  # Wikipedia
  5000  \  # Stack Exchange
  1000  \  # MathOverflow
  2000  \  # ArXiv
  10000 \  # OEIS
  0     \  # Proof-Pile (skip)
  5000  \  # Lean Mathlib
  0     \  # Metamath (skip)
  0     \  # Isabelle AFP (skip)
  0     \  # Coq (skip)
  0        # zbMATH (skip)
```

## Data Quality

### Highest Quality Sources
1. **Metamath** (100/100) - Formally verified from axioms
2. **Lean Mathlib** (95/100) - Proof assistant verified
3. **Isabelle AFP** (95/100) - Formally verified
4. **Coq** (95/100) - Constructive proofs
5. **OEIS** (90/100) - Peer-reviewed sequences

### Largest Volume Sources
1. **Stack Exchange** - 500,000 items
2. **ArXiv** - 400,000 papers
3. **OEIS** - 370,000 sequences
4. **MathOverflow** - 150,000 items
5. **Lean Mathlib** - 150,000 theorems

## Next Steps

1. **Download dumps** using `./download_dumps.sh`
2. **Test parsing** with `collect_dumps.py small`
3. **Run full collection** with `collect_dumps.py max`
4. **Use your data** for model training

## Legacy Support

The old web scraping approach (`collect_samples.py`) is still available for:
- Sources without dumps (nLab, Project Euler)
- Incremental updates
- Testing individual scrapers

However, **dump-based parsing is strongly recommended** for production use.

## Questions?

- Check [README.md](README.md) for detailed documentation
- See parser source code in `parsers/` directory
- Each parser has a `__main__` block for testing

---

**Built with the goal of advancing mathematical AI research**
