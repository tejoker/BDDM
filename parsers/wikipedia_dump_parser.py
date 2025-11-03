"""
Wikipedia dump parser - processes Wikipedia XML dumps
Download from: https://dumps.wikimedia.org/enwiki/latest/
File: enwiki-latest-pages-articles.xml.bz2
"""

import xml.etree.ElementTree as ET
import bz2
import re
import logging
from typing import List, Dict, Iterator, Optional
from pathlib import Path
from .base_parser import BaseDumpParser

logger = logging.getLogger(__name__)


class WikipediaDumpParser(BaseDumpParser):
    """Parser for Wikipedia XML dumps"""

    # Math-related categories to filter
    MATH_CATEGORIES = {
        'mathematics', 'mathematical', 'theorem', 'proof', 'algebra', 'geometry',
        'calculus', 'topology', 'analysis', 'number theory', 'logic', 'set theory',
        'graph theory', 'combinatorics', 'statistics', 'probability', 'equation',
        'function', 'derivative', 'integral', 'matrix', 'vector', 'tensor'
    }

    def __init__(self, dump_path: str, skip_ids: Optional[set] = None):
        super().__init__(dump_path, skip_ids)
        self.namespace = {'wiki': 'http://www.mediawiki.org/xml/export-0.10/'}

    def parse(self, max_items: Optional[int] = None) -> List[Dict]:
        """Parse Wikipedia dump and return math articles"""
        if not self.validate_dump_path():
            return []

        items = []
        count = 0

        print(f"Parsing Wikipedia dump: {self.dump_path}")

        for item in self._parse_dump():
            if self._should_skip(item['id']):
                continue

            items.append(item)
            count += 1

            if count % 1000 == 0:
                print(f"  Parsed {count} math articles...")

            if max_items and count >= max_items:
                break

        print(f"Wikipedia parsing complete: {len(items)} articles")
        return items

    def parse_iter(self, max_items: Optional[int] = None) -> Iterator[Dict]:
        """Stream parse Wikipedia dump (memory efficient)"""
        if not self.validate_dump_path():
            return

        count = 0
        for item in self._parse_dump():
            if self._should_skip(item['id']):
                continue

            yield item
            count += 1

            if count % 1000 == 0:
                print(f"  Parsed {count} math articles...")

            if max_items and count >= max_items:
                break

    def _parse_dump(self) -> Iterator[Dict]:
        """Parse Wikipedia XML dump file"""

        # Open file (supports .bz2 or plain .xml)
        if str(self.dump_path).endswith('.bz2'):
            file_obj = bz2.open(self.dump_path, 'rt', encoding='utf-8')
        else:
            file_obj = open(self.dump_path, 'r', encoding='utf-8')

        try:
            # Use iterparse for memory efficiency
            for event, elem in ET.iterparse(file_obj, events=['end']):
                if elem.tag == '{http://www.mediawiki.org/xml/export-0.10/}page':
                    item = self._parse_page(elem)
                    if item:
                        yield item
                    # Clear element to free memory
                    elem.clear()
        finally:
            file_obj.close()

    def _parse_page(self, page_elem) -> Optional[Dict]:
        """Parse a single Wikipedia page element"""
        ns = self.namespace

        # Get title
        title_elem = page_elem.find('wiki:title', ns)
        if title_elem is None:
            return None
        title = title_elem.text

        # Skip non-article pages
        if ':' in title or title.startswith('Wikipedia:') or title.startswith('Template:'):
            return None

        # Get page text
        revision = page_elem.find('wiki:revision', ns)
        if revision is None:
            return None

        text_elem = revision.find('wiki:text', ns)
        if text_elem is None or text_elem.text is None:
            return None

        text = text_elem.text

        # Check if it's a math article
        if not self._is_math_article(title, text):
            return None

        # Extract categories
        categories = self._extract_categories(text)

        # Clean text (remove wiki markup)
        clean_text = self._clean_wikitext(text)

        # Must have substantial content
        if len(clean_text) < 200:
            return None

        return self._create_standard_item(
            item_id=f"wikipedia_{title.replace(' ', '_')}",
            source='wikipedia',
            title=title,
            content=clean_text,
            tags=categories[:5],
            url=f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
            metadata={
                'language': 'en',
                'type': 'encyclopedia'
            }
        )

    def _is_math_article(self, title: str, text: str) -> bool:
        """Check if article is math-related"""
        # Check title
        title_lower = title.lower()
        if any(keyword in title_lower for keyword in self.MATH_CATEGORIES):
            return True

        # Check categories
        categories = self._extract_categories(text)
        for cat in categories:
            cat_lower = cat.lower()
            if any(keyword in cat_lower for keyword in self.MATH_CATEGORIES):
                return True

        # Check for math indicators in text (first 1000 chars)
        text_sample = text[:1000].lower()
        math_indicators = ['<math>', '{{math', 'theorem', 'proof', 'lemma', 'corollary']
        if any(indicator in text_sample for indicator in math_indicators):
            return True

        return False

    def _extract_categories(self, text: str) -> List[str]:
        """Extract categories from wikitext"""
        categories = []
        # Find [[Category:Something]]
        for match in re.finditer(r'\[\[Category:([^\]|]+)', text):
            cat = match.group(1).strip()
            categories.append(cat)
        return categories

    def _clean_wikitext(self, text: str) -> str:
        """Remove wiki markup and extract readable text"""
        # Remove templates
        text = re.sub(r'\{\{[^}]+\}\}', '', text)
        # Remove file/image links
        text = re.sub(r'\[\[File:[^\]]+\]\]', '', text)
        text = re.sub(r'\[\[Image:[^\]]+\]\]', '', text)
        # Convert wiki links [[Link|Text]] -> Text or [[Link]] -> Link
        text = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]+)\]\]', r'\1', text)
        # Remove external links
        text = re.sub(r'\[http[^\]]+\]', '', text)
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        # Remove categories
        text = re.sub(r'\[\[Category:[^\]]+\]\]', '', text)
        # Clean up whitespace
        text = re.sub(r'\n+', '\n', text)
        text = ' '.join(text.split())
        return text.strip()


# Test
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        parser = WikipediaDumpParser(sys.argv[1])
        items = parser.parse(max_items=10)
        print(f"\nParsed {len(items)} items")
        if items:
            print(f"\nExample: {items[0]['title']}")
            print(f"Content: {items[0]['content'][:200]}...")
    else:
        print("Usage: python wikipedia_dump_parser.py <path_to_dump.xml.bz2>")
