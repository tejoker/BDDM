"""
Stack Exchange dump parser
Download from: https://archive.org/download/stackexchange
File: math.stackexchange.com.7z (extract to get XML files)
"""

import xml.etree.ElementTree as ET
import logging
from typing import List, Dict, Iterator, Optional
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup
from .base_parser import BaseDumpParser

logger = logging.getLogger(__name__)


class StackExchangeDumpParser(BaseDumpParser):
    """Parser for Stack Exchange XML dumps"""

    def __init__(self, dump_dir: str, skip_ids: Optional[set] = None, min_score: int = 3):
        """
        Initialize Stack Exchange parser

        Args:
            dump_dir: Path to extracted Stack Exchange dump directory
            skip_ids: Set of IDs to skip
            min_score: Minimum question score to include
        """
        super().__init__(dump_dir, skip_ids)
        self.min_score = min_score
        self.posts_file = Path(dump_dir) / 'Posts.xml'
        self.answers = {}  # Cache answers by ID

    def parse(self, max_items: Optional[int] = None) -> List[Dict]:
        """Parse Stack Exchange dump and return Q&A pairs"""
        if not self.validate_dump_path():
            return []

        if not self.posts_file.exists():
            logger.error(f"Posts.xml not found in {self.dump_path}")
            return []

        items = []
        count = 0

        print(f"Parsing Stack Exchange dump: {self.posts_file}")
        print("  Step 1: Loading answers...")

        # First pass: load all answers
        self._load_answers()

        print(f"  Loaded {len(self.answers)} answers")
        print("  Step 2: Processing questions...")

        # Second pass: process questions with accepted answers
        for item in self._parse_questions():
            if self._should_skip(item['id']):
                continue

            items.append(item)
            count += 1

            if count % 1000 == 0:
                print(f"    Processed {count} Q&A pairs...")

            if max_items and count >= max_items:
                break

        print(f"Stack Exchange parsing complete: {len(items)} items")
        return items

    def parse_iter(self, max_items: Optional[int] = None) -> Iterator[Dict]:
        """Stream parse Stack Exchange dump (memory efficient)"""
        if not self.validate_dump_path():
            return

        if not self.posts_file.exists():
            logger.error(f"Posts.xml not found in {self.dump_path}")
            return

        print("  Loading answers...")
        self._load_answers()
        print(f"  Loaded {len(self.answers)} answers")

        count = 0
        for item in self._parse_questions():
            if self._should_skip(item['id']):
                continue

            yield item
            count += 1

            if count % 1000 == 0:
                print(f"    Processed {count} Q&A pairs...")

            if max_items and count >= max_items:
                break

    def _load_answers(self):
        """Load all answers into memory for lookup"""
        self.answers = {}

        for event, elem in ET.iterparse(str(self.posts_file), events=['end']):
            if elem.tag == 'row':
                post_type = elem.get('PostTypeId')
                # PostTypeId=2 means Answer
                if post_type == '2':
                    answer_id = elem.get('Id')
                    self.answers[answer_id] = {
                        'body': elem.get('Body', ''),
                        'score': int(elem.get('Score', 0)),
                        'creation_date': elem.get('CreationDate', '')
                    }
                elem.clear()

    def _parse_questions(self) -> Iterator[Dict]:
        """Parse questions and match with answers"""

        for event, elem in ET.iterparse(str(self.posts_file), events=['end']):
            if elem.tag == 'row':
                post_type = elem.get('PostTypeId')
                # PostTypeId=1 means Question
                if post_type == '1':
                    item = self._parse_question(elem)
                    if item:
                        yield item
                elem.clear()

    def _parse_question(self, elem) -> Optional[Dict]:
        """Parse a single question element"""
        # Get basic attributes
        question_id = elem.get('Id')
        title = elem.get('Title', '')
        body = elem.get('Body', '')
        tags = elem.get('Tags', '')
        score = int(elem.get('Score', 0))
        accepted_answer_id = elem.get('AcceptedAnswerId')
        creation_date = elem.get('CreationDate', '')

        # Filter by score
        if score < self.min_score:
            return None

        # Must have accepted answer
        if not accepted_answer_id:
            return None

        # Get the accepted answer
        answer = self.answers.get(accepted_answer_id)
        if not answer:
            return None

        # Clean HTML
        clean_question = self._clean_html(body)
        clean_answer = self._clean_html(answer['body'])

        # Parse tags
        tag_list = self._parse_tags(tags)

        return self._create_standard_item(
            item_id=f"se_{question_id}",
            source='stackexchange',
            title=title,
            question=clean_question,
            answer=clean_answer,
            content=f"{title}\n\n{clean_question}\n\nAnswer:\n{clean_answer}",
            tags=tag_list,
            score=score,
            answer_score=answer['score'],
            url=f"https://math.stackexchange.com/questions/{question_id}",
            created_date=creation_date,
            metadata={
                'type': 'qa_pair'
            }
        )

    def _parse_tags(self, tags_str: str) -> List[str]:
        """Parse tags from format <tag1><tag2><tag3>"""
        if not tags_str:
            return []
        # Extract tags between < and >
        import re
        return re.findall(r'<([^>]+)>', tags_str)

    def _clean_html(self, html_content: str) -> str:
        """Clean HTML and extract text/LaTeX"""
        if not html_content:
            return ''

        soup = BeautifulSoup(html_content, 'html.parser')

        # Preserve code blocks (often contain math)
        for code in soup.find_all('code'):
            code.replace_with(f"${code.get_text()}$")

        # Extract text
        text = soup.get_text()

        # Clean whitespace
        text = ' '.join(text.split())

        return text


class MathOverflowDumpParser(StackExchangeDumpParser):
    """Parser for MathOverflow dumps (same format as Stack Exchange)"""

    def __init__(self, dump_dir: str, skip_ids: Optional[set] = None, min_score: int = 3):
        super().__init__(dump_dir, skip_ids, min_score)
        self.source_name = 'mathoverflow'

    def _parse_question(self, elem) -> Optional[Dict]:
        """Parse question with MathOverflow-specific handling"""
        item = super()._parse_question(elem)
        if item:
            # Update source and ID prefix
            item['source'] = 'mathoverflow'
            item['id'] = item['id'].replace('se_', 'mo_')
            item['url'] = item['url'].replace('math.stackexchange.com', 'mathoverflow.net')
        return item


# Test
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        parser = StackExchangeDumpParser(sys.argv[1])
        items = parser.parse(max_items=10)
        print(f"\nParsed {len(items)} items")
        if items:
            print(f"\nExample: {items[0]['title']}")
            print(f"Tags: {items[0]['tags']}")
            print(f"Question: {items[0]['question'][:100]}...")
    else:
        print("Usage: python stackexchange_dump_parser.py <path_to_dump_dir>")
