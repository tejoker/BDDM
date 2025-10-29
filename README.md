# BDDM - Math Dataset Builder

Large-scale mathematics dataset collection tool for theorem-proof pairs, Q&A, and formal mathematics content.

🎯 **Goal**: Collect 1.1M-1.6M mathematical items from 7 high-quality sources  
📊 **Data**: Theorems, proofs, Q&A pairs, research papers, competition problems  
🚀 **Status**: Production-ready v2 with round-robin optimization

---

## 🚀 Quick Start

### Installation

```bash
git clone https://github.com/tejoker/BDDM.git
cd BDDM
./install.sh
```

### Basic Usage

```bash
# Small test (~275 items, ~5 minutes)
./math/bin/python collect_samples.py 50 30 100 20 50 5 20

# Medium collection (~2,550 items, ~3-5 hours)
./math/bin/python collect_samples.py 1000 500 200 200 500 50 100

# Large collection (~28,500 items, ~30-40 hours)
./math/bin/python collect_samples.py 10000 5000 1000 1000 5000 1000 500

# Collect 1000 from ALL sources (flexible mode)
./math/bin/python collect_samples.py all 1000

# Collect MAXIMUM from a single source
./math/bin/python collect_samples.py max euler  # 956 problems
./math/bin/python collect_samples.py max wiki   # 10k-50k articles
```

### ♻️ Resume Capability (NEW!)

**Collection stopped? No problem!** The pipeline now supports resumable collection:

```bash
# Start a long collection
./math/bin/python collect_samples.py all 10000

# If stopped (Ctrl+C, network issue, etc.), just resume:
./math/bin/python collect_samples.py --resume

# Resume works with any mode:
./math/bin/python collect_samples.py --resume all 5000
./math/bin/python collect_samples.py --resume max se
```

**How it works:**
- 🔄 Checkpoint saved after every round
- 💾 Tracks collected counts per source
- 🚫 Duplicates prevented via content hash
- ✅ Automatic checkpoint cleanup on completion
- 📍 Resume from exact stopping point

**Checkpoint location**: `samples_en/checkpoint.json`

---

**Command format**: `SE PW Wiki nLab MO ArXiv_FULL Euler`

**Flexible modes**:
- `all N`: Collect N items from each source
- `max SOURCE`: Collect maximum from single source (se/pw/wiki/nlab/mo/arxiv/euler)
- Selective: `1000 0 0 0 0 0 0` for Stack Exchange only
- Resume: `--resume` flag continues from checkpoint

---

## 📚 Data Sources

| Source | Type | Items Available | Quality | Speed |
|--------|------|----------------|---------|-------|
| **Stack Exchange** | Q&A | ~500,000 | 30-100/100 | Fast |
| **ProofWiki** | Formal proofs | ~20,000 | 95/100 | Medium |
| **Wikipedia** | Encyclopedia | **10k-50k** | 85/100 | Fast |
| **nLab** | Advanced math | ~15,000 | 85/100 | Medium |
| **MathOverflow** | Research Q&A | ~50,000 | 50-100/100 | Fast |
| **ArXiv FULL** | Research proofs | ~500,000 | 90/100 | Slow |
| **Project Euler** | Competition | **956** | 90/100 | Fast |

### Source Details

**Stack Exchange** - Undergraduate to graduate level Q&A
- Questions with accepted answers
- Score-filtered for quality
- Tags: proof-writing, logic, algebra, calculus, etc.
- API key increases limit from 300 to 10,000 requests/day

**ProofWiki** - Structured formal proofs
- Theorem statement + complete proof
- Verified and peer-reviewed
- Categories: Set theory, algebra, analysis, topology

**Wikipedia** - General math encyclopedia with **CATEGORY GRAPH** 🌟
- **NEW**: Traverses Wikipedia's category tree to discover 10k-50k math articles!
- Uses BFS algorithm starting from "Category:Mathematics"
- Requires User-Agent header for API access
- Toggle modes: hardcoded (200+ topics) or category graph (comprehensive)

**nLab** - Category theory & higher mathematics
- Advanced topics: functors, monads, topoi, homotopy theory
- Rigorous definitions
- Graduate+ level content

**MathOverflow** - Research-level mathematics
- Expert Q&A
- Advanced topics: algebraic geometry, number theory
- Professional mathematician community

**ArXiv FULL** - LaTeX source extraction
- Downloads full paper sources
- Extracts `\begin{theorem}...\begin{proof}` pairs
- ~5 proofs per paper average
- 2MB per paper (deleted after extraction)

**Project Euler** - Computational mathematics
- **956 competition problems** (updated 2025!)
- Increasing difficulty levels
- Number theory, algorithms, optimization
- No anti-scraping protection ✅

---

## ⚡ Performance Optimization

**Round-Robin Strategy**: Instead of collecting all items from one source then moving to the next, the collector uses a round-robin approach:

```
ROUND 1: Fetch 80 from SE → 80 from MO → 50 from PW → ...
ROUND 2: Fetch 80 more from SE → 80 more from MO → ...
```

**Benefits**:
- ~40% faster collection
- Maximizes API usage during rate limit cooldowns
- Never idle while waiting for limits to reset

---

## 📊 Dataset Estimates

### Maximum Collection
- **Total items**: ~1,095,800-1,585,800
- **Storage**: ~12-52 GB (JSON)
- **Time**: ~960-980 hours (~40 days continuous)

### Recommended Collections

**Phase 1: Quality Core (1-2 days)**
```bash
./math/bin/python collect_samples.py 10000 5000 1000 1000 5000 1000 500
```
- ~28,500 items, ~2.7 GB
- High-quality diverse dataset

**Phase 2: Comprehensive (1 week)**
```bash
# Run "all" mode with higher counts
./math/bin/python collect_samples.py all 10000
```
- ~70,000 items, ~5-7 GB
- Substantial training corpus

**Phase 3: Maximum (1-2 months)**
- Requires batch processing and resume capability
- Full 1.1M-1.6M items
- See `FULL_COLLECTION_ESTIMATES.md` for details

---

## 🔑 API Keys & Rate Limits

### Stack Exchange / MathOverflow

**Without API key**: 300 requests/day  
**With API key**: 10,000 requests/day

**Getting a key** (takes 5 minutes):
1. Go to: https://stackapps.com/apps/oauth/register
2. Fill in:
   - Application Name: Math Scraper
   - Description: Educational math data collection
   - Application Website: https://github.com/tejoker/BDDM
3. Copy your API key
4. Set environment variable:
   ```bash
   echo "STACKEXCHANGE_API_KEY=your_key_here" > .env
   ```

### Rate Limit Error (HTTP 429)

If you see "Too many requests":
1. **Wait 30-60 minutes** (temporary block)
2. **Get an API key** (permanent solution)
3. **Collect from other sources** while waiting:
   ```bash
   # Skip SE/MO, collect from others:
   ./math/bin/python collect_samples.py 0 1000 500 1000 0 100 200
   ```

---

## 📁 Output Structure

```
samples_en/
├── raw/
│   ├── stackexchange/
│   │   └── batch_*.json
│   ├── proofwiki/
│   │   └── batch_*.json
│   ├── wikipedia/
│   ├── nlab/
│   ├── mathoverflow/
│   ├── arxiv_full/
│   └── project_euler/
└── index.json
```

### Data Format

Each item has:
```json
{
  "id": "se_12345",
  "source": "stackexchange",
  "title": "Prove by induction that...",
  "question": "Full question text...",
  "answer": "Full answer with proof...",
  "tags": ["induction", "proof-writing"],
  "score": 42,
  "url": "https://...",
  "created_date": "2024-10-28T...",
  "metadata": {...}
}
```

For formal proofs (ProofWiki, ArXiv FULL):
```json
{
  "id": "pw_12345",
  "source": "proofwiki",
  "title": "Pythagorean Theorem",
  "theorem": "Statement of theorem...",
  "proof": "Complete formal proof...",
  "categories": ["Geometry", "Algebra"],
  "url": "https://..."
}
```

---

## 🛠️ Advanced Usage

### Individual Source Testing

```bash
# Test a specific scraper
./math/bin/python -c "import asyncio; from scrapers.proofwiki_scraper import ProofWikiScraper; asyncio.run(ProofWikiScraper().scrape(max_items=5))"
```

### Wikipedia Category Graph Mode

By default, Wikipedia uses category graph traversal to discover 10k-50k articles. To use hardcoded topics instead (200+ topics), modify `collect_samples.py`:

```python
# In collect_samples.py, change:
wiki_scraper = WikipediaScraper(use_category_graph=False)  # Hardcoded mode
```

---

## � Data Organization

All collected data is stored in `samples_en/` directory:

```
samples_en/
├── index.json                    # Master index (duplicate tracking)
├── checkpoint.json               # Resume checkpoint (if interrupted)
└── raw/                          # Raw collected data by source
    ├── stackexchange/
    │   ├── batch_20250128_153945.json
    │   └── batch_20250128_165832.json
    ├── proofwiki/
    │   └── batch_20250128_154120.json
    ├── wikipedia/
    ├── nlab/
    ├── mathoverflow/
    ├── arxiv_full/
    └── project_euler/
```

### File Structure

**`index.json`** - Master tracking file
```json
{
  "items": {
    "hash_abc123": {
      "source": "stackexchange",
      "added_at": "2025-01-28T16:00:00"
    }
  },
  "stats": {
    "stackexchange": {
      "count": 500,
      "files": ["batch_20250128_153945.json"]
    }
  }
}
```

**`batch_YYYYMMDD_HHMMSS.json`** - Data batch
```json
[
  {
    "title": "Proving that square root of 2 is irrational",
    "question": "How do I prove...",
    "answer": "Assume √2 is rational...",
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

**For fastest collection**: Get API keys and use round-robin (automatic)  
**For highest quality**: Focus on ProofWiki, ArXiv FULL, and Project Euler  
**For largest volume**: Stack Exchange has 500k+ items  
**For research level**: MathOverflow and ArXiv FULL  
**For competition math**: Project Euler (956 problems) and Stack Exchange (with competition-math filter)

**Storage optimization**: Use gzip compression to reduce size by 70%

---

## 📧 Support

- **Issues**: https://github.com/tejoker/BDDM/issues
- **Repository**: https://github.com/tejoker/BDDM

---

**Built with ❤️ for mathematical AI research**
