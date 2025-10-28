"""
nLab Scraper
Category theory and higher mathematics wiki
"""

import aiohttp
import asyncio
import logging
from typing import List, Dict
from bs4 import BeautifulSoup
import re

logger = logging.getLogger(__name__)


class NLabScraper:
    """Scraper for nLab (category theory wiki)"""
    
    BASE_URL = "https://ncatlab.org"
    NLAB_URL = f"{BASE_URL}/nlab/show"
    
    # Important category theory topics
    TOPICS = [
        "category", "functor", "natural+transformation",
        "adjoint+functor", "limit", "colimit", 
        "monad", "topos", "sheaf", "cohomology",
        "homotopy+theory", "higher+category+theory"
    ]
    
    def __init__(self):
        self.session = None
    
    async def scrape(self, max_items: int = None) -> List[Dict]:
        """Scrape nLab articles"""
        all_items = []
        max_items = max_items or 20
        
        async with aiohttp.ClientSession() as session:
            self.session = session
            
            for topic in self.TOPICS[:10]:  # Limit topics
                if len(all_items) >= max_items:
                    break
                
                item = await self._scrape_article(topic)
                if item:
                    all_items.append(item)
                    logger.info(f"nLab - {topic}: collected")
                
                await asyncio.sleep(2)  # Be respectful
        
        logger.info(f"nLab scraping terminé: {len(all_items[:max_items])} items")
        return all_items[:max_items]
    
    async def _scrape_article(self, topic: str) -> Dict:
        """Scrape a single nLab article"""
        url = f"{self.NLAB_URL}/{topic}"
        
        try:
            async with self.session.get(url, timeout=15) as response:
                if response.status != 200:
                    return None
                
                html_content = await response.text()
                soup = BeautifulSoup(html_content, 'html.parser')
                
                # Get title
                title_elem = soup.find('h1')
                title = title_elem.get_text(strip=True) if title_elem else topic.replace('+', ' ')
                
                # Get main content
                content_div = soup.find('div', id='revision')
                if not content_div:
                    content_div = soup.find('div', class_='content')
                
                if not content_div:
                    return None
                
                # Extract definition and main concepts
                sections = {}
                current_section = 'intro'
                current_content = []
                
                for elem in content_div.find_all(['h2', 'h3', 'p']):
                    if elem.name in ['h2', 'h3']:
                        # Save previous section
                        if current_content:
                            sections[current_section] = '\n\n'.join(current_content)
                        
                        # Start new section
                        current_section = elem.get_text(strip=True).lower()
                        current_content = []
                    
                    elif elem.name == 'p':
                        text = elem.get_text(strip=True)
                        if text and len(text) > 20:
                            current_content.append(text)
                
                # Save last section
                if current_content:
                    sections[current_section] = '\n\n'.join(current_content)
                
                # Build content: definition + main sections
                content_parts = []
                
                if 'definition' in sections:
                    content_parts.append("Definition:\n" + sections['definition'])
                elif 'intro' in sections:
                    content_parts.append(sections['intro'])
                
                # Add other important sections
                for key in ['properties', 'examples', 'theorem']:
                    if key in sections:
                        content_parts.append(f"{key.title()}:\n{sections[key]}")
                
                if not content_parts:
                    return None
                
                content = "\n\n".join(content_parts)
                
                # Limit content length
                if len(content) > 2000:
                    content = content[:2000] + "..."
                
                return {
                    'id': f"nlab_{topic.replace('+', '_')}",
                    'source': 'nlab',
                    'title': title,
                    'content': content,
                    'url': url,
                    'tags': ['category-theory', 'higher-mathematics'],
                    'metadata': {
                        'language': 'en',
                        'type': 'encyclopedia',
                        'level': 'research'
                    }
                }
        
        except Exception as e:
            logger.debug(f"Error scraping {topic}: {e}")
            return None


# Test
async def test_scraper():
    scraper = NLabScraper()
    items = await scraper.scrape(max_items=2)
    
    print(f"\n✓ Collected {len(items)} nLab items")
    if items:
        print("\nExample:")
        item = items[0]
        print(f"  Title: {item['title']}")
        print(f"  Content: {item['content'][:150]}...")


if __name__ == "__main__":
    asyncio.run(test_scraper())
