"""Provider interface. All backends implement SearchProvider / FetchProvider / Reranker."""

from __future__ import annotations

import abc
import logging
import time
from typing import Any

import httpx

from ..config.settings import Settings, get_settings
from ..models.schemas import (
    FetchedDocument,
    HealthState,
    ProviderKind,
    QuotaSnapshot,
    SearchResult,
)

log = logging.getLogger(__name__)


class ProviderError(Exception):
    """Base error with HTTP-ish classification for health tracking."""

    def __init__(self, message: str, status_code: int | None = None,
                 kind: str = "error", *, affects_health: bool = True) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.kind = kind  # error | auth | rate_limit | timeout | server | quota | target
        # A target-level failure (dead URL, anti-bot page) says nothing about
        # the provider's own availability, so routers must not trip its breaker.
        self.affects_health = affects_health

    @staticmethod
    def from_http(status_code: int, body: str, *,
                  affects_health: bool = True) -> "ProviderError":
        """Build a classified error from an HTTP response status."""
        if status_code in (401, 403):
            return ProviderError(f"auth error {status_code}: {body[:200]}", status_code, "auth",
                                 affects_health=affects_health)
        if status_code == 429:
            return ProviderError(f"rate limited 429: {body[:200]}", status_code, "rate_limit",
                                 affects_health=affects_health)
        if status_code == 402:
            # Payment required => treat as quota exhausted; NEVER trigger payment.
            return ProviderError(f"payment required 402: {body[:200]}", status_code, "quota",
                                 affects_health=affects_health)
        if status_code >= 500:
            return ProviderError(f"server error {status_code}: {body[:200]}", status_code, "server",
                                 affects_health=affects_health)
        return ProviderError(f"http {status_code}: {body[:200]}", status_code, "error",
                             affects_health=affects_health)

    @staticmethod
    def target_http(status_code: int, body: str, what: str = "target") -> "ProviderError":
        """A provider-side rejection caused by the requested URL/page itself."""
        return ProviderError(
            f"{what} http {status_code}: {body[:200]}",
            status_code, "target", affects_health=False,
        )

    # Alias kept for readability at call sites.
    map_http_error = from_http


class QuotaExhaustedError(ProviderError):
    """Raised when a provider's tracked free quota is used up until reset."""

    def __init__(self, provider: str, reset_at: float | None = None) -> None:
        super().__init__(
            f"free quota exhausted for '{provider}'",
            kind="quota",
        )
        self.provider = provider
        self.reset_at = reset_at


class BaseProvider(abc.ABC):
    """Common plumbing: name, settings, httpx client, error mapping."""

    name: str = "base"
    kind: ProviderKind = ProviderKind.SEARCH
    # Capabilities used by router query-fit scoring.
    capabilities: list[str] = []
    # True => talk to a LAN service (SearXNG/Crawl4AI): bypass the proxy.
    is_local: bool = False

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        """Whether we have what we need to call this provider at all."""
        return True

    async def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            kwargs: dict[str, Any] = {
                "timeout": httpx.Timeout(30.0, connect=10.0),
                "headers": {"User-Agent": "MagicalGirlWebOracle/0.1 (personal; zero-cost)"},
                "follow_redirects": True,
            }
            # External providers route through the configured proxy; LAN services
            # (SearXNG/Crawl4AI) must go direct — proxying 10.0.0.x would fail.
            if self.settings.proxy_url and not self.is_local:
                kwargs["proxy"] = self.settings.proxy_url
                kwargs["trust_env"] = False  # use ONLY our configured proxy
            elif self.is_local:
                # Never let HTTP_PROXY/HTTPS_PROXY from the environment hijack
                # requests to a LAN service.
                kwargs["trust_env"] = False
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def quota_snapshot(self) -> QuotaSnapshot | None:
        return None

    def describe(self) -> dict[str, Any]:
        q = self.quota_snapshot()
        return {
            "name": self.name,
            "kind": self.kind.value,
            "configured": self.configured,
            "capabilities": list(self.capabilities),
            "quota": q.model_dump() if q else None,
        }


class SearchProvider(BaseProvider):
    """A backend that turns a query into normalized SearchResults."""

    @abc.abstractmethod
    async def search(self, query: str, *, max_results: int = 10,
                     mode: str = "auto", **opts: Any) -> list[SearchResult]:
        ...


class FetchProvider(BaseProvider):
    """A backend that fetches a URL and returns a clean document."""

    kind: ProviderKind = ProviderKind.FETCH

    @abc.abstractmethod
    async def fetch(self, url: str, **opts: Any) -> FetchedDocument:
        ...


class Reranker(BaseProvider):
    kind: ProviderKind = ProviderKind.RERANK

    @abc.abstractmethod
    async def rerank(self, query: str, results: list[SearchResult],
                     top_n: int = 10) -> list[SearchResult]:
        ...


def now_ms() -> int:
    return int(time.time() * 1000)
