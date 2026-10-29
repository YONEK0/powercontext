#!/usr/bin/env bash
# End-to-end validation run used to regenerate evidence/final.
#
#   bash scripts/validate.sh [--live]
#
# --live calls MiniMax; without it the recorded patch is replayed. The script
# starts from empty local state so the run is reproducible from a clean clone.
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="replay"
if [ "${1:-}" = "--live" ]; then MODE="live"; fi

export PYTHONPATH="${PYTHONPATH:-}:src"
PY="${MINIMAX_TEST_PYTHON:-python}"
CLI=(env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY "$PY" -m minimax_context.cli)

echo "==> 0. reset local state"
"$PY" -m minimax_context.cli reset --yes >/dev/null
rm -f evidence/contexts/*.json evidence/tests/*.log evidence/state/*.json
rm -rf evidence/minimax

echo "==> 1. restore the initial code"
cp evidence/before/app.py demo_app/app.py

echo "==> 2. init"
"${CLI[@]}" init

echo "==> 3. context (probes the candidate tests on the unmodified code)"
"${CLI[@]}" context > /tmp/validate-context.json

echo "==> 4. target test before the change (expected to fail)"
set +e
"${CLI[@]}" test --target > /tmp/validate-before.json
BEFORE_EXIT=$?
set -e
[ "$BEFORE_EXIT" -ne 0 ] || { echo "target test unexpectedly passed before the change"; exit 1; }
echo "    exit=$BEFORE_EXIT (expected non-zero)"

echo "==> 5. agent ($MODE)"
if [ "$MODE" = "live" ]; then
  "${CLI[@]}" agent --live --apply > /tmp/validate-agent.json
else
  "${CLI[@]}" agent --replay --apply > /tmp/validate-agent.json
fi

echo "==> 6. target + regression after the change (expected to pass)"
"${CLI[@]}" test --target --regression > /tmp/validate-after.json

echo "==> 7. context again, now that the run is finished"
# The snapshot collected here embeds the finished state (completed items,
# remaining todo and both test results) instead of a mid-run one.
"${CLI[@]}" context > /tmp/validate-context-final.json

echo "==> 8. state read by a fresh process"
"${CLI[@]}" status > /tmp/validate-status.json

echo "==> 8. collect evidence"
"$PY" scripts/collect_evidence.py

echo "==> 9. restore the initial code so the repository ships the before state"
cp evidence/before/app.py demo_app/app.py

echo
echo "Done. Evidence refreshed in evidence/final/."
