"""Crawl4AI provider — self-hosted unlimited browser-grade fetching.

Default deployment: https://github.com/unclecode/crawl4ai docker at :11235.
POST /md  -> {"url": ...} returns markdown extraction (server renders JS).
Falls back to POST /crawl for older/newer API shapes.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..models.schemas import CrawlPage, CrawlResponse, FetchedDocument
from .base import FetchProvider, ProviderError

log = logging.getLogger(__name__)

# Crawl4AI reports target-side failures (anti-bot wall, DNS/navigation error)
# as 502 with a JSON "detail" body. Those say nothing about the service.
_TARGET_FAILURE_MARKERS = (
    "Blocked by anti-bot",
    "Failed on navigating",
    "net::ERR_",
)


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code == 200:
        return
    body = resp.text
    if resp.status_code == 502 and any(marker in body for marker in _TARGET_FAILURE_MARKERS):
        raise ProviderError.target_http(502, body, "crawl4ai target")
    raise ProviderError.map_http_error(resp.status_code, body[:300])


class Crawl4AIProvider(FetchProvider):
    name = "crawl4ai"
    capabilities = ["fetch", "js_render", "crawl", "screenshot", "self_hosted"]
    is_local = True  # LAN service: never route through the outbound proxy

    @property
    def configured(self) -> bool:
        return bool(self.settings.crawl4ai_url)

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.settings.crawl4ai_token:
            h["Authorization"] = f"Bearer {self.settings.crawl4ai_token}"
        return h

    async def fetch(self, url: str, **opts: Any) -> FetchedDocument:
        if not self.configured:
            raise ProviderError("CRAWL4AI_URL not configured", kind="config")
        base = self.settings.crawl4ai_url
        client = await self.client()
        payload = {"url": url}
        if opts.get("wait_for"):
            payload["wait_for"] = opts["wait_for"]

        try:
            resp = await client.post(f"{base}/md", json=payload,
                                     headers=self._headers(), timeout=90.0)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise ProviderError(f"crawl4ai unreachable: {e}", kind="timeout") from e
        if resp.status_code == 404:
            # Older deployments: /crawl async job endpoint.
            return await self._fetch_via_crawl(url)
        _raise_for_status(resp)
        data = resp.json()
        content = data.get("markdown") or data.get("text") or ""
        if isinstance(content, dict):  # some versions: {"raw_markdown": ...}
            content = content.get("raw_markdown") or content.get("fit_markdown") or ""
        if not content:
            raise ProviderError("crawl4ai returned empty content", kind="error")
        return FetchedDocument(
            url=url, content=content, content_type="markdown", provider=self.name,
            title=data.get("title"), metadata={"success": data.get("success", True)},
        )

    async def _fetch_via_crawl(self, url: str) -> FetchedDocument:
        base = self.settings.crawl4ai_url
        client = await self.client()
        resp = await client.post(
            f"{base}/crawl", json={"urls": [url], "browser_config": {}},
            headers=self._headers(), timeout=120.0,
        )
        if resp.status_code != 200:
            _raise_for_status(resp)
        data = resp.json()
        results = data.get("results") or []
        if not results:
            raise ProviderError("crawl4ai /crawl returned no results", kind="error")
        r = results[0]
        return FetchedDocument(
            url=url, content=r.get("markdown") or "", content_type="markdown",
            provider=self.name, title=r.get("title"),
            metadata={"via": "crawl-endpoint"},
        )

    async def crawl_site(self, root_url: str, *, max_pages: int = 10,
                         max_depth: int = 2) -> CrawlResponse:
        """Crawl via the /crawl endpoint with link following (depth-limited BFS)."""
        if not self.configured:
            raise ProviderError("CRAWL4AI_URL not configured", kind="config")
        base = self.settings.crawl4ai_url
        client = await self.client()
        resp = await client.post(
            f"{base}/crawl",
            json={"urls": [root_url],
                  "browser_config": {},
                  "crawler_config": {"max_pages": max_pages, "max_depth": max_depth}},
            headers=self._headers(), timeout=180.0,
        )
        if resp.status_code != 200:
            _raise_for_status(resp)
        data = resp.json()
        pages = [
            CrawlPage(url=r.get("url", ""), content=r.get("markdown") or "",
                      status="ok" if r.get("success", True) else "failed",
                      error=r.get("error"))
            for r in (data.get("results") or [])
        ]
        return CrawlResponse(root_url=root_url, pages=pages[:max_pages],
                             provider=self.name)
