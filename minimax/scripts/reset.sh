#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHONPATH="${PYTHONPATH:-}:src" python -m minimax_context.cli reset --yes
rm -f evidence/contexts/*.json evidence/tests/*.log evidence/minimax/*.json
echo "removed local seekdb/PowerContext data and generated evidence"
