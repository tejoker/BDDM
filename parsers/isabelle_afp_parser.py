"""
Isabelle AFP (Archive of Formal Proofs) parser
Download from: https://www.isa-afp.org/download.html
Format: Isabelle theory files (.thy)
"""

import re
import logging
from typing import List, Dict, Iterator, Optional
from pathlib import Path
from .base_parser import BaseDumpParser

logger = logging.getLogger(__name__)


class IsabelleAFPParser(BaseDumpParser):
    """Parser for Isabelle AFP"""

    def __init__(self, dump_path: str, skip_ids: Optional[set] = None):
        """
        Initialize Isabelle AFP parser

        Args:
            dump_path: Path to AFP root directory
            skip_ids: Set of IDs to skip
        """
        super().__init__(dump_path, skip_ids)

    def parse(self, max_items: Optional[int] = None) -> List[Dict]:
        """Parse Isabelle AFP"""
        if not self.validate_dump_path():
            return []

        items = []
        count = 0

        print(f"Parsing Isabelle AFP: {self.dump_path}")

        for item in self._parse_theory_files():
            if self._should_skip(item['id']):
                continue

            items.append(item)
            count += 1

            if count % 1000 == 0:
                print(f"  Parsed {count} theorems...")

            if max_items and count >= max_items:
                break

        print(f"Isabelle AFP parsing complete: {len(items)} theorems")
        return items

    def parse_iter(self, max_items: Optional[int] = None) -> Iterator[Dict]:
        """Stream parse Isabelle AFP"""
        if not self.validate_dump_path():
            return

        count = 0
        for item in self._parse_theory_files():
            if self._should_skip(item['id']):
                continue

            yield item
            count += 1

            if count % 1000 == 0:
                print(f"  Parsed {count} theorems...")

            if max_items and count >= max_items:
                break

    def _parse_theory_files(self) -> Iterator[Dict]:
        """Parse all .thy files"""

        theory_files = Path(self.dump_path).glob('**/*.thy')

        for theory_file in theory_files:
            try:
                with open(theory_file, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Extract theorems/lemmas
                for theorem in self._extract_theorems(content, theory_file):
                    yield theorem

            except Exception as e:
                logger.debug(f"Error parsing {theory_file}: {e}")
                continue

    def _extract_theorems(self, content: str, file_path: Path) -> Iterator[Dict]:
        """Extract theorems from Isabelle theory file"""

        # Pattern for theorem/lemma
        # theorem name: "statement" proof
        pattern = r'(theorem|lemma|corollary)\s+([a-zA-Z_][a-zA-Z0-9_\']*)\s*:\s*"([^"]+)"(.*?)(?=\n(?:theorem|lemma|corollary|end|$))'

        matches = re.finditer(pattern, content, re.DOTALL)

        for match in matches:
            theorem_type = match.group(1)
            theorem_name = match.group(2)
            statement = match.group(3).strip()
            proof = match.group(4).strip()

            # Create item
            item_id = f"isabelle_{file_path.stem}_{theorem_name}"

            yield self._create_standard_item(
                item_id=item_id,
                source='isabelle_afp',
                title=f"{theorem_type.capitalize()}: {theorem_name}",
                content=f'{theorem_type} {theorem_name}: "{statement}"\n{proof}',
                theorem=statement,
                proof=proof,
                tags=[theorem_type, file_path.parent.name],
                metadata={
                    'theorem_type': theorem_type,
                    'theorem_name': theorem_name,
                    'file': str(file_path),
                    'language': 'isabelle'
                }
            )


# Test
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        parser = IsabelleAFPParser(sys.argv[1])
        items = parser.parse(max_items=10)
        print(f"\nParsed {len(items)} items")
        if items:
            print(f"\nExample: {items[0]['title']}")
    else:
        print("Usage: python isabelle_afp_parser.py <path_to_afp_root>")
