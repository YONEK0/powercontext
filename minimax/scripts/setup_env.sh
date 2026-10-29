#!/usr/bin/env bash
# 安装依赖并准备本地运行环境。
#
# 用法:
#   bash scripts/setup_env.sh            # 在 minimax/ 下创建 .venv 并安装依赖
#   bash scripts/setup_env.sh /path/to/venv
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${1:-$HERE/.venv}"
PY="${PYTHON:-python3}"

echo "==> 使用解释器: $($PY --version)"
"$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip setuptools wheel >/dev/null

INDEX_ARGS=()
if [ -n "${PIP_INDEX_URL:-}" ]; then
  INDEX_ARGS=(--index-url "$PIP_INDEX_URL")
fi

"$VENV/bin/python" -m pip install "${INDEX_ARGS[@]}" -r "$HERE/requirements.txt"
echo "==> 依赖安装完成: $VENV"
echo "==> 后续请使用: export PATH=\"$VENV/bin:\$PATH\""
