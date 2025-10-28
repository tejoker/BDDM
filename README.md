# BDDM - Math Dataset Builder# Math Data Scraper



Large-scale mathematics dataset collection tool for theorem-proof pairs, Q&A, and formal mathematics content.Système de scraping modulaire pour collecter des données mathématiques (théorèmes, preuves, exercices) depuis plusieurs sources.



🎯 **Goal**: Collect ~1.2M mathematical items from multiple high-quality sources  ## 🎯 Sources de données

📊 **Data**: Theorems, proofs, Q&A pairs, formal mathematics  

🚀 **Status**: Production-ready with round-robin optimization1. **Stack Exchange Mathematics** (~500k items potentiels)

   - Questions avec réponses acceptées

---   - Filtrage par score et tags

   - API officielle avec rate limiting

## 🚀 Quick Start

2. **ProofWiki** (~20k théorèmes)

### Installation   - Théorèmes formels avec preuves détaillées

   - Structure bien définie

```bash   - Excellent pour données structurées

git clone https://github.com/tejoker/BDDM.git

cd BDDM3. **Wikipedia** (Articles mathématiques)

./install.sh   - Encyclopédie accessible via API officielle

```   - Articles sur concepts, théorèmes, domaines mathématiques

   - Extraits d'introduction en texte plain

### Basic Usage

4. **nLab** (Catégorie theory & higher math)

```bash   - Wiki de mathématiques avancées

# Small test (180 items, ~5 minutes)   - Théorie des catégories, topologie algébrique

./math/bin/python collect_samples.py 50 30 22 20 50 5   - Définitions rigoureuses et formelles



# Medium collection (2,520 items, ~3-5 hours)5. **MathOverflow** (Recherche niveau)

./math/bin/python collect_samples.py 1000 500 22 200 500 50   - Questions/réponses de niveau recherche

   - Utilise l'API Stack Exchange

# Large collection (27,000 items, ~30-40 hours)   - Haute qualité, experts du domaine

./math/bin/python collect_samples.py 10000 5000 22 1000 5000 1000

```6. **arXiv** (Papiers de recherche - optionnel)

   - Métadonnées de papiers mathématiques

**Command format**: `SE PW Wiki nLab MO ArXiv_FULL`   - Titres, abstracts, auteurs

   - Catégories: algèbre, analyse, géométrie, etc.

---

**Note**: Sources comme MathWorld, Art of Problem Solving et MIT OCW sont implémentées mais peuvent être bloquées par protection anti-scraping.

## 📚 Data Sources

## 📦 Installation

| Source | Type | Items Available | Quality | Speed |

|--------|------|----------------|---------|-------|```bash

| **Stack Exchange** | Q&A | ~500,000 | 30-100/100 | Fast |# Cloner/télécharger le projet

| **ProofWiki** | Formal proofs | ~20,000 | 95/100 | Medium |cd math_scraper

| **Wikipedia** | Encyclopedia | ~22* | 85/100 | Very Fast |

| **nLab** | Advanced math | ~15,000 | 85/100 | Medium |# Créer environnement virtuel (recommandé)

| **MathOverflow** | Research Q&A | ~50,000 | 50-100/100 | Fast |python -m venv venv

| **ArXiv FULL** | Research proofs | ~500,000 | 90/100 | Slow |source venv/bin/activate  # Linux/Mac

# ou: venv\Scripts\activate  # Windows

*Wikipedia limited by hardcoded topics list (can be expanded)

# Installer dépendances

### Source Detailspip install -r requirements.txt

```

**Stack Exchange** - Undergraduate to graduate level Q&A

- Questions with accepted answers## 🚀 Utilisation rapide

- Score-filtered for quality

- Tags: proof-writing, logic, algebra, calculus, etc.### Collecter des échantillons (recommandé)



**ProofWiki** - Structured formal proofs```bash

- Theorem statement + complete proof# Collect 10 items from each main source

- Verified and peer-reviewed./math/bin/python collect_samples.py

- Categories: Set theory, algebra, analysis, topology

# Collect custom amounts: SE PW ArXiv Wiki nLab MathOverflow

**Wikipedia** - General math encyclopedia./math/bin/python collect_samples.py 20 15 0 10 5 10

- Definitions and explanations

- Accessible introductions# Example: 50 SE + 30 PW + 20 Wiki + 10 MathOverflow

- Can expand by adding topics to list./math/bin/python collect_samples.py 50 30 0 20 0 10

```

**nLab** - Category theory & higher mathematics

- Advanced topics: functors, monads, topoi, homotopy theoryLes données sont sauvegardées dans `samples_en/raw/<source>/`

- Rigorous definitions

- Graduate+ level content### Scraper toutes les sources (ancien mode)



**MathOverflow** - Research-level mathematics```python

- Expert Q&Apython main.py

- Advanced topics: algebraic geometry, number theory```

- Professional mathematician community

Par défaut, cela scrape Stack Exchange et ProofWiki avec 1000 items maximum par source.

**ArXiv FULL** - LaTeX source extraction

- Downloads full paper sources### Configuration personnalisée

- Extracts `\begin{theorem}...\begin{proof}` pairs

- ~5 proofs per paper average```python

- 2MB per paper (deleted after extraction)import asyncio

from main import MathDataScraper

---

async def custom_scrape():

## ⚡ Performance Optimization    scraper = MathDataScraper(output_dir="./mon_dataset")

    

**Round-Robin Strategy**: Instead of collecting all items from one source then moving to the next, the collector uses a round-robin approach:    # Choisir sources

    sources = ['stackexchange', 'proofwiki']

```    

ROUND 1: Fetch 80 from SE → 80 from MO → 50 from PW → ...    # Scraper sans limite (production)

ROUND 2: Fetch 80 more from SE → 80 more from MO → ...    await scraper.scrape_all(

```        sources=sources,

        max_per_source=None  # Pas de limite

**Benefits**:    )

- ~40% faster collection    

- Maximizes API usage during rate limit cooldowns    # Voir statistiques

- Never idle while waiting for limits to reset    print(scraper.get_summary())



---asyncio.run(custom_scrape())

```

## 📊 Dataset Estimates

### Tester un scraper individuellement

### Maximum Collection

- **Total items**: ~1,185,000```bash

- **Storage**: ~12-52 GB (JSON)# Test Stack Exchange

- **Time**: ~950 hours (~40 days continuous)python scrapers/stackexchange_scraper.py



### Recommended Collections# Test ProofWiki

python scrapers/proofwiki_scraper.py

**Phase 1: Quality Core (1-2 days)**

```bash# Test arXiv (plus lent)

./math/bin/python collect_samples.py 10000 5000 22 1000 5000 1000python scrapers/arxiv_scraper.py

``````

- ~25,000 items, ~2.5 GB

- High-quality diverse dataset## 📊 Structure des données



**Phase 2: Comprehensive (1 week)**Chaque item scrappé a la structure suivante:

```bash

# Run multiple times with different offsets```json

./math/bin/python collect_samples.py 50000 15000 22 5000 10000 10000{

```  "id": "unique_identifier",

- ~135,000 items, ~20 GB  "source": "stackexchange|proofwiki|arxiv|...",

- Substantial training corpus  "title": "Titre du problème/théorème",

  "question": "Énoncé (pour exercices)",

**Phase 3: Maximum (1-2 months)**  "answer": "Solution (pour exercices)",

- Requires batch processing and resume capability  "theorem": "Énoncé du théorème (pour preuves formelles)",

- Full 1.2M items  "proof": "Démonstration",

- See `FULL_COLLECTION_ESTIMATES.md` for details  "tags": ["algebra", "induction", ...],

  "url": "URL source",

---  "language": "en|fr",

  "proof_structure": {

## 🔑 API Keys & Rate Limits    "type": "induction|contradiction|direct",

    "techniques": ["mathematical_induction", ...],

### Stack Exchange / MathOverflow    "steps": ["base_case", "inductive_step"]

  },

**Without API key**: 300 requests/day    "metadata": {...}

**With API key**: 10,000 requests/day}

```

**Getting a key** (takes 5 minutes):

1. Go to: https://stackapps.com/apps/oauth/register## 📁 Organisation des fichiers

2. Fill in:

   - Application Name: Math Scraper```

   - Description: Educational math data collectionmath_dataset/

   - Application Website: https://github.com/tejoker/BDDM├── raw/                    # Données brutes par source

3. Copy your API key│   ├── stackexchange/

4. Set environment variable:│   │   └── batch_20241024_120000.json

   ```bash│   ├── proofwiki/

   echo "STACKEXCHANGE_API_KEY=your_key_here" > .env│   └── arxiv/

   ```├── processed/              # Données nettoyées

│   ├── train.jsonl

### Rate Limit Error (HTTP 429)│   ├── validation.jsonl

│   └── test.jsonl

If you see "Too many requests":├── index.json             # Index anti-doublons

1. **Wait 30-60 minutes** (temporary block)└── scraping_stats.json    # Statistiques

2. **Get an API key** (permanent solution)```

3. **Collect from other sources** while waiting:

   ```bash## 🔧 Post-processing

   # Skip SE/MO, collect from others:

   ./math/bin/python collect_samples.py 0 1000 22 1000 0 100### Fusionner tous les batches

   ```

```python

---from utils.storage import DataStorage



## 📁 Output Structurestorage = DataStorage("./math_dataset")

storage.merge_to_single_file("complete_dataset.json")

``````

samples_en/

├── raw/### Créer splits train/val/test

│   ├── stackexchange/

│   │   └── batch_*.json```python

│   ├── proofwiki/storage.export_by_format()

│   │   └── batch_*.json# Crée automatiquement train.jsonl, validation.jsonl, test.jsonl

│   ├── wikipedia/```

│   ├── nlab/

│   ├── mathoverflow/## ⚙️ Configuration avancée

│   └── arxiv_full/

└── index.json### Rate Limiting

```

Les scrapers respectent les limites:

### Data Format- Stack Exchange: 10k requêtes/jour (sans clé API)

- ProofWiki: 0.5s entre requêtes

Each item has:- arXiv: 3s entre requêtes

```json

{### Ajouter une clé API Stack Exchange

  "id": "se_12345",

  "source": "stackexchange",Pour augmenter les limites (30k requêtes/jour):

  "title": "Prove by induction that...",

  "question": "Full question text...",```python

  "answer": "Full answer with proof...",from scrapers.stackexchange_scraper import StackExchangeScraper

  "tags": ["induction", "proof-writing"],

  "score": 42,scraper = StackExchangeScraper(api_key="VOTRE_CLE")

  "url": "https://...",```

  "created_date": "2024-10-28T...",

  "metadata": {...}Obtenir une clé: https://stackapps.com/apps/oauth/register

}

```### Filtres de qualité



For formal proofs (ProofWiki, ArXiv FULL):Dans `utils/cleaner.py`, vous pouvez ajuster:

```json

{```python

  "id": "pw_12345",cleaner = DataCleaner(

  "source": "proofwiki",    min_length=50,    # Longueur minimale

  "title": "Pythagorean Theorem",    max_length=5000   # Longueur maximale

  "theorem": "Statement of theorem...",)

  "proof": "Complete formal proof...",```

  "categories": ["Geometry", "Algebra"],

  "url": "https://..."## 📈 Estimation de volumétrie

}

```Scraping complet (sans limite):

- Stack Exchange: ~500k items (≈2-3 heures)

---- ProofWiki: ~20k items (≈1 heure)

- arXiv: ~100k preuves (≈10-15 heures, nécessite beaucoup de bande passante)

## 🛠️ Advanced Usage

**Total estimé: 600k+ items mathématiques**

### Analyze Collection

## 🐛 Dépannage

```bash

./math/bin/python analyze.py### Erreur "Rate limit exceeded"

```Attendre ou utiliser une clé API.



Shows statistics, quality metrics, and collection summary.### Erreur réseau/timeout

Augmenter le timeout dans les scrapers:

### Production Scraping```python

async with session.get(url, timeout=60):

```bash    ...

./math/bin/python production_scraping.py```

```

### Mémoire insuffisante

Optimized for large-scale collection with error handling and resume capability.Réduire `max_per_source` ou traiter par batches:

```python

### Individual Source Testingawait scraper.scrape_all(max_per_source=10000)

```

```bash

# Test a specific scraper## 🔜 Prochaines étapes

./math/bin/python -c "import asyncio; from scrapers.proofwiki_scraper import ProofWikiScraper; asyncio.run(ProofWikiScraper().scrape(max_items=5))"

```Après le scraping, voici les étapes suivantes pour ton projet:



---1. **Nettoyage supplémentaire**: affiner les filtres de qualité

2. **Extraction de structure**: identifier patterns de preuve

## 🐛 Troubleshooting3. **Modèle de traduction**: fine-tune pour LaTeX → Lean

4. **Validation**: utiliser Lean compiler pour vérifier outputs

### "Too many requests" error

- **Cause**: Stack Exchange rate limit## 📝 Notes importantes

- **Solution**: Wait 1 hour OR get API key (see above)

- Les données proviennent de sources publiques mais vérifier les licences

### "No module named 'pandas'"- Stack Exchange: contenu sous CC BY-SA

- **Cause**: Missing dependencies- arXiv: accès libre mais respecter conditions d'utilisation

- **Solution**: `./math/bin/pip install -r requirements.txt`- ProofWiki: licence Creative Commons



### ArXiv downloads failing## 🤝 Contribution

- **Cause**: Network issues or ArXiv rate limiting

- **Solution**: Reduce batch size, add delays between requestsPour ajouter une nouvelle source:



### Out of memory1. Créer `scrapers/nouvelle_source_scraper.py`

- **Cause**: Too many items in memory2. Implémenter `async def scrape(self, max_items: int) -> List[Dict]`

- **Solution**: Reduce collection size, process in batches3. Ajouter dans `main.py` et `scrapers/__init__.py`



---## 📧 Support



## 📈 Quality MetricsPour questions ou bugs, créer une issue GitHub ou contacter directement.


**Highest Quality** (Recommended for training):
1. ProofWiki: 95/100 - Verified formal proofs
2. ArXiv FULL: 90/100 - Published research proofs
3. MathOverflow: 50-100/100 - Expert answers
4. Stack Exchange: 30-100/100 - Score-filtered

**Medium Quality**:
5. nLab: 85/100 - Advanced but sometimes informal
6. Wikipedia: 85/100 - General but reliable

---

## 🔜 Next Steps

After collection:
1. **Clean data**: Use `utils/cleaner.py` to remove duplicates
2. **Split datasets**: Train/validation/test splits
3. **Export formats**: JSONL, LaTeX, or custom format
4. **Train models**: Use for mathematical reasoning, proof generation, etc.

---

## 📖 Documentation

- **QUICKSTART.md** - Step-by-step guide for first-time users
- **ARCHITECTURE.md** - Code structure and technical details
- **FULL_COLLECTION_ESTIMATES.md** - Detailed dataset sizing and timing

---

## 📝 License & Attribution

Data sources have different licenses:
- **Stack Exchange/MathOverflow**: CC BY-SA 4.0
- **ProofWiki**: CC BY-SA 3.0
- **Wikipedia**: CC BY-SA 3.0
- **ArXiv**: Open access (respect terms of use)
- **nLab**: MIT License

Please respect licenses and provide proper attribution when using collected data.

---

## 🤝 Contributing

To add a new source:
1. Create `scrapers/new_source_scraper.py`
2. Implement `async def scrape(self, max_items: int) -> List[Dict]`
3. Add to `scrapers/__init__.py`
4. Update `collect_samples.py`

---

## 💡 Tips

**For fastest collection**: Get API keys and use round-robin (automatic)  
**For highest quality**: Focus on ProofWiki and ArXiv FULL  
**For largest volume**: Stack Exchange has 500k+ items  
**For research level**: MathOverflow and ArXiv FULL  

**Storage optimization**: Use gzip compression to reduce size by 70%

---

## 📧 Support

- **Issues**: https://github.com/tejoker/BDDM/issues
- **Repository**: https://github.com/tejoker/BDDM

---

**Built with ❤️ for mathematical AI research**
