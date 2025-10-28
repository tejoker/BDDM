#!/usr/bin/env python3
"""
Simple script to collect math exercise samples from English sources
"""

import asyncio
import json
from scrapers.stackexchange_scraper import StackExchangeScraper
from scrapers.proofwiki_scraper import ProofWikiScraper
from scrapers.arxiv_full_scraper import ArxivFullScraper
from scrapers.wikipedia_scraper import WikipediaMathScraper
from scrapers.nlab_scraper import NLabScraper
from scrapers.mathoverflow_scraper import MathOverflowScraper
from utils.storage import DataStorage


async def collect_samples(se_items=10, pw_items=10, wiki_items=10, 
                         nlab_items=10, mo_items=10, arxiv_full_items=0):
    """
    Collect sample exercises and proofs from English sources using round-robin strategy
    to maximize API rate limit usage.
    
    Args:
        se_items: Number of Stack Exchange Q&A to collect
        pw_items: Number of ProofWiki theorems to collect
        wiki_items: Number of Wikipedia articles to collect
        nlab_items: Number of nLab articles to collect
        mo_items: Number of MathOverflow Q&A to collect
        arxiv_full_items: Number of ArXiv papers to download FULL LaTeX sources from
                         (extracts actual theorem-proof pairs from LaTeX)
    """
    storage = DataStorage('samples_en')
    
    print("="*70)
    print("COLLECTING ENGLISH MATH EXERCISES & PROOFS")
    print("🔄 Using ROUND-ROBIN strategy to maximize API usage")
    print("="*70)
    
    # Initialize scrapers and target counts
    sources = []
    if se_items > 0:
        sources.append({
            'name': 'Stack Exchange',
            'emoji': '📚',
            'scraper': StackExchangeScraper(),
            'target': se_items,
            'collected': [],
            'batch_size': 80,  # Use SE rate limit efficiently
            'page': 1
        })
    
    if mo_items > 0:
        sources.append({
            'name': 'MathOverflow',
            'emoji': '🎓',
            'scraper': MathOverflowScraper(),
            'target': mo_items,
            'collected': [],
            'batch_size': 80,  # Same API as SE
            'page': 1
        })
    
    if pw_items > 0:
        sources.append({
            'name': 'ProofWiki',
            'emoji': '📐',
            'scraper': ProofWikiScraper(),
            'target': pw_items,
            'collected': [],
            'batch_size': 50,  # Moderate batch for ProofWiki
            'page': 1
        })
    
    if wiki_items > 0:
        sources.append({
            'name': 'Wikipedia',
            'emoji': '📖',
            'scraper': WikipediaMathScraper(),
            'target': wiki_items,
            'collected': [],
            'batch_size': 22,  # Limited by hardcoded topics
            'page': 1
        })
    
    if nlab_items > 0:
        sources.append({
            'name': 'nLab',
            'emoji': '🔬',
            'scraper': NLabScraper(),
            'target': nlab_items,
            'collected': [],
            'batch_size': 30,  # Moderate batch for nLab
            'page': 1
        })
    
    if arxiv_full_items > 0:
        sources.append({
            'name': 'ArXiv FULL',
            'emoji': '🔬',
            'scraper': ArxivFullScraper(),
            'target': arxiv_full_items,
            'collected': [],
            'batch_size': 10,  # Smaller batches due to download size
            'page': 1
        })
    
    # Round-robin collection
    print("\n🔄 Starting round-robin collection...")
    round_num = 1
    
    while any(len(src['collected']) < src['target'] for src in sources):
        print(f"\n{'='*70}")
        print(f"ROUND {round_num}")
        print(f"{'='*70}")
        
        for source in sources:
            # Skip if already collected enough
            if len(source['collected']) >= source['target']:
                continue
            
            remaining = source['target'] - len(source['collected'])
            batch_size = min(source['batch_size'], remaining)
            
            print(f"\n{source['emoji']} {source['name']}: Fetching {batch_size} items "
                  f"({len(source['collected'])}/{source['target']} collected)...")
            
            try:
                # Fetch batch
                batch_data = await source['scraper'].scrape(max_items=batch_size)
                source['collected'].extend(batch_data)
                
                print(f"   ✓ Got {len(batch_data)} items (total: {len(source['collected'])}/{source['target']})")
                
            except Exception as e:
                print(f"   ✗ Error: {e}")
                await asyncio.sleep(1)
        
        round_num += 1
        
        # Small delay between rounds
        await asyncio.sleep(0.5)
    
    # Extract collected data
    se_data = next((s['collected'] for s in sources if s['name'] == 'Stack Exchange'), [])
    mo_data = next((s['collected'] for s in sources if s['name'] == 'MathOverflow'), [])
    pw_data = next((s['collected'] for s in sources if s['name'] == 'ProofWiki'), [])
    wiki_data = next((s['collected'] for s in sources if s['name'] == 'Wikipedia'), [])
    nlab_data = next((s['collected'] for s in sources if s['name'] == 'nLab'), [])
    arxiv_full_data = next((s['collected'] for s in sources if s['name'] == 'ArXiv FULL'), [])
    
    print("\n" + "="*70)
    print("COLLECTION COMPLETE")
    print("="*70)
    
    # Save
    print("\n💾 Saving data...")
    storage.save_batch(se_data, 'stackexchange')
    storage.save_batch(pw_data, 'proofwiki')
    storage.save_batch(wiki_data, 'wikipedia')
    storage.save_batch(nlab_data, 'nlab')
    storage.save_batch(mo_data, 'mathoverflow')
    storage.save_batch(arxiv_full_data, 'arxiv_full')
    
    total = len(se_data) + len(pw_data) + len(wiki_data) + len(nlab_data) + len(mo_data) + len(arxiv_full_data)
    print(f"\n✅ DONE! Collected {total} items total:")
    print(f"   - {len(se_data)} Stack Exchange Q&A with accepted answers")
    print(f"   - {len(pw_data)} ProofWiki theorems with proofs")
    print(f"   - {len(wiki_data)} Wikipedia math articles")
    print(f"   - {len(nlab_data)} nLab articles")
    print(f"   - {len(mo_data)} MathOverflow Q&A")
    print(f"   - {len(arxiv_full_data)} ArXiv FULL theorem-proof pairs (from LaTeX)")
    print(f"\n📁 Saved to: samples_en/")
    
    # Show examples
    print("\n" + "="*70)
    print("EXAMPLES")
    print("="*70)
    
    if se_data:
        ex = se_data[0]
        print(f"\n[Stack Exchange] {ex['title']}")
        print(f"Score: {ex['score']} | Tags: {ex['tags']}")
        print(f"Question: {ex['question'][:150]}...")
    
    if pw_data:
        ex = pw_data[0]
        print(f"\n[ProofWiki] {ex['title']}")
        print(f"Theorem: {ex['theorem'][:150]}...")
    
    if wiki_data:
        ex = wiki_data[0]
        print(f"\n[Wikipedia] {ex['title']}")
        print(f"Content: {ex['content'][:150]}...")
    
    if mo_data:
        ex = mo_data[0]
        print(f"\n[MathOverflow] {ex['title']}")
        print(f"Score: {ex['score']} | Tags: {ex['tags']}")
    
    if arxiv_full_data:
        ex = arxiv_full_data[0]
        print(f"\n[ArXiv FULL] {ex['title'][:60]}...")
        print(f"Type: {ex.get('type', 'unknown')} | Paper: {ex.get('arxiv_id', 'N/A')}")
        print(f"Theorem: {ex['theorem'][:100]}...")
        print(f"Proof: {ex['proof'][:100]}...")
    
    return total


if __name__ == "__main__":
    import sys
    
    # Parse arguments: SE PW Wiki nLab MathOverflow ArXiv_FULL
    se_count = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    pw_count = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    wiki_count = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    nlab_count = int(sys.argv[4]) if len(sys.argv) > 4 else 5
    mo_count = int(sys.argv[5]) if len(sys.argv) > 5 else 10
    arxiv_full_count = int(sys.argv[6]) if len(sys.argv) > 6 else 0
    
    print(f"Collecting: {se_count} SE, {pw_count} PW, {wiki_count} Wiki, "
          f"{nlab_count} nLab, {mo_count} MO, {arxiv_full_count} ArXiv(FULL)\n")
    
    if arxiv_full_count > 0:
        print("⚠️  WARNING: ArXiv FULL will download LaTeX sources!")
        print(f"   - Downloads: ~{arxiv_full_count * 2}MB")
        print(f"   - Time: ~{arxiv_full_count * 5 / 60:.1f} minutes")
        print(f"   - Expected proofs: ~{arxiv_full_count * 5}")
        print()
    
    total = asyncio.run(collect_samples(se_count, pw_count, wiki_count, 
                                       nlab_count, mo_count, arxiv_full_count))
    
    print(f"\n🎉 Collection complete! {total} items ready for training.")
