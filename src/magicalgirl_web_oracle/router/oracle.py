"""The Oracle — single facade the MCP tools / CLI call.

web_search / web_research / fetch_url / crawl_site / search_status all route
through here so every entry point shares quota, health, and dedup state.
"""

from __future__ import annotations

import logging
from typing import Any

from ..cache import from_settings as cache_from_settings
from ..config.settings import Settings, get_settings
from ..dedup.canonical import canonicalize_url
from ..models.schemas import (
    CrawlResponse,
    FetchedDocument,
    ProviderStatus,
    ProviderKind,
    QuotaSnapshot,
    QuotaType,
    ResearchResponse,
    SearchResponse,
    SearchResult,
    SystemStatus,
    Citation,
)
from ..providers.base import ProviderError
from ..providers.registry import Registry
from ..quota.usage_db import UsageDB
from ..quota.tracing import emit, traced
from ..health.tracker import HealthTracker
from .fetch_router import FetchRouter
from .search_router import SearchRouter

log = logging.getLogger(__name__)


class Oracle:
    def __init__(self, *, settings=None, registry: Registry | None = None,
                 usage: UsageDB | None = None, health: HealthTracker | None = None) -> None:
        self.settings = settings or get_settings()
        self.usage = usage or UsageDB(self.settings.usage_db_path)
        self.health = health or HealthTracker()
        self.registry = registry or Registry(self.settings)
        self.cache = cache_from_settings(self.settings)
        self.search_router = SearchRouter(
            self.registry, settings=self.settings, usage=self.usage,
            health=self.health, cache=self.cache)
        self.fetch_router = FetchRouter(
            self.registry, settings=self.settings, usage=self.usage,
            health=self.health, cache=self.cache)

    # ------------------------------------------------------------------
    # web_search
    # ------------------------------------------------------------------

    @traced("web_search")
    async def web_search(self, query: str, *, mode: str | None = None,
                         max_results: int = 10,
                         provider_override: str | None = None,
                         bypass_cache: bool = False, **opts: Any) -> SearchResponse:
        self.registry.refresh_credentials()
        return await self.search_router.search(
            query, mode=mode, max_results=max_results,
            provider_override=provider_override,
            bypass_cache=bypass_cache, **opts)

    # ------------------------------------------------------------------
    # fetch_url — smart: reuse search-supplied content, else escalate chain
    # ------------------------------------------------------------------

    @traced("fetch_url")
    async def fetch_url(self, url: str, *, force_provider: str | None = None,
                        search_content: str | None = None,
                        bypass_cache: bool = False, **opts: Any) -> FetchedDocument:
        self.registry.refresh_credentials()
        url_c = canonicalize_url(url)
        if force_provider:
            return await self.fetch_router.fetch(
                url_c, force_provider=force_provider,
                bypass_cache=bypass_cache, **opts)
        # If a previous search already gave us substantial content, don't burn quota.
        if search_content and len(search_content) >= 1200:
            emit("reused",provider="search_cache",reason="search_content")
            return FetchedDocument(
                url=url_c, content=search_content, content_type="text",
                provider="search_cache",
                metadata={"reason": "content already supplied by search provider"})
        return await self.fetch_router.fetch(url_c, bypass_cache=bypass_cache, **opts)

    # ------------------------------------------------------------------
    # crawl_site
    # ------------------------------------------------------------------

    @traced("crawl_site")
    async def crawl_site(self, root_url: str, *, max_pages: int = 10,
                         max_depth: int = 2) -> CrawlResponse:
        self.registry.refresh_credentials()
        # Self-hosted Crawl4AI first (unlimited); Firecrawl credits last.
        for name in ("crawl4ai", "firecrawl"):
            emit("candidate",provider=name,operation="crawl_site",reason="crawl_order")
            prov = self.registry.fetch.get(name)
            if prov is None or not prov.configured or (not getattr(prov,"managed_credentials",False) and not self.health.is_usable(name)):
                emit("skipped",provider=name,operation="crawl_site",reason="not_configured" if prov is None or not prov.configured else "unhealthy")
                continue
            try:
                crawler = getattr(prov, "crawl_site", None)
                if crawler is None:
                    continue
                emit("selected",provider=name,operation="crawl_site",reason="crawl_order")
                resp = await crawler(root_url, max_pages=max_pages, max_depth=max_depth)
                self.usage.record(name, endpoint="crawl", success=True, credits=0.0
                                  if name == "crawl4ai" else float(max_pages),
                                  query=root_url)
                emit("settled",provider=name,operation="crawl_site")
                return resp
            except ProviderError as e:
                emit("failed",provider=name,operation="crawl_site",reason=e.kind)
                if e.affects_health:
                    self.health.record_failure(name, str(e))
                continue
        raise ProviderError(
            "crawl_site failed: no usable crawl backend "
            "(need CRAWL4AI_URL or FIRECRAWL_API_KEY)", kind="all_failed")

    # ------------------------------------------------------------------
    # web_research — Evidence Pack assembly (citations always real URLs)
    # ------------------------------------------------------------------

    @traced("web_research")
    async def web_research(self, query: str, *, max_results: int = 12,
                           fetch_top: int = 3,
                           bypass_cache: bool = False, **opts: Any) -> ResearchResponse:
        self.registry.refresh_credentials()
        search = await self.search_router.search(
            query, mode="max", max_results=max_results,
            bypass_cache=bypass_cache, **opts)

        key_sources = search.results[: max(5, max_results // 2)]
        citations = [
            Citation(url=r.url, title=r.title, snippet=r.snippet[:300],
                     provider=r.provider, source="search")
            for r in key_sources
        ]

        evidence: list[FetchedDocument] = []
        used_urls: set[str] = set()
        for r in search.results[:fetch_top]:
            try:
                doc = await self.fetch_url(
                    r.url, search_content=r.content, bypass_cache=bypass_cache)
                evidence.append(doc)
                used_urls.add(canonicalize_url(r.url))
                citations.append(Citation(url=doc.url, title=doc.title or r.title,
                                          snippet=doc.content[:300],
                                          provider=doc.provider, source="fetch"))
            except ProviderError as e:
                search.notes.append(f"evidence fetch failed for {r.url}: {e.kind}")

        # Cross-provider agreement summary.
        agreement: dict[str, float] = {}
        for r in search.results:
            n = len(set(r.found_by))
            if n > 1:
                agreement[r.url] = round(min(1.0, n / 3.0), 3)

        # Conflicts: same host family disagreeing is hard to detect cheaply;
        # flag when top sources have diverging published dates on same topic.
        conflicts: list[str] = []
        dates = [r.published_at for r in key_sources if r.published_at]
        if len(set(dates)) > 1 and len(dates) >= 2:
            conflicts.append(
                "top sources carry differing publication dates — verify freshness")

        return ResearchResponse(
            query=query, mode="max", providers_used=search.providers_used,
            key_sources=key_sources, citations=citations, evidence=evidence,
            conflicts=conflicts, cross_provider_agreement=agreement,
            usage=search.usage, notes=search.notes,
        )

    # ------------------------------------------------------------------
    # search_status — never leaks keys
    # ------------------------------------------------------------------

    def status(self) -> SystemStatus:
        self.registry.refresh_credentials()
        providers: list[ProviderStatus] = []
        for name, prov in self.registry.all.items():
            cfg = self.settings.providers_cfg["providers"].get(name, {})
            qcfg = cfg.get("quota", {})
            qtype = QuotaType(qcfg.get("type", "unknown"))
            limit = qcfg.get("limit")
            unit = qcfg.get("unit", "credits")
            snap = self.usage.quota_snapshot(name, qtype, limit,
                                             bool(qcfg.get("renewing")), unit=unit)
            if getattr(prov,"managed_credentials",False):
                snap = prov.quota_snapshot()
            stats = self.usage.stats_24h(name)
            providers.append(ProviderStatus(
                provider=name,
                kind=prov.kind,
                health=self.health.state(name),
                configured=prov.configured,
                quota=snap,
                avg_latency_ms=stats.get("avg_latency_ms"),
                success_rate_24h=stats.get("success_rate"),
                last_error=self.health.last_error(name),
                capabilities=list(getattr(prov, "capabilities", [])),
            ))
        alerts=[]
        monitor=None
        scheduling=None
        if self.registry.credentials is not None:
            executor=self.registry.credentials
            grouped={}
            for alert in executor.alerts.evaluate():
                if alert['dismissed_at'] is None:
                    identity=(alert['kind'],alert['severity'])
                    grouped[identity]=grouped.get(identity,0)+1
            alerts=[{'kind':kind,'severity':severity,'count':count} for (kind,severity),count in sorted(grouped.items())]
            monitor=getattr(executor,'scheduler_status',None)
            scheduling=executor.scheduling_info() if hasattr(executor,'scheduling_info') else None
        return SystemStatus(providers=providers,allow_paid_overage=self.settings.allow_paid_overage,
                            quota_alerts=alerts,quota_monitor=monitor,
                            cache=self.cache_stats(),scheduling=scheduling)

    def cache_stats(self) -> dict[str, Any]:
        if self.cache is None:
            return {"enabled": False, "entries": 0, "hits": 0, "misses": 0,
                    "search_ttl_seconds": int(getattr(self.settings, "cache_ttl_seconds", 0) or 0),
                    "fetch_ttl_seconds": int(getattr(self.settings, "fetch_cache_ttl_seconds", 0) or 0)}
        stats = self.cache.stats()
        stats["search_ttl_seconds"] = int(getattr(self.settings, "cache_ttl_seconds", 0) or 0)
        stats["fetch_ttl_seconds"] = int(getattr(self.settings, "fetch_cache_ttl_seconds", 0) or 0)
        return stats

    async def live_balances(self) -> dict[str, dict[str, Any]]:
        """Query official balance APIs where available (Tavily/Firecrawl/You.com).
        Used by `doctor` to reconcile local bookkeeping against reality."""
        import asyncio
        self.registry.refresh_credentials()
        out: dict[str, dict[str, Any]] = {}
        if self.registry.credentials is not None:
            for result in await self.registry.credentials.balance_sync.run():
                account=result["account_id"]
                out[f"account:{account}"]={**result,"balance":self.registry.credentials.ledger.balances().get(account)}
        tasks = {}
        for name, prov in self.registry.all.items():
            fn = getattr(prov, "fetch_balance", None)
            if fn and prov.configured:
                tasks[name] = asyncio.create_task(fn())
        for name, task in tasks.items():
            try:
                bal = await task
                if bal:
                    out[name] = bal
            except Exception as e:
                out[name] = {"error": str(e)[:120]}
        return out

    async def aclose(self) -> None:
        await self.registry.aclose()
        self.usage.close()
        if self.cache is not None:
            self.cache.close()
