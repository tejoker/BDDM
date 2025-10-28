# Math Scraper - Quick Usage Guide

## ✅ Setup Complete

All dependencies installed and scrapers tested successfully!

- ✓ Python 3.13 compatible
- ✓ All packages installed (pandas 2.3.3, numpy 2.3.4, lxml 6.0.2, etc.)
- ✓ Stack Exchange scraper working
- ✓ ProofWiki scraper working
- ✓ Tests passing (5/5)

## 🚀 Quick Start

### Collect Sample Data (Recommended)

```bash
# Activate virtualenv
source ./math/bin/activate

# Collect samples (Stack Exchange, ProofWiki, ArXiv)
python collect_samples.py 10 10 5
# Arguments: <stackexchange_count> <proofwiki_count> <arxiv_count>
```

### Run Tests

```bash
python test.py
```

### Run Individual Scrapers

```bash
# Stack Exchange only
python -c "import asyncio; from scrapers.stackexchange_scraper import StackExchangeScraper; asyncio.run(StackExchangeScraper().scrape(max_items=10))"

# ProofWiki only
python -c "import asyncio; from scrapers.proofwiki_scraper import ProofWikiScraper; asyncio.run(ProofWikiScraper().scrape(max_items=10))"
```

## 📊 What You Get

### Stack Exchange (English Q&A)
- **Questions** with accepted answers
- High-quality content (filtered by score > 50)
- Tags for categorization
- Full question text and answer text
- Metadata (score, views, etc.)

**Example:**
```json
{
  "id": "stackexchange_12345",
  "source": "stackexchange",
  "title": "Can every proof by contradiction also be shown without contradiction?",
  "question": "Are there some proofs that can only be shown...",
  "answer": "To determine what can and cannot be proved...",
  "tags": ["logic", "proof-writing", "proof-theory"],
  "score": 388
}
```

### ProofWiki (Theorems + Proofs)
- **Theorems** with formal proofs
- Mathematical statements with complete proofs
- LaTeX formatting preserved
- Category tags

**Example:**
```json
{
  "id": "proofwiki_abc123",
  "source": "proofwiki",
  "title": "*-Algebra Homomorphism between C*-Algebras is Norm-Decreasing",
  "theorem": "Let $\\struct {A, \\ast, \\norm {\\, \\cdot \\,}_A}$...",
  "proof": "FromSpectrum of Image of Element...",
  "tags": ["c*-algebras", "proven results"]
}
```

## 📁 Output Structure

```
samples_en/                    # English samples
├── raw/
│   ├── stackexchange/
│   │   └── batch_YYYYMMDD_HHMMSS.json
│   ├── proofwiki/
│   │   └── batch_YYYYMMDD_HHMMSS.json
│   └── arxiv/
│       └── batch_YYYYMMDD_HHMMSS.json
└── index.json                 # Master index
```

## 🎯 Current Focus: English Sources

The scraper currently focuses on **English** mathematical content:

- ✅ **Stack Exchange** (math.stackexchange.com) - Q&A with answers
- ✅ **ProofWiki** (proofwiki.org) - Theorems with proofs
- ✅ **ArXiv** (arxiv.org) - Research papers

French sources (Exo7, Bibmath) are **disabled** because:
- URLs have changed/moved
- Content structure changed
- No longer easily scrapable

You can add multilingual support later with other sources.

## 🔧 Configuration

Edit individual scrapers in `scrapers/` to customize:

- **Stack Exchange**: Change tags, score threshold, answer requirements
- **ProofWiki**: Change categories, theorem types
- **ArXiv**: Change search queries, date ranges

## 📊 Stats from Test Run

Latest collection (5 Stack Exchange + 5 ProofWiki):

**Stack Exchange:**
- All 5 have accepted answers ✓
- Average question length: ~660 chars
- Average answer length: ~2,200 chars
- Score range: 82-388

**ProofWiki:**
- 5 theorems collected
- Proof length range: 0-740 chars
- Various math topics (algebra, set theory, C*-algebras)

## 🚀 Next Steps

1. **Collect more data:**
   ```bash
   python collect_samples.py 50 50 10
   ```

2. **Process/clean data:**
   ```bash
   python analyze.py samples_en/
   ```

3. **Use in training:**
   - Data is ready in JSON format
   - Each item has question/answer or theorem/proof
   - Tags and metadata included

## 🐛 Troubleshooting

**If tests fail:**
```bash
# Check installed packages
pip list

# Reinstall requirements
pip install -r requirements.txt
```

**If scraping is slow:**
- Stack Exchange API has rate limits (30 requests/second)
- ProofWiki has no official API (uses web scraping, slower)
- Use smaller max_items for testing

## 📝 Notes

- ArXiv scraper may return 0 items depending on queries
- Some ProofWiki proofs may be empty stubs
- Stack Exchange answers are always present (filtered by accepted_answer=True)
- All scrapers respect rate limits and add delays
