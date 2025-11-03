"""
Lean Mathlib parser - extracts theorems and proofs from Lean 4 files
Clone from: https://github.com/leanprover-community/mathlib4
"""

import re
import logging
from typing import List, Dict, Iterator, Optional
from pathlib import Path
from .base_parser import BaseDumpParser

logger = logging.getLogger(__name__)


class LeanMathlibParser(BaseDumpParser):
    """Parser for Lean Mathlib repository"""

    def __init__(self, dump_path: str, skip_ids: Optional[set] = None):
        """
        Initialize Lean Mathlib parser

        Args:
            dump_path: Path to mathlib4 repository root
            skip_ids: Set of IDs to skip
        """
        super().__init__(dump_path, skip_ids)
        self.mathlib_dir = Path(dump_path) / 'Mathlib'

    def parse(self, max_items: Optional[int] = None) -> List[Dict]:
        """Parse Lean Mathlib files"""
        if not self.validate_dump_path():
            return []

        if not self.mathlib_dir.exists():
            logger.error(f"Mathlib directory not found: {self.mathlib_dir}")
            return []

        items = []
        count = 0

        print(f"Parsing Lean Mathlib: {self.mathlib_dir}")

        for item in self._parse_lean_files():
            if self._should_skip(item['id']):
                continue

            items.append(item)
            count += 1

            if count % 1000 == 0:
                print(f"  Parsed {count} theorems...")

            if max_items and count >= max_items:
                break

        print(f"Lean Mathlib parsing complete: {len(items)} theorems")
        return items

    def parse_iter(self, max_items: Optional[int] = None) -> Iterator[Dict]:
        """Stream parse Lean Mathlib"""
        if not self.validate_dump_path():
            return

        if not self.mathlib_dir.exists():
            logger.error(f"Mathlib directory not found: {self.mathlib_dir}")
            return

        count = 0
        for item in self._parse_lean_files():
            if self._should_skip(item['id']):
                continue

            yield item
            count += 1

            if count % 1000 == 0:
                print(f"  Parsed {count} theorems...")

            if max_items and count >= max_items:
                break

    def _parse_lean_files(self) -> Iterator[Dict]:
        """Parse all .lean files in Mathlib"""

        # Find all .lean files
        lean_files = self.mathlib_dir.glob('**/*.lean')

        for lean_file in lean_files:
            try:
                with open(lean_file, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Extract theorems from this file
                for theorem in self._extract_theorems(content, lean_file):
                    yield theorem

            except Exception as e:
                logger.debug(f"Error parsing {lean_file}: {e}")
                continue

    def _extract_theorems(self, content: str, file_path: Path) -> Iterator[Dict]:
        """Extract theorems/lemmas from Lean file"""

        # Pattern for theorem/lemma declarations
        # theorem name (args) : statement := proof
        pattern = r'(theorem|lemma|def)\s+([a-zA-Z_][a-zA-Z0-9_\']*)\s*(.*?)(?=\n(?:theorem|lemma|def|end|$))'

        matches = re.finditer(pattern, content, re.DOTALL)

        for match in matches:
            theorem_type = match.group(1)
            theorem_name = match.group(2)
            theorem_body = match.group(3).strip()

            # Split into statement and proof
            statement, proof = self._split_statement_proof(theorem_body)

            if not statement:
                continue

            # Create item
            item_id = f"lean_{file_path.stem}_{theorem_name}"

            # Get module path
            rel_path = file_path.relative_to(self.dump_path)
            module_path = str(rel_path).replace('/', '.').replace('.lean', '')

            yield self._create_standard_item(
                item_id=item_id,
                source='lean_mathlib',
                title=f"{theorem_type.capitalize()}: {theorem_name}",
                content=f"{theorem_type} {theorem_name} {theorem_body}",
                theorem=statement,
                proof=proof or "proof omitted",
                tags=[theorem_type, module_path.split('.')[1] if '.' in module_path else ''],
                metadata={
                    'theorem_type': theorem_type,
                    'theorem_name': theorem_name,
                    'module': module_path,
                    'file': str(file_path),
                    'language': 'lean4'
                }
            )

    def _split_statement_proof(self, body: str) -> tuple:
        """Split theorem body into statement and proof"""

        # Look for := separator
        if ':=' in body:
            parts = body.split(':=', 1)
            statement = parts[0].strip()
            proof = parts[1].strip() if len(parts) > 1 else None
        else:
            # No proof, just statement
            statement = body.strip()
            proof = None

        return statement, proof


# Test
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        parser = LeanMathlibParser(sys.argv[1])
        items = parser.parse(max_items=10)
        print(f"\nParsed {len(items)} items")
        if items:
            print(f"\nExample: {items[0]['title']}")
            print(f"Statement: {items[0]['theorem'][:100]}...")
    else:
        print("Usage: python lean_mathlib_parser.py <path_to_mathlib4_repo>")
        print("Clone from: https://github.com/leanprover-community/mathlib4")
