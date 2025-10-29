"""
Wikipedia Math Scraper
Mathematics articles from Wikipedia
"""

import aiohttp
import asyncio
import logging
from typing import List, Dict
import re
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.user_agents import get_rotating_headers

logger = logging.getLogger(__name__)


class WikipediaMathScraper:
    """Scraper for Wikipedia mathematics articles"""
    
    API_URL = "https://en.wikipedia.org/w/api.php"
    
    # Comprehensive list of math topics (1000+ potential articles)
    TOPICS = [
        # Calculus & Analysis
        "Calculus", "Real analysis", "Complex analysis", "Functional analysis",
        "Measure theory", "Differential equations", "Partial differential equations",
        "Ordinary differential equations", "Integral", "Derivative", "Limit",
        "Continuity", "Sequence", "Series", "Convergence", "Taylor series",
        "Fourier analysis", "Fourier transform", "Laplace transform",
        "Multivariable calculus", "Vector calculus", "Differential calculus",
        "Integral calculus", "Fundamental theorem of calculus",
        
        # Algebra
        "Linear algebra", "Abstract algebra", "Group theory", "Ring theory",
        "Field theory", "Galois theory", "Module", "Vector space",
        "Matrix", "Determinant", "Eigenvalue", "Eigenvector",
        "Linear transformation", "Basis", "Dimension", "Inner product space",
        "Tensor", "Bilinear form", "Quadratic form", "Boolean algebra",
        "Universal algebra", "Lattice", "Lie algebra", "Associative algebra",
        "Commutative algebra", "Homological algebra",
        
        # Number Theory
        "Number theory", "Prime number", "Composite number", "Greatest common divisor",
        "Least common multiple", "Modular arithmetic", "Congruence",
        "Fermat's little theorem", "Euler's theorem", "Chinese remainder theorem",
        "Quadratic reciprocity", "Diophantine equation", "Pell's equation",
        "Continued fraction", "Partition", "Divisor function",
        "Riemann zeta function", "Prime number theorem",
        
        # Geometry & Topology
        "Topology", "Algebraic topology", "Differential geometry",
        "Riemannian geometry", "Euclidean geometry", "Non-Euclidean geometry",
        "Hyperbolic geometry", "Projective geometry", "Affine geometry",
        "Metric space", "Topological space", "Manifold", "Homotopy",
        "Homology", "Cohomology", "Fundamental group", "Covering space",
        "Knot theory", "Graph theory", "Combinatorial topology",
        
        # Discrete Math & Combinatorics
        "Combinatorics", "Graph theory", "Discrete mathematics",
        "Permutation", "Combination", "Binomial coefficient",
        "Generating function", "Recurrence relation", "Fibonacci number",
        "Catalan number", "Partition", "Pigeonhole principle",
        "Ramsey theory", "Extremal combinatorics", "Probabilistic method",
        
        # Logic & Foundations
        "Mathematical logic", "Set theory", "Model theory", "Proof theory",
        "Computability theory", "Gödel's incompleteness theorems",
        "Axiom of choice", "Zermelo–Fraenkel set theory", "Ordinal number",
        "Cardinal number", "First-order logic", "Propositional calculus",
        "Predicate logic", "Boolean logic",
        
        # Probability & Statistics
        "Probability theory", "Statistics", "Random variable",
        "Probability distribution", "Normal distribution", "Binomial distribution",
        "Poisson distribution", "Exponential distribution",
        "Central limit theorem", "Law of large numbers",
        "Markov chain", "Stochastic process", "Brownian motion",
        "Statistical inference", "Hypothesis testing", "Regression analysis",
        
        # Applied Math
        "Numerical analysis", "Optimization", "Game theory",
        "Information theory", "Coding theory", "Cryptography",
        "Dynamical system", "Chaos theory", "Fractal",
        "Operations research", "Control theory", "Mathematical physics",
        
        # Famous Theorems & Problems
        "Pythagorean theorem", "Fundamental theorem of arithmetic",
        "Fundamental theorem of algebra", "Fermat's Last Theorem",
        "Four color theorem", "Poincaré conjecture", "Riemann hypothesis",
        "Goldbach's conjecture", "Twin prime conjecture",
        "Collatz conjecture", "P versus NP problem",
        "Banach–Tarski paradox", "Gödel's incompleteness theorems",
        
        # Mathematical Structures
        "Algebraic structure", "Semigroup", "Monoid", "Group",
        "Abelian group", "Cyclic group", "Symmetric group",
        "Ring", "Integral domain", "Field", "Polynomial ring",
        "Quotient ring", "Ideal", "Module", "Algebra over a field",
        
        # Special Functions
        "Gamma function", "Beta function", "Bessel function",
        "Legendre polynomials", "Hermite polynomials",
        "Laguerre polynomials", "Chebyshev polynomials",
        "Hypergeometric function", "Elliptic function",
        
        # Matrix Theory
        "Matrix theory", "Diagonal matrix", "Identity matrix",
        "Orthogonal matrix", "Unitary matrix", "Hermitian matrix",
        "Positive-definite matrix", "Matrix decomposition",
        "LU decomposition", "QR decomposition", "Singular value decomposition",
        "Cholesky decomposition", "Jordan normal form",
        
        # Proof Techniques
        "Mathematical proof", "Mathematical induction", "Proof by contradiction",
        "Proof by contrapositive", "Direct proof", "Proof by construction",
        "Proof by exhaustion", "Diagonalization argument",
        
        # Sequences & Series
        "Arithmetic progression", "Geometric progression",
        "Harmonic series", "Power series", "Maclaurin series",
        "Binomial series", "Infinite series", "Alternating series",
        
        # Inequalities
        "Triangle inequality", "Cauchy–Schwarz inequality",
        "Hölder's inequality", "Minkowski inequality",
        "Jensen's inequality", "Arithmetic–geometric mean inequality",
        "Bernoulli's inequality", "Chebyshev's inequality",
        
        # Special Topics
        "Representation theory", "Harmonic analysis",
        "Ergodic theory", "Operator theory", "Spectral theory",
        "Algebraic combinatorics", "Analytic number theory",
        "Algebraic number theory", "Arithmetic geometry",
        "Complex dynamics", "Differential topology",
        "Symplectic geometry", "Lie group", "Algebraic group"
    ]
    
    def __init__(self, use_category_graph: bool = False, skip_ids: set = None):
        self.session = None
        self.use_category_graph = use_category_graph  # If True, fetch from category tree
        self.visited_pages = set()  # Track visited to avoid duplicates
        self.skip_ids = skip_ids or set()  # IDs to skip (already collected)
    
    async def scrape(self, max_items: int = None) -> List[Dict]:
        """Scrape Wikipedia math articles"""
        all_items = []
        max_items = max_items or 20

        # Use rotating User-Agent headers to avoid blocking
        headers = get_rotating_headers(include_academic=True)

        async with aiohttp.ClientSession(headers=headers) as session:
            self.session = session
            
            if self.use_category_graph:
                # Use category graph to find ALL math articles
                print("📊 Using Wikipedia category graph to discover math articles...")
                topics = await self._fetch_from_categories(max_items)
                print(f"   Found {len(topics)} math articles from categories")
            else:
                # Use hardcoded list
                topics = self.TOPICS[:max_items]
            
            for topic in topics:
                if topic in self.visited_pages:
                    continue

                # Check if already collected (by ID)
                expected_id = f"wikipedia_{topic.replace(' ', '_')}"
                if expected_id in self.skip_ids:
                    self.visited_pages.add(topic)
                    continue

                item = await self._scrape_article(topic)
                if item:
                    all_items.append(item)
                    self.visited_pages.add(topic)
                    if len(all_items) % 50 == 0:
                        print(f"   Collected {len(all_items)} articles...")

                await asyncio.sleep(0.5)  # Rate limiting

                if len(all_items) >= max_items:
                    break
        
        print(f"Wikipedia scraping complete: {len(all_items)} items")
        return all_items
    
    async def _fetch_from_categories(self, max_items: int) -> List[str]:
        """Fetch article titles from Wikipedia math categories using BFS"""
        # Start with main math categories
        root_categories = [
            'Category:Mathematics',
            'Category:Mathematical_theorems',
            'Category:Mathematical_proofs',
            'Category:Algebra',
            'Category:Calculus',
            'Category:Number_theory',
            'Category:Topology',
            'Category:Geometry',
            'Category:Mathematical_analysis'
        ]
        
        visited_categories = set()
        article_titles = []
        categories_to_visit = root_categories.copy()
        
        while categories_to_visit and len(article_titles) < max_items * 2:  # Fetch extra to filter
            if len(visited_categories) >= 100:  # Limit category traversal
                break
                
            category = categories_to_visit.pop(0)
            if category in visited_categories:
                continue
            
            visited_categories.add(category)
            
            # Fetch pages in this category
            params = {
                'action': 'query',
                'format': 'json',
                'list': 'categorymembers',
                'cmtitle': category,
                'cmlimit': '50',  # Max per request
                'cmtype': 'page|subcat'  # Get both pages and subcategories
            }
            
            try:
                async with self.session.get(self.API_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status != 200:
                        logger.warning(f"Category fetch failed for '{category}' with status {response.status}")
                        if response.status == 429:
                            logger.error("RATE LIMIT HIT during category fetching")
                        continue

                    data = await response.json()

                    # Check for API errors
                    if 'error' in data:
                        logger.error(f"Category API error for '{category}': {data['error']}")
                        continue

                    members = data.get('query', {}).get('categorymembers', [])

                    for member in members:
                        title = member['title']

                        if title.startswith('Category:'):
                            # Add subcategory to visit
                            if title not in visited_categories:
                                categories_to_visit.append(title)
                        else:
                            # It's an article
                            if title not in article_titles:
                                article_titles.append(title)

                    await asyncio.sleep(0.3)  # Rate limiting

            except aiohttp.ClientError as e:
                logger.error(f"Network error fetching category '{category}': {e}")
                continue
            except Exception as e:
                logger.error(f"Unexpected error fetching category '{category}': {e}")
                continue
        
        return article_titles[:max_items * 2]  # Return extra to filter during scraping
    
    async def _scrape_article(self, title: str) -> Dict:
        """Scrape a single Wikipedia article"""
        params = {
            'action': 'query',
            'format': 'json',
            'titles': title,
            'prop': 'extracts|categories',
            'exintro': '1',  # String not bool
            'explaintext': '1',  # String not bool
            'clcategories': 'Category:Mathematics'
        }

        # Retry with exponential backoff for rate limits
        max_retries = 3
        base_delay = 2  # seconds

        for attempt in range(max_retries):
            try:
                # Use session headers (already set with rotating User-Agent)
                async with self.session.get(self.API_URL, params=params) as response:
                    if response.status == 429:
                        # Rate limit hit - wait and retry
                        retry_after = response.headers.get('Retry-After')
                        if retry_after:
                            wait_time = int(retry_after)
                        else:
                            wait_time = base_delay * (2 ** attempt)  # Exponential backoff

                        logger.warning(f"RATE LIMIT (429) for '{title}' - waiting {wait_time}s (attempt {attempt + 1}/{max_retries})")
                        await asyncio.sleep(wait_time)
                        continue  # Retry

                    if response.status != 200:
                        logger.warning(f"Wikipedia API returned status {response.status} for '{title}'")
                        logger.warning(f"Response headers: {dict(response.headers)}")
                        return None

                data = await response.json()

                # Check for API errors
                if 'error' in data:
                    logger.error(f"Wikipedia API error for '{title}': {data['error']}")
                    return None

                pages = data.get('query', {}).get('pages', {})

                if not pages:
                    logger.debug(f"No pages returned for '{title}'")
                    return None

                # Get the page content
                page = list(pages.values())[0]

                # Check if page exists
                if 'missing' in page:
                    logger.debug(f"Page '{title}' does not exist")
                    return None

                if 'extract' not in page:
                    logger.warning(f"No extract in page for '{title}'. Page keys: {list(page.keys())}")
                    return None

                extract = page['extract']

                if len(extract) < 100:
                    logger.debug(f"Extract too short for '{title}': {len(extract)} chars")
                    return None

                # Get categories
                categories = page.get('categories', [])
                tags = [cat['title'].replace('Category:', '') for cat in categories[:5]]

                return {
                    'id': f"wikipedia_{title.replace(' ', '_')}",
                    'source': 'wikipedia',
                    'title': title,
                    'content': extract,
                    'tags': tags,
                    'url': f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                    'metadata': {
                        'language': 'en',
                        'type': 'encyclopedia'
                    }
                }

            except aiohttp.ClientError as e:
                logger.error(f"Network error scraping '{title}': {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(base_delay * (2 ** attempt))
                    continue
                return None
            except Exception as e:
                logger.error(f"Unexpected error scraping '{title}': {e}", exc_info=True)
                return None

        # If we exhausted all retries
        logger.error(f"Failed to scrape '{title}' after {max_retries} attempts")
        return None


# Test
async def test_scraper():
    scraper = WikipediaMathScraper()
    items = await scraper.scrape(max_items=3)
    
    print(f"\n✓ Collected {len(items)} Wikipedia items")
    if items:
        print("\nExample:")
        item = items[0]
        print(f"  Title: {item['title']}")
        print(f"  Content: {item['content'][:150]}...")


if __name__ == "__main__":
    asyncio.run(test_scraper())
