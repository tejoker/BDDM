#!/usr/bin/env python3
"""
Simple script to collect math exercise samples from English sources
"""

import asyncio
import json
import os
import logging
from pathlib import Path
from datetime import datetime
from scrapers.stackexchange_scraper import StackExchangeScraper
from scrapers.proofwiki_scraper import ProofWikiScraper
from scrapers.arxiv_full_scraper import ArxivFullScraper
from scrapers.wikipedia_scraper import WikipediaMathScraper
from scrapers.nlab_scraper import NLabScraper
from scrapers.mathoverflow_scraper import MathOverflowScraper
from scrapers.project_euler_scraper import ProjectEulerScraper
from utils.storage import DataStorage

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('scraper.log')
    ]
)
logger = logging.getLogger(__name__)


def load_api_key():
    """Load Stack Exchange API key from .env file"""
    env_file = Path(__file__).parent / '.env'
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    if key.strip() == 'STACKEXCHANGE_API_KEY':
                        return value.strip()
    return os.environ.get('STACKEXCHANGE_API_KEY')


def load_checkpoint(checkpoint_path):
    """Load checkpoint from previous interrupted collection"""
    if not checkpoint_path.exists():
        return None
    
    try:
        with open(checkpoint_path) as f:
            checkpoint = json.load(f)
            print(f"✅ Found checkpoint from {checkpoint['started_at']}")
            print(f"   Last updated: {checkpoint['last_updated']}")
            print(f"   Round: {checkpoint['round']}")
            return checkpoint
    except (json.JSONDecodeError, KeyError) as e:
        print(f"⚠️  Corrupted checkpoint file: {e}")
        return None


def save_checkpoint(checkpoint_path, session_id, started_at, round_num, sources):
    """Save current progress to checkpoint file"""
    checkpoint = {
        'session_id': session_id,
        'started_at': started_at,
        'last_updated': datetime.now().isoformat(),
        'round': round_num,
        'sources': {}
    }
    
    for source in sources:
        checkpoint['sources'][source['name'].lower().replace(' ', '_')] = {
            'collected': len(source['collected']),
            'target': source['target'],
            'page': source.get('page', 1)
        }
    
    with open(checkpoint_path, 'w') as f:
        json.dump(checkpoint, f, indent=2)



async def collect_samples(se_items=10, pw_items=10, wiki_items=10, nlab_items=5,
                         mo_items=10, arxiv_full_items=0, euler_items=0, resume=False):
    """
    Collect sample exercises and proofs from English sources using round-robin strategy
    to maximize API rate limit usage.
    
    Args:
        se_items: Number of Stack Exchange Q&A to collect
        pw_items: Number of ProofWiki theorems to collect
        wiki_items: Number of Wikipedia articles to collect (now supports 200+ topics!)
        nlab_items: Number of nLab articles to collect
        mo_items: Number of MathOverflow Q&A to collect
        arxiv_full_items: Number of ArXiv papers to download FULL LaTeX sources from
                         (extracts actual theorem-proof pairs from LaTeX)
        euler_items: Number of Project Euler problems to collect (956 available, no blocking!)
        resume: Whether to resume from previous checkpoint (default: False)
    """
    storage = DataStorage('samples_en')
    checkpoint_path = Path('samples_en') / 'checkpoint.json'
    
    # Session info
    session_id = datetime.now().strftime('%Y%m%d_%H%M%S')
    started_at = datetime.now().isoformat()
    
    # Try to load checkpoint if resume requested
    checkpoint = None
    if resume and checkpoint_path.exists():
        checkpoint = load_checkpoint(checkpoint_path)
        if checkpoint:
            print(f"♻️  RESUMING from checkpoint (session: {checkpoint['session_id']})")
        else:
            print("⚠️  Failed to load checkpoint, starting fresh...")
            resume = False
    elif resume:
        print("⚠️  No checkpoint found, starting fresh...")
        resume = False
    
    # Load API key
    api_key = load_api_key()
    if api_key:
        print("✅ Using Stack Exchange API key (10,000 requests/day)")
    else:
        print("⚠️  No API key found - using anonymous mode (300 requests/day)")
        print("   Create .env file with STACKEXCHANGE_API_KEY to get higher limits")
    
    print("="*70)
    print("COLLECTING ENGLISH MATH EXERCISES & PROOFS")
    print("🔄 Using ROUND-ROBIN strategy to maximize API usage")
    print("="*70)

    # Get already collected IDs to skip
    skip_ids = {
        'proofwiki': storage.get_collected_ids('proofwiki'),
        'wikipedia': storage.get_collected_ids('wikipedia')
    }
    logger.info(f"Skipping {len(skip_ids['proofwiki'])} already collected ProofWiki items")
    logger.info(f"Skipping {len(skip_ids['wikipedia'])} already collected Wikipedia items")

    # Initialize scrapers and target counts with ADAPTIVE batch sizing
    sources = []
    if se_items > 0:
        source_key = 'stack_exchange'
        collected_count = checkpoint['sources'][source_key]['collected'] if (checkpoint and source_key in checkpoint['sources']) else 0
        page = checkpoint['sources'][source_key].get('page', 1) if (checkpoint and source_key in checkpoint['sources']) else 1
        
        sources.append({
            'name': 'Stack Exchange',
            'emoji': '📚',
            'scraper': StackExchangeScraper(api_key=api_key),
            'target': se_items,
            'collected': [],  # Will skip already collected items via storage index
            'initial_collected': collected_count,  # Track starting point
            'batch_size_base': 200,  # Maximum performance: 2.5x boost
            'batch_size_max': 300,  # Peak batch size
            'speed_tier': 'fast',
            'page': page
        })
    
    if mo_items > 0:
        source_key = 'mathoverflow'
        collected_count = checkpoint['sources'][source_key]['collected'] if (checkpoint and source_key in checkpoint['sources']) else 0
        page = checkpoint['sources'][source_key].get('page', 1) if (checkpoint and source_key in checkpoint['sources']) else 1
        
        sources.append({
            'name': 'MathOverflow',
            'emoji': '🎓',
            'scraper': MathOverflowScraper(api_key=api_key),
            'target': mo_items,
            'collected': [],
            'initial_collected': collected_count,
            'batch_size_base': 200,  # Maximum performance: 2.5x boost
            'batch_size_max': 300,  # Peak batch size
            'speed_tier': 'fast',
            'page': page
        })
    
    if pw_items > 0:
        source_key = 'proofwiki'
        collected_count = checkpoint['sources'][source_key]['collected'] if (checkpoint and source_key in checkpoint['sources']) else 0
        page = checkpoint['sources'][source_key].get('page', 1) if (checkpoint and source_key in checkpoint['sources']) else 1

        sources.append({
            'name': 'ProofWiki',
            'emoji': '📐',
            'scraper': ProofWikiScraper(skip_ids=skip_ids['proofwiki']),
            'target': pw_items,
            'collected': [],
            'initial_collected': collected_count,
            'batch_size_base': 150,  # Maximum performance: 3x boost
            'batch_size_max': 200,  # Peak batch size
            'speed_tier': 'medium',
            'page': page
        })
    
    if wiki_items > 0:
        source_key = 'wikipedia'
        collected_count = checkpoint['sources'][source_key]['collected'] if (checkpoint and source_key in checkpoint['sources']) else 0
        page = checkpoint['sources'][source_key].get('page', 1) if (checkpoint and source_key in checkpoint['sources']) else 1

        sources.append({
            'name': 'Wikipedia',
            'emoji': '📖',
            'scraper': WikipediaMathScraper(use_category_graph=True, skip_ids=skip_ids['wikipedia']),  # 🌟 CATEGORY MODE: 10,000+ articles!
            'target': wiki_items,
            'collected': [],
            'initial_collected': collected_count,
            'batch_size_base': 250,  # Maximum performance: 2.5x boost
            'batch_size_max': 400,  # Peak batch size
            'speed_tier': 'fast',
            'page': page
        })
    
    if nlab_items > 0:
        source_key = 'nlab'
        collected_count = checkpoint['sources'][source_key]['collected'] if (checkpoint and source_key in checkpoint['sources']) else 0
        page = checkpoint['sources'][source_key].get('page', 1) if (checkpoint and source_key in checkpoint['sources']) else 1
        
        sources.append({
            'name': 'nLab',
            'emoji': '🔬',
            'scraper': NLabScraper(),
            'target': nlab_items,
            'collected': [],
            'initial_collected': collected_count,
            'batch_size_base': 75,  # Maximum performance: 2.5x boost
            'batch_size_max': 150,  # Peak batch size (no more bottleneck!)
            'speed_tier': 'slow',
            'page': page
        })
    
    if arxiv_full_items > 0:
        source_key = 'arxiv_full'
        collected_count = checkpoint['sources'][source_key]['collected'] if (checkpoint and source_key in checkpoint['sources']) else 0
        page = checkpoint['sources'][source_key].get('page', 1) if (checkpoint and source_key in checkpoint['sources']) else 1
        
        sources.append({
            'name': 'ArXiv FULL',
            'emoji': '🔬',
            'scraper': ArxivFullScraper(),
            'target': arxiv_full_items,
            'collected': [],
            'initial_collected': collected_count,
            'batch_size_base': 25,  # Maximum performance: 2.5x boost
            'batch_size_max': 40,  # Peak batch size (careful with bandwidth)
            'speed_tier': 'slow',
            'page': page
        })
    
    if euler_items > 0:
        source_key = 'project_euler'
        collected_count = checkpoint['sources'][source_key]['collected'] if (checkpoint and source_key in checkpoint['sources']) else 0
        page = checkpoint['sources'][source_key].get('page', 1) if (checkpoint and source_key in checkpoint['sources']) else 1
        
        sources.append({
            'name': 'Project Euler',
            'emoji': '🧮',
            'scraper': ProjectEulerScraper(),
            'target': euler_items,
            'collected': [],
            'initial_collected': collected_count,
            'batch_size_base': 150,  # Maximum performance: 3x boost
            'batch_size_max': 250,  # Peak batch size (no limits!)
            'speed_tier': 'fast',
            'page': page
        })
    
    # Round-robin collection
    print("\n🔄 Starting round-robin collection...")
    starting_round = checkpoint['round'] + 1 if checkpoint else 1
    round_num = starting_round
    max_rounds = 500  # Safety limit to prevent infinite loops
    
    # Track consecutive failures per source
    for source in sources:
        source['consecutive_failures'] = 0
        source['max_failures'] = 5  # Give up after 5 consecutive failures
        
        # Adjust target to account for already collected items
        total_collected = source['initial_collected'] + len(source['collected'])
        if total_collected >= source['target']:
            print(f"✅ {source['name']}: Already collected {source['initial_collected']}/{source['target']} (skipping)")
            source['target'] = 0  # Skip this source
        elif source['initial_collected'] > 0:
            print(f"♻️  {source['name']}: Resuming from {source['initial_collected']}/{source['target']} collected")
    
    while any(len(src['collected']) + src['initial_collected'] < src['target'] and src['consecutive_failures'] < src['max_failures'] 
              for src in sources) and round_num <= max_rounds:
        print(f"\n{'='*70}")
        print(f"ROUND {round_num}")
        print(f"{'='*70}")
        
        active_sources = 0
        
        for source in sources:
            # Calculate total collected (previous + current session)
            total_collected = source['initial_collected'] + len(source['collected'])
            
            # Skip if already collected enough
            if total_collected >= source['target']:
                continue
            
            # Skip if too many consecutive failures
            if source['consecutive_failures'] >= source['max_failures']:
                continue
            
            active_sources += 1
            remaining = source['target'] - total_collected
            
            # 🎯 ADAPTIVE BATCH SIZING
            # Scale batch size based on remaining items and source speed
            if remaining > 5000:
                # Large collection: use maximum batch size
                batch_size = source['batch_size_max']
            elif remaining > 1000:
                # Medium collection: use 1.5x base
                batch_size = int(source['batch_size_base'] * 1.5)
            elif remaining > 100:
                # Small collection: use base size
                batch_size = source['batch_size_base']
            else:
                # Finishing up: use smaller batches (50% of base)
                batch_size = max(10, int(source['batch_size_base'] * 0.5))
            
            # Don't exceed remaining items
            batch_size = min(batch_size, remaining)
            
            print(f"\n{source['emoji']} {source['name']}: Fetching {batch_size} items "
                  f"({total_collected}/{source['target']} collected)...")
            
            try:
                # Fetch batch
                batch_data = await source['scraper'].scrape(max_items=batch_size)
                
                if len(batch_data) == 0:
                    source['consecutive_failures'] += 1
                    print(f"   ⚠️  Got 0 items (failure {source['consecutive_failures']}/{source['max_failures']})")
                    if source['consecutive_failures'] >= source['max_failures']:
                        print(f"   ❌ Giving up on {source['name']} after {source['consecutive_failures']} failures")
                else:
                    source['consecutive_failures'] = 0  # Reset on success
                    source['collected'].extend(batch_data)
                    total_collected = source['initial_collected'] + len(source['collected'])
                    print(f"   ✓ Got {len(batch_data)} items (total: {total_collected}/{source['target']})")
                
            except Exception as e:
                source['consecutive_failures'] += 1
                print(f"   ✗ Error: {e} (failure {source['consecutive_failures']}/{source['max_failures']})")
                await asyncio.sleep(1)
        
        # Save checkpoint after each round
        try:
            save_checkpoint(checkpoint_path, session_id, started_at, round_num, sources)
        except Exception as e:
            print(f"⚠️  Failed to save checkpoint: {e}")
        
        # Break if no active sources
        if active_sources == 0:
            print("\n⚠️  No more active sources, stopping collection.")
            break
        
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
    mathbooks_data = next((s['collected'] for s in sources if s['name'] == 'MathBooks'), [])
    aops_data = next((s['collected'] for s in sources if s['name'] == 'AoPS'), [])
    tricki_data = next((s['collected'] for s in sources if s['name'] == 'Tricki'), [])
    
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
    storage.save_batch(mathbooks_data, 'mathbooks')
    storage.save_batch(aops_data, 'aops')
    storage.save_batch(tricki_data, 'tricki')
    
    total = (len(se_data) + len(pw_data) + len(wiki_data) + len(nlab_data) + 
             len(mo_data) + len(arxiv_full_data) + len(mathbooks_data) + 
             len(aops_data) + len(tricki_data))
    print(f"\n✅ DONE! Collected {total} items total:")
    print(f"   - {len(se_data)} Stack Exchange Q&A with accepted answers")
    print(f"   - {len(pw_data)} ProofWiki theorems with proofs")
    print(f"   - {len(wiki_data)} Wikipedia math articles")
    print(f"   - {len(nlab_data)} nLab articles")
    print(f"   - {len(mo_data)} MathOverflow Q&A")
    print(f"   - {len(arxiv_full_data)} ArXiv FULL theorem-proof pairs (from LaTeX)")
    print(f"   - {len(mathbooks_data)} MathBooks theorem-proof pairs")
    print(f"   - {len(aops_data)} AoPS competition problems")
    print(f"   - {len(tricki_data)} Tricki technique articles")
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
    
    if mathbooks_data:
        ex = mathbooks_data[0]
        print(f"\n[MathBooks] {ex['book_title']}")
        print(f"Theorem {ex['theorem_number']}: {ex['theorem_name']}")
    
    if aops_data:
        ex = aops_data[0]
        print(f"\n[AoPS] {ex['competition']}")
        print(f"Problem {ex['problem_number']}: {ex['problem_statement'][:100]}...")
    
    if tricki_data:
        ex = tricki_data[0]
        print(f"\n[Tricki] {ex['title']}")
        print(f"Technique: {ex['description'][:100]}...")
    
    return total


if __name__ == "__main__":
    import sys
    
    # Check for --resume flag
    resume_mode = '--resume' in sys.argv
    if resume_mode:
        sys.argv.remove('--resume')
        print("\n♻️  RESUME MODE: Will continue from checkpoint if available\n")
    
    # Special keywords
    if len(sys.argv) > 1:
        # Check for "all" keyword - collect maximum from all sources
        if sys.argv[1].lower() == 'all':
            target = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
            print(f"\n🌟 ALL mode: Collecting {target} items from EACH source\n")
            se_count = target
            pw_count = min(target, 20000)  # ProofWiki limited
            wiki_count = target  # Can use category graph for more
            nlab_count = min(target, 15000)  # nLab limited
            mo_count = target
            arxiv_full_count = target // 5  # Fewer since each gives ~5 proofs
            euler_count = min(target, 956)  # Project Euler limited to 956
        
        # Check for "max" keyword - get EVERYTHING from ONE source
        elif sys.argv[1].lower() == 'max':
            source = sys.argv[2] if len(sys.argv) > 2 else 'se'
            print(f"\n🎯 MAX mode: Collecting MAXIMUM from {source.upper()}\n")
            
            se_count = 500000 if source.lower() in ['se', 'stackexchange'] else 0
            pw_count = 20000 if source.lower() in ['pw', 'proofwiki'] else 0
            wiki_count = 10000 if source.lower() in ['wiki', 'wikipedia'] else 0
            nlab_count = 15000 if source.lower() == 'nlab' else 0
            mo_count = 50000 if source.lower() in ['mo', 'mathoverflow'] else 0
            arxiv_full_count = 100000 if source.lower() in ['arxiv', 'arxiv_full'] else 0
            euler_count = 956 if source.lower() in ['euler', 'project_euler'] else 0
        
        else:
            # Normal mode with individual counts
            se_count = int(sys.argv[1]) if len(sys.argv) > 1 else 10
            pw_count = int(sys.argv[2]) if len(sys.argv) > 2 else 10
            wiki_count = int(sys.argv[3]) if len(sys.argv) > 3 else 10
            nlab_count = int(sys.argv[4]) if len(sys.argv) > 4 else 5
            mo_count = int(sys.argv[5]) if len(sys.argv) > 5 else 10
            arxiv_full_count = int(sys.argv[6]) if len(sys.argv) > 6 else 0
            euler_count = int(sys.argv[7]) if len(sys.argv) > 7 else 0
    else:
        # Default values
        se_count = 10
        pw_count = 10
        wiki_count = 10
        nlab_count = 5
        mo_count = 10
        arxiv_full_count = 0
        euler_count = 0
    
    print(f"\n🎯 Target: {se_count} SE, {pw_count} PW, {wiki_count} Wiki, "
          f"{nlab_count} nLab, {mo_count} MO, {arxiv_full_count} ArXiv(FULL), "
          f"{euler_count} Project Euler\n")
    
    if arxiv_full_count > 0:
        print("⚠️  WARNING: ArXiv FULL will download LaTeX sources!")
        print(f"   - Downloads: ~{arxiv_full_count * 2}MB")
        print(f"   - Time: ~{arxiv_full_count * 5 / 60:.1f} minutes")
        print(f"   - Expected proofs: ~{arxiv_full_count * 5}")
        print()
    
    if euler_count > 0:
        print("🧮 Project Euler: 956 computational math problems, NO blocking!")
        print(f"   - Fast collection (~0.5 sec per problem)")
        print(f"   - High quality competition-level problems")
        print()
    
    # Run the collection
    asyncio.run(collect_samples(se_count, pw_count, wiki_count, 
                               nlab_count, mo_count, arxiv_full_count, euler_count, 
                               resume=resume_mode))
    
    # Delete checkpoint on successful completion
    checkpoint_path = Path('samples_en') / 'checkpoint.json'
    if checkpoint_path.exists():
        checkpoint_path.unlink()
        print("\n✅ Checkpoint cleared (collection completed)")
    
    print(f"\n✨ Examples of usage:")
    print(f"  Small test:  ./math/bin/python collect_samples.py 50 30 100 20 50 5 20")
    print(f"  Medium:      ./math/bin/python collect_samples.py 1000 500 200 200 500 50 100")
    print(f"  Large:       ./math/bin/python collect_samples.py 10000 5000 200 1000 5000 1000 500")
    print(f"\n  🌟 ALL mode:  ./math/bin/python collect_samples.py all 1000")
    print(f"     (Collects 1000 from each source)")
    print(f"\n  🎯 MAX mode:  ./math/bin/python collect_samples.py max se")
    print(f"     (Collects MAXIMUM from Stack Exchange only)")
    print(f"     Sources: se, pw, wiki, nlab, mo, arxiv, euler")
    print(f"\n  🔍 Selective: ./math/bin/python collect_samples.py 1000 0 0 0 0 0 0")
    print(f"     (Only Stack Exchange with 1000 items, all others skipped)")
    print(f"\n  ♻️  Resume:    ./math/bin/python collect_samples.py --resume")
    print(f"     (Resume from checkpoint after interruption)")
    print(f"     Note: --resume can be combined with any mode above")
    print(f"\n  Parameters: SE PW Wiki nLab MO ArXiv_FULL Euler")
    print(f"  - Wikipedia: Uses CATEGORY GRAPH mode by default (10,000-50,000 articles!)")
    print(f"  - Project Euler: 956 problems (updated 2025, no anti-scraping!)")
