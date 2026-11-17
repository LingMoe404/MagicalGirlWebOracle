"""Tests for proxy routing, USD quota model, and official balance APIs."""

from __future__ import annotations

import json

import httpx
import pytest

from magicalgirl_web_oracle.models.schemas import QuotaType
from magicalgirl_web_oracle.providers.exa import ExaProvider
from magicalgirl_web_oracle.providers.firecrawl import FirecrawlProvider
from magicalgirl_web_oracle.providers.registry import Registry
from magicalgirl_web_oracle.providers.searxng import SearxngProvider
from magicalgirl_web_oracle.providers.tavily import TavilyProvider
from magicalgirl_web_oracle.providers.you_mcp import YouMcpProvider

from conftest import DummySettings


def patch_client(provider, handler) -> None:
    async def _client():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    provider._client = None
    provider.client = _client  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Proxy routing
# ---------------------------------------------------------------------------

def test_external_providers_use_proxy(settings):
    settings.proxy_url = "http://10.0.0.2:7893"
    reg = Registry(settings)
    for name in ("exa", "tavily", "firecrawl", "you_mcp", "jina_reader",
                 "jina_reranker", "light_http"):
        assert reg.all[name].is_local is False, f"{name} should use the proxy"
    # LAN services must bypass the proxy.
    assert reg.all["searxng"].is_local is True
    assert reg.all["crawl4ai"].is_local is True


@pytest.mark.asyncio
async def test_searxng_client_has_no_proxy(settings):
    settings.proxy_url = "http://10.0.0.2:7893"
    p = SearxngProvider(settings)
    client = await p.client()
    # httpx stores proxy transports in _mounts; a local provider should have none.
    assert not client._mounts
    await p.aclose()


@pytest.mark.asyncio
async def test_external_client_has_proxy_mount(settings):
    settings.proxy_url = "http://10.0.0.2:7893"
    p = ExaProvider(settings)
    client = await p.client()
    assert client._mounts, "external provider should route through the proxy"
    await p.aclose()


def test_proxy_falls_back_to_env(monkeypatch, tmp_path):
    # Settings reads PROXY_URL / HTTPS_PROXY / HTTP_PROXY in that order.
    monkeypatch.setenv("HTTPS_PROXY", "http://env-proxy:8080")
    monkeypatch.delenv("PROXY_URL", raising=False)
    s = DummySettings()
    assert s.proxy_url == "http://env-proxy:8080"


# ---------------------------------------------------------------------------
# Exa USD-denominated quota
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exa_records_actual_dollar_cost(settings):
    p = ExaProvider(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "results": [{"title": "T", "url": "https://a.example/x", "score": 0.9}],
            "costDollars": {"total": 0.007, "search": {"neural": 0.007}},
        })

    patch_client(p, handler)
    await p.search("q", mode="fast")
    assert p.last_cost_usd == pytest.approx(0.007)
    await p.aclose()


@pytest.mark.asyncio
async def test_exa_cost_defaults_when_absent(settings):
    p = ExaProvider(settings)
    patch_client(p, lambda req: httpx.Response(200, json={"results": []}))
    await p.search("q", mode="fast")
    assert p.last_cost_usd == pytest.approx(0.007)  # documented standard rate
    await p.aclose()


def test_exa_quota_is_usd_denominated(settings):
    p = ExaProvider(settings)
    snap = p.quota_snapshot()
    assert snap.unit == "usd"
    assert snap.limit == 10.0  # $10/month recurring free credit
    assert snap.quota_type == QuotaType.MONTHLY


def test_providers_yaml_exa_usd(settings):
    exa_cfg = settings.providers_cfg["providers"]["exa"]["quota"]
    assert exa_cfg["unit"] == "usd"
    assert exa_cfg["limit"] == 10.0
    assert exa_cfg["one_time_bonus"] == 20.0


# ---------------------------------------------------------------------------
# Official balance APIs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tavily_fetch_balance(settings):
    p = TavilyProvider(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/usage"
        return httpx.Response(200, json={
            "key": {"usage": 5, "limit": None},
            "account": {"current_plan": "Researcher", "plan_usage": 12,
                        "plan_limit": 1000, "paygo_usage": 0, "paygo_limit": None},
        })

    patch_client(p, handler)
    bal = await p.fetch_balance()
    assert bal["plan"] == "Researcher"
    assert bal["used"] == 12 and bal["limit"] == 1000
    await p.aclose()


@pytest.mark.asyncio
async def test_firecrawl_fetch_balance(settings):
    settings.firecrawl_api_key = "fc-test"
    p = FirecrawlProvider(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        assert "credit-usage" in request.url.path
        return httpx.Response(200, json={"success": True, "data": {
            "remainingCredits": 1399, "planCredits": 1000,
            "billingPeriodStart": "2026-09-09T08:52:15.001Z",
            "billingPeriodEnd": "2026-10-09T08:52:15.001Z"}})

    patch_client(p, handler)
    bal = await p.fetch_balance()
    assert bal["remaining_credits"] == 1399
    await p.aclose()


@pytest.mark.asyncio
async def test_you_mcp_fetch_balance_uses_auth_endpoint(settings):
    settings.ydc_api_key = "ydc-test"
    p = YouMcpProvider(settings)
    assert p.endpoint == "https://api.you.com/mcp"  # authenticated

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["method"])
        assert request.headers.get("Authorization") == "Bearer ydc-test"
        if body["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}},
                                  headers={"mcp-session-id": "s1"})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": {
            "content": [{"type": "text", "text": "{}"}],
            "structuredContent": {"data": {"type": "account",
                                           "attributes": {"balance": 10000}}}}})

    patch_client(p, handler)
    bal = await p.fetch_balance()
    assert bal == {"balance": 10000}
    assert "tools/call" in calls
    await p.aclose()


@pytest.mark.asyncio
async def test_you_mcp_free_profile_without_key(settings):
    settings.ydc_api_key = ""
    p = YouMcpProvider(settings)
    assert p.endpoint == "https://api.you.com/mcp?profile=free"
    assert await p.fetch_balance() is None  # you-balance is auth-only


@pytest.mark.asyncio
async def test_you_mcp_headers_include_auth_when_configured(settings):
    settings.ydc_api_key = "ydc-test"
    p = YouMcpProvider(settings)
    assert p._headers()["Authorization"] == "Bearer ydc-test"
