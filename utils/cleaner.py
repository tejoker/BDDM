"""
Data Cleaner
Nettoyage et normalisation des données mathématiques
"""

import re
import logging
from typing import Dict, Optional
from html import unescape

logger = logging.getLogger(__name__)


class DataCleaner:
    """Nettoyeur de données mathématiques"""
    
    # Patterns LaTeX courants à préserver
    LATEX_PATTERNS = [
        r'\$[^\$]+\$',  # Inline math
        r'\$\$[^\$]+\$\$',  # Display math
        r'\\[(.*?)\\]',  # Display math alt
        r'\\((.*?)\\)',  # Inline math alt
    ]
    
    # Mots-clés de structures de preuve
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
        Nettoyer et valider un item
        Retourne None si l'item ne passe pas les filtres de qualité
        """
        try:
            cleaned_item = item.copy()
            
            # Nettoyer chaque champ texte
            for field in ['question', 'answer', 'theorem', 'proof', 'title']:
                if field in cleaned_item:
                    cleaned_item[field] = self._clean_text(cleaned_item[field])
            
            # Validation qualité
            if not self._is_valid(cleaned_item):
                return None
            
            # Détecter langue
            cleaned_item['language'] = self._detect_language(cleaned_item)
            
            # Extraire structures de preuve
            cleaned_item['proof_structure'] = self._extract_proof_structure(cleaned_item)
            
            return cleaned_item
            
        except Exception as e:
            logger.warning(f"Erreur nettoyage: {e}")
            return None
    
    def _clean_text(self, text: str) -> str:
        """Nettoyer un texte mathématique"""
        if not text:
            return ''
        
        # Décoder HTML entities
        text = unescape(text)
        
        # Préserver formules LaTeX (marquer temporairement)
        latex_placeholders = {}
        for i, pattern in enumerate(self.LATEX_PATTERNS):
            for match in re.finditer(pattern, text, re.DOTALL):
                placeholder = f"__LATEX_{i}_{len(latex_placeholders)}__"
                latex_placeholders[placeholder] = match.group(0)
                text = text.replace(match.group(0), placeholder)
        
        # Nettoyer caractères spéciaux mais garder ponctuation math
        text = re.sub(r'\s+', ' ', text)  # Normaliser espaces
        text = re.sub(r'[\r\n]+', ' ', text)  # Supprimer sauts de ligne multiples
        
        # Enlever URLs
        text = re.sub(r'http[s]?://\S+', '', text)
        
        # Enlever tags HTML restants
        text = re.sub(r'<[^>]+>', '', text)
        
        # Nettoyer artefacts LaTeX sans formules
        text = re.sub(r'\\newline', ' ', text)
        text = re.sub(r'\\\\', ' ', text)
        
        # Restaurer formules LaTeX
        for placeholder, original in latex_placeholders.items():
            text = text.replace(placeholder, original)
        
        # Nettoyer espaces finaux
        text = ' '.join(text.split())
        
        return text.strip()
    
    def _is_valid(self, item: Dict) -> bool:
        """
        Valider la qualité d'un item
        Critères:
        - Longueur minimale/maximale
        - Présence de contenu mathématique
        - Pas de spam/garbage
        """
        # Récupérer contenu principal
        content = ''
        if 'question' in item and 'answer' in item:
            content = item['question'] + ' ' + item['answer']
        elif 'theorem' in item and 'proof' in item:
            content = item['theorem'] + ' ' + item['proof']
        else:
            # Pas de structure reconnue
            return False
        
        # Vérifier longueur
        if len(content) < self.min_length:
            return False
        
        if len(content) > self.max_length:
            # Tronquer plutôt que rejeter
            logger.debug(f"Item tronqué: {len(content)} chars")
        
        # Vérifier présence de contenu mathématique
        has_math = any([
            '$' in content,
            '\\(' in content,
            '\\[' in content,
            any(symbol in content for symbol in ['∈', '∀', '∃', '→', '≤', '≥', '∑', '∫'])
        ])
        
        # Ou mots-clés mathématiques
        has_math_keywords = any([
            word in content.lower() 
            for word in ['theorem', 'proof', 'lemma', 'proposition', 
                        'démonstration', 'théorème', 'equation', 'function']
        ])
        
        if not (has_math or has_math_keywords):
            return False
        
        # Vérifier ratio caractères alphanumériques (éviter garbage)
        alphanum_ratio = sum(c.isalnum() or c.isspace() for c in content) / len(content)
        if alphanum_ratio < 0.6:
            return False
        
        return True
    
    def _detect_language(self, item: Dict) -> str:
        """Détecter la langue du contenu"""
        content = ''
        for field in ['question', 'answer', 'theorem', 'proof', 'title']:
            if field in item:
                content += ' ' + str(item[field])
        
        content_lower = content.lower()
        
        # Compter mots-clés français vs anglais
        fr_count = sum(1 for word in self.PROOF_KEYWORDS['fr'] if word in content_lower)
        en_count = sum(1 for word in self.PROOF_KEYWORDS['en'] if word in content_lower)
        
        if fr_count > en_count:
            return 'fr'
        elif en_count > fr_count:
            return 'en'
        
        # Fallback sur métadonnées
        if 'metadata' in item and 'language' in item['metadata']:
            return item['metadata']['language']
        
        return 'en'  # Par défaut
    
    def _extract_proof_structure(self, item: Dict) -> Dict:
        """
        Extraire la structure de la preuve
        Identifier: récurrence, absurde, directe, etc.
        """
        structure = {
            'type': 'direct',
            'techniques': [],
            'steps': []
        }
        
        # Récupérer texte de la preuve
        proof_text = ''
        if 'proof' in item:
            proof_text = item['proof']
        elif 'answer' in item:
            proof_text = item['answer']
        
        if not proof_text:
            return structure
        
        proof_lower = proof_text.lower()
        
        # Détecter type de preuve
        if any(word in proof_lower for word in ['récurrence', 'induction', 'récurence']):
            structure['type'] = 'induction'
            structure['techniques'].append('mathematical_induction')
            
            # Chercher cas de base et hérédité
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
        
        # Techniques additionnelles
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
        Pré-traiter pour future conversion en Lean
        Standardiser notations mathématiques
        """
        normalized = item.copy()
        
        # Normaliser notations ensemblistes
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
