#!/usr/bin/env bash
# Restart the local API with DEMO write-token guard enabled.
set -e
cd "$(dirname "$0")/.."
mkdir -p data/logs
lsof -ti:8000 | xargs -r kill -9 2>/dev/null || true
sleep 1
# shellcheck disable=SC1091
source .venv/bin/activate
set -a
[ -f .env.demo ] && . ./.env.demo
set +a
export MODELSCOPE_API_KEY="${MODELSCOPE_API_KEY:-sk-7daa73a823a94478b907e84995e77ec2}"
export MODELSCOPE_BASE_URL="${MODELSCOPE_BASE_URL:-https://api.deepseek.com/v1}"
export MODELSCOPE_MODEL="${MODELSCOPE_MODEL:-deepseek-chat}"
nohup uvicorn src.api.app:app --port 8000 > data/logs/api.log 2>&1 &
PID=$!
echo "api_pid=$PID"
sleep 3
if curl -sf localhost:8000/healthz; then
  echo
  echo "API is up. DEMO_WRITE_TOKEN=${DEMO_WRITE_TOKEN:0:6}…"
else
  echo "API failed to start; tail of log:"
  tail -30 data/logs/api.log
  exit 1
fi
