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
    Collect sample exercises and proofs from English sources
    
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
    print("="*70)
    
    # Stack Exchange - Questions with accepted answers
    if se_items > 0:
        print(f"\n📚 Stack Exchange: Collecting {se_items} Q&A...")
        se_scraper = StackExchangeScraper()
        se_data = await se_scraper.scrape(max_items=se_items)
        print(f"   ✓ Collected {len(se_data)} questions with answers")
    else:
        se_data = []
    
    # ProofWiki - Theorems with proofs
    if pw_items > 0:
        print(f"\n📐 ProofWiki: Collecting {pw_items} theorems...")
        pw_scraper = ProofWikiScraper()
        pw_data = await pw_scraper.scrape(max_items=pw_items)
        print(f"   ✓ Collected {len(pw_data)} theorems with proofs")
    else:
        pw_data = []
    
    # Wikipedia - Math encyclopedia
    if wiki_items > 0:
        print(f"\n📖 Wikipedia: Collecting {wiki_items} articles...")
        wiki_scraper = WikipediaMathScraper()
        wiki_data = await wiki_scraper.scrape(max_items=wiki_items)
        print(f"   ✓ Collected {len(wiki_data)} math articles")
    else:
        wiki_data = []
    
    # nLab - Category theory and higher math
    if nlab_items > 0:
        print(f"\n🔬 nLab: Collecting {nlab_items} articles...")
        nlab_scraper = NLabScraper()
        nlab_data = await nlab_scraper.scrape(max_items=nlab_items)
        print(f"   ✓ Collected {len(nlab_data)} nLab articles")
    else:
        nlab_data = []
    
    # MathOverflow - Research-level Q&A
    if mo_items > 0:
        print(f"\n🎓 MathOverflow: Collecting {mo_items} Q&A...")
        mo_scraper = MathOverflowScraper()
        mo_data = await mo_scraper.scrape(max_items=mo_items)
        print(f"   ✓ Collected {len(mo_data)} research-level Q&A")
    else:
        mo_data = []
    
    # ArXiv Full - ACTUAL theorem-proof pairs from LaTeX sources
    if arxiv_full_items > 0:
        print(f"\n🔬 ArXiv FULL: Downloading LaTeX sources from {arxiv_full_items} papers...")
        print(f"   ⚠️  WARNING: This downloads tar.gz files (~{arxiv_full_items * 2}MB)")
        print(f"   ⏱️  Estimated time: ~{arxiv_full_items * 5 / 60:.1f} minutes")
        arxiv_full_scraper = ArxivFullScraper()
        arxiv_full_data = await arxiv_full_scraper.scrape(max_items=arxiv_full_items)
        print(f"   ✓ Extracted {len(arxiv_full_data)} theorem-proof pairs")
        print(f"   📊 Success rate: {len(arxiv_full_data)/max(arxiv_full_items, 1):.1f} proofs per paper")
    else:
        arxiv_full_data = []
    
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
