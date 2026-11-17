"""Router / Oracle / fetch-ladder tests (all mocked, no network)."""

from __future__ import annotations

import asyncio
import time

import pytest

from magicalgirl_web_oracle.health.tracker import HealthTracker
from magicalgirl_web_oracle.models.schemas import FetchedDocument, HealthState, QuotaType
from magicalgirl_web_oracle.providers.base import FetchProvider, ProviderError, SearchProvider
from magicalgirl_web_oracle.quota.usage_db import UsageDB
from magicalgirl_web_oracle.router.fetch_router import FetchRouter
from magicalgirl_web_oracle.router.oracle import Oracle
from magicalgirl_web_oracle.router.search_router import SearchRouter
from magicalgirl_web_oracle.providers.registry import Registry

from conftest import FakeFetchProvider, FakeSearchProvider, make_result


def build_oracle(settings, usage, search_providers: dict[str, SearchProvider] | None = None,
                 fetch_providers: dict[str, FetchProvider] | None = None,
                 reranker=None) -> Oracle:
    reg = Registry(settings)
    if search_providers:
        reg.search.clear()
        reg.search.update(search_providers)
    if fetch_providers:
        reg.fetch.clear()
        reg.fetch.update(fetch_providers)
    if reranker:
        reg.rerankers.clear()
        reg.rerankers["jina_reranker"] = reranker
    reg.all.clear()
    reg.all.update(reg.search)
    reg.all.update(reg.fetch)
    reg.all.update(reg.rerankers)
    health = HealthTracker()
    return Oracle(settings=settings, registry=reg, usage=usage, health=health), health


# ---------------------------------------------------------------------------
# Search routing & fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auto_mode_prefers_free_providers(settings, usage):
    cheap = FakeSearchProvider([make_result("https://c.example/1", "you_mcp")], settings)
    pricey = FakeSearchProvider([make_result("https://p.example/1", "exa")], settings)
    for p, name in ((cheap, "you_mcp"), (pricey, "exa")):
        p.name = name
        p.capabilities = ["general"] if name == "you_mcp" else ["technical", "semantic"]
    oracle, _ = build_oracle(settings, usage, {"you_mcp": cheap, "exa": pricey})
    resp = await oracle.web_search("some ordinary question", mode="auto", max_results=5)
    # you_mcp has no quota limit in config; exa's monthly limit starts consuming.
    assert "you_mcp" in resp.providers_used
    assert any(n.startswith("candidates:") for n in resp.notes)
    await oracle.aclose()


@pytest.mark.asyncio
async def test_fallback_on_provider_failure(settings, usage):
    dead = FakeSearchProvider(ProviderError("429 rate limited", 429, "rate_limit"), settings)
    dead.name = "you_mcp"
    dead.capabilities = ["general"]
    alive = FakeSearchProvider([make_result("https://a.example/1", "searxng", "A")], settings)
    alive.name = "searxng"
    alive.capabilities = ["general", "chinese"]
    oracle, health = build_oracle(settings, usage, {"you_mcp": dead, "searxng": alive})
    resp = await oracle.web_search("normal query", mode="auto", max_results=5)
    assert "searxng" in resp.providers_used
    assert any("you_mcp" in n and "failed" in n for n in resp.notes)
    assert health.state("you_mcp") == HealthState.DEGRADED
    await oracle.aclose()


@pytest.mark.asyncio
async def test_quota_exhaustion_blocks_provider(settings, usage):
    # Burn exa's full monthly limit.
    usage.record("exa", endpoint="search", success=True, credits=1000, query="old")
    exa = FakeSearchProvider([make_result("https://e.example/1", "exa")], settings)
    exa.name = "exa"
    exa.capabilities = ["technical"]
    searx = FakeSearchProvider([make_result("https://s.example/1", "searxng")], settings)
    searx.name = "searxng"
    searx.capabilities = ["general", "chinese"]
    oracle, _ = build_oracle(settings, usage, {"exa": exa, "searxng": searx})
    resp = await oracle.web_search("niche technical question", mode="high", max_results=5)
    assert "exa" not in resp.providers_used
    assert any("quota" in n for n in resp.notes)
    await oracle.aclose()


@pytest.mark.asyncio
async def test_high_mode_multi_provider_and_rerank_fallback(settings, usage):
    a = FakeSearchProvider([make_result("https://a.example/1", "exa", "AAA",
                                        snippet="rust async runtime deep dive")], settings)
    a.name = "exa"; a.capabilities = ["technical", "semantic"]
    b = FakeSearchProvider([make_result("https://b.example/1", "tavily", "BBB",
                                        snippet="rust tokio explained")], settings)
    b.name = "tavily"; b.capabilities = ["general"]
    oracle, _ = build_oracle(settings, usage, {"exa": a, "tavily": b})
    resp = await oracle.web_search("rust async runtime internals benchmark",
                                   mode="high", max_results=10)
    assert {"exa", "tavily"} <= set(resp.providers_used)
    # No Jina key => rerank skipped gracefully, agreement ordering still works.
    assert any("rerank" in n for n in resp.notes)
    await oracle.aclose()


@pytest.mark.asyncio
async def test_chinese_query_boosts_searxng(settings, usage):
    exa = FakeSearchProvider([make_result("https://e.example/1", "exa")], settings)
    exa.name = "exa"; exa.capabilities = ["technical", "semantic"]
    searx = FakeSearchProvider([make_result("https://s.example/1", "searxng", "国产大模型")],
                               settings)
    searx.name = "searxng"; searx.capabilities = ["general", "chinese"]
    you = FakeSearchProvider([], settings)
    you.name = "you_mcp"; you.capabilities = ["general"]
    oracle, _ = build_oracle(settings, usage,
                             {"exa": exa, "searxng": searx, "you_mcp": you})
    resp = await oracle.web_search("微信公众号排版工具推荐", mode="auto", max_results=5)
    assert "searxng" in resp.providers_used
    await oracle.aclose()


@pytest.mark.asyncio
async def test_all_providers_down_still_returns_response(settings, usage):
    dead1 = FakeSearchProvider(ProviderError("timeout", kind="timeout"), settings)
    dead1.name = "you_mcp"; dead1.capabilities = ["general"]
    dead2 = FakeSearchProvider(ProviderError("boom"), settings)
    dead2.name = "searxng"; dead2.capabilities = ["general", "chinese"]
    oracle, _ = build_oracle(settings, usage, {"you_mcp": dead1, "searxng": dead2})
    resp = await oracle.web_search("anything", mode="auto", max_results=5)
    assert resp.results == []
    assert len(resp.notes) >= 2  # explained, not crashed
    await oracle.aclose()


# ---------------------------------------------------------------------------
# Fetch ladder
# ---------------------------------------------------------------------------

def _doc(url: str, provider: str) -> FetchedDocument:
    return FetchedDocument(url=url, content=f"content from {provider}",
                           provider=provider)


@pytest.mark.asyncio
async def test_fetch_ladder_light_first(settings, usage):
    calls = []

    class Light(FakeFetchProvider):
        name = "light_http"
        async def fetch(self, url, **opts):
            calls.append(self.name)
            return _doc(url, self.name)

    class Jina(FakeFetchProvider):
        name = "jina_reader"
        async def fetch(self, url, **opts):
            calls.append(self.name)
            return _doc(url, self.name)

    fr = FetchRouter(settings=settings, usage=usage, registry=Registry(settings))
    fr.registry.fetch.clear()
    fr.registry.fetch.update({"light_http": Light(None, settings),
                              "jina_reader": Jina(None, settings)})
    fr.registry.all.clear(); fr.registry.all.update(fr.registry.fetch)
    doc = await fr.fetch("https://static.example/page")
    assert calls == ["light_http"]
    assert doc.provider == "light_http"


@pytest.mark.asyncio
async def test_fetch_ladder_escalates_on_failure(settings, usage):
    calls = []

    class Fail(FakeFetchProvider):
        def __init__(self, name):
            super().__init__(None, settings)
            self.name = name
        async def fetch(self, url, **opts):
            calls.append(self.name)
            raise ProviderError(f"{self.name} blocked", 403, "error")

    class C4(FakeFetchProvider):
        name = "crawl4ai"
        async def fetch(self, url, **opts):
            calls.append(self.name)
            return _doc(url, "crawl4ai")

    class Fire(FakeFetchProvider):
        name = "firecrawl"
        async def fetch(self, url, **opts):
            calls.append(self.name)
            return _doc(url, "firecrawl")

    fr = FetchRouter(settings=settings, usage=usage, registry=Registry(settings))
    fr.registry.fetch.clear()
    fr.registry.fetch.update({
        "light_http": Fail("light_http"),
        "jina_reader": Fail("jina_reader"),
        "crawl4ai": C4(None, settings),
        "firecrawl": Fire(None, settings),
    })
    fr.registry.all.clear(); fr.registry.all.update(fr.registry.fetch)
    doc = await fr.fetch("https://hard.example/page")
    assert calls == ["light_http", "jina_reader", "crawl4ai"]
    assert doc.provider == "crawl4ai"  # firecrawl NOT burned


@pytest.mark.asyncio
async def test_fetch_known_hard_hosts_skip_light(settings, usage):
    class C4(FakeFetchProvider):
        name = "crawl4ai"
        async def fetch(self, url, **opts):
            return _doc(url, "crawl4ai")

    fr = FetchRouter(settings=settings, usage=usage, registry=Registry(settings))
    fr.registry.fetch.clear()
    fr.registry.fetch.update({"crawl4ai": C4(None, settings)})
    fr.registry.all.clear(); fr.registry.all.update(fr.registry.fetch)
    doc = await fr.fetch("https://x.com/someone/status/123")
    assert doc.provider == "crawl4ai"  # light_http never tried


@pytest.mark.asyncio
async def test_fetch_force_provider_accepts_short_alias(settings, usage):
    class Jina(FakeFetchProvider):
        name = "jina_reader"
        async def fetch(self, url, **opts):
            return _doc(url, "jina_reader")

    fr = FetchRouter(settings=settings, usage=usage, registry=Registry(settings))
    fr.registry.fetch.clear()
    fr.registry.fetch.update({"jina_reader": Jina(None, settings)})
    fr.registry.all.clear(); fr.registry.all.update(fr.registry.fetch)
    doc = await fr.fetch("https://example.com/a", force_provider="jina")
    assert doc.provider == "jina_reader"


@pytest.mark.asyncio
async def test_fetch_unknown_provider_reports_unknown_not_unconfigured(settings, usage):
    class Jina(FakeFetchProvider):
        name = "jina_reader"
        async def fetch(self, url, **opts):
            return _doc(url, "jina_reader")

    fr = FetchRouter(settings=settings, usage=usage, registry=Registry(settings))
    fr.registry.fetch.clear()
    fr.registry.fetch.update({"jina_reader": Jina(None, settings)})
    fr.registry.all.clear(); fr.registry.all.update(fr.registry.fetch)
    with pytest.raises(ProviderError) as ei:
        await fr.fetch("https://example.com/a", force_provider="nope")
    assert ei.value.kind == "all_failed"
    assert "nope: unknown provider" in str(ei.value)
    assert "registered: jina_reader" in str(ei.value)
    assert "not configured" not in str(ei.value)


@pytest.mark.asyncio
async def test_fetch_unconfigured_provider_still_reports_not_configured(settings, usage):
    class Jina(FakeFetchProvider):
        name = "jina_reader"
        @property
        def configured(self) -> bool:
            return False
        async def fetch(self, url, **opts):
            raise AssertionError("must not be called when unconfigured")

    fr = FetchRouter(settings=settings, usage=usage, registry=Registry(settings))
    fr.registry.fetch.clear()
    fr.registry.fetch.update({"jina_reader": Jina(None, settings)})
    fr.registry.all.clear(); fr.registry.all.update(fr.registry.fetch)
    with pytest.raises(ProviderError) as ei:
        await fr.fetch("https://example.com/a", force_provider="jina_reader")
    assert "jina_reader: not configured" in str(ei.value)


@pytest.mark.asyncio
async def test_fetch_url_reuses_search_content(settings, usage):
    oracle, _ = build_oracle(settings, usage, {}, {})
    doc = await oracle.fetch_url("https://already-have.example/page",
                                 search_content="x" * 2000)
    assert doc.provider == "search_cache"  # no quota spent
    await oracle.aclose()


@pytest.mark.asyncio
async def test_fetch_target_failure_does_not_degrade_provider(settings, usage):
    class TargetFail(FakeFetchProvider):
        name = "light_http"
        async def fetch(self, url, **opts):
            raise ProviderError("target page http 404", 404, "target", affects_health=False)

    class Jina(FakeFetchProvider):
        name = "jina_reader"
        async def fetch(self, url, **opts):
            return _doc(url, "jina_reader")

    fr = FetchRouter(settings=settings, usage=usage, registry=Registry(settings))
    fr.registry.fetch.clear()
    fr.registry.fetch.update({"light_http": TargetFail(None, settings),
                              "jina_reader": Jina(None, settings)})
    fr.registry.all.clear(); fr.registry.all.update(fr.registry.fetch)
    doc = await fr.fetch("https://dead.example/page")
    assert doc.provider == "jina_reader"  # ladder still escalates
    assert fr.health.state("light_http") == HealthState.HEALTHY
    assert fr.health.state("jina_reader") == HealthState.HEALTHY


@pytest.mark.asyncio
async def test_fetch_provider_failure_still_degrades(settings, usage):
    class ServerFail(FakeFetchProvider):
        name = "light_http"
        async def fetch(self, url, **opts):
            raise ProviderError("server error 502", 502, "server")

    class Jina(FakeFetchProvider):
        name = "jina_reader"
        async def fetch(self, url, **opts):
            return _doc(url, "jina_reader")

    fr = FetchRouter(settings=settings, usage=usage, registry=Registry(settings))
    fr.registry.fetch.clear()
    fr.registry.fetch.update({"light_http": ServerFail(None, settings),
                              "jina_reader": Jina(None, settings)})
    fr.registry.all.clear(); fr.registry.all.update(fr.registry.fetch)
    await fr.fetch("https://example.com/page")
    assert fr.health.state("light_http") == HealthState.DEGRADED
    assert fr.health.state("jina_reader") == HealthState.HEALTHY


# ---------------------------------------------------------------------------
# Research / status / crawl
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_research_builds_evidence_pack(settings, usage):
    a = FakeSearchProvider([make_result("https://r.example/deep", "exa", "Deep",
                                        content="full body text" * 300)], settings)
    a.name = "exa"; a.capabilities = ["technical", "semantic"]
    oracle, _ = build_oracle(settings, usage, {"exa": a}, {})
    resp = await oracle.web_research("deep research question", max_results=5,
                                     fetch_top=1)
    assert resp.mode == "max"
    assert resp.key_sources and resp.citations
    assert all(c.url.startswith("https://") for c in resp.citations)
    assert resp.evidence[0].provider == "search_cache"  # content reused, no fetch spent
    await oracle.aclose()


def test_status_never_leaks_keys(settings, usage):
    oracle, _ = build_oracle(settings, usage, {}, {})
    st = oracle.status()
    s = str(st.model_dump())
    assert "test-exa-key" not in s and "test-tavily-key" not in s
    by_name = {p.provider: p for p in st.providers}
    assert by_name["searxng"].configured is True
    assert by_name["firecrawl"].configured is False
    assert by_name["exa"].quota.quota_type == QuotaType.MONTHLY


@pytest.mark.asyncio
async def test_crawl_site_prefers_crawl4ai(settings, usage):
    class C4(FakeFetchProvider):
        name = "crawl4ai"
        async def crawl_site(self, root_url, max_pages=10, max_depth=2):
            from magicalgirl_web_oracle.models.schemas import CrawlPage, CrawlResponse
            return CrawlResponse(root_url=root_url,
                                 pages=[CrawlPage(url=root_url, content="c4 content")],
                                 provider="crawl4ai")

    class Fire(FakeFetchProvider):
        name = "firecrawl"
        async def crawl_site(self, root_url, max_pages=10, max_depth=2):
            raise AssertionError("firecrawl must not be used when crawl4ai works")

    oracle, _ = build_oracle(settings, usage, {},
                             {"crawl4ai": C4(None, settings),
                              "firecrawl": Fire(None, settings)})
    resp = await oracle.crawl_site("https://site.example/")
    assert resp.provider == "crawl4ai"
    await oracle.aclose()
