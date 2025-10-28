"""
French Courses Scraper
Collecte exercices et corrections depuis sites français (Exo7, etc.)
"""

import aiohttp
import asyncio
import logging
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
import re

logger = logging.getLogger(__name__)


class FrenchCoursesScraper:
    """Scraper pour cours et exercices français"""
    
    SOURCES = {
        'exo7': {
            'base_url': 'http://exo7.emath.fr',
            'search_path': '/ficpdf',
        },
        'bibmath': {
            'base_url': 'https://www.bibmath.net',
            'exercises_path': '/exercices',
        }
    }
    
    def __init__(self):
        self.session = None
    
    async def scrape(self, max_items: int = None) -> List[Dict]:
        """Scrape sources françaises d'exercices
        
        NOTE: Exo7 and Bibmath URLs have changed and are no longer easily scrapable.
        This scraper is currently disabled but kept for future implementation.
        Consider using alternative sources like:
        - les-mathematiques.net forums
        - maths-france.fr
        - Or implement PDF extraction for Exo7 materials
        """
        all_items = []
        
        logger.warning("French courses scraper is currently disabled - source URLs have changed")
        logger.info("Consider implementing alternative French math sources")
        
        # Temporarily disabled until proper URLs are found
        # async with aiohttp.ClientSession() as session:
        #     self.session = session
        #     # Scraper implementation here
        
        logger.info(f"Cours français scraping terminé: {len(all_items)} items")
        return all_items
    
    async def _scrape_exo7(self, max_items: int = None) -> List[Dict]:
        """
        Scraper Exo7.emath.fr
        Note: Exo7 fournit principalement des PDFs
        On scrape les liens et descriptions
        """
        items = []
        base_url = self.SOURCES['exo7']['base_url']
        
        # Page principale des fiches
        url = f"{base_url}/ficpdf.html"
        
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    return items
                
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')
                
                # Trouver liens vers PDFs d'exercices
                for link in soup.find_all('a', href=True):
                    href = link['href']
                    
                    # Filtrer exercices/corrections
                    if 'exercices' in href.lower() or 'correction' in href.lower():
                        if href.endswith('.pdf'):
                            title = link.get_text(strip=True)
                            
                            # Pour l'instant, on garde juste les métadonnées
                            # L'extraction du PDF se fera plus tard
                            items.append({
                                'id': f"exo7_{href.split('/')[-1].replace('.pdf', '')}",
                                'source': 'exo7',
                                'title': title,
                                'pdf_url': f"{base_url}{href}",
                                'content_type': 'pdf',
                                'tags': self._extract_tags_from_title(title),
                                'url': url,
                                'metadata': {
                                    'language': 'fr',
                                    'requires_pdf_extraction': True
                                }
                            })
                            
                            if max_items and len(items) >= max_items:
                                break
        
        except Exception as e:
            logger.warning(f"Erreur Exo7: {e}")
        
        return items
    
    async def _scrape_bibmath(self, max_items: int = None) -> List[Dict]:
        """Scraper Bibmath.net - exercices en ligne"""
        items = []
        base_url = self.SOURCES['bibmath']['base_url']
        
        # Sections d'exercices
        sections = [
            '/exercices/lycee',
            '/exercices/premiere-s',
            '/exercices/terminale-s',
            '/exercices/mpsi',
            '/exercices/analyse',
            '/exercices/algebre',
        ]
        
        for section in sections[:3]:  # Limiter pour test
            try:
                url = f"{base_url}{section}.html"
                async with self.session.get(url) as response:
                    if response.status != 200:
                        continue
                    
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # Trouver exercices individuels
                    exercises = soup.find_all('div', class_=re.compile(r'exercice|problem'))
                    
                    for exercise in exercises:
                        item = self._parse_bibmath_exercise(exercise, url)
                        if item:
                            items.append(item)
                        
                        if max_items and len(items) >= max_items:
                            break
                
                await asyncio.sleep(1)
                
                if max_items and len(items) >= max_items:
                    break
                    
            except Exception as e:
                logger.warning(f"Erreur Bibmath section {section}: {e}")
                continue
        
        return items
    
    def _parse_bibmath_exercise(self, exercise_div, source_url: str) -> Optional[Dict]:
        """Parser un exercice Bibmath"""
        try:
            # Titre/énoncé
            title_elem = exercise_div.find(['h3', 'h4', 'strong'])
            title = title_elem.get_text(strip=True) if title_elem else ''
            
            # Énoncé complet
            question = exercise_div.get_text(strip=True)
            
            # Chercher solution/correction
            solution = ''
            solution_div = exercise_div.find_next_sibling('div', 
                                                          class_=re.compile(r'solution|correction'))
            if solution_div:
                solution = solution_div.get_text(strip=True)
            
            if not question or len(question) < 30:
                return None
            
            return {
                'id': f"bibmath_{hash(question[:100])}",
                'source': 'bibmath',
                'title': title,
                'question': question,
                'answer': solution,
                'tags': self._extract_tags_from_title(title + ' ' + question),
                'url': source_url,
                'metadata': {
                    'language': 'fr',
                    'has_solution': bool(solution)
                }
            }
            
        except Exception as e:
            logger.debug(f"Erreur parse exercice: {e}")
            return None
    
    def _extract_tags_from_title(self, title: str) -> List[str]:
        """Extraire tags depuis le titre"""
        tags = []
        title_lower = title.lower()
        
        keywords = {
            'algèbre': ['algèbre', 'algebra', 'equation', 'polynome'],
            'analyse': ['analyse', 'suite', 'série', 'limite', 'continuité'],
            'géométrie': ['géométrie', 'geometry', 'triangle', 'cercle'],
            'probabilités': ['probabilité', 'proba', 'statistics'],
            'arithmétique': ['arithmétique', 'divisibilité', 'pgcd', 'nombres premiers'],
            'intégration': ['intégrale', 'integration', 'primitive'],
            'dérivation': ['dérivée', 'dérivation', 'derivative'],
        }
        
        for tag, words in keywords.items():
            if any(word in title_lower for word in words):
                tags.append(tag)
        
        return tags


# Test standalone
async def test_scraper():
    scraper = FrenchCoursesScraper()
    items = await scraper.scrape(max_items=10)
    
    print(f"\n✓ Collecté {len(items)} items de cours français")
    if items:
        print("\nExemples:")
        for item in items[:3]:
            print(f"\n- {item['source']}: {item['title'][:60]}")
            if 'question' in item:
                print(f"  Question: {item['question'][:80]}...")


if __name__ == "__main__":
    asyncio.run(test_scraper())
