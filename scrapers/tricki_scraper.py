#!/usr/bin/env python3
"""
Tricki.org Scraper
Scrapes mathematical techniques and tricks from Tricki (by Timothy Gowers).

Tricki is a wiki explaining HOW to solve math problems - techniques and strategies.
"""

import asyncio
import aiohttp
from bs4 import BeautifulSoup
from typing import List, Dict
import re
import hashlib


class TrickiScraper:
    """
    Scraper for Tricki.org - a repository of mathematical problem-solving techniques.
    
    Tricki focuses on:
    - How to approach problems
    - General strategies
    - Specific techniques
    - Examples of technique applications
    """
    
    def __init__(self):
        self.session = None
        self.base_url = 'https://www.tricki.org'
        
        # Main categories on Tricki
        self.categories = [
            'How_to_solve_inequalities',
            'How_to_use_induction',
            'How_to_find_counterexamples',
            'How_to_use_Cauchy-Schwarz',
            'How_to_solve_counting_problems',
            'How_to_use_generating_functions',
            'How_to_solve_recurrence_relations',
            'How_to_use_pigeonhole_principle',
            'How_to_find_patterns',
            'How_to_prove_by_contradiction'
        ]
    
    async def scrape(self, max_items: int = None) -> List[Dict]:
        """
        Scrape mathematical techniques and examples from Tricki.
        
        ⚠️  NOTE: Tricki.org may be inactive/archived. This scraper is a template.
        
        Args:
            max_items: Maximum number of technique articles to collect
            
        Returns:
            List of dictionaries with technique descriptions and examples
        """
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as self.session:
            all_items = []
            
            print("⚠️  Tricki.org appears to be inactive/archived")
            print("   This scraper is a template for when/if the site returns")
            
            # First, get the main page to find all articles
            articles = await self._get_article_list()
            
            if not articles:
                print("   Could not access Tricki.org - site may be down")
                return []
            
            for article_url in articles:
                if max_items and len(all_items) >= max_items:
                    break
                
                try:
                    item = await self._scrape_article(article_url)
                    if item:
                        all_items.append(item)
                    
                    # Be respectful with delays
                    await asyncio.sleep(1)
                    
                except Exception as e:
                    print(f"Error scraping {article_url}: {e}")
                    continue
            
            return all_items[:max_items] if max_items else all_items
    
    async def _get_article_list(self) -> List[str]:
        """
        Get list of all article URLs from Tricki.
        
        Tricki has a Special:AllPages that lists all articles.
        """
        article_urls = []
        
        try:
            # Try to get all pages listing
            url = f'{self.base_url}/Special:AllPages'
            
            async with self.session.get(url) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # Find all article links
                    for link in soup.find_all('a', href=True):
                        href = link['href']
                        # Filter for actual article pages
                        if href.startswith('/') and not any(x in href for x in 
                            ['Special:', 'Talk:', 'User:', 'Help:', 'Category:']):
                            full_url = self.base_url + href
                            if full_url not in article_urls:
                                article_urls.append(full_url)
                else:
                    print(f"  ⚠️  Could not fetch article list (status {response.status})")
                    # Fallback to hardcoded categories
                    article_urls = [f'{self.base_url}/{cat}' for cat in self.categories]
        
        except Exception as e:
            print(f"  ⚠️  Error fetching article list: {e}")
            # Fallback to hardcoded categories
            article_urls = [f'{self.base_url}/{cat}' for cat in self.categories]
        
        return article_urls
    
    async def _scrape_article(self, url: str) -> Dict:
        """
        Scrape a single Tricki article.
        
        Tricki articles typically have:
        - Technique name
        - General description
        - Prerequisites
        - Examples of application
        - Related techniques
        """
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    return None
                
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')
                
                # Extract title
                title_elem = soup.find('h1', class_='firstHeading')
                title = title_elem.get_text(strip=True) if title_elem else url.split('/')[-1]
                
                # Extract main content
                content_elem = soup.find('div', id='mw-content-text')
                if not content_elem:
                    return None
                
                # Get all text content
                paragraphs = content_elem.find_all('p')
                content = '\n\n'.join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
                
                # Extract examples (often in specific sections)
                examples = []
                example_headers = content_elem.find_all(['h2', 'h3'], string=re.compile('Example', re.I))
                for header in example_headers:
                    # Get content until next header
                    example_text = []
                    for sibling in header.find_next_siblings():
                        if sibling.name in ['h2', 'h3']:
                            break
                        if sibling.name == 'p':
                            example_text.append(sibling.get_text(strip=True))
                    if example_text:
                        examples.append('\n'.join(example_text))
                
                # Create item
                item = {
                    'id': self._generate_id(url),
                    'source': 'tricki',
                    'title': title,
                    'url': url,
                    'technique': title,
                    'description': content,
                    'examples': examples,
                    'num_examples': len(examples),
                    'content_length': len(content),
                    'tags': self._extract_tags(title, content)
                }
                
                return item
        
        except Exception as e:
            print(f"  ✗ Error scraping {url}: {e}")
            return None
    
    def _generate_id(self, url: str) -> str:
        """Generate unique ID from URL"""
        return f"tricki_{hashlib.md5(url.encode()).hexdigest()[:16]}"
    
    def _extract_tags(self, title: str, content: str) -> List[str]:
        """Extract relevant tags from title and content"""
        tags = []
        
        # Common mathematical topics
        topics = [
            'induction', 'inequality', 'combinatorics', 'algebra',
            'analysis', 'number theory', 'geometry', 'probability',
            'calculus', 'linear algebra', 'topology', 'graph theory',
            'generating functions', 'pigeonhole', 'contradiction',
            'counterexample', 'proof technique'
        ]
        
        text = (title + ' ' + content).lower()
        for topic in topics:
            if topic.lower() in text:
                tags.append(topic)
        
        return tags
    
    def _clean_text(self, text: str) -> str:
        """Clean and normalize text"""
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text)
        # Remove wiki markup remnants
        text = re.sub(r'\[edit\]', '', text)
        return text.strip()


async def main():
    """Test the scraper"""
    scraper = TrickiScraper()
    
    print("🎯 Scraping Tricki.org for mathematical techniques...\n")
    
    results = await scraper.scrape(max_items=10)
    
    print(f"\n✅ Found {len(results)} technique articles")
    
    if results:
        print("\nExample:")
        ex = results[0]
        print(f"Technique: {ex['title']}")
        print(f"Description: {ex['description'][:200]}...")
        print(f"Examples: {ex['num_examples']}")
        print(f"Tags: {', '.join(ex['tags'])}")


if __name__ == "__main__":
    asyncio.run(main())
