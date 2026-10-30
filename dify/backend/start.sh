#!/usr/bin/env bash
# 启动记忆桥：先确认 PowerContext 在跑
set -euo pipefail
cd "$(dirname "$0")"
PC_BASE_URL="${PC_BASE_URL:-http://127.0.0.1:18080}"
curl -s --fail "$PC_BASE_URL/health/live" >/dev/null || {
  echo "PowerContext 未启动：$PC_BASE_URL"; echo "先运行：powercontext server run --host 127.0.0.1 --port 18080"; exit 1;
}
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install -q -r requirements.txt
export PC_BASE_URL
exec ./.venv/bin/python memory_bridge.py
