"""
Data Cleaner for mathematical content
"""

import re
import logging
from typing import Dict, Optional
from html import unescape

logger = logging.getLogger(__name__)


class DataCleaner:
    """Mathematical data cleaner"""

    # LaTeX patterns to preserve
    LATEX_PATTERNS = [
        r'\$[^\$]+\$',  # Inline math
        r'\$\$[^\$]+\$\$',  # Display math
        r'\\[(.*?)\\]',  # Display math alt
        r'\\((.*?)\\)',  # Inline math alt
    ]
    
    # Proof structure keywords
    PROOF_KEYWORDS = {
        'fr': [
            'démonstration', 'preuve', 'montrons', 'supposons',
            'par récurrence', 'par induction', "par l'absurde",
            'donc', 'ainsi', 'par conséquent', 'cqfd',
            'cas de base', 'hérédité', 'hypothèse'
        ],
        'en': [
            'proof', 'theorem', 'lemma', 'proposition',
            'assume', 'suppose', 'by induction', 'by contradiction',
            'therefore', 'thus', 'hence', 'qed',
            'base case', 'inductive step', 'hypothesis'
        ]
    }
    
    def __init__(self, min_length: int = 50, max_length: int = 5000):
        self.min_length = min_length
        self.max_length = max_length
    
    def clean(self, item: Dict) -> Optional[Dict]:
        """
        Clean and validate an item.
        Returns None if item doesn't pass quality filters.
        """
        try:
            cleaned_item = item.copy()

            # Clean each text field
            for field in ['question', 'answer', 'theorem', 'proof', 'title']:
                if field in cleaned_item:
                    cleaned_item[field] = self._clean_text(cleaned_item[field])

            # Validate quality
            if not self._is_valid(cleaned_item):
                return None

            # Detect language
            cleaned_item['language'] = self._detect_language(cleaned_item)

            # Extract proof structure
            cleaned_item['proof_structure'] = self._extract_proof_structure(cleaned_item)

            return cleaned_item

        except Exception as e:
            logger.warning(f"Cleaning error: {e}")
            return None
    
    def _clean_text(self, text: str) -> str:
        """Clean mathematical text"""
        if not text:
            return ''

        # Decode HTML entities
        text = unescape(text)

        # Preserve LaTeX formulas (mark temporarily)
        latex_placeholders = {}
        for i, pattern in enumerate(self.LATEX_PATTERNS):
            for match in re.finditer(pattern, text, re.DOTALL):
                placeholder = f"__LATEX_{i}_{len(latex_placeholders)}__"
                latex_placeholders[placeholder] = match.group(0)
                text = text.replace(match.group(0), placeholder)

        # Clean special characters but keep math punctuation
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'[\r\n]+', ' ', text)

        # Remove URLs
        text = re.sub(r'http[s]?://\S+', '', text)

        # Remove remaining HTML tags
        text = re.sub(r'<[^>]+>', '', text)

        # Clean LaTeX artifacts
        text = re.sub(r'\\newline', ' ', text)
        text = re.sub(r'\\\\', ' ', text)

        # Restore LaTeX formulas
        for placeholder, original in latex_placeholders.items():
            text = text.replace(placeholder, original)

        # Clean trailing spaces
        text = ' '.join(text.split())

        return text.strip()
    
    def _is_valid(self, item: Dict) -> bool:
        """
        Validate item quality.
        Criteria:
        - Min/max length
        - Mathematical content presence
        - No spam/garbage
        """
        # Get main content
        content = ''
        if 'question' in item and 'answer' in item:
            content = item['question'] + ' ' + item['answer']
        elif 'theorem' in item and 'proof' in item:
            content = item['theorem'] + ' ' + item['proof']
        else:
            return False

        # Check length
        if len(content) < self.min_length:
            return False

        if len(content) > self.max_length:
            logger.debug(f"Item truncated: {len(content)} chars")

        # Check for mathematical content
        has_math = any([
            '$' in content,
            '\\(' in content,
            '\\[' in content,
            any(symbol in content for symbol in ['∈', '∀', '∃', '→', '≤', '≥', '∑', '∫'])
        ])

        # Or mathematical keywords
        has_math_keywords = any([
            word in content.lower()
            for word in ['theorem', 'proof', 'lemma', 'proposition',
                        'démonstration', 'théorème', 'equation', 'function']
        ])

        if not (has_math or has_math_keywords):
            return False

        # Check alphanumeric ratio (avoid garbage)
        alphanum_ratio = sum(c.isalnum() or c.isspace() for c in content) / len(content)
        if alphanum_ratio < 0.6:
            return False

        return True
    
    def _detect_language(self, item: Dict) -> str:
        """Detect content language"""
        content = ''
        for field in ['question', 'answer', 'theorem', 'proof', 'title']:
            if field in item:
                content += ' ' + str(item[field])

        content_lower = content.lower()

        # Count French vs English keywords
        fr_count = sum(1 for word in self.PROOF_KEYWORDS['fr'] if word in content_lower)
        en_count = sum(1 for word in self.PROOF_KEYWORDS['en'] if word in content_lower)

        if fr_count > en_count:
            return 'fr'
        elif en_count > fr_count:
            return 'en'

        # Fallback to metadata
        if 'metadata' in item and 'language' in item['metadata']:
            return item['metadata']['language']

        return 'en'
    
    def _extract_proof_structure(self, item: Dict) -> Dict:
        """
        Extract proof structure.
        Identify: induction, contradiction, direct, etc.
        """
        structure = {
            'type': 'direct',
            'techniques': [],
            'steps': []
        }

        # Get proof text
        proof_text = ''
        if 'proof' in item:
            proof_text = item['proof']
        elif 'answer' in item:
            proof_text = item['answer']

        if not proof_text:
            return structure

        proof_lower = proof_text.lower()

        # Detect proof type
        if any(word in proof_lower for word in ['récurrence', 'induction', 'récurence']):
            structure['type'] = 'induction'
            structure['techniques'].append('mathematical_induction')

            # Look for base case and inductive step
            if 'cas de base' in proof_lower or 'base case' in proof_lower:
                structure['steps'].append('base_case')
            if 'hérédité' in proof_lower or 'inductive step' in proof_lower:
                structure['steps'].append('inductive_step')
        
        elif any(word in proof_lower for word in ['absurde', 'contradiction']):
            structure['type'] = 'contradiction'
            structure['techniques'].append('proof_by_contradiction')
        
        elif any(word in proof_lower for word in ['contraposée', 'contrapositive']):
            structure['type'] = 'contrapositive'
            structure['techniques'].append('contrapositive')

        # Additional techniques
        if any(word in proof_lower for word in ['factorisation', 'factor']):
            structure['techniques'].append('factorization')

        if any(word in proof_lower for word in ['substitution']):
            structure['techniques'].append('substitution')

        if any(word in proof_lower for word in ['intégration', 'integration', 'integr']):
            structure['techniques'].append('integration')

        if any(word in proof_lower for word in ['dérivée', 'derivative', 'deriv']):
            structure['techniques'].append('differentiation')

        return structure
    
    def normalize_for_lean(self, item: Dict) -> Dict:
        """
        Preprocess for future conversion to Lean.
        Standardize mathematical notations.
        """
        normalized = item.copy()

        # Normalize set notations
        replacements = {
            '∈': 'in',
            '∉': 'not_in',
            '∀': 'forall',
            '∃': 'exists',
            '→': 'implies',
            '⇒': 'implies',
            '⇔': 'iff',
            '∧': 'and',
            '∨': 'or',
            '¬': 'not',
        }
        
        for field in ['question', 'answer', 'theorem', 'proof']:
            if field in normalized:
                text = normalized[field]
                for symbol, replacement in replacements.items():
                    text = text.replace(symbol, f' {replacement} ')
                normalized[field] = text
        
        return normalized
