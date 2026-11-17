"""Provider wire-format tests with mocked HTTP (httpx MockTransport)."""

from __future__ import annotations

import json

import httpx
import pytest

from magicalgirl_web_oracle.models.schemas import QuotaType
from magicalgirl_web_oracle.providers.crawl4ai import Crawl4AIProvider
from magicalgirl_web_oracle.providers.exa import ExaProvider
from magicalgirl_web_oracle.providers.jina import JinaReaderFetcher
from magicalgirl_web_oracle.providers.light_http import LightHttpFetcher
from magicalgirl_web_oracle.providers.tavily import TavilyProvider
from magicalgirl_web_oracle.providers.searxng import SearxngProvider
from magicalgirl_web_oracle.providers.you_mcp import YouMcpProvider
from magicalgirl_web_oracle.providers.base import ProviderError
from magicalgirl_web_oracle.providers.registry import Registry

from conftest import DummySettings


def patch_client(provider: "object", handler) -> None:
    """Replace a provider's async client with a MockTransport-backed one."""

    async def _client():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    provider._client = None  # reset
    provider.client = _client  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Exa
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exa_search_parses_results():
    s = DummySettings()
    prov = ExaProvider(s)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.exa.ai"
        assert request.headers["x-api-key"] == "test-exa-key"
        body = json.loads(request.content)
        assert body["type"] == "fast"  # auto mode maps to cost-cheap fast
        return httpx.Response(200, json={"results": [
            {"title": "Rust vs C++", "url": "https://blog.example.com/rust?utm=x",
             "score": 0.9, "publishedDate": "2025-01-01",
             "text": "some content", "summary": "a summary"}]})

    patch_client(prov, handler)
    results = await prov.search("rust vs c++", mode="auto", max_results=5)
    assert len(results) == 1
    r = results[0]
    assert r.provider == "exa"
    assert r.url == "https://blog.example.com/rust"  # canonicalized
    assert r.published_at == "2025-01-01"
    assert r.content == "some content"
    await prov.aclose()


@pytest.mark.asyncio
async def test_exa_402_is_payment_error():
    prov = ExaProvider(DummySettings())
    patch_client(prov, lambda req: httpx.Response(402, text="out of credits"))
    with pytest.raises(ProviderError) as ei:
        await prov.search("q", mode="high")
    assert ei.value.kind == "quota"  # 402 must NEVER trigger payment
    await prov.aclose()


# ---------------------------------------------------------------------------
# Tavily
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tavily_basic_depth_on_auto():
    prov = TavilyProvider(DummySettings())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-tavily-key"
        body = json.loads(request.content)
        assert body["search_depth"] == "basic"  # cost control on auto
        return httpx.Response(200, json={"results": [
            {"title": "T", "url": "https://a.example.com/x",
             "content": "snippet", "score": 0.8}]})

    patch_client(prov, handler)
    results = await prov.search("q", mode="auto")
    assert results[0].provider == "tavily"
    assert results[0].score == 0.8
    await prov.aclose()


@pytest.mark.asyncio
async def test_tavily_advanced_on_high():
    prov = TavilyProvider(DummySettings())
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"results": []})

    patch_client(prov, handler)
    await prov.search("q", mode="high")
    assert seen["search_depth"] == "advanced"
    await prov.aclose()


# ---------------------------------------------------------------------------
# SearXNG
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_searxng_chinese_routing_uses_cn_engines():
    prov = SearxngProvider(DummySettings())
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"results": [
            {"title": "小米SU7", "url": "https://www.bilibili.com/video/BV1xx/?spm=1",
             "content": "评测", "engines": ["bilibili", "baidu"], "score": 9}]})

    patch_client(prov, handler)
    results = await prov.search("小米su7 评测 怎么样", mode="auto")
    assert "baidu" in seen["engines"]
    assert "bilibili" in seen["engines"]
    assert seen["language"] == "zh-CN"
    assert results[0].url == "https://bilibili.com/video/BV1xx"
    assert any("searxng:bilibili" in fb for fb in results[0].found_by)
    await prov.aclose()


@pytest.mark.asyncio
async def test_searxng_english_query_no_cn_engines():
    prov = SearxngProvider(DummySettings())
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"results": []})

    patch_client(prov, handler)
    await prov.search("best static site generator", mode="fast")
    assert "engines" not in seen
    await prov.aclose()


@pytest.mark.asyncio
async def test_searxng_403_json_disabled():
    prov = SearxngProvider(DummySettings())
    patch_client(prov, lambda req: httpx.Response(403, text="json format not allowed"))
    with pytest.raises(ProviderError):
        await prov.search("q", mode="auto")
    await prov.aclose()


# ---------------------------------------------------------------------------
# You.com free MCP
# ---------------------------------------------------------------------------

def _mcp_response(result_obj) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "result": result_obj}


@pytest.mark.asyncio
async def test_you_mcp_initialize_and_search():
    prov = YouMcpProvider(DummySettings())
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if calls[-1]["method"] == "initialize":
            return httpx.Response(
                200, json=_mcp_response({"protocolVersion": "2025-03-26",
                                         "capabilities": {}, "serverInfo": {"name": "you"}}),
                headers={"mcp-session-id": "sess-1"})
        if calls[-1]["method"] == "tools/call":
            assert calls[-1]["params"]["name"] == "you-search"
            return httpx.Response(200, json=_mcp_response({
                "content": [{"type": "text", "text":
                    "1. [Example News](https://news.example.com/a?utm_source=you)\n"
                    "   Fresh headline about something\n"
                    "2. [Second](https://two.example.com/)\n"
                    "   Another snippet"}]}))
        return httpx.Response(200, json={})

    patch_client(prov, handler)
    results = await prov.search("latest news", mode="fast")
    assert calls[0]["method"] == "initialize"
    assert len(results) == 2
    assert results[0].url == "https://news.example.com/a"
    assert results[0].snippet == "Fresh headline about something"
    await prov.aclose()


@pytest.mark.asyncio
async def test_you_mcp_structured_results():
    prov = YouMcpProvider(DummySettings())
    state = {"init": False}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "initialize":
            state["init"] = True
            return httpx.Response(200, json=_mcp_response({}),
                                  headers={"mcp-session-id": "s"})
        return httpx.Response(200, json=_mcp_response({
            "structuredContent": {"results": [
                {"title": "S", "url": "https://s.example.com",
                 "snippet": "structured"}]}}))

    patch_client(prov, handler)
    results = await prov.search("q", mode="fast")
    assert results[0].provider == "you_mcp"
    await prov.aclose()


@pytest.mark.asyncio
async def test_you_mcp_unreachable_raises_timeout():
    prov = YouMcpProvider(DummySettings())

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    patch_client(prov, handler)
    with pytest.raises(ProviderError) as ei:
        await prov.search("q", mode="fast")
    assert ei.value.kind == "timeout"  # auto-skip, system keeps working
    await prov.aclose()


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------

def test_registry_contains_all_backends(settings):
    reg = Registry(settings)
    assert set(reg.search) == {"you_mcp", "searxng", "exa", "tavily"}
    assert set(reg.fetch) == {"light_http", "jina_reader", "crawl4ai", "firecrawl"}
    assert "jina_reranker" in reg.rerankers
    # Unconfigured providers report configured=False (auto-unavailable, non-fatal).
    assert reg.fetch["firecrawl"].configured is False
    assert reg.fetch["crawl4ai"].configured is False
    assert reg.search["exa"].configured is True


# ---------------------------------------------------------------------------
# Target-level failures must not poison provider health
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_light_http_non_200_is_target_failure_not_provider_fault():
    prov = LightHttpFetcher(DummySettings())
    patch_client(prov, lambda req: httpx.Response(404, text="missing page"))
    with pytest.raises(ProviderError) as ei:
        await prov.fetch("https://example.com/missing")
    assert ei.value.kind == "target"
    assert ei.value.affects_health is False
    await prov.aclose()


@pytest.mark.asyncio
async def test_jina_reader_422_is_target_failure_not_provider_fault():
    s = DummySettings()
    s.jina_api_key = "test-jina-key"
    prov = JinaReaderFetcher(s)
    patch_client(prov, lambda req: httpx.Response(422, text='{"detail":"cannot fetch target"}'))
    with pytest.raises(ProviderError) as ei:
        await prov.fetch("https://dead.example/page")
    assert ei.value.kind == "target"
    assert ei.value.affects_health is False
    await prov.aclose()


@pytest.mark.asyncio
async def test_crawl4ai_target_failure_is_target_not_provider_fault():
    s = DummySettings()
    s.crawl4ai_url = "http://crawl4ai.local:11235"
    prov = Crawl4AIProvider(s)
    patch_client(prov, lambda req: httpx.Response(
        502, json={"detail": "Blocked by anti-bot protection: Structural: minimal_text"}))
    with pytest.raises(ProviderError) as ei:
        await prov.fetch("https://blocked.example/page")
    assert ei.value.kind == "target"
    assert ei.value.affects_health is False
    await prov.aclose()


@pytest.mark.asyncio
async def test_crawl4ai_generic_5xx_still_counts_against_provider():
    s = DummySettings()
    s.crawl4ai_url = "http://crawl4ai.local:11235"
    prov = Crawl4AIProvider(s)
    patch_client(prov, lambda req: httpx.Response(500, text="internal error"))
    with pytest.raises(ProviderError) as ei:
        await prov.fetch("https://example.com/page")
    assert ei.value.kind == "server"
    assert ei.value.affects_health is True
    await prov.aclose()
