"""
MathOverflow Scraper
Research-level mathematics Q&A (uses Stack Exchange API)
"""

import aiohttp
import asyncio
import logging
from typing import List, Dict, Optional
import html

logger = logging.getLogger(__name__)


class MathOverflowScraper:
    """Scraper for MathOverflow (research-level math Q&A)"""
    
    BASE_URL = "https://api.stackexchange.com/2.3"
    SITE = "mathoverflow.net"
    
    def __init__(self, api_key: Optional[str] = None):
        self.session = None
        self.current_page = 1  # Track pagination state
        self.api_key = api_key  # Optional API key for higher limits
    
    async def scrape(self, max_items: int = None, start_page: int = None) -> List[Dict]:
        """
        Scrape MathOverflow for research-level math questions with answers
        
        Similar to Stack Exchange but higher level content
        
        Args:
            max_items: Maximum number of items to collect in this call
            start_page: Page to start from (if None, uses self.current_page)
        """
        all_items = []
        
        async with aiohttp.ClientSession() as session:
            self.session = session
            
            # Get questions with accepted answers
            page = start_page if start_page is not None else self.current_page
            max_items = max_items or 20
            empty_pages = 0  # Track consecutive empty pages
            
            while len(all_items) < max_items:
                items = await self._fetch_page(page)
                
                if not items:
                    empty_pages += 1
                    # Stop if 3 consecutive empty pages
                    if empty_pages >= 3:
                        break
                    page += 1
                    continue
                
                empty_pages = 0  # Reset on successful page
                all_items.extend(items)
                
                logger.info(f"MathOverflow - Page {page}: {len(all_items)} items total")
                
                page += 1
                await asyncio.sleep(0.1)  # Rate limiting
        
        # Update current page for next call
        self.current_page = page
        
        logger.info(f"MathOverflow scraping terminé: {len(all_items[:max_items])} items")
        return all_items[:max_items]
    
    async def _fetch_page(self, page: int) -> List[Dict]:
        """Fetch one page of questions"""
        url = f"{self.BASE_URL}/questions"
        
        params = {
            'page': page,
            'pagesize': 100,  # Increased from 30 to get more results
            'order': 'desc',
            'sort': 'votes',  # High-quality content
            'site': self.SITE,
            'filter': 'withbody',  # Include question body
        }
        
        if self.api_key:
            params['key'] = self.api_key
        
        try:
            async with self.session.get(url, params=params) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.warning(f"MathOverflow API returned {response.status}: {error_text}")
                    print(f"MathOverflow API returned {response.status}")
                    print(f"URL: {url}")
                    print(f"Params: {params}")
                    print(f"Response: {error_text[:200]}")
                    return []
                
                data = await response.json()
                items = data.get('items', [])
                
                result = []
                for item in items[:20]:  # Limit processing
                    # Only include if has accepted answer
                    if not item.get('accepted_answer_id'):
                        continue
                    
                    # Get the accepted answer
                    answer = await self._fetch_answer(item['accepted_answer_id'])
                    
                    if answer:
                        result.append({
                            'id': f"mathoverflow_{item['question_id']}",
                            'source': 'mathoverflow',
                            'title': html.unescape(item.get('title', '')),
                            'question': self._clean_html(item.get('body', '')),
                            'answer': self._clean_html(answer),
                            'tags': item.get('tags', []),
                            'score': item.get('score', 0),
                            'url': item.get('link', ''),
                            'metadata': {
                                'language': 'en',
                                'view_count': item.get('view_count', 0),
                                'answer_count': item.get('answer_count', 0),
                                'level': 'research'
                            }
                        })
                        
                        await asyncio.sleep(0.2)  # Rate limit between answer fetches
                
                return result
        
        except Exception as e:
            logger.warning(f"Error fetching MathOverflow page {page}: {e}")
            return []
    
    async def _fetch_answer(self, answer_id: int) -> str:
        """Fetch answer body by ID"""
        url = f"{self.BASE_URL}/answers/{answer_id}"
        
        params = {
            'site': self.SITE,
            'filter': 'withbody'  # Include body
        }
        
        if self.api_key:
            params['key'] = self.api_key
        
        try:
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    items = data.get('items', [])
                    if items:
                        return items[0].get('body', '')
        except Exception as e:
            logger.debug(f"Error fetching answer {answer_id}: {e}")
        
        return ''
    
    def _clean_html(self, text: str) -> str:
        """Remove HTML tags, keep text and LaTeX"""
        if not text:
            return ""
        
        import re
        
        # Remove HTML tags but keep content
        text = re.sub(r'<code>(.*?)</code>', r'`\1`', text, flags=re.DOTALL)
        text = re.sub(r'<pre><code>(.*?)</code></pre>', r'\n```\n\1\n```\n', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', '', text)
        
        # Clean HTML entities
        text = html.unescape(text)
        
        # Clean whitespace
        text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)
        
        return text.strip()


# Test
async def test_scraper():
    scraper = MathOverflowScraper()
    items = await scraper.scrape(max_items=3)
    
    print(f"\n✓ Collected {len(items)} MathOverflow items")
    if items:
        print("\nExample:")
        item = items[0]
        print(f"  Title: {item['title'][:80]}")
        print(f"  Score: {item['score']}")
        print(f"  Tags: {item['tags']}")


if __name__ == "__main__":
    asyncio.run(test_scraper())
