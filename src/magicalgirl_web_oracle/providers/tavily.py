"""Tavily provider — second retrieval system (general + advanced search).

API: https://api.tavily.com  |  Auth: `Authorization: Bearer <key>`.
search_depth: basic=1 credit, advanced=2 credits. Router only spends
`advanced` on HIGH/MAX; AUTO uses basic. 429/402 => quota exhausted.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ..dedup.canonical import canonicalize_url
from ..langdetect.detector import detect_language
from ..models.schemas import ProviderKind, QuotaSnapshot, QuotaType, SearchResult
from .base import ProviderError, SearchProvider

log = logging.getLogger(__name__)

_BASE = "https://api.tavily.com"
_MONTHLY_FREE = 1000


class TavilyProvider(SearchProvider):
    name = "tavily"
    kind = ProviderKind.SEARCH
    capabilities = ["general", "advanced_search", "extract", "crawl", "map", "research"]

    @property
    def configured(self) -> bool:
        return bool(self.settings.tavily_api_key)

    async def search(self, query: str, *, max_results: int = 10,
                     mode: str = "auto", **opts: Any) -> list[SearchResult]:
        if not self.configured:
            raise ProviderError("TAVILY_API_KEY not set", kind="config")

        depth = "advanced" if mode in ("high", "max") else "basic"
        payload: dict[str, Any] = {
            "query": query,
            "search_depth": depth,
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": mode in ("high", "max"),
        }
        if opts.get("topic"):
            payload["topic"] = opts["topic"]
        if opts.get("time_range"):
            payload["time_range"] = opts["time_range"]
        if opts.get("include_domains"):
            payload["include_domains"] = opts["include_domains"]
        if opts.get("exclude_domains"):
            payload["exclude_domains"] = opts["exclude_domains"]

        t0 = time.time()
        client = await self.client()
        try:
            resp = await client.post(
                f"{_BASE}/search",
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.tavily_api_key}"},
            )
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise ProviderError(f"tavily unreachable: {e}", kind="timeout") from e
        if resp.status_code != 200:
            raise ProviderError.map_http_error(resp.status_code, resp.text[:300])
        data = resp.json()
        log.debug("tavily %s %.0fms", depth, (time.time() - t0) * 1000)

        out: list[SearchResult] = []
        for item in data.get("results", []):
            url = item.get("url") or ""
            if not url:
                continue
            raw = item.get("raw_content") or ""
            out.append(SearchResult(
                title=(item.get("title") or "").strip(),
                url=canonicalize_url(url),
                snippet=(item.get("content") or "").strip(),
                content=raw.strip()[:4000],
                published_at=item.get("published_date"),
                provider=self.name,
                score=_norm_score(item.get("score")),
                language=detect_language((item.get("title") or "") + (item.get("content") or "")),
                metadata={"tavily_depth": depth},
            ))
        return out

    def quota_snapshot(self) -> QuotaSnapshot:
        return QuotaSnapshot(
            provider=self.name, quota_type=QuotaType.MONTHLY, limit=_MONTHLY_FREE,
            remaining=None, reset_at=None, renewing=True,
        )

    async def fetch_balance(self) -> dict[str, Any] | None:
        """Official usage API — lets the router reconcile local bookkeeping."""
        if not self.configured:
            return None
        client = await self.client()
        try:
            r = await client.get(f"{_BASE}/usage",
                                 headers={"Authorization": f"Bearer {self.settings.tavily_api_key}"},
                                 timeout=15.0)
        except (httpx.TimeoutException, httpx.TransportError):
            return None
        if r.status_code != 200:
            return None
        d = r.json()
        acct = d.get("account", {})
        return {
            "plan": acct.get("current_plan"),
            "used": acct.get("plan_usage"),
            "limit": acct.get("plan_limit"),
            "paygo_used": acct.get("paygo_usage"),
            "paygo_limit": acct.get("paygo_limit"),
        }


def _norm_score(raw: Any) -> float:
    try:
        return max(0.05, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.5
