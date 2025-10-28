# Data Format and Normalization Strategy

## Current State: Non-Normalized

Currently, each source has its own format with different field names:

### Stack Exchange / MathOverflow
```json
{
  "id": "se_243770",
  "source": "stackexchange",
  "title": "Can every proof by contradiction...",
  "question": "Are there some proofs...",
  "answer": "To determine what can and cannot...",
  "tags": ["logic", "proof-writing"],
  "score": 388,
  "url": "https://...",
  "metadata": {...}
}
```

### ProofWiki
```json
{
  "id": "pw_...",
  "source": "proofwiki",
  "title": "(A cap C) cup (B cap Complement C)...",
  "theorem": "Let $A$, $B$ and $C$ be subsets...",
  "proof": "$\\blacksquare$",
  "tags": ["set union", "set intersection"],
  "url": "https://...",
  "metadata": {...}
}
```

### ArXiv Full
```json
{
  "id": "arxiv_2510.21498_0",
  "source": "arxiv_full",
  "title": "Local stability in structures...",
  "theorem": "The following are equivalent...",
  "proof": "By Fact~[REF] it suffices...",
  "tags": ["math.LO", "logic"],
  "paper_id": "2510.21498",
  "url": "https://...",
  "metadata": {
    "type": "theorem",  // or "lemma", "proposition", "corollary"
    "authors": [...],
    "proof_index": 0
  }
}
```

### Wikipedia / nLab
```json
{
  "id": "wikipedia_Number_theory",
  "source": "wikipedia",
  "title": "Number theory",
  "content": "Number theory is a branch...",
  "tags": [],
  "url": "https://...",
  "metadata": {...}
}
```

---

## Proposed: Unified Normalized Schema

### Core Fields (All Sources)
```json
{
  // Identity
  "id": "unique_identifier",
  "source": "stackexchange|proofwiki|arxiv_full|wikipedia|mathoverflow|nlab",
  "type": "qa|theorem_proof|encyclopedia",
  
  // Content
  "title": "Human-readable title or theorem name",
  "statement": "Question, theorem statement, or article intro",
  "solution": "Answer, proof, or explanation",
  
  // Classification
  "tags": ["tag1", "tag2"],
  "difficulty": "undergraduate|graduate|research",
  "domain": "algebra|analysis|topology|logic|geometry|...",
  
  // Metadata
  "url": "source URL",
  "language": "en",
  "quality_score": 0-100,
  "created_date": "ISO timestamp",
  
  // Source-specific (preserved in 'extras')
  "extras": {
    // Source-specific fields that don't fit core schema
  }
}
```

---

## Normalization Options

### Option 1: Keep Everything (Current)
**Pros**:
- No information loss
- Easy to implement (already done!)
- Can query original structure

**Cons**:
- Inconsistent field names across sources
- Harder to train on mixed data
- Need source-specific code

**Storage**: Same as current (~2-200 GB depending on collection)

### Option 2: Minimal Normalization
Map similar fields to common names:
- `question` OR `theorem` → `statement`
- `answer` OR `proof` → `solution`
- Keep `title` as-is
- Preserve everything else in `extras`

**Pros**:
- Easy to train on (consistent field names)
- Still preserves all data
- Simple mapping

**Cons**:
- Loses semantic difference between "question" and "theorem"
- Harder to filter by specific types

**Storage**: +5-10% overhead (due to duplicated fields)

### Option 3: Full Normalization (Recommended)
Transform all sources into unified schema with:
- Semantic type classification
- Quality scoring
- Domain categorization
- Theorem name extraction (if present)

**Pros**:
- Clean, consistent data
- Easy filtering and querying
- Better for ML training
- Can still access original data

**Cons**:
- More complex processing
- Some edge cases need special handling
- One-time processing cost

**Storage**: +10-20% overhead

---

## Theorem Name Extraction Strategy

### Question: Keep "Lebesgue Theorem" name or just theorem content?

**Answer: Keep BOTH!**

### Proposed Structure

```json
{
  "id": "pw_lebesgue_dominated_convergence_0",
  "source": "proofwiki",
  "type": "theorem_proof",
  
  // Extracted theorem name (if present)
  "theorem_name": "Lebesgue's Dominated Convergence Theorem",
  "theorem_aliases": ["DCT", "Dominated Convergence", "Lebesgue DCT"],
  
  // Full title as it appears in source
  "title": "Lebesgue's Dominated Convergence Theorem",
  
  // The actual mathematical statement
  "statement": "Let $(X, \\Sigma, \\mu)$ be a measure space...",
  
  // The proof
  "solution": "We proceed in steps. First, note that...",
  
  // Tags include both general and specific
  "tags": ["real-analysis", "measure-theory", "integration", "convergence-theorems"],
  
  // Additional structure
  "metadata": {
    "has_theorem_name": true,
    "named_theorem": "lebesgue_dominated_convergence",
    "theorem_category": "convergence",
    "prerequisites": ["measure-theory", "lebesgue-integration"]
  }
}
```

### Why Keep Both?

1. **Training flexibility**: 
   - With name: "Prove Lebesgue's DCT" → model learns named theorems
   - Without name: "Prove this convergence result" → model learns patterns

2. **Search & Discovery**:
   - Users can search "Lebesgue" to find specific theorem
   - Can also search by concept "dominated convergence"

3. **Knowledge Graph**:
   - Can build relationships: "DCT" is-a "convergence theorem"
   - Link related theorems: "Fatou's Lemma" relates-to "DCT"

---

## Extraction Strategies by Source

### ProofWiki: Extract from Title
```python
# Input: "(A cap C) cup (B cap Complement C) = Empty iff B subset C subset Complement A"
# Extract:
{
  "theorem_name": None,  # No named theorem
  "statement_type": "characterization",
  "title": "(A cap C) cup (B cap Complement C) = Empty iff..."
}

# Input: "Lebesgue's Dominated Convergence Theorem"
# Extract:
{
  "theorem_name": "Lebesgue's Dominated Convergence Theorem",
  "named_theorem": "lebesgue_dominated_convergence",
  "attributed_to": "Lebesgue"
}
```

### ArXiv Full: Extract from LaTeX
```python
# From LaTeX source:
# \begin{theorem}[Lebesgue's DCT]
#   Let $(X, \Sigma, \mu)$ be...
# \end{theorem}

# Extract:
{
  "theorem_name": "Lebesgue's DCT",
  "type": "theorem",  # vs lemma, proposition, corollary
  "labeled": true
}
```

### Stack Exchange: Extract from Title
```python
# Input: "Proof of Cauchy-Schwarz inequality"
{
  "theorem_name": "Cauchy-Schwarz inequality",
  "question_type": "proof-request"
}

# Input: "How to integrate this function?"
{
  "theorem_name": None,
  "question_type": "problem-solving"
}
```

---

## Storage Format Options

### Option A: Single JSON per Item (Current)
```
samples_en/raw/proofwiki/batch_20251026.json
samples_en/raw/stackexchange/batch_20251026.json
...
```

**Pros**: Easy to append, source isolation
**Cons**: Many small files, harder to query across sources

### Option B: SQLite Database
```
samples_en/database.db

Tables:
  - items (id, source, type, title, statement, solution, ...)
  - tags (item_id, tag)
  - metadata (item_id, key, value)
  - theorem_names (item_id, name, aliases)
```

**Pros**: Fast queries, normalization, indexing
**Cons**: Need migration script, less portable

### Option C: Hybrid (Recommended)
```
samples_en/
  raw/                    # Original source-specific JSON
    stackexchange/
    proofwiki/
    arxiv_full/
  
  normalized/             # Normalized unified format
    all_items.jsonl       # JSON lines format
    index.db              # SQLite index for fast queries
    
  exports/                # Generated exports
    latex/
    csv/
    parquet/
```

**Pros**: 
- Keep original data intact
- Fast normalized access
- Easy to regenerate if normalization changes

---

## Proposed Normalization Script

Create `normalize_data.py`:

```python
#!/usr/bin/env python3
"""
Normalize collected data into unified schema
"""

import json
import glob
from pathlib import Path

def normalize_stackexchange(item):
    return {
        'id': item['id'],
        'source': item['source'],
        'type': 'qa',
        'theorem_name': extract_theorem_name(item['title']),
        'title': item['title'],
        'statement': item['question'],
        'solution': item['answer'],
        'tags': item['tags'],
        'difficulty': classify_difficulty(item),
        'domain': classify_domain(item['tags']),
        'quality_score': calculate_quality(item),
        'url': item['url'],
        'language': 'en',
        'created_date': item['created_date'],
        'extras': {
            'score': item['score'],
            'answer_score': item['answer_score'],
            'view_count': item['metadata'].get('view_count')
        }
    }

def normalize_proofwiki(item):
    return {
        'id': item['id'],
        'source': item['source'],
        'type': 'theorem_proof',
        'theorem_name': extract_theorem_name(item['title']),
        'title': item['title'],
        'statement': item['theorem'],
        'solution': item['proof'],
        'tags': item['tags'],
        'difficulty': 'graduate',  # ProofWiki is generally advanced
        'domain': classify_domain(item['tags']),
        'quality_score': 95,  # ProofWiki is high quality
        'url': item['url'],
        'language': 'en',
        'extras': {
            'has_proof': item['metadata'].get('has_proof'),
            'has_theorem': item['metadata'].get('has_theorem')
        }
    }

def normalize_arxiv_full(item):
    return {
        'id': item['id'],
        'source': item['source'],
        'type': 'theorem_proof',
        'theorem_name': extract_from_latex_label(item['theorem']),
        'title': item['title'],
        'statement': item['theorem'],
        'solution': item['proof'],
        'tags': item['tags'],
        'difficulty': 'research',
        'domain': classify_domain(item['tags']),
        'quality_score': 90,  # ArXiv is peer-reviewed
        'url': item['url'],
        'language': 'en',
        'created_date': item['metadata'].get('published'),
        'extras': {
            'paper_id': item['paper_id'],
            'authors': item['metadata'].get('authors'),
            'theorem_type': item['metadata'].get('type'),  # theorem, lemma, etc.
            'proof_index': item['metadata'].get('proof_index')
        }
    }

def extract_theorem_name(title):
    """Extract theorem name if present in title"""
    # Patterns: "Theorem Name", "Name's Theorem", "Name Theorem"
    import re
    
    patterns = [
        r"([A-Z][a-z]+(?:'s)?)\s+(Theorem|Lemma|Inequality|Formula|Rule|Law)",
        r"(Theorem|Lemma|Proposition)\s+\(([^)]+)\)",
    ]
    
    for pattern in patterns:
        match = re.search(pattern, title)
        if match:
            return match.group(0)
    
    return None

def classify_difficulty(item):
    """Classify difficulty based on tags and source"""
    # Logic based on tags, score, source
    if item['source'] == 'mathoverflow':
        return 'research'
    elif 'undergraduate' in item.get('tags', []):
        return 'undergraduate'
    elif 'graduate' in item.get('tags', []):
        return 'graduate'
    else:
        return 'undergraduate'  # default

def classify_domain(tags):
    """Map tags to primary mathematical domain"""
    domain_keywords = {
        'algebra': ['algebra', 'group-theory', 'ring-theory', 'field-theory'],
        'analysis': ['real-analysis', 'complex-analysis', 'functional-analysis'],
        'topology': ['topology', 'algebraic-topology', 'differential-topology'],
        'geometry': ['geometry', 'differential-geometry', 'algebraic-geometry'],
        'logic': ['logic', 'set-theory', 'model-theory', 'proof-theory'],
        'number-theory': ['number-theory', 'analytic-number-theory'],
        'combinatorics': ['combinatorics', 'graph-theory'],
        'probability': ['probability', 'statistics', 'stochastic-processes'],
    }
    
    for domain, keywords in domain_keywords.items():
        if any(kw in tags for kw in keywords):
            return domain
    
    return 'general'

def calculate_quality(item):
    """Calculate quality score 0-100"""
    score = 50  # base
    
    # Boost for high score
    if item.get('score', 0) > 100:
        score += 20
    elif item.get('score', 0) > 50:
        score += 10
    
    # Boost for accepted answer with high score
    if item.get('answer_score', 0) > 100:
        score += 20
    elif item.get('answer_score', 0) > 50:
        score += 10
    
    # Cap at 100
    return min(100, score)

# Main normalization
def normalize_all():
    """Normalize all collected data"""
    pass  # Implementation
```

---

## Recommendation

### Immediate: Keep Current Format
- **Phase 1** (Now): Continue collecting with source-specific formats
- Preserve all information
- Easy to iterate on collection

### Near-term: Add Normalization Layer
- **Phase 2** (After collection): Write normalization script
- Create `samples_en/normalized/` directory
- Generate unified JSONL file + SQLite index

### Long-term: Enhance with NER
- **Phase 3** (Optional): Add NLP processing
- Extract theorem names automatically
- Build knowledge graph of related theorems
- Add semantic embeddings

---

## Metadata Flags to Keep

### Essential (Always Keep)
- ✅ `id` - Unique identifier
- ✅ `source` - Where it came from
- ✅ `title` - Human-readable name
- ✅ `statement` - Question/theorem
- ✅ `solution` - Answer/proof
- ✅ `tags` - Topic classification
- ✅ `url` - Original source link

### Important (Usually Keep)
- ✅ `theorem_name` - If it's a named theorem
- ✅ `difficulty` - Educational level
- ✅ `quality_score` - For filtering
- ✅ `language` - For multilingual support
- ✅ `created_date` - Temporal analysis

### Optional (Keep in 'extras')
- ⚠️ `authors` - Attribution (ArXiv)
- ⚠️ `score` - Community rating (SE/MO)
- ⚠️ `view_count` - Popularity
- ⚠️ `paper_id` - Source reference (ArXiv)
- ⚠️ `proof_index` - Position in paper

### Can Drop (Regenerable)
- ❌ Raw HTML - Can always re-scrape
- ❌ Intermediate parsing - Regenerate from source
- ❌ Cached responses - Not needed long-term

---

## Summary

**Current approach is fine for now!** The source-specific formats preserve all information.

When you're ready to train:
1. Write a simple normalization script
2. Map `question|theorem → statement` and `answer|proof → solution`
3. Extract theorem names where present
4. Keep both the name AND the content
5. Store normalized data separately from raw data

This gives you maximum flexibility without losing any information.
