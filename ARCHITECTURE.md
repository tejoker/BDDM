# BDDM Architecture v3.0

## Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     DATA SOURCES (v3)                        │
├──────────────┬──────────────┬───────────────┬───────────────┤
│ Stack        │ MathOverflow │ ArXiv         │ Wikipedia     │
│ Exchange     │              │ (Kaggle)      │ (Dumps)       │
│ 500k items   │ 150k items   │ 400k papers   │ 50k articles  │
├──────────────┼──────────────┼───────────────┼───────────────┤
│ OEIS         │ Lean Mathlib │ Metamath      │ Proof-Pile    │
│ 370k seqs    │ 150k thms    │ 40k proofs    │ 20k items     │
├──────────────┼──────────────┼───────────────┼───────────────┤
│ Isabelle AFP │ Coq          │ zbMATH Open   │               │
│ 10k+ proofs  │ 5k+ proofs   │ 4M metadata   │               │
└──────┬───────┴──────┬───────┴───────┬───────┴───────┬───────┘
       │              │               │               │
       v              v               v               v
┌─────────────────────────────────────────────────────────────┐
│                PARSERS (Dump-Based - Fast)                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ XML Parser   │  │ Git Parser   │  │ File Parser  │      │
│  │ - SE dumps   │  │ - Lean repo  │  │ - Metamath   │      │
│  │ - MO dumps   │  │ - Isabelle   │  │ - OEIS       │      │
│  │ - Wikipedia  │  │ - Coq repo   │  │ - HF dataset │      │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
└─────────┼──────────────────┼──────────────────┼─────────────┘
          │                  │                  │
          └──────────────────┴──────────────────┘
                             │
                             v
┌─────────────────────────────────────────────────────────────┐
│                     DATA CLEANER                             │
│  - HTML/LaTeX cleaning                                       │
│  - Text normalization                                        │
│  - Quality filtering                                         │
│  - Language detection                                        │
│  - Duplicate detection (content hash)                        │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              v
┌─────────────────────────────────────────────────────────────┐
│                     DATA STORAGE                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  raw/                                                │   │
│  │   ├── stackexchange/batch_*.json                    │   │
│  │   ├── mathoverflow/batch_*.json                     │   │
│  │   └── ... (11 sources total)                        │   │
│  ├─────────────────────────────────────────────────────┤   │
│  │  index.json (duplicate tracking)                    │   │
│  │  checkpoint.json (resume capability)                │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## Architecture Evolution: v2 vs v3

| Component | v2 (Web Scraping) | v3 (Dump Parsing) |
|-----------|-------------------|-------------------|
| Data Source | Live APIs/websites | Static dumps |
| Parsers | 7 web scrapers | 11 dump parsers |
| Speed | Rate-limited | CPU-limited |
| Collection Time | 96 days | 19 hours |
| Network Dependency | High | Low (after download) |
| Reproducibility | Variable | Perfect |

### Directory Structure Changes

**v2:**
```
math_scraper/
├── scrapers/
├── collect_samples.py
└── utils/
```

**v3:**
```
math_scraper/
├── parsers/
│   ├── base_parser.py
│   ├── stackexchange_dump_parser.py
│   └── ... (11 parsers)
├── scrapers/           # Legacy (deprecated)
├── collect_dumps.py
├── download_dumps.sh
└── utils/
```

## Components

### 1. Main Orchestrator (collect_dumps.py)

Coordinates all parsers and manages collection workflow.

Key features:
- Predefined collection presets (small, medium, large, max)
- Source selection (individual or all)
- Progress tracking and resumption
- Duplicate prevention

Usage:
```bash
# Predefined presets
./math/bin/python collect_dumps.py small
./math/bin/python collect_dumps.py medium

# Specific sources
./math/bin/python collect_dumps.py se mo wiki

# Resume interrupted collection
./math/bin/python collect_dumps.py --resume
```

### 2. Parsers

All parsers inherit from `BaseDumpParser`:

```python
class BaseDumpParser:
    def __init__(self, storage):
        self.storage = storage

    def parse(self, max_items: int = None) -> List[Dict]:
        """Parse dump and return items"""
        raise NotImplementedError

    def clean_item(self, item: Dict) -> Dict:
        """Clean and normalize item"""
        return item
```

#### XML-Based Parsers

**StackExchangeDumpParser**
- Input: Posts.xml from Stack Exchange data dump
- Output: Q&A pairs with accepted answers
- Challenge: Large XML files, requires streaming parser

**MathOverflowDumpParser**
- Input: mathoverflow.net/Posts.xml
- Output: Research-level Q&A pairs

**WikipediaDumpParser**
- Input: enwiki-latest-pages-articles.xml.bz2
- Output: Math articles filtered by category
- Features: Category filtering, redirect resolution

#### Git Repository Parsers

**LeanMathlibParser**
- Input: Lean 4 Mathlib git repository
- Output: Formal theorems with verified proofs
- Challenge: Lean syntax parsing, dependency resolution

**IsabelleAFPParser**
- Input: Archive of Formal Proofs (AFP)
- Output: Peer-reviewed formal proofs

**CoqParser**
- Input: Coq standard library
- Output: Constructive proofs

#### File-Based Parsers

**MetamathParser**
- Input: set.mm single file (~40MB)
- Output: 40k+ formal proofs from ZFC axioms

**OEISParser**
- Input: stripped.gz from OEIS
- Output: 370k integer sequences with formulas

**ArxivKaggleParser**
- Input: ArXiv dataset from Kaggle
- Output: Research papers with abstracts
- Size: 400k papers, ~80GB

#### API-Based Parsers

**ProofPileParser**
- Input: HuggingFace proof-pile dataset
- Output: Mixed formal/informal proofs

**zbMATHParser**
- Input: zbMATH Open API
- Output: Research metadata (4M+ publications)

### 3. Data Storage (utils/storage.py)

Features:
- Duplicate detection via content hashing
- Incremental batch saving
- Master index management
- Checkpoint/resume capability

Storage structure:
```
samples_en/
├── index.json
│   {
│     "items": {
│       "hash_abc123": {
│         "source": "stackexchange",
│         "added_at": "2025-11-03T12:00:00"
│       }
│     }
│   }
├── checkpoint.json
└── raw/
    ├── stackexchange/
    │   ├── batch_20251103_120000.json
    │   └── batch_20251103_123000.json
    └── ...
```

### 4. Data Cleaner (utils/cleaner.py)

Cleaning pipeline:
```
Raw text
  ↓ Decode HTML entities
  ↓ Preserve LaTeX formulas
  ↓ Remove HTML/XML tags
  ↓ Normalize whitespace
  ↓ Restore LaTeX formulas
  ↓ Quality validation
  ↓
Clean text
```

Quality filters:
- Length constraints (min/max)
- Math content presence
- Character distribution validation
- Spam detection

Enrichment:
- Language detection (EN/FR)
- Proof structure extraction
- Type identification: induction, contradiction, direct
- Technique detection: factorization, substitution, etc.

### 5. Download Manager (download_dumps.sh)

One-time download script for all data dumps (~100GB total).

Sources:
- Stack Exchange (15 GB)
- MathOverflow (2 GB)
- Wikipedia (20 GB)
- ArXiv Kaggle (80 GB)
- OEIS (100 MB)
- Lean Mathlib (500 MB)
- Metamath (40 MB)
- Proof-Pile (8 GB)
- Isabelle AFP (200 MB)
- Coq (150 MB)

Download time: 4-8 hours depending on connection

## Performance

### v2 vs v3 Comparison

Collection speed:
- v2: Limited by API rate limits (~10 req/min)
- v3: Limited by CPU/disk I/O (~1000 items/sec)

Bottlenecks:
- v2: Network requests, rate limits, anti-scraping
- v3: XML parsing, disk I/O

Memory usage:
- v2: ~100 MB (small batches)
- v3: ~500 MB - 2 GB (larger dumps)

CPU usage:
- v2: Low (mostly waiting)
- v3: High (parsing, cleaning, hashing)

### Optimization Techniques

XML parsing:
- Use SAX streaming instead of DOM
- Process elements incrementally
- Clear memory after each item

Git repositories:
- Shallow clone (--depth 1)
- Parallel file processing
- Skip non-essential files

Duplicate detection:
- MD5 hash of content
- In-memory hash set for session
- Index file for persistence

Checkpointing:
- Save state after each batch
- Resume from exact position
- Atomic file writes

## Extensibility

### Adding a New Parser

1. Create parser file: `parsers/new_source_parser.py`

```python
from parsers.base_parser import BaseDumpParser

class NewSourceParser(BaseDumpParser):
    def __init__(self, storage):
        super().__init__(storage)
        self.source_name = "new_source"

    def parse(self, max_items: int = None) -> List[Dict]:
        items = []
        # Your parsing logic here
        for item in extracted_items:
            if len(items) >= max_items:
                break
            cleaned = self.clean_item(item)
            if self.storage.add_item(cleaned):
                items.append(cleaned)
        return items

    def clean_item(self, item: Dict) -> Dict:
        # Custom cleaning logic
        return item
```

2. Register in `parsers/__init__.py`
3. Add to `collect_dumps.py` PARSERS dict
4. Update documentation

### Adding a New Cleaning Rule

Modify `utils/cleaner.py`:

```python
def clean_text(text: str) -> str:
    # Existing cleaning...
    text = your_new_cleaning_function(text)
    return text
```

## Data Quality

Target metrics (v3):
- 1.66M+ items (vs 1.1M in v2)
- 11 diverse sources (vs 7 in v2)
- 90%+ with mathematical content
- Formal proofs from 5 proof assistants
- 100% reproducible
- No duplicates

Quality by source:
- Formal proofs (Lean, Metamath, Isabelle, Coq): 95-100/100
- Research (ArXiv, zbMATH): 90/100
- Q&A (Stack Exchange, MathOverflow): 70-100/100
- Sequences (OEIS): 90/100
- Encyclopedia (Wikipedia): 85/100

## Migration from v2

Backward compatibility:
- `collect_samples.py` still works (deprecated)
- Same JSON structure
- Same directory structure (`samples_en/`)
- Can use both v2 and v3 together

Migration recommended because:
- 120x faster
- 50% more data
- No rate limits
- Better reproducibility

See DUMP_MIGRATION_GUIDE.md for details.

## Technical Stack

Languages: Python 3.8+

Key libraries:
- lxml - Fast XML parsing (SAX)
- BeautifulSoup4 - HTML parsing
- datasets - HuggingFace datasets
- gitpython - Git repository access
- requests - HTTP requests
- tqdm - Progress bars

Tools:
- 7zip - Archive extraction
- bzip2 - Compression
- git - Repository cloning
- kaggle - Kaggle dataset download

## Future Improvements

Potential additions:
- Multi-process parsing for faster collection
- Streaming processing without full download
- Incremental updates (only new items)
- ML-based quality scoring
- Cross-source deduplication
- Export to Parquet, HuggingFace datasets

Parser enhancements:
- LaTeX normalization
- Proof step extraction
- Cross-reference linking
- Metadata enrichment
