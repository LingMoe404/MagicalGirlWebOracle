"""Lightweight built-in HTTP fetcher — the zero-cost first hop.

Plain httpx GET with readability extraction (html->text/markdown-lite).
No external service, no quota. Handles static pages; router escalates
anything that looks JS-heavy or gets blocked.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from ..models.schemas import FetchedDocument
from .base import FetchProvider, ProviderError

log = logging.getLogger(__name__)

_JS_HEAVY_HINTS = re.compile(
    r'<div id="(?:root|app|__next)"|ng-app|data-reactroot|window\.__NUXT__|'
    r'window\.__INITIAL_STATE__|id="root"',
    re.I,
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_SCRIPT_RE = re.compile(r"<(script|noscript|style|svg|iframe)[^>]*>.*?</\1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_TAGS = re.compile(r"</?(p|div|br|li|h[1-6]|tr|section|article|blockquote|pre)[^>]*>", re.I)
_WS_RE = re.compile(r"[ \t]{2,}")


class LightHttpFetcher(FetchProvider):
    name = "light_http"
    capabilities = ["fetch", "static"]

    async def fetch(self, url: str, **opts: Any) -> FetchedDocument:
        t0 = None
        client = await self.client()
        headers = {}
        if opts.get("headers"):
            headers.update(opts["headers"])
        try:
            resp = await client.get(url, headers=headers, timeout=20.0)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise ProviderError(f"light fetch failed: {e}", kind="timeout") from e
        if resp.status_code != 200:
            # light_http has no credentials: any non-200 is the target's answer,
            # not a fault of this fetcher. Escalate, but keep it healthy.
            raise ProviderError.target_http(resp.status_code, resp.text, f"fetching {url}")

        ctype = resp.headers.get("content-type", "")
        if "html" in ctype or "xml" in ctype or ctype.startswith("text/"):
            html = resp.text
            title_m = _TITLE_RE.search(html)
            title = title_m.group(1).strip() if title_m else None
            text = _extract_text(html)
            if _JS_HEAVY_HINTS.search(html) and len(text) < 500:
                raise ProviderError(
                    "page appears JS-rendered (light fetch got little content)",
                    kind="js_heavy",
                )
            return FetchedDocument(
                url=url, final_url=str(resp.url), title=title, content=text,
                content_type="text", provider=self.name,
                metadata={"status_code": resp.status_code, "content_type": ctype},
            )
        # Binary / JSON / plain — return as-is (truncated).
        body = resp.text if len(resp.content) < 400_000 else ""
        return FetchedDocument(
            url=url, final_url=str(resp.url), content=body[:200_000],
            content_type=ctype or "text", provider=self.name,
            metadata={"status_code": resp.status_code},
        )


def _extract_text(html: str) -> str:
    html = _SCRIPT_RE.sub(" ", html)
    html = _BLOCK_TAGS.sub("\n", html)
    text = _TAG_RE.sub(" ", html)
    # Minimal entity cleanup.
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                 ("&#39;", "'"), ("&nbsp;", " ")):
        text = text.replace(a, b)
    lines = [_WS_RE.sub(" ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)[:60_000]
