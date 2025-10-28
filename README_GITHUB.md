# BDDM - Base de Données Mathématiques (Mathematical Database)

A comprehensive data collection system for scraping mathematical content from multiple sources: Stack Exchange, ProofWiki, Wikipedia, nLab, MathOverflow, and ArXiv (with full LaTeX proof extraction).

## 🎯 Purpose

Collect high-quality mathematical content for training AI models, including:
- ✏️ Q&A pairs with solutions
- 📐 Formal theorem-proof pairs
- 📖 Encyclopedia articles
- 🔬 Research paper proofs (extracted from LaTeX)

## ✨ Features

- **6 Data Sources**: Stack Exchange, ProofWiki, Wikipedia, nLab, MathOverflow, ArXiv FULL
- **ArXiv FULL LaTeX Extraction**: Downloads LaTeX sources and extracts actual theorem-proof pairs
- **Unified Collection**: One command to collect from all sources
- **Data Normalization**: Convert all formats to unified schema with quality scoring
- **Async Scraping**: Fast parallel collection
- **Quality Scoring**: 0-100 scores based on community metrics

## 📊 Data Sources

| Source | Content Type | Quality | Items Available |
|--------|-------------|---------|-----------------|
| ProofWiki | Theorem-proof pairs | 95/100 | ~20,000 |
| ArXiv FULL | Research paper proofs | 90/100 | ~500,000 |
| nLab | Category theory articles | 85/100 | ~15,000 |
| Wikipedia | Math encyclopedia | 85/100 | ~22+ |
| MathOverflow | Research Q&A | 50-100 | ~50,000 |
| Stack Exchange | General Q&A | 30-100 | ~500,000 |

## 🚀 Quick Start

### Installation

```bash
# Clone repository
git clone https://github.com/YOUR_USERNAME/BDDM.git
cd BDDM

# Run installation script
chmod +x install.sh
./install.sh
```

This creates a virtual environment and installs all dependencies.

### Basic Usage

```bash
# Activate virtual environment
source math/bin/activate  # Linux/Mac
# or
math\Scripts\activate  # Windows

# Collect samples: SE PW Wiki nLab MO ArXiv_FULL
python collect_samples.py 20 20 22 20 20 10

# Normalize collected data
python normalize_data.py

# Analyze results
python analyze.py
```

## 📋 Collection Command

```bash
./math/bin/python collect_samples.py SE PW Wiki nLab MO ArXiv_FULL
```

### Parameters:
1. **SE** - Stack Exchange Q&A count
2. **PW** - ProofWiki theorems count
3. **Wiki** - Wikipedia articles count (max 22 with current topics)
4. **nLab** - nLab articles count
5. **MO** - MathOverflow Q&A count
6. **ArXiv_FULL** - ArXiv papers to download and extract proofs from

### Examples:

**Quick test (30 seconds):**
```bash
python collect_samples.py 5 5 5 5 5 2
```

**Medium collection (2-3 minutes):**
```bash
python collect_samples.py 20 20 22 20 20 10
# Output: ~130 items including ~50 ArXiv FULL proofs
```

**Large collection (30-40 hours):**
```bash
python collect_samples.py 10000 5000 22 1000 5000 1000
# Output: ~27,000 items including ~5,000 ArXiv FULL proofs
```

## 🔬 ArXiv FULL - LaTeX Proof Extraction

The ArXiv FULL scraper downloads actual LaTeX sources and extracts theorem-proof pairs:

- Downloads `.tar.gz` files from ArXiv
- Extracts `\begin{theorem}...\begin{proof}` patterns
- Also finds lemmas, propositions, corollaries
- ~5 proofs per paper on average (varies 0-20)
- ~5 seconds per paper (download + parse)

**Example extracted proof:**
```json
{
  "title": "Cauchy-Schwarz Inequality",
  "theorem": "\\begin{theorem}For all vectors u,v...\\end{theorem}",
  "proof": "\\begin{proof}By the triangle inequality...\\end{proof}",
  "arxiv_id": "2301.12345",
  "quality_score": 90
}
```

## 📦 Data Format

### Raw Data
Stored in `samples_en/raw/{source}/batch_*.json`

### Normalized Data
After running `normalize_data.py`, unified format in `samples_en/normalized/unified_data.jsonl`:

```json
{
  "id": "stackexchange_12345",
  "source": "stackexchange",
  "type": "qa",
  "theorem_name": "Cauchy-Schwarz Inequality",
  "title": "How to prove Cauchy-Schwarz?",
  "statement": "Question text...",
  "solution": "Answer text...",
  "tags": ["linear-algebra", "inequality"],
  "difficulty": "undergraduate",
  "domain": "algebra",
  "quality_score": 85,
  "extras": {...}
}
```

## 📈 Data Normalization

```bash
python normalize_data.py
```

Features:
- **Unified schema** across all sources
- **Theorem name extraction** (e.g., "Lebesgue Dominated Convergence Theorem")
- **Domain classification** (algebra, analysis, topology, etc.)
- **Difficulty assessment** (undergraduate, graduate, research)
- **Quality scoring** (0-100 based on source and metrics)

## 📊 Estimates for Full Collection

| Phase | Items | Storage | Time | ArXiv Proofs |
|-------|-------|---------|------|--------------|
| Quick test | ~180 | ~10 MB | ~10 min | ~25 |
| Medium | ~2,500 | ~150 MB | ~5 hours | ~250 |
| Large | ~27,000 | ~2.5 GB | ~40 hours | ~5,000 |
| Maximum | ~1,185,000 | ~12-52 GB | ~40 days | ~500,000 |

See `FULL_COLLECTION_ESTIMATES.md` for detailed breakdown.

## 🛠️ Project Structure

```
BDDM/
├── collect_samples.py          # Main collection script
├── normalize_data.py           # Data normalization
├── analyze.py                  # Data analysis
├── requirements.txt            # Python dependencies
├── install.sh                  # Installation script
├── scrapers/
│   ├── stackexchange_scraper.py
│   ├── proofwiki_scraper.py
│   ├── wikipedia_scraper.py
│   ├── nlab_scraper.py
│   ├── mathoverflow_scraper.py
│   └── arxiv_full_scraper.py   # LaTeX proof extractor
├── utils/
│   ├── storage.py              # Data storage utilities
│   └── cleaner.py              # Text cleaning
└── samples_en/                 # Output directory (git-ignored)
    ├── raw/                    # Source-specific formats
    └── normalized/             # Unified format
```

## 📚 Documentation

- **START_HERE.md** - Getting started guide
- **QUICKSTART.md** - Quick reference
- **ARCHITECTURE.md** - System design
- **COLLECTION_UNIFIED.md** - Collection guide
- **DATA_FORMAT_GUIDE.md** - Data format reference
- **ARXIV_FULL_GUIDE.md** - ArXiv extraction details
- **FULL_COLLECTION_ESTIMATES.md** - Storage/time estimates
- **LEGACY_CLEANUP.md** - Recent changes

## 🔧 Requirements

- Python 3.13+
- Libraries: pandas, numpy, lxml, beautifulsoup4, aiohttp, requests
- ~200 MB for virtual environment
- Storage: varies by collection size (10 MB - 52 GB)

## ⚠️ Rate Limits

- **Stack Exchange**: 10,000 requests/day
- **MathOverflow**: 10,000 requests/day
- **Wikipedia**: 200 requests/second
- **ArXiv**: 1 request/3 seconds recommended
- **ProofWiki**: No official limit (be respectful)
- **nLab**: No official limit (be respectful)

## 🎯 Use Cases

- **AI Training**: Train mathematical reasoning models
- **Research**: Analyze mathematical content patterns
- **Education**: Create study materials and problem sets
- **Validation**: Test theorem-proving systems
- **Discovery**: Find connections between mathematical concepts

## 🤝 Contributing

Contributions welcome! Areas for improvement:
- Add more data sources
- Improve theorem name extraction
- Better domain classification
- Additional language support (French courses scraper exists)
- Resume capability for large collections

## 📄 License

This project is for educational and research purposes. Please respect the terms of service of each data source:
- Stack Exchange: [CC BY-SA](https://creativecommons.org/licenses/by-sa/4.0/)
- ProofWiki: [CC BY-SA](https://creativecommons.org/licenses/by-sa/3.0/)
- Wikipedia: [CC BY-SA](https://creativecommons.org/licenses/by-sa/3.0/)
- nLab: [CC BY-SA](https://creativecommons.org/licenses/by-sa/4.0/)
- ArXiv: [Various open licenses](https://arxiv.org/help/license)

## 🙏 Acknowledgments

Data sources:
- [Stack Exchange Mathematics](https://math.stackexchange.com/)
- [MathOverflow](https://mathoverflow.net/)
- [ProofWiki](https://proofwiki.org/)
- [nLab](https://ncatlab.org/)
- [Wikipedia Mathematics Portal](https://en.wikipedia.org/wiki/Portal:Mathematics)
- [ArXiv Mathematics](https://arxiv.org/archive/math)

## 📧 Contact

For questions or issues, please open a GitHub issue.

---

**Built for researchers who need ACTUAL mathematical proofs, not just metadata! 🎉**
