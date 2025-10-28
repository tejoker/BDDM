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
python3 -m venv venv

# Activer environnement
if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "win32" ]]; then
    source venv/Scripts/activate
else
    source venv/bin/activate
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
echo "  python main.py              # Scraper ~2k items (test)"
echo "  python test.py              # Tests unitaires"
echo "  python analyze.py           # Analyser données"
echo "  python production_scraping.py sample    # 5k items"
echo "  python production_scraping.py production # Production complète"
echo ""
echo "Documentation:"
echo "  - README.md       : Documentation complète"
echo "  - QUICKSTART.md   : Guide rapide 5 minutes"
echo "  - ARCHITECTURE.md : Explication technique"
