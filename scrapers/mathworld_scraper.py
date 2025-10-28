"""
MathWorld Scraper
Wolfram's mathematical encyclopedia
"""

import aiohttp
import asyncio
import logging
from typing import List, Dict
from bs4 import BeautifulSoup
import re

logger = logging.getLogger(__name__)


class MathWorldScraper:
    """Scraper for MathWorld (Wolfram's math encyclopedia)"""
    
    BASE_URL = "https://mathworld.wolfram.com"
    
    # Popular math topics
    TOPICS = [
        "Algebra", "Calculus", "Geometry", "NumberTheory",
        "Topology", "Logic", "SetTheory", "Analysis",
        "LinearAlgebra", "GroupTheory", "Probability"
    ]
    
    def __init__(self):
        self.session = None
    
    async def scrape(self, max_items: int = None) -> List[Dict]:
        """Scrape MathWorld encyclopedia entries"""
        all_items = []
        max_items = max_items or 20
        
        async with aiohttp.ClientSession() as session:
            self.session = session
            
            # Get entries from each topic
            for topic in self.TOPICS[:5]:  # Limit topics
                if len(all_items) >= max_items:
                    break
                
                items = await self._scrape_topic(topic)
                all_items.extend(items)
                
                logger.info(f"MathWorld - {topic}: {len(items)} items")
                await asyncio.sleep(1)  # Be respectful
        
        logger.info(f"MathWorld scraping terminé: {len(all_items[:max_items])} items")
        return all_items[:max_items]
    
    async def _scrape_topic(self, topic: str) -> List[Dict]:
        """Scrape entries from a topic category"""
        url = f"{self.BASE_URL}/{topic}.html"
        
        try:
            async with self.session.get(url, timeout=10) as response:
                if response.status != 200:
                    return []
                
                html_content = await response.text()
                soup = BeautifulSoup(html_content, 'html.parser')
                
                # Find links to definitions/entries
                links = soup.find_all('a', href=re.compile(r'^[A-Z].*\.html$'))
                
                items = []
                for link in links[:5]:  # Limit per topic
                    href = link.get('href')
                    if href:
                        full_url = f"{self.BASE_URL}/{href}"
                        item = await self._scrape_entry(full_url)
                        if item:
                            items.append(item)
                        await asyncio.sleep(0.5)
                
                return items
        
        except Exception as e:
            logger.warning(f"Error scraping topic {topic}: {e}")
            return []
    
    async def _scrape_entry(self, url: str) -> Dict:
        """Scrape a single MathWorld entry"""
        try:
            async with self.session.get(url, timeout=10) as response:
                if response.status != 200:
                    return None
                
                html_content = await response.text()
                soup = BeautifulSoup(html_content, 'html.parser')
                
                # Get title
                title_elem = soup.find('h1')
                title = title_elem.get_text(strip=True) if title_elem else ''
                
                # Get main content
                content_div = soup.find('div', class_='entry-content')
                if not content_div:
                    content_div = soup.find('div', id='content')
                
                if not content_div:
                    return None
                
                # Extract definition and theorems
                paragraphs = content_div.find_all('p')
                content_parts = []
                
                for p in paragraphs[:5]:  # First few paragraphs
                    text = p.get_text(strip=True)
                    if text and len(text) > 20:
                        content_parts.append(text)
                
                if not content_parts:
                    return None
                
                # Extract any formulas (MathWorld uses images for formulas)
                # We'll just get the alt text which often contains LaTeX
                formulas = []
                for img in content_div.find_all('img', alt=True):
                    alt = img.get('alt', '')
                    if alt and len(alt) > 3:
                        formulas.append(f"${alt}$")
                
                content = "\n\n".join(content_parts)
                if formulas:
                    content += "\n\nKey formulas:\n" + "\n".join(formulas[:5])
                
                return {
                    'id': f"mathworld_{url.split('/')[-1].replace('.html', '')}",
                    'source': 'mathworld',
                    'title': title,
                    'content': content,
                    'url': url,
                    'metadata': {
                        'language': 'en',
                        'type': 'encyclopedia',
                        'publisher': 'Wolfram'
                    }
                }
        
        except Exception as e:
            logger.debug(f"Error scraping entry {url}: {e}")
            return None


# Test
async def test_scraper():
    scraper = MathWorldScraper()
    items = await scraper.scrape(max_items=3)
    
    print(f"\n✓ Collected {len(items)} MathWorld items")
    if items:
        print("\nExample:")
        item = items[0]
        print(f"  Title: {item['title']}")
        print(f"  Content: {item['content'][:150]}...")


if __name__ == "__main__":
    asyncio.run(test_scraper())
