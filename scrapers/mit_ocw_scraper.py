"""
MIT OpenCourseWare Scraper
Problem sets and lecture notes from MIT courses
"""

import aiohttp
import asyncio
import logging
from typing import List, Dict
from bs4 import BeautifulSoup
import re

logger = logging.getLogger(__name__)


class MITOpenCourseWareScraper:
    """Scraper for MIT OpenCourseWare mathematics"""
    
    BASE_URL = "https://ocw.mit.edu"
    
    # Popular MIT math courses
    COURSES = [
        "/courses/18-01-single-variable-calculus-fall-2006",
        "/courses/18-02-multivariable-calculus-fall-2007",
        "/courses/18-03-differential-equations-spring-2010",
        "/courses/18-06-linear-algebra-spring-2010",
        "/courses/18-100a-real-analysis-fall-2020"
    ]
    
    def __init__(self):
        self.session = None
    
    async def scrape(self, max_items: int = None) -> List[Dict]:
        """Scrape MIT OCW problem sets"""
        all_items = []
        max_items = max_items or 20
        
        async with aiohttp.ClientSession() as session:
            self.session = session
            
            for course_url in self.COURSES[:3]:  # Limit courses
                if len(all_items) >= max_items:
                    break
                
                items = await self._scrape_course(course_url)
                all_items.extend(items)
                
                logger.info(f"MIT OCW - {course_url.split('/')[-1]}: {len(items)} items")
                await asyncio.sleep(2)
        
        logger.info(f"MIT OCW scraping terminé: {len(all_items[:max_items])} items")
        return all_items[:max_items]
    
    async def _scrape_course(self, course_path: str) -> List[Dict]:
        """Scrape problem sets from a course"""
        # Try assignments page
        assignments_url = f"{self.BASE_URL}{course_path}/assignments"
        
        try:
            async with self.session.get(assignments_url, timeout=15) as response:
                if response.status != 200:
                    return []
                
                html_content = await response.text()
                soup = BeautifulSoup(html_content, 'html.parser')
                
                # Get course title
                title_elem = soup.find('h1')
                course_title = title_elem.get_text(strip=True) if title_elem else ''
                
                # Find problem set descriptions
                items = []
                
                # Look for assignment descriptions in tables or lists
                tables = soup.find_all('table')
                for table in tables:
                    rows = table.find_all('tr')
                    for row in rows[1:]:  # Skip header
                        cells = row.find_all(['td', 'th'])
                        if len(cells) >= 2:
                            assignment_name = cells[0].get_text(strip=True)
                            description = cells[1].get_text(strip=True) if len(cells) > 1 else ''
                            
                            if assignment_name and ('Problem' in assignment_name or 'Assignment' in assignment_name):
                                # Extract course number
                                course_num = re.search(r'18[.-]\d+', course_path)
                                course_id = course_num.group(0) if course_num else 'unknown'
                                
                                items.append({
                                    'id': f"mit_{course_id}_{assignment_name.replace(' ', '_')}",
                                    'source': 'mit_ocw',
                                    'title': f"{course_title} - {assignment_name}",
                                    'content': description,
                                    'url': assignments_url,
                                    'tags': [course_id, 'problem-set'],
                                    'metadata': {
                                        'language': 'en',
                                        'type': 'problem-set',
                                        'institution': 'MIT',
                                        'course': course_title
                                    }
                                })
                
                return items[:5]  # Limit per course
        
        except Exception as e:
            logger.debug(f"Error scraping {course_path}: {e}")
            return []


# Test
async def test_scraper():
    scraper = MITOpenCourseWareScraper()
    items = await scraper.scrape(max_items=3)
    
    print(f"\n✓ Collected {len(items)} MIT OCW items")
    if items:
        print("\nExample:")
        item = items[0]
        print(f"  Title: {item['title']}")
        print(f"  Content: {item['content'][:100] if item['content'] else 'N/A'}...")


if __name__ == "__main__":
    asyncio.run(test_scraper())
