"""Optional LIVE integration tests — run explicitly with:
    LIVE=1 python -m pytest tests/test_live_integration.py -v

Skipped by default (CI-safe). Uses only free endpoints; You.com free MCP is
keyless, example.com/httpbin need no auth. Never touches paid-only endpoints.
"""

from __future__ import annotations

import os

import pytest

from magicalgirl_web_oracle.providers.light_http import LightHttpFetcher
from magicalgirl_web_oracle.providers.you_mcp import YouMcpProvider
from magicalgirl_web_oracle.config.settings import get_settings

pytestmark = pytest.mark.skipif(
    os.getenv("LIVE", "") != "1",
    reason="live integration tests: set LIVE=1 to run (uses free endpoints only)",
)


@pytest.mark.asyncio
async def test_light_http_fetches_example_com():
    p = LightHttpFetcher(get_settings())
    doc = await p.fetch("https://example.com")
    assert doc.status == "ok"
    assert "Example Domain" in doc.content
    await p.aclose()


@pytest.mark.asyncio
async def test_you_mcp_free_search():
    p = YouMcpProvider(get_settings())
    results = await p.search("python release notes", max_results=3, mode="fast")
    assert len(results) > 0
    assert all(r.url.startswith("http") for r in results)
    await p.aclose()


@pytest.mark.asyncio
async def test_you_mcp_chinese_query():
    p = YouMcpProvider(get_settings())
    results = await p.search("小米su7 评测", max_results=3, mode="fast")
    assert len(results) > 0
    await p.aclose()
