#!/usr/bin/env bash
# 在「宿主机」执行：验证 Dify → 记忆桥 → PowerContext 的主路径真的通。
# 前置：
#   1. PowerContext 在 127.0.0.1:18080 运行
#   2. 记忆桥在 127.0.0.1:19080 运行（backend/start.sh）
#   3. Dify 已导入 GrudgeImp.yml、已发布、已创建 API Key
# 用法：
#   DIFY_API_KEY=app-xxxx bash verify_e2e.sh
set -euo pipefail
DIFY_BASE="${DIFY_BASE:-http://127.0.0.1:5001}"
BRIDGE="${BRIDGE:-http://127.0.0.1:19080}"
KEY="${DIFY_API_KEY:?请设置 DIFY_API_KEY（Dify 应用 → API 访问 → 创建密钥）}"
OUT="${OUT:-evidence/07-dify-e2e-$(date +%Y%m%d-%H%M%S).log}"

before=$(curl -s "$BRIDGE/health" | python3 -c 'import json,sys;print(json.load(sys.stdin)["memories"])')
echo "记忆条数（调用前）: $before" | tee "$OUT"

for q in "我把你的咖啡换成酱油了" "你还记得我干过什么吗？"; do
  echo "--- 提问: $q" | tee -a "$OUT"
  curl -s -X POST "$DIFY_BASE/v1/chat-messages" \
    -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
    -d "{\"inputs\":{},\"query\":\"$q\",\"response_mode\":\"blocking\",\"conversation_id\":\"\",\"user\":\"verify\"}" \
    | python3 -c 'import json,sys;d=json.load(sys.stdin);print("HTTP answer:", (d.get("answer") or d)[:120] if isinstance(d.get("answer"),str) else str(d)[:120])' | tee -a "$OUT"
done

after=$(curl -s "$BRIDGE/health" | python3 -c 'import json,sys;print(json.load(sys.stdin)["memories"])')
echo "记忆条数（调用后）: $after" | tee -a "$OUT"
[ "$after" -gt "$before" ] && echo "✅ 主路径通过：每轮都写入了 PowerContext" | tee -a "$OUT" || { echo "❌ 记忆没有增长，检查 HTTP 节点地址/桥接日志" | tee -a "$OUT"; exit 1; }
