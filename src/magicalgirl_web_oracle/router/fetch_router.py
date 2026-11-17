"""Fetch router — cost-ordered escalation ladder.

light_http (free, static) -> jina_reader (clean markdown; keyless low-rate
if no key) -> crawl4ai (self-hosted, JS pages, unlimited) -> firecrawl
(monthly credits, anti-bot last resort).

Escalation triggers: network error, 403/429/5xx, JS-heavy page detected,
or suspiciously empty content. If a search provider already supplied full
content for the URL, the caller can skip fetching entirely (see Oracle.fetch_url).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..cache import ResponseCache, from_settings
from ..config.settings import Settings, get_settings
from ..health.tracker import HealthTracker
from ..models.schemas import FetchedDocument
from ..providers.base import ProviderError
from ..providers.registry import Registry
from ..quota.usage_db import UsageDB
from ..quota.tracing import emit

log = logging.getLogger(__name__)

# Hosts known to be JS-heavy / anti-bot — skip light_http entirely.
_KNOWN_HARD_HOSTS = (
    "x.com", "twitter.com", "instagram.com", "facebook.com", "linkedin.com",
    "bilibili.com/video", "weixin.qq.com", "mp.weixin.qq.com", "zhihu.com",
)


class FetchRouter:
    ORDER = ["light_http", "jina_reader", "crawl4ai", "firecrawl"]

    # Debug callers tend to type the short brand name ("jina", "crawl"); accept
    # those instead of failing with a misleading "not configured".
    ALIASES = {
        "jina": "jina_reader",
        "reader": "jina_reader",
        "light": "light_http",
        "http": "light_http",
        "crawl": "crawl4ai",
        "fire": "firecrawl",
    }

    @classmethod
    def resolve_name(cls, name: str) -> str:
        key = name.strip().lower().replace("-", "_")
        return cls.ALIASES.get(key, key)

    def __init__(self, registry: Registry | None = None, *,
                 settings: Settings | None = None,
                 usage: UsageDB | None = None,
                 health: HealthTracker | None = None,
                 cache: ResponseCache | None = None) -> None:
        self.settings = settings or get_settings()
        self.registry = registry or Registry(self.settings)
        self.usage = usage or UsageDB()
        self.health = health or HealthTracker()
        self.cache = cache if cache is not None else from_settings(self.settings)

    async def fetch(self, url: str, *, skip: set[str] | None = None,
                    force_provider: str | None = None,
                    bypass_cache: bool = False, **opts: Any) -> FetchedDocument:
        skip = {self.resolve_name(s) for s in (skip or set())}
        ttl = int(getattr(self.settings, "fetch_cache_ttl_seconds", 0) or 0)
        cacheable = bool(self.cache and ttl > 0 and not bypass_cache)
        material = (ResponseCache.fetch_material(
            url, force_provider=force_provider, skip=skip) if self.cache else None)
        if self.cache and bypass_cache:
            emit("skipped", provider="fetch_cache", operation="fetch",
                 reason="cache_bypass")
        if cacheable and material is not None:
            payload, age = self.cache.lookup("fetch", material)
            if payload is not None:
                try:
                    cached = FetchedDocument.model_validate(payload)
                except ValueError:
                    cached = None
                if cached is not None:
                    cached.url = url
                    cached.metadata["cache_hit"] = True
                    cached.metadata["cache_age_seconds"] = int(age or 0)
                    emit("reused", provider="fetch_cache", operation="fetch",
                         reason="cache_hit")
                    return cached
        chain = ([self.resolve_name(force_provider)] if force_provider
                 else self._chain_for(url))
        errors: list[str] = []
        for name in chain:
            emit("candidate",provider=name,operation="fetch",reason="manual" if force_provider else "fetch_order")
            if name in skip or name is None:
                emit("skipped",provider=name,reason="caller_skip")
                continue
            prov = self.registry.fetch.get(name)
            if prov is None:
                emit("skipped",operation="fetch",reason="no_backend")
                errors.append(
                    f"{name}: unknown provider "
                    f"(registered: {', '.join(sorted(self.registry.fetch))})")
                continue
            if not prov.configured:
                emit("skipped",provider=name,reason="not_configured")
                errors.append(f"{name}: not configured")
                continue
            if not getattr(prov,"managed_credentials",False) and not self.health.is_usable(name):
                emit("skipped",provider=name,reason="unhealthy")
                errors.append(f"{name}: unhealthy")
                continue
            t0 = time.time()
            try:
                emit("selected",provider=name,operation="fetch",reason="manual" if force_provider else "fetch_order")
                doc = await prov.fetch(url, **opts)
                self.health.record_success(name)
                self.usage.record(name, endpoint="fetch", success=True,
                                  latency_ms=int((time.time() - t0) * 1000),
                                  credits=0.0 if name in ("light_http", "crawl4ai") else 1.0,
                                  query=url)
                emit("settled",provider=name,operation="fetch",latency_ms=int((time.time()-t0)*1000))
                if cacheable and material is not None and doc.status == "ok" and doc.content:
                    try:
                        payload = doc.model_dump(mode="json")
                        # The requested URL is the lookup key material; do not
                        # persist it again as cache content.
                        payload["url"] = ""
                        self.cache.store("fetch", material, payload, ttl)
                    except (ValueError, TypeError):
                        pass
                return doc
            except ProviderError as e:
                emit("failed",provider=name,operation="fetch",reason=e.kind)
                if e.affects_health:
                    self.health.record_failure(name, str(e))
                self.usage.record(name, endpoint="fetch", success=False,
                                  latency_ms=int((time.time() - t0) * 1000),
                                  credits=0.0, query=url)
                errors.append(f"{name}: {e.kind}")
            except Exception as e:
                emit("failed",provider=name,operation="fetch",reason="error")
                log.exception("fetch provider %s crashed", name)
                self.health.record_failure(name, str(e))
                errors.append(f"{name}: crashed")
        raise ProviderError(
            f"all fetch backends failed for {url}; tried: " + "; ".join(errors),
            kind="all_failed",
        )

    def close(self) -> None:
        if self.cache is not None:
            self.cache.close()

    def _chain_for(self, url: str) -> list[str]:
        low = url.lower()
        hard = any(h in low for h in _KNOWN_HARD_HOSTS)
        if hard:
            # JS/anti-bot hosts: skip light_http, keep credit order.
            return ["crawl4ai", "jina_reader", "firecrawl"]
        policy = self.settings.router_policy.get("fetch_routing", {})
        return policy.get("order", self.ORDER)
