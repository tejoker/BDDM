#!/bin/bash
set -e
cd ~/DESol
export PATH=$HOME/.elan/bin:$PATH
rm -rf .lake/packages lake-manifest.json
echo "[setup] Updating lake dependencies..."
lake update
echo "[setup] Building SDE modules..."
lake build
echo "[done] Build completed successfully"
