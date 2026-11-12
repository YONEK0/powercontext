#!/usr/bin/env bash
# 一键定位「主玩法跑不通」的环节：宿主服务 → 容器→宿主网络 → Dify HTTP 节点 → 应用 API
# 用法：DIFY_API_KEY=app-xxx bash deploy/diagnose.sh
set -uo pipefail
PC="${PC_BASE_URL:-http://127.0.0.1:18080}"
BRIDGE="${BRIDGE:-http://127.0.0.1:19080}"
DIFY_BASE="${DIFY_BASE:-http://127.0.0.1}"
KEY="${DIFY_API_KEY:-}"

say() { printf '\n== %s\n' "$1"; }

say "1) PowerContext ($PC)"
curl -s -m 5 -o /dev/null -w "health/live -> %{http_code}\n" "$PC/health/live" || echo "❌ 连不上，先启动：powercontext server run --host 0.0.0.0 --port 18080"

say "2) 记忆桥 ($BRIDGE)"
curl -s -m 5 "$BRIDGE/health" || echo "❌ 连不上，先启动：cd backend && PC_BASE_URL=$PC python memory_bridge.py"

say "3) 容器 -> 宿主（关键：ssrf_proxy 也必须能解析 host.docker.internal）"
if command -v docker >/dev/null 2>&1; then
  for c in $(docker ps --format '{{.Names}}' | grep -E 'api|worker|ssrf' ); do
    printf '%-24s' "$c"
    docker exec "$c" python3 -c "import urllib.request;print(urllib.request.urlopen('$BRIDGE/health',timeout=5).status)" 2>/dev/null \
      || docker exec "$c" sh -c "wget -q -O- '$BRIDGE/health' >/dev/null && echo 200" 2>/dev/null \
      || echo "❌ 不可达"
  done
else
  echo "(本机没有 docker CLI，跳过)"
fi

say "4) Dify 应用 API"
if [ -n "$KEY" ]; then
  curl -s -o /tmp/_dify_resp.json -w "chat-messages -> %{http_code}\n" -X POST "$DIFY_BASE/v1/chat-messages" \
    -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
    -d '{"inputs":{},"query":"诊断测试","response_mode":"blocking","conversation_id":"","user":"diag"}'
  head -c 300 /tmp/_dify_resp.json; echo
else
  echo "(未提供 DIFY_API_KEY，跳过)"
fi

say "5) 检查项"
echo "- 是否用了 deploy/docker-compose.override.yaml（api/worker/ssrf_proxy 三处 host.docker.internal）"
echo "- DSL 两个 HTTP 节点是否为 http://host.docker.internal:19080/recall|remember"
echo "- 应用是否已发布、API Key 是否属于该应用"
echo "- Dify 1.x 的模型 provider 可能是 langgenius/openai/openai，需在应用里重选模型"
