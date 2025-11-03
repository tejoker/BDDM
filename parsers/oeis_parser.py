"""
OEIS (Online Encyclopedia of Integer Sequences) parser
Download from: https://oeis.org/stripped.gz
Format: Plain text with one sequence per line
Full database: https://oeis.org/wiki/Welcome#Compressed_Versions
"""

import gzip
import re
import logging
from typing import List, Dict, Iterator, Optional
from pathlib import Path
from .base_parser import BaseDumpParser

logger = logging.getLogger(__name__)


class OEISParser(BaseDumpParser):
    """Parser for OEIS database dumps"""

    def __init__(self, dump_path: str, skip_ids: Optional[set] = None):
        """
        Initialize OEIS parser

        Args:
            dump_path: Path to OEIS dump (e.g., stripped.gz or oeis.txt)
            skip_ids: Set of IDs to skip
        """
        super().__init__(dump_path, skip_ids)

    def parse(self, max_items: Optional[int] = None) -> List[Dict]:
        """Parse OEIS dump and return sequences"""
        if not self.validate_dump_path():
            return []

        items = []
        count = 0

        print(f"Parsing OEIS dump: {self.dump_path}")

        for item in self._parse_sequences():
            if self._should_skip(item['id']):
                continue

            items.append(item)
            count += 1

            if count % 10000 == 0:
                print(f"  Parsed {count} sequences...")

            if max_items and count >= max_items:
                break

        print(f"OEIS parsing complete: {len(items)} sequences")
        return items

    def parse_iter(self, max_items: Optional[int] = None) -> Iterator[Dict]:
        """Stream parse OEIS dump (memory efficient)"""
        if not self.validate_dump_path():
            return

        count = 0
        for item in self._parse_sequences():
            if self._should_skip(item['id']):
                continue

            yield item
            count += 1

            if count % 10000 == 0:
                print(f"  Parsed {count} sequences...")

            if max_items and count >= max_items:
                break

    def _parse_sequences(self) -> Iterator[Dict]:
        """Parse OEIS sequences from dump file"""

        # Open file (supports .gz or plain text)
        if str(self.dump_path).endswith('.gz'):
            file_obj = gzip.open(self.dump_path, 'rt', encoding='utf-8')
        else:
            file_obj = open(self.dump_path, 'r', encoding='utf-8')

        try:
            current_sequence = {}

            for line in file_obj:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                # Parse line format: %X AXXXXXX data
                match = re.match(r'%([A-Z])\s+(A\d{6})\s+(.*)', line)
                if not match:
                    continue

                field_type = match.group(1)
                seq_id = match.group(2)
                data = match.group(3)

                # Start new sequence if we see ID field
                if field_type == 'I':
                    # Yield previous sequence if exists
                    if current_sequence:
                        item = self._create_sequence_item(current_sequence)
                        if item:
                            yield item

                    # Start new sequence
                    current_sequence = {'id': seq_id}

                # Add data to current sequence
                if seq_id in current_sequence.get('id', ''):
                    self._add_field(current_sequence, field_type, data)

            # Yield last sequence
            if current_sequence:
                item = self._create_sequence_item(current_sequence)
                if item:
                    yield item

        finally:
            file_obj.close()

    def _add_field(self, sequence: Dict, field_type: str, data: str):
        """Add field to sequence dict"""
        field_map = {
            'I': 'id',
            'S': 'values',
            'T': 'terms',  # Alternative format for values
            'U': 'terms_unsigned',
            'N': 'name',
            'C': 'comment',
            'D': 'reference',
            'H': 'link',
            'F': 'formula',
            'e': 'example',
            'p': 'maple',
            'o': 'mathematica',
            't': 'other',
            'Y': 'cross_ref',
            'K': 'keywords',
            'A': 'author',
        }

        field_name = field_map.get(field_type)
        if not field_name:
            return

        # Accumulate multi-line fields
        if field_name in sequence:
            if isinstance(sequence[field_name], list):
                sequence[field_name].append(data)
            else:
                sequence[field_name] += ' ' + data
        else:
            if field_type in ['C', 'D', 'H', 'e', 'F']:
                sequence[field_name] = [data]
            else:
                sequence[field_name] = data

    def _create_sequence_item(self, seq_data: Dict) -> Optional[Dict]:
        """Create standardized item from sequence data"""
        seq_id = seq_data.get('id')
        if not seq_id:
            return None

        name = seq_data.get('name', '')
        if not name:
            return None

        # Get sequence values
        values = seq_data.get('values', seq_data.get('terms', ''))

        # Combine description
        content_parts = [name]

        # Add values
        if values:
            content_parts.append(f"Sequence: {values}")

        # Add formulas
        formulas = seq_data.get('formula', [])
        if formulas:
            if isinstance(formulas, list):
                content_parts.append(f"Formulas: {'; '.join(formulas)}")
            else:
                content_parts.append(f"Formula: {formulas}")

        # Add comments
        comments = seq_data.get('comment', [])
        if comments:
            if isinstance(comments, list):
                content_parts.extend(comments)
            else:
                content_parts.append(comments)

        # Add examples
        examples = seq_data.get('example', [])
        if examples:
            if isinstance(examples, list):
                content_parts.append("Examples: " + "; ".join(examples))
            else:
                content_parts.append(f"Example: {examples}")

        content = '\n'.join(content_parts)

        # Parse keywords as tags
        keywords = seq_data.get('keywords', '')
        tags = [k.strip() for k in keywords.split(',')] if keywords else []

        return self._create_standard_item(
            item_id=f"oeis_{seq_id}",
            source='oeis',
            title=f"{seq_id}: {name}",
            content=content,
            tags=tags,
            url=f"https://oeis.org/{seq_id}",
            metadata={
                'sequence_id': seq_id,
                'author': seq_data.get('author', ''),
                'type': 'integer_sequence',
                'keywords': keywords
            }
        )


# Test
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        parser = OEISParser(sys.argv[1])
        items = parser.parse(max_items=10)
        print(f"\nParsed {len(items)} items")
        if items:
            print(f"\nExample: {items[0]['title']}")
            print(f"Content preview: {items[0]['content'][:200]}...")
    else:
        print("Usage: python oeis_parser.py <path_to_oeis_dump>")
        print("Download from: https://oeis.org/stripped.gz")
