"""MCP Server exposing six tools:

  web_search / web_research / fetch_url / crawl_site / search_status
  (+ search_debug for provider_override experiments)

Provider selection stays inside the Oracle — agents never pick backends.

Transports (MCP_TRANSPORT env var):
  stdio            default; Agent spawns it as a child process (single machine)
  streamable-http  shared Oracle on a trusted host; several machines connect
                   over the network and share ONE quota ledger
  sse              legacy HTTP transport, kept for older clients

Run locally (stdio):
  python -m magicalgirl_web_oracle.mcp_server.server

Run shared (HTTP):
  MCP_TRANSPORT=streamable-http MCP_PORT=28288 \
    MCP_HOST=0.0.0.0 MCP_AUTH_TOKEN=<secret> \
    python -m magicalgirl_web_oracle.mcp_server.server
Client URL: http://<host>:28288/mcp
Optional shared secret: MCP_AUTH_TOKEN=<secret>  => clients send
  Authorization: Bearer <secret>
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager, suppress

from mcp.server.fastmcp import FastMCP

from ..config.settings import get_settings
from ..providers.base import ProviderError
from ..router.oracle import Oracle

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("mcp_server")

_settings = get_settings()

mcp = FastMCP(
    "magicalgirl-web-oracle",
    host=_settings.mcp_host,
    port=_settings.mcp_port,
    streamable_http_path=_settings.mcp_path,
)

# One Oracle per server process; keys stay in env, never echoed.
_oracle: Oracle | None = None


def _get_oracle() -> Oracle:
    global _oracle
    if _oracle is None:
        _oracle = Oracle(settings=get_settings())
    return _oracle


def _err(e: Exception) -> str:
    if isinstance(e, ProviderError):
        return f"error: {e.kind}: {e}"
    return f"error: {type(e).__name__}: {e}"


@mcp.tool()
async def web_search(query: str, mode: str = "auto", max_results: int = 10,
                     provider_override: str = "", bypass_cache: bool = False) -> str:
    """Search the web. Modes: fast (quick facts) | auto (default) | high
    (technical/niche, multi-source) | max (deepest, still zero-cost).
    Provider routing is automatic; set provider_override only for debugging.
    Set bypass_cache=true to force a fresh provider request when caching is enabled."""
    try:
        resp = await _get_oracle().web_search(
            query, mode=mode, max_results=max_results,
            provider_override=provider_override or None,
            bypass_cache=bypass_cache)
        lines = [f"mode={resp.mode} providers={','.join(resp.providers_used)} "
                 f"results={len(resp.results)} "
                 f"dedup {resp.dedup_stats.get('raw', 0)}->{resp.dedup_stats.get('deduped', 0)}"]
        for i, r in enumerate(resp.results, 1):
            agree = r.metadata.get("cross_provider_agreement")
            tag = f" [agreement:{agree}]" if agree else ""
            lines.append(f"{i}. {r.title}\n   {r.url}\n   {r.snippet[:300]}\n"
                         f"   by:{','.join(r.found_by)}{tag}")
        for n in resp.notes:
            lines.append(f"note: {n}")
        return "\n".join(lines) if len(lines) > 1 else "no results"
    except Exception as e:
        return _err(e)


@mcp.tool()
async def web_research(query: str, max_results: int = 12, fetch_top: int = 3,
                       bypass_cache: bool = False) -> str:
    """Deep research: multi-provider search + full-text evidence + citations
    (all citations are real URLs from search results). Use for complex questions.
    Set bypass_cache=true to force fresh search and fetch requests."""
    try:
        resp = await _get_oracle().web_research(
            query, max_results=max_results, fetch_top=fetch_top,
            bypass_cache=bypass_cache)
        parts = [f"providers={','.join(resp.providers_used)}"]
        parts.append("== key sources ==")
        for i, r in enumerate(resp.key_sources, 1):
            parts.append(f"{i}. {r.title or r.url} — {r.url} (by {','.join(r.found_by)})")
        parts.append("== evidence excerpts ==")
        for ev in resp.evidence:
            parts.append(f"--- {ev.url} (via {ev.provider}) ---\n{ev.content[:2500]}")
        if resp.conflicts:
            parts.append("== conflicts ==\n" + "\n".join(resp.conflicts))
        parts.append("== citations ==")
        parts.extend(f"- {c.url} [{c.provider or ''}]" for c in resp.citations)
        for n in resp.notes:
            parts.append(f"note: {n}")
        return "\n".join(parts)
    except Exception as e:
        return _err(e)


@mcp.tool()
async def fetch_url(url: str, force_provider: str = "", bypass_cache: bool = False) -> str:
    """Fetch a URL as clean markdown/text. Escalates: light HTTP -> Jina Reader
    -> Crawl4AI (JS pages) -> Firecrawl (anti-bot). Set force_provider only for
    debugging. Set bypass_cache=true to force a fresh fetch."""
    try:
        doc = await _get_oracle().fetch_url(
            url, force_provider=force_provider or None,
            bypass_cache=bypass_cache)
        head = f"[provider={doc.provider} type={doc.content_type}]"
        if doc.title:
            head += f" {doc.title}"
        return f"{head}\n\n{doc.content[:15000]}"
    except Exception as e:
        return _err(e)


@mcp.tool()
async def crawl_site(url: str, max_pages: int = 10, max_depth: int = 2) -> str:
    """Crawl a site (multi-page). Uses self-hosted Crawl4AI (unlimited);
    falls back to Firecrawl credits only if Crawl4AI fails."""
    try:
        resp = await _get_oracle().crawl_site(url, max_pages=max_pages,
                                              max_depth=max_depth)
        parts = [f"[provider={resp.provider} pages={len(resp.pages)}]"]
        for p in resp.pages:
            parts.append(f"--- {p.url} ---\n{p.content[:4000]}")
        return "\n".join(parts)
    except Exception as e:
        return _err(e)


@mcp.tool()
async def search_status() -> str:
    """Provider health/quota/usage status. Never exposes API keys."""
    st = _get_oracle().status()
    lines = [f"allow_paid_overage={st.allow_paid_overage} (hard rule: false)"]
    for p in st.providers:
        q = p.quota
        quota = "unlimited" if (q and q.quota_type.value == "unlimited") else (
            f"{q.used}/{q.limit} used" if (q and q.limit is not None) else
            f"type={q.quota_type.value if q else '?'}")
        lines.append(f"{p.provider:<14} {p.kind.value:<7} health={p.health.value:<12} "
                     f"configured={p.configured} quota={quota} "
                     f"lat={p.avg_latency_ms or '-'}ms")
    for alert in st.quota_alerts:
        lines.append(f"quota_alert kind={alert['kind']} severity={alert['severity']} count={alert['count']}")
    if st.quota_monitor is not None:
        lines.append(f"quota_monitor running={st.quota_monitor['running']} interval={st.quota_monitor['interval_seconds']}s")
    if st.scheduling:
        lines.append(f"scheduling strategy={st.scheduling.get('strategy')} "
                     f"preferred_account={st.scheduling.get('preferred_account') or '-'}")
    if st.cache:
        lines.append(f"cache enabled={st.cache.get('enabled')} entries={st.cache.get('entries')} "
                     f"hits={st.cache.get('hits')} search_ttl={st.cache.get('search_ttl_seconds')}s "
                     f"fetch_ttl={st.cache.get('fetch_ttl_seconds')}s")
    return "\n".join(lines)


@mcp.tool()
async def search_debug(query: str, provider: str, max_results: int = 5) -> str:
    """Debug: query ONE provider directly (exa/tavily/searxng/you_mcp).
    Bypasses routing — use to compare provider quality."""
    try:
        resp = await _get_oracle().web_search(
            query, mode="auto", max_results=max_results, provider_override=provider)
        lines = [f"provider={provider} results={len(resp.results)}"]
        for i, r in enumerate(resp.results, 1):
            lines.append(f"{i}. {r.title}\n   {r.url}\n   {r.snippet[:200]}")
        for n in resp.notes:
            lines.append(f"note: {n}")
        return "\n".join(lines)
    except Exception as e:
        return _err(e)


def _with_auth(app, token: str):
    """Wrap a Starlette app with a constant-time Bearer-token check.

    LAN-only deployments may leave MCP_AUTH_TOKEN empty; setting it keeps the
    shared Oracle from being usable by anything else on the network.
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class BearerAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            # Allow health probes through so NAS uptime checks keep working.
            if request.url.path in ("/healthz", "/", "/admin") or request.url.path.startswith("/admin/"):
                return await call_next(request)
            header = request.headers.get("authorization", "")
            prefix = "bearer "
            supplied = header[len(prefix):].strip() if header.lower().startswith(prefix) else ""
            if not secrets.compare_digest(supplied, token):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    app.add_middleware(BearerAuth)
    return app


def _with_utf8_charset(app):
    """Declare charset=utf-8 on text responses.

    uvicorn emits ``text/event-stream`` with no charset, so clients that fall
    back to the OS locale (cp936 on Chinese Windows) decode UTF-8 bytes with
    GBK and mojibake every non-ASCII character. Everything this server emits
    is UTF-8, so state it instead of leaving it to the client to guess.
    """
    async def middleware(scope, receive, send):
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        async def send_with_charset(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [
                    (key, _declare_utf8(value) if key.lower() == b"content-type"
                     else value)
                    for key, value in message.get("headers", [])
                ]}
            await send(message)

        await app(scope, receive, send_with_charset)

    return middleware


def _declare_utf8(value: bytes) -> bytes:
    ctype = value.decode("latin-1")
    if not ctype.lower().startswith("text/") or "charset=" in ctype.lower():
        return value
    return f"{ctype}; charset=utf-8".encode("latin-1")


@asynccontextmanager
async def _background_quota():
    task=None
    scheduler=None
    if getattr(_settings,'accounts_file','') or getattr(_settings,'vault_key',''):
        from ..quota.scheduler import BalanceScheduler
        oracle=_get_oracle()
        if oracle.registry.credentials is not None:
            scheduler=BalanceScheduler(oracle.registry.credentials,interval=_settings.balance_sync_interval)
            task=asyncio.create_task(scheduler.run(),name='oracle-quota-monitor')
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.stop()
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


async def _close_oracle():
    global _oracle
    if _oracle is not None:
        oracle=_oracle
        _oracle=None
        await oracle.aclose()


def _build_http_app(transport: str):
    from starlette.responses import JSONResponse
    from starlette.routing import Route, Mount
    from ..admin.app import create_admin_app

    app = (mcp.streamable_http_app() if transport == "streamable-http"
           else mcp.sse_app())
    original_lifespan=app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        try:
            async with original_lifespan(application) as state:
                async with _background_quota():
                    yield state
        finally:
            await _close_oracle()

    app.router.lifespan_context=lifespan

    async def healthz(request):
        """Unauthenticated liveness probe for container/uptime checks."""
        st = _get_oracle().status()
        return JSONResponse({
            "status": "ok",
            "transport": transport,
            "providers": {p.provider: p.health.value for p in st.providers},
            "allow_paid_overage": st.allow_paid_overage,
        })

    app.routes.append(Route("/healthz", healthz, methods=["GET"]))
    if _settings.admin_auth_token and _settings.admin_auth_token == _settings.mcp_auth_token:
        raise ValueError("ORACLE_ADMIN_TOKEN must be different from MCP_AUTH_TOKEN")
    app.routes.append(Mount("/admin",create_admin_app(_get_oracle,_settings.admin_auth_token)))

    if _settings.mcp_auth_token:
        app = _with_auth(app, _settings.mcp_auth_token)
    # Install Starlette middleware before wrapping the ASGI callable: the
    # charset wrapper does not expose Starlette.add_middleware().
    return _with_utf8_charset(app)


def _run_http(transport: str) -> None:
    import uvicorn
    app = _build_http_app(transport)
    auth_state = "Bearer token required" if _settings.mcp_auth_token else "NO AUTH (trusted network only)"
    if not _settings.mcp_auth_token and _settings.mcp_host not in ("127.0.0.1", "::1", "localhost"):
        log.warning(
            "MCP HTTP is listening on %s without MCP_AUTH_TOKEN. "
            "Anyone who can reach this port can spend your free quota. "
            "Set MCP_AUTH_TOKEN or bind to 127.0.0.1.", _settings.mcp_host)

    log.info("MCP %s on http://%s:%s%s  [%s]",
             transport, _settings.mcp_host, _settings.mcp_port,
             _settings.mcp_path, auth_state)
    uvicorn.run(app, host=_settings.mcp_host, port=_settings.mcp_port,
                log_level="info")


def main() -> None:
    transport = _settings.mcp_transport
    if transport in ("streamable-http", "sse"):
        _run_http(transport)
    else:
        asyncio.run(_run_stdio())


async def _run_stdio():
    try:
        async with _background_quota():
            await mcp.run_stdio_async()
    finally:
        await _close_oracle()


if __name__ == "__main__":
    main()
