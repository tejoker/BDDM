#!/usr/bin/env bash
# run_arxiv.sh — CLI wrapper for the arxiv → Lean 4 pipeline
#
# Usage:
#   ./scripts/run_arxiv.sh 2301.04567
#   ./scripts/run_arxiv.sh 2301.04567 --translate-only
#   ./scripts/run_arxiv.sh 2301.04567 --out output/my_paper.lean --repair-rounds 5
#   ./scripts/run_arxiv.sh 2301.04567 --max-theorems 10
#
# Environment variables:
#   MISTRAL_API_KEY   — required
#   MISTRAL_MODEL     — optional, defaults to labs-leanstral-2603
#
# The retrieval index at data/mathlib_embeddings is used automatically if present.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -z "${MISTRAL_API_KEY:-}" ]]; then
  if [[ -f "${PROJECT_ROOT}/.env" ]]; then
    # shellcheck disable=SC1091
    source "${PROJECT_ROOT}/.env"
  fi
fi

if [[ -z "${MISTRAL_API_KEY:-}" ]]; then
  echo "[fail] MISTRAL_API_KEY is not set" >&2
  exit 1
fi

RETRIEVAL_INDEX="${PROJECT_ROOT}/data/mathlib_embeddings"
RETRIEVAL_FLAG=""
if [[ -d "${RETRIEVAL_INDEX}" ]]; then
  RETRIEVAL_FLAG="--retrieval-index ${RETRIEVAL_INDEX}"
fi

cd "${PROJECT_ROOT}"

exec python3 scripts/arxiv_to_lean.py \
  --project-root "${PROJECT_ROOT}" \
  ${RETRIEVAL_FLAG} \
  "$@"
