#!/usr/bin/env python3#!/usr/bin/env python3

""""""

Art of Problem Solving (AoPS) ScraperArt of Problem Solving (AoPS) Scraper

Scrapes competition math problems and solutions from AoPS forums and resources.Scrapes competition math problems and solutions from AoPS forums and resources.



⚠️  WARNING: AoPS has anti-scraping protection⚠️  WARNING: AoPS has anti-scraping protection

- Rate limiting- Rate limiting

- CAPTCHA challenges- CAPTCHA challenges

- IP blocking- IP blocking

Use responsibly with delays and respect their ToS.Use responsibly with delays and respect their ToS.

""""""



import asyncioimport asyncio

import aiohttpimport aiohttp

from bs4 import BeautifulSoupfrom bs4 import BeautifulSoup

from typing import List, Dictfrom typing import List, Dict

import reimport re

import hashlibimport hashlib

import randomimport random





class AoPSScraper:class AoPSScraper:

    """    """

    Scraper for Art of Problem Solving content.    Scraper for Art of Problem Solving content.

    Focuses on problem-solution pairs from competition math.    Focuses on problem-solution pairs from competition math.

    """    """

        

    def __init__(self):    def __init__(self):

        self.session = None        self.session = None

        self.base_url = 'https://artofproblemsolving.com'        self.base_url = 'https://artofproblemsolving.com'

                

        # AoPS has STRONG anti-scraping - use conservative settings        # AoPS has STRONG anti-scraping - use conservative settings

        self.delay_range = (3, 7)  # Random delay between requests        self.delay_range = (3, 7)  # Random delay between requests

        self.max_retries = 2        self.max_retries = 2

                

        # User agent to appear as browser        # User agent to appear as browser

        self.headers = {        self.headers = {

            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',

            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',

            'Accept-Language': 'en-US,en;q=0.5',            'Accept-Language': 'en-US,en;q=0.5',

            'Accept-Encoding': 'gzip, deflate',            'Accept-Encoding': 'gzip, deflate',

            'Connection': 'keep-alive',            'Connection': 'keep-alive',

            'Upgrade-Insecure-Requests': '1'            'Upgrade-Insecure-Requests': '1'

        }        }

                

        # Competition problem categories        # Competition problem categories

        self.categories = [        self.categories = [

            'AMC_8',            'AMC_8',

            'AMC_10',            'AMC_10',

            'AMC_12',            'AMC_12',

            'AIME',            'AIME',

            'USAMO',            'USAMO',

            'IMO',            'IMO',

            'Putnam'            'Putnam'

        ]        ]

        

    async def scrape(self, max_items: int = None) -> List[Dict]:    async def scrape(self, max_items: int = None) -> List[Dict]:

        """        """

        Scrape problem-solution pairs from AoPS.        Scrape problem-solution pairs from AoPS.

                

        ⚠️  This will be SLOW due to anti-scraping measures.        ⚠️  This will be SLOW due to anti-scraping measures.

        Expect ~5-10 seconds per problem.        Expect ~5-10 seconds per problem.

                

        Args:        Args:

            max_items: Maximum number of problems to collect            max_items: Maximum number of problems to collect

                        

        Returns:        Returns:

            List of dictionaries with problem-solution pairs            List of dictionaries with problem-solution pairs

        """        """

        timeout = aiohttp.ClientTimeout(total=30)        timeout = aiohttp.ClientTimeout(total=30)

        async with aiohttp.ClientSession(timeout=timeout, headers=self.headers) as self.session:        async with aiohttp.ClientSession(timeout=timeout, headers=self.headers) as self.session:

            all_items = []            all_items = []

                        

            print("⚠️  AoPS has strong anti-scraping protection")            print("⚠️  AoPS has strong anti-scraping protection")

            print("   This will be SLOW (5-10 sec per problem)")            print("   This will be SLOW (5-10 sec per problem)")

            print("   May encounter CAPTCHAs or IP blocks")            print("   May encounter CAPTCHAs or IP blocks")

                        

            for category in self.categories:            for category in self.categories:

                if max_items and len(all_items) >= max_items:                if max_items and len(all_items) >= max_items:

                    break                    break

                                

                try:                try:

                    items = await self._scrape_category(category, max_items)                    items = await self._scrape_category(category, max_items)

                    all_items.extend(items)                    all_items.extend(items)

                                        

                    if max_items:                    if max_items:

                        all_items = all_items[:max_items]                        all_items = all_items[:max_items]

                                        

                    # Long delay between categories to avoid detection                    # Long delay between categories to avoid detection

                    await asyncio.sleep(random.uniform(5, 10))                    await asyncio.sleep(random.uniform(5, 10))

                                        

                except Exception as e:                except Exception as e:

                    print(f"Error scraping {category}: {e}")                    print(f"Error scraping {category}: {e}")

                    continue                    continue

                        

            return all_items            return all_items

            

    async def _scrape_category(self, category: str, max_items: int = None) -> List[Dict]:        logger.info(f"AoPS scraping terminé: {len(all_items[:max_items])} items")

        """        return all_items[:max_items]

        Scrape problems from a specific competition category.    

                    return all_items

        Note: This is a template showing the structure.    

        Real implementation needs to:    async def _scrape_category(self, category: str, max_items: int = None) -> List[Dict]:

        1. Handle CAPTCHA challenges        """

        2. Use session cookies        Scrape problems from a specific competition category.

        3. Parse forum/wiki pages        

        4. Extract LaTeX from their custom format        Note: This is a template showing the structure.

        """        Real implementation needs to:

        items = []        1. Handle CAPTCHA challenges

                2. Use session cookies

        print(f"  📝 Attempting {category}...")        3. Parse forum/wiki pages

        print(f"  ⚠️  Template implementation - needs anti-scraping bypass")        4. Extract LaTeX from their custom format

                """

        # Example structure of what we'd extract        items = []

        example = {        

            'id': self._generate_id(f"{category}_problem_1"),        print(f"  📝 Attempting {category}...")

            'source': 'aops',        print(f"  ⚠️  Template implementation - needs anti-scraping bypass")

            'competition': category,        

            'year': 2023,        # Example structure of what we'd extract

            'problem_number': 1,        example = {

            'difficulty': 'intermediate',  # based on competition level            'id': self._generate_id(f"{category}_problem_1"),

            'problem_statement': 'Find the sum of all positive integers...',            'source': 'aops',

            'solution': 'We can solve this by noting that...',            'competition': category,

            'solution_author': 'Community',            'year': 2023,

            'votes': 15,            'problem_number': 1,

            'tags': ['algebra', 'number-theory'],            'difficulty': 'intermediate',  # based on competition level

            'url': f'{self.base_url}/wiki/index.php/{category}'            'problem_statement': 'Find the sum of all positive integers...',

        }            'solution': 'We can solve this by noting that...',

                    'solution_author': 'Community',

        # Real implementation would:            'votes': 15,

        # 1. Navigate to category page            'tags': ['algebra', 'number-theory'],

        # 2. Find problem links            'url': f'{self.base_url}/wiki/index.php/{category}'

        # 3. For each problem:        }

        #    a. GET problem page (with delay)        

        #    b. Parse problem statement        # Real implementation would:

        #    c. Parse solution(s)        # 1. Navigate to category page

        #    d. Handle their LaTeX format (Asymptote diagrams, etc.)        # 2. Find problem links

        # 4. Handle rate limits and CAPTCHAs        # 3. For each problem:

                #    a. GET problem page (with delay)

        return items  # Returns empty - needs full implementation with anti-scraping        #    b. Parse problem statement

            #    c. Parse solution(s)

    def _generate_id(self, text: str) -> str:        #    d. Handle their LaTeX format (Asymptote diagrams, etc.)

        """Generate unique ID from text"""        # 4. Handle rate limits and CAPTCHAs

        return f"aops_{hashlib.md5(text.encode()).hexdigest()[:16]}"        

            return items  # Returns empty - needs full implementation with anti-scraping    async def _scrape_problem(self, url: str) -> Dict:

    async def _random_delay(self):        """Scrape a single problem with solution"""

        """Random delay to avoid detection"""        try:

        delay = random.uniform(*self.delay_range)            async with self.session.get(url, timeout=10) as response:

        await asyncio.sleep(delay)                if response.status != 200:

                        return None

    def _clean_aops_latex(self, text: str) -> str:                

        """                html_content = await response.text()

        Clean AoPS custom LaTeX format.                soup = BeautifulSoup(html_content, 'html.parser')

        They use $ for inline math and [math] tags.                

        """                # Get title

        # Convert [math] tags to standard LaTeX                title_elem = soup.find('h1', id='firstHeading')

        text = re.sub(r'\[math\](.*?)\[/math\]', r'$\1$', text)                title = title_elem.get_text(strip=True) if title_elem else ''

        # Handle Asymptote code blocks                

        text = re.sub(r'\[asy\].*?\[/asy\]', '[diagram]', text, flags=re.DOTALL)                # Get content

        return text.strip()                content_div = soup.find('div', id='mw-content-text')

                if not content_div:

                    return None

async def main():                

    """Test the scraper"""                # Look for Problem and Solution sections

    scraper = AoPSScraper()                problem_text = ''

                    solution_text = ''

    print("⚠️  WARNING: AoPS scraping will likely fail without:")                

    print("   - CAPTCHA solver")                # Find headers

    print("   - Rotating proxies")                headers = content_div.find_all(['h2', 'h3'])

    print("   - Session management")                

    print("\nThis is a template implementation.\n")                for header in headers:

                        header_text = header.get_text(strip=True).lower()

    results = await scraper.scrape(max_items=5)                    

                        if 'problem' in header_text:

    print(f"\n✅ Found {len(results)} problems")                        # Get content after this header

                            next_elem = header.find_next_sibling()

    if results:                        if next_elem:

        print("\nExample:")                            problem_text = next_elem.get_text(strip=True)

        ex = results[0]                    

        print(f"Competition: {ex['competition']} {ex['year']}")                    elif 'solution' in header_text:

        print(f"Problem: {ex['problem_statement'][:100]}...")                        # Get content after this header

                        next_elem = header.find_next_sibling()

                        if next_elem:

if __name__ == "__main__":                            solution_text = next_elem.get_text(strip=True)

    asyncio.run(main())                

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
