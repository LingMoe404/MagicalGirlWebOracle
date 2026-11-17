# Multi-account and multi-key setup

**English** · [简体中文](multi-key-setup.zh-CN.md) · [日本語](multi-key-setup.ja.md)

By default the Oracle uses the single key per provider found in `.env`. The
multi-account mode is opt-in and lets you:

- register several provider accounts (for example two Google accounts),
- model shared or independent quota pools,
- attach multiple keys to one pool,
- add, replace, disable, and rotate keys from the admin UI without editing
  `.env`,
- keep an auditable per-key and per-pool usage ledger.

It is designed for free-tier budgets: a pool starts **unavailable** until you
confirm its current allowance, and unknown budgets are skipped rather than
spent optimistically.

## Data model

```text
account label (e.g. Google-A)
  └─ provider account / organization (e.g. Tavily-A)
      ├─ quota pool (monthly free credits, one-time gift, ...)
      └─ API key(s) — each call is attributed to a specific key
```

- Keys under the same provider account share a pool by default.
- Use separate pools only when the provider actually grants separate quotas.
- Do not add balances with different units: USD, credits, tokens, and requests
  are tracked separately.
- Jina Reader/Reranker keys are treated as independent wallets; one wallet
  cannot be shared across keys.

## Enable multi-account mode

1. Create `data/accounts.yaml` from one of the examples:

   ```bash
   cp config/accounts.example.yaml data/accounts.yaml
   ```

2. Point the service at it:

   ```ini
   ORACLE_ACCOUNTS_FILE=/app/data/accounts.yaml
   ```

3. Restart the service.

The YAML file contains references only — never real keys. A key's `secret_env`
names the environment variable that holds its secret:

```yaml
version: 1
accounts:
  - id: tavily-a
    provider: tavily
    label: Tavily primary
    owner_label: Google-A
  - id: tavily-b
    provider: tavily
    label: Tavily secondary
    owner_label: Google-B
pools:
  - id: tavily-a-monthly
    account_id: tavily-a
    unit: credits
    limit: null
    initial_used: null
    free_allowance_confirmed: false
    window: { kind: unknown }
  - id: tavily-b-monthly
    account_id: tavily-b
    unit: credits
    limit: null
    initial_used: null
    free_allowance_confirmed: false
    window: { kind: unknown }
keys:
  - id: tavily-a-primary
    account_id: tavily-a
    label: Primary key
    secret_env: TAVILY_API_KEY
    pool_ids: [tavily-a-monthly]
    priority: 10
  - id: tavily-b-primary
    account_id: tavily-b
    label: Secondary key
    secret_env: TAVILY_API_KEY_SECOND
    pool_ids: [tavily-b-monthly]
    priority: 20
```

More examples:

- `config/accounts.example.yaml` — two Tavily accounts with separate pools.
- `config/accounts.you.example.yaml` — two You.com USD wallets.
- `config/accounts.jina.example.yaml` — two independent Jina token wallets.

### Confirm a pool

A pool with `limit: null`, `initial_used: null`, or
`free_allowance_confirmed: false` is not spendable. Confirm the **current**
free allowance, already-consumed amount, billing day, and timezone before
enabling it. You can do this in the admin UI (recommended) or by editing the
YAML and restarting.

Once confirmed, the ledger reserves budget before each call and settles it
afterwards. Unknown or unconfirmed budgets are skipped.

## Secrets: environment vs encrypted ledger

There are two supported ways to provide key material:

1. **Environment variables** — `secret_env` in the YAML names an environment
   variable. This is simple but keeps secrets in `.env`.
2. **Admin UI + encrypted ledger** — set `ORACLE_VAULT_KEY` and add keys in the
   Web UI. Secrets are stored as Fernet ciphertext in the SQLite ledger; the
   master key stays in the environment and must be backed up separately.

The admin UI never returns a full key after submission. Responses, HTML,
frontend bundles, and logs contain only masked identifiers.

> Losing `ORACLE_VAULT_KEY` makes encrypted credentials unrecoverable. Do not
> regenerate it while an encrypted ledger exists.

## Admin dashboard

Set:

```ini
ORACLE_ADMIN_TOKEN=<long-random-token>
ORACLE_VAULT_KEY=<fernet-key>
ORACLE_ACCOUNTS_FILE=/app/data/accounts.yaml
```

The admin token must differ from `MCP_AUTH_TOKEN`. Start the optional HTTPS
listener with:

```bash
docker compose --profile admin up -d
```

Then open `https://127.0.0.1:28443/admin/`. See
[deployment.md](deployment.md#optional-admin-dashboard) for certificate setup.

The dashboard provides:

- **Quota overview** — per account/key remaining budget, unit, billing window,
  and whether the number is official or locally estimated.
- **Key management** — add a key to an existing shared pool or a new account,
  rename, disable, and replace credentials. Confirmation of the initial usage is
  explicit.
- **Call history and traces** — provider, key, account, status, latency,
  reserved/settled cost, candidate order, and switching reasons.
- **Balance snapshots** — official balance queries per account, with stale or
  failed snapshots kept rather than shown as zero.
- **Alerts** — low quota, invalid credentials, repeated failures, and
  conservative exhaustion forecasts.
- **Cache and scheduling** — current strategy, TTLs, and cache counters.

## Official balance sync

When multi-account mode is enabled, a background scheduler queries official
balances for Tavily, Firecrawl, You.com, and Jina. Configure the interval with
`ORACLE_BALANCE_SYNC_INTERVAL` (seconds; `0` disables, otherwise 60–480).

- Exa has no public balance API; its remaining budget is a local estimate from
  reported `costDollars`.
- Firecrawl/You.com official totals cannot always be split into "free" and
  "paid" sources. They are treated as ceilings, not as automatic free budget.
- A failed sync keeps the previous snapshot and marks it stale. It does not
  reset a balance to full.

## Cache, scheduling, export, backup

```bash
# Search cache (300s) and fetch cache (3600s)
ORACLE_CACHE_TTL_SECONDS=300
ORACLE_FETCH_CACHE_TTL_SECONDS=3600

# priority | balanced | expiry | preferred_account
ORACLE_SCHEDULING_STRATEGY=balanced

# Export masked history (CSV or JSON)
docker compose exec oracle magicalgirl-web export --month 2026-11 --format csv

# Online backup + verification
docker compose exec oracle magicalgirl-web backup \
  --db /app/data/usage.accounts.sqlite3 \
  -o /app/data/backup/usage.accounts.sqlite3
docker compose exec oracle magicalgirl-web verify-backup \
  /app/data/backup/usage.accounts.sqlite3
```

Exports never include query text, URLs as plaintext, credentials, fingerprints,
or the master key. Cache hits do not call a provider and do not consume quota.

## Failure behavior

- **Provider-level failure** (timeout, auth, rate limit, server error) is
  recorded against the provider health breaker and can put a key into a short
  cooldown.
- **Target-level failure** (a dead URL, a 404/422 from the target, an anti-bot
  wall) is reported as `kind="target"`. It does not degrade provider health and
  does not cool down the key; the router escalates to the next fetch backend.
- **Async crawl creation** is not retried blindly when the outcome is unknown,
  to avoid duplicate jobs and double billing.
- A dispatched request whose outcome is unknown remains `uncertain` in the
  ledger; budget is not silently released.

## Backup and recovery

Back up:

- the ledger (`data/usage.accounts.sqlite3`),
- `data/accounts.yaml`,
- `.env` (provider keys and admin token),
- `ORACLE_VAULT_KEY` (separate from the ledger),
- `data/private/tls/` if you use the bundled admin HTTPS listener.

Restore with the services stopped, then run `verify-backup` before trusting the
restored ledger. See [deployment.md](deployment.md#backup-and-restore).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Admin UI says key management is disabled | `ORACLE_VAULT_KEY` is empty | Set it and restart; keep it stable |
| Admin UI is unreachable | Admin token empty or TLS files missing | Set `ORACLE_ADMIN_TOKEN`, provide cert/key, start the `admin` profile |
| A pool never spends | Free allowance not confirmed | Confirm limit, initial usage, and billing window |
| A key is skipped | Disabled, missing secret, unit mismatch, or cooldown | Check the call trace and the key state in the UI |
| Jina key rejected | Jina wallets cannot be shared across keys | Give each Jina key its own account/wallet |
| Balance looks lower than official | Local ledger includes in-flight or uncertain calls | This is intentional; reconcile from the balance history page |
