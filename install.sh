#!/bin/bash
# BDDM v3.0 Installation Script

echo "BDDM - Mathematical Dataset Builder v3.0"
echo "==========================================="
echo ""

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 not found"
    echo "Please install Python 3.8 or higher"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
echo "Python detected: $PYTHON_VERSION"

# Check Python version >= 3.8
if ! python3 -c 'import sys; exit(0 if sys.version_info >= (3, 8) else 1)'; then
    echo "Error: Python 3.8+ required (found $PYTHON_VERSION)"
    exit 1
fi

# Create virtual environment
echo ""
echo "Creating virtual environment..."
python3 -m venv math

# Activate environment
if [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "win32" ]]; then
    source math/Scripts/activate
else
    source math/bin/activate
fi

echo "Virtual environment created"

# Upgrade pip
echo ""
echo "Upgrading pip..."
pip install -q --upgrade pip

# Install dependencies
echo ""
echo "Installing dependencies..."
pip install -q -r requirements.txt

echo "Dependencies installed"

# Check if 7zip is available (needed for Stack Exchange dumps)
echo ""
echo "Checking optional tools..."
if command -v 7z &> /dev/null || command -v 7za &> /dev/null; then
    echo "7zip detected (Stack Exchange dumps supported)"
else
    echo "Warning: 7zip not found (needed for Stack Exchange/MathOverflow dumps)"
    echo "  Install: sudo apt-get install p7zip-full (Linux)"
    echo "           brew install p7zip (macOS)"
fi

# Check if git is available (needed for Lean, Isabelle, Coq)
if command -v git &> /dev/null; then
    echo "Git detected (repository-based parsers supported)"
else
    echo "Warning: Git not found (needed for Lean, Isabelle, Coq parsers)"
fi

# Check if kaggle CLI is available (needed for ArXiv dataset)
if command -v kaggle &> /dev/null; then
    echo "Kaggle CLI detected (ArXiv dataset supported)"
else
    echo "Warning: Kaggle CLI not found (optional, needed for ArXiv Kaggle dataset)"
    echo "  Install: pip install kaggle"
fi

# Test imports
echo ""
echo "Testing imports..."
python3 -c "
import sys
errors = []

# Test core modules
try:
    from utils.cleaner import DataCleaner
    from utils.storage import DataStorage
except Exception as e:
    errors.append(f'Core utils: {e}')

# Test v3 parsers
try:
    from parsers.base_parser import BaseDumpParser
    from parsers.stackexchange_dump_parser import StackExchangeDumpParser
    from parsers.oeis_parser import OEISParser
except Exception as e:
    errors.append(f'Parsers: {e}')

# Test legacy scrapers (deprecated but still included)
try:
    from scrapers.stackexchange_scraper import StackExchangeScraper
except Exception as e:
    # Legacy scrapers are optional
    pass

if errors:
    print('Import errors:')
    for error in errors:
        print(f'   {error}')
    sys.exit(1)
else:
    print('All modules imported successfully')
"

if [ $? -ne 0 ]; then
    echo ""
    echo "Installation failed"
    exit 1
fi

echo ""
echo "==========================================="
echo "Installation complete!"
echo ""
echo "Quick Start:"
echo ""
echo "1. Download data dumps (one-time, 4-8 hours):"
echo "   ./download_dumps.sh"
echo ""
echo "2. Parse dumps (choose a preset):"
echo "   ./math/bin/python collect_dumps.py small    # ~10k items, 30 min"
echo "   ./math/bin/python collect_dumps.py medium   # ~50k items, 2 hours"
echo "   ./math/bin/python collect_dumps.py large    # ~200k items, 8 hours"
echo "   ./math/bin/python collect_dumps.py max      # ~1.66M items, 19 hours"
echo ""
echo "Documentation:"
echo "   - README.md                - Main documentation"
echo "   - DUMP_MIGRATION_GUIDE.md  - v2 to v3 migration guide"
echo "   - ARCHITECTURE.md          - Technical architecture"
echo "   - CHANGELOG.md             - Version history"
echo ""
echo "Legacy Web Scraping (deprecated, 120x slower):"
echo "   ./math/bin/python collect_samples.py 50 30 100 20 50 5 20"
echo ""
echo "Need help? Check the documentation or open an issue:"
echo "   https://github.com/tejoker/BDDM/issues"
echo ""
