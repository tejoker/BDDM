#!/bin/bash
# Download script for all mathematical data dumps
# Run this script to download all datasets

set -e

DUMPS_DIR="data_dumps"
mkdir -p "$DUMPS_DIR"

echo "========================================="
echo "Mathematical Data Dumps Download Script"
echo "========================================="
echo ""
echo "This script will download ~100GB of data."
echo "Estimated time: 4-8 hours depending on connection"
echo ""
read -p "Continue? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]
then
    exit 1
fi

echo ""
echo "1/11: Downloading Wikipedia dump..."
echo "    Size: ~20GB compressed"
echo "    URL: https://dumps.wikimedia.org/enwiki/latest/"
cd "$DUMPS_DIR"
mkdir -p wikipedia
cd wikipedia
wget -c https://dumps.wikimedia.org/enwiki/latest/enwiki-latest-pages-articles.xml.bz2
cd ../..

echo ""
echo "2/11: Downloading Stack Exchange (Math) dump..."
echo "    Size: ~15GB"
echo "    URL: https://archive.org/download/stackexchange"
cd "$DUMPS_DIR"
mkdir -p stackexchange
cd stackexchange
wget -c https://archive.org/download/stackexchange/math.stackexchange.com.7z
7z x math.stackexchange.com.7z -o./math.stackexchange.com
cd ../..

echo ""
echo "3/11: Downloading MathOverflow dump..."
echo "    Size: ~3GB"
cd "$DUMPS_DIR"
mkdir -p mathoverflow
cd mathoverflow
wget -c https://archive.org/download/stackexchange/mathoverflow.net.7z
7z x mathoverflow.net.7z -o./mathoverflow.net
cd ../..

echo ""
echo "4/11: Downloading ArXiv metadata (Kaggle)..."
echo "    Note: Requires Kaggle CLI"
echo "    Size: ~3GB metadata + optional 1.1TB sources"
cd "$DUMPS_DIR"
mkdir -p arxiv
cd arxiv
if command -v kaggle &> /dev/null
then
    kaggle datasets download -d Cornell-University/arxiv
    unzip arxiv.zip
else
    echo "    Kaggle CLI not found."
    echo "    Install: ./math/bin/pip install kaggle"
    echo "    Or download manually from: https://www.kaggle.com/datasets/Cornell-University/arxiv"
fi
cd ../..

echo ""
echo "5/11: Downloading OEIS database..."
echo "    Size: ~200MB"
cd "$DUMPS_DIR"
mkdir -p oeis
cd oeis
wget -c https://oeis.org/stripped.gz
cd ../..

echo ""
echo "6/11: Cloning Lean Mathlib..."
echo "    Size: ~500MB"
cd "$DUMPS_DIR"
if [ ! -d "mathlib4" ]; then
    git clone --depth=1 https://github.com/leanprover-community/mathlib4.git
else
    echo "    Mathlib4 already exists, pulling latest..."
    cd mathlib4
    git pull
    cd ..
fi
cd ..

echo ""
echo "7/11: Downloading Metamath set.mm..."
echo "    Size: ~30MB"
cd "$DUMPS_DIR"
mkdir -p metamath
cd metamath
wget -c https://raw.githubusercontent.com/metamath/set.mm/develop/set.mm
cd ../..

echo ""
echo "8/11: Cloning Isabelle AFP..."
echo "    Size: ~1GB"
cd "$DUMPS_DIR"
if [ ! -d "isabelle-afp" ]; then
    git clone --depth=1 https://github.com/isabelle-prover/mirror-afp-devel.git isabelle-afp
else
    echo "    Isabelle AFP already exists, pulling latest..."
    cd isabelle-afp
    git pull
    cd ..
fi
cd ..

echo ""
echo "9/11: Cloning Coq standard library..."
echo "    Size: ~100MB"
cd "$DUMPS_DIR"
if [ ! -d "coq" ]; then
    git clone --depth=1 https://github.com/coq/coq.git
else
    echo "    Coq already exists, pulling latest..."
    cd coq
    git pull
    cd ..
fi
cd ..

echo ""
echo "10/11: Downloading Proof-Pile dataset..."
echo "    Note: Requires HuggingFace datasets library"
echo "    Size: ~8GB"
echo "    This will be downloaded on-demand by the parser"
if python3 -c "import datasets" 2>/dev/null; then
    echo "    datasets library found"
else
    echo "    Installing datasets library..."
    if [ -d "math/bin" ]; then
        ./math/bin/pip install datasets
    else
        echo "    Warning: Virtual environment not found at ./math/"
        echo "    Please run ./install.sh first, then run this script again"
        echo "    Or manually install: ./math/bin/pip install datasets"
    fi
fi

echo ""
echo "11/11: zbMATH Open (API-based, no download needed)"
echo "    Will be fetched via API during collection"

echo ""
echo "========================================="
echo "Download complete!"
echo "========================================="
echo ""
echo "Summary:"
echo "  - Wikipedia: $DUMPS_DIR/wikipedia/"
echo "  - Stack Exchange: $DUMPS_DIR/stackexchange/math.stackexchange.com/"
echo "  - MathOverflow: $DUMPS_DIR/mathoverflow/mathoverflow.net/"
echo "  - ArXiv: $DUMPS_DIR/arxiv/"
echo "  - OEIS: $DUMPS_DIR/oeis/"
echo "  - Lean Mathlib: $DUMPS_DIR/mathlib4/"
echo "  - Metamath: $DUMPS_DIR/metamath/"
echo "  - Isabelle AFP: $DUMPS_DIR/isabelle-afp/"
echo "  - Coq: $DUMPS_DIR/coq/"
echo "  - Proof-Pile: (automatic download)"
echo "  - zbMATH: (API-based)"
echo ""
echo "Next step: Run collect_dumps.py to parse the dumps"
echo ""
