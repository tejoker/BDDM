"""
ArXiv Full LaTeX Scraper
Downloads complete LaTeX sources and extracts theorem-proof pairs
WARNING: This is SLOW and requires large storage!
"""

import aiohttp
import asyncio
import logging
from typing import List, Dict, Optional
import tarfile
import io
import re
from pathlib import Path
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)


class ArxivFullScraper:
    """
    Full ArXiv scraper - downloads LaTeX sources and extracts proofs
    
    WARNING: 
    - Very slow: ~5-10 seconds per paper
    - Large storage: 100KB-5MB per paper
    - Only 10-20% papers have extractable proofs
    - Use only if you need actual theorem-proof pairs from research papers
    """
    
    BASE_URL = "https://arxiv.org"
    EXPORT_URL = "https://export.arxiv.org"
    
    # Math categories with lots of proofs
    PROOF_HEAVY_CATEGORIES = [
        'math.LO',  # Logic - lots of formal proofs
        'math.CT',  # Category Theory - formal
        'math.AG',  # Algebraic Geometry
        'math.NT',  # Number Theory
        'math.CO',  # Combinatorics
        'math.GR',  # Group Theory
        'math.RA',  # Rings and Algebras
    ]
    
    def __init__(self, download_dir: str = "arxiv_latex_cache"):
        self.session = None
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(exist_ok=True)
        
    async def scrape(self, max_items: int = None, categories: List[str] = None) -> List[Dict]:
        """
        Scrape arXiv papers and extract theorem-proof pairs
        
        Args:
            max_items: Maximum number of PAPERS to process (not proofs)
            categories: List of arXiv categories (default: PROOF_HEAVY_CATEGORIES)
        
        Returns:
            List of theorem-proof pairs extracted from papers
        """
        all_proofs = []
        categories = categories or self.PROOF_HEAVY_CATEGORIES
        max_items = max_items or 100
        
        async with aiohttp.ClientSession() as session:
            self.session = session
            
            # Get paper IDs from categories
            paper_ids = await self._search_papers(max_items, categories)
            logger.info(f"ArXiv: Found {len(paper_ids)} papers to process")
            
            # Process each paper
            for i, paper_id in enumerate(paper_ids[:max_items], 1):
                logger.info(f"Processing paper {i}/{len(paper_ids[:max_items])}: {paper_id}")
                
                try:
                    proofs = await self._process_paper(paper_id)
                    all_proofs.extend(proofs)
                    
                    if proofs:
                        logger.info(f"  ✓ Extracted {len(proofs)} proofs from {paper_id}")
                    else:
                        logger.debug(f"  ✗ No extractable proofs in {paper_id}")
                    
                    # Rate limiting - be respectful to arXiv
                    await asyncio.sleep(3)
                    
                except Exception as e:
                    logger.warning(f"Error processing {paper_id}: {e}")
                    continue
        
        logger.info(f"ArXiv full scraping complete: {len(all_proofs)} theorem-proof pairs from {len(paper_ids[:max_items])} papers")
        return all_proofs
    
    async def _search_papers(self, max_results: int, categories: List[str]) -> List[str]:
        """Search arXiv and return paper IDs"""
        paper_ids = []
        results_per_category = max(10, max_results // len(categories))
        
        for category in categories:
            url = f"{self.EXPORT_URL}/api/query"
            params = {
                'search_query': f'cat:{category}',
                'start': 0,
                'max_results': results_per_category,
                'sortBy': 'submittedDate',
                'sortOrder': 'descending'
            }
            
            try:
                async with self.session.get(url, params=params) as response:
                    if response.status == 200:
                        xml_text = await response.text()
                        ids = self._extract_paper_ids(xml_text)
                        paper_ids.extend(ids)
                        logger.info(f"Category {category}: {len(ids)} papers")
                
                await asyncio.sleep(3)  # Rate limit
                
            except Exception as e:
                logger.warning(f"Error searching {category}: {e}")
                continue
        
        return paper_ids[:max_results]
    
    def _extract_paper_ids(self, xml_text: str) -> List[str]:
        """Extract paper IDs from arXiv API XML response"""
        ids = []
        try:
            root = ET.fromstring(xml_text)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            
            for entry in root.findall('atom:entry', ns):
                id_elem = entry.find('atom:id', ns)
                if id_elem is not None:
                    # Extract ID from URL: http://arxiv.org/abs/2301.12345
                    match = re.search(r'(\d+\.\d+)', id_elem.text)
                    if match:
                        ids.append(match.group(1))
        except Exception as e:
            logger.debug(f"Error parsing XML: {e}")
        
        return ids
    
    async def _process_paper(self, paper_id: str) -> List[Dict]:
        """
        Download and process a single paper
        Returns list of theorem-proof pairs extracted
        """
        # Get metadata first
        metadata = await self._get_metadata(paper_id)
        
        # Download LaTeX source
        latex_content = await self._download_source(paper_id)
        
        if not latex_content:
            return []
        
        # Extract proofs from LaTeX
        proofs = self._extract_proofs_from_latex(latex_content)
        
        # Create items
        items = []
        for i, proof in enumerate(proofs):
            items.append({
                'id': f"arxiv_{paper_id}_{i}",
                'source': 'arxiv_full',
                'paper_id': paper_id,
                'title': metadata.get('title', ''),
                'theorem': proof.get('theorem', ''),
                'proof': proof.get('proof', ''),
                'tags': metadata.get('categories', []),
                'url': f"{self.BASE_URL}/abs/{paper_id}",
                'metadata': {
                    'authors': metadata.get('authors', []),
                    'abstract': metadata.get('abstract', ''),
                    'published': metadata.get('published', ''),
                    'proof_index': i,
                    'language': 'en'
                }
            })
        
        return items
    
    async def _download_source(self, paper_id: str) -> Optional[str]:
        """
        Download LaTeX source for a paper
        Returns: Combined LaTeX content from all .tex files
        """
        url = f"{self.BASE_URL}/e-print/{paper_id}"
        
        try:
            async with self.session.get(url, timeout=30) as response:
                if response.status != 200:
                    logger.debug(f"Cannot download source for {paper_id}: status {response.status}")
                    return None
                
                content = await response.read()
                
                # Try to extract from tar.gz or use directly
                latex_content = self._extract_tex_from_tar(content)
                
                # Cache to disk (optional - for debugging)
                # cache_file = self.download_dir / f"{paper_id}.tex"
                # cache_file.write_text(latex_content or "", encoding='utf-8')
                
                return latex_content
                
        except asyncio.TimeoutError:
            logger.warning(f"Timeout downloading {paper_id}")
            return None
        except Exception as e:
            logger.warning(f"Error downloading {paper_id}: {e}")
            return None
    
    def _extract_tex_from_tar(self, content: bytes) -> Optional[str]:
        """Extract LaTeX content from tar.gz or plain .tex"""
        try:
            # Try as tar.gz first
            tar_buffer = io.BytesIO(content)
            with tarfile.open(fileobj=tar_buffer, mode='r:gz') as tar:
                # Get all .tex files
                tex_files = [m for m in tar.getmembers() if m.name.endswith('.tex')]
                
                if not tex_files:
                    return None
                
                # Combine all .tex files (some papers split into multiple files)
                all_tex = []
                for tex_file in tex_files:
                    f = tar.extractfile(tex_file)
                    if f:
                        tex_content = f.read().decode('utf-8', errors='ignore')
                        all_tex.append(tex_content)
                
                return "\n\n".join(all_tex)
                
        except tarfile.TarError:
            # Not a tar file, maybe plain .tex
            try:
                return content.decode('utf-8', errors='ignore')
            except:
                return None
        except Exception as e:
            logger.debug(f"Error extracting tex: {e}")
            return None
    
    async def _get_metadata(self, paper_id: str) -> Dict:
        """Get paper metadata from arXiv API"""
        url = f"{self.EXPORT_URL}/api/query"
        params = {'id_list': paper_id}
        
        metadata = {}
        
        try:
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    xml_text = await response.text()
                    
                    root = ET.fromstring(xml_text)
                    ns = {'atom': 'http://www.w3.org/2005/Atom',
                          'arxiv': 'http://arxiv.org/schemas/atom'}
                    
                    entry = root.find('atom:entry', ns)
                    if entry is not None:
                        # Title
                        title = entry.find('atom:title', ns)
                        if title is not None:
                            metadata['title'] = title.text.strip()
                        
                        # Abstract
                        abstract = entry.find('atom:summary', ns)
                        if abstract is not None:
                            metadata['abstract'] = abstract.text.strip()[:500]
                        
                        # Authors
                        authors = []
                        for author in entry.findall('atom:author', ns):
                            name = author.find('atom:name', ns)
                            if name is not None:
                                authors.append(name.text.strip())
                        metadata['authors'] = authors
                        
                        # Categories
                        categories = []
                        for cat in entry.findall('atom:category', ns):
                            term = cat.get('term')
                            if term:
                                categories.append(term)
                        metadata['categories'] = categories
                        
                        # Published date
                        published = entry.find('atom:published', ns)
                        if published is not None:
                            metadata['published'] = published.text.strip()
        
        except Exception as e:
            logger.debug(f"Error getting metadata for {paper_id}: {e}")
        
        return metadata
    
    def _extract_proofs_from_latex(self, latex_content: str) -> List[Dict]:
        """
        Extract theorem-proof pairs from LaTeX content
        
        Looks for patterns like:
        - \\begin{theorem}...\\end{theorem} followed by \\begin{proof}...\\end{proof}
        - \\begin{lemma}...\\end{lemma} followed by \\begin{proof}...\\end{proof}
        - \\begin{proposition}...\\end{proposition} followed by \\begin{proof}...\\end{proof}
        """
        proofs = []
        
        # Patterns for theorem-like environments
        theorem_patterns = [
            ('theorem', r'\\begin{theorem}(.*?)\\end{theorem}'),
            ('lemma', r'\\begin{lemma}(.*?)\\end{lemma}'),
            ('proposition', r'\\begin{proposition}(.*?)\\end{proposition}'),
            ('corollary', r'\\begin{corollary}(.*?)\\end{corollary}'),
        ]
        
        proof_pattern = r'\\begin{proof}(.*?)\\end{proof}'
        
        for env_name, theorem_pattern in theorem_patterns:
            # Find all theorems of this type
            for theorem_match in re.finditer(theorem_pattern, latex_content, re.DOTALL | re.IGNORECASE):
                theorem_text = theorem_match.group(1)
                theorem_end = theorem_match.end()
                
                # Look for proof immediately after (within next 2000 chars)
                remaining_text = latex_content[theorem_end:theorem_end + 2000]
                proof_match = re.search(proof_pattern, remaining_text, re.DOTALL | re.IGNORECASE)
                
                if proof_match:
                    proof_text = proof_match.group(1)
                    
                    # Clean LaTeX
                    theorem_clean = self._clean_latex(theorem_text)
                    proof_clean = self._clean_latex(proof_text)
                    
                    # Quality filter: reasonable length
                    if (len(theorem_clean) > 20 and len(proof_clean) > 50 and
                        len(theorem_clean) < 5000 and len(proof_clean) < 10000):
                        
                        proofs.append({
                            'type': env_name,
                            'theorem': theorem_clean,
                            'proof': proof_clean
                        })
        
        # Limit to reasonable number per paper
        return proofs[:20]
    
    def _clean_latex(self, latex_text: str) -> str:
        """Clean LaTeX commands while preserving mathematical content"""
        if not latex_text:
            return ""
        
        text = latex_text
        
        # Remove comments
        text = re.sub(r'%.*?$', '', text, flags=re.MULTILINE)
        
        # Remove labels, refs, cites
        text = re.sub(r'\\label\{[^}]+\}', '', text)
        text = re.sub(r'\\cite\{[^}]+\}', '[REF]', text)
        text = re.sub(r'\\ref\{[^}]+\}', '[REF]', text)
        text = re.sub(r'\\eqref\{[^}]+\}', '[EQ]', text)
        
        # Keep math mode delimiters: $, $$, \[, \], \(, \)
        # Keep common math environments: equation, align, etc.
        
        # Remove some formatting commands but keep their content
        text = re.sub(r'\\textbf\{([^}]+)\}', r'\1', text)
        text = re.sub(r'\\textit\{([^}]+)\}', r'\1', text)
        text = re.sub(r'\\emph\{([^}]+)\}', r'\1', text)
        
        # Clean excessive whitespace
        text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
        text = re.sub(r' +', ' ', text)
        
        return text.strip()


# Test standalone
async def test_scraper():
    """Test the full ArXiv scraper"""
    scraper = ArxivFullScraper()
    
    print("="*70)
    print("ArXiv FULL LaTeX Scraper Test")
    print("WARNING: This will download LaTeX sources - slow and large!")
    print("="*70)
    
    # Test with just 5 papers
    proofs = await scraper.scrape(max_items=5, categories=['math.LO'])
    
    print(f"\n✓ Extracted {len(proofs)} theorem-proof pairs from 5 papers")
    print(f"  Success rate: {len(proofs)/5:.1f} proofs per paper")
    
    if proofs:
        print("\nExample:")
        proof = proofs[0]
        print(f"  Paper: {proof['title'][:60]}...")
        print(f"  Type: {proof.get('type', 'unknown')}")
        print(f"  Theorem: {proof['theorem'][:150]}...")
        print(f"  Proof: {proof['proof'][:150]}...")
    
    return proofs


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    
    # Run test
    results = asyncio.run(test_scraper())
    
    print(f"\n{'='*70}")
    print(f"Total proofs extracted: {len(results)}")
    print(f"{'='*70}")
