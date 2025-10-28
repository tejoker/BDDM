"""
Art of Problem Solving (AoPS) Scraper
Competition mathematics problems and solutions
"""

import aiohttp
import asyncio
import logging
from typing import List, Dict
from bs4 import BeautifulSoup
import re

logger = logging.getLogger(__name__)


class AoPSScraper:
    """Scraper for Art of Problem Solving (competition math)"""
    
    BASE_URL = "https://artofproblemsolving.com"
    WIKI_URL = f"{BASE_URL}/wiki"
    
    # Popular competition problem lists
    PROBLEM_SOURCES = [
        "AMC_10_Problems_and_Solutions",
        "AMC_12_Problems_and_Solutions",
        "AIME_Problems_and_Solutions",
        "IMO_Problems_and_Solutions",
        "USAMO_Problems_and_Solutions"
    ]
    
    def __init__(self):
        self.session = None
    
    async def scrape(self, max_items: int = None) -> List[Dict]:
        """Scrape AoPS competition problems with solutions"""
        all_items = []
        max_items = max_items or 20
        
        async with aiohttp.ClientSession() as session:
            self.session = session
            
            # Scrape from wiki pages (publicly available)
            for source in self.PROBLEM_SOURCES[:3]:  # Limit sources
                if len(all_items) >= max_items:
                    break
                
                items = await self._scrape_problem_source(source)
                all_items.extend(items)
                
                logger.info(f"AoPS - {source}: {len(items)} items")
                await asyncio.sleep(2)  # Be respectful
        
        logger.info(f"AoPS scraping terminé: {len(all_items[:max_items])} items")
        return all_items[:max_items]
    
    async def _scrape_problem_source(self, source: str) -> List[Dict]:
        """Scrape problems from a source (e.g., AMC 10)"""
        url = f"{self.WIKI_URL}/index.php/{source}"
        
        try:
            async with self.session.get(url, timeout=10) as response:
                if response.status != 200:
                    return []
                
                html_content = await response.text()
                soup = BeautifulSoup(html_content, 'html.parser')
                
                # Find links to specific years/problems
                links = soup.find_all('a', href=re.compile(r'/(AMC|AIME|IMO|USAMO).*Problem'))
                
                items = []
                for link in links[:5]:  # Limit per source
                    href = link.get('href')
                    if href:
                        # Convert relative to absolute
                        if href.startswith('/'):
                            full_url = f"{self.BASE_URL}{href}"
                        else:
                            full_url = href
                        
                        item = await self._scrape_problem(full_url)
                        if item:
                            items.append(item)
                        await asyncio.sleep(1)
                
                return items
        
        except Exception as e:
            logger.warning(f"Error scraping {source}: {e}")
            return []
    
    async def _scrape_problem(self, url: str) -> Dict:
        """Scrape a single problem with solution"""
        try:
            async with self.session.get(url, timeout=10) as response:
                if response.status != 200:
                    return None
                
                html_content = await response.text()
                soup = BeautifulSoup(html_content, 'html.parser')
                
                # Get title
                title_elem = soup.find('h1', id='firstHeading')
                title = title_elem.get_text(strip=True) if title_elem else ''
                
                # Get content
                content_div = soup.find('div', id='mw-content-text')
                if not content_div:
                    return None
                
                # Look for Problem and Solution sections
                problem_text = ''
                solution_text = ''
                
                # Find headers
                headers = content_div.find_all(['h2', 'h3'])
                
                for header in headers:
                    header_text = header.get_text(strip=True).lower()
                    
                    if 'problem' in header_text:
                        # Get content after this header
                        next_elem = header.find_next_sibling()
                        if next_elem:
                            problem_text = next_elem.get_text(strip=True)
                    
                    elif 'solution' in header_text:
                        # Get content after this header
                        next_elem = header.find_next_sibling()
                        if next_elem:
                            solution_text = next_elem.get_text(strip=True)
                
                # If no clear sections, try to get all paragraphs
                if not problem_text:
                    paragraphs = content_div.find_all('p')
                    if paragraphs:
                        problem_text = paragraphs[0].get_text(strip=True) if len(paragraphs) > 0 else ''
                        solution_text = paragraphs[1].get_text(strip=True) if len(paragraphs) > 1 else ''
                
                if not problem_text or len(problem_text) < 20:
                    return None
                
                # Extract competition type and year from title
                comp_match = re.search(r'(AMC|AIME|IMO|USAMO)\s+(\d+)', title)
                tags = []
                if comp_match:
                    tags = [comp_match.group(1), comp_match.group(2)]
                
                return {
                    'id': f"aops_{url.split('/')[-1]}",
                    'source': 'aops',
                    'title': title,
                    'question': problem_text,
                    'answer': solution_text,
                    'tags': tags,
                    'url': url,
                    'metadata': {
                        'language': 'en',
                        'type': 'competition',
                        'level': 'olympiad'
                    }
                }
        
        except Exception as e:
            logger.debug(f"Error scraping problem {url}: {e}")
            return None


# Test
async def test_scraper():
    scraper = AoPSScraper()
    items = await scraper.scrape(max_items=2)
    
    print(f"\n✓ Collected {len(items)} AoPS items")
    if items:
        print("\nExample:")
        item = items[0]
        print(f"  Title: {item['title']}")
        print(f"  Problem: {item['question'][:100]}...")


if __name__ == "__main__":
    asyncio.run(test_scraper())
