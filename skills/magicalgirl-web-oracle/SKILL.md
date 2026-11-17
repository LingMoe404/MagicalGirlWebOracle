---
name: magicalgirl-web-oracle
description: Zero-cost unified web search / deep research / web fetch routing for AI agents. Use when the agent needs to search the web, fetch a page, or research a topic — the system auto-routes across free-tier providers (You.com MCP, SearXNG CN engines, Exa, Tavily) and self-hosted fetchers (Crawl4AI) with an unlimited fallback. Supports Chinese internet search, evidence packs with real citations, response caching, and never spends money.
---

# MagicalGirlWebOracle

You have access to a unified web-access toolset backed by **MagicalGirlWebOracle**.
It picks providers, accounts and keys for you — you never choose a search backend
yourself.

## Tools

| Tool | When to use |
|---|---|
| `web_search(query, mode, max_results, provider_override, bypass_cache)` | Any normal search. Modes: `fast` (facts/versions/news), `auto` (default), `high` (niche technical, needs cross-validation), `max` (deepest; still free). `provider_override` is debugging-only; `bypass_cache` forces fresh results. |
| `web_research(query, max_results, fetch_top, bypass_cache)` | Complex multi-hop questions. Returns an evidence pack: key sources, full-text excerpts, and citations (all real URLs). |
| `fetch_url(url, force_provider, bypass_cache)` | Read a specific page. Escalates automatically: plain HTTP → Jina Reader → Crawl4AI (JS pages) → Firecrawl (anti-bot). |
| `crawl_site(url, max_pages)` | Multi-page crawl of a site. Prefers self-hosted Crawl4AI (unlimited). Crawl results are not cached. |
| `search_status()` | Provider health/quota table, current scheduling strategy and cache status. Use when results seem degraded. |
| `search_debug(query, provider)` | Force one provider to compare quality. Debugging only. |

All parameters after `query`/`url` are optional. Existing clients do not need a
config change to use the new cache or scheduling features.

## Response cache and scheduling

- The shared deployment may serve a cached search/fetch result. Cache hits do
  not call a provider and do not consume free quota; the result notes or trace
  will say it came from cache.
- Default TTLs on a shared deployment are 300 seconds for search and
  3600 seconds for fetch. Freshness-sensitive queries (`最新`、`今天`、`实时`、
  `股价`、`latest`、`today`、`live` 等) automatically skip the cache.
- Set `bypass_cache=true` only when the user explicitly needs fresh information,
  when a cached answer looks stale, or when comparing provider quality. Do not
  set it by default — it spends provider quota.
- Scheduling (`priority` / `balanced` / `expiry` / `preferred_account`) is
  server-side. The agent must not ask for account IDs or keys, and must not try
  to choose a specific account to “save” quota. `search_status()` reports the
  active strategy if you need to explain routing.

## Routing behavior (what happens behind the scenes)

- Simple/factual queries → free daily providers first (You.com MCP → SearXNG). Monthly-credit engines (Exa/Tavily) are conserved.
- Technical/niche/GitHub/HF queries → Exa, cross-checked with a second source.
- Chinese-internet queries (公众号, Bilibili, 国内新闻, 国产 AI …) → SearXNG Chinese engine matrix (baidu/sogou/bilibili/quark/360…), optionally cross-validated with Exa/Tavily.
- JS-heavy or blocked pages → Crawl4AI (self-hosted, unlimited) → Firecrawl only as last resort.
- When all free tiers are exhausted → SearXNG + Crawl4AI still work. The system never pays.
- In multi-key mode, `balanced` scheduling prefers an account with more remaining
  free budget; a cache hit bypasses provider selection entirely.

## Rules for the agent

1. Don't specify `mode` unless you have a reason; `auto` is calibrated.
2. Use `web_research` (not repeated `web_search`) when you'd otherwise need >3 searches on one topic.
3. Citations in research output are grounded in real URLs — cite those, don't invent others.
4. If a result set looks weak, check `search_status()` before retrying blindly.
5. Provider health/quota state is managed for you; never ask the user for API keys.
6. Don't set `bypass_cache` by default. Use it for explicit freshness requests or
   when a cached result is clearly stale; mention that a retry may consume quota.
7. A cache note is not a provider failure. Only retry without cache when freshness
   actually matters.

## CLI equivalent

```text
magicalgirl-web search "query" --mode high
magicalgirl-web search "query" --bypass-cache
magicalgirl-web research "query"
magicalgirl-web fetch "URL" --bypass-cache
magicalgirl-web status
magicalgirl-web doctor

# Stage 4 operations (sanitized; no query, URL, or credentials in exports)
magicalgirl-web export --month 2026-11 --format csv -o usage-2026-11.csv
magicalgirl-web backup -o backup/usage.accounts.manual.sqlite3
magicalgirl-web verify-backup backup/usage.accounts.manual.sqlite3

# Fixed public samples; without --live this only validates the sample file.
magicalgirl-web quality-eval
magicalgirl-web quality-eval --live --max-samples 6
```

`quality-eval --live` and repeated `--bypass-cache` calls consume real free
quota. Ask the user before running a batch.

## Setup (one-time)

Fill `.env` (see `.env.example`) with any keys you have; everything optional:
`EXA_API_KEY`, `TAVILY_API_KEY`, `FIRECRAWL_API_KEY`, `JINA_API_KEY`,
`SEARXNG_URL` (self-hosted), `CRAWL4AI_URL` (self-hosted). Missing keys just disable that provider.

A shared deployment is also a valid backend: connect
the MCP client to `http://<oracle-host>:28288/mcp` (add the `Authorization`
header only when auth is enabled there). Cache, scheduling and multi-key
rotation are server-side; clients keep the same URL and tool names.

For this home lab the endpoint and parameters live in
`deploy/homelab/DEPLOY.md`; per-client registration (ZCode / DSH / any MCP
client) is in `deploy/homelab/CLIENT_INSTALL.md`.
