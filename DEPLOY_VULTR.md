# Deploying Leado 2.0 on Vultr

This is the exact runbook for putting Leado 2.0 on a **Vultr Cloud Compute** instance.
The deploy script installs Docker, opens the firewall, generates a login token,
builds the container, and verifies health.

---

## 1. Launch the instance (Vultr dashboard)

| Setting | Value |
|---|---|
| Product | **Cloud Compute – Shared CPU** |
| Location | Closest to you (e.g. `Frankfurt`, `New Jersey`) |
| Image | **Ubuntu 24.04 LTS x64** |
| Plan | **1 vCPU / 2 GB RAM / 55 GB SSD** (~$10–12/mo) |
| SSH key | Add your `~/.ssh/id_ed25519.pub` |
| Auto backups | Optional |

> 1 GB RAM works, but the Docker build is more comfortable with 2 GB.

### Firewall

Create a **Firewall Group** and attach it to the instance, allowing inbound:

- `22/tcp` — SSH
- `80/tcp` — HTTP
- `443/tcp` — HTTPS (only needed if you add a domain/TLS later)

`deploy_vultr.sh` also configures `ufw` on the VM, but Vultr's cloud firewall is
separate and must allow port 80 too.

---

## 2. Copy the project to the VM

From your local machine, in the project root:

```bash
rsync -avz \
  --exclude .git \
  --exclude __pycache__ \
  --exclude graphify-out \
  ./ root@<VULTR_VM_IP>:/opt/leado/
```

(If the repo is pushed to GitHub, `git clone <repo> /opt/leado` works instead.)

---

## 3. Configure secrets (recommended before deploy)

On the VM:

```bash
cd /opt/leado
cp -n .env.example .env
nano .env
```

Set at minimum:

```dotenv
# Vultr Serverless Inference
VULTR_INFERENCE_API_KEY=your_vultr_inference_api_key_here
VULTR_MODEL=meta/llama-3.1-70b-instruct

# Login token — leave blank and the deploy script generates a random one
SOFTORASYSTEMS_TOKEN=
```

`OPENROUTER_API_KEY` is optional and used only as an LLM fallback.

---

## 4. Deploy

```bash
cd /opt/leado
chmod +x deploy_vultr.sh
./deploy_vultr.sh
```

The script re-execs itself with `sudo` and:

1. installs Docker Engine + Compose plugin,
2. opens `22/80/443` in `ufw`,
3. creates `.env` and a random `SOFTORASYSTEMS_TOKEN` if missing,
4. ensures the SQLite files exist (avoids Docker creating directories),
5. builds and starts the container on **port 80**,
6. waits for a healthy response and prints the URL + token.

Expected output:

```
🎉 DEPLOYMENT SUCCESSFUL ON VULTR!
App URL:      http://<VULTR_VM_IP>/
Access token: <generated token>
```

Open the URL and paste the token into the login screen.

---

## 5. Operations

```bash
cd /opt/leado

docker compose ps                 # status
docker compose logs -f --tail=100 # logs
docker compose restart            # restart
docker compose down               # stop

# Update after code changes
rsync ... ./ root@<IP>:/opt/leado/   # from your machine
ssh root@<IP> 'cd /opt/leado && docker compose up -d --build'
```

The SQLite databases (`leads.db`, `lead_finder.db`) are bind-mounted from the
project directory, so data survives container rebuilds. Back them up with:

```bash
sqlite3 /opt/leado/leads.db ".backup '/opt/leado/backups/leads-$(date +%F).db'"
```

---

## 6. Optional: HTTPS with a domain

Point an `A` record at the VM, then add a Caddy reverse proxy:

```yaml
# docker-compose.tls.yml
services:
  leado:
    ports: !reset []
  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports: ["80:80", "443:443"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
volumes:
  caddy_data:
```

```caddyfile
# Caddyfile
leado.example.com {
    reverse_proxy leado:8000
}
```

```bash
docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d --build
```

Caddy obtains a free Let's Encrypt certificate automatically.

---

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| URL times out | Allow `80/tcp` in the **Vultr firewall group**, not just `ufw`. |
| `port is already allocated` | Something else uses 80; run `APP_PORT=8080 ./deploy_vultr.sh`. |
| Login rejected | Read the token from `.env`; the deploy script prints it once. |
| App exits on start | `docker compose logs` — usually `SOFTORASYSTEMS_TOKEN` is under 16 chars. |
| Sandbox shows `jailed_process` | The host image `python:3.11-alpine` is still pulling; rerun after a minute. |
| 502 / LLM errors | Add `VULTR_INFERENCE_API_KEY` (or `OPENROUTER_API_KEY`) to `.env` and restart. |

---

## Security notes

- `.env` is git-ignored, excluded from the Docker build, and `chmod 600` on deploy.
- The access token is no longer embedded in the public `ui.html`; enter it at login.
- The app container mounts the Docker socket to run **Blast Radius Zero** audit
  containers (`--read-only`, `--cap-drop=ALL`, 256 MB / 0.5 CPU / non-root).
- This setup serves plain HTTP. Add the Caddy step above before sharing real
  credentials or customer data.
