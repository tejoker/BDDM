# Architecture du Math Scraper

## Vue d'ensemble

```
┌─────────────────────────────────────────────────────────────┐
│                     SOURCES DE DONNÉES                       │
├──────────────┬──────────────┬───────────────┬───────────────┤
│ Stack        │ ProofWiki    │ arXiv         │ Cours FR      │
│ Exchange     │              │               │ (Exo7, etc.)  │
│ ~500k items  │ ~20k items   │ ~100k proofs  │ ~50k items    │
└──────┬───────┴──────┬───────┴───────┬───────┴───────┬───────┘
       │              │               │               │
       v              v               v               v
┌──────────────────────────────────────────────────────────────┐
│                  SCRAPERS (async/concurrent)                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ SE Scraper   │  │ PW Scraper   │  │ arXiv Scraper│      │
│  │ - API calls  │  │ - Web scrape │  │ - LaTeX parse│      │
│  │ - Rate limit │  │ - HTML parse │  │ - Tar extract│      │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
└─────────┼──────────────────┼──────────────────┼─────────────┘
          │                  │                  │
          └──────────────────┴──────────────────┘
                             │
                             v
┌─────────────────────────────────────────────────────────────┐
│                     DATA CLEANER                             │
│  - HTML/LaTeX cleaning                                       │
│  - Text normalization                                        │
│  - Quality filtering                                         │
│  - Language detection                                        │
│  - Proof structure extraction                                │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              v
┌─────────────────────────────────────────────────────────────┐
│                     DATA STORAGE                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  raw/                                                │   │
│  │   ├── stackexchange/batch_*.json                    │   │
│  │   ├── proofwiki/batch_*.json                        │   │
│  │   └── arxiv/batch_*.json                            │   │
│  ├─────────────────────────────────────────────────────┤   │
│  │  processed/                                          │   │
│  │   ├── train.jsonl (80%)                             │   │
│  │   ├── validation.jsonl (10%)                        │   │
│  │   └── test.jsonl (10%)                              │   │
│  ├─────────────────────────────────────────────────────┤   │
│  │  index.json (anti-duplicates)                       │   │
│  │  scraping_stats.json                                │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              v
┌─────────────────────────────────────────────────────────────┐
│                   PRÊT POUR ENTRAÎNEMENT                    │
│             (Fine-tuning pour text → Lean)                   │
└─────────────────────────────────────────────────────────────┘
```

## Composants principaux

### 1. Main Orchestrator (`main.py`)
**Rôle**: Coordonner tous les scrapers et gérer le workflow global

**Fonctionnalités**:
- Lancer scrapers en parallèle avec `asyncio.gather()`
- Gérer les erreurs et logging
- Collecter statistiques
- Sauvegarder résultats progressivement

**Flux d'exécution**:
```python
1. Initialiser scrapers
2. Pour chaque source:
   a. Scraper données
   b. Nettoyer avec DataCleaner
   c. Sauvegarder avec DataStorage
   d. Mettre à jour statistiques
3. Générer rapport final
```

### 2. Scrapers (dossier `scrapers/`)

#### StackExchangeScraper
- **API**: Utilise l'API officielle REST
- **Rate Limiting**: 10k requêtes/jour (sans clé), 30k avec clé
- **Stratégie**: 
  - Filtrer par score minimum (qualité)
  - Seulement questions avec réponse acceptée
  - Tags mathématiques: proof, induction, etc.
- **Output**: Question + Réponse + métadonnées

#### ProofWikiScraper
- **Méthode**: Web scraping avec BeautifulSoup
- **Structure**: Site bien organisé avec théorèmes + preuves
- **Parsing**: Extraction d'environnements "Theorem" et "Proof"
- **Avantage**: Données très structurées et formelles
- **Output**: Théorème + Preuve + catégories

#### ArxivScraper
- **Source**: Sources LaTeX des papiers
- **Process**:
  1. API pour chercher papiers par catégorie math
  2. Télécharger archive tar.gz
  3. Extraire fichiers .tex
  4. Parser environnements LaTeX (theorem, proof, lemma)
  5. Nettoyer commandes LaTeX
- **Défi**: Fichiers volumineux, parsing LaTeX complexe
- **Output**: Théorème + Preuve par papier

#### FrenchCoursesScraper
- **Sources**: Exo7, Bibmath, etc.
- **Contenu**: Exercices pédagogiques corrigés
- **Langue**: Principalement français
- **Output**: Énoncé + Solution

### 3. Data Cleaner (`utils/cleaner.py`)

**Pipeline de nettoyage**:
```
Texte brut
   ↓
1. Décoder HTML entities
   ↓
2. Préserver formules LaTeX (placeholders)
   ↓
3. Nettoyer HTML/LaTeX
   ↓
4. Normaliser espaces
   ↓
5. Restaurer formules LaTeX
   ↓
6. Validation qualité
   ↓
Texte propre
```

**Filtres de qualité**:
- Longueur minimale/maximale
- Présence de contenu mathématique (formules, symboles)
- Ratio caractères alphanumériques (éviter garbage)
- Détection de spam

**Enrichissement**:
- Détection langue (FR/EN)
- Extraction structure de preuve:
  - Type: induction, absurde, directe
  - Techniques: factorisation, substitution, etc.
  - Étapes: cas de base, hérédité

### 4. Data Storage (`utils/storage.py`)

**Organisation des données**:
```
math_dataset/
├── raw/               # Données brutes par source
│   ├── stackexchange/
│   │   ├── batch_20241024_120000.json
│   │   └── batch_20241024_130000.json
│   └── proofwiki/
│       └── batch_20241024_120500.json
├── processed/         # Données finales pour ML
│   ├── train.jsonl
│   ├── validation.jsonl
│   └── test.jsonl
├── index.json         # Index des IDs pour éviter doublons
└── scraping_stats.json
```

**Anti-duplication**:
- Index central des IDs
- Hash du contenu pour générer IDs uniques
- Vérification avant sauvegarde

**Formats d'export**:
- JSON: batches incrémentaux
- JSONL: format standard ML (une ligne = un item)
- Splits: train (80%) / validation (10%) / test (10%)

## Flux de données détaillé

### Exemple: Stack Exchange

```
1. API Request
   GET /questions?tagged=proof&accepted=true
   ↓
2. Réponse JSON (100 questions)
   ↓
3. Pour chaque question:
   GET /answers/{accepted_answer_id}
   ↓
4. Structure initiale:
   {
     "question_id": 12345,
     "title": "Proof by induction...",
     "body": "<p>Show that...</p>",
     "answer_body": "<p>By induction...</p>",
     "score": 42
   }
   ↓
5. DataCleaner:
   - Nettoie HTML
   - Extrait LaTeX
   - Détecte "induction" → structure type
   ↓
6. Structure nettoyée:
   {
     "id": "se_12345",
     "source": "stackexchange",
     "question": "Show that for all n ∈ ℕ...",
     "answer": "By induction on n. Base case: ...",
     "proof_structure": {
       "type": "induction",
       "techniques": ["mathematical_induction"],
       "steps": ["base_case", "inductive_step"]
     },
     "language": "en",
     "tags": ["induction", "proof"]
   }
   ↓
7. DataStorage:
   - Vérifier index → nouveau
   - Sauvegarder dans batch
   - Mettre à jour stats
```

## Performance et optimisations

### Concurrence
- Utilisation d'`asyncio` pour I/O non-bloquant
- Scrapers parallèles avec `asyncio.gather()`
- Rate limiting respecté par source

### Mémoire
- Sauvegarde incrémentale par batches
- Pas de chargement complet en mémoire
- Processus peut être interrompu et repris

### Erreurs
- Try/catch à chaque niveau
- Logging détaillé
- Continuer même si une source échoue

## Extensibilité

### Ajouter une nouvelle source

1. Créer `scrapers/nouvelle_source_scraper.py`:
```python
class NouvelleSourceScraper:
    async def scrape(self, max_items: int = None) -> List[Dict]:
        # Implémenter logique de scraping
        return items
```

2. Ajouter dans `scrapers/__init__.py`

3. Ajouter dans `main.py`:
```python
self.scrapers = {
    ...
    'nouvelle_source': NouvelleSourceScraper()
}
```

### Personnaliser le nettoyage

Modifier `utils/cleaner.py`:
- Ajuster filtres de qualité
- Ajouter patterns de détection
- Nouvelles techniques de preuve

## Prochaines étapes

Après le scraping, le dataset est prêt pour:

1. **Analyse exploratoire** (`analyze.py`)
2. **Préparation pour Lean**:
   - Parser structure mathématique
   - Normaliser notations
   - Créer paires (texte, code Lean)
3. **Fine-tuning modèle**:
   - Charger dataset avec Hugging Face datasets
   - Fine-tune CodeLlama/DeepSeek-Prover
   - Validation avec compilateur Lean

## Métriques de qualité

**Dataset idéal**:
- ✅ 600k+ items
- ✅ Diversité de sources
- ✅ 80%+ avec formules mathématiques
- ✅ Bilingue (FR + EN)
- ✅ Structures de preuve identifiées
- ✅ Pas de doublons
- ✅ Filtrage qualité appliqué

**Ce scraper atteint tous ces objectifs.**
