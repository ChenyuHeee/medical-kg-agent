#!/usr/bin/env bash
# Public tunnel via Pinggy.io over SSH on port 443 (firewall-friendly).
# Free tier: random subdomain that rotates every ~60min, but works reliably
# in mainland-China networks where localtunnel data ports are blocked.
set -u
cd "$(dirname "$0")/.."
mkdir -p data/logs
pkill -f 'ssh.*pinggy' 2>/dev/null || true
sleep 1
LOG=data/logs/pinggy.log
: > "$LOG"
# -p 443: TLS-friendly port; -R 0:: ask remote for a random forward
# -o options disable host-key prompt and keep alive
nohup ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes \
  -p 443 -R 0:localhost:8000 a.pinggy.io \
  > "$LOG" 2>&1 &
PID=$!
echo "pinggy_pid=${PID}"
URL=""
for i in $(seq 1 30); do
  sleep 2
  URL=$(grep -Eo 'https://[a-z0-9.-]+\.pinggy[a-z.-]+' "$LOG" | head -1)
  if [ -n "$URL" ]; then
    echo "TUNNEL_URL=${URL}"
    echo "$URL" > data/logs/pinggy_url.txt
    exit 0
  fi
done
echo "FAILED to obtain pinggy URL; tail of log:"
tail -30 "$LOG"
exit 1
