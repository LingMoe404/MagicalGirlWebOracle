"""Jina Reader (r.jina.ai) + Jina Reranker (api.jina.ai/v1/rerank).

Reader: cleans any page (incl. JS-heavy) into LLM-friendly markdown.
Reranker: cross-provider result reranking for HIGH/MAX modes.

IMPORTANT: Jina's free credits are a ONE-TIME signup gift, not monthly.
Keyless r.jina.ai works at a low rate (20 RPM) — settings.jina_keyless_fallback
controls whether we use it without a key. The reranker is only spent on
HIGH/MAX where it earns its credits.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..models.schemas import FetchedDocument, QuotaSnapshot, QuotaType, SearchResult
from .base import FetchProvider, ProviderError, Reranker

log = logging.getLogger(__name__)


class JinaReaderFetcher(FetchProvider):
    name = "jina_reader"
    capabilities = ["reader", "markdown_clean"]

    @property
    def configured(self) -> bool:
        return bool(self.settings.jina_api_key) or self.settings.jina_keyless_fallback

    async def fetch(self, url: str, **opts: Any) -> FetchedDocument:
        if not self.configured:
            raise ProviderError("jina reader disabled (no key & keyless fallback off)",
                                kind="config")
        target = f"https://r.jina.ai/{url}"
        headers = {"Accept": "text/plain", "X-Return-Format": "markdown"}
        if self.settings.jina_api_key:
            headers["Authorization"] = f"Bearer {self.settings.jina_api_key}"
        try:
            client = await self.client()
            resp = await client.get(target, headers=headers, timeout=40.0)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise ProviderError(f"jina reader unreachable: {e}", kind="timeout") from e
        if resp.status_code == 422:
            # Jina answers 422 when it cannot process the target page itself
            # (dead link, blocked content) — the Reader service is still up.
            raise ProviderError.target_http(422, resp.text, "jina reader upstream")
        if resp.status_code != 200:
            raise ProviderError.map_http_error(resp.status_code, resp.text[:300])

        text = resp.text
        title = None
        # Jina prefixes "Title: ..." / "URL Source: ..." metadata lines.
        if text.startswith("Title:"):
            first = text.split("\n", 1)[0]
            title = first.removeprefix("Title:").strip() or None
        return FetchedDocument(
            url=url, title=title, content=text[:200_000], content_type="markdown",
            provider=self.name,
            metadata={"keyless": not bool(self.settings.jina_api_key)},
        )

    def quota_snapshot(self) -> QuotaSnapshot:
        return QuotaSnapshot(
            provider=self.name, quota_type=QuotaType.ONE_TIME, limit=1000,
            remaining=None, reset_at=None, renewing=False,
        )


class JinaReranker(Reranker):
    name = "jina_reranker"
    capabilities = ["rerank"]

    @property
    def configured(self) -> bool:
        return bool(self.settings.jina_api_key)

    async def rerank(self, query: str, results: list[SearchResult],
                     top_n: int = 10) -> list[SearchResult]:
        if not self.configured:
            raise ProviderError("JINA_API_KEY not set", kind="config")
        docs = [f"{r.title}\n{r.snippet}\n{r.content[:500]}".strip() or r.url
                for r in results]
        try:
            client = await self.client()
            resp = await client.post(
                "https://api.jina.ai/v1/rerank",
                json={"model": "jina-reranker-v3", "query": query,
                      "documents": docs, "top_n": top_n},
                headers={"Authorization": f"Bearer {self.settings.jina_api_key}"},
                timeout=30.0,
            )
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise ProviderError(f"jina reranker unreachable: {e}", kind="timeout") from e
        if resp.status_code != 200:
            raise ProviderError.map_http_error(resp.status_code, resp.text[:300])

        data = resp.json()
        out: list[SearchResult] = []
        for item in data.get("results", []):
            idx = item.get("index")
            if idx is None or idx >= len(results):
                continue
            r = results[idx].model_copy(deep=True)
            r.score = float(item.get("relevance_score", 0.0))
            r.metadata = {**r.metadata, "reranked_by": self.name}
            out.append(r)
        out.sort(key=lambda x: x.score, reverse=True)
        return out

    def quota_snapshot(self) -> QuotaSnapshot:
        return QuotaSnapshot(
            provider=self.name, quota_type=QuotaType.ONE_TIME, limit=1000,
            remaining=None, reset_at=None, renewing=False,
        )
