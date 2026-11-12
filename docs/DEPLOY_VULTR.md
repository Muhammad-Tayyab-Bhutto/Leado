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

# Public hostname — enables Caddy + automatic HTTPS
DOMAIN=leado.example.com

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
5. builds and starts the app,
6. if `DOMAIN` is set, starts **Caddy** on 80/443 and obtains a Let's Encrypt
   certificate automatically; otherwise publishes the app on port 80,
7. waits for a healthy response and prints the URL + token.

Expected output with a domain:

```
🎉 DEPLOYMENT SUCCESSFUL ON VULTR!
App URL:      https://leado.example.com/
Access token: <generated token>
```

Open the URL and paste the token into the login screen.

---

## 5. Operations

```bash
cd /opt/leado

# With DOMAIN set (Caddy + HTTPS)
docker compose -f docker-compose.yml -f docker-compose.tls.yml ps
docker compose -f docker-compose.yml -f docker-compose.tls.yml logs -f --tail=100
docker compose -f docker-compose.yml -f docker-compose.tls.yml restart

# Without a domain (plain HTTP)
docker compose ps
docker compose logs -f --tail=100

# Update after code changes
rsync ... ./ root@<IP>:/opt/leado/   # from your machine
ssh root@<IP> 'cd /opt/leado && ./deploy_vultr.sh'
```

The SQLite databases (`leads.db`, `lead_finder.db`) are bind-mounted from the
project directory, so data survives container rebuilds. Back them up with:

```bash
sqlite3 /opt/leado/leads.db ".backup '/opt/leado/backups/leads-$(date +%F).db'"
```

---

## 6. HTTPS with a domain (automatic)

Set the hostname in `.env`:

```dotenv
DOMAIN=leado.example.com
```

Point an **A record** for that hostname at the VM's public IP, then re-run:

```bash
./deploy_vultr.sh
```

The script starts `docker-compose.tls.yml`, which runs **Caddy** on ports 80/443
and proxies to the app over the internal Docker network. Caddy obtains and
renews a free Let's Encrypt certificate automatically — no manual certificate
management.

The app container is bound to `127.0.0.1:8000`; only Caddy is publicly
reachable. If DNS is not correct yet, Caddy logs a challenge failure and retries
every minute; it will succeed on its own once the A record propagates.

To add the record at **Vercel DNS** (the nameservers for `softorasystems.com`):

1. Open the Vercel dashboard → **Domains** → `softorasystems.com` → **DNS Records**.
2. Add: `A` · name `leado` · value `<VM_PUBLIC_IP>` · TTL default.
3. Wait a few minutes, then run `curl -I https://leado.example.com/`.

---

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| URL times out | Allow `80/tcp` and `443/tcp` in the **Vultr firewall group**, not just `ufw`. |
| `port is already allocated` | Something else uses 80; run `APP_PORT=8080 ./deploy_vultr.sh`. |
| HTTPS certificate not issued | Check the A record points at the VM (`dig +short <domain>`), then inspect `docker logs leado-caddy`. Caddy retries automatically. |
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
- With `DOMAIN` set, Caddy terminates TLS and the app is bound to
  `127.0.0.1:8000`; only ports 80/443 are public.
- Root SSH password login is disabled (`PermitRootLogin prohibit-password`) on
  the production VM; use the installed SSH key. Rotate the root password in the
  Vultr dashboard after provisioning.
