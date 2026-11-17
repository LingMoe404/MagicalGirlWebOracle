"""Shared fixtures: isolated settings/usage DB and fake providers."""

from __future__ import annotations

import pytest

from magicalgirl_web_oracle.config.settings import Settings
from magicalgirl_web_oracle.quota.usage_db import UsageDB
from magicalgirl_web_oracle.health.tracker import HealthTracker
from magicalgirl_web_oracle.models.schemas import FetchedDocument, SearchResult
from magicalgirl_web_oracle.providers.base import FetchProvider, ProviderError, SearchProvider


class DummySettings(Settings):
    """No env vars, tiny config — safe for tests."""

    def __init__(self) -> None:
        super().__init__()
        self.exa_api_key = "test-exa-key"
        self.tavily_api_key = "test-tavily-key"
        self.firecrawl_api_key = ""
        self.jina_api_key = ""
        self.ydc_api_key = ""
        self.searxng_url = "http://mock-searxng.local"
        self.crawl4ai_url = ""
        self.allow_paid_overage = False
        self.default_mode = "auto"
        self.store_query_text = False
        self.jina_keyless_fallback = False
        # Never inherit a developer's live multi-account profile or vault.
        self.accounts_file = ''
        self.vault_key = ''
        self.admin_auth_token = ''


@pytest.fixture()
def settings(tmp_path) -> DummySettings:
    s = DummySettings()
    s.usage_db_path = str(tmp_path / "usage.sqlite3")
    return s


@pytest.fixture()
def usage(settings) -> UsageDB:
    db = UsageDB(settings.usage_db_path)
    yield db
    db.close()


@pytest.fixture()
def health() -> HealthTracker:
    return HealthTracker()


def make_result(url: str, provider: str, title: str = "T", snippet: str = "s",
                content: str = "", **kw) -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet, content=content,
                        provider=provider, **kw)


class FakeSearchProvider(SearchProvider):
    name = "fake"
    capabilities = ["general"]

    def __init__(self, results: list[SearchResult] | Exception, settings=None) -> None:
        super().__init__(settings)
        self._results = results
        self.calls: list[str] = []

    async def search(self, query, *, max_results=10, mode="auto", **opts):
        self.calls.append(query)
        if isinstance(self._results, Exception):
            raise self._results
        return self._results[:max_results]


class FakeFetchProvider(FetchProvider):
    name = "fake_fetch"

    def __init__(self, doc: FetchedDocument | Exception, settings=None) -> None:
        super().__init__(settings)
        self._doc = doc

    async def fetch(self, url, **opts):
        if isinstance(self._doc, Exception):
            raise self._doc
        return self._doc


@pytest.fixture()
def fake_ok_provider(settings) -> FakeSearchProvider:
    return FakeSearchProvider([
        make_result("https://example.com/a", "fake", "Result A"),
        make_result("https://example.com/b", "fake", "Result B"),
    ], settings)


@pytest.fixture()
def fake_rate_limited(settings) -> FakeSearchProvider:
    return FakeSearchProvider(ProviderError("429: too many", 429, "rate_limit"), settings)


__all__ = ["DummySettings", "FakeSearchProvider", "FakeFetchProvider",
           "make_result", "ProviderError"]
