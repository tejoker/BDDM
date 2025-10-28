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
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key  # Optionnel mais augmente les limites
        self.rate_limit_remaining = 10000
        self.backoff_until = None
        
    async def scrape(self, max_items: int = None) -> List[Dict]:
        """
        Scrape questions avec réponses acceptées
        
        Filtres appliqués:
        - Questions avec réponse acceptée
        - Tags: proof, proof-theory, induction, etc.
        - Score minimum pour qualité
        """
        all_items = []
        page = 1
        max_pages = (max_items // 100) + 1 if max_items else 100
        
        async with aiohttp.ClientSession() as session:
            while page <= max_pages:
                try:
                    questions = await self._fetch_questions(session, page)
                    
                    if not questions:
                        break
                    
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
            'tagged': 'proof-writing',  # Tag correct sur Math SE
        }
        
        if self.api_key:
            params['key'] = self.api_key
        
        url = f"{self.BASE_URL}/questions"
        
        async with session.get(url, params=params) as response:
            if response.status == 200:
                data = await response.json()
                self.rate_limit_remaining = data.get('quota_remaining', 0)
                items = data.get('items', [])
                
                # Filter in code: only questions with accepted answer and min score
                filtered = [
                    item for item in items 
                    if item.get('accepted_answer_id') and item.get('score', 0) >= 5
                ]
                return filtered
            else:
                logger.warning(f"Erreur HTTP {response.status}")
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
