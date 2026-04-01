#!/usr/bin/env bash
set -euo pipefail

# Wrapper for research CLI workflows.
python3 scripts/research.py "$@"
