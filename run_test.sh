#!/bin/bash
set -e
cd ~/DESol
export PATH=$HOME/.elan/bin:$PATH
source .venv/bin/activate
echo "[pipeline] environment ready, running integration test..."
python scripts/hello_world_integration.py --project-root . --telemetry-file logs/server_pipeline_test.json
echo "[pipeline] test completed successfully"
