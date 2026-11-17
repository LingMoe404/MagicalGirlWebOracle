"""SearXNG provider — self-hosted, unlimited, permanent free fallback.

Also the Chinese-internet workhorse: for CN-relevant queries we add Chinese
engine categories/engines (baidu, 360, quark/sogou if configured server-side,
sogou-wechat, bilibili, baidu dev search) — tested engine list, not max engines.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ..dedup.canonical import canonicalize_url
from ..langdetect.detector import china_relevance, detect_language
from ..models.schemas import ProviderKind, SearchResult
from .base import ProviderError, SearchProvider

log = logging.getLogger(__name__)

# Conservative Chinese engine candidates. SearXNG ignores unknown/disabled
# engines server-side unless "enabled_bypass" — errors don't break the query.
_CN_ENGINES = ["baidu", "bing", "quark", "sogou", "sogou-wechat", "bilibili",
               "360search", "baidu developer search", "github"]
_CN_CATEGORIES = ["general", "news"]


class SearxngProvider(SearchProvider):
    name = "searxng"
    kind = ProviderKind.SEARCH
    capabilities = ["general", "chinese", "news", "self_hosted"]
    is_local = True  # LAN service: never route through the outbound proxy

    @property
    def configured(self) -> bool:
        return bool(self.settings.searxng_url)

    @property
    def base_url(self) -> str:
        return self.settings.searxng_url

    async def search(self, query: str, *, max_results: int = 10,
                     mode: str = "auto", **opts: Any) -> list[SearchResult]:
        if not self.configured:
            raise ProviderError("SEARXNG_URL not configured", kind="config")

        params: dict[str, str] = {
            "q": query,
            "format": "json",
            "language": "auto",
            "safesearch": "0",
        }

        cn_rel = china_relevance(query)
        lang = detect_language(query)
        if cn_rel >= 0.45:
            # Chinese routing: target CN engine group. SearXNG accepts
            # comma-separated engine names via `engines` param.
            params["engines"] = ",".join(_CN_ENGINES)
            params["language"] = "zh-CN"
            params["categories"] = "general" if mode != "max" else "general,news"
        elif lang == "zh":
            params["language"] = "zh-CN"

        t0 = time.time()
        try:
            client = await self.client()
            resp = await client.get(f"{self.base_url}/search", params=params)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise ProviderError(f"searxng unreachable: {e}", kind="timeout") from e
        if resp.status_code != 200:
            # Common: instance disallows json format (403) => provider unusable
            # until instance enables `search.formats: [html, json]`.
            raise ProviderError.map_http_error(resp.status_code, resp.text[:300])
        data = resp.json()
        log.debug("searxng %.0fms", (time.time() - t0) * 1000)

        out: list[SearchResult] = []
        for item in data.get("results", [])[: max_results * 2]:
            url = item.get("url") or ""
            if not url:
                continue
            engs = item.get("engines") or []
            out.append(SearchResult(
                title=(item.get("title") or "").strip(),
                url=canonicalize_url(url),
                snippet=(item.get("content") or "").strip(),
                provider=self.name,
                score=_norm_score(item.get("score")),
                language=lang if lang != "auto" else detect_language(
                    (item.get("title") or "") + (item.get("content") or "")),
                found_by=[f"searxng:{e}" for e in engs] or ["searxng"],
                metadata={
                    "engine": engs,
                    "category": item.get("category"),
                    "china_relevance": cn_rel,
                    "publishedDate": item.get("publishedDate"),
                },
            ))
        out = out[:max_results]
        for o in out:
            if o.metadata.get("publishedDate"):
                o.published_at = str(o.metadata["publishedDate"])
        return out

    def describe(self) -> dict[str, Any]:
        d = super().describe()
        d["chinese_engines"] = _CN_ENGINES
        return d


def _norm_score(raw: Any) -> float:
    try:
        s = float(raw)
    except (TypeError, ValueError):
        return 0.5
    # SearXNG scores are unbounded; squash to 0..1.
    return max(0.05, min(1.0, s / (s + 5)))
