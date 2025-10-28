#!/bin/bash
# Installation automatique du Math Scraper

echo "🚀 Installation du Math Scraper"
echo "================================"
echo ""

# Vérifier Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 n'est pas installé"
    exit 1
fi

echo "✓ Python détecté: $(python3 --version)"

# Créer environnement virtuel
echo ""
echo "📦 Création de l'environnement virtuel..."
python3 -m venv math

# Activer environnement
if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "win32" ]]; then
    source math/Scripts/activate
else
    source math/bin/activate
fi

echo "✓ Environnement virtuel créé"

# Installer dépendances
echo ""
echo "📥 Installation des dépendances..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo "✓ Dépendances installées"

# Test rapide
echo ""
echo "🧪 Test rapide..."
python3 -c "
from scrapers.stackexchange_scraper import StackExchangeScraper
from utils.cleaner import DataCleaner
from utils.storage import DataStorage
print('✓ Tous les modules importés')
"

echo ""
echo "================================"
echo "✅ Installation terminée !"
echo ""
echo "Commandes disponibles:"
echo "  ./math/bin/python collect_samples.py 20 20 22 20 20 10  # Collect samples"
echo "  ./math/bin/python normalize_data.py                     # Normalize data"
echo "  ./math/bin/python analyze.py                            # Analyze data"
echo ""
echo "Documentation:"
echo "  - START_HERE.md              : Getting started guide"
echo "  - COLLECTION_UNIFIED.md      : Collection guide"
echo "  - FULL_COLLECTION_ESTIMATES.md : Storage/time estimates"
