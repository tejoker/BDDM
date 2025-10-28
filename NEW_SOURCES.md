# New Math Sources Implementation

## Summary

Successfully implemented and integrated 3 new working math data sources into the collection system.

## Working Sources (6 Total)

### Original Sources (3)
1. **Stack Exchange** - Q&A with answers (working)
2. **ProofWiki** - Formal theorems with proofs (working)
3. **ArXiv** - Research paper metadata (working, optional)

### New Sources (3)
4. **Wikipedia** ✅ - Math encyclopedia articles
   - Uses Wikipedia API
   - Extracts intro sections of math topics
   - Covers: Calculus, Algebra, Topology, Analysis, etc.
   - Status: WORKING, collecting ~12-15 items per run

5. **nLab** ✅ - Category theory & higher mathematics
   - Scrapes ncatlab.org
   - Focuses on: Categories, Functors, Monads, Topoi, etc.
   - Status: WORKING, collecting ~9-10 items per run

6. **MathOverflow** ✅ - Research-level Q&A
   - Uses Stack Exchange API
   - Filters for accepted answers
   - High-quality, expert-level content
   - Status: WORKING, collecting ~3-4 items per run

## Implementation Details

### Bug Fixes Applied

1. **Wikipedia Scraper**:
   - Fixed: aiohttp boolean parameter issue (exintro=True → exintro='1')
   - Fixed: Missing User-Agent header
   - Fixed: Wrong attribute name (base_url → API_URL)

2. **MathOverflow Scraper**:
   - Fixed: Invalid API filter (custom filter → 'withbody')
   - Added: Proper rate limiting between answer fetches
   - Removed: Invalid API parameters

3. **nLab Scraper**:
   - Working as-is, minimal fixes needed

### Non-Working Sources (Implemented but Blocked)

- **MathWorld** - Wolfram's protection blocks scraping
- **Art of Problem Solving (AoPS)** - Anti-scraping measures
- **MIT OpenCourseWare** - Site structure changed or blocking

These scrapers are created but return 0 items due to website protections.

## Usage

### Collect from all sources:
```bash
./math/bin/python collect_samples.py 20 10 0 15 10 15
```

### Parameters (in order):
1. Stack Exchange count (default: 10)
2. ProofWiki count (default: 10)
3. ArXiv count (default: 0, set to 5+ to enable)
4. Wikipedia count (default: 10)
5. nLab count (default: 5)
6. MathOverflow count (default: 10)

### Example Collections:
```bash
# Balanced collection
./math/bin/python collect_samples.py 20 10 0 15 10 15

# Focus on Q&A sources
./math/bin/python collect_samples.py 50 10 0 0 0 30

# Encyclopedia + formal proofs
./math/bin/python collect_samples.py 10 20 0 30 20 5
```

## Data Structure

All data saved to: `samples_en/raw/<source>/batch_<timestamp>.json`

### New Source Formats:

**Wikipedia**:
```json
{
  "id": "wikipedia_Calculus",
  "source": "wikipedia",
  "title": "Calculus",
  "content": "Calculus is the mathematical study of...",
  "tags": ["Mathematics", "Calculus", ...],
  "url": "https://en.wikipedia.org/wiki/Calculus",
  "metadata": {
    "language": "en",
    "type": "encyclopedia"
  }
}
```

**nLab**:
```json
{
  "id": "nlab_category",
  "source": "nlab",
  "title": "category",
  "content": "This page is about the concept in mathematics...",
  "tags": [...],
  "url": "https://ncatlab.org/nlab/show/category",
  "metadata": {
    "language": "en",
    "level": "advanced"
  }
}
```

**MathOverflow**:
```json
{
  "id": "mathoverflow_12345",
  "source": "mathoverflow",
  "title": "Why do roots of polynomials...",
  "question": "I've noticed that...",
  "answer": "This is because...",
  "tags": ["polynomials", "complex-analysis"],
  "score": 437,
  "url": "https://mathoverflow.net/questions/12345",
  "metadata": {
    "language": "en",
    "view_count": 45000,
    "answer_count": 15,
    "level": "research"
  }
}
```

## Collection Statistics

Recent run (2024-10-26):
- **Total**: 55 items collected
- Stack Exchange: 20 Q&A
- ProofWiki: 10 theorems
- Wikipedia: 12 articles
- nLab: 9 articles
- MathOverflow: 4 Q&A

## LaTeX Export

LaTeX formatter needs updating to support new sources. Add these methods to `utils/latex_formatter.py`:

```python
def format_wikipedia(self, item):
    # Format Wikipedia articles
    
def format_nlab(self, item):
    # Format nLab articles
    
def format_mathoverflow(self, item):
    # Format MathOverflow Q&A (similar to SE)
```

## Next Steps

1. ✅ Integrate 3 working scrapers into collect_samples.py
2. ✅ Test end-to-end collection
3. ⚠️ Update LaTeX formatter for new sources
4. ⚠️ Run large-scale collection (100+ items per source)
5. ⚠️ Export to LaTeX format
6. ⚠️ Document new data formats

## Technical Notes

- All scrapers use aiohttp for async HTTP requests
- Wikipedia & MathOverflow use official APIs (rate-limited)
- nLab uses direct HTML scraping (respectful delays)
- User-Agent headers required for Wikipedia
- Boolean parameters must be strings for aiohttp in Python 3.13
