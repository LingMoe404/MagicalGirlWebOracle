"""Firecrawl provider — LAST-RESORT fetcher for anti-bot / hard pages.

API: https://api.firecrawl.dev/v2  |  Auth: Bearer. Monthly free credits.
Deliberately reserved: router only calls Firecrawl after Crawl4AI and
Jina have failed. proxy=auto costs nothing extra on free tier.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..models.schemas import CrawlPage, CrawlResponse, FetchedDocument, QuotaSnapshot, QuotaType
from .base import FetchProvider, ProviderError

log = logging.getLogger(__name__)

_BASE = "https://api.firecrawl.dev/v2"
_MONTHLY_FREE = 500


class FirecrawlProvider(FetchProvider):
    name = "firecrawl"
    capabilities = ["fetch", "js_render", "anti_bot", "crawl", "map"]

    @property
    def configured(self) -> bool:
        return bool(self.settings.firecrawl_api_key)

    async def fetch(self, url: str, **opts: Any) -> FetchedDocument:
        if not self.configured:
            raise ProviderError("FIRECRAWL_API_KEY not set", kind="config")
        client = await self.client()
        payload: dict[str, Any] = {
            "url": url,
            "formats": ["markdown"],
            "onlyMainContent": True,
            "waitFor": int(opts.get("wait_ms", 0)),
        }
        try:
            resp = await client.post(
                f"{_BASE}/scrape", json=payload,
                headers={"Authorization": f"Bearer {self.settings.firecrawl_api_key}"},
                timeout=90.0,
            )
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise ProviderError(f"firecrawl unreachable: {e}", kind="timeout") from e
        if resp.status_code != 200:
            raise ProviderError.map_http_error(resp.status_code, resp.text[:300])
        data = resp.json()
        doc = data.get("data") or {}
        md = doc.get("markdown") or ""
        if not md:
            raise ProviderError("firecrawl returned empty markdown", kind="error")
        meta = doc.get("metadata") or {}
        return FetchedDocument(
            url=url, title=meta.get("title"), content=md, content_type="markdown",
            provider=self.name,
            metadata={"status_code": meta.get("statusCode"),
                      "credits_used": data.get("creditsUsed", 1)},
        )

    async def crawl_site(self, root_url: str, *, max_pages: int = 10,
                         max_depth: int = 2) -> CrawlResponse:
        """POST /crawl (async job) then poll until complete."""
        if not self.configured:
            raise ProviderError("FIRECRAWL_API_KEY not set", kind="config")
        client = await self.client()
        headers = {"Authorization": f"Bearer {self.settings.firecrawl_api_key}"}
        resp = await client.post(
            f"{_BASE}/crawl",
            json={"url": root_url, "limit": max_pages, "maxDiscoveryDepth": max_depth,
                  "scrapeOptions": {"formats": ["markdown"], "onlyMainContent": True}},
            headers=headers, timeout=60.0, follow_redirects=False,
        )
        if resp.status_code not in (200, 202):
            raise ProviderError.map_http_error(resp.status_code, resp.text[:300])
        job = resp.json()
        job_id = job.get("id")
        if not job_id:
            raise ProviderError("firecrawl crawl: no job id", kind="error")

        import asyncio
        pages: list[CrawlPage] = []
        for _ in range(40):  # up to ~120s
            await asyncio.sleep(3)
            check = await client.get(f"{_BASE}/crawl/{job_id}", headers=headers, timeout=30.0,follow_redirects=False)
            if check.status_code != 200:
                continue
            data = check.json()
            if data.get("status") == "completed":
                pages = [
                    CrawlPage(url=d.get("metadata", {}).get("sourceURL", d.get("url", "")),
                              content=d.get("markdown") or "")
                    for d in data.get("data", [])
                ]
                break
            if data.get("status") in ("failed", "cancelled"):
                break
        return CrawlResponse(root_url=root_url, pages=pages[:max_pages], provider=self.name)

    def quota_snapshot(self) -> QuotaSnapshot:
        return QuotaSnapshot(
            provider=self.name, quota_type=QuotaType.MONTHLY, limit=_MONTHLY_FREE,
            remaining=None, reset_at=None, renewing=True,
        )

    async def fetch_balance(self) -> dict[str, Any] | None:
        """Official credit-usage API — used by `doctor` and status reconciliation."""
        if not self.configured:
            return None
        client = await self.client()
        try:
            r = await client.get(f"{_BASE}/team/credit-usage",
                                 headers={"Authorization": f"Bearer {self.settings.firecrawl_api_key}"},
                                 timeout=15.0)
        except (httpx.TimeoutException, httpx.TransportError):
            return None
        if r.status_code != 200:
            return None
        d = r.json().get("data", {})
        return {
            "remaining_credits": d.get("remainingCredits"),
            "plan_credits": d.get("planCredits"),
            "billing_period_start": d.get("billingPeriodStart"),
            "billing_period_end": d.get("billingPeriodEnd"),
        }
