"""
Analyse des données collectées
Génère des statistiques et insights sur le dataset
"""

import json
from pathlib import Path
from collections import Counter, defaultdict
import re


class DatasetAnalyzer:
    """Analyseur de dataset mathématique"""
    
    def __init__(self, data_dir: str = "./math_dataset"):
        self.data_dir = Path(data_dir)
        self.items = []
        self.load_data()
    
    def load_data(self):
        """Charger toutes les données"""
        raw_dir = self.data_dir / "raw"
        
        if not raw_dir.exists():
            print(f"⚠ Dossier {raw_dir} introuvable")
            return
        
        for source_dir in raw_dir.iterdir():
            if source_dir.is_dir():
                for batch_file in source_dir.glob("*.json"):
                    with open(batch_file, 'r', encoding='utf-8') as f:
                        batch_items = json.load(f)
                        self.items.extend(batch_items)
        
        print(f"✓ Chargé {len(self.items)} items")
    
    def analyze(self):
        """Analyse complète du dataset"""
        if not self.items:
            print("Aucune donnée à analyser")
            return
        
        print("\n" + "="*60)
        print("ANALYSE DU DATASET")
        print("="*60)
        
        self.basic_stats()
        self.source_distribution()
        self.language_distribution()
        self.tags_analysis()
        self.proof_structure_analysis()
        self.length_analysis()
        self.quality_metrics()
    
    def basic_stats(self):
        """Statistiques de base"""
        print(f"\n📊 STATISTIQUES GÉNÉRALES")
        print(f"  Total d'items: {len(self.items)}")
        
        # Compter items avec différents champs
        with_question = sum(1 for item in self.items if 'question' in item)
        with_answer = sum(1 for item in self.items if 'answer' in item)
        with_theorem = sum(1 for item in self.items if 'theorem' in item)
        with_proof = sum(1 for item in self.items if 'proof' in item)
        
        print(f"  Items avec question: {with_question}")
        print(f"  Items avec answer: {with_answer}")
        print(f"  Items avec theorem: {with_theorem}")
        print(f"  Items avec proof: {with_proof}")
    
    def source_distribution(self):
        """Distribution par source"""
        print(f"\n📦 DISTRIBUTION PAR SOURCE")
        
        sources = Counter(item['source'] for item in self.items)
        
        for source, count in sources.most_common():
            percentage = (count / len(self.items)) * 100
            print(f"  {source:20s}: {count:6d} items ({percentage:5.1f}%)")
    
    def language_distribution(self):
        """Distribution par langue"""
        print(f"\n🌍 DISTRIBUTION PAR LANGUE")
        
        languages = Counter(
            item.get('language', 'unknown') for item in self.items
        )
        
        for lang, count in languages.most_common():
            percentage = (count / len(self.items)) * 100
            print(f"  {lang:10s}: {count:6d} items ({percentage:5.1f}%)")
    
    def tags_analysis(self):
        """Analyse des tags/catégories"""
        print(f"\n🏷️  TOP 20 TAGS/CATÉGORIES")
        
        all_tags = []
        for item in self.items:
            tags = item.get('tags', [])
            all_tags.extend(tags)
        
        tag_counts = Counter(all_tags)
        
        for tag, count in tag_counts.most_common(20):
            print(f"  {tag:30s}: {count:5d}")
    
    def proof_structure_analysis(self):
        """Analyse des structures de preuve"""
        print(f"\n🔍 STRUCTURES DE PREUVE")
        
        proof_types = Counter()
        techniques = Counter()
        
        for item in self.items:
            structure = item.get('proof_structure', {})
            
            if structure:
                proof_type = structure.get('type', 'unknown')
                proof_types[proof_type] += 1
                
                item_techniques = structure.get('techniques', [])
                techniques.update(item_techniques)
        
        print(f"\n  Types de preuve:")
        for ptype, count in proof_types.most_common():
            percentage = (count / len(self.items)) * 100
            print(f"    {ptype:20s}: {count:5d} ({percentage:4.1f}%)")
        
        if techniques:
            print(f"\n  Techniques utilisées (top 10):")
            for tech, count in techniques.most_common(10):
                print(f"    {tech:30s}: {count:5d}")
    
    def length_analysis(self):
        """Analyse des longueurs de texte"""
        print(f"\n📏 ANALYSE DES LONGUEURS")
        
        lengths = {
            'question': [],
            'answer': [],
            'theorem': [],
            'proof': []
        }
        
        for item in self.items:
            for field in lengths.keys():
                if field in item:
                    lengths[field].append(len(item[field]))
        
        for field, values in lengths.items():
            if values:
                avg = sum(values) / len(values)
                min_len = min(values)
                max_len = max(values)
                median = sorted(values)[len(values)//2]
                
                print(f"\n  {field.capitalize()}:")
                print(f"    Moyenne: {avg:.0f} chars")
                print(f"    Médiane: {median:.0f} chars")
                print(f"    Min: {min_len}, Max: {max_len}")
    
    def quality_metrics(self):
        """Métriques de qualité"""
        print(f"\n✨ MÉTRIQUES DE QUALITÉ")
        
        # Présence de formules mathématiques
        with_math = sum(
            1 for item in self.items
            if self._has_math_content(item)
        )
        
        percentage = (with_math / len(self.items)) * 100
        print(f"  Items avec formules math: {with_math} ({percentage:.1f}%)")
        
        # Items complets (question + réponse OU théorème + preuve)
        complete = sum(
            1 for item in self.items
            if (('question' in item and 'answer' in item) or
                ('theorem' in item and 'proof' in item))
        )
        
        percentage = (complete / len(self.items)) * 100
        print(f"  Items complets: {complete} ({percentage:.1f}%)")
        
        # Scores moyens (si disponible)
        scores = [item.get('score', 0) for item in self.items if 'score' in item]
        if scores:
            avg_score = sum(scores) / len(scores)
            print(f"  Score moyen: {avg_score:.1f}")
    
    def _has_math_content(self, item):
        """Vérifier si un item contient des formules mathématiques"""
        text = ''
        for field in ['question', 'answer', 'theorem', 'proof']:
            if field in item:
                text += item[field]
        
        return any([
            '$' in text,
            '\\(' in text,
            '\\[' in text,
            any(symbol in text for symbol in ['∈', '∀', '∃', '→', '∑', '∫'])
        ])
    
    def export_summary(self, filename: str = "dataset_summary.json"):
        """Exporter résumé en JSON"""
        summary = {
            'total_items': len(self.items),
            'sources': dict(Counter(item['source'] for item in self.items)),
            'languages': dict(Counter(
                item.get('language', 'unknown') for item in self.items
            )),
            'tags': dict(Counter([
                tag for item in self.items
                for tag in item.get('tags', [])
            ]).most_common(50)),
            'proof_types': dict(Counter([
                item.get('proof_structure', {}).get('type', 'unknown')
                for item in self.items
            ])),
        }
        
        output_path = self.data_dir / filename
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        
        print(f"\n✓ Résumé exporté: {output_path}")
    
    def sample_items(self, n: int = 5):
        """Afficher quelques exemples"""
        print(f"\n📝 EXEMPLES D'ITEMS (n={n})")
        
        import random
        samples = random.sample(self.items, min(n, len(self.items)))
        
        for i, item in enumerate(samples, 1):
            print(f"\n--- Exemple {i} ---")
            print(f"Source: {item['source']}")
            print(f"ID: {item['id']}")
            
            if 'title' in item:
                print(f"Titre: {item['title'][:70]}...")
            
            if 'question' in item:
                print(f"Question: {item['question'][:100]}...")
            
            if 'theorem' in item:
                print(f"Théorème: {item['theorem'][:100]}...")
            
            if 'tags' in item:
                print(f"Tags: {', '.join(item['tags'][:5])}")


def main():
    """Point d'entrée"""
    analyzer = DatasetAnalyzer("./math_dataset")
    
    if not analyzer.items:
        print("\n⚠ Aucune donnée trouvée.")
        print("Exécutez d'abord: python main.py")
        return
    
    # Analyse complète
    analyzer.analyze()
    
    # Exemples
    analyzer.sample_items(n=3)
    
    # Export résumé
    analyzer.export_summary()


if __name__ == "__main__":
    main()
