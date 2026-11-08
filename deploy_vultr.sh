#!/usr/bin/env bash
# ==============================================================================
# 🚀 1-Click Deployment Script for a Vultr Cloud Compute instance
# Leado 2.0 × Vultr: Agent Rush Hackathon
#
# Usage (on the fresh Ubuntu VM, from the project directory):
#   chmod +x deploy_vultr.sh
#   ./deploy_vultr.sh            # re-execs itself with sudo
#
# Optional environment overrides:
#   APP_PORT=80 ./deploy_vultr.sh
# ==============================================================================
set -euo pipefail

# Re-exec as root so Docker + firewall setup is non-interactive and the
# freshly-installed Docker socket is usable without a re-login.
if [ "${EUID}" -ne 0 ]; then
  echo "Elevating with sudo..."
  exec sudo --preserve-env=APP_PORT bash "$0" "$@"
fi

APP_PORT="${APP_PORT:-80}"
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

# 5. Persistent SQLite files ---------------------------------------------------
# Bind-mounting a missing file makes Docker create a *directory* in its place.
touch leads.db lead_finder.db

# 6. Build + start -------------------------------------------------------------
echo "Building Leado 2.0 container..."
docker compose down --remove-orphans || true
APP_PORT="$APP_PORT" docker compose up -d --build

# 7. Health check --------------------------------------------------------------
echo "Waiting for the app to become healthy..."
HEALTHY=0
for _ in $(seq 1 60); do
  if curl -fsS "http://localhost:${APP_PORT}/" >/dev/null 2>&1; then
    HEALTHY=1
    break
  fi
  sleep 2
done

PUBLIC_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null \
  || hostname -I | awk '{print $1}')"
LOGIN_TOKEN="$(grep -E '^SOFTORASYSTEMS_TOKEN=' .env | head -n1 | cut -d= -f2- | tr -d "'\"")"

if [ "$HEALTHY" -eq 1 ]; then
  echo "=========================================================="
  echo "  🎉 DEPLOYMENT SUCCESSFUL ON VULTR!"
  echo "  App URL:      http://${PUBLIC_IP}:${APP_PORT}/"
  echo "  Access token: ${LOGIN_TOKEN}"
  echo "  (Token is stored in .env; keep that file private.)"
  echo "=========================================================="
else
  echo "⚠️  App did not answer on port ${APP_PORT}. Recent logs:"
  docker compose logs --tail=40
  exit 1
fi
