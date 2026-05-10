#!/usr/bin/env bash
# One-shot: bring up Cloudflare quick tunnel, push the resulting URL into
# the GitHub Actions secret API_BASE, and trigger a Pages rebuild so judges
# can hit the public site without any token / port games.
#
# Requires:
#   - cloudflared installed (brew install cloudflared)
#   - gh CLI authenticated to ChenyuHeee/medical-kg-agent
#   - Local API already running on :8000
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="ChenyuHeee/medical-kg-agent"

bash scripts/start_cf.sh
URL=$(cat data/logs/cf_url.txt)
echo "Public URL: $URL"

# Sanity check
curl -sf --max-time 15 "$URL/healthz" >/dev/null && echo "healthz OK"

# Update GH secret + trigger rebuild
gh secret set API_BASE -b "$URL" --repo "$REPO" >/dev/null
echo "secret API_BASE updated"

# Force a redeploy by pushing an empty commit (workflow only triggers on push)
git commit --allow-empty -m "ci: redeploy with new tunnel URL [$(date +%H:%M)]" >/dev/null
git push origin main >/dev/null
echo "pages rebuild triggered: https://chenyuheee.github.io/medical-kg-agent/"
echo "(wait ~30s for the new build to finish)"
