"""
ArXiv Kaggle dataset parser
Download from: https://www.kaggle.com/datasets/Cornell-University/arxiv
Files: arxiv-metadata-oai-snapshot.json + source files
"""

import json
import tarfile
import re
import logging
from typing import List, Dict, Iterator, Optional
from pathlib import Path
from .base_parser import BaseDumpParser

logger = logging.getLogger(__name__)


class ArxivKaggleParser(BaseDumpParser):
    """Parser for ArXiv Kaggle dataset"""

    def __init__(self, dump_path: str, skip_ids: Optional[set] = None, extract_proofs: bool = True):
        """
        Initialize ArXiv parser

        Args:
            dump_path: Path to arxiv-metadata-oai-snapshot.json
            skip_ids: Set of IDs to skip
            extract_proofs: If True, extract theorem-proof pairs from LaTeX
        """
        super().__init__(dump_path, skip_ids)
        self.extract_proofs = extract_proofs
        self.sources_dir = Path(dump_path).parent / 'arxiv-sources'  # Assumes sources are here

    def parse(self, max_items: Optional[int] = None, categories: Optional[List[str]] = None) -> List[Dict]:
        """
        Parse ArXiv metadata and optionally extract proofs

        Args:
            max_items: Maximum items to parse
            categories: List of categories to filter (e.g., ['math.AG', 'math.NT'])
                       If None, includes all math.* categories
        """
        if not self.validate_dump_path():
            return []

        if categories is None:
            categories = ['math.']  # All math categories

        items = []
        count = 0

        print(f"Parsing ArXiv metadata: {self.dump_path}")

        for item in self._parse_metadata(categories):
            if self._should_skip(item['id']):
                continue

            items.append(item)
            count += 1

            if count % 1000 == 0:
                print(f"  Parsed {count} papers...")

            if max_items and count >= max_items:
                break

        print(f"ArXiv parsing complete: {len(items)} papers")
        return items

    def parse_iter(self, max_items: Optional[int] = None, categories: Optional[List[str]] = None) -> Iterator[Dict]:
        """Stream parse ArXiv metadata"""
        if not self.validate_dump_path():
            return

        if categories is None:
            categories = ['math.']

        count = 0
        for item in self._parse_metadata(categories):
            if self._should_skip(item['id']):
                continue

            yield item
            count += 1

            if count % 1000 == 0:
                print(f"  Parsed {count} papers...")

            if max_items and count >= max_items:
                break

    def _parse_metadata(self, categories: List[str]) -> Iterator[Dict]:
        """Parse ArXiv metadata JSON file"""

        with open(self.dump_path, 'r') as f:
            for line in f:
                try:
                    paper = json.loads(line)

                    # Filter by category
                    paper_cats = paper.get('categories', '').split()
                    if not any(any(cat.startswith(filter_cat) for filter_cat in categories) for cat in paper_cats):
                        continue

                    arxiv_id = paper.get('id')
                    title = paper.get('title', '').strip()
                    abstract = paper.get('abstract', '').strip()
                    authors = paper.get('authors', '')

                    # Clean abstract (remove newlines)
                    abstract = ' '.join(abstract.split())

                    # Extract proofs if LaTeX source is available
                    proofs = []
                    if self.extract_proofs:
                        proofs = self._extract_proofs_from_source(arxiv_id)

                    # Create item
                    item_data = {
                        'item_id': f"arxiv_{arxiv_id}",
                        'source': 'arxiv',
                        'title': title,
                        'content': abstract,
                        'tags': paper_cats[:5],
                        'url': f"https://arxiv.org/abs/{arxiv_id}",
                        'created_date': paper.get('versions', [{}])[0].get('created', ''),
                        'metadata': {
                            'authors': authors,
                            'categories': paper_cats,
                            'num_proofs': len(proofs)
                        }
                    }

                    # Add proofs if found
                    if proofs:
                        item_data['proofs'] = proofs

                    yield self._create_standard_item(**item_data)

                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    logger.warning(f"Error parsing paper: {e}")
                    continue

    def _extract_proofs_from_source(self, arxiv_id: str) -> List[Dict]:
        """
        Extract theorem-proof pairs from LaTeX source

        Args:
            arxiv_id: ArXiv paper ID

        Returns:
            List of theorem-proof pairs
        """
        if not self.sources_dir.exists():
            return []

        # Find source file (format: arxiv_id.tar.gz or arxiv_id.gz)
        source_file = self._find_source_file(arxiv_id)
        if not source_file:
            return []

        try:
            # Extract and parse LaTeX
            latex_content = self._extract_latex(source_file)
            if not latex_content:
                return []

            # Find theorem-proof pairs
            return self._parse_theorem_proofs(latex_content)

        except Exception as e:
            logger.debug(f"Could not extract proofs from {arxiv_id}: {e}")
            return []

    def _find_source_file(self, arxiv_id: str) -> Optional[Path]:
        """Find source file for given ArXiv ID"""
        # Try different formats
        for ext in ['.tar.gz', '.gz', '.tar']:
            source_file = self.sources_dir / f"{arxiv_id}{ext}"
            if source_file.exists():
                return source_file
        return None

    def _extract_latex(self, source_file: Path) -> str:
        """Extract LaTeX content from source file"""
        # This is a simplified version - full implementation would handle
        # multiple .tex files, includes, etc.
        try:
            if source_file.suffix == '.gz' and not str(source_file).endswith('.tar.gz'):
                # Single .tex.gz file
                import gzip
                with gzip.open(source_file, 'rt', encoding='utf-8', errors='ignore') as f:
                    return f.read()
            elif '.tar' in source_file.suffixes:
                # Tar archive with multiple files
                with tarfile.open(source_file, 'r:*') as tar:
                    # Find main .tex file
                    for member in tar.getmembers():
                        if member.name.endswith('.tex'):
                            f = tar.extractfile(member)
                            if f:
                                return f.read().decode('utf-8', errors='ignore')
        except Exception as e:
            logger.debug(f"Error extracting LaTeX: {e}")
        return ''

    def _parse_theorem_proofs(self, latex_content: str) -> List[Dict]:
        """Extract theorem-proof pairs from LaTeX"""
        proofs = []

        # Find theorem environments followed by proof environments
        theorem_pattern = r'\\begin\{(theorem|lemma|proposition|corollary)\}(.*?)\\end\{\1\}'
        proof_pattern = r'\\begin\{proof\}(.*?)\\end\{proof\}'

        theorems = re.finditer(theorem_pattern, latex_content, re.DOTALL | re.IGNORECASE)

        for theorem_match in theorems:
            theorem_type = theorem_match.group(1)
            theorem_text = theorem_match.group(2).strip()

            # Look for proof immediately after theorem
            search_start = theorem_match.end()
            search_end = search_start + 5000  # Look within next 5000 chars

            proof_match = re.search(
                proof_pattern,
                latex_content[search_start:search_end],
                re.DOTALL | re.IGNORECASE
            )

            if proof_match:
                proof_text = proof_match.group(1).strip()

                proofs.append({
                    'theorem_type': theorem_type,
                    'theorem': self._clean_latex(theorem_text),
                    'proof': self._clean_latex(proof_text)
                })

                if len(proofs) >= 10:  # Limit proofs per paper
                    break

        return proofs

    def _clean_latex(self, text: str) -> str:
        """Basic LaTeX cleaning"""
        # Remove comments
        text = re.sub(r'%.*$', '', text, flags=re.MULTILINE)
        # Remove extra whitespace
        text = ' '.join(text.split())
        return text.strip()


# Test
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        parser = ArxivKaggleParser(sys.argv[1], extract_proofs=False)
        items = parser.parse(max_items=10, categories=['math.'])
        print(f"\nParsed {len(items)} items")
        if items:
            print(f"\nExample: {items[0]['title']}")
            print(f"Categories: {items[0]['tags']}")
    else:
        print("Usage: python arxiv_kaggle_parser.py <path_to_arxiv-metadata-oai-snapshot.json>")
