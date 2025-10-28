"""
Math Scrapers Package
"""

from .stackexchange_scraper import StackExchangeScraper
from .proofwiki_scraper import ProofWikiScraper
from .arxiv_full_scraper import ArxivFullScraper
from .french_courses_scraper import FrenchCoursesScraper
from .project_euler_scraper import ProjectEulerScraper

__all__ = [
    'StackExchangeScraper',
    'ProofWikiScraper',
    'ArxivFullScraper',
    'FrenchCoursesScraper',
    'ProjectEulerScraper',
]
