#!/usr/bin/env bash
# Start localtunnel exposing local API (port 8000) to the public.
# Logs to data/logs/lt.log; URL is the first https:// line in that log.
set -e
cd "$(dirname "$0")/.."
mkdir -p data/logs
pkill -f 'localtunnel --port 8000' 2>/dev/null || true
SUB="${LT_SUBDOMAIN:-kg-agent-chenyu-510}"
nohup npx -y localtunnel --port 8000 --subdomain "$SUB" > data/logs/lt.log 2>&1 &
PID=$!
echo "tunnel_pid=$PID"
for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 2
  URL=$(grep -Eo 'https://[a-z0-9-]+\.loca\.lt' data/logs/lt.log | head -1)
  if [ -n "$URL" ]; then
    echo "TUNNEL_URL=$URL"
    exit 0
  fi
done
echo "FAILED to obtain tunnel URL; check data/logs/lt.log"
cat data/logs/lt.log
exit 1
