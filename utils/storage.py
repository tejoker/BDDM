"""
Data Storage Utility
Gestion du stockage des données scrapées
"""

import json
import logging
from pathlib import Path
from typing import List, Dict
from datetime import datetime
import hashlib

logger = logging.getLogger(__name__)


class DataStorage:
    """Gestionnaire de stockage des données scrapées"""
    
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
        # Créer sous-dossiers par source
        self.raw_dir = self.base_dir / "raw"
        self.processed_dir = self.base_dir / "processed"
        
        self.raw_dir.mkdir(exist_ok=True)
        self.processed_dir.mkdir(exist_ok=True)
        
        # Index des IDs pour éviter doublons
        self.index_file = self.base_dir / "index.json"
        self.index = self._load_index()
    
    def _load_index(self) -> Dict:
        """Charger l'index des items déjà stockés"""
        if self.index_file.exists():
            with open(self.index_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {'items': {}, 'stats': {}}
    
    def _save_index(self):
        """Sauvegarder l'index"""
        with open(self.index_file, 'w', encoding='utf-8') as f:
            json.dump(self.index, f, indent=2, ensure_ascii=False)
    
    def save_batch(self, items: List[Dict], source: str):
        """
        Sauvegarder un batch d'items
        Évite les doublons automatiquement
        """
        if not items:
            return
        
        # Créer dossier source
        source_dir = self.raw_dir / source
        source_dir.mkdir(exist_ok=True)
        
        # Filtrer doublons
        new_items = []
        duplicates = 0
        
        for item in items:
            item_id = item.get('id', self._generate_id(item))
            
            if item_id not in self.index['items']:
                new_items.append(item)
                self.index['items'][item_id] = {
                    'source': source,
                    'added_at': datetime.now().isoformat()
                }
            else:
                duplicates += 1
        
        if not new_items:
            logger.info(f"{source}: {duplicates} doublons ignorés")
            return
        
        # Sauvegarder dans fichier JSON
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = source_dir / f"batch_{timestamp}.json"
        
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(new_items, f, indent=2, ensure_ascii=False)
        
        # Mettre à jour stats
        if source not in self.index['stats']:
            self.index['stats'][source] = {'count': 0, 'files': []}
        
        self.index['stats'][source]['count'] += len(new_items)
        self.index['stats'][source]['files'].append(str(filename))
        
        self._save_index()
        
        logger.info(f"✓ {source}: {len(new_items)} items sauvegardés ({duplicates} doublons)")
    
    def _generate_id(self, item: Dict) -> str:
        """Générer un ID unique pour un item"""
        # Utiliser hash du contenu principal
        content = str(item.get('question', '')) + str(item.get('theorem', ''))
        return hashlib.md5(content.encode()).hexdigest()[:16]

    def get_collected_ids(self, source: str = None) -> set:
        """
        Get set of all collected item IDs, optionally filtered by source

        Args:
            source: If provided, only return IDs from this source

        Returns:
            Set of item IDs that have already been collected
        """
        if source:
            return {
                item_id for item_id, info in self.index['items'].items()
                if info.get('source') == source
            }
        return set(self.index['items'].keys())
    
    def get_stats(self) -> Dict:
        """Obtenir statistiques de stockage"""
        return {
            'total_items': len(self.index['items']),
            'by_source': self.index['stats'],
            'index_file': str(self.index_file)
        }
    
    def merge_to_single_file(self, output_file: str = "merged_dataset.json"):
        """
        Fusionner tous les batches en un seul fichier
        Utile pour entraînement
        """
        all_items = []
        
        for source_dir in self.raw_dir.iterdir():
            if source_dir.is_dir():
                for batch_file in source_dir.glob("*.json"):
                    with open(batch_file, 'r', encoding='utf-8') as f:
                        items = json.load(f)
                        all_items.extend(items)
        
        output_path = self.base_dir / output_file
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(all_items, f, indent=2, ensure_ascii=False)
        
        logger.info(f"✓ Dataset fusionné: {len(all_items)} items dans {output_path}")
        return output_path
    
    def export_by_format(self):
        """
        Exporter dans différents formats
        - JSONL pour entraînement
        - CSV pour analyse
        """
        all_items = []
        
        for source_dir in self.raw_dir.iterdir():
            if source_dir.is_dir():
                for batch_file in source_dir.glob("*.json"):
                    with open(batch_file, 'r', encoding='utf-8') as f:
                        items = json.load(f)
                        all_items.extend(items)
        
        # JSONL (une ligne par item)
        jsonl_path = self.processed_dir / "dataset.jsonl"
        with open(jsonl_path, 'w', encoding='utf-8') as f:
            for item in all_items:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')
        
        logger.info(f"✓ Export JSONL: {jsonl_path}")
        
        # Split train/val/test
        self._create_splits(all_items)
    
    def _create_splits(self, items: List[Dict]):
        """Créer splits train/validation/test"""
        import random
        random.shuffle(items)
        
        total = len(items)
        train_size = int(0.8 * total)
        val_size = int(0.1 * total)
        
        splits = {
            'train': items[:train_size],
            'validation': items[train_size:train_size + val_size],
            'test': items[train_size + val_size:]
        }
        
        for split_name, split_items in splits.items():
            split_file = self.processed_dir / f"{split_name}.jsonl"
            with open(split_file, 'w', encoding='utf-8') as f:
                for item in split_items:
                    f.write(json.dumps(item, ensure_ascii=False) + '\n')
            
            logger.info(f"✓ Split {split_name}: {len(split_items)} items")
