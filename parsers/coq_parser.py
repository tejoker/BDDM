"""
Coq parser - parses Coq .v files
Download from: https://github.com/coq/coq or various Coq projects
Format: .v files
"""

import re
import logging
from typing import List, Dict, Iterator, Optional
from pathlib import Path
from .base_parser import BaseDumpParser

logger = logging.getLogger(__name__)


class CoqParser(BaseDumpParser):
    """Parser for Coq proof files"""

    def __init__(self, dump_path: str, skip_ids: Optional[set] = None):
        """
        Initialize Coq parser

        Args:
            dump_path: Path to Coq project root
            skip_ids: Set of IDs to skip
        """
        super().__init__(dump_path, skip_ids)

    def parse(self, max_items: Optional[int] = None) -> List[Dict]:
        """Parse Coq files"""
        if not self.validate_dump_path():
            return []

        items = []
        count = 0

        print(f"Parsing Coq files: {self.dump_path}")

        for item in self._parse_coq_files():
            if self._should_skip(item['id']):
                continue

            items.append(item)
            count += 1

            if count % 1000 == 0:
                print(f"  Parsed {count} theorems...")

            if max_items and count >= max_items:
                break

        print(f"Coq parsing complete: {len(items)} theorems")
        return items

    def parse_iter(self, max_items: Optional[int] = None) -> Iterator[Dict]:
        """Stream parse Coq files"""
        if not self.validate_dump_path():
            return

        count = 0
        for item in self._parse_coq_files():
            if self._should_skip(item['id']):
                continue

            yield item
            count += 1

            if count % 1000 == 0:
                print(f"  Parsed {count} theorems...")

            if max_items and count >= max_items:
                break

    def _parse_coq_files(self) -> Iterator[Dict]:
        """Parse all .v files"""

        coq_files = Path(self.dump_path).glob('**/*.v')

        for coq_file in coq_files:
            try:
                with open(coq_file, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Extract theorems
                for theorem in self._extract_theorems(content, coq_file):
                    yield theorem

            except Exception as e:
                logger.debug(f"Error parsing {coq_file}: {e}")
                continue

    def _extract_theorems(self, content: str, file_path: Path) -> Iterator[Dict]:
        """Extract theorems from Coq file"""

        # Pattern for Theorem/Lemma
        # Theorem name : statement. Proof. ... Qed.
        pattern = r'(Theorem|Lemma|Corollary|Proposition)\s+([a-zA-Z_][a-zA-Z0-9_\']*)\s*:\s*(.*?)\.\s*Proof\.(.*?)Qed\.'

        matches = re.finditer(pattern, content, re.DOTALL)

        for match in matches:
            theorem_type = match.group(1)
            theorem_name = match.group(2)
            statement = match.group(3).strip()
            proof = match.group(4).strip()

            # Create item
            item_id = f"coq_{file_path.stem}_{theorem_name}"

            yield self._create_standard_item(
                item_id=item_id,
                source='coq',
                title=f"{theorem_type}: {theorem_name}",
                content=f"{theorem_type} {theorem_name} : {statement}.\nProof.\n{proof}\nQed.",
                theorem=statement,
                proof=proof,
                tags=[theorem_type.lower(), file_path.parent.name],
                metadata={
                    'theorem_type': theorem_type,
                    'theorem_name': theorem_name,
                    'file': str(file_path),
                    'language': 'coq'
                }
            )


# Test
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        parser = CoqParser(sys.argv[1])
        items = parser.parse(max_items=10)
        print(f"\nParsed {len(items)} items")
        if items:
            print(f"\nExample: {items[0]['title']}")
    else:
        print("Usage: python coq_parser.py <path_to_coq_project>")
