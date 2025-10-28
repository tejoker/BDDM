# Full Collection Estimates

## Summary

If you fetch **everything available** from all sources:

### 📊 Total Counts
- **~1,185,000 items** total
- **~12-52 GB** storage (JSON format)
- **~20-80 GB** if exported to LaTeX

---

## 📚 By Content Type

### ✏️ Exercises with Solutions (Q&A Format)
**~550,000 items** | **~1.9 GB**

- **Stack Exchange**: ~500,000 Q&A pairs
  - Questions with accepted answers
  - Filtered by score (quality control)
  - Tags: proof-writing, logic, algebra, calculus, etc.
  - Varying difficulty: undergraduate to graduate level

- **MathOverflow**: ~50,000 Q&A pairs
  - Research-level questions
  - Expert answers
  - Advanced topics: algebraic geometry, number theory, etc.
  - Graduate to professional level

### 📐 Formal Proofs
**~20,000 items** | **~15 MB**

- **ProofWiki**: ~20,000 theorem-proof pairs
  - Highly structured format
  - Theorem statement + formal proof
  - Categories: Set theory, algebra, analysis, topology, etc.
  - Quality: Verified and peer-reviewed

### 📖 Encyclopedia & Explanatory Content
**~15,000 items** | **~20 MB**

- **nLab**: ~15,000 articles
  - Category theory and higher mathematics
  - Definitions, examples, properties
  - Advanced/graduate level
  - Topics: Functors, monads, topoi, homotopy theory, etc.

- **Wikipedia**: ~22 articles
  - Limited by hardcoded TOPICS list in scraper
  - Could be expanded by modifying the list
  - General math concepts and introductions

### 🔬 Research Papers (FULL LaTeX Proofs)
**~500,000 items** | **~10-50 GB**

- **ArXiv FULL**: ~100,000 math papers → ~500,000 theorem-proof pairs
  - **Downloads full LaTeX sources** and extracts theorem-proof pairs
  - **Success rate**: ~5 proofs per paper on average (varies 0-20)
  - **Extraction**: Uses regex to find `\begin{theorem}...\begin{proof}` patterns
  - **Storage**: ~2 MB per paper (LaTeX source), deleted after extraction
  - **Time**: ~5 seconds per paper (download + parse)
  - **Categories**: math.LO, math.CT, math.AG, math.NT, math.CO, math.GR, math.RA, etc.
  - **Quality**: 90/100 (published research papers)
  - **Why this approach?**
    - YOU wanted actual proofs, not just metadata!
    - Extracts real mathematical demonstrations
    - High-quality formal proofs from research papers

---

## 💾 Storage Breakdown by Source

| Source | Items | Size per Item | Total Size | Time to Collect* |
|--------|------:|-------------:|-----------:|----------------:|
| Stack Exchange | 500,000 | ~3.3 KB | ~1.6 GB | ~140 hours |
| MathOverflow | 50,000 | ~6.9 KB | ~328 MB | ~35 hours |
| ProofWiki | 20,000 | ~0.8 KB | ~15 MB | ~55 hours |
| nLab | 15,000 | ~1.4 KB | ~20 MB | ~21 hours |
| **ArXiv FULL** | **500,000** | **~20-100 KB** | **~10-50 GB** | **~700 hours** |
| Wikipedia | 22 | ~1.7 KB | ~0.03 MB | <1 minute |
| **TOTAL** | **~1,185,022** | — | **~12-52 GB** | **~950 hours** |

*Estimated with rate limiting and API delays

---

## 🎯 Practical Recommendations

### Small Collection (Quick Start)
```bash
./math/bin/python collect_samples.py 50 30 22 20 50 5
```
- **~180 items** in ~5-10 minutes
- **~10 MB** storage (includes ~25 ArXiv FULL proofs)
- Good for testing and initial training

### Medium Collection (Balanced Dataset)
```bash
./math/bin/python collect_samples.py 1000 500 22 200 500 50
```
- **~2,520 items** in ~3-5 hours
- **~150 MB** storage (includes ~250 ArXiv FULL proofs)
- Diverse content across all sources

### Large Collection (Comprehensive)
```bash
./math/bin/python collect_samples.py 10000 5000 22 1000 5000 1000
```
- **~27,000 items** in ~30-40 hours
- **~2.5 GB** storage (includes ~5,000 ArXiv FULL proofs)
- Substantial training dataset

### Maximum Collection (Everything)
```bash
# Would require running multiple times with pagination
# Stack Exchange: 500,000 items
# MathOverflow: 50,000 items
# ProofWiki: 20,000 items
# nLab: 15,000 items
# ArXiv FULL: 100,000 papers → ~500,000 theorem-proof pairs
```
- **~1,185,000 items** in ~950 hours (~40 days continuous)
- **~12-52 GB** storage
- Requires robust error handling and resume capability
- Consider running in batches

---

## ⚠️ Limitations

### Wikipedia
- Currently limited to 22 topics (hardcoded list)
- Can be expanded by adding more topics to `TOPICS` list in `scrapers/wikipedia_scraper.py`
- Potential: Could expand to ~1,000+ articles by adding more topics

### API Rate Limits
- **Stack Exchange**: 10,000 requests/day per IP
- **MathOverflow**: Same as Stack Exchange (uses SE API)
- **Wikipedia**: 200 requests/second (very generous)
- **ArXiv**: 1 request/3 seconds recommended

### Blocked Sources (Not Included)
- **MathWorld**: Website blocks scraping (~5,000 potential items)
- **Art of Problem Solving**: Anti-scraping (~10,000 potential items)
- **MIT OCW**: Changed structure (~500 potential items)

---

## 📈 Quality vs Quantity

### High Quality Sources (Recommended for Training)
1. **ProofWiki**: 100% structured, verified proofs (Quality: 95/100)
2. **ArXiv FULL**: Research-level theorem-proof pairs (Quality: 90/100)
3. **MathOverflow**: Expert-level, peer-reviewed content (Quality: 50-100/100)
4. **Stack Exchange**: High-score answers (Quality: 30-100/100)

### Medium Quality Sources
5. **nLab**: Advanced but sometimes informal (Quality: 85/100)
6. **Wikipedia**: General but reliable (Quality: 85/100)

---

## ❓ FAQ

### How does ArXiv FULL extraction work?

**Answer**: Downloads LaTeX sources and extracts theorem-proof pairs!

**ArXiv FULL approach**:
- **Storage**: ~20-100 KB per proof (extracted content only)
- **Time**: ~5 seconds per paper (download + parse)
- **Success rate**: ~5 proofs per paper (varies 0-20)
- **Result**: ~500,000 theorem-proof pairs from 100,000 papers
- **Quality**: 90/100 (published research papers)

**What we extract**:
- `\begin{theorem}...\end{theorem}` + `\begin{proof}...\end{proof}`
- Also: lemmas, propositions, corollaries
- LaTeX source is downloaded temporarily and deleted after extraction

**Why this works now**:
- YOU said: "i dont care about the author what i want is the mathematical demonstration"
- YOU said: "i dont care if its taking 10 days and 10To of data!"
- We implemented it! 🎉

---

## 🚀 Suggested Collection Strategy

### Phase 1: Quality Core (1-2 days)
- Stack Exchange: 10,000 high-score items
- ProofWiki: 5,000 items
- MathOverflow: 5,000 items
- **ArXiv FULL: 1,000 papers → ~5,000 proofs**
- **Total: ~25,000 items, ~2.5 GB**

### Phase 2: Diversity (3-5 days)
- Stack Exchange: +40,000 more
- ProofWiki: +15,000 more (all remaining)
- nLab: 5,000 items
- **ArXiv FULL: +9,000 papers → ~45,000 proofs**
- **Total: +110,000 items, +20 GB**

### Phase 3: Comprehensive (1-2 months)
- Collect remaining Stack Exchange
- All MathOverflow
- All nLab
- **ArXiv FULL: 90,000 more papers → ~450,000 proofs**
- **Total: Full 1,185,000 items, ~12-52 GB**

---

## 💡 Storage Optimization

### Raw JSON: ~12-52 GB
### Compressed (gzip): ~3-13 GB (70% reduction)
### Database (SQLite): ~8-35 GB (indexed)
### LaTeX Export: ~20-80 GB (verbose format)

### Recommendation
Store as compressed JSON, decompress only when needed for training. ArXiv FULL proofs are already in LaTeX format, so no conversion needed!
