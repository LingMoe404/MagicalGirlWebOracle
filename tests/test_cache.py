"""Response cache: TTL, bypass, freshness and query redaction."""
from __future__ import annotations

import sqlite3
import time

import pytest

from magicalgirl_web_oracle.cache import ResponseCache, is_fresh_query
from magicalgirl_web_oracle.quota.usage_db import UsageDB
from magicalgirl_web_oracle.router.oracle import Oracle

from conftest import FakeFetchProvider, FakeSearchProvider, make_result
from test_router import build_oracle


def cache_settings(settings, tmp_path, *, search=60, fetch=60):
    settings.cache_ttl_seconds = search
    settings.fetch_cache_ttl_seconds = fetch
    settings.cache_path = str(tmp_path / "cache.sqlite3")
    return settings


def test_search_cache_serves_without_second_provider_call(settings, usage, tmp_path):
    cache_settings(settings, tmp_path)
    provider = FakeSearchProvider([make_result("https://a.example/1", "searxng")], settings)
    provider.name = "searxng"
    provider.capabilities = ["general", "chinese"]
    oracle, _ = build_oracle(settings, usage, {"searxng": provider})
    try:
        async def run():
            first = await oracle.web_search("cache test ordinary query", mode="fast", max_results=5)
            second = await oracle.web_search("cache test ordinary query", mode="fast", max_results=5)
            return first, second
        import asyncio
        first, second = asyncio.run(run())
        assert len(provider.calls) == 1
        assert first.query == second.query == "cache test ordinary query"
        assert any("response cache" in note for note in second.notes)
    finally:
        import asyncio
        asyncio.run(oracle.aclose())


def test_cache_payload_does_not_contain_query_text(settings, usage, tmp_path):
    cache_settings(settings, tmp_path)
    provider = FakeSearchProvider([make_result("https://a.example/1", "searxng")], settings)
    provider.name = "searxng"
    provider.capabilities = ["general", "chinese"]
    oracle, _ = build_oracle(settings, usage, {"searxng": provider})
    query = "secret-ish cache query 92831"
    try:
        import asyncio
        asyncio.run(oracle.web_search(query, mode="fast", max_results=5))
        con = sqlite3.connect(settings.cache_path)
        payload = con.execute("SELECT payload FROM cache_entries").fetchone()[0]
        keys = con.execute("SELECT key FROM cache_entries").fetchall()
        con.close()
        assert query not in payload
        assert all(query not in row[0] for row in keys)
    finally:
        import asyncio
        asyncio.run(oracle.aclose())


def test_freshness_query_and_bypass_always_hit_provider(settings, usage, tmp_path):
    cache_settings(settings, tmp_path)
    provider = FakeSearchProvider([make_result("https://a.example/1", "searxng")], settings)
    provider.name = "searxng"
    provider.capabilities = ["general", "chinese"]
    oracle, _ = build_oracle(settings, usage, {"searxng": provider})
    try:
        import asyncio
        async def run():
            await oracle.web_search("latest Python release", mode="fast")
            await oracle.web_search("latest Python release", mode="fast")
            await oracle.web_search("ordinary query", mode="fast", bypass_cache=True)
            await oracle.web_search("ordinary query", mode="fast", bypass_cache=True)
        asyncio.run(run())
        assert len(provider.calls) == 4
    finally:
        import asyncio
        asyncio.run(oracle.aclose())


def test_fetch_cache_and_bypass(settings, usage, tmp_path):
    cache_settings(settings, tmp_path)
    from magicalgirl_web_oracle.models.schemas import FetchedDocument

    class CountingFetch(FakeFetchProvider):
        def __init__(self, doc, settings=None):
            super().__init__(doc, settings)
            self.calls = []

        async def fetch(self, url, **opts):
            self.calls.append(url)
            return await super().fetch(url, **opts)

    provider = CountingFetch(FetchedDocument(
        url="https://example.com/page", content="cached content", provider="light_http"), settings)
    provider.name = "light_http"
    oracle, _ = build_oracle(settings, usage, {}, {"light_http": provider})
    try:
        import asyncio
        async def run():
            one = await oracle.fetch_url("https://example.com/page")
            two = await oracle.fetch_url("https://example.com/page")
            three = await oracle.fetch_url("https://example.com/page", bypass_cache=True)
            return one, two, three
        _, two, _ = asyncio.run(run())
        assert two.metadata.get("cache_hit") is True
        assert len(provider.calls) == 2
    finally:
        import asyncio
        asyncio.run(oracle.aclose())


def test_cache_expiry_and_max_entries(tmp_path):
    cache = ResponseCache(tmp_path / "cache.sqlite3", max_entries=10)
    material = cache.search_material("q", mode="auto", max_results=5,
                                     provider_override=None, opts={})
    assert cache.store("search", material, {"ok": True}, 60)
    assert cache.lookup("search", material)[0] == {"ok": True}
    cache.db.execute("UPDATE cache_entries SET expires=?", (time.time() - 1,))
    assert cache.lookup("search", material)[0] is None
    for index in range(15):
        item = cache.search_material(f"q{index}", mode="auto", max_results=5,
                                     provider_override=None, opts={})
        cache.store("search", item, {"index": index}, 60)
    assert cache.db.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0] <= 10
    cache.close()


def test_freshness_markers():
    assert is_fresh_query("latest Python release")
    assert is_fresh_query("今天 天气")
    assert not is_fresh_query("SQLite backup API documentation")


def test_cache_hit_is_recorded_in_request_trace(settings, usage, tmp_path):
    cache_settings(settings, tmp_path)
    from test_key_pool import runtime
    cfg, ledger, executor = runtime(tmp_path, settings)
    provider = FakeSearchProvider([make_result("https://a.example/1", "searxng")], settings)
    provider.name = "searxng"
    provider.capabilities = ["general", "chinese"]
    oracle, _ = build_oracle(settings, usage, {"searxng": provider})
    oracle.registry.credentials = executor
    try:
        import asyncio
        async def run():
            await oracle.web_search("trace cache query", mode="fast")
            await oracle.web_search("trace cache query", mode="fast")
        asyncio.run(run())
        trace = ledger.traces(limit=1)[0]
        detail = ledger.trace_detail(trace["id"])
        assert any(event["event"] == "reused" and event["details"].get("reason") == "cache_hit"
                   for event in detail["events"])
    finally:
        import asyncio
        asyncio.run(oracle.aclose())
