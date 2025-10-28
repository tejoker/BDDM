"""
Wikipedia Math Scraper
Mathematics articles from Wikipedia
"""

import aiohttp
import asyncio
import logging
from typing import List, Dict
import re

logger = logging.getLogger(__name__)


class WikipediaMathScraper:
    """Scraper for Wikipedia mathematics articles"""
    
    API_URL = "https://en.wikipedia.org/w/api.php"
    
    # Important math topics
    TOPICS = [
        "Calculus", "Linear algebra", "Abstract algebra", "Number theory",
        "Topology", "Real analysis", "Complex analysis", "Differential equations",
        "Group theory", "Ring theory", "Field theory", "Galois theory",
        "Measure theory", "Functional analysis", "Algebraic topology",
        "Differential geometry", "Algebraic geometry", "Category theory",
        "Set theory", "Mathematical logic", "Probability theory", "Statistics"
    ]
    
    def __init__(self):
        self.session = None
    
    async def scrape(self, max_items: int = None) -> List[Dict]:
        """Scrape Wikipedia math articles"""
        all_items = []
        max_items = max_items or 20
        
        async with aiohttp.ClientSession() as session:
            self.session = session
            
            for topic in self.TOPICS[:max_items]:
                item = await self._scrape_article(topic)
                if item:
                    all_items.append(item)
                    logger.info(f"Wikipedia - {topic}: collected")
                
                await asyncio.sleep(0.5)  # Rate limiting
        
        logger.info(f"Wikipedia scraping terminé: {len(all_items)} items")
        return all_items
    
    async def _scrape_article(self, title: str) -> Dict:
        """Scrape a single Wikipedia article"""
        params = {
            'action': 'query',
            'format': 'json',
            'titles': title,
            'prop': 'extracts|categories',
            'exintro': '1',  # String not bool
            'explaintext': '1',  # String not bool
            'clcategories': 'Category:Mathematics'
        }
        
        headers = {
            'User-Agent': 'MathScraperBot/1.0 (Educational Research)'
        }
        
        try:
            async with self.session.get(self.API_URL, params=params, headers=headers) as response:
                if response.status != 200:
                    return None
                
                data = await response.json()
                pages = data.get('query', {}).get('pages', {})
                
                if not pages:
                    return None
                
                # Get the page content
                page = list(pages.values())[0]
                
                if 'extract' not in page:
                    return None
                
                extract = page['extract']
                
                if len(extract) < 100:
                    return None
                
                # Get categories
                categories = page.get('categories', [])
                tags = [cat['title'].replace('Category:', '') for cat in categories[:5]]
                
                return {
                    'id': f"wikipedia_{title.replace(' ', '_')}",
                    'source': 'wikipedia',
                    'title': title,
                    'content': extract,
                    'tags': tags,
                    'url': f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                    'metadata': {
                        'language': 'en',
                        'type': 'encyclopedia'
                    }
                }
        
        except Exception as e:
            logger.debug(f"Error scraping {title}: {e}")
            return None


# Test
async def test_scraper():
    scraper = WikipediaMathScraper()
    items = await scraper.scrape(max_items=3)
    
    print(f"\n✓ Collected {len(items)} Wikipedia items")
    if items:
        print("\nExample:")
        item = items[0]
        print(f"  Title: {item['title']}")
        print(f"  Content: {item['content'][:150]}...")


if __name__ == "__main__":
    asyncio.run(test_scraper())
