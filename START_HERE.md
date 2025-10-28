# 🎯 COMMENCEZ ICI

Bienvenue dans le **Math Data Scraper** - votre système de collecte de données mathématiques pour l'IA !

## ⚡ Installation (2 minutes)

### Sur Linux/Mac :
```bash
cd math_scraper
./install.sh
```

### Sur Windows :
```bash
cd math_scraper
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## 🚀 Premier test (3 minutes)

```bash
# Activer environnement (si pas déjà fait)
source venv/bin/activate  # Linux/Mac
# ou: venv\Scripts\activate  # Windows

# Test rapide
python test.py

# Premier scraping (collecte ~2000 items)
python main.py

# Voir les résultats
python analyze.py
```

## 📊 Ce que tu vas obtenir

Après le scraping, tu auras :

```
math_dataset/
├── raw/                    # Données brutes par source
│   ├── stackexchange/     # Questions + Réponses votées
│   └── proofwiki/         # Théorèmes + Preuves formelles
├── processed/             # Prêt pour ML
│   ├── train.jsonl       # 80% des données
│   ├── validation.jsonl  # 10% des données
│   └── test.jsonl        # 10% des données
└── index.json            # Anti-doublons
```

**Format des données** :
```json
{
  "id": "se_12345",
  "source": "stackexchange",
  "question": "Montrer que pour tout n ∈ ℕ, n² ≥ 0",
  "answer": "Par récurrence sur n. Cas de base: ...",
  "proof_structure": {
    "type": "induction",
    "techniques": ["mathematical_induction"],
    "steps": ["base_case", "inductive_step"]
  },
  "tags": ["induction", "number-theory"],
  "language": "fr"
}
```

## 📚 Sources de données

| Source | Volume | Qualité | Temps |
|--------|--------|---------|-------|
| **Stack Exchange** | ~500k | ⭐⭐⭐⭐ | 2-3h |
| **ProofWiki** | ~20k | ⭐⭐⭐⭐⭐ | 1h |
| **arXiv** | ~100k | ⭐⭐⭐⭐ | 10-15h |
| **Cours FR** | ~50k | ⭐⭐⭐⭐ | 2-3h |

**Total : 670k+ items**

## 🎮 Modes de scraping

### 1. Test rapide (5 minutes)
```bash
python main.py
```
→ 2,000 items pour tester

### 2. Échantillon (10 minutes)
```bash
python production_scraping.py sample
```
→ 5,000 items de qualité

### 3. Production complète (5-20 heures)
```bash
python production_scraping.py production
```
→ 600,000+ items complets

**💡 Astuce** : Tu peux interrompre avec Ctrl+C, les données collectées sont sauvegardées !

## 📖 Documentation complète

1. **QUICKSTART.md** - Guide rapide 5 minutes
2. **README.md** - Documentation détaillée
3. **ARCHITECTURE.md** - Explication technique du système

## 🔥 Prochaines étapes

Après avoir collecté les données :

### 1. Exploration
```bash
python analyze.py
```
Affiche statistiques, distribution, tags populaires, etc.

### 2. Préparation pour Lean
Tu auras besoin de :
- Parser la structure mathématique
- Créer des templates de conversion
- Utiliser le dataset pour fine-tuner ton modèle

### 3. Fine-tuning
Avec tes 4×A100, tu peux :
- Fine-tune **DeepSeek-Prover** (7B) ou **LLEMMA**
- Utiliser LoRA/QLoRA pour optimiser mémoire
- Entraîner sur paires (texte naturel → Lean)

**Fichier d'entraînement** : `math_dataset/processed/train.jsonl`

## 🛠️ Configuration avancée

### Filtrer par source
```python
# Dans main.py
sources_to_scrape = ['stackexchange']  # Seulement Stack Exchange
```

### Ajuster la qualité
```python
# Dans utils/cleaner.py
cleaner = DataCleaner(
    min_length=200,  # Plus strict
    max_length=3000
)
```

### Ajouter clé API Stack Exchange
```python
# Dans scrapers/stackexchange_scraper.py
scraper = StackExchangeScraper(api_key="VOTRE_CLE")
```
Obtenir clé : https://stackapps.com/apps/oauth/register

## ❓ Problèmes courants

### Erreur "Rate limit exceeded"
→ Attendre ou utiliser une clé API

### Scraping lent
→ Normal pour arXiv (gros fichiers LaTeX)
→ Utiliser mode échantillon d'abord

### Espace disque
→ Production complète = ~3 GB
→ Réduire `max_per_source` si nécessaire

## 📧 Support

Les logs détaillés sont dans `scraping.log`

## ✅ Checklist avant fine-tuning

- [ ] Au moins 10k items collectés
- [ ] Plus de 80% avec formules mathématiques  
- [ ] Fichiers train/val/test créés
- [ ] Pas d'erreurs dans `scraping.log`
- [ ] `analyze.py` montre bonne distribution

Si tous cochés : **Prêt pour l'entraînement !** 🎉

---

## 🚀 Commencer maintenant

```bash
# Installation
./install.sh  # ou suivre instructions Windows

# Premier test
python test.py

# Premier scraping
python main.py

# Analyse
python analyze.py
```

**Bonne collecte de données !** 📊🤖
