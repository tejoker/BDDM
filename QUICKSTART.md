# Guide de Démarrage Rapide

## ⚡ Quick Start (5 minutes)

### 1. Installation

```bash
# Cloner le projet
cd math_scraper

# Créer environnement virtuel
python -m venv venv
source venv/bin/activate  # Linux/Mac
# OU: venv\Scripts\activate  # Windows

# Installer dépendances
pip install -r requirements.txt
```

### 2. Test rapide

```bash
# Tester que tout fonctionne
python test.py
```

Cela va:
- Tester chaque scraper individuellement
- Vérifier le nettoyage des données
- Tester le système de stockage
- Devrait prendre ~2-3 minutes

### 3. Premier scraping (échantillon)

```bash
# Collecter ~1000 items (2-3 minutes)
python main.py
```

Cela va créer:
```
math_dataset/
├── raw/
│   ├── stackexchange/batch_*.json
│   └── proofwiki/batch_*.json
└── index.json
```

### 4. Analyser les données

```bash
python analyze.py
```

Affiche:
- Nombre total d'items
- Distribution par source
- Tags populaires
- Structures de preuve
- Exemples

## 🎯 Workflow complet

### Phase 1: Collecte (échantillon test)

```bash
python main.py
```

**Résultat attendu**: ~2000 items en 5 minutes

### Phase 2: Analyse

```bash
python analyze.py
```

**Vérifier**:
- ✅ Items avec formules mathématiques > 80%
- ✅ Items complets (question+réponse) > 90%
- ✅ Diversité des tags
- ✅ Pas d'erreurs dans les logs

### Phase 3: Production (collecte complète)

```bash
# Échantillon rapide (5k items, ~10 min)
python production_scraping.py sample

# OU production complète (700k items, plusieurs heures)
python production_scraping.py production
```

**Important**: Le scraping production peut être interrompu avec Ctrl+C. Les données déjà collectées seront conservées.

### Phase 4: Vérification finale

```bash
# Analyser dataset complet
python analyze.py

# Vérifier les fichiers
ls -lh math_dataset/processed/
# Devrait afficher:
# - train.jsonl
# - validation.jsonl
# - test.jsonl
```

## 📊 Résultats attendus

### Échantillon (main.py par défaut)

```
Source              Items
----------------------------------
Stack Exchange      1,000
ProofWiki           1,000
----------------------------------
TOTAL               2,000
```

**Temps**: 5 minutes  
**Taille**: ~10 MB

### Production sample

```
Source              Items
----------------------------------
Stack Exchange      2,500
ProofWiki           2,500
----------------------------------
TOTAL               5,000
```

**Temps**: 10 minutes  
**Taille**: ~25 MB

### Production complète

```
Source              Items
----------------------------------
Stack Exchange      500,000
ProofWiki           20,000
arXiv               50,000
Cours français      30,000
----------------------------------
TOTAL               600,000+
```

**Temps**: 5-20 heures (selon connexion)  
**Taille**: ~2-3 GB

## 🔧 Personnalisation rapide

### Modifier les sources

Dans `main.py`:

```python
# Ne scraper que Stack Exchange
sources_to_scrape = ['stackexchange']

# Ou seulement sources françaises
sources_to_scrape = ['proofwiki', 'french_courses']
```

### Ajuster la quantité

```python
# Plus de données
await scraper.scrape_all(
    sources=sources_to_scrape,
    max_per_source=10000
)

# Moins de données (test rapide)
await scraper.scrape_all(
    sources=sources_to_scrape,
    max_per_source=100
)
```

### Filtrer par qualité

Dans `utils/cleaner.py`:

```python
# Plus strict (seulement contenu de très haute qualité)
cleaner = DataCleaner(
    min_length=200,  # Au lieu de 50
    max_length=3000  # Au lieu de 5000
)

# Moins strict (garder plus de contenu)
cleaner = DataCleaner(
    min_length=30,
    max_length=10000
)
```

## 🐛 Résolution de problèmes

### Erreur "No module named 'aiohttp'"

```bash
pip install -r requirements.txt
```

### Erreur "Rate limit exceeded" (Stack Exchange)

**Solution 1**: Attendre (quota se recharge)

**Solution 2**: Utiliser une clé API

```python
# Dans scrapers/stackexchange_scraper.py
scraper = StackExchangeScraper(api_key="VOTRE_CLE")
```

Obtenir clé: https://stackapps.com/apps/oauth/register

### Scraping très lent

**Causes possibles**:
- Connexion internet lente
- Rate limiting
- Serveur source surchargé

**Solutions**:
- Utiliser mode échantillon d'abord
- Lancer pendant la nuit
- Réduire `max_per_source`

### Espace disque insuffisant

Production complète nécessite ~3 GB.

**Solution**: Libérer espace ou réduire quantité:

```python
max_per_source=50000  # Au lieu de None
```

## 📚 Exemples de commandes

### Scraping progressif

```bash
# Jour 1: Stack Exchange
python -c "
import asyncio
from main import MathDataScraper

async def run():
    s = MathDataScraper('./dataset')
    await s.scrape_source('stackexchange', max_items=100000)

asyncio.run(run())
"

# Jour 2: ProofWiki
python -c "
import asyncio
from main import MathDataScraper

async def run():
    s = MathDataScraper('./dataset')  # Même dossier!
    await s.scrape_source('proofwiki', max_items=20000)

asyncio.run(run())
"
```

### Exporter données pour analyse

```bash
# Format CSV (pour Excel/Pandas)
python -c "
import json
import csv
from pathlib import Path

# Charger données
items = []
for f in Path('./math_dataset/raw').rglob('*.json'):
    items.extend(json.load(open(f)))

# Exporter CSV
with open('dataset.csv', 'w', encoding='utf-8') as f:
    if items:
        writer = csv.DictWriter(f, fieldnames=items[0].keys())
        writer.writeheader()
        writer.writerows(items)

print(f'Exporté {len(items)} items vers dataset.csv')
"
```

### Fusionner avec dataset existant

```python
from utils.storage import DataStorage

# Charger ancien dataset
old_storage = DataStorage('./old_dataset')

# Charger nouveau
new_storage = DataStorage('./new_dataset')

# Fusionner (anti-doublons automatique)
# Les IDs sont uniques, donc pas de doublons
```

## 🎓 Prochaines étapes

Après avoir collecté les données:

1. **Exploration**: 
   ```bash
   python analyze.py
   jupyter notebook  # Si Jupyter installé
   ```

2. **Préparation pour Lean**:
   - Parser structure mathématique
   - Créer templates de conversion
   - Identifier patterns communs

3. **Fine-tuning**:
   - Charger avec Hugging Face `datasets`
   - Fine-tune CodeLlama/DeepSeek
   - Valider avec Lean compiler

4. **Itération**:
   - Analyser erreurs du modèle
   - Collecter plus de données ciblées
   - Affiner filtres de qualité

## 💡 Astuces

- **Logs détaillés**: Vérifier `scraping.log` pour diagnostiquer problèmes
- **Interruption sûre**: Ctrl+C sauvegarde les données déjà collectées
- **Incrémental**: Peut lancer plusieurs fois, les doublons sont filtrés
- **Parallèle**: Les scrapers tournent en parallèle (plus rapide)

## ✅ Checklist de réussite

Avant de passer au fine-tuning, vérifier:

- [ ] Au moins 10k items collectés
- [ ] Plus de 80% avec formules mathématiques
- [ ] Au moins 3 sources différentes
- [ ] Fichiers train/val/test créés
- [ ] Pas d'erreurs critiques dans logs
- [ ] `analyze.py` montre bonne distribution

Si tous les points sont cochés: **Prêt pour l'entraînement!** 🎉
