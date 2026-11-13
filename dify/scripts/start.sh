#!/usr/bin/env bash
set -euo pipefail
LOSTFOUND_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if ! docker info >/dev/null 2>&1; then
  python3 "$LOSTFOUND_ROOT/scripts/start_docker_wsl.py"
fi
bash "$LOSTFOUND_ROOT/scripts/compose.sh" up -d --build --wait --wait-timeout 300
python3 "$LOSTFOUND_ROOT/scripts/bootstrap_app.py" --base-url "${DIFY_BASE_URL:-http://127.0.0.1:18081}"
python3 "$LOSTFOUND_ROOT/scripts/diagnose.py" --base-url "${DIFY_BASE_URL:-http://127.0.0.1:18081}" --bridge-url "${BRIDGE_BASE_URL:-http://127.0.0.1:19080}"
