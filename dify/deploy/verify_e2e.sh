#!/usr/bin/env bash
# 验证「忘事管理局」主路径：存 → 查 → 兜底，并核对 PowerContext 记忆增长
# 用法：DIFY_BASE=http://127.0.0.1 DIFY_API_KEY=app-xxxx bash deploy/verify_e2e.sh
set -euo pipefail
DIFY_BASE="${DIFY_BASE:-http://127.0.0.1}"
BRIDGE="${BRIDGE:-http://127.0.0.1:19080}"
KEY="${DIFY_API_KEY:?请设置 DIFY_API_KEY}"
OUT="${OUT:-evidence/10-lostfound-$(date +%Y%m%d-%H%M%S).log}"

# 全容器模式下记忆在容器里，优先用 docker exec 读；否则读宿主机桥
cnt() {
  if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' 2>/dev/null | grep -q 'memory-bridge'; then
    docker exec "$(docker ps --format '{{.Names}}' | grep -m1 'memory-bridge')" \
      python -c "import urllib.request,json;print(json.loads(urllib.request.urlopen('http://127.0.0.1:19080/health',timeout=5).read())['memories'])"
  else
    curl -s "$BRIDGE/health" | python3 -c 'import json,sys;print(json.load(sys.stdin)["memories"])'
  fi
}
ask() {
  curl -s -X POST "$DIFY_BASE/v1/chat-messages" \
    -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
    -d "$(python3 -c 'import json,sys;print(json.dumps({"inputs":{},"query":sys.argv[1],"response_mode":"blocking","conversation_id":"","user":"verify"}, ensure_ascii=False))' "$1")" \
  | python3 -c 'import json,sys;d=json.load(sys.stdin);print((d.get("answer") or str(d))[:120])'
}

before=$(cnt); echo "记忆（前）: $before" | tee "$OUT"
echo "--- 存：备用钥匙在玄关第二个抽屉" | tee -a "$OUT"
ask "存：备用钥匙在玄关第二个抽屉" | tee -a "$OUT"
after=$(cnt); echo "记忆（后）: $after" | tee -a "$OUT"
echo "--- 查：钥匙在哪" | tee -a "$OUT"
ask "查：钥匙在哪" | tee -a "$OUT"
echo "--- 你好" | tee -a "$OUT"
ask "你好" | tee -a "$OUT"
[ "$after" -gt "$before" ] && echo "✅ 主路径通过：归档写入了 PowerContext" | tee -a "$OUT" \
  || { echo "❌ 记忆没有增长" | tee -a "$OUT"; exit 1; }
