# Configuration

**English** · [简体中文](configuration.zh-CN.md) · [日本語](configuration.ja.md)

MagicalGirlWebOracle reads configuration from two places:

1. **Environment variables** — usually a `.env` file in the current working
   directory (or the project root when running from a source checkout).
2. **YAML files** — `config/providers.yaml` and `config/router_policy.yaml`.
   When installed from a wheel, bundled defaults in
   `magicalgirl_web_oracle/config/defaults/` are used unless a local `config/`
   directory is present.

Real secrets belong only in `.env`, environment variables, or the encrypted
account ledger. Never commit them.

## Environment variables

### Provider keys (all optional)

A provider is enabled when its key is present (or, for You.com and Jina Reader,
when their keyless modes are available). Missing keys simply remove that
provider from routing.

| Variable | Purpose |
|---|---|
| `EXA_API_KEY` | Exa search / contents |
| `TAVILY_API_KEY` | Tavily search |
| `FIRECRAWL_API_KEY` | Firecrawl fetch / crawl (last resort) |
| `JINA_API_KEY` | Jina Reader and (optionally) reranker |
| `YDC_API_KEY` | You.com authenticated profile; keyless MCP works without it |

### Self-hosted services

| Variable | Default | Purpose |
|---|---|---|
| `SEARXNG_URL` | empty | SearXNG base URL (JSON output must be enabled) |
| `CRAWL4AI_URL` | empty | Crawl4AI API base URL |
| `CRAWL4AI_TOKEN` | empty | Bearer token for Crawl4AI, if your deployment requires one |
| `PROXY_URL` | empty | Outbound proxy for cloud providers; self-hosted URLs bypass it |

`PROXY_URL` falls back to `HTTPS_PROXY` / `HTTP_PROXY`. Requests to
`SEARXNG_URL` and `CRAWL4AI_URL` always go direct and are never sent through
the proxy.

### Behavior

| Variable | Default | Purpose |
|---|---|---|
| `ALLOW_PAID_OVERAGE` | `false` | Hard rule. Keep it false; paid overage is not supported |
| `SEARCH_MODE_DEFAULT` | `auto` | `fast` / `auto` / `high` / `max` |
| `USAGE_DB_PATH` | `data/usage.sqlite3` | SQLite usage ledger |
| `STORE_QUERY_TEXT` | `false` | Store raw query text instead of only a salted hash |
| `QUERY_HASH_SALT` | built-in fallback | Set your own random salt if you store hashes long-term |
| `JINA_KEYLESS_FALLBACK` | `true` | Allow low-rate keyless `r.jina.ai` calls when no Jina key is set |

### MCP transport and access

| Variable | Default | Purpose |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` | `stdio`, `streamable-http`, or `sse` |
| `MCP_HOST` | `127.0.0.1` | Bind address for direct HTTP runs. Set `0.0.0.0` to expose |
| `MCP_PORT` | `28288` | HTTP port |
| `MCP_PATH` | `/mcp` | HTTP path |
| `MCP_BIND_IP` | `127.0.0.1` | Host interface published by Docker Compose |
| `MCP_AUTH_TOKEN` | empty | Bearer token required by clients when set |

For a shared deployment, set a long random `MCP_AUTH_TOKEN` and publish the
port only on the interface you need (LAN, VPN, or loopback behind a reverse
proxy). See [deployment.md](deployment.md) and [../SECURITY.md](../SECURITY.md).

### Multi-account / admin (opt-in)

| Variable | Default | Purpose |
|---|---|---|
| `ORACLE_ACCOUNTS_FILE` | empty | Path to the multi-account YAML (e.g. `data/accounts.yaml`) |
| `ORACLE_ADMIN_TOKEN` | empty | Independent bearer token for `/admin/`; empty disables the UI |
| `ORACLE_VAULT_KEY` | empty | Fernet master key for Web-managed credentials |
| `ORACLE_ADMIN_BIND_IP` | `127.0.0.1` | Host interface published by Docker Compose for the admin listener |
| `ORACLE_ADMIN_HTTPS_PORT` | `28443` | Port for the bundled HTTPS admin listener |
| `ORACLE_ADMIN_TLS_CERT` | `/app/data/private/tls/cert.pem` | TLS certificate |
| `ORACLE_ADMIN_TLS_KEY` | `/app/data/private/tls/key.pem` | TLS private key |
| `ORACLE_BALANCE_SYNC_INTERVAL` | `300` | Official balance sync interval in seconds; `0` disables, otherwise 60–480 |
| `ORACLE_LOW_QUOTA_RATIO` | `0.1` | Low-quota warning threshold, `0`–`1`; `0` disables |

`ORACLE_ADMIN_TOKEN` must differ from `MCP_AUTH_TOKEN`. The admin listener does
not expose `/mcp`.

### Cache and scheduling

| Variable | Default | Purpose |
|---|---|---|
| `ORACLE_CACHE_TTL_SECONDS` | `0` | Search cache TTL; `0` disables caching |
| `ORACLE_FETCH_CACHE_TTL_SECONDS` | `3600` when search cache is enabled, else `0` | Fetch cache TTL |
| `ORACLE_CACHE_MAX_ENTRIES` | `500` | Maximum cached entries (10–10000) |
| `ORACLE_CACHE_PATH` | next to the usage DB | Separate SQLite cache file |
| `ORACLE_SCHEDULING_STRATEGY` | `priority` | `priority`, `balanced`, `expiry`, or `preferred_account` |
| `ORACLE_PREFERRED_ACCOUNT` | empty | Required when the strategy is `preferred_account` |

Cache hits do not call a provider and do not consume quota. Freshness-sensitive
queries (for example `latest`, `today`, `live`, `最新`, `今天`, `实时`) bypass
the cache automatically.

## YAML configuration

### `config/providers.yaml`

Free-quota metadata per provider: quota type, limit, unit, renewal behavior,
cost tier, capabilities, and notes. The router uses this data to score
candidates and to decide whether a request is provably within a free quota.
Do not put API keys in this file.

### `config/router_policy.yaml`

Scoring weights, mode definitions (`fast`, `auto`, `high`, `max`), Chinese-query
boosting, the fetch escalation order, and the paid-overage guard. The defaults
are intentionally conservative: cheap and self-hosted providers are preferred,
and unknown quotas are skipped.

## Multi-account configuration

Multi-account mode is opt-in and uses a separate YAML file referenced by
`ORACLE_ACCOUNTS_FILE`. It models accounts, quota pools, and keys explicitly;
the Web UI can add and rotate keys without editing `.env`. See
[multi-key-setup.md](multi-key-setup.md) for the schema, the Web management
flow, and backup/restore notes.

## Checking your configuration

```bash
magicalgirl-web status      # provider health, quota and masked config
magicalgirl-web doctor      # connectivity and configuration diagnostics
magicalgirl-web doctor --json
```

Neither command prints provider keys or the vault key.
