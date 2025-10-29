"""
ProofWiki Scraper
Collecte les théorèmes et preuves depuis proofwiki.org
"""

import aiohttp
import asyncio
import logging
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
import re
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.user_agents import get_rotating_headers

logger = logging.getLogger(__name__)


class ProofWikiScraper:
    """Scraper pour ProofWiki.org"""
    
    BASE_URL = "https://proofwiki.org"
    
    # Catégories principales à scraper
    CATEGORIES = [
        "Category:Proofs",
        "Category:Theorems",
        "Category:Number_Theory",
        "Category:Algebra",
        "Category:Real_Analysis",
        "Category:Complex_Analysis",
        "Category:Set_Theory",
        "Category:Logic",
    ]
    
    def __init__(self, skip_ids: set = None):
        self.visited_urls = set()
        self.session = None
        self.skip_ids = skip_ids or set()  # IDs to skip (already collected)
    
    async def scrape(self, max_items: int = None) -> List[Dict]:
        """Scrape ProofWiki pour théorèmes et preuves"""
        all_items = []

        # Use rotating User-Agent headers to avoid blocking
        headers = get_rotating_headers(include_academic=True)

        async with aiohttp.ClientSession(headers=headers) as session:
            self.session = session
            
            # Récupérer les URLs des pages de théorèmes
            theorem_urls = await self._get_theorem_urls(max_items)
            
            logger.info(f"ProofWiki: {len(theorem_urls)} théorèmes à scraper")
            
            # Scraper chaque théorème
            for i, url in enumerate(theorem_urls):
                if max_items and len(all_items) >= max_items:
                    break

                # Check if already collected (by ID)
                expected_id = f"pw_{url.split('/')[-1]}"
                if expected_id in self.skip_ids:
                    continue

                try:
                    item = await self._scrape_theorem_page(url)
                    if item:
                        all_items.append(item)

                    if (i + 1) % 50 == 0:
                        logger.info(f"ProofWiki: {len(all_items)} items collectés")

                    # Rate limiting respectueux
                    await asyncio.sleep(0.5)

                except Exception as e:
                    logger.warning(f"Erreur scraping {url}: {e}")
                    continue
        
        logger.info(f"ProofWiki scraping terminé: {len(all_items)} items")
        return all_items
    
    async def _get_theorem_urls(self, max_items: int = None) -> List[str]:
        """Récupérer les URLs de tous les théorèmes"""
        urls = []
        
        # Méthode 1: Via Special:AllPages
        url = f"{self.BASE_URL}/w/index.php"
        params = {
            'title': 'Special:AllPages',
            'namespace': '0'
        }
        
        try:
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')

                    # Extraire les liens vers les théorèmes
                    for link in soup.find_all('a', href=True):
                        href = link['href']
                        if '/wiki/' in href and ':' not in href:
                            full_url = f"{self.BASE_URL}{href}"
                            if full_url not in self.visited_urls:
                                urls.append(full_url)
                                self.visited_urls.add(full_url)

                                if max_items and len(urls) >= max_items * 2:
                                    break

                    logger.info(f"Found {len(urls)} theorem URLs from Special:AllPages")
                else:
                    logger.warning(f"Failed to fetch Special:AllPages: status {response.status}")
                    if response.status == 403:
                        logger.error("ACCESS FORBIDDEN (403): ProofWiki may have blocked our IP")
        except aiohttp.ClientError as e:
            logger.error(f"Network error fetching URLs: {e}")
        except Exception as e:
            logger.error(f"Unexpected error fetching URLs: {e}", exc_info=True)
        
        # Méthode 2: Via catégories spécifiques si pas assez d'URLs
        if len(urls) < 100:
            for category in self.CATEGORIES[:3]:  # Limiter pour test
                cat_urls = await self._get_category_urls(category)
                urls.extend(cat_urls)
                
                if max_items and len(urls) >= max_items * 2:
                    break
        
        return urls[:max_items * 2 if max_items else len(urls)]
    
    async def _get_category_urls(self, category: str) -> List[str]:
        """Récupérer les URLs d'une catégorie"""
        urls = []
        url = f"{self.BASE_URL}/wiki/{category}"
        
        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # Pages dans la catégorie
                    content = soup.find('div', {'id': 'mw-pages'})
                    if content:
                        for link in content.find_all('a', href=True):
                            href = link['href']
                            if '/wiki/' in href and category not in href:
                                full_url = f"{self.BASE_URL}{href}"
                                if full_url not in self.visited_urls:
                                    urls.append(full_url)
                                    self.visited_urls.add(full_url)
        except Exception as e:
            logger.warning(f"Erreur catégorie {category}: {e}")
        
        return urls
    
    async def _scrape_theorem_page(self, url: str) -> Optional[Dict]:
        """Scraper une page de théorème spécifique"""
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    logger.warning(f"ProofWiki returned status {response.status} for '{url}'")
                    logger.warning(f"Response headers: {dict(response.headers)}")
                    if response.status == 429:
                        logger.error("RATE LIMIT HIT: ProofWiki is throttling requests")
                    elif response.status == 403:
                        logger.error("ACCESS FORBIDDEN (403): ProofWiki may have blocked our IP")
                    elif response.status == 503:
                        logger.error("SERVICE UNAVAILABLE (503): ProofWiki may be down or blocking")
                    return None

                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')

                # Extraire le titre
                title = soup.find('h1', {'id': 'firstHeading'})
                title_text = title.get_text() if title else ''

                if not title_text:
                    logger.debug(f"No title found for '{url}'")
                    return None

                # Extraire le contenu principal
                content = soup.find('div', {'id': 'mw-content-text'})
                if not content:
                    logger.warning(f"No content div found for '{url}'")
                    return None

                # Extraire théorème et preuve
                theorem_text = self._extract_theorem(content)
                proof_text = self._extract_proof(content)

                if not theorem_text and not proof_text:
                    logger.debug(f"No theorem or proof found for '{url}'")
                    return None

                # Extraire les catégories/tags
                tags = self._extract_categories(soup)

                return {
                    'id': f"pw_{url.split('/')[-1]}",
                    'source': 'proofwiki',
                    'title': title_text,
                    'theorem': theorem_text,
                    'proof': proof_text,
                    'tags': tags,
                    'url': url,
                    'metadata': {
                        'has_proof': bool(proof_text),
                        'has_theorem': bool(theorem_text)
                    }
                }

        except aiohttp.ClientError as e:
            logger.error(f"Network error scraping '{url}': {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error scraping '{url}': {e}", exc_info=True)
            return None
    
    def _extract_theorem(self, content) -> str:
        """Extraire l'énoncé du théorème"""
        # Chercher les sections "Theorem", "Statement", etc.
        theorem_sections = content.find_all(['div', 'p'], 
                                           class_=re.compile(r'theorem|statement'))
        
        if theorem_sections:
            return ' '.join(s.get_text(strip=True) for s in theorem_sections)
        
        # Fallback: premier paragraphe avant "Proof"
        paragraphs = content.find_all('p')
        theorem_parts = []
        
        for p in paragraphs:
            text = p.get_text(strip=True)
            if 'Proof' in text or 'proof' in text:
                break
            if text and len(text) > 20:
                theorem_parts.append(text)
        
        return ' '.join(theorem_parts[:3])  # Limiter aux 3 premiers paragraphes
    
    def _extract_proof(self, content) -> str:
        """Extraire la preuve"""
        # Chercher section "Proof"
        proof_header = content.find(['h2', 'h3', 'h4'], 
                                   string=re.compile(r'Proof', re.IGNORECASE))
        
        if not proof_header:
            return ''
        
        # Récupérer tous les éléments suivants jusqu'à la prochaine section
        proof_parts = []
        current = proof_header.find_next_sibling()
        
        while current and current.name not in ['h2', 'h3', 'h4']:
            if current.name in ['p', 'div', 'li']:
                text = current.get_text(strip=True)
                if text and len(text) > 10:
                    proof_parts.append(text)
            current = current.find_next_sibling()
        
        return ' '.join(proof_parts)
    
    def _extract_categories(self, soup) -> List[str]:
        """Extraire les catégories/tags"""
        tags = []
        
        # Catégories en bas de page
        cat_section = soup.find('div', {'id': 'mw-normal-catlinks'})
        if cat_section:
            for link in cat_section.find_all('a'):
                cat = link.get_text()
                if cat != 'Category':
                    tags.append(cat.lower())
        
        return tags[:10]  # Limiter nombre de tags


# Test standalone
async def test_scraper():
    scraper = ProofWikiScraper()
    items = await scraper.scrape(max_items=5)
    
    print(f"\n✓ Collecté {len(items)} items de ProofWiki")
    if items:
        print("\nExemple:")
        item = items[0]
        print(f"Titre: {item['title']}")
        print(f"Théorème (100 chars): {item['theorem'][:100]}...")
        print(f"Preuve (100 chars): {item['proof'][:100]}...")


if __name__ == "__main__":
    asyncio.run(test_scraper())
