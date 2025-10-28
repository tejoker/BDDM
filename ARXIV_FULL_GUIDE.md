# ArXiv Full LaTeX Collection Guide

## Overview

The **ArXiv Full Scraper** downloads complete LaTeX source code from research papers and extracts theorem-proof pairs.

⚠️ **This is different from the default metadata-only scraper!**

## Quick Start

### Small Test (10 papers, ~50 proofs)
```bash
./math/bin/python collect_arxiv_full.py 10
```
- Time: ~1 minute
- Storage: ~20 MB
- Proofs: ~50 theorem-proof pairs

### Medium Collection (1,000 papers, ~5,000 proofs)
```bash
./math/bin/python collect_arxiv_full.py 1000
```
- Time: ~1.5 hours
- Storage: ~2 GB
- Proofs: ~5,000 theorem-proof pairs

### Large Collection (10,000 papers, ~50,000 proofs)
```bash
./math/bin/python collect_arxiv_full.py 10000
```
- Time: ~15 hours
- Storage: ~20 GB
- Proofs: ~50,000 theorem-proof pairs

### Maximum Collection (100,000 papers, ~500,000 proofs)
```bash
./math/bin/python collect_arxiv_full.py 100000
```
- Time: ~140 hours (~6 days)
- Storage: ~200 GB
- Proofs: ~500,000 theorem-proof pairs

## Specify Categories

Focus on specific math areas:

```bash
# Logic only
./math/bin/python collect_arxiv_full.py 1000 math.LO

# Multiple categories
./math/bin/python collect_arxiv_full.py 1000 math.LO,math.CT,math.NT
```

### Available Categories

**Proof-Heavy (Recommended)**:
- `math.LO` - Logic (formal proofs)
- `math.CT` - Category Theory
- `math.NT` - Number Theory
- `math.CO` - Combinatorics
- `math.AG` - Algebraic Geometry
- `math.GR` - Group Theory
- `math.RA` - Rings and Algebras

**Other Math Categories**:
- `math.AT` - Algebraic Topology
- `math.CA` - Classical Analysis
- `math.CV` - Complex Variables
- `math.DG` - Differential Geometry
- `math.DS` - Dynamical Systems
- `math.FA` - Functional Analysis
- `math.GM` - General Mathematics
- `math.GT` - Geometric Topology
- `math.HO` - History and Overview
- `math.IT` - Information Theory
- `math.KT` - K-Theory and Homology
- `math.MG` - Metric Geometry
- `math.MP` - Mathematical Physics
- `math.NA` - Numerical Analysis
- `math.OA` - Operator Algebras
- `math.OC` - Optimization and Control
- `math.PR` - Probability
- `math.QA` - Quantum Algebra
- `math.RT` - Representation Theory
- `math.SG` - Symplectic Geometry
- `math.SP` - Spectral Theory
- `math.ST` - Statistics Theory

## What Gets Extracted

The scraper looks for LaTeX environments:
- `\begin{theorem}...\end{theorem}` + `\begin{proof}...\end{proof}`
- `\begin{lemma}...\end{lemma}` + `\begin{proof}...\end{proof}`
- `\begin{proposition}...\end{proposition}` + `\begin{proof}...\end{proof}`
- `\begin{corollary}...\end{corollary}` + `\begin{proof}...\end{proof}`

### Example Output

```json
{
  "id": "arxiv_2510.21498_0",
  "source": "arxiv_full",
  "paper_id": "2510.21498",
  "title": "Local stability in structures with a standard sort",
  "theorem": "The following are equivalent\\n\\begin{itemize}\\n\\item [1.] $D$ is involutive...",
  "proof": "By Fact~[REF] it suffices to prove $\\sim\\sim D \\subseteq D$...",
  "tags": ["math.LO"],
  "url": "https://arxiv.org/abs/2510.21498",
  "metadata": {
    "authors": ["John Doe", "Jane Smith"],
    "abstract": "We study local stability...",
    "published": "2025-10-21T00:00:00Z",
    "proof_index": 0,
    "language": "en"
  }
}
```

## Success Rate

From our tests:
- **Average**: ~5-6 proofs per paper
- **Good papers**: 10-20 proofs
- **Some papers**: 0 proofs (no theorem/proof environments)

### Why Some Papers Have No Proofs?

- Paper is a survey/overview (no new theorems)
- Uses custom LaTeX environments we don't recognize
- Proofs are inline (not in `\begin{proof}` environment)
- Paper is computational/experimental (no formal proofs)

**Expected success rate: ~60-70% of papers will yield at least one proof**

## Storage Estimates

### Per Paper
- LaTeX source download: 100 KB - 5 MB (average: 2 MB)
- Extracted proofs JSON: 1-10 KB per proof
- Average: ~2 MB per paper processed

### Total Storage

| Papers | Proofs (est.) | Storage | Time |
|-------:|--------------:|--------:|-----:|
| 100 | 500 | 200 MB | 10 min |
| 1,000 | 5,000 | 2 GB | 1.5 hours |
| 10,000 | 50,000 | 20 GB | 15 hours |
| 100,000 | 500,000 | 200 GB | 6 days |

## Performance Tips

### 1. Run in Background
```bash
nohup ./math/bin/python collect_arxiv_full.py 10000 > arxiv_collection.log 2>&1 &
```

### 2. Resume After Interruption
The scraper processes papers sequentially. To resume:
1. Check how many papers were processed (check log or data files)
2. Reduce your target number by that amount and run again

### 3. Parallel Collection (Advanced)
Split by category and run multiple instances:

```bash
# Terminal 1
./math/bin/python collect_arxiv_full.py 5000 math.LO &

# Terminal 2  
./math/bin/python collect_arxiv_full.py 5000 math.NT &

# Terminal 3
./math/bin/python collect_arxiv_full.py 5000 math.CO &
```

### 4. Monitor Progress
```bash
# Watch log
tail -f arxiv_collection.log

# Check storage usage
du -sh samples_en/raw/arxiv_full/
```

## Data Quality

### High Quality Proofs
Papers with formal mathematical proofs using standard LaTeX environments.

**Best categories for quality**:
- `math.LO` (Logic) - Very formal
- `math.CT` (Category Theory) - Rigorous
- `math.NT` (Number Theory) - Classical proofs

### What Gets Cleaned

The scraper removes:
- LaTeX comments (`%...`)
- Labels (`\label{...}`)
- References (`\ref{...}`, `\cite{...}`)
- Formatting commands (`\textbf`, `\textit`)

**What gets preserved**:
- Math mode content (`$...$`, `\[...\]`)
- Math environments (`equation`, `align`, etc.)
- All mathematical notation

## Comparison: Metadata vs Full

| Aspect | Metadata (default) | Full LaTeX |
|--------|-------------------|-----------|
| **Storage per 100k items** | 143 MB | 200 GB |
| **Time for 100k items** | 14 hours | 140 hours |
| **Success rate** | 100% | 60-70% |
| **Content** | Title, abstract, authors | Actual theorem-proof pairs |
| **Quality for ML training** | Low (no proofs) | High (formal proofs) |
| **Use case** | Topic discovery | Proof generation training |

## When to Use Full LaTeX Scraper

✅ **Use Full LaTeX when**:
- You need actual mathematical proofs for training
- You're building a proof generation model
- You want formal theorem-proof pairs
- Storage and time are not constraints
- You need high-quality mathematical reasoning data

❌ **Don't use Full LaTeX when**:
- You just want paper metadata
- Limited storage (<100 GB available)
- Need results quickly (<1 day)
- Building a paper recommendation system

## Combining with Other Sources

For a comprehensive dataset:

```bash
# 1. Collect from fast sources first
./math/bin/python collect_samples.py 10000 5000 0 22 1000 5000

# 2. Then collect ArXiv full (takes longest)
./math/bin/python collect_arxiv_full.py 5000

# Result: ~26,000 + ~25,000 = 51,000 high-quality items
```

## Estimated Full Collection

If you collected **ALL available theorem-proof pairs** from ArXiv:

### Conservative Estimate
- Papers to process: 100,000
- Success rate: 60%
- Proofs per successful paper: 5
- **Total proofs: ~300,000**
- **Storage: ~150 GB**
- **Time: ~6 days continuous**

### Optimistic Estimate  
- Papers to process: 200,000 (all math papers)
- Success rate: 70%
- Proofs per successful paper: 8
- **Total proofs: ~1,120,000**
- **Storage: ~300 GB**
- **Time: ~12 days continuous**

## Troubleshooting

### "Timeout downloading paper"
- Normal! Some papers are very large
- The scraper will skip and continue
- Expect ~5-10% timeouts

### "No extractable proofs"
- Also normal! Not all papers use theorem/proof environments
- Try focusing on `math.LO` or `math.CT` for better success rate

### "Disk space full"
- Monitor with `df -h`
- Consider collecting in smaller batches
- Delete `arxiv_latex_cache/` directory if enabled

### Rate Limiting
- ArXiv allows 1 request per 3 seconds
- This is respected by default
- Don't run multiple instances on same category

## Advanced: Modify Extraction

Edit `scrapers/arxiv_full_scraper.py`:

```python
# Line 330: Add custom environments
theorem_patterns = [
    ('theorem', r'\\begin{theorem}(.*?)\\end{theorem}'),
    ('lemma', r'\\begin{lemma}(.*?)\\end{lemma}'),
    # ADD YOUR CUSTOM ENVIRONMENT:
    ('claim', r'\\begin{claim}(.*?)\\end{claim}'),
]

# Line 360: Adjust quality filters
if (len(theorem_clean) > 20 and len(proof_clean) > 50 and
    len(theorem_clean) < 5000 and len(proof_clean) < 10000):
    # CHANGE THESE NUMBERS to filter differently
```

## Summary

The ArXiv Full LaTeX scraper is **powerful but slow**. It's perfect for building high-quality proof generation datasets, but requires patience and storage.

**Recommended approach**: Start with 100 papers to test, then scale up based on your needs.
