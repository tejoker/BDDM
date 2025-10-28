"""
Math Scrapers Package
"""

from .stackexchange_scraper import StackExchangeScraper
from .proofwiki_scraper import ProofWikiScraper
from .arxiv_full_scraper import ArxivFullScraper
from .french_courses_scraper import FrenchCoursesScraper
from .mathbooks_scraper import MathBooksScraper
from .aops_scraper import AoPSScraper
from .tricki_scraper import TrickiScraper

__all__ = [
    'StackExchangeScraper',
    'ProofWikiScraper',
    'ArxivFullScraper',
    'FrenchCoursesScraper',
    'MathBooksScraper',
    'AoPSScraper',
    'TrickiScraper',
]
