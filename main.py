"""
Math Data Scraper - Main Orchestrator
Collecte de données mathématiques depuis multiples sources
"""

import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict
import json

from scrapers.stackexchange_scraper import StackExchangeScraper
from scrapers.proofwiki_scraper import ProofWikiScraper
from scrapers.arxiv_scraper import ArxivScraper
from scrapers.french_courses_scraper import FrenchCoursesScraper
from utils.storage import DataStorage
from utils.cleaner import DataCleaner

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('scraping.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


class MathDataScraper:
    """Orchestrateur principal pour le scraping de données mathématiques"""
    
    def __init__(self, output_dir: str = "./data"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.storage = DataStorage(self.output_dir)
        self.cleaner = DataCleaner()
        
        # Initialiser les scrapers
        self.scrapers = {
            'stackexchange': StackExchangeScraper(),
            'proofwiki': ProofWikiScraper(),
            'arxiv': ArxivScraper(),
            'french_courses': FrenchCoursesScraper()
        }
        
        self.stats = {
            'total_scraped': 0,
            'by_source': {},
            'errors': []
        }
    
    async def scrape_source(self, source_name: str, max_items: int = None) -> List[Dict]:
        """Scrape une source spécifique"""
        logger.info(f"Début scraping: {source_name}")
        
        try:
            scraper = self.scrapers[source_name]
            items = await scraper.scrape(max_items=max_items)
            
            # Nettoyage des données
            cleaned_items = []
            for item in items:
                try:
                    cleaned = self.cleaner.clean(item)
                    if cleaned:
                        cleaned_items.append(cleaned)
                except Exception as e:
                    logger.warning(f"Erreur nettoyage: {e}")
                    continue
            
            # Stockage
            self.storage.save_batch(cleaned_items, source_name)
            
            # Stats
            self.stats['by_source'][source_name] = len(cleaned_items)
            self.stats['total_scraped'] += len(cleaned_items)
            
            logger.info(f"✓ {source_name}: {len(cleaned_items)} items collectés")
            return cleaned_items
            
        except Exception as e:
            logger.error(f"Erreur scraping {source_name}: {e}")
            self.stats['errors'].append({
                'source': source_name,
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            })
            return []
    
    async def scrape_all(self, sources: List[str] = None, max_per_source: int = None):
        """Scrape toutes les sources en parallèle"""
        if sources is None:
            sources = list(self.scrapers.keys())
        
        logger.info(f"Démarrage scraping de {len(sources)} sources")
        
        # Lancer tous les scrapers en parallèle
        tasks = [
            self.scrape_source(source, max_items=max_per_source)
            for source in sources
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Sauvegarder les statistiques
        self.save_stats()
        
        logger.info(f"Scraping terminé: {self.stats['total_scraped']} items au total")
        return results
    
    def save_stats(self):
        """Sauvegarder les statistiques de scraping"""
        stats_file = self.output_dir / "scraping_stats.json"
        with open(stats_file, 'w', encoding='utf-8') as f:
            json.dump(self.stats, f, indent=2, ensure_ascii=False)
        logger.info(f"Stats sauvegardées: {stats_file}")
    
    def get_summary(self) -> Dict:
        """Obtenir un résumé du scraping"""
        return {
            'total_items': self.stats['total_scraped'],
            'by_source': self.stats['by_source'],
            'num_errors': len(self.stats['errors']),
            'output_directory': str(self.output_dir)
        }


async def main():
    """Point d'entrée principal"""
    scraper = MathDataScraper(output_dir="./math_dataset")
    
    # Configuration: quelles sources scraper
    sources_to_scrape = [
        'stackexchange',  # Priorité 1: le plus riche
        'proofwiki',      # Priorité 2: très structuré
        # 'arxiv',        # Priorité 3: plus long
        # 'french_courses' # Priorité 4: spécifique
    ]
    
    # Limiter pour tests (enlever max_per_source pour production)
    await scraper.scrape_all(
        sources=sources_to_scrape,
        max_per_source=1000  # Commencer petit pour tester
    )
    
    # Afficher résumé
    summary = scraper.get_summary()
    print("\n" + "="*50)
    print("RÉSUMÉ DU SCRAPING")
    print("="*50)
    print(f"Total items collectés: {summary['total_items']}")
    print("\nPar source:")
    for source, count in summary['by_source'].items():
        print(f"  - {source}: {count}")
    print(f"\nDonnées sauvegardées dans: {summary['output_directory']}")


if __name__ == "__main__":
    asyncio.run(main())
