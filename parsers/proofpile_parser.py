"""
Proof-Pile dataset parser (includes ProofWiki, Stacks Project, etc.)
Download from: https://huggingface.co/datasets/hoskinson-center/proof-pile
Use: huggingface-cli or datasets library
"""

import json
import logging
from typing import List, Dict, Iterator, Optional
from pathlib import Path
from .base_parser import BaseDumpParser

logger = logging.getLogger(__name__)


class ProofPileParser(BaseDumpParser):
    """Parser for Proof-Pile HuggingFace dataset"""

    def __init__(self, dump_path: str, skip_ids: Optional[set] = None, source_filter: Optional[str] = None):
        """
        Initialize Proof-Pile parser

        Args:
            dump_path: Path to Proof-Pile data directory or JSON file
            skip_ids: Set of IDs to skip
            source_filter: Filter by source (e.g., 'proofwiki', 'stacks', 'trench')
        """
        super().__init__(dump_path, skip_ids)
        self.source_filter = source_filter

    def parse(self, max_items: Optional[int] = None) -> List[Dict]:
        """Parse Proof-Pile dataset"""
        if not self.validate_dump_path():
            return []

        items = []
        count = 0

        print(f"Parsing Proof-Pile dataset: {self.dump_path}")

        for item in self._parse_proofpile():
            if self._should_skip(item['id']):
                continue

            items.append(item)
            count += 1

            if count % 1000 == 0:
                print(f"  Parsed {count} items...")

            if max_items and count >= max_items:
                break

        print(f"Proof-Pile parsing complete: {len(items)} items")
        return items

    def parse_iter(self, max_items: Optional[int] = None) -> Iterator[Dict]:
        """Stream parse Proof-Pile dataset"""
        if not self.validate_dump_path():
            return

        count = 0
        for item in self._parse_proofpile():
            if self._should_skip(item['id']):
                continue

            yield item
            count += 1

            if count % 1000 == 0:
                print(f"  Parsed {count} items...")

            if max_items and count >= max_items:
                break

    def _parse_proofpile(self) -> Iterator[Dict]:
        """Parse Proof-Pile data"""

        # Try to use datasets library first
        try:
            from datasets import load_dataset
            dataset = load_dataset('hoskinson-center/proof-pile', split='train', streaming=True)

            for item in dataset:
                parsed_item = self._parse_proofpile_item(item)
                if parsed_item:
                    yield parsed_item

        except ImportError:
            # Fall back to manual JSON parsing if datasets library not available
            logger.info("datasets library not available, trying manual JSON parsing")
            yield from self._parse_json_files()

    def _parse_json_files(self) -> Iterator[Dict]:
        """Parse JSON files manually"""
        dump_path = Path(self.dump_path)

        # If it's a directory, find JSON files
        if dump_path.is_dir():
            json_files = list(dump_path.glob('*.json')) + list(dump_path.glob('**/*.json'))
        else:
            json_files = [dump_path]

        for json_file in json_files:
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    # Try JSONL format (one JSON per line)
                    for line in f:
                        try:
                            item = json.loads(line)
                            parsed_item = self._parse_proofpile_item(item)
                            if parsed_item:
                                yield parsed_item
                        except json.JSONDecodeError:
                            # Maybe it's a single big JSON
                            f.seek(0)
                            data = json.load(f)
                            if isinstance(data, list):
                                for item in data:
                                    parsed_item = self._parse_proofpile_item(item)
                                    if parsed_item:
                                        yield parsed_item
                            break
            except Exception as e:
                logger.error(f"Error parsing {json_file}: {e}")
                continue

    def _parse_proofpile_item(self, item: Dict) -> Optional[Dict]:
        """Parse a single Proof-Pile item"""

        # Proof-Pile items have different formats depending on source
        # Common fields: text, meta, source

        source = item.get('meta', {}).get('source', 'unknown')

        # Apply source filter
        if self.source_filter and source.lower() != self.source_filter.lower():
            return None

        text = item.get('text', '')
        if not text or len(text) < 100:
            return None

        # Try to extract theorem and proof
        theorem, proof = self._extract_theorem_proof(text, source)

        # Generate ID
        item_id = f"proofpile_{source}_{hash(text) % 1000000}"

        # Get title (if available)
        title = item.get('meta', {}).get('title', '')
        if not title:
            # Extract first line as title
            first_line = text.split('\n')[0][:100]
            title = first_line

        result = {
            'item_id': item_id,
            'source': f'proofpile_{source}',
            'title': title,
            'content': text,
            'metadata': {
                'original_source': source,
                'type': 'formal_proof'
            }
        }

        if theorem:
            result['theorem'] = theorem
        if proof:
            result['proof'] = proof

        return self._create_standard_item(**result)

    def _extract_theorem_proof(self, text: str, source: str) -> tuple:
        """Try to extract theorem and proof from text"""

        # Different sources have different formats
        if source.lower() == 'proofwiki':
            # ProofWiki format: Theorem\n===\nstatement\n\nProof\n===\nproof
            import re
            theorem_match = re.search(r'Theorem.*?===\s*(.*?)\s*(?:Proof|$)', text, re.DOTALL | re.IGNORECASE)
            proof_match = re.search(r'Proof.*?===\s*(.*?)(?:$|\\end)', text, re.DOTALL | re.IGNORECASE)

            theorem = theorem_match.group(1).strip() if theorem_match else None
            proof = proof_match.group(1).strip() if proof_match else None

            return theorem, proof

        # For other sources, return None (full text is in content)
        return None, None


# Test
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        parser = ProofPileParser(sys.argv[1])
        items = parser.parse(max_items=10)
        print(f"\nParsed {len(items)} items")
        if items:
            print(f"\nExample: {items[0]['title']}")
            print(f"Source: {items[0]['source']}")
    else:
        print("Usage: python proofpile_parser.py <path_to_proofpile_data>")
        print("Or install datasets library and it will download automatically")
