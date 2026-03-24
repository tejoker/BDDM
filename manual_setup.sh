#!/bin/bash
set -e
cd ~/DESol
export PATH=$HOME/.elan/bin:$PATH
mkdir -p .lake/packages
cd .lake/packages
if [ ! -d mathlib4 ]; then
  echo "[setup] Cloning Mathlib4..."
  git clone --depth 1 https://github.com/leanprover-community/mathlib4.git
fi
cd ~/DESol
echo "[setup] Building SDE modules..."
lake build
echo "[done] Build completed successfully"
