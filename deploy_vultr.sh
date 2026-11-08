#!/usr/bin/env bash
# ==============================================================================
# 🚀 1-Click Deployment Script for a Vultr Cloud Compute instance
# Leado 2.0 × Vultr: Agent Rush Hackathon
#
# Usage (on the VM, from the project directory):
#   chmod +x deploy_vultr.sh
#   ./deploy_vultr.sh            # re-execs itself with sudo
#
# Modes:
#   DOMAIN set in .env  -> Caddy + automatic HTTPS on https://$DOMAIN/
#   DOMAIN empty        -> plain HTTP on http://<vm-ip>:80/
#
# Optional environment overrides:
#   DOMAIN=leado.example.com APP_PORT=8080 ./deploy_vultr.sh
# ==============================================================================
set -euo pipefail

# Re-exec as root so Docker + firewall setup is non-interactive and the
# freshly-installed Docker socket is usable without a re-login.
if [ "${EUID}" -ne 0 ]; then
  echo "Elevating with sudo..."
  exec sudo --preserve-env=APP_PORT,DOMAIN,APP_BIND bash "$0" "$@"
fi

cd "$(dirname "$0")"

echo "=========================================================="
echo "  Deploying Leado 2.0 on Vultr Cloud Compute..."
echo "=========================================================="

# 1. Base packages -------------------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends curl git sqlite3 ca-certificates ufw openssl
apt-get upgrade -y

# 2. Docker + Compose plugin ---------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  echo "Installing Docker Engine + Compose plugin..."
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sh /tmp/get-docker.sh
  rm -f /tmp/get-docker.sh
fi
systemctl enable --now docker
docker compose version >/dev/null 2>&1 || {
  echo "ERROR: Docker Compose plugin is not available." >&2
  exit 1
}
# Pre-pull the image used by the Blast Radius Zero sandbox so the first
# containment check does not race the image download.
docker pull python:3.11-alpine >/dev/null 2>&1 || true

# 3. Firewall ------------------------------------------------------------------
ufw allow 22/tcp   || true
ufw allow 80/tcp   || true
ufw allow 443/tcp  || true
ufw --force enable || true

# 4. Environment / secrets -----------------------------------------------------
if [ ! -f .env ]; then
  echo "No .env found; creating one from .env.example..."
  cp .env.example .env
fi

TOKEN="$(grep -E '^SOFTORASYSTEMS_TOKEN=' .env | head -n1 | cut -d= -f2- \
  | sed -E "s/^['\"]//; s/['\"]$//")"
if [ -z "$TOKEN" ]; then
  TOKEN="$(openssl rand -hex 24)"
  echo "Generated a new SOFTORASYSTEMS_TOKEN in .env"
fi
# Re-write single-quoted: Compose interpolates `$` inside unquoted .env values,
# which would silently truncate tokens containing a dollar sign.
case "$TOKEN" in *"'"*) TOKEN="$(openssl rand -hex 24)" ;; esac
sed -i '/^SOFTORASYSTEMS_TOKEN=/d' .env
printf "SOFTORASYSTEMS_TOKEN='%s'\n" "$TOKEN" >> .env
chmod 600 .env

# Resolve the public hostname from the environment or .env.
DOMAIN="${DOMAIN:-$(grep -E '^DOMAIN=' .env 2>/dev/null | head -n1 | cut -d= -f2- | tr -d "'\"")}"

# 5. Persistent SQLite files ---------------------------------------------------
# Bind-mounting a missing file makes Docker create a *directory* in its place.
touch leads.db lead_finder.db

# 6. Select deployment mode ----------------------------------------------------
COMPOSE=(docker compose -f docker-compose.yml)
if [ -n "$DOMAIN" ]; then
  if [ ! -f Caddyfile ] || [ ! -f docker-compose.tls.yml ]; then
    echo "ERROR: DOMAIN is set but Caddyfile/docker-compose.tls.yml is missing." >&2
    exit 1
  fi
  COMPOSE+=(-f docker-compose.tls.yml)
  APP_BIND=127.0.0.1
  APP_PORT=8000
  HEALTH_PORT=8000
else
  APP_BIND="${APP_BIND:-0.0.0.0}"
  APP_PORT="${APP_PORT:-80}"
  HEALTH_PORT="$APP_PORT"
fi
export APP_BIND APP_PORT DOMAIN

# 7. Build + start -------------------------------------------------------------
echo "Building Leado 2.0 container..."
"${COMPOSE[@]}" down --remove-orphans || true
"${COMPOSE[@]}" up -d --build

# 8. Health check --------------------------------------------------------------
echo "Waiting for the app to become healthy..."
HEALTHY=0
for _ in $(seq 1 60); do
  if curl -fsS "http://localhost:${HEALTH_PORT}/" >/dev/null 2>&1; then
    HEALTHY=1
    break
  fi
  sleep 2
done

PUBLIC_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null \
  || hostname -I | awk '{print $1}')"
LOGIN_TOKEN="$(grep -E '^SOFTORASYSTEMS_TOKEN=' .env | head -n1 | cut -d= -f2- | tr -d "'\"")"

if [ "$HEALTHY" -ne 1 ]; then
  echo "⚠️  App did not answer on port ${HEALTH_PORT}. Recent logs:"
  "${COMPOSE[@]}" logs --tail=40
  exit 1
fi

if [ -n "$DOMAIN" ]; then
  URL="https://${DOMAIN}/"
  RESOLVED="$(getent hosts "$DOMAIN" 2>/dev/null | awk '{print $1}' | head -n1)"
  if [ "$RESOLVED" != "$PUBLIC_IP" ]; then
    echo "⚠️  DNS check: ${DOMAIN} resolves to '${RESOLVED:-nothing}', not ${PUBLIC_IP}."
    echo "    Add an A record: ${DOMAIN} -> ${PUBLIC_IP}."
    echo "    Caddy will obtain the TLS certificate automatically once DNS is correct."
  fi
else
  URL="http://${PUBLIC_IP}:${APP_PORT}/"
fi

echo "=========================================================="
echo "  🎉 DEPLOYMENT SUCCESSFUL ON VULTR!"
echo "  App URL:      ${URL}"
echo "  Access token: ${LOGIN_TOKEN}"
echo "  (Token is stored in .env; keep that file private.)"
echo "=========================================================="
