# Deployment

**English** · [简体中文](deployment.zh-CN.md) · [日本語](deployment.ja.md)

This guide covers a Docker deployment. The same package also runs directly with
Python (see [configuration.md](configuration.md) for environment variables).

## Prerequisites

- Docker Engine with the Compose plugin (or Docker Desktop).
- Optional but recommended: a SearXNG instance with JSON output enabled and/or
  a Crawl4AI instance. Without them the service still runs, but loses the
  self-hosted search and browser-grade fetch fallbacks.
- At least one provider key, or keyless You.com / Jina Reader access.

## Quick start (Docker Compose)

```bash
git clone https://github.com/LingMoe404/MagicalGirlWebOracle.git
cd MagicalGirlWebOracle
cp .env.example .env
```

Edit `.env`:

- Set `MCP_AUTH_TOKEN` to a long random value if any other machine will
  connect.
- Set `SEARXNG_URL` / `CRAWL4AI_URL` if you run those services.
- Add the provider keys you have.

Start the service:

```bash
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:28288/healthz
```

The port is published on `127.0.0.1` by default. To allow other machines on a
trusted LAN or VPN, set `MCP_BIND_IP` to the interface address (for example
`0.0.0.0` or a VPN address) and keep `MCP_AUTH_TOKEN` enabled.

### Use the published image instead of building

```bash
# after creating .env
ORACLE_IMAGE=lingmoe404/magicalgirl-web-oracle:0.1.0 \
  docker compose up -d --no-build
```

The image is also available without a version tag (`:latest`). Pin an explicit
version for reproducible deployments.

### One-command script

`deploy.sh` builds the image, auto-detects SearXNG/Crawl4AI from inside a
container, generates a token on first run, starts the service, and runs
`magicalgirl-web doctor --json`:

```bash
bash deploy.sh
```

It is idempotent and keeps existing `.env` values. Invoke it through `bash`
because some network filesystems do not preserve the executable bit.

## Standalone Docker

Stdio (single machine, an agent spawns the container):

```bash
docker run --rm -i \
  --env-file .env \
  -v "$PWD/data:/app/data" \
  lingmoe404/magicalgirl-web-oracle:0.1.0
```

HTTP (shared service):

```bash
docker run -d --name magicalgirl-web-oracle \
  --env-file .env \
  -e MCP_TRANSPORT=streamable-http \
  -e MCP_HOST=0.0.0.0 \
  -e MCP_AUTH_TOKEN="$MCP_AUTH_TOKEN" \
  -p 127.0.0.1:28288:28288 \
  -v "$PWD/data:/app/data" \
  lingmoe404/magicalgirl-web-oracle:0.1.0
```

The `data/` volume holds the usage ledger, the optional cache, and (if you use
the admin UI) the encrypted account state and TLS files. Back it up.

## MCP client configuration

The server exposes a streamable-http endpoint at `/mcp`. A generic client
configuration looks like:

```json
{
  "mcpServers": {
    "magicalgirl-web-oracle": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:28288/mcp",
      "headers": { "Authorization": "Bearer <MCP_AUTH_TOKEN>" }
    }
  }
}
```

Omit the `headers` object when `MCP_AUTH_TOKEN` is empty. For a stdio client,
run `magicalgirl-web` or `python -m magicalgirl_web_oracle.mcp_server.server`
as the command instead of using a URL. See
[`examples/mcp/`](../examples/mcp/) for copy-paste examples.

## Optional admin dashboard

The admin dashboard is a separate, opt-in HTTPS listener. It shares the usage
ledger with the MCP process and does not expose `/mcp`.

1. Set the tokens and paths in `.env`:

   ```ini
   ORACLE_ACCOUNTS_FILE=/app/data/accounts.yaml
   ORACLE_ADMIN_TOKEN=<long-random-token>
   ORACLE_VAULT_KEY=<fernet-key>
   ORACLE_ADMIN_BIND_IP=127.0.0.1
   ORACLE_ADMIN_HTTPS_PORT=28443
   ```

   Generate a Fernet key with:

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

2. Generate a self-signed certificate (or provide your own):

   ```bash
   mkdir -p data/private/tls
   openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
     -keyout data/private/tls/key.pem \
     -out data/private/tls/cert.pem \
     -subj "/CN=magicalgirl-web-oracle" \
     -addext "subjectAltName=IP:127.0.0.1,DNS:localhost"
   chmod 700 data/private data/private/tls
   chmod 600 data/private/tls/key.pem data/private/tls/cert.pem
   ```

3. Start both services:

   ```bash
   docker compose --profile admin up -d
   ```

4. Open `https://127.0.0.1:28443/admin/` and log in with
   `ORACLE_ADMIN_TOKEN`.

The admin listener binds to `127.0.0.1` on the host by default. Use a VPN or an
authenticated reverse proxy for remote access; do not expose it directly to the
internet. See [multi-key-setup.md](multi-key-setup.md) for account and key
management.

## SearXNG and Crawl4AI

### SearXNG

Enable JSON in `settings.yml`:

```yaml
search:
  formats:
    - html
    - json
```

Then set `SEARXNG_URL`. The provider layer treats it as a local service and
bypasses `PROXY_URL`.

### Crawl4AI

Run the Crawl4AI Docker image, then set `CRAWL4AI_URL` (and
`CRAWL4AI_TOKEN` if your deployment requires a bearer token). Crawl4AI is the
preferred backend for JavaScript-heavy pages and multi-page crawls.

## Remote access

Recommended: keep the service on loopback or a private interface and connect
through a VPN (WireGuard, Tailscale, etc.). If you must use a reverse proxy:

- Terminate TLS at the proxy and forward to `127.0.0.1:28288`.
- Require authentication at the proxy or keep `MCP_AUTH_TOKEN` enabled.
- Do not expose `/healthz` or `/admin/` publicly without protection.
- Restrict source addresses where possible.

## Verification

```bash
# Liveness and provider health
curl -fsS http://127.0.0.1:28288/healthz

# Full diagnostics (inside the container)
docker compose exec oracle magicalgirl-web doctor --json

# MCP initialize smoke test
curl -sS -D - -o /dev/null -X POST http://127.0.0.1:28288/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H "Authorization: Bearer $MCP_AUTH_TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'
```

A healthy response includes `"status":"ok"` and a `providers` map. Individual
providers may be `degraded` if a recent request failed; `healthy` means the
circuit breaker has no consecutive failures.

## Upgrades

```bash
git pull
docker compose build
docker compose up -d
```

or, with the published image:

```bash
docker compose pull
docker compose up -d --no-build
```

The `data/` volume survives container replacement. Read the
[CHANGELOG](../CHANGELOG.md) before upgrading across minor versions.

## Backup and restore

The CLI can copy the account ledger with SQLite's backup API:

```bash
docker compose exec oracle magicalgirl-web backup \
  --db /app/data/usage.accounts.sqlite3 \
  -o /app/data/backup/usage.accounts.sqlite3
docker compose exec oracle magicalgirl-web verify-backup \
  /app/data/backup/usage.accounts.sqlite3
```

Also back up:

- `.env` (contains provider keys and the admin token),
- `data/accounts.yaml` (account/pool/key references, no secrets),
- `ORACLE_VAULT_KEY` (separately from the ledger — without it, encrypted
  credentials cannot be recovered),
- `data/private/tls/` if you use the bundled admin listener.

Restore by stopping the services, replacing the ledger/configuration, and
starting again. Run `verify-backup` before trusting a restored file.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `docker compose` cannot reach SearXNG/Crawl4AI | They run in another Compose project or on the host | Use the host IP / `host.docker.internal`, or put the services on the same Docker network |
| `/healthz` returns 200 but every search fails | No provider keys and no SearXNG | Set at least one provider key or deploy SearXNG |
| MCP client gets 401 | Token enabled but missing/wrong header | Send `Authorization: Bearer <MCP_AUTH_TOKEN>` |
| MCP client gets connection refused | `MCP_BIND_IP` is loopback and the client is remote | Publish on a reachable interface or use a VPN |
| Admin page cannot start | Missing TLS cert/key or admin token | Check `data/private/tls/` and `ORACLE_ADMIN_TOKEN` |
| Crawl4AI returns 502 for a page | The target page is blocked or unreachable | This is a target-level failure; the router escalates rather than marking Crawl4AI unhealthy |
| Provider shows `degraded` | Recent provider-level failure | Check `search_status` and provider keys; a successful call resets it |

## Container user

The image currently runs as `root`, which keeps volume permissions simple. If
your policy requires non-root execution, derive an image that creates an
unprivileged user and `chown`s `/app/data` before dropping privileges.
