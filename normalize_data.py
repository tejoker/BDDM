#!/usr/bin/env python3
"""
Data Normalization Script
Converts source-specific formats into unified schema
"""

import json
import glob
import re
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime


class DataNormalizer:
    """Normalize data from different sources into unified schema"""
    
    DOMAIN_KEYWORDS = {
        'algebra': ['algebra', 'group-theory', 'ring-theory', 'field-theory', 'galois', 'linear-algebra'],
        'analysis': ['real-analysis', 'complex-analysis', 'functional-analysis', 'measure-theory', 'integration'],
        'topology': ['topology', 'algebraic-topology', 'differential-topology', 'topological-spaces'],
        'geometry': ['geometry', 'differential-geometry', 'algebraic-geometry', 'euclidean'],
        'logic': ['logic', 'set-theory', 'model-theory', 'proof-theory', 'mathematical-logic'],
        'number-theory': ['number-theory', 'analytic-number-theory', 'arithmetic'],
        'combinatorics': ['combinatorics', 'graph-theory', 'discrete-math'],
        'probability': ['probability', 'statistics', 'stochastic-processes', 'random'],
        'calculus': ['calculus', 'differential-equations', 'derivatives', 'integrals'],
    }
    
    def normalize_all(self, input_dir: str = 'samples_en/raw', output_dir: str = 'samples_en/normalized'):
        """Normalize all collected data"""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        all_normalized = []
        
        # Process each source
        sources = ['stackexchange', 'proofwiki', 'arxiv_full', 'wikipedia', 'mathoverflow', 'nlab']
        
        for source in sources:
            pattern = f"{input_dir}/{source}/*.json"
            files = glob.glob(pattern)
            
            if not files:
                print(f"⊘ No files found for {source}")
                continue
            
            print(f"\n📂 Processing {source}...")
            source_count = 0
            
            for file_path in files:
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        items = json.load(f)
                    
                    for item in items:
                        normalized = self.normalize_item(item)
                        if normalized:
                            all_normalized.append(normalized)
                            source_count += 1
                
                except Exception as e:
                    print(f"  ✗ Error processing {file_path}: {e}")
            
            print(f"  ✓ Normalized {source_count} items from {source}")
        
        # Save as JSON Lines (one JSON object per line)
        output_file = output_path / 'unified_data.jsonl'
        with open(output_file, 'w', encoding='utf-8') as f:
            for item in all_normalized:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')
        
        print(f"\n✅ Total: {len(all_normalized)} items normalized")
        print(f"📁 Saved to: {output_file}")
        
        # Generate stats
        self.print_stats(all_normalized)
        
        return all_normalized
    
    def normalize_item(self, item: Dict) -> Optional[Dict]:
        """Normalize a single item based on its source"""
        source = item.get('source', '')
        
        if source == 'stackexchange':
            return self.normalize_stackexchange(item)
        elif source == 'proofwiki':
            return self.normalize_proofwiki(item)
        elif source == 'arxiv_full':
            return self.normalize_arxiv_full(item)
        elif source == 'mathoverflow':
            return self.normalize_mathoverflow(item)
        elif source in ['wikipedia', 'nlab']:
            return self.normalize_encyclopedia(item)
        else:
            print(f"  ⚠ Unknown source: {source}")
            return None
    
    def normalize_stackexchange(self, item: Dict) -> Dict:
        """Normalize Stack Exchange Q&A"""
        return {
            'id': item['id'],
            'source': item['source'],
            'type': 'qa',
            'theorem_name': self.extract_theorem_name(item['title']),
            'title': item['title'],
            'statement': item.get('question', ''),
            'solution': item.get('answer', ''),
            'tags': item.get('tags', []),
            'difficulty': self.classify_difficulty_from_source('stackexchange', item),
            'domain': self.classify_domain(item.get('tags', [])),
            'quality_score': self.calculate_quality_score(item),
            'url': item.get('url', ''),
            'language': 'en',
            'created_date': item.get('created_date', ''),
            'extras': {
                'score': item.get('score', 0),
                'answer_score': item.get('answer_score', 0),
                'view_count': item.get('metadata', {}).get('view_count', 0)
            }
        }
    
    def normalize_proofwiki(self, item: Dict) -> Dict:
        """Normalize ProofWiki theorem-proof pairs"""
        return {
            'id': item['id'],
            'source': item['source'],
            'type': 'theorem_proof',
            'theorem_name': self.extract_theorem_name(item['title']),
            'title': item['title'],
            'statement': item.get('theorem', ''),
            'solution': item.get('proof', ''),
            'tags': item.get('tags', []),
            'difficulty': 'graduate',  # ProofWiki is generally advanced
            'domain': self.classify_domain(item.get('tags', [])),
            'quality_score': 95,  # ProofWiki is high quality, verified
            'url': item.get('url', ''),
            'language': 'en',
            'created_date': '',
            'extras': {
                'has_proof': item.get('metadata', {}).get('has_proof', True),
                'has_theorem': item.get('metadata', {}).get('has_theorem', True)
            }
        }
    
    def normalize_arxiv_full(self, item: Dict) -> Dict:
        """Normalize ArXiv full LaTeX theorem-proof pairs"""
        return {
            'id': item['id'],
            'source': item['source'],
            'type': 'theorem_proof',
            'theorem_name': self.extract_from_latex(item.get('theorem', '')),
            'title': item['title'],
            'statement': item.get('theorem', ''),
            'solution': item.get('proof', ''),
            'tags': item.get('tags', []),
            'difficulty': 'research',
            'domain': self.classify_domain(item.get('tags', [])),
            'quality_score': 90,  # ArXiv is peer-reviewed
            'url': item.get('url', ''),
            'language': 'en',
            'created_date': item.get('metadata', {}).get('published', ''),
            'extras': {
                'paper_id': item.get('paper_id', ''),
                'authors': item.get('metadata', {}).get('authors', []),
                'theorem_type': item.get('metadata', {}).get('type', 'theorem'),
                'proof_index': item.get('metadata', {}).get('proof_index', 0),
                'abstract': item.get('metadata', {}).get('abstract', '')
            }
        }
    
    def normalize_mathoverflow(self, item: Dict) -> Dict:
        """Normalize MathOverflow Q&A"""
        return {
            'id': item['id'],
            'source': item['source'],
            'type': 'qa',
            'theorem_name': self.extract_theorem_name(item['title']),
            'title': item['title'],
            'statement': item.get('question', ''),
            'solution': item.get('answer', ''),
            'tags': item.get('tags', []),
            'difficulty': 'research',
            'domain': self.classify_domain(item.get('tags', [])),
            'quality_score': self.calculate_quality_score(item),
            'url': item.get('url', ''),
            'language': 'en',
            'created_date': '',
            'extras': {
                'score': item.get('score', 0),
                'view_count': item.get('metadata', {}).get('view_count', 0),
                'answer_count': item.get('metadata', {}).get('answer_count', 0),
                'level': 'research'
            }
        }
    
    def normalize_encyclopedia(self, item: Dict) -> Dict:
        """Normalize Wikipedia/nLab encyclopedia entries"""
        return {
            'id': item['id'],
            'source': item['source'],
            'type': 'encyclopedia',
            'theorem_name': None,  # Encyclopedia articles don't have theorem names
            'title': item['title'],
            'statement': item.get('content', '')[:500],  # First 500 chars as summary
            'solution': item.get('content', ''),  # Full content
            'tags': item.get('tags', []),
            'difficulty': 'graduate' if item['source'] == 'nlab' else 'undergraduate',
            'domain': self.classify_domain(item.get('tags', [])),
            'quality_score': 85,
            'url': item.get('url', ''),
            'language': 'en',
            'created_date': '',
            'extras': {
                'content_length': len(item.get('content', ''))
            }
        }
    
    def extract_theorem_name(self, title: str) -> Optional[str]:
        """Extract theorem name from title if it's a named theorem"""
        if not title:
            return None
        
        # Patterns for named theorems
        patterns = [
            r"([A-Z][a-z]+(?:-[A-Z][a-z]+)*(?:'s)?)\s+(Theorem|Lemma|Inequality|Formula|Rule|Law|Identity|Principle)",
            r"(Fundamental Theorem of [A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
            r"(Mean Value Theorem|Intermediate Value Theorem|Extreme Value Theorem)",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, title, re.IGNORECASE)
            if match:
                return match.group(0)
        
        return None
    
    def extract_from_latex(self, latex_text: str) -> Optional[str]:
        """Extract theorem name from LaTeX label or content"""
        if not latex_text:
            return None
        
        # Look for [Theorem Name] after \begin{theorem}
        match = re.search(r'\\begin\{(?:theorem|lemma|proposition)\}\[([^\]]+)\]', latex_text)
        if match:
            return match.group(1)
        
        return None
    
    def classify_difficulty_from_source(self, source: str, item: Dict) -> str:
        """Classify difficulty based on source and metadata"""
        if source == 'mathoverflow' or item.get('metadata', {}).get('level') == 'research':
            return 'research'
        elif source == 'proofwiki':
            return 'graduate'
        elif source == 'arxiv_full':
            return 'research'
        
        # For Stack Exchange, try to infer from tags
        tags = item.get('tags', [])
        if any(tag in ['undergraduate', 'calculus', 'linear-algebra'] for tag in tags):
            return 'undergraduate'
        elif any(tag in ['graduate', 'real-analysis', 'abstract-algebra'] for tag in tags):
            return 'graduate'
        
        return 'undergraduate'  # default
    
    def classify_domain(self, tags: List[str]) -> str:
        """Classify mathematical domain from tags"""
        tags_lower = [tag.lower().replace('_', '-') for tag in tags]
        
        for domain, keywords in self.DOMAIN_KEYWORDS.items():
            if any(keyword in tags_lower for keyword in keywords):
                return domain
        
        return 'general'
    
    def calculate_quality_score(self, item: Dict) -> int:
        """Calculate quality score 0-100 based on community metrics"""
        score = 50  # base
        
        # Factor in question/answer score
        item_score = item.get('score', 0)
        answer_score = item.get('answer_score', 0)
        
        if item_score > 200:
            score += 25
        elif item_score > 100:
            score += 20
        elif item_score > 50:
            score += 10
        elif item_score > 20:
            score += 5
        
        if answer_score > 200:
            score += 25
        elif answer_score > 100:
            score += 20
        elif answer_score > 50:
            score += 10
        
        return min(100, score)
    
    def print_stats(self, normalized_items: List[Dict]):
        """Print statistics about normalized data"""
        print("\n" + "="*70)
        print("NORMALIZATION STATISTICS")
        print("="*70)
        
        # Count by source
        by_source = {}
        by_type = {}
        by_difficulty = {}
        by_domain = {}
        named_theorems = 0
        
        for item in normalized_items:
            source = item['source']
            by_source[source] = by_source.get(source, 0) + 1
            
            item_type = item['type']
            by_type[item_type] = by_type.get(item_type, 0) + 1
            
            difficulty = item['difficulty']
            by_difficulty[difficulty] = by_difficulty.get(difficulty, 0) + 1
            
            domain = item['domain']
            by_domain[domain] = by_domain.get(domain, 0) + 1
            
            if item['theorem_name']:
                named_theorems += 1
        
        print(f"\nBy Source:")
        for source, count in sorted(by_source.items()):
            print(f"  {source:20s}: {count:6d}")
        
        print(f"\nBy Type:")
        for item_type, count in sorted(by_type.items()):
            print(f"  {item_type:20s}: {count:6d}")
        
        print(f"\nBy Difficulty:")
        for difficulty, count in sorted(by_difficulty.items()):
            print(f"  {difficulty:20s}: {count:6d}")
        
        print(f"\nBy Domain:")
        for domain, count in sorted(by_domain.items(), key=lambda x: -x[1])[:10]:
            print(f"  {domain:20s}: {count:6d}")
        
        print(f"\nNamed Theorems: {named_theorems}/{len(normalized_items)} ({named_theorems/len(normalized_items)*100:.1f}%)")
        
        avg_quality = sum(item['quality_score'] for item in normalized_items) / len(normalized_items)
        print(f"Average Quality Score: {avg_quality:.1f}/100")


if __name__ == "__main__":
    import sys
    
    normalizer = DataNormalizer()
    
    input_dir = sys.argv[1] if len(sys.argv) > 1 else 'samples_en/raw'
    output_dir = sys.argv[2] if len(sys.argv) > 2 else 'samples_en/normalized'
    
    print("="*70)
    print("DATA NORMALIZATION")
    print("="*70)
    print(f"\nInput:  {input_dir}")
    print(f"Output: {output_dir}")
    
    normalized = normalizer.normalize_all(input_dir, output_dir)
    
    print(f"\n✅ Normalization complete!")
    print(f"   {len(normalized)} items in unified format")
    print(f"   Ready for ML training!")
