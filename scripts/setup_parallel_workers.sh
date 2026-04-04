#!/usr/bin/env bash
# setup_parallel_workers.sh — Create N isolated DESol worker copies for parallel MCTS.
#
# Uses `cp -rl` (recursive hardlinks) so compiled Mathlib objects are NOT duplicated —
# only new writes (your theorem files) create actual copies.
# Each worker gets its own .lake/ directory, eliminating cache conflicts.
#
# Usage:
#   ./scripts/setup_parallel_workers.sh [N] [BASE_DIR]
#   N         number of workers (default: 4)
#   BASE_DIR  parent directory where worker copies are created (default: parent of repo)
#
# Examples:
#   ./scripts/setup_parallel_workers.sh 4
#   ./scripts/setup_parallel_workers.sh 2 /tmp/workers
#
# After setup, run workers like:
#   DESOL_FORCE_REPL_DOJO=1 python scripts/benchmark_minif2f.py \
#     --project-root ~/DESol_w1 --problem-offset 0   --n-problems 61 --out-dir output/mcts_w1 &
#   DESOL_FORCE_REPL_DOJO=1 python scripts/benchmark_minif2f.py \
#     --project-root ~/DESol_w2 --problem-offset 61  --n-problems 61 --out-dir output/mcts_w2 &
#   DESOL_FORCE_REPL_DOJO=1 python scripts/benchmark_minif2f.py \
#     --project-root ~/DESol_w3 --problem-offset 122 --n-problems 61 --out-dir output/mcts_w3 &
#   DESOL_FORCE_REPL_DOJO=1 python scripts/benchmark_minif2f.py \
#     --project-root ~/DESol_w4 --problem-offset 183 --n-problems 61 --out-dir output/mcts_w4 &
#   wait
#   python scripts/merge_worker_results.py output/mcts_w{1,2,3,4}

set -euo pipefail

N="${1:-4}"
BASE_DIR="${2:-$(dirname "$(realpath "$0")/../..")}"
REPO_DIR="$(realpath "$(dirname "$0")/..")"
REPO_NAME="$(basename "$REPO_DIR")"

echo "Setting up $N worker copies of $REPO_DIR in $BASE_DIR"
echo "Using hardlinks (cp -rl) — Mathlib objects shared, .lake dirs isolated"
echo ""

TOTAL_DISK_KB=0

for i in $(seq 1 "$N"); do
    WORKER_DIR="$BASE_DIR/${REPO_NAME}_w${i}"

    if [ -d "$WORKER_DIR" ]; then
        echo "Worker $i: $WORKER_DIR already exists — skipping (delete manually to recreate)"
        continue
    fi

    echo -n "Worker $i: copying to $WORKER_DIR ... "
    cp -rl "$REPO_DIR" "$WORKER_DIR"

    # Give each worker its own .lake cache dir (break the hardlink for the index files).
    if [ -d "$WORKER_DIR/.lake" ]; then
        # Only copy the non-Mathlib parts (small); Mathlib compiled objects stay hardlinked.
        find "$WORKER_DIR/.lake" -name "*.olean" -newer "$REPO_DIR/.lake" -exec cp --remove-destination {} {} \; 2>/dev/null || true
    fi

    DISK_KB=$(du -sk "$WORKER_DIR" 2>/dev/null | cut -f1)
    TOTAL_DISK_KB=$((TOTAL_DISK_KB + DISK_KB))
    echo "done (${DISK_KB} KB)"
done

echo ""
echo "Total disk used by workers: $((TOTAL_DISK_KB / 1024)) MB"
echo ""
echo "Verify no .lake conflicts:"
for i in $(seq 1 "$N"); do
    WORKER_DIR="$BASE_DIR/${REPO_NAME}_w${i}"
    if [ -d "$WORKER_DIR/.lake" ]; then
        INODE_REPO=$(stat -c '%i' "$REPO_DIR/.lake/build" 2>/dev/null || echo "?")
        INODE_W=$(stat -c '%i' "$WORKER_DIR/.lake/build" 2>/dev/null || echo "?")
        if [ "$INODE_REPO" = "$INODE_W" ] && [ "$INODE_REPO" != "?" ]; then
            echo "  Worker $i .lake/build: SHARED inode $INODE_REPO — WARNING: may conflict"
        else
            echo "  Worker $i .lake/build: separate inode — OK"
        fi
    fi
done

echo ""
echo "To run benchmark across $N workers (244 problems split evenly):"
CHUNK=$(( (244 + N - 1) / N ))
for i in $(seq 1 "$N"); do
    OFFSET=$(( (i - 1) * CHUNK ))
    WORKER_DIR="$BASE_DIR/${REPO_NAME}_w${i}"
    echo "  DESOL_FORCE_REPL_DOJO=1 python scripts/benchmark_minif2f.py \\"
    echo "    --project-root $WORKER_DIR \\"
    echo "    --problem-offset $OFFSET --n-problems $CHUNK \\"
    echo "    --split test --k 1 --workers 1 \\"
    echo "    --mode mcts-draft --mcts-iterations 15 \\"
    echo "    --model labs-leanstral-2603 \\"
    echo "    --retrieval-index data/mathlib_embeddings \\"
    echo "    --retrieval-top-k 12 --lean-timeout 120 \\"
    echo "    --out-dir output/mcts_244_w${i} \\"
    echo "    > output/mcts_244_w${i}.log 2>&1 &"
    echo ""
done
echo "  wait && python scripts/merge_worker_results.py \$(for i in \$(seq 1 $N); do echo output/mcts_244_w\$i; done)"
