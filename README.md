# BDDM - Mathematical Dataset Builder v3.0# BDDM - Math Dataset Builder



🚀 **Large-scale mathematics dataset collection tool** for theorem-proof pairs, Q&A, formal proofs, and research papers.Large-scale mathematics dataset collection tool for theorem-proof pairs, Q&A, and formal mathematics content.



[![Version](https://img.shields.io/badge/version-3.0.0-blue.svg)](https://github.com/tejoker/BDDM/releases)🎯 **Goal**: Collect 1.1M-1.6M mathematical items from 11+ high-quality sources

[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)📊 **Data**: Theorems, proofs, Q&A pairs, research papers, formal proofs, sequences

🚀 **Status**: Production-ready v3 with DUMP-BASED PARSING

---

## NEW: Dump-Based Collection (500x Faster!)

## ✨ What's New in v3.0

**Revolutionary upgrade:** Instead of web scraping (96 days), download data dumps once and parse locally (~19 hours).

**🎯 Revolutionary Performance Upgrade: Dump-Based Parsing**

| Method | Time | Rate Limits | Completeness |

| Metric | v2 (Web Scraping) | v3 (Dump Parsing) | Improvement ||--------|------|-------------|--------------|

|--------|-------------------|-------------------|-------------|| Web Scraping (v2) | 96 days | Yes | Partial |

| **Collection Time** | 96 days | 19 hours | **120x faster** || Dump Parsing (v3) | 19 hours | No | 100% |

| **Total Items** | 1.1M | 1.66M | **50% more data** |

| **Rate Limits** | Frequent | None | **No limits** |**Speed improvement: 120x faster**

| **Data Coverage** | Partial | 100% | **Complete** |

| **Reproducibility** | Variable | Perfect | **Same dumps = same results** |---



**New Features:**## 🚀 Quick Start

- ✅ 11 dump-based parsers (vs 7 web scrapers)

- ✅ 7 new formal mathematics sources### Installation

- ✅ Offline processing after initial download

- ✅ 100% reproducible datasets```bash

- ✅ No API keys or rate limitsgit clone https://github.com/tejoker/BDDM.git

cd BDDM

---./install.sh

```

## 📊 Data Sources (v3)

### NEW: Dump-Based Collection (Recommended)

### Dump-Based Sources (Primary)

```bash

| Source | Type | Items | Quality | Time |# Step 1: Download all data dumps (~100GB, one-time)

|--------|------|-------|---------|------|./download_dumps.sh

| **Stack Exchange** | Q&A | 500k | ⭐⭐⭐ | 6h |

| **MathOverflow** | Research Q&A | 150k | ⭐⭐⭐⭐⭐ | 2h |# Step 2: Parse dumps locally (fast!)

| **ArXiv (Kaggle)** | Papers | 400k | ⭐⭐⭐⭐⭐ | 4h |./math/bin/python collect_dumps.py small    # ~10k items, 30 min

| **OEIS** | Sequences | 370k | ⭐⭐⭐⭐⭐ | 2h |./math/bin/python collect_dumps.py medium   # ~50k items, 2 hours

| **Lean Mathlib** | Formal theorems | 150k | ⭐⭐⭐⭐⭐ | 3h |./math/bin/python collect_dumps.py large    # ~200k items, 8 hours

| **Wikipedia** | Encyclopedia | 50k | ⭐⭐⭐⭐ | 1h |./math/bin/python collect_dumps.py max      # ~1.6M items, 19 hours

| **Metamath** | Formal proofs | 40k | ⭐⭐⭐⭐⭐ | 30m |```

| **Proof-Pile** | Mixed formal | 20k | ⭐⭐⭐⭐ | 1h |

| **Isabelle AFP** | Formal proofs | 10k+ | ⭐⭐⭐⭐⭐ | 2h |### Legacy: Web Scraping (Slower)

| **Coq** | Constructive proofs | 5k+ | ⭐⭐⭐⭐⭐ | 1h |

| **zbMATH Open** | Research metadata | 4M | ⭐⭐⭐⭐ | API |```bash

# Small test (~275 items, ~5 minutes)

### Legacy Web Scraping (Deprecated)./math/bin/python collect_samples.py 50 30 100 20 50 5 20



| Source | Type | Items | Quality | Status |# Medium collection (~2,550 items, ~3-5 hours)

|--------|------|-------|---------|--------|./math/bin/python collect_samples.py 1000 500 200 200 500 50 100

| ProofWiki | Formal proofs | 20k | ⭐⭐⭐⭐⭐ | Deprecated (use Proof-Pile) |

| nLab | Advanced math | 15k | ⭐⭐⭐⭐ | Deprecated |# Large collection (~28,500 items, ~30-40 hours)

| Project Euler | Competition | 956 | ⭐⭐⭐⭐ | Deprecated |./math/bin/python collect_samples.py 10000 5000 1000 1000 5000 1000 500

```

---

### ♻️ Resume Capability (NEW!)

## 🚀 Quick Start

**Collection stopped? No problem!** The pipeline now supports resumable collection:

### Installation

```bash

```bash# Start a long collection

git clone https://github.com/tejoker/BDDM.git./math/bin/python collect_samples.py all 10000

cd BDDM

./install.sh# If stopped (Ctrl+C, network issue, etc.), just resume:

```./math/bin/python collect_samples.py --resume



### Step 1: Download Data Dumps (One-Time)# Resume works with any mode:

./math/bin/python collect_samples.py --resume all 5000

```bash./math/bin/python collect_samples.py --resume max se

# Downloads ~100GB of data dumps (4-8 hours depending on connection)```

./download_dumps.sh

```**How it works:**

- 🔄 Checkpoint saved after every round

This downloads:- 💾 Tracks collected counts per source

- Stack Exchange XML dumps- 🚫 Duplicates prevented via content hash

- MathOverflow XML dumps- ✅ Automatic checkpoint cleanup on completion

- ArXiv Kaggle dataset- 📍 Resume from exact stopping point

- OEIS database

- Lean Mathlib git repository**Checkpoint location**: `samples_en/checkpoint.json`

- Metamath database

- Proof-Pile from HuggingFace---

- Isabelle AFP git repository

- Coq sources**Command format**: `SE PW Wiki nLab MO ArXiv_FULL Euler`



### Step 2: Parse Dumps**Flexible modes**:

- `all N`: Collect N items from each source

Choose a collection size:- `max SOURCE`: Collect maximum from single source (se/pw/wiki/nlab/mo/arxiv/euler)

- Selective: `1000 0 0 0 0 0 0` for Stack Exchange only

```bash- Resume: `--resume` flag continues from checkpoint

# Small test - 10k items in ~30 minutes

./math/bin/python collect_dumps.py small---



# Medium dataset - 50k items in ~2 hours## 📚 Data Sources

./math/bin/python collect_dumps.py medium

### Dump-Based Sources (Recommended - Fast)

# Large dataset - 200k items in ~8 hours

./math/bin/python collect_dumps.py large| Source | Type | Items Available | Quality | Collection Time |

|--------|------|----------------|---------|-----------------|

# Maximum dataset - 1.66M items in ~19 hours| **Stack Exchange** | Q&A | ~500,000 | 30-100/100 | 6 hours (dump) |

./math/bin/python collect_dumps.py max| **MathOverflow** | Research Q&A | ~150,000 | 50-100/100 | 2 hours (dump) |

```| **Wikipedia** | Encyclopedia | **10k-50k** | 85/100 | 1 hour (dump) |

| **ArXiv (Kaggle)** | Research papers | ~400,000 | 90/100 | 4 hours (dump) |

### Step 3: Use Your Data| **OEIS** | Integer sequences | **370,000** | 90/100 | 2 hours (dump) |

| **Lean Mathlib** | Formal theorems | **150,000** | 95/100 | 3 hours (git) |

```bash| **Metamath** | Formal proofs | **40,000** | 100/100 | 30 min (file) |

# Data is stored in samples_en/| **Isabelle AFP** | Formal proofs | **10,000+** | 95/100 | 2 hours (git) |

ls samples_en/raw/| **Coq** | Constructive proofs | **5,000+** | 95/100 | 1 hour (git) |

| **Proof-Pile** | Mixed formal | **20,000+** | 90/100 | 1 hour (HF) |

# Each source has its own directory with JSON batches| **zbMATH Open** | Metadata | **4M** | 80/100 | API-based |

cat samples_en/raw/stackexchange/batch_*.json | jq

```### Legacy Web Scraping Sources



---| Source | Type | Items Available | Quality | Speed |

|--------|------|----------------|---------|-------|

## 📋 Collection Presets| **ProofWiki** | Formal proofs | ~20,000 | 95/100 | Medium |

| **nLab** | Advanced math | ~15,000 | 85/100 | Medium |

### `small` - Quick Test (30 min, ~10k items)| **Project Euler** | Competition | **956** | 90/100 | Fast |

Perfect for testing all parsers and validating your setup.

### Source Details

```bash

./math/bin/python collect_dumps.py small**Stack Exchange** - Undergraduate to graduate level Q&A

```- Questions with accepted answers

- Score-filtered for quality

**Sources:**- Tags: proof-writing, logic, algebra, calculus, etc.

- Stack Exchange: 1,000- API key increases limit from 300 to 10,000 requests/day

- MathOverflow: 1,000

- Wikipedia: 500**ProofWiki** - Structured formal proofs

- ArXiv: 2,000- Theorem statement + complete proof

- OEIS: 3,000- Verified and peer-reviewed

- Lean: 1,000- Categories: Set theory, algebra, analysis, topology

- Metamath: 1,000

- Others: 500**Wikipedia** - General math encyclopedia with **CATEGORY GRAPH** 🌟

- **NEW**: Traverses Wikipedia's category tree to discover 10k-50k math articles!

### `medium` - Training Dataset (2h, ~50k items)- Uses BFS algorithm starting from "Category:Mathematics"

Good balance for initial model training.- Requires User-Agent header for API access

- Toggle modes: hardcoded (200+ topics) or category graph (comprehensive)

```bash

./math/bin/python collect_dumps.py medium**nLab** - Category theory & higher mathematics

```- Advanced topics: functors, monads, topoi, homotopy theory

- Rigorous definitions

**Sources:**- Graduate+ level content

- Stack Exchange: 10,000

- MathOverflow: 5,000**MathOverflow** - Research-level mathematics

- Wikipedia: 2,000- Expert Q&A

- ArXiv: 10,000- Advanced topics: algebraic geometry, number theory

- OEIS: 15,000- Professional mathematician community

- Lean: 5,000

- Metamath: 2,000**ArXiv FULL** - LaTeX source extraction

- Others: 1,000- Downloads full paper sources

- Extracts `\begin{theorem}...\begin{proof}` pairs

### `large` - Comprehensive Dataset (8h, ~200k items)- ~5 proofs per paper average

Comprehensive dataset for serious training.- 2MB per paper (deleted after extraction)



```bash**Project Euler** - Computational mathematics

./math/bin/python collect_dumps.py large- **956 competition problems** (updated 2025!)

```- Increasing difficulty levels

- Number theory, algorithms, optimization

**Sources:**- No anti-scraping protection ✅

- Stack Exchange: 50,000

- MathOverflow: 20,000**OEIS (Online Encyclopedia of Integer Sequences)** - NEW

- Wikipedia: 10,000- **370,000+ sequences** with formulas and patterns

- ArXiv: 50,000- Computational mathematics problems

- OEIS: 40,000- Cross-references and examples

- Lean: 20,000- Download: https://oeis.org/stripped.gz

- Metamath: 5,000

- Others: 5,000**Lean Mathlib** - NEW (Formal Mathematics)

- **150,000+ formalized theorems** in Lean 4

### `max` - Complete Dataset (19h, ~1.66M items)- Verified by proof assistant

Maximum dataset with all available data.- Areas: algebra, analysis, topology, number theory

- Clone: https://github.com/leanprover-community/mathlib4

```bash

./math/bin/python collect_dumps.py max**Metamath** - NEW (Foundational Mathematics)

```- **40,000+ theorems** from ZFC axioms

- Complete formal proofs

**Sources:**- Foundation of mathematics

- Stack Exchange: 500,000- Download: https://github.com/metamath/set.mm

- MathOverflow: 150,000

- Wikipedia: 50,000**Isabelle AFP** - NEW (Archive of Formal Proofs)

- ArXiv: 400,000- **700+ articles**, 3M+ lines of proof

- OEIS: 370,000- High-quality formal mathematics

- Lean: 150,000- Research-level formalization

- Metamath: 40,000- Clone: https://github.com/isabelle-prover/mirror-afp-devel

- Others: 20,000

**Coq** - NEW (Constructive Mathematics)

### Custom Configuration- Thousands of constructive proofs

- CompCert, Feit-Thompson, Four Color Theorem

```bash- Verified software and mathematics

# Specify exact counts for each source:- Clone: https://github.com/coq/coq

# Format: SE MO Wiki ArXiv OEIS ProofPile Lean Metamath Isabelle Coq zbMATH

./math/bin/python collect_dumps.py 1000 500 200 2000 5000 100 500 200 100 50 0**Proof-Pile** - NEW (HuggingFace Dataset)

```- Includes ProofWiki, Stacks Project, textbooks

- 8GB compressed

---- Mixed informal and formal proofs

- Download: https://huggingface.co/datasets/hoskinson-center/proof-pile

## 📁 Output Structure

**zbMATH Open** - NEW (Research Metadata)

```- 4M+ mathematical publications

samples_en/- Abstracts, citations, classifications

├── index.json              # Master index (duplicate tracking)- OAI-PMH and REST API access

├── checkpoint.json         # Resume checkpoint- API: https://zbmath.org/

└── raw/                    # Raw data by source

    ├── stackexchange/---

    │   ├── batch_20251103_120000.json

    │   └── batch_20251103_123000.json## ⚡ Performance Optimization

    ├── mathoverflow/

    ├── wikipedia/**Round-Robin Strategy**: Instead of collecting all items from one source then moving to the next, the collector uses a round-robin approach:

    ├── arxiv/

    ├── oeis/```

    ├── lean/ROUND 1: Fetch 80 from SE → 80 from MO → 50 from PW → ...

    ├── metamath/ROUND 2: Fetch 80 more from SE → 80 more from MO → ...

    ├── proofpile/```

    ├── isabelle/

    ├── coq/**Benefits**:

    └── zbmath/- ~40% faster collection

```- Maximizes API usage during rate limit cooldowns

- Never idle while waiting for limits to reset

### Data Format

---

**Q&A Format** (Stack Exchange, MathOverflow):

```json## 📊 Dataset Estimates

{

  "id": "se_12345",### Maximum Collection (Dump-Based - v3)

  "source": "stackexchange",- **Total items**: ~1.66M

  "title": "Prove by induction that...",- **Storage**: ~15-60 GB (JSON)

  "question": "Full question text...",- **Time**: ~19 hours (with dumps)

  "answer": "Full answer with proof...",

  "tags": ["induction", "proof-writing"],**Sources breakdown:**

  "score": 42,- Stack Exchange: 500k

  "url": "https://math.stackexchange.com/questions/12345"- MathOverflow: 150k

}- Wikipedia: 50k

```- ArXiv: 400k

- OEIS: 370k

**Theorem-Proof Format** (Lean, Metamath, Isabelle, Coq):- Lean Mathlib: 150k

```json- Metamath: 40k

{- Proof-Pile: 20k

  "id": "lean_12345",- Isabelle AFP: 10k

  "source": "lean_mathlib",- Coq: 5k

  "title": "theorem_name",- Others: 16k

  "theorem": "Statement of theorem...",

  "proof": "Complete formal proof...",### Legacy Maximum Collection (Web Scraping - v2)

  "file": "Mathlib/Algebra/Group/Basic.lean"- **Total items**: ~1.1M

}- **Storage**: ~12-52 GB (JSON)

```- **Time**: ~96 days continuous



**Paper Format** (ArXiv):### Recommended Collections (Dump-Based)

```json

{**Phase 1: Quick Test (30 minutes)**

  "id": "arxiv_1234.5678",```bash

  "source": "arxiv",./math/bin/python collect_dumps.py small

  "title": "Paper Title",```

  "abstract": "Paper abstract...",- ~10,000 items, ~1 GB

  "authors": ["Author 1", "Author 2"],- Test all parsers

  "categories": ["math.NT", "math.AG"],

  "url": "https://arxiv.org/abs/1234.5678"**Phase 2: Medium Dataset (2 hours)**

}```bash

```./math/bin/python collect_dumps.py medium

```

---- ~50,000 items, ~5 GB

- Good for initial training

## 🔧 Advanced Usage

**Phase 3: Large Dataset (8 hours)**

### Resume Collection```bash

./math/bin/python collect_dumps.py large

All collection scripts support resuming:```

- ~200,000 items, ~15 GB

```bash- Comprehensive training corpus

# Start collection

./math/bin/python collect_dumps.py large**Phase 4: Maximum Dataset (19 hours)**

```bash

# If interrupted (Ctrl+C, crash, etc.), resume:./math/bin/python collect_dumps.py max

./math/bin/python collect_dumps.py --resume```

```- ~1.66M items, ~60 GB

- Complete mathematical dataset

### Select Specific Sources

---

```bash

# Only parse Stack Exchange and MathOverflow:## 🔑 API Keys & Rate Limits

./math/bin/python collect_dumps.py se mo

### Stack Exchange / MathOverflow

# Only parse formal proof sources:

./math/bin/python collect_dumps.py lean mm isa coq pp**Without API key**: 300 requests/day  

```**With API key**: 10,000 requests/day



Source codes:**Getting a key** (takes 5 minutes):

- `se` - Stack Exchange1. Go to: https://stackapps.com/apps/oauth/register

- `mo` - MathOverflow2. Fill in:

- `wiki` - Wikipedia   - Application Name: Math Scraper

- `arxiv` - ArXiv   - Description: Educational math data collection

- `oeis` - OEIS   - Application Website: https://github.com/tejoker/BDDM

- `pp` - Proof-Pile3. Copy your API key

- `lean` - Lean Mathlib4. Set environment variable:

- `mm` - Metamath   ```bash

- `isa` - Isabelle AFP   echo "STACKEXCHANGE_API_KEY=your_key_here" > .env

- `coq` - Coq   ```

- `zbm` - zbMATH Open

### Rate Limit Error (HTTP 429)

---

If you see "Too many requests":

## 📊 Storage Requirements1. **Wait 30-60 minutes** (temporary block)

2. **Get an API key** (permanent solution)

| Collection | Items | Raw JSON | Compressed |3. **Collect from other sources** while waiting:

|------------|-------|----------|------------|   ```bash

| Small | 10k | ~1 GB | ~300 MB |   # Skip SE/MO, collect from others:

| Medium | 50k | ~5 GB | ~1.5 GB |   ./math/bin/python collect_samples.py 0 1000 500 1000 0 100 200

| Large | 200k | ~15 GB | ~4.5 GB |   ```

| Maximum | 1.66M | ~60 GB | ~18 GB |

---

**Dump storage:** ~100 GB (one-time download)

## 📁 Output Structure

---

```

## 🛠️ Legacy Web Scraping (Deprecated)samples_en/

├── raw/

For compatibility, web scraping is still available but **not recommended**:│   ├── stackexchange/

│   │   └── batch_*.json

```bash│   ├── proofwiki/

# Small test (~275 items, ~5 minutes)│   │   └── batch_*.json

./math/bin/python collect_samples.py 50 30 100 20 50 5 20│   ├── wikipedia/

│   ├── nlab/

# Medium collection (~2,550 items, ~3-5 hours)│   ├── mathoverflow/

./math/bin/python collect_samples.py 1000 500 200 200 500 50 100│   ├── arxiv_full/

```│   └── project_euler/

└── index.json

**Why deprecated:**```

- ❌ 120x slower than dump parsing

- ❌ Rate limits and API restrictions### Data Format

- ❌ Incomplete data coverage

- ❌ Network-dependentEach item has:

- ❌ Not reproducible```json

{

**Use dump parsing instead!**  "id": "se_12345",

  "source": "stackexchange",

---  "title": "Prove by induction that...",

  "question": "Full question text...",

## 📖 Documentation  "answer": "Full answer with proof...",

  "tags": ["induction", "proof-writing"],

- **[DUMP_MIGRATION_GUIDE.md](DUMP_MIGRATION_GUIDE.md)** - Complete v2→v3 migration guide  "score": 42,

- **[ARCHITECTURE.md](ARCHITECTURE.md)** - Code structure and technical details  "url": "https://...",

- **[CHANGELOG.md](CHANGELOG.md)** - Version history and breaking changes  "created_date": "2024-10-28T...",

  "metadata": {...}

---}

```

## 🐛 Troubleshooting

For formal proofs (ProofWiki, ArXiv FULL):

### "Dumps not found" error```json

```bash{

# Download dumps first:  "id": "pw_12345",

./download_dumps.sh  "source": "proofwiki",

```  "title": "Pythagorean Theorem",

  "theorem": "Statement of theorem...",

### Missing dependencies  "proof": "Complete formal proof...",

```bash  "categories": ["Geometry", "Algebra"],

./math/bin/pip install -r requirements.txt  "url": "https://..."

```}

```

### Out of disk space

- Free up space (dumps need ~100GB)---

- Use smaller collection preset

## 🛠️ Advanced Usage

### Parser crashes

- Check logs in `scraper.log`### Individual Source Testing

- Report issues: https://github.com/tejoker/BDDM/issues

```bash

---# Test a specific scraper

./math/bin/python -c "import asyncio; from scrapers.proofwiki_scraper import ProofWikiScraper; asyncio.run(ProofWikiScraper().scrape(max_items=5))"

## 📈 Performance Tips```



1. **Use SSD storage** - Significantly faster parsing### Wikipedia Category Graph Mode

2. **Parallel parsing** - Run multiple parsers simultaneously

3. **Compress output** - Use `gzip` to save 70% disk spaceBy default, Wikipedia uses category graph traversal to discover 10k-50k articles. To use hardcoded topics instead (200+ topics), modify `collect_samples.py`:

4. **Selective parsing** - Only parse sources you need

```python

---# In collect_samples.py, change:

wiki_scraper = WikipediaScraper(use_category_graph=False)  # Hardcoded mode

## 🤝 Contributing```



To add a new parser:---



1. Create `parsers/new_source_parser.py`## � Data Organization

2. Inherit from `BaseParser`

3. Implement `parse()` methodAll collected data is stored in `samples_en/` directory:

4. Add to `collect_dumps.py`

5. Update documentation```

samples_en/

See existing parsers for examples.├── index.json                    # Master index (duplicate tracking)

├── checkpoint.json               # Resume checkpoint (if interrupted)

---└── raw/                          # Raw collected data by source

    ├── stackexchange/

## 📝 License & Attribution    │   ├── batch_20250128_153945.json

    │   └── batch_20250128_165832.json

Data sources have different licenses:    ├── proofwiki/

- **Stack Exchange/MathOverflow**: CC BY-SA 4.0    │   └── batch_20250128_154120.json

- **Wikipedia**: CC BY-SA 3.0    ├── wikipedia/

- **ArXiv**: Various (check individual papers)    ├── nlab/

- **OEIS**: CC BY-SA 3.0    ├── mathoverflow/

- **Lean Mathlib**: Apache 2.0    ├── arxiv_full/

- **Metamath**: Public Domain    └── project_euler/

- **Isabelle AFP**: BSD License```

- **Coq**: LGPL

- **Proof-Pile**: Various### File Structure



Please respect licenses and provide proper attribution when using collected data.**`index.json`** - Master tracking file

```json

---{

  "items": {

## 📧 Support    "hash_abc123": {

      "source": "stackexchange",

- **Issues**: https://github.com/tejoker/BDDM/issues      "added_at": "2025-01-28T16:00:00"

- **Discussions**: https://github.com/tejoker/BDDM/discussions    }

- **Repository**: https://github.com/tejoker/BDDM  },

  "stats": {

---    "stackexchange": {

      "count": 500,

## 🎯 Use Cases      "files": ["batch_20250128_153945.json"]

    }

- **LLM Training**: Mathematical reasoning and proof generation  }

- **Theorem Provers**: Training data for automated theorem proving}

- **Education**: Mathematics problem banks and solutions```

- **Research**: Mathematical corpus analysis

- **Benchmark Creation**: Evaluation datasets for math AI**`batch_YYYYMMDD_HHMMSS.json`** - Data batch

```json

---[

  {

**Built with ❤️ for mathematical AI research**    "title": "Proving that square root of 2 is irrational",

    "question": "How do I prove...",

**Star ⭐ this repo if you find it useful!**    "answer": "Assume √2 is rational...",

    "score": 42,
    "tags": ["proof-writing", "number-theory"]
  }
]
```

**`checkpoint.json`** - Resume state
```json
{
  "session_id": "20250128_160000",
  "started_at": "2025-01-28T16:00:00",
  "last_updated": "2025-01-28T16:15:00",
  "round": 5,
  "sources": {
    "stack_exchange": {
      "collected": 400,
      "target": 1000,
      "page": 6
    }
  }
}
```

### Duplicate Detection

- **Method**: MD5 hash of content (question+answer or theorem+proof)
- **Tracking**: `index.json` stores all item hashes
- **Benefit**: Can resume without re-downloading same data
- **Cross-source**: Detects duplicates even across different sources

---

## �🐛 Troubleshooting

### "Too many requests" error
- **Cause**: Stack Exchange rate limit
- **Solution**: Wait 1 hour OR get API key (see above)

### "No module named 'pandas'"
- **Cause**: Missing dependencies
- **Solution**: `./math/bin/pip install -r requirements.txt`

### ArXiv downloads failing
- **Cause**: Network issues or ArXiv rate limiting
- **Solution**: Reduce batch size, add delays between requests

### Wikipedia 403 Forbidden
- **Cause**: Missing User-Agent header (already fixed in v2)
- **Solution**: Ensure you're using latest version with User-Agent header

### Out of memory
- **Cause**: Too many items in memory
- **Solution**: Reduce collection size, process in batches

---

## 📈 Quality Metrics

**Highest Quality** (Recommended for training):
1. ProofWiki: 95/100 - Verified formal proofs
2. ArXiv FULL: 90/100 - Published research proofs
3. Project Euler: 90/100 - Competition-level problems
4. MathOverflow: 50-100/100 - Expert answers
5. Stack Exchange: 30-100/100 - Score-filtered

**Medium Quality**:
6. nLab: 85/100 - Advanced but sometimes informal
7. Wikipedia: 85/100 - General but reliable

---

## 🔜 Next Steps

After collection:
1. **Clean data**: Use `utils/cleaner.py` to remove duplicates
2. **Split datasets**: Train/validation/test splits
3. **Export formats**: JSONL, LaTeX, or custom format
4. **Train models**: Use for mathematical reasoning, proof generation, etc.

---

## 📖 Documentation

- **QUICKSTART.md** - Step-by-step guide for first-time users
- **ARCHITECTURE.md** - Code structure and technical details
- **FULL_COLLECTION_ESTIMATES.md** - Detailed dataset sizing and timing
- **ANTI_SCRAPING_GUIDE.md** - Technical guide on anti-scraping techniques
- **OUTSMARTING_REALITY.md** - Cost-benefit analysis for bypassing anti-scraping

---

## 📝 License & Attribution

Data sources have different licenses:
- **Stack Exchange/MathOverflow**: CC BY-SA 4.0
- **ProofWiki**: CC BY-SA 3.0
- **Wikipedia**: CC BY-SA 3.0
- **ArXiv**: Open access (respect terms of use)
- **nLab**: MIT License
- **Project Euler**: Public (respect terms of use)

Please respect licenses and provide proper attribution when using collected data.

---

## 🤝 Contributing

To add a new source:
1. Create `scrapers/new_source_scraper.py`
2. Implement `async def scrape(self, max_items: int) -> List[Dict]`
3. Add to `scrapers/__init__.py`
4. Update `collect_samples.py`

---

## 💡 Tips

**For fastest collection**: Use dump-based parsing (120x faster than scraping)
**For highest quality**: Focus on Lean Mathlib, Metamath, Isabelle AFP (formal proofs)
**For largest volume**: Stack Exchange (500k), OEIS (370k), ArXiv (400k)
**For research level**: MathOverflow, ArXiv, zbMATH Open
**For formal mathematics**: Lean Mathlib, Metamath, Isabelle AFP, Coq
**For computational math**: OEIS (370k sequences), Project Euler (956 problems)

**Storage optimization**: Use gzip compression to reduce size by 70%

**Dump requirements**:
- ~100GB disk space for dumps
- 7zip for Stack Exchange/MathOverflow extraction
- Kaggle CLI for ArXiv dataset (optional)
- HuggingFace datasets library for Proof-Pile

---

## 📧 Support

- **Issues**: https://github.com/tejoker/BDDM/issues
- **Repository**: https://github.com/tejoker/BDDM

---

**Built with ❤️ for mathematical AI research**
