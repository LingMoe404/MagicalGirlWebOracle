"""Unified search router.

Given a query + mode, scores candidate providers on:
  quality_fit (capabilities vs query traits) + cost + remaining quota
  + health + latency + expiry_pressure, applies the Chinese-internet boost,
  picks the mode's provider budget, fans out, dedups/merges, optionally
  reranks, and NEVER lets a request through that might leave free quota.

Quota gate: if tracked remaining hits 0 or the provider raises 402/429,
the provider is skipped for the rest of the window (until reset).
ALLOW_PAID_OVERAGE is always false here; there is no code path that pays.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..cache import ResponseCache, from_settings, is_fresh_query
from ..config.settings import Settings, get_settings
from ..dedup.canonical import agreement_score, merge_results
from ..health.tracker import HealthTracker
from ..langdetect.detector import china_relevance, detect_language
from ..models.schemas import (
    HealthState,
    QuotaType,
    SearchResponse,
    SearchResult,
)
from ..providers.base import ProviderError, QuotaExhaustedError
from ..providers.registry import Registry
from ..quota.usage_db import UsageDB
from ..quota.tracing import emit
from .search_quality import site_constraints,filter_sites,rerank_local

log = logging.getLogger(__name__)


class SearchRouter:
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
        # Manually exhausted-until-reset set (402/429 or quota math hit 0).
        self._quota_blocked: dict[str, float] = {}  # provider -> reset epoch

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    async def search(self, query: str, *, mode: str | None = None,
                     max_results: int = 10, provider_override: str | None = None,
                     bypass_cache: bool = False, **opts: Any) -> SearchResponse:
        try:
            sites=site_constraints(query)
        except ValueError:
            raise ProviderError("invalid site filter",kind="config") from None
        mode = (mode or self.settings.default_mode).lower()
        if mode not in ("fast", "auto", "high", "max"):
            mode = "auto"
        ttl = int(getattr(self.settings, "cache_ttl_seconds", 0) or 0)
        fresh = is_fresh_query(query)
        cacheable = bool(self.cache and ttl > 0 and not bypass_cache and not fresh)
        material = (ResponseCache.search_material(
            query, mode=mode, max_results=max_results,
            provider_override=provider_override, opts=opts) if self.cache else None)
        if self.cache and not cacheable:
            emit("skipped", provider="search_cache", operation="search",
                 reason="cache_bypass" if bypass_cache else "cache_bypass_freshness")
        if cacheable and material is not None:
            payload, age = self.cache.lookup("search", material)
            if payload is not None:
                try:
                    cached = SearchResponse.model_validate(payload)
                except ValueError:
                    cached = None
                if cached is not None:
                    cached.query = query
                    cached.notes.append(
                        f"served from response cache ({int(age or 0)}s old; "
                        "set bypass_cache=true to refresh)")
                    emit("reused", provider="search_cache", operation="search",
                         reason="cache_hit")
                    return cached
        policy = self.settings.router_policy["mode_definitions"][mode]
        weights = self.settings.router_policy["weights"]
        notes: list[str] = []
        usage_rows: list[dict[str, Any]] = []

        lang = detect_language(query)
        cn_rel = china_relevance(query)
        query_traits = {
            "lang": lang,
            "china_relevance": cn_rel,
        }

        if provider_override:
            candidates = [provider_override]
            notes.append(f"provider_override={provider_override}")
        else:
            ranked = self._rank_candidates(query_traits, weights)
            candidates = ranked[: int(policy["max_providers"])]
            notes.append("candidates: " + ", ".join(ranked[:5]))

        for rank,name in enumerate(candidates if provider_override else ranked,1):
            emit("candidate",provider=name,operation="search",rank=rank,reason="manual" if provider_override else "ranked")
            if name not in candidates:
                emit("skipped",provider=name,operation="search",reason="policy_limit")

        # Fan out with per-provider guard.
        tasks = {}
        for name in candidates:
            if not self._is_searchable(name, notes):
                continue
            emit("selected",provider=name,operation="search",reason="manual" if provider_override else "ranked")
            tasks[name] = asyncio.create_task(
                self._guarded_search(name, query, max_results=max_results,
                                     mode=mode, opts=opts)
            )

        results: list[SearchResult] = []
        succeeded: list[str] = []
        try:
            for name, task in tasks.items():
                try:
                    r = await task
                    results.extend(r)
                    succeeded.append(name)
                except QuotaExhaustedError as e:
                    emit("failed",provider=name,operation="search",reason="quota")
                    self._block_until_reset(name, e.reset_at)
                    notes.append(f"{name}: quota exhausted, skipped until reset")
                except ProviderError as e:
                    emit("failed",provider=name,operation="search",reason=e.kind)
                    notes.append(f"{name}: failed ({e.kind})")
                except Exception as e:  # defensive: one bad provider must not sink the search
                    emit("failed",provider=name,operation="search",reason="error")
                    log.exception("provider %s crashed", name)
                    notes.append(f"{name}: crashed ({e})")
        finally:
            # Keep inherited trace context and reservations tied to this request.
            # A cancelled fan-out must not leave sibling providers running.
            for task in tasks.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks.values(),return_exceptions=True)

        # Dedup + merge across providers.
        constrained=filter_sites(results,sites)
        removed=len(results)-len(constrained)
        if removed:
            notes.append(f"site filters removed {removed} off-site results; constraints were not relaxed")
            emit("phase",operation="site_filter",reason="site_filter")
        merged = merge_results(constrained)
        dedup_stats = {
            "raw": len(results),
            "deduped": len(merged),
            "site_filtered": removed,
        }

        # Cross-provider agreement as a credibility signal.
        agree = agreement_score(merged)
        for r in merged:
            a = agree.get(r.url, 0.0)
            if a > 0:
                r.metadata["cross_provider_agreement"] = a
                # Small score boost when independent sources agree.
                r.score = min(1.0, r.score + 0.1 * a)

        # HIGH/MAX: local in multi-key mode; retain the legacy path otherwise.
        if policy["rerank"] and merged:
            if getattr(self.registry,'credentials',None) is not None:
                # Multi-key mode must never bypass its verified cost contracts
                # through the legacy token-counted reranker.
                emit('selected',provider='local_rerank',operation='rerank',reason='local_free')
                merged=rerank_local(query,merged)
                emit('settled',provider='local_rerank',operation='rerank')
                notes.append('reranked locally by query coverage (no external tokens; lexical heuristic)')
            else:
                rr = self.registry.rerankers.get("jina_reranker")
                if rr is None or not rr.configured:
                    emit("skipped",provider="jina_reranker",operation="rerank",reason="not_configured")
                    notes.append("rerank skipped: no reranker configured (set JINA_API_KEY); "
                                 "using agreement/score order")
                elif not getattr(rr,"managed_credentials",False) and not self.health.is_usable("jina_reranker"):
                    emit("skipped",provider="jina_reranker",operation="rerank",reason="unhealthy")
                    notes.append("rerank skipped: reranker unhealthy")
                else:
                    try:
                        t0 = time.time()
                        emit("selected",provider="jina_reranker",operation="rerank",reason="ranked")
                        merged = await rr.rerank(query, merged, top_n=max_results)
                        self.usage.record("jina_reranker", endpoint="v1/rerank",
                                          success=True, latency_ms=int((time.time()-t0)*1000),
                                          credits=1, mode=mode, query=query)
                        usage_rows.append({"provider": "jina_reranker", "endpoint": "v1/rerank"})
                        emit("settled",provider="jina_reranker",operation="rerank",latency_ms=int((time.time()-t0)*1000))
                        notes.append("reranked by jina_reranker")
                    except ProviderError as e:
                        emit("failed",provider="jina_reranker",operation="rerank",reason=e.kind)
                        notes.append(f"rerank failed: {e}")
            merged.sort(key=lambda r: r.score, reverse=True)

        merged = merged[:max_results]
        providers_used = sorted({r.provider for r in merged} | set(succeeded))
        response = SearchResponse(
            query=query, mode=mode,  # type: ignore[arg-type]
            providers_used=sorted(providers_used), results=merged,
            dedup_stats=dedup_stats, usage=usage_rows, notes=notes,
        )
        if cacheable and material is not None and merged:
            try:
                payload = response.model_dump(mode="json")
                # Never persist the user's query as cache content.
                payload["query"] = ""
                self.cache.store("search", material, payload, ttl)
            except (ValueError, TypeError):
                pass
        return response

    # ------------------------------------------------------------------
    # Candidate scoring
    # ------------------------------------------------------------------

    def _query_fit(self, name: str, traits: dict[str, Any]) -> float:
        prov = self.registry.all[name]
        caps = set(getattr(prov, "capabilities", []))
        cn_rel: float = traits["china_relevance"]
        lang: str = traits["lang"]
        fit = 0.5
        if cn_rel >= 0.45 and "chinese" in caps:
            fit = 1.0  # SearXNG CN matrix
        elif lang == "zh" and "chinese" in caps:
            fit = 0.8
        if "technical" in caps or "semantic" in caps:
            fit = max(fit, 0.7)  # exa base fit
        if "general" in caps:
            fit = max(fit, 0.6)
        return fit

    def _remaining_quota(self, name: str) -> float:
        """Normalized 0..1 remaining quota (1 for unlimited, 0 when blocked)."""
        if getattr(self.registry.all.get(name),"managed_credentials",False):
            snap = self.registry.all[name].quota_snapshot()
            return min(1.0, snap.remaining / snap.limit) if snap.limit else 0.0
        if name in self._quota_blocked:
            return 0.0
        prov = self.registry.all[name]
        cfg = self.settings.providers_cfg["providers"].get(name)
        if not cfg:
            return 0.5
        q = cfg.get("quota", {})
        qtype = QuotaType(q.get("type", "unknown"))
        limit = q.get("limit")
        remaining = self.usage.remaining(name, qtype, limit)
        if qtype == QuotaType.UNLIMITED:
            return 1.0
        if remaining is None:
            # Unknown quota: policy = skip (0-cost rule) — conservative.
            return 0.25 if qtype == QuotaType.ONE_TIME else 0.5
        if limit:
            return max(0.0, min(1.0, remaining / limit))
        return 0.5

    def _cost_score(self, name: str) -> float:
        cfg = self.settings.providers_cfg["providers"].get(name, {})
        tier = cfg.get("cost_tier", 1)
        return {0: 1.0, 1: 0.6, 2: 0.3}.get(tier, 0.5)

    def _expiry_pressure(self, name: str) -> float:
        if getattr(self.registry.all.get(name),"managed_credentials",False):
            return 0.0  # mixed account billing windows cannot use provider defaults
        cfg = self.settings.providers_cfg["providers"].get(name)
        if not cfg:
            return 0.0
        q = cfg.get("quota", {})
        qtype = QuotaType(q.get("type", "unknown"))
        return self.usage.expiry_pressure(name, qtype, q.get("limit"))

    def _rank_candidates(self, traits: dict[str, Any], weights: dict[str, float]) -> list[str]:
        scored: list[tuple[str, float]] = []
        for name in self.registry.search:
            prov = self.registry.search[name]
            if not prov.configured:
                continue
            h = self.health.state(name)
            health_score = {HealthState.HEALTHY: 1.0, HealthState.DEGRADED: 0.5,
                            HealthState.UNAVAILABLE: 0.0}[h]
            stats = self.usage.stats_24h(name)
            lat = stats.get("avg_latency_ms")
            lat_score = max(0.0, 1.0 - (lat or 500) / 5000)

            fit = self._query_fit(name, traits)
            # Chinese routing boost from policy.
            if traits["china_relevance"] >= 0.45:
                zh_cfg = self.settings.router_policy.get("chinese_routing", {})
                if name in zh_cfg.get("boosted_providers", []):
                    fit = min(1.0, fit + zh_cfg.get("boost_factor", 0.35))

            total = (
                weights["quality_fit"] * fit
                + weights["cost"] * self._cost_score(name)
                + weights["remaining_quota"] * self._remaining_quota(name)
                + weights["health"] * health_score
                + weights["latency"] * lat_score
                + weights["expiry_pressure"] * self._expiry_pressure(name)
            )
            scored.append((name, round(total, 4)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [n for n, _ in scored]

    # ------------------------------------------------------------------
    # Execution guards
    # ------------------------------------------------------------------

    def _is_searchable(self, name: str, notes: list[str]) -> bool:
        prov = self.registry.search.get(name)
        if prov is None:
            emit("skipped",operation="search",reason="no_backend")
            return False
        if not prov.configured:
            emit("skipped",provider=name,reason="not_configured")
            notes.append(f"{name}: not configured, skipped")
            return False
        if getattr(prov,"managed_credentials",False):
            return True
        if not self.health.is_usable(name):
            emit("skipped",provider=name,reason="unhealthy")
            notes.append(f"{name}: unhealthy, skipped")
            return False
        if name in self._quota_blocked:
            reset = self._quota_blocked[name]
            if time.time() < reset:
                emit("skipped",provider=name,operation="search",reason="quota")
                notes.append(f"{name}: quota blocked until reset, skipped")
                return False
            del self._quota_blocked[name]
        return True

    def _block_until_reset(self, name: str, reset_at: float | None) -> None:
        cfg = self.settings.providers_cfg["providers"].get(name, {})
        qtype = QuotaType(cfg.get("quota", {}).get("type", "unknown"))
        reset = reset_at or self.usage.reset_time(qtype) or (time.time() + 6 * 3600)
        self._quota_blocked[name] = reset

    async def _guarded_search(self, name: str, query: str, *, max_results: int,
                              mode: str, opts: dict[str, Any]) -> list[SearchResult]:
        """Run one provider with usage+health bookkeeping and local quota gate."""
        prov = self.registry.search[name]
        if getattr(prov,"managed_credentials",False):
            # The executor owns account budget, failure classification and
            # per-key records. Legacy provider totals cannot gate this pool.
            return await prov.search(query,max_results=max_results,mode=mode,**opts)
        cfg = self.settings.providers_cfg["providers"].get(name, {})
        qtype = QuotaType(cfg.get("quota", {}).get("type", "unknown"))
        limit = cfg.get("quota", {}).get("limit")

        remaining = self.usage.remaining(name, qtype, limit)
        if qtype not in (QuotaType.UNLIMITED,) and remaining == 0:
            raise QuotaExhaustedError(name, self.usage.reset_time(qtype))

        t0 = time.time()
        try:
            results = await prov.search(query, max_results=max_results, mode=mode, **opts)
        except ProviderError as e:
            if e.affects_health:
                self.health.record_failure(name, str(e))
            self.usage.record(name, endpoint="search", success=False,
                              latency_ms=int((time.time() - t0) * 1000),
                              credits=0.0, mode=mode, query=query)
            raise
        except Exception:
            self.health.record_failure(name, "unexpected error")
            raise
        self.health.record_success(name)
        emit("settled",provider=name,operation="search",latency_ms=int((time.time()-t0)*1000))
        # Cost accounting: prefer the provider's own reported cost (Exa reports
        # costDollars; USD-denominated free tier). Otherwise 1 credit/request.
        if qtype == QuotaType.UNLIMITED:
            credits = 0.0
        else:
            credits = float(getattr(prov, "last_cost_usd", 1.0) or 1.0)
        self.usage.record(name, endpoint="search", success=True,
                          latency_ms=int((time.time() - t0) * 1000),
                          credits=credits, mode=mode, query=query)
        return results

    def close(self) -> None:
        if self.cache is not None:
            self.cache.close()
