#!/usr/bin/env bash
# 启动 PowerContext Server（任务状态服务）。
#
# 用法:
#   bash scripts/start_powercontext.sh          # 前台启动
#   bash scripts/start_powercontext.sh --check  # 只做连通性检查
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${SDCTX_VENV:-$HERE/.venv}"
URL="${SDCTX_PC_URL:-http://127.0.0.1:18080}"
HOME_DIR="${SDCTX_PC_HOME:-$HERE/.sdctx/powercontext}"
ENV_FILE="$HOME_DIR/powercontext.env"

mkdir -p "$HOME_DIR"
cat > "$ENV_FILE" <<ENV
POWERCONTEXT_HOME=$HOME_DIR
POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1
POWERCONTEXT_SERVER_HTTP_PORT=${SDCTX_PC_PORT:-18080}
POWERCONTEXT_SERVER_ACCESS_MODE=disabled
POWERCONTEXT_SERVER_DASHBOARD_ENABLED=false
POWERCONTEXT_SERVER_DATABASE_KIND=sqlite
POWERCONTEXT_SERVER_LOGGING_LEVEL=INFO
ENV

if [ "${1:-}" = "--check" ]; then
  echo "==> 检查 PowerContext: $URL"
  curl --fail --silent --show-error "$URL/health/live" && echo
  exit 0
fi

echo "==> 启动 PowerContext Server ($URL)"
echo "==> 配置文件: $ENV_FILE"
exec "$VENV/bin/powercontext" server run --env-file "$ENV_FILE"
