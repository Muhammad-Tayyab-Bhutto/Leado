#!/usr/bin/env bash
# Records the submission video: fresh demo DB -> real PayPal sandbox flow -> demo/dealwright-demo.mp4
# Needs in .env: PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, DEMO_PAYPAL_BUYER_EMAIL, DEMO_PAYPAL_BUYER_PASSWORD, OPENROUTER_API_KEY
set -euo pipefail
cd "$(dirname "$0")/../.."
eval "$(python3 scripts/demo/env_exports.py .env)"
: "${DEMO_PAYPAL_BUYER_EMAIL:?set DEMO_PAYPAL_BUYER_EMAIL in .env}" "${DEMO_PAYPAL_BUYER_PASSWORD:?set DEMO_PAYPAL_BUYER_PASSWORD in .env}"
case "$DEMO_PAYPAL_BUYER_EMAIL" in *business.example.com) echo "DEMO_PAYPAL_BUYER_EMAIL is your sandbox BUSINESS (merchant) account. Use the PERSONAL account (…@personal.example.com)."; exit 1;; esac
python3 scripts/paypal_selfcheck.py
mkdir -p demo
python3 -u scripts/demo/serve_demo.py > demo/server.log 2>&1 & SRV=$!
trap 'kill $SRV 2>/dev/null || true' EXIT
for _ in $(seq 1 120); do grep -q "http://" demo/server.log 2>/dev/null && break; sleep 1; done
~/.local/share/product-demo-video/venv/bin/python ~/.claude/skills/product-demo-video/demo_recorder.py \
  scripts/demo/storyboard.json demo/dealwright-demo.mp4 "$@"
