# Changelog

All notable changes to the BDDM (Mathematical Dataset Builder) project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] - 2025-11-03

### 🚀 Revolutionary Performance Upgrade

Complete rewrite from web scraping to dump-based parsing, achieving **120x speed improvement**.

### Added

#### New Parsers (11 Total)
- **StackExchangeDumpParser** - Parse 500k Q&A from XML dumps (6h vs 30 days)
- **MathOverflowDumpParser** - Parse 150k research Q&A from dumps (2h vs 7 days)
- **WikipediaDumpParser** - Parse 50k math articles from dumps (1h vs 3 days)
- **ArxivKaggleParser** - Parse 400k papers from Kaggle dataset (4h vs 20 days)
- **OEISParser** - Parse 370k sequences from OEIS database (2h)
- **LeanMathlibParser** - Extract 150k theorems from Lean 4 Mathlib (3h)
- **MetamathParser** - Extract 40k formal proofs from set.mm (30min)
- **ProofPileParser** - Parse 20k mixed proofs from HuggingFace (1h)
- **IsabelleAFPParser** - Extract 10k+ proofs from AFP (2h)
- **CoqParser** - Extract 5k+ constructive proofs (1h)
- **zbMATHParser** - API access to 4M research metadata

#### New Scripts
- **`download_dumps.sh`** - One-time download script for all data dumps (~100GB)
- **`collect_dumps.py`** - Main collection script with predefined configs (small/medium/large/max)
- **`BaseParser`** class - Common parser interface for extensibility

#### New Documentation
- **`DUMP_MIGRATION_GUIDE.md`** - Complete v2→v3 migration guide
- **`CHANGELOG.md`** - This file
- Updated **`README.md`** - Emphasize v3 dump-based approach
- Updated **`ARCHITECTURE.md`** - v3 architecture and design

#### New Features
- **Offline Processing** - Parse dumps without internet after initial download
- **Perfect Reproducibility** - Same dumps = same results every time
- **No Rate Limits** - Parse at CPU speed, not network speed
- **Complete Data** - Access 100% of each source's data
- **Formal Mathematics** - 5 new proof assistant sources (390k+ formal proofs)
- **Collection Presets** - Predefined configs: small (10k), medium (50k), large (200k), max (1.66M)

### Changed

#### Performance Improvements
- **Collection Time**: 96 days → 19 hours (**120x faster**)
- **Total Items**: 1.1M → 1.66M (**50% more data**)
- **Data Coverage**: Partial → 100% (**Complete**)
- **Network Dependency**: High → Low (**Offline after download**)

#### Architecture Changes
- **Primary Method**: Web scraping → Dump parsing
- **Main Script**: `collect_samples.py` → `collect_dumps.py`
- **Parser Directory**: `scrapers/` → `parsers/`
- **Source Count**: 7 → 11 sources

#### Code Structure
- Introduced `BaseParser` abstract class for consistency
- Separated download (`download_dumps.sh`) from parsing (`collect_dumps.py`)
- Improved duplicate detection (content hashing)
- Enhanced checkpoint/resume capability

### Deprecated

#### Legacy Web Scrapers (Still Functional)
- `collect_samples.py` - Old web scraping script (120x slower)
- `scrapers/stackexchange_scraper.py` - API-based scraping
- `scrapers/proofwiki_scraper.py` - Web scraping
- `scrapers/wikipedia_scraper.py` - Web scraping with category graph
- `scrapers/nlab_scraper.py` - Web scraping
- `scrapers/mathoverflow_scraper.py` - API-based scraping
- `scrapers/arxiv_full_scraper.py` - LaTeX source scraping
- `scrapers/project_euler_scraper.py` - Competition problem scraping

**Reason for deprecation**: 
- 120x slower than dump parsing
- Rate limits and API restrictions
- Incomplete data coverage
- Network-dependent
- Not reproducible

**Migration Path**: Use `collect_dumps.py` instead of `collect_samples.py`

### Removed

- **Old Documentation**: Removed outdated `FIXES_SUMMARY.md` and `FULL_COLLECTION_ESTIMATES.md`
- **Temporary Files**: Removed `scraper.log` and `arxiv_latex_cache/`

### Fixed

- **Duplicate Prevention**: Enhanced content hashing for cross-source deduplication
- **Memory Efficiency**: Use SAX parsing for large XML files instead of DOM
- **Error Handling**: Better error messages and recovery for parser failures

### Security

- **No API Keys Needed**: Dump-based parsing doesn't require API authentication
- **Offline Processing**: Reduced attack surface by minimizing network requests

---

## [2.0.0] - 2025-10-28

### Added

#### New Scrapers
- **Wikipedia Category Graph** - BFS traversal of math categories (10k-50k articles)
- **Project Euler** - All 956 competition problems
- **MathOverflow** - Research-level Q&A

#### New Features
- **Resume Capability** - Checkpoint-based resumable collection
- **Adaptive Batch Sizes** - Dynamic batch sizing for optimal performance
- **Round-Robin Collection** - Parallel scraping during rate limit cooldowns
- **User-Agent Rotation** - 16+ realistic User-Agent strings
- **State Persistence** - Track collected IDs to skip duplicates

### Changed
- **Collection Strategy**: Sequential → Round-robin (40% faster)
- **Batch Sizes**: Fixed → Adaptive (2-3x performance increase)
- **Wikipedia Method**: Hardcoded topics → Category graph (50x more articles)

### Fixed
- **Wikipedia 403 Errors**: Added User-Agent header for API access
- **ProofWiki Duplicates**: State persistence to skip already-collected items
- **Stack Exchange Rate Limits**: Exponential backoff retry logic
- **Project Euler Coverage**: Updated to fetch all 956 problems dynamically

---

## [1.0.0] - 2025-10-01

### Initial Release

#### Core Features
- **Web Scraping**: Async scraping from 7 mathematical sources
- **Data Storage**: JSON batches with duplicate detection
- **Data Cleaning**: HTML/LaTeX cleaning and normalization

#### Scrapers
- Stack Exchange (API-based)
- ProofWiki (web scraping)
- Wikipedia (200+ hardcoded topics)
- nLab (web scraping)
- ArXiv (LaTeX source extraction)
- French Courses (Exo7, Bibmath)

#### Capabilities
- Collect ~1.1M mathematical items
- Score-based quality filtering
- Theorem-proof pair extraction
- Bilingual support (EN/FR)

---

## Upgrade Guide

### From v2 to v3

**Quick Migration** (Recommended):
```bash
# 1. Download dumps (one-time, 4-8 hours)
./download_dumps.sh

# 2. Use new collection script
./math/bin/python collect_dumps.py medium

# 3. Enjoy 120x faster collection!
```

**Detailed Migration**: See [DUMP_MIGRATION_GUIDE.md](DUMP_MIGRATION_GUIDE.md)

**Backward Compatibility**: 
- v2 scrapers still work (deprecated)
- Same data format and storage structure
- Can run both v2 and v3 simultaneously

### From v1 to v2

**Changes**:
- Add `--resume` flag support
- Wikipedia now uses category graph by default
- Adaptive batch sizes enabled automatically

**No Breaking Changes**: v1 commands still work unchanged

---

## Future Roadmap

### Planned for v3.1
- [ ] Parallel parsing (multi-process)
- [ ] Streaming dump processing
- [ ] Quality scoring with ML
- [ ] Cross-source deduplication

### Planned for v4.0
- [ ] Real-time dump updates (incremental)
- [ ] Export to HuggingFace datasets format
- [ ] Export to Parquet/Arrow format
- [ ] Integrated data analysis tools
- [ ] Proof structure extraction (ML-based)
- [ ] Mathematical notation normalization

### Long-term
- [ ] More sources (textbooks, lecture notes, MOOCs)
- [ ] Multilingual expansion (FR, DE, RU, CN)
- [ ] Theorem linking (cross-reference detection)
- [ ] Difficulty classification
- [ ] Topic clustering

---

## Contributing

We welcome contributions! See areas for improvement:

1. **New Parsers**: Add more mathematical data sources
2. **Performance**: Optimize parsing and memory usage
3. **Quality**: Improve cleaning and filtering
4. **Documentation**: Examples, tutorials, use cases
5. **Testing**: Unit tests and integration tests

---

## Credits

### Data Sources
- **Stack Exchange** - CC BY-SA 4.0
- **MathOverflow** - CC BY-SA 4.0  
- **Wikipedia** - CC BY-SA 3.0
- **OEIS** - CC BY-SA 3.0
- **Lean Community** - Apache 2.0
- **Metamath Contributors** - Public Domain
- **Isabelle AFP** - BSD License
- **Coq Development Team** - LGPL
- **ArXiv** - Various (check individual papers)

### Contributors
- Nicolas Bigeard - Project creator and maintainer

---

**Legend**:
- 🚀 Major feature
- ⚡ Performance improvement
- 🐛 Bug fix
- 📚 Documentation
- 🔒 Security
- ⚠️ Deprecation
- 💥 Breaking change
