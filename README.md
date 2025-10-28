# Math Data Scraper

Système de scraping modulaire pour collecter des données mathématiques (théorèmes, preuves, exercices) depuis plusieurs sources.

## 🎯 Sources de données

1. **Stack Exchange Mathematics** (~500k items potentiels)
   - Questions avec réponses acceptées
   - Filtrage par score et tags
   - API officielle avec rate limiting

2. **ProofWiki** (~20k théorèmes)
   - Théorèmes formels avec preuves détaillées
   - Structure bien définie
   - Excellent pour données structurées

3. **Wikipedia** (Articles mathématiques)
   - Encyclopédie accessible via API officielle
   - Articles sur concepts, théorèmes, domaines mathématiques
   - Extraits d'introduction en texte plain

4. **nLab** (Catégorie theory & higher math)
   - Wiki de mathématiques avancées
   - Théorie des catégories, topologie algébrique
   - Définitions rigoureuses et formelles

5. **MathOverflow** (Recherche niveau)
   - Questions/réponses de niveau recherche
   - Utilise l'API Stack Exchange
   - Haute qualité, experts du domaine

6. **arXiv** (Papiers de recherche - optionnel)
   - Métadonnées de papiers mathématiques
   - Titres, abstracts, auteurs
   - Catégories: algèbre, analyse, géométrie, etc.

**Note**: Sources comme MathWorld, Art of Problem Solving et MIT OCW sont implémentées mais peuvent être bloquées par protection anti-scraping.

## 📦 Installation

```bash
# Cloner/télécharger le projet
cd math_scraper

# Créer environnement virtuel (recommandé)
python -m venv venv
source venv/bin/activate  # Linux/Mac
# ou: venv\Scripts\activate  # Windows

# Installer dépendances
pip install -r requirements.txt
```

## 🚀 Utilisation rapide

### Collecter des échantillons (recommandé)

```bash
# Collect 10 items from each main source
./math/bin/python collect_samples.py

# Collect custom amounts: SE PW ArXiv Wiki nLab MathOverflow
./math/bin/python collect_samples.py 20 15 0 10 5 10

# Example: 50 SE + 30 PW + 20 Wiki + 10 MathOverflow
./math/bin/python collect_samples.py 50 30 0 20 0 10
```

Les données sont sauvegardées dans `samples_en/raw/<source>/`

### Scraper toutes les sources (ancien mode)

```python
python main.py
```

Par défaut, cela scrape Stack Exchange et ProofWiki avec 1000 items maximum par source.

### Configuration personnalisée

```python
import asyncio
from main import MathDataScraper

async def custom_scrape():
    scraper = MathDataScraper(output_dir="./mon_dataset")
    
    # Choisir sources
    sources = ['stackexchange', 'proofwiki']
    
    # Scraper sans limite (production)
    await scraper.scrape_all(
        sources=sources,
        max_per_source=None  # Pas de limite
    )
    
    # Voir statistiques
    print(scraper.get_summary())

asyncio.run(custom_scrape())
```

### Tester un scraper individuellement

```bash
# Test Stack Exchange
python scrapers/stackexchange_scraper.py

# Test ProofWiki
python scrapers/proofwiki_scraper.py

# Test arXiv (plus lent)
python scrapers/arxiv_scraper.py
```

## 📊 Structure des données

Chaque item scrappé a la structure suivante:

```json
{
  "id": "unique_identifier",
  "source": "stackexchange|proofwiki|arxiv|...",
  "title": "Titre du problème/théorème",
  "question": "Énoncé (pour exercices)",
  "answer": "Solution (pour exercices)",
  "theorem": "Énoncé du théorème (pour preuves formelles)",
  "proof": "Démonstration",
  "tags": ["algebra", "induction", ...],
  "url": "URL source",
  "language": "en|fr",
  "proof_structure": {
    "type": "induction|contradiction|direct",
    "techniques": ["mathematical_induction", ...],
    "steps": ["base_case", "inductive_step"]
  },
  "metadata": {...}
}
```

## 📁 Organisation des fichiers

```
math_dataset/
├── raw/                    # Données brutes par source
│   ├── stackexchange/
│   │   └── batch_20241024_120000.json
│   ├── proofwiki/
│   └── arxiv/
├── processed/              # Données nettoyées
│   ├── train.jsonl
│   ├── validation.jsonl
│   └── test.jsonl
├── index.json             # Index anti-doublons
└── scraping_stats.json    # Statistiques
```

## 🔧 Post-processing

### Fusionner tous les batches

```python
from utils.storage import DataStorage

storage = DataStorage("./math_dataset")
storage.merge_to_single_file("complete_dataset.json")
```

### Créer splits train/val/test

```python
storage.export_by_format()
# Crée automatiquement train.jsonl, validation.jsonl, test.jsonl
```

## ⚙️ Configuration avancée

### Rate Limiting

Les scrapers respectent les limites:
- Stack Exchange: 10k requêtes/jour (sans clé API)
- ProofWiki: 0.5s entre requêtes
- arXiv: 3s entre requêtes

### Ajouter une clé API Stack Exchange

Pour augmenter les limites (30k requêtes/jour):

```python
from scrapers.stackexchange_scraper import StackExchangeScraper

scraper = StackExchangeScraper(api_key="VOTRE_CLE")
```

Obtenir une clé: https://stackapps.com/apps/oauth/register

### Filtres de qualité

Dans `utils/cleaner.py`, vous pouvez ajuster:

```python
cleaner = DataCleaner(
    min_length=50,    # Longueur minimale
    max_length=5000   # Longueur maximale
)
```

## 📈 Estimation de volumétrie

Scraping complet (sans limite):
- Stack Exchange: ~500k items (≈2-3 heures)
- ProofWiki: ~20k items (≈1 heure)
- arXiv: ~100k preuves (≈10-15 heures, nécessite beaucoup de bande passante)

**Total estimé: 600k+ items mathématiques**

## 🐛 Dépannage

### Erreur "Rate limit exceeded"
Attendre ou utiliser une clé API.

### Erreur réseau/timeout
Augmenter le timeout dans les scrapers:
```python
async with session.get(url, timeout=60):
    ...
```

### Mémoire insuffisante
Réduire `max_per_source` ou traiter par batches:
```python
await scraper.scrape_all(max_per_source=10000)
```

## 🔜 Prochaines étapes

Après le scraping, voici les étapes suivantes pour ton projet:

1. **Nettoyage supplémentaire**: affiner les filtres de qualité
2. **Extraction de structure**: identifier patterns de preuve
3. **Modèle de traduction**: fine-tune pour LaTeX → Lean
4. **Validation**: utiliser Lean compiler pour vérifier outputs

## 📝 Notes importantes

- Les données proviennent de sources publiques mais vérifier les licences
- Stack Exchange: contenu sous CC BY-SA
- arXiv: accès libre mais respecter conditions d'utilisation
- ProofWiki: licence Creative Commons

## 🤝 Contribution

Pour ajouter une nouvelle source:

1. Créer `scrapers/nouvelle_source_scraper.py`
2. Implémenter `async def scrape(self, max_items: int) -> List[Dict]`
3. Ajouter dans `main.py` et `scrapers/__init__.py`

## 📧 Support

Pour questions ou bugs, créer une issue GitHub ou contacter directement.
