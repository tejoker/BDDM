"""
Metamath parser - parses set.mm database
Download from: https://github.com/metamath/set.mm
File: set.mm (single file, ~40k theorems)
"""

import re
import logging
from typing import List, Dict, Iterator, Optional
from pathlib import Path
from .base_parser import BaseDumpParser

logger = logging.getLogger(__name__)


class MetamathParser(BaseDumpParser):
    """Parser for Metamath set.mm database"""

    def __init__(self, dump_path: str, skip_ids: Optional[set] = None):
        """
        Initialize Metamath parser

        Args:
            dump_path: Path to set.mm file
            skip_ids: Set of IDs to skip
        """
        super().__init__(dump_path, skip_ids)

    def parse(self, max_items: Optional[int] = None) -> List[Dict]:
        """Parse Metamath database"""
        if not self.validate_dump_path():
            return []

        items = []
        count = 0

        print(f"Parsing Metamath database: {self.dump_path}")

        for item in self._parse_metamath():
            if self._should_skip(item['id']):
                continue

            items.append(item)
            count += 1

            if count % 1000 == 0:
                print(f"  Parsed {count} theorems...")

            if max_items and count >= max_items:
                break

        print(f"Metamath parsing complete: {len(items)} theorems")
        return items

    def parse_iter(self, max_items: Optional[int] = None) -> Iterator[Dict]:
        """Stream parse Metamath database"""
        if not self.validate_dump_path():
            return

        count = 0
        for item in self._parse_metamath():
            if self._should_skip(item['id']):
                continue

            yield item
            count += 1

            if count % 1000 == 0:
                print(f"  Parsed {count} theorems...")

            if max_items and count >= max_items:
                break

    def _parse_metamath(self) -> Iterator[Dict]:
        """Parse Metamath set.mm file"""

        with open(self.dump_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Find all theorem blocks
        # Format: $( <comment> $) label $p statement $= proof $.
        pattern = r'\$\(\s*(.*?)\s*\$\)\s*([a-zA-Z0-9_.-]+)\s+\$p\s+(.*?)\s+\$=(.*?)\$\.'

        matches = re.finditer(pattern, content, re.DOTALL)

        for match in matches:
            comment = match.group(1).strip()
            label = match.group(2).strip()
            statement = match.group(3).strip()
            proof = match.group(4).strip()

            # Extract title from comment (first line)
            comment_lines = comment.split('\n')
            title = comment_lines[0].strip() if comment_lines else label

            # Create item
            yield self._create_standard_item(
                item_id=f"metamath_{label}",
                source='metamath',
                title=f"Theorem: {label}",
                content=f"{title}\n\nStatement: {statement}\n\n{comment}",
                theorem=statement,
                proof=proof,
                tags=['metamath', 'formal_proof'],
                url=f"https://us.metamath.org/mpeuni/{label}.html",
                metadata={
                    'label': label,
                    'comment': comment,
                    'language': 'metamath'
                }
            )


# Test
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        parser = MetamathParser(sys.argv[1])
        items = parser.parse(max_items=10)
        print(f"\nParsed {len(items)} items")
        if items:
            print(f"\nExample: {items[0]['title']}")
            print(f"Statement: {items[0]['theorem'][:100]}...")
    else:
        print("Usage: python metamath_parser.py <path_to_set.mm>")
        print("Download from: https://github.com/metamath/set.mm")
