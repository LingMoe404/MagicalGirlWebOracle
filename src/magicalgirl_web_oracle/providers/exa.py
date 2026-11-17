"""Exa provider — primary engine for technical / semantic / niche searches.

API: https://api.exa.ai  |  Auth: `x-api-key` header.
Search types: fast | auto | deep-lite | deep (see docs; deep = Deep Search).
Free tier is monthly credits — router reserves Exa for HIGH/MAX or AUTO when
cheap engines (You/SearXNG) don't fit. 402 => quota exhausted, never pay.
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

_BASE = "https://api.exa.ai"
# Exa's free tier is DOLLAR-denominated: $10/month recurring + $20 one-time
# signup credit. Standard search = $0.007/call; deep search = ~$0.012-0.015.
# We track spend in USD (the response's costDollars), not request counts.
_MONTHLY_FREE_USD = 10.0
_SIGNUP_FREE_USD = 20.0
_COST_PER_CALL_USD = 0.007  # fallback estimate when costDollars is absent


class ExaProvider(SearchProvider):
    name = "exa"
    kind = ProviderKind.SEARCH
    capabilities = ["technical", "semantic", "github", "hf", "deep_search", "contents"]

    @property
    def configured(self) -> bool:
        return bool(self.settings.exa_api_key)

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        # Dollar cost of the most recent call (reported by Exa in costDollars).
        self.last_cost_usd: float = _COST_PER_CALL_USD
        self.last_cost_reported: bool = False

    async def search(self, query: str, *, max_results: int = 10,
                     mode: str = "auto", **opts: Any) -> list[SearchResult]:
        if not self.configured:
            raise ProviderError("EXA_API_KEY not set", kind="config")

        # Cost control: `fast` for light modes; `auto` for high; `deep-lite`
        # only for MAX. `deep` burns many credits — only via explicit override.
        exa_type = {
            "fast": "fast",
            "auto": "fast",
            "high": "auto",
            "max": "deep-lite",
        }.get(mode, "auto")
        if opts.get("exa_type"):
            exa_type = str(opts["exa_type"])

        payload: dict[str, Any] = {
            "query": query,
            "numResults": max_results,
            "type": exa_type,
        }
        if opts.get("category"):
            payload["category"] = opts["category"]
        if opts.get("include_domains"):
            payload["includeDomains"] = opts["include_domains"]
        if opts.get("start_published_date"):
            payload["startPublishedDate"] = opts["start_published_date"]
        # Text snippets inline — avoids a second /contents call (saves credits).
        if mode in ("high", "max") or opts.get("with_contents"):
            payload["contents"] = {"text": {"maxCharacters": 2000}}

        t0 = time.time()
        client = await self.client()
        try:
            resp = await client.post(
                f"{_BASE}/search",
                json=payload,
                headers={"x-api-key": self.settings.exa_api_key},
            )
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise ProviderError(f"exa unreachable: {e}", kind="timeout") from e
        if resp.status_code != 200:
            raise ProviderError.map_http_error(resp.status_code, resp.text[:300])
        data = resp.json()
        log.debug("exa %s %.0fms", exa_type, (time.time() - t0) * 1000)

        # Record the ACTUAL dollar cost Exa reports (free tier is USD-denominated).
        cost = data.get("costDollars") or {}
        self.last_cost_reported = cost.get("total") is not None
        self.last_cost_usd = float(cost.get("total", _COST_PER_CALL_USD))

        out: list[SearchResult] = []
        for item in data.get("results", []):
            url = item.get("url") or ""
            if not url:
                continue
            published = item.get("publishedDate") or item.get("crawlDate")
            out.append(SearchResult(
                title=(item.get("title") or "").strip(),
                url=canonicalize_url(url),
                snippet=(item.get("summary") or "").strip(),
                content=(item.get("text") or "").strip(),
                published_at=published,
                provider=self.name,
                score=_norm_score(item.get("score")),
                language=detect_language((item.get("title") or "") + (item.get("text") or "")[:200]),
                metadata={"author": item.get("author"), "id": item.get("id"),
                          "exa_type": exa_type},
            ))
        return out

    def quota_snapshot(self) -> QuotaSnapshot:
        # Free tier is USD-denominated ($10/mo recurring). Real remaining balance
        # is only visible in the dashboard; we track local spend in USD.
        return QuotaSnapshot(
            provider=self.name, quota_type=QuotaType.MONTHLY, limit=_MONTHLY_FREE_USD,
            remaining=None, reset_at=None, renewing=True, unit="usd",
        )


def _norm_score(raw: Any) -> float:
    try:
        return max(0.05, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.5
