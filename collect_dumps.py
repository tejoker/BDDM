#!/usr/bin/env python3
"""
Collect mathematical data from downloaded dumps
Much faster than web scraping: ~19 hours vs ~96 days
"""

import sys
import json
import logging
from pathlib import Path
from datetime import datetime
from utils.storage import DataStorage

# Import all parsers
from parsers.wikipedia_dump_parser import WikipediaDumpParser
from parsers.stackexchange_dump_parser import StackExchangeDumpParser
from parsers.mathoverflow_dump_parser import MathOverflowDumpParser
from parsers.arxiv_kaggle_parser import ArxivKaggleParser
from parsers.oeis_parser import OEISParser
from parsers.proofpile_parser import ProofPileParser
from parsers.lean_mathlib_parser import LeanMathlibParser
from parsers.metamath_parser import MetamathParser
from parsers.isabelle_afp_parser import IsabelleAFPParser
from parsers.coq_parser import CoqParser
from parsers.zbmath_parser import ZbMATHParser

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('parser.log')
    ]
)
logger = logging.getLogger(__name__)


def collect_from_dumps(
    wiki_items=0,
    se_items=0,
    mo_items=0,
    arxiv_items=0,
    oeis_items=0,
    proofpile_items=0,
    lean_items=0,
    metamath_items=0,
    isabelle_items=0,
    coq_items=0,
    zbmath_items=0
):
    """
    Collect data from downloaded dumps

    Args:
        wiki_items: Wikipedia articles to parse (0=skip)
        se_items: Stack Exchange Q&A pairs (0=skip)
        mo_items: MathOverflow Q&A pairs (0=skip)
        arxiv_items: ArXiv papers (0=skip)
        oeis_items: OEIS sequences (0=skip, max ~370k)
        proofpile_items: Proof-Pile items (0=skip)
        lean_items: Lean Mathlib theorems (0=skip, max ~150k)
        metamath_items: Metamath theorems (0=skip, max ~40k)
        isabelle_items: Isabelle AFP theorems (0=skip)
        coq_items: Coq theorems (0=skip)
        zbmath_items: zbMATH records (0=skip, API-based)
    """

    storage = DataStorage('samples_en')
    dumps_dir = Path('data_dumps')

    # Check if dumps directory exists
    if not dumps_dir.exists():
        print("Error: data_dumps/ directory not found")
        print("Please run ./download_dumps.sh first")
        return

    print("=" * 60)
    print("MATHEMATICAL DATA COLLECTION FROM DUMPS")
    print("=" * 60)
    print()

    total_collected = 0
    start_time = datetime.now()

    # 1. Wikipedia
    if wiki_items > 0:
        print(f"\n[1/11] Wikipedia: Parsing {wiki_items} articles...")
        wiki_dump = dumps_dir / 'wikipedia' / 'enwiki-latest-pages-articles.xml.bz2'
        if wiki_dump.exists():
            parser = WikipediaDumpParser(str(wiki_dump), skip_ids=storage.get_existing_ids())
            items = parser.parse(max_items=wiki_items)
            saved = storage.save_batch('wikipedia', items)
            total_collected += saved
            print(f"   Saved {saved} Wikipedia articles")
        else:
            print(f"   WARNING: {wiki_dump} not found")

    # 2. Stack Exchange
    if se_items > 0:
        print(f"\n[2/11] Stack Exchange: Parsing {se_items} Q&A pairs...")
        se_dump = dumps_dir / 'stackexchange' / 'math.stackexchange.com'
        if se_dump.exists():
            parser = StackExchangeDumpParser(str(se_dump), skip_ids=storage.get_existing_ids())
            items = parser.parse(max_items=se_items)
            saved = storage.save_batch('stackexchange', items)
            total_collected += saved
            print(f"   Saved {saved} Stack Exchange items")
        else:
            print(f"   WARNING: {se_dump} not found")

    # 3. MathOverflow
    if mo_items > 0:
        print(f"\n[3/11] MathOverflow: Parsing {mo_items} Q&A pairs...")
        mo_dump = dumps_dir / 'mathoverflow' / 'mathoverflow.net'
        if mo_dump.exists():
            parser = MathOverflowDumpParser(str(mo_dump), skip_ids=storage.get_existing_ids())
            items = parser.parse(max_items=mo_items)
            saved = storage.save_batch('mathoverflow', items)
            total_collected += saved
            print(f"   Saved {saved} MathOverflow items")
        else:
            print(f"   WARNING: {mo_dump} not found")

    # 4. ArXiv
    if arxiv_items > 0:
        print(f"\n[4/11] ArXiv: Parsing {arxiv_items} papers...")
        arxiv_dump = dumps_dir / 'arxiv' / 'arxiv-metadata-oai-snapshot.json'
        if arxiv_dump.exists():
            parser = ArxivKaggleParser(str(arxiv_dump), skip_ids=storage.get_existing_ids(), extract_proofs=False)
            items = parser.parse(max_items=arxiv_items, categories=['math.'])
            saved = storage.save_batch('arxiv', items)
            total_collected += saved
            print(f"   Saved {saved} ArXiv papers")
        else:
            print(f"   WARNING: {arxiv_dump} not found")

    # 5. OEIS
    if oeis_items > 0:
        print(f"\n[5/11] OEIS: Parsing {oeis_items} sequences...")
        oeis_dump = dumps_dir / 'oeis' / 'stripped.gz'
        if oeis_dump.exists():
            parser = OEISParser(str(oeis_dump), skip_ids=storage.get_existing_ids())
            items = parser.parse(max_items=oeis_items)
            saved = storage.save_batch('oeis', items)
            total_collected += saved
            print(f"   Saved {saved} OEIS sequences")
        else:
            print(f"   WARNING: {oeis_dump} not found")

    # 6. Proof-Pile
    if proofpile_items > 0:
        print(f"\n[6/11] Proof-Pile: Parsing {proofpile_items} items...")
        # Proof-Pile uses HuggingFace datasets library
        parser = ProofPileParser('proofpile', skip_ids=storage.get_existing_ids())
        items = parser.parse(max_items=proofpile_items)
        saved = storage.save_batch('proofpile', items)
        total_collected += saved
        print(f"   Saved {saved} Proof-Pile items")

    # 7. Lean Mathlib
    if lean_items > 0:
        print(f"\n[7/11] Lean Mathlib: Parsing {lean_items} theorems...")
        lean_dump = dumps_dir / 'mathlib4'
        if lean_dump.exists():
            parser = LeanMathlibParser(str(lean_dump), skip_ids=storage.get_existing_ids())
            items = parser.parse(max_items=lean_items)
            saved = storage.save_batch('lean_mathlib', items)
            total_collected += saved
            print(f"   Saved {saved} Lean theorems")
        else:
            print(f"   WARNING: {lean_dump} not found")

    # 8. Metamath
    if metamath_items > 0:
        print(f"\n[8/11] Metamath: Parsing {metamath_items} theorems...")
        metamath_dump = dumps_dir / 'metamath' / 'set.mm'
        if metamath_dump.exists():
            parser = MetamathParser(str(metamath_dump), skip_ids=storage.get_existing_ids())
            items = parser.parse(max_items=metamath_items)
            saved = storage.save_batch('metamath', items)
            total_collected += saved
            print(f"   Saved {saved} Metamath theorems")
        else:
            print(f"   WARNING: {metamath_dump} not found")

    # 9. Isabelle AFP
    if isabelle_items > 0:
        print(f"\n[9/11] Isabelle AFP: Parsing {isabelle_items} theorems...")
        isabelle_dump = dumps_dir / 'isabelle-afp'
        if isabelle_dump.exists():
            parser = IsabelleAFPParser(str(isabelle_dump), skip_ids=storage.get_existing_ids())
            items = parser.parse(max_items=isabelle_items)
            saved = storage.save_batch('isabelle_afp', items)
            total_collected += saved
            print(f"   Saved {saved} Isabelle theorems")
        else:
            print(f"   WARNING: {isabelle_dump} not found")

    # 10. Coq
    if coq_items > 0:
        print(f"\n[10/11] Coq: Parsing {coq_items} theorems...")
        coq_dump = dumps_dir / 'coq'
        if coq_dump.exists():
            parser = CoqParser(str(coq_dump), skip_ids=storage.get_existing_ids())
            items = parser.parse(max_items=coq_items)
            saved = storage.save_batch('coq', items)
            total_collected += saved
            print(f"   Saved {saved} Coq theorems")
        else:
            print(f"   WARNING: {coq_dump} not found")

    # 11. zbMATH
    if zbmath_items > 0:
        print(f"\n[11/11] zbMATH: Fetching {zbmath_items} records via API...")
        parser = ZbMATHParser(skip_ids=storage.get_existing_ids())
        items = parser.parse(max_items=zbmath_items)
        saved = storage.save_batch('zbmath', items)
        total_collected += saved
        print(f"   Saved {saved} zbMATH records")

    # Summary
    elapsed = datetime.now() - start_time
    print()
    print("=" * 60)
    print(f"COLLECTION COMPLETE!")
    print("=" * 60)
    print(f"Total items collected: {total_collected}")
    print(f"Time elapsed: {elapsed}")
    print(f"Output directory: samples_en/")
    print("=" * 60)


def print_usage():
    """Print usage instructions"""
    print("Usage: ./math/bin/python collect_dumps.py <config>")
    print()
    print("Configs:")
    print("  small    - Test collection (~10k items, ~30 min)")
    print("  medium   - Medium collection (~50k items, ~2 hours)")
    print("  large    - Large collection (~200k items, ~8 hours)")
    print("  max      - Maximum collection (~1.6M items, ~19 hours)")
    print()
    print("Or specify custom counts:")
    print("  ./math/bin/python collect_dumps.py wiki se mo arxiv oeis pp lean mm isa coq zbm")
    print()
    print("Example:")
    print("  ./math/bin/python collect_dumps.py 1000 5000 1000 2000 10000 0 5000 0 0 0 0")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    config = sys.argv[1].lower()

    # Predefined configs
    if config == 'small':
        collect_from_dumps(
            wiki_items=500,
            se_items=2000,
            mo_items=500,
            arxiv_items=1000,
            oeis_items=5000,
            proofpile_items=0,
            lean_items=1000,
            metamath_items=0,
            isabelle_items=0,
            coq_items=0,
            zbmath_items=0
        )
    elif config == 'medium':
        collect_from_dumps(
            wiki_items=5000,
            se_items=10000,
            mo_items=5000,
            arxiv_items=5000,
            oeis_items=20000,
            proofpile_items=0,
            lean_items=5000,
            metamath_items=0,
            isabelle_items=0,
            coq_items=0,
            zbmath_items=0
        )
    elif config == 'large':
        collect_from_dumps(
            wiki_items=50000,
            se_items=50000,
            mo_items=20000,
            arxiv_items=20000,
            oeis_items=50000,
            proofpile_items=5000,
            lean_items=10000,
            metamath_items=5000,
            isabelle_items=0,
            coq_items=0,
            zbmath_items=0
        )
    elif config == 'max':
        collect_from_dumps(
            wiki_items=50000,
            se_items=500000,
            mo_items=150000,
            arxiv_items=400000,
            oeis_items=370000,
            proofpile_items=20000,
            lean_items=150000,
            metamath_items=40000,
            isabelle_items=10000,
            coq_items=5000,
            zbmath_items=0  # API-based, optional
        )
    elif len(sys.argv) >= 12:
        # Custom counts
        collect_from_dumps(
            wiki_items=int(sys.argv[1]),
            se_items=int(sys.argv[2]),
            mo_items=int(sys.argv[3]),
            arxiv_items=int(sys.argv[4]),
            oeis_items=int(sys.argv[5]),
            proofpile_items=int(sys.argv[6]),
            lean_items=int(sys.argv[7]),
            metamath_items=int(sys.argv[8]),
            isabelle_items=int(sys.argv[9]),
            coq_items=int(sys.argv[10]),
            zbmath_items=int(sys.argv[11])
        )
    else:
        print(f"Unknown config: {config}")
        print_usage()
        sys.exit(1)
