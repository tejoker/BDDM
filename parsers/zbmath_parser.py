"""
zbMATH Open API parser
Access via: OAI-PMH protocol or REST API
URL: https://zbmath.org/
Documentation: https://oai.zbmath.org/
"""

import asyncio
import aiohttp
import logging
from typing import List, Dict, Iterator, Optional
from datetime import datetime
from .base_parser import BaseDumpParser

logger = logging.getLogger(__name__)


class ZbMATHParser(BaseDumpParser):
    """Parser for zbMATH Open via OAI-PMH API"""

    OAI_URL = "https://oai.zbmath.org/v1/"
    API_URL = "https://api.zbmath.org/v1/"

    def __init__(self, dump_path: str = None, skip_ids: Optional[set] = None, use_api: bool = True):
        """
        Initialize zbMATH parser

        Args:
            dump_path: Not used (API-based)
            skip_ids: Set of IDs to skip
            use_api: If True, use REST API; if False, use OAI-PMH
        """
        super().__init__(dump_path or "zbmath_api", skip_ids)
        self.use_api = use_api

    def parse(self, max_items: Optional[int] = None) -> List[Dict]:
        """Parse zbMATH via API"""
        return asyncio.run(self._parse_async(max_items))

    async def _parse_async(self, max_items: Optional[int] = None) -> List[Dict]:
        """Async parse zbMATH"""
        items = []
        count = 0

        print(f"Fetching zbMATH metadata via {'API' if self.use_api else 'OAI-PMH'}")

        async with aiohttp.ClientSession() as session:
            if self.use_api:
                async for item in self._fetch_from_api(session, max_items):
                    if self._should_skip(item['id']):
                        continue

                    items.append(item)
                    count += 1

                    if count % 100 == 0:
                        print(f"  Fetched {count} records...")

                    if max_items and count >= max_items:
                        break
            else:
                async for item in self._fetch_from_oai(session, max_items):
                    if self._should_skip(item['id']):
                        continue

                    items.append(item)
                    count += 1

                    if count % 100 == 0:
                        print(f"  Fetched {count} records...")

                    if max_items and count >= max_items:
                        break

        print(f"zbMATH fetching complete: {len(items)} records")
        return items

    async def _fetch_from_api(self, session: aiohttp.ClientSession, max_items: Optional[int]) -> Iterator[Dict]:
        """Fetch from zbMATH REST API"""
        # Search for mathematics papers
        page = 0
        per_page = 100

        while True:
            params = {
                'page': page,
                'results_per_page': per_page,
                'format': 'json'
            }

            try:
                async with session.get(f"{self.API_URL}document/", params=params) as response:
                    if response.status != 200:
                        logger.error(f"API error: {response.status}")
                        break

                    data = await response.json()
                    results = data.get('result', [])

                    if not results:
                        break

                    for record in results:
                        item = self._parse_zbmath_record(record)
                        if item:
                            yield item

                    page += 1
                    await asyncio.sleep(0.5)  # Rate limiting

            except Exception as e:
                logger.error(f"Error fetching page {page}: {e}")
                break

    async def _fetch_from_oai(self, session: aiohttp.ClientSession, max_items: Optional[int]) -> Iterator[Dict]:
        """Fetch from zbMATH OAI-PMH"""
        # OAI-PMH ListRecords request
        params = {
            'verb': 'ListRecords',
            'metadataPrefix': 'oai_dc',
            'set': 'mathematics'  # Filter for mathematics
        }

        resumption_token = None

        while True:
            if resumption_token:
                params = {
                    'verb': 'ListRecords',
                    'resumptionToken': resumption_token
                }

            try:
                async with session.get(self.OAI_URL, params=params) as response:
                    if response.status != 200:
                        logger.error(f"OAI-PMH error: {response.status}")
                        break

                    # Parse XML response
                    text = await response.text()
                    # Simplified parsing - full implementation would use XML parser
                    # This is a placeholder

                    # Check for resumption token
                    import re
                    token_match = re.search(r'<resumptionToken[^>]*>([^<]+)</resumptionToken>', text)
                    resumption_token = token_match.group(1) if token_match else None

                    if not resumption_token:
                        break

                    await asyncio.sleep(1)  # Rate limiting

            except Exception as e:
                logger.error(f"Error fetching OAI-PMH: {e}")
                break

    def _parse_zbmath_record(self, record: Dict) -> Optional[Dict]:
        """Parse zbMATH record"""

        zbmath_id = record.get('de', '')
        title = record.get('title', '')
        authors = record.get('authors', [])
        abstract = record.get('abstract', '')
        year = record.get('year', '')

        if not title:
            return None

        author_names = [a.get('name', '') for a in authors] if isinstance(authors, list) else []

        return self._create_standard_item(
            item_id=f"zbmath_{zbmath_id}",
            source='zbmath',
            title=title,
            content=abstract or title,
            tags=['research_paper', 'metadata'],
            url=f"https://zbmath.org/{zbmath_id}",
            created_date=str(year),
            metadata={
                'zbmath_id': zbmath_id,
                'authors': author_names,
                'year': year,
                'type': 'metadata_only'
            }
        )


# Test
if __name__ == "__main__":
    parser = ZbMATHParser()
    items = parser.parse(max_items=10)
    print(f"\nFetched {len(items)} items")
    if items:
        print(f"\nExample: {items[0]['title']}")
