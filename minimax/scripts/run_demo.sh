#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHONPATH="${PYTHONPATH:-}:src" python -m minimax_context.cli demo "$@"
