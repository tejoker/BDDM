#!/usr/bin/env python3
"""
Project Euler Scraper
Scrapes mathematical/computational problems and solutions from Project Euler

Project Euler: 956 problems with increasing difficulty
- Problems are public
- Solutions available after solving
- No anti-scraping (respectful rate limiting)
"""

import aiohttp
import asyncio
from bs4 import BeautifulSoup
from typing import List, Dict
import re


class ProjectEulerScraper:
    """
    Scraper for Project Euler mathematical problems.
    Note: Solutions are only available for problems you've solved.
    This scraper collects problem statements.
    """
    
    BASE_URL = "https://projecteuler.net"
    
    def __init__(self):
        self.session = None
    
    async def scrape(self, max_items: int = None) -> List[Dict]:
        """
        Scrape Project Euler problems.
        
        Args:
            max_items: Maximum number of problems to collect
            
        Returns:
            List of problem dictionaries
        """
        all_items = []
        max_items = max_items or 100
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        timeout = aiohttp.ClientTimeout(total=30)
        
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            self.session = session
            
            # Project Euler problems are numbered 1-800+
            # We'll fetch the archive page to get available problems
            problems = await self._get_problem_list()
            
            print(f"Found {len(problems)} Project Euler problems")
            
            for problem_id in problems[:max_items]:
                try:
                    item = await self._scrape_problem(problem_id)
                    if item:
                        all_items.append(item)
                        if len(all_items) % 10 == 0:
                            print(f"  Collected {len(all_items)} problems...")
                    
                    # Respectful delay
                    await asyncio.sleep(0.5)
                
                except Exception as e:
                    print(f"  Error scraping problem {problem_id}: {e}")
                    continue
        
        print(f"Project Euler scraping complete: {len(all_items)} items")
        return all_items
    
    async def _get_problem_list(self) -> List[int]:
        """Get list of available problem IDs from archive page"""
        url = f"{self.BASE_URL}/archives"
        
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    # Fallback: just use sequential IDs (updated to 956)
                    return list(range(1, 957))
                
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')
                
                # Find all problem links
                problem_ids = []
                for link in soup.find_all('a', href=re.compile(r'problem=(\d+)')):
                    match = re.search(r'problem=(\d+)', link['href'])
                    if match:
                        problem_ids.append(int(match.group(1)))
                
                return sorted(problem_ids) if problem_ids else list(range(1, 957))
        
        except Exception as e:
            print(f"  Error fetching problem list: {e}")
            # Fallback to sequential IDs (updated to 956)
            return list(range(1, 957))
    
    async def _scrape_problem(self, problem_id: int) -> Dict:
        """Scrape a single Project Euler problem"""
        url = f"{self.BASE_URL}/problem={problem_id}"
        
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    return None
                
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')
                
                # Get problem title
                title_elem = soup.find('h2')
                title = title_elem.get_text(strip=True) if title_elem else f"Problem {problem_id}"
                
                # Get problem content
                content_div = soup.find('div', class_='problem_content')
                if not content_div:
                    return None
                
                # Extract problem text
                problem_text = content_div.get_text(strip=True)
                
                # Extract difficulty rating (if available)
                difficulty_elem = soup.find('span', string=re.compile(r'Difficulty'))
                difficulty = None
                if difficulty_elem:
                    difficulty_match = re.search(r'(\d+)%', difficulty_elem.get_text())
                    if difficulty_match:
                        difficulty = int(difficulty_match.group(1))
                
                # Extract solved count
                solved_elem = soup.find('span', string=re.compile(r'Solved by'))
                solved_by = None
                if solved_elem:
                    solved_match = re.search(r'(\d+)', solved_elem.get_text())
                    if solved_match:
                        solved_by = int(solved_match.group(1))
                
                if len(problem_text) < 50:
                    return None
                
                return {
                    'id': f"euler_{problem_id}",
                    'source': 'project_euler',
                    'title': title,
                    'problem_number': problem_id,
                    'question': problem_text,
                    'difficulty': difficulty,
                    'solved_by': solved_by,
                    'url': url,
                    'tags': self._extract_tags(title, problem_text),
                    'metadata': {
                        'language': 'en',
                        'type': 'computational_math',
                        'requires_programming': True
                    }
                }
        
        except Exception as e:
            return None
    
    def _extract_tags(self, title: str, content: str) -> List[str]:
        """Extract relevant mathematical tags"""
        tags = []
        
        # Mathematical topics
        topics = {
            'number theory': ['prime', 'divisor', 'factor', 'fibonacci', 'digit'],
            'combinatorics': ['permutation', 'combination', 'arrangement', 'path'],
            'algebra': ['polynomial', 'equation', 'sum', 'product'],
            'geometry': ['triangle', 'square', 'circle', 'area', 'volume'],
            'probability': ['probability', 'random', 'expected'],
            'sequences': ['sequence', 'series', 'progression'],
            'optimization': ['maximum', 'minimum', 'largest', 'smallest']
        }
        
        text = (title + ' ' + content).lower()
        
        for topic, keywords in topics.items():
            if any(keyword in text for keyword in keywords):
                tags.append(topic)
        
        return tags


async def test_scraper():
    """Test the Project Euler scraper"""
    scraper = ProjectEulerScraper()
    
    print("🎯 Testing Project Euler Scraper")
    print("=" * 70)
    
    items = await scraper.scrape(max_items=10)
    
    print("\n" + "=" * 70)
    print(f"✅ Collected {len(items)} Project Euler problems")
    print("=" * 70)
    
    if items:
        print("\n📋 Examples:")
        for i, item in enumerate(items[:3], 1):
            print(f"\n{i}. Problem #{item['problem_number']}: {item['title']}")
            print(f"   Question: {item['question'][:100]}...")
            print(f"   Difficulty: {item['difficulty']}%" if item['difficulty'] else "   Difficulty: N/A")
            print(f"   Solved by: {item['solved_by']}" if item['solved_by'] else "")
            print(f"   Tags: {', '.join(item['tags'])}")


if __name__ == "__main__":
    asyncio.run(test_scraper())
