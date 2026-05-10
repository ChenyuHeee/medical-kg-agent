#!/usr/bin/env bash
# Start a Cloudflare Quick Tunnel for the local API on :8000.
# Cloudflare uses HTTPS (443) only, so it works behind firewalls that block
# localtunnel's random data ports.
set -u
cd "$(dirname "$0")/.."
mkdir -p data/logs
pkill -f 'cloudflared.*tunnel' 2>/dev/null || true
pkill -f 'localtunnel' 2>/dev/null || true
sleep 1
LOG=data/logs/cf.log
: > "$LOG"
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
nohup cloudflared tunnel --no-autoupdate --url http://localhost:8000 > "$LOG" 2>&1 &
PID=$!
echo "cf_pid=${PID}"
URL=""
for i in $(seq 1 30); do
  sleep 2
  URL=$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1)
  if [ -n "$URL" ]; then
    echo "TUNNEL_URL=${URL}"
    echo "$URL" > data/logs/cf_url.txt
    exit 0
  fi
done
echo "FAILED to obtain tunnel URL; tail of log:"
tail -30 "$LOG"
exit 1
