#!/usr/bin/env bash
set -euo pipefail
LOSTFOUND_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export LOSTFOUND_ROOT
python3 "$LOSTFOUND_ROOT/scripts/init_env.py"
exec docker compose --project-name "${LOSTFOUND_PROJECT:-lostfound-dify}" \
  --project-directory "$LOSTFOUND_ROOT/vendor/dify/docker" \
  --env-file "$LOSTFOUND_ROOT/.env" \
  -f "$LOSTFOUND_ROOT/vendor/dify/docker/docker-compose.yaml" \
  -f "$LOSTFOUND_ROOT/deploy/docker-compose.yaml" \
  -f "$LOSTFOUND_ROOT/deploy/images.lock.yaml" "$@"
