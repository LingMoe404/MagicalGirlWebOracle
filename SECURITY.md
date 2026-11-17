# Security policy

**English** · [简体中文](SECURITY.zh-CN.md) · [日本語](SECURITY.ja.md)

## Supported versions

Security fixes are applied to the latest `0.1.x` release line. If you run a
modified fork, please reproduce the issue against the latest release before
reporting.

## Reporting a vulnerability

Please **do not open a public issue** for a vulnerability that could expose
credentials, allow quota abuse, or bypass authentication.

Use GitHub's private vulnerability reporting:

1. Open the repository's **Security** tab.
2. Choose **Advisories → Report a vulnerability**.
3. Include reproduction steps, affected version/commit, and the impact.

If private reporting is unavailable, contact the maintainer through the
contact method listed on the GitHub profile and ask for a private channel.
Please allow a reasonable amount of time for a fix before public disclosure.

## Security model

MagicalGirlWebOracle is a local/self-hosted service. It does not phone home and
it has no telemetry. Its sensitive assets are:

| Asset | Where it lives | Notes |
|---|---|---|
| Provider API keys | `.env` / environment, or encrypted in the account ledger | Never written to logs or API responses |
| Fernet master key | `ORACLE_VAULT_KEY` | Must be backed up separately; losing it makes encrypted credentials unrecoverable |
| MCP bearer token | `MCP_AUTH_TOKEN` | Optional; empty means anyone who can reach the port can spend your free quota |
| Admin bearer token | `ORACLE_ADMIN_TOKEN` | Independent from the MCP token; empty disables `/admin/` |
| Usage ledger and cache | `data/*.sqlite3` | Contains query hashes by default, not raw queries |
| TLS key/certificate | `data/private/tls/` | Self-signed by default; protect the private key |

### Defaults

- The MCP HTTP server binds to `127.0.0.1` unless you set `MCP_HOST`.
- Docker Compose publishes the port on `127.0.0.1` unless you set
  `MCP_BIND_IP`. `deploy.sh` opts into `0.0.0.0` for shared deployments and
  generates a random `MCP_AUTH_TOKEN` on first run.
- If the HTTP server listens on a non-loopback address without a token, it logs
  a prominent warning at startup.
- Paid overage is disabled and there is no code path that enables payment.
  Provider responses indicating payment required are treated as quota
  exhaustion and the provider is skipped.

## Safe deployment checklist

Before exposing the service beyond a single trusted machine:

- [ ] Set a long random `MCP_AUTH_TOKEN` and configure the same token in every
      MCP client.
- [ ] Keep the MCP and admin ports on a private network or a VPN (WireGuard,
      Tailscale, etc.). Do not publish them directly to the internet.
- [ ] If remote access is required, put the service behind an authenticated
      reverse proxy with TLS and restrict source addresses.
- [ ] Set `ORACLE_ADMIN_TOKEN` to a different long random value; never reuse
      the MCP token.
- [ ] If you use the Web Key management UI, set `ORACLE_VAULT_KEY`, store it
      outside the data volume, and back it up. Do not regenerate it while an
      encrypted ledger exists.
- [ ] Restrict filesystem permissions on `.env`, `data/`, and
      `data/private/` (0600 for files, 0700 for the private directory).
- [ ] Treat the admin UI as a credential-management surface: use HTTPS (the
      bundled `admin.tls_server` or your own reverse proxy) and log out when
      finished.
- [ ] Review the provider list and remove API keys you do not need.

## Known limitations

- The container currently runs as `root` by default. If your threat model
  requires non-root execution, use a user namespace or a derived image that
  creates an unprivileged user and owns `data/` accordingly.
- The bundled admin HTTPS listener uses a self-signed certificate. Browsers
  will warn until you trust it or place the service behind a certificate you
  control.
- Free-tier quotas and provider APIs can change at any time. The project
  cannot guarantee that a provider will remain free.
- `search_status`, `doctor`, and the admin API redact credentials, but the
  usage ledger still contains metadata (timestamps, providers, URLs hashes,
  costs). Protect the data volume like any other local log.

## Out of scope

- Vulnerabilities in third-party providers or their websites.
- Quota policy changes, account bans, or terms-of-service enforcement by
  providers.
- Denial of service caused by exposing an unauthenticated port to the
  internet.
- Issues that require an already-compromised host or filesystem access.
