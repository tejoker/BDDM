"""
Base parser class for dump-based data processing
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Iterator
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class BaseDumpParser(ABC):
    """Base class for all dump parsers"""

    def __init__(self, dump_path: str, skip_ids: Optional[set] = None):
        """
        Initialize parser

        Args:
            dump_path: Path to the data dump file or directory
            skip_ids: Set of IDs to skip (already collected)
        """
        self.dump_path = Path(dump_path)
        self.skip_ids = skip_ids or set()
        self.source_name = self.__class__.__name__.replace('Parser', '').lower()

    @abstractmethod
    def parse(self, max_items: Optional[int] = None) -> List[Dict]:
        """
        Parse the dump and return items

        Args:
            max_items: Maximum number of items to parse (None = all)

        Returns:
            List of parsed items in standard format
        """
        pass

    def parse_iter(self, max_items: Optional[int] = None) -> Iterator[Dict]:
        """
        Parse the dump and yield items one at a time (memory efficient)

        Args:
            max_items: Maximum number of items to parse (None = all)

        Yields:
            Parsed items in standard format
        """
        # Default implementation - subclasses can override for true streaming
        items = self.parse(max_items)
        for item in items:
            yield item

    def validate_dump_path(self) -> bool:
        """Check if dump path exists and is valid"""
        if not self.dump_path.exists():
            logger.error(f"Dump path does not exist: {self.dump_path}")
            return False
        return True

    def _should_skip(self, item_id: str) -> bool:
        """Check if item should be skipped based on skip_ids"""
        return item_id in self.skip_ids

    def _create_standard_item(
        self,
        item_id: str,
        source: str,
        title: str,
        content: str,
        **kwargs
    ) -> Dict:
        """
        Create item in standard format

        Args:
            item_id: Unique identifier
            source: Source name
            title: Item title
            content: Main content
            **kwargs: Additional fields (tags, score, url, metadata, etc.)

        Returns:
            Dictionary in standard format
        """
        item = {
            'id': item_id,
            'source': source,
            'title': title,
            'content': content,
        }

        # Add optional fields if provided
        for key in ['tags', 'score', 'url', 'created_date', 'metadata',
                    'question', 'answer', 'theorem', 'proof']:
            if key in kwargs:
                item[key] = kwargs[key]

        return item
