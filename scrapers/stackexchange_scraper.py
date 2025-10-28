"""
Stack Exchange Mathematics Scraper
Utilise l'API officielle pour collecter questions et réponses
"""

import aiohttp
import asyncio
import logging
from typing import List, Dict, Optional
from datetime import datetime
import time

logger = logging.getLogger(__name__)


class StackExchangeScraper:
    """Scraper pour Stack Exchange Mathematics"""
    
    BASE_URL = "https://api.stackexchange.com/2.3"
    SITE = "math.stackexchange.com"
    
    def __init__(self, api_key: Optional[str] = None, competition_math: bool = False):
        self.api_key = api_key  # Optionnel mais augmente les limites
        self.rate_limit_remaining = 10000
        self.backoff_until = None
        self.current_page = 1  # Track pagination state
        self.competition_math = competition_math  # Filter for competition math if True
        
    async def scrape(self, max_items: int = None, start_page: int = None) -> List[Dict]:
        """
        Scrape questions avec réponses acceptées
        
        Filtres appliqués:
        - Questions avec réponse acceptée
        - Tags: proof, proof-theory, induction, etc.
        - Score minimum pour qualité
        
        Args:
            max_items: Maximum number of items to collect in this call
            start_page: Page to start from (if None, uses self.current_page)
        """
        all_items = []
        page = start_page if start_page is not None else self.current_page
        # Calculate max pages from current position, not from 0
        max_pages = page + ((max_items // 50) + 2) if max_items else page + 100
        empty_pages = 0  # Track consecutive empty pages
        
        async with aiohttp.ClientSession() as session:
            while page <= max_pages:
                try:
                    questions = await self._fetch_questions(session, page)
                    
                    if not questions:
                        empty_pages += 1
                        # Stop if 3 consecutive empty pages
                        if empty_pages >= 3:
                            break
                        page += 1
                        continue
                    
                    empty_pages = 0  # Reset counter on successful page
                    
                    # Pour chaque question, récupérer la réponse acceptée
                    for question in questions:
                        if max_items and len(all_items) >= max_items:
                            break
                        
                        item = await self._process_question(session, question)
                        if item:
                            all_items.append(item)
                    
                    logger.info(f"Stack Exchange - Page {page}: {len(all_items)} items total")
                    page += 1
                    
                    # Respecter rate limiting
                    await self._handle_rate_limit()
                    
                    if max_items and len(all_items) >= max_items:
                        break
                        
                except Exception as e:
                    logger.error(f"Erreur page {page}: {e}")
                    await asyncio.sleep(5)
                    continue
        
        # Update current page for next call
        self.current_page = page
        
        logger.info(f"Stack Exchange scraping terminé: {len(all_items)} items")
        return all_items
    
    async def _fetch_questions(self, session: aiohttp.ClientSession, page: int) -> List[Dict]:
        """Récupérer une page de questions"""
        params = {
            'page': page,
            'pagesize': 100,
            'order': 'desc',
            'sort': 'votes',
            'site': self.SITE,
            'filter': 'withbody',  # Inclure le corps
        }
        
        # Add competition-math tags if enabled
        if self.competition_math:
            params['tagged'] = 'contest-math;competition-math;olympiad'
        
        if self.api_key:
            params['key'] = self.api_key
        
        url = f"{self.BASE_URL}/questions"
        
        async with session.get(url, params=params) as response:
            if response.status == 200:
                data = await response.json()
                self.rate_limit_remaining = data.get('quota_remaining', 0)
                items = data.get('items', [])
                
                # Filter in code: only questions with accepted answer and min score
                # Relaxed filter - just need accepted answer and decent score
                filtered = [
                    item for item in items 
                    if item.get('accepted_answer_id') and item.get('score', 0) >= 3
                ]
                return filtered
            else:
                error_text = await response.text()
                logger.warning(f"Erreur HTTP {response.status}: {error_text}")
                print(f"Erreur HTTP {response.status}")
                print(f"URL: {url}")
                print(f"Params: {params}")
                print(f"Response: {error_text[:200]}")
                return []
    
    async def _process_question(self, session: aiohttp.ClientSession, question: Dict) -> Optional[Dict]:
        """Traiter une question et récupérer sa réponse acceptée"""
        try:
            # Récupérer la réponse acceptée
            answer_id = question.get('accepted_answer_id')
            if not answer_id:
                return None
            
            answer = await self._fetch_answer(session, answer_id)
            if not answer:
                return None
            
            # Structure des données
            return {
                'id': f"se_{question['question_id']}",
                'source': 'stackexchange',
                'title': question.get('title', ''),
                'question': self._clean_html(question.get('body', '')),
                'answer': self._clean_html(answer.get('body', '')),
                'tags': question.get('tags', []),
                'score': question.get('score', 0),
                'answer_score': answer.get('score', 0),
                'url': question.get('link', ''),
                'created_date': datetime.fromtimestamp(
                    question.get('creation_date', 0)
                ).isoformat(),
                'metadata': {
                    'view_count': question.get('view_count', 0),
                    'is_answered': question.get('is_answered', False)
                }
            }
            
        except Exception as e:
            logger.warning(f"Erreur traitement question {question.get('question_id')}: {e}")
            return None
    
    async def _fetch_answer(self, session: aiohttp.ClientSession, answer_id: int) -> Optional[Dict]:
        """Récupérer une réponse spécifique"""
        params = {
            'site': self.SITE,
            'filter': 'withbody'
        }
        
        if self.api_key:
            params['key'] = self.api_key
        
        url = f"{self.BASE_URL}/answers/{answer_id}"
        
        try:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    items = data.get('items', [])
                    return items[0] if items else None
        except Exception as e:
            logger.warning(f"Erreur fetch answer {answer_id}: {e}")
            return None
    
    def _clean_html(self, html_content: str) -> str:
        """Nettoyer le HTML et extraire le texte/LaTeX"""
        from bs4 import BeautifulSoup
        
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Préserver les formules LaTeX
        for code in soup.find_all('code'):
            code.replace_with(f"${code.get_text()}$")
        
        # Extraire le texte
        text = soup.get_text()
        
        # Nettoyer les espaces
        text = ' '.join(text.split())
        
        return text
    
    async def _handle_rate_limit(self):
        """Gérer les limites de taux de l'API"""
        if self.rate_limit_remaining < 100:
            logger.warning(f"Rate limit faible: {self.rate_limit_remaining} restants")
            await asyncio.sleep(2)
        else:
            await asyncio.sleep(0.1)  # Petit délai entre requêtes


# Test standalone
async def test_scraper():
    scraper = StackExchangeScraper()
    items = await scraper.scrape(max_items=10)
    
    print(f"\n✓ Collecté {len(items)} items")
    if items:
        print("\nExemple:")
        item = items[0]
        print(f"Titre: {item['title']}")
        print(f"Tags: {item['tags']}")
        print(f"Question (100 premiers chars): {item['question'][:100]}...")


if __name__ == "__main__":
    asyncio.run(test_scraper())
