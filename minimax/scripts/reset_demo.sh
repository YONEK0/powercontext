#!/usr/bin/env bash
# 把业务工程恢复到"修改前"的原始状态，并清空上下文模块的本地索引。
#
# 用法: bash scripts/reset_demo.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="$HERE/business_project"

echo "==> 恢复 business_project/schedule/__init__.py 到 schedule 1.2.2 原始版本"
cp "$PROJECT/upstream_reference/schedule__init__v1.2.2.py" "$PROJECT/schedule/__init__.py"

echo "==> 恢复 SPEC-003 规格到 v1"
cp "$PROJECT/spec_revisions/SPEC-003-job-error-isolation.v1.md" \
   "$PROJECT/specs/SPEC-003-job-error-isolation.md"

echo "==> 清空 seekdb 索引"
SDCTX_PROJECT_ROOT="$PROJECT" "${SDCTX_VENV:-$HERE/.venv}/bin/python" -m sdctx ingest --reset >/dev/null

echo "==> 完成。业务工程已回到修改前状态。"
