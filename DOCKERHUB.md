![MagicalGirlWebOracle](https://raw.githubusercontent.com/LingMoe404/MagicalGirlWebOracle/main/assets/icon/MagicalGirlWebOracle-160.png)

# MagicalGirlWebOracle

**Zero-cost web search, deep research, and page fetching for AI agents over MCP.**

This image runs a self-hosted routing service that selects free-tier providers
and self-hosted fallbacks automatically. It never enables paid overage.

- Search providers: You.com MCP, SearXNG, Exa, Tavily
- Fetch ladder: light HTTP → Jina Reader → Crawl4AI → Firecrawl
- Extras: response cache, explainable scheduling, multi-account quota ledger,
  optional Fluent UI admin dashboard

## Quick start

```bash
docker run -d --name magicalgirl-web-oracle \
  --env-file .env \
  -e MCP_TRANSPORT=streamable-http \
  -e MCP_HOST=0.0.0.0 \
  -p 127.0.0.1:28288:28288 \
  -v magicalgirl-oracle-data:/app/data \
  lingmoe404/magicalgirl-web-oracle:0.1.0
```

Then create a `.env` with the provider keys you have (all optional) and, for
shared deployments, a long random `MCP_AUTH_TOKEN`.

MCP endpoint: `http://127.0.0.1:28288/mcp`

## Tags

- `0.1.0` — current release
- `0.1` — latest patch of the 0.1 line
- `latest` — latest stable release

Pin an explicit version for reproducible deployments.

## Links

- Source, documentation, and configuration reference:
  https://github.com/LingMoe404/MagicalGirlWebOracle
- Deployment guide:
  https://github.com/LingMoe404/MagicalGirlWebOracle/blob/main/docs/deployment.md
- Security policy:
  https://github.com/LingMoe404/MagicalGirlWebOracle/blob/main/SECURITY.md

## Security

The MCP port is published on `127.0.0.1` in the example above. Do not expose it
directly to the internet. Set `MCP_AUTH_TOKEN`, use a VPN or an authenticated
reverse proxy, and keep the admin dashboard private. The container currently
runs as root; see the security policy for hardening notes.

## License

MIT. This project is not affiliated with Oracle Corporation or with any search
provider.
