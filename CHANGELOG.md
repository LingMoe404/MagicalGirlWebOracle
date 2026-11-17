# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-17

First public release.

### Added

- Unified MCP server with `web_search`, `web_research`, `fetch_url`,
  `crawl_site`, `search_status`, and `search_debug`.
- Multi-provider search routing across You.com MCP, SearXNG, Exa, and Tavily,
  with quality/cost/health/latency scoring and provider fallback.
- Fetch escalation ladder: lightweight HTTP → Jina Reader → Crawl4AI →
  Firecrawl, with target-level failures isolated from provider health.
- Evidence packs with real citation URLs and cross-provider deduplication.
- CLI (`magicalgirl-web`) for search, research, fetch, crawl, status, doctor,
  export, backup, and quality evaluation.
- Optional multi-account / multi-key quota ledger with Fernet-encrypted
  credentials, independent admin bearer token, and an HTTPS admin listener.
- Fluent UI admin dashboard for quotas, keys, call history, balance snapshots,
  alerts, request traces, cache, and scheduling information.
- Response cache (search and fetch), explainable scheduling strategies
  (`priority`, `balanced`, `expiry`, `preferred_account`), export/backup tools,
  and a fixed-sample quality evaluation harness.
- Original project icon set in `assets/icon/` and integration into the admin UI.

### Security

- MCP HTTP binds to loopback by default; `deploy.sh` opts into `0.0.0.0` and
  generates a random token for shared deployments.
- A startup warning is emitted when the HTTP server is exposed without a token.
- Admin token is independent from the MCP token and cannot be the same value.
- Query text is not stored by default; only a salted hash is retained.

### Known limitations

- The container runs as root by default.
- The bundled admin TLS listener uses a self-signed certificate.
- Free-tier provider quotas and APIs may change without notice.
- External Jina reranking is disabled; HIGH/MAX modes use the local reranker.
