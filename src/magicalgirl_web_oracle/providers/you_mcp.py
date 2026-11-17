"""You.com Free MCP provider — keyless daily free search via official MCP.

Endpoint: https://api.you.com/mcp?profile=free  (keyless basic `you-search`)
Transport: Streamable HTTP (JSON-RPC over POST). If the endpoint is
unreachable/blocked, the provider fails fast and the system routes elsewhere.
No paid/advanced API is ever called.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from ..dedup.canonical import canonicalize_url
from ..langdetect.detector import detect_language
from ..models.schemas import ProviderKind, QuotaSnapshot, QuotaType, SearchResult
from .base import ProviderError, SearchProvider

log = logging.getLogger(__name__)

_ENDPOINT_FREE = "https://api.you.com/mcp?profile=free"
_ENDPOINT_AUTH = "https://api.you.com/mcp"
_DAILY_FREE = 20  # conservative estimate; enforced softly via usage DB
_PROTO_VERSION = "2025-03-26"


class YouMcpProvider(SearchProvider):
    name = "you_mcp"
    kind = ProviderKind.SEARCH
    capabilities = ["general", "news", "fresh"]

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._session_id: str | None = None
        self._next_id = 1

    @property
    def endpoint(self) -> str:
        """Authenticated endpoint when YDC_API_KEY is set (more tools/quota),
        otherwise the keyless free profile."""
        return _ENDPOINT_AUTH if self.settings.ydc_api_key else _ENDPOINT_FREE

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        if self.settings.ydc_api_key:
            h["Authorization"] = f"Bearer {self.settings.ydc_api_key}"
        return h

    @property
    def configured(self) -> bool:
        # Keyless — always "configured"; reachability handled by health system.
        return True

    # ------------------------------------------------------------------
    # Minimal MCP streamable-http client (initialize -> call tool)
    # ------------------------------------------------------------------

    async def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        client = await self.client()
        headers = self._headers()
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        body = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            body["params"] = params
        self._next_id += 1

        try:
            resp = await client.post(self.endpoint, json=body, headers=headers, timeout=25.0)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise ProviderError(f"you mcp unreachable: {e}", kind="timeout") from e

        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid
        if resp.status_code == 404 and method == "tools/call" and self._session_id:
            # Session expired — reinitialize once.
            self._session_id = None
            return await self._rpc(method, params)
        if resp.status_code != 200:
            raise ProviderError.map_http_error(resp.status_code, resp.text[:300])

        text = resp.text.strip()
        if text.startswith("event:") or text.startswith("data:"):
            # SSE-style stream: several events may arrive (e.g. logging
            # notifications); the JSON-RPC response is the LAST data payload.
            payload = None
            for line in text.splitlines():
                if line.startswith("data:"):
                    try:
                        payload = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
            if payload is None:
                raise ProviderError("you mcp: empty SSE payload", kind="error")
            return payload
        try:
            return resp.json()
        except json.JSONDecodeError as e:
            raise ProviderError(f"you mcp: non-JSON response: {text[:120]}", kind="error") from e

    async def _ensure_session(self) -> None:
        if self._session_id:
            return
        await self._rpc("initialize", {
            "protocolVersion": _PROTO_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "magicalgirl-web-oracle", "version": "0.1.0"},
        })
        try:
            await self._rpc("notifications/initialized")
        except ProviderError:
            pass  # some servers don't echo notifications

    async def search(self, query: str, *, max_results: int = 10,
                     mode: str = "auto", **opts: Any) -> list[SearchResult]:
        await self._ensure_session()
        result = await self._rpc("tools/call", {
            "name": "you-search",
            "arguments": {"query": query, "numResults": max_results},
        })
        if result.get("error"):
            raise ProviderError(f"you mcp error: {result['error']}", kind="error")
        payload = result.get("result", {})
        return self._parse_tool_payload(payload, query, max_results)

    # ------------------------------------------------------------------
    # Payload parsing (tolerant: text blocks or structured content)
    # ------------------------------------------------------------------

    def _parse_tool_payload(self, payload: dict[str, Any], query: str,
                            max_results: int) -> list[SearchResult]:
        out: list[SearchResult] = []

        structured = payload.get("structuredContent") or {}
        items = (structured.get("results") or structured.get("data")
                 or structured.get("organic") or [])
        if isinstance(items, list):
            for it in items:
                if not isinstance(it, dict):
                    continue
                url = it.get("url") or it.get("link") or ""
                if not url:
                    continue
                out.append(self._mk(query, it.get("title") or "", url,
                                    it.get("snippet") or it.get("description") or "",
                                    it.get("published_date") or it.get("page_age")))
        if not out:
            # Fallback: parse text blocks — plain markdown or a JSON string.
            for block in payload.get("content", []):
                if block.get("type") != "text":
                    continue
                text = block.get("text", "")
                stripped = text.strip()
                if stripped.startswith("{"):
                    out.extend(self._parse_json_text(stripped, query))
                if not out:
                    out.extend(_parse_markdown_results(text, query))
        return out[:max_results]

    def _parse_json_text(self, text: str, query: str) -> list[SearchResult]:
        """Parse you-search's JSON-string result: {"results": {"web": [...]}}."""
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        web = data.get("results", {})
        if isinstance(web, dict):
            web = web.get("web") or web.get("organic") or []
        if not isinstance(web, list):
            return []
        out = []
        for it in web:
            if not isinstance(it, dict):
                continue
            url = it.get("url") or ""
            if not url:
                continue
            out.append(self._mk(query, it.get("title") or "", url,
                                it.get("description") or it.get("snippet") or "",
                                it.get("page_age")))
        return out

    def _mk(self, query: str, title: str, url: str, snippet: str,
            published: Any) -> SearchResult:
        return SearchResult(
            title=(title or "").strip(),
            url=canonicalize_url(url),
            snippet=(snippet or "").strip(),
            provider=self.name,
            score=0.5,
            language=detect_language(title + snippet),
            published_at=str(published) if published else None,
            metadata={"transport": "mcp-free"},
        )

    def quota_snapshot(self) -> QuotaSnapshot:
        return QuotaSnapshot(
            provider=self.name, quota_type=QuotaType.DAILY, limit=_DAILY_FREE,
            remaining=None, reset_at=None, renewing=True,
        )

    async def fetch_balance(self) -> dict[str, Any] | None:
        """`you-balance` tool (authenticated endpoint only)."""
        if not self.settings.ydc_api_key:
            return None
        try:
            await self._ensure_session()
            result = await self._rpc("tools/call",
                                     {"name": "you-balance", "arguments": {}})
        except ProviderError:
            return None
        if result.get("error"):
            return None
        payload = result.get("result", {})
        sc = payload.get("structuredContent") or {}
        attrs = (sc.get("data") or {}).get("attributes") or {}
        if attrs.get("balance") is not None:
            return {"balance": attrs["balance"]}
        return None


_URL_RE = None  # lazy import inside parser


def _parse_markdown_results(text: str, query: str) -> list[SearchResult]:
    """Extract [title](url) + following-line snippet pairs from markdown text."""
    import re
    out: list[SearchResult] = []
    lines = text.splitlines()
    link_re = re.compile(r"\[([^\]]{1,200})\]\((https?://[^)\s]+)\)")
    for i, line in enumerate(lines):
        m = link_re.search(line)
        if not m:
            continue
        title, url = m.group(1), m.group(2)
        snippet = ""
        for j in range(i + 1, min(i + 3, len(lines))):
            if link_re.search(lines[j]):
                break
            if lines[j].strip():
                snippet = lines[j].strip()
                break
        if title.lower() in ("", "search"):
            continue
        out.append(SearchResult(
            title=title.strip(), url=canonicalize_url(url), snippet=snippet,
            provider="you_mcp", score=0.45,
            language=detect_language(title + snippet),
            metadata={"transport": "mcp-free"},
        ))
    return out
